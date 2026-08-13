import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from ..domain.market_data import number
from ..domain.portfolio_calculations import (
    BROKER_FX_SOURCE_TYPE,
    DAILY_MARKET_FX_SOURCE_TYPE,
    FALLBACK_FX_SOURCE_TYPE,
    LIVE_MARKET_FX_SOURCE_TYPE,
    broker_fx_rates_from_positions,
)
from ..domain.portfolio import Position, utc_now_iso
from .external_signal_utils import dart_document_text, parse_iso, symbol_assignments, symbol_list
from .opendart_calendar_source import OPENDART_CORP_CODE_URL, parse_opendart_corp_codes


class ExternalSignalMarketMixin:
    def fred_timeout_seconds(self) -> float:
        # FRED periodically exceeds the generic fast-quote timeout. It runs
        # in the collection path, never inline in the reasoning worker.
        return self.provider_timeout_seconds(
            "externalFredTimeoutSeconds",
            8.0,
            minimum=2.0,
            maximum=20.0,
        )

    def dart_document_text_enabled(self) -> bool:
        return str(self.settings.get("externalDartDocumentTextEnabled") or "0").strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def dart_document_text_max_chars(self) -> int:
        return max(500, min(20000, int(number(self.settings.get("externalDartDocumentTextMaxChars")) or 6000)))

    def dart_company_fundamentals_enabled(self) -> bool:
        value = str(self.settings.get("externalDartCompanyFundamentalsEnabled") or "1").strip().lower()
        return value not in {"0", "false", "no", "off", "disabled"}

    def add_coingecko(self, signals: Dict[str, object]) -> bool:
        if not self.external_api_enabled("externalCoinGeckoEnabled"):
            return False
        ids = self.limited_targets(
            signals,
            "CoinGecko",
            symbol_list(self.settings.get("externalCryptoIds") or "bitcoin,ethereum"),
            "externalCryptoMaxIds",
            50,
        )
        if not ids:
            return False
        headers = {"Accept": "application/json", "User-Agent": "DigitalTwin/1.0"}
        api_key = str(self.settings.get("coingeckoApiKey") or "").strip()
        if api_key:
            headers["x-cg-demo-api-key"] = api_key
        # A cache entry may contain the previous CoinGecko failure row. It is
        # state for the last attempt, not permanent source health, so replace
        # it before this independent refresh reports its own outcome.
        statuses = signals.get("statuses") if isinstance(signals.get("statuses"), list) else []
        signals["statuses"] = [
            item for item in statuses
            if not isinstance(item, dict) or str(item.get("source") or "") != "CoinGecko"
        ]
        signals["cryptoLastAttemptAt"] = utc_now_iso()
        try:
            def fetch_markets():
                url = "https://api.coingecko.com/api/v3/coins/markets?" + urllib.parse.urlencode({
                    "vs_currency": "usd",
                    "ids": ",".join(ids),
                    "order": "market_cap_desc",
                    "per_page": str(len(ids)),
                    "page": "1",
                    "sparkline": "false",
                    "price_change_percentage": "1h,24h,7d",
                    "locale": "en",
                })
                payload = self.fetch_json(url, headers)
                if not isinstance(payload, list):
                    raise RuntimeError("coins/markets JSON is not a list")
                return payload

            payload = self.guarded_call("CoinGecko", "coins/markets", fetch_markets)
            fetched_at = utc_now_iso()
            markets = {}
            for item in payload:
                if not isinstance(item, dict):
                    continue
                coin_id = str(item.get("id") or "").lower()
                if not coin_id:
                    continue
                markets[coin_id] = {
                    "provider": "CoinGecko",
                    "symbol": str(item.get("symbol") or "").upper(),
                    "name": str(item.get("name") or coin_id),
                    "price": number(item.get("current_price")),
                    "marketCap": number(item.get("market_cap")),
                    "volume24h": number(item.get("total_volume")),
                    "change1h": number(item.get("price_change_percentage_1h_in_currency")),
                    "change24h": number(item.get("price_change_percentage_24h_in_currency") or item.get("price_change_percentage_24h")),
                    "change7d": number(item.get("price_change_percentage_7d_in_currency")),
                    "lastUpdated": str(item.get("last_updated") or ""),
                    "fetchedAt": fetched_at,
                }
            if not markets:
                raise RuntimeError("coins/markets returned no configured assets")
            # Do not clear the last good snapshot unless a complete refresh
            # has succeeded.  This keeps a transient API failure from turning
            # a fresh market fact into a misleading empty value.
            signals["cryptoMarkets"] = markets
            signals["cryptoFetchedAt"] = fetched_at
            signals["cryptoSourceAsOf"] = max(
                str(item.get("lastUpdated") or item.get("fetchedAt") or "")
                for item in markets.values()
            )
            self.status(
                signals,
                "CoinGecko",
                True,
                "coins/markets refreshed",
                dataUsable=True,
            )
            self.persist_crypto_market_snapshot(signals)
            return True
        except Exception as error:  # noqa: BLE001
            self.status_for_error(signals, "CoinGecko", "", error)
            # Persist the failed attempt together with the last good market
            # facts so cache-only reasoning can distinguish stale/partial
            # evidence from a genuinely missing dataset.
            self.persist_crypto_market_snapshot(signals)
            return False

    def add_fred(self, signals: Dict[str, object]) -> None:
        if not self.external_api_enabled("externalFredEnabled"):
            return
        api_key = str(self.settings.get("fredApiKey") or "").strip()
        if not api_key:
            return
        series_ids = self.limited_targets(
            signals,
            "FRED",
            [item.upper() for item in symbol_list(self.settings.get("externalFredSeries") or "DGS10,DGS2,DFF")],
            "externalFredMaxSeries",
            5,
        )
        macro = signals.setdefault("macro", {})
        macro["series"] = {}
        timeout_seconds = self.fred_timeout_seconds()
        for series_id in series_ids:
            try:
                def fetch_series():
                    url = "https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode({
                        "series_id": series_id,
                        "api_key": api_key,
                        "file_type": "json",
                        # FRED publishes these series daily. Keep enough source
                        # observations to distinguish a real rate move from a
                        # monitor refresh of the same published value.
                        "limit": "25",
                        "sort_order": "desc",
                    })
                    payload = self.fetch_json_with_timeout(
                        url,
                        {"Accept": "application/json"},
                        "externalFredTimeoutSeconds",
                        fallback_timeout=timeout_seconds,
                        minimum_timeout=2.0,
                        maximum_timeout=20.0,
                    )
                    observations = payload.get("observations") if isinstance(payload.get("observations"), list) else []
                    valid_observations = []
                    for observation in observations:
                        if not isinstance(observation, dict):
                            continue
                        raw_value = observation.get("value")
                        if raw_value in (None, "", "."):
                            continue
                        try:
                            value = float(raw_value)
                        except (TypeError, ValueError):
                            continue
                        valid_observations.append({
                            "date": str(observation.get("date") or ""),
                            "value": value,
                        })
                    if not valid_observations:
                        raise RuntimeError("empty observations")
                    latest = valid_observations[0]
                    result = {
                        "provider": "FRED",
                        "date": str(latest.get("date") or ""),
                        "value": number(latest.get("value")),
                        "observationDate": str(latest.get("date") or ""),
                        "sourceAsOf": str(latest.get("date") or ""),
                        "observationCount": len(valid_observations),
                        "changeBasis": "fred-published-observations",
                    }
                    lookbacks = ((1, "delta1dBp"), (5, "delta5dBp"), (20, "delta20dBp"))
                    for index, field in lookbacks:
                        if len(valid_observations) <= index:
                            continue
                        prior = valid_observations[index]
                        result[field] = (number(latest.get("value")) - number(prior.get("value"))) * 100
                        result[field.replace("delta", "comparison", 1).replace("Bp", "Date")] = str(prior.get("date") or "")
                    if len(valid_observations) > 1:
                        previous = valid_observations[1]
                        result["previousValue"] = number(previous.get("value"))
                        result["previousDate"] = str(previous.get("date") or "")
                        result["deltaBp"] = result.get("delta1dBp", 0.0)
                        result["deltaPctPoint"] = number(latest.get("value")) - number(previous.get("value"))
                    return result

                macro["series"][series_id] = self.guarded_call("FRED", "series:" + series_id, fetch_series)
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "FRED", series_id + " ", error)
        series = macro.get("series") or {}
        if "DGS10" in series and "DGS2" in series:
            macro["yieldSpread10y2y"] = number(series["DGS10"].get("value")) - number(series["DGS2"].get("value"))
            dgs10_previous = series["DGS10"].get("previousValue")
            dgs2_previous = series["DGS2"].get("previousValue")
            if dgs10_previous not in (None, "") and dgs2_previous not in (None, ""):
                previous_spread = number(dgs10_previous) - number(dgs2_previous)
                macro["previousYieldSpread10y2y"] = previous_spread
                macro["yieldSpread10y2yDeltaBp"] = (
                    number(macro.get("yieldSpread10y2y")) - previous_spread
                ) * 100
                previous_dates = [
                    str(item.get("previousDate") or "")
                    for item in [series["DGS10"], series["DGS2"]]
                    if str(item.get("previousDate") or "")
                ]
                macro["yieldSpreadPreviousDate"] = min(previous_dates) if previous_dates else ""
            observation_dates = [
                str(item.get("date") or "")
                for item in [series["DGS10"], series["DGS2"]]
                if str(item.get("date") or "")
            ]
            macro["yieldSpreadObservationDate"] = min(observation_dates) if observation_dates else ""

    def add_fx_rates(self, signals: Dict[str, object], positions: List[Position] = None) -> None:
        assignments = symbol_assignments(self.settings.get("fxRates") or "")
        rates: Dict[str, object] = {}
        fetched_at = str(signals.get("fetchedAt") or utc_now_iso())
        broker_rates = broker_fx_rates_from_positions(positions or [], fetched_at=fetched_at)
        live_rates = self.live_fx_rates(signals, sorted(assignments.keys()))
        currencies = set(assignments.keys()) | {
            str(item.get("base") or "").upper().strip()
            for item in broker_rates.values()
            if isinstance(item, dict)
        }
        for currency in sorted(currencies):
            raw_rate = assignments.get(currency)
            base = str(currency or "").upper().strip()
            if not base or base == "KRW":
                continue
            live = live_rates.get(base) if isinstance(live_rates.get(base), dict) else {}
            broker = broker_rates.get(base + "KRW") if isinstance(broker_rates.get(base + "KRW"), dict) else {}
            broker_rate = number(broker.get("rate"))
            live_rate = number(live.get("rate"))
            fallback_rate = number(raw_rate)
            rate = broker_rate or live_rate or fallback_rate
            if rate <= 0:
                continue
            pair = base + "KRW"
            source_type = FALLBACK_FX_SOURCE_TYPE
            evidence_strength = "fallback"
            provider = "RuntimeSettings"
            last_updated = ""
            if broker_rate:
                source_type = BROKER_FX_SOURCE_TYPE
                evidence_strength = "account_applied"
                provider = str(broker.get("provider") or "BrokerAccount")
                last_updated = str(broker.get("lastUpdated") or "")
            elif live_rate:
                source_type = str(live.get("sourceType") or DAILY_MARKET_FX_SOURCE_TYPE)
                evidence_strength = str(live.get("evidenceStrength") or "daily_market")
                provider = str(live.get("provider") or "Alpha Vantage")
                last_updated = str(live.get("lastUpdated") or "")
            row = {
                "provider": provider,
                "base": base,
                "quote": "KRW",
                "rate": rate,
                "value": rate,
                "lastUpdated": last_updated,
                "fetchedAt": fetched_at,
                "sourceType": source_type,
                "evidenceStrength": evidence_strength,
            }
            if broker_rate:
                row.update({
                    "valuationRate": broker_rate,
                    "valuationProvider": str(broker.get("provider") or provider),
                    "valuationSourceType": BROKER_FX_SOURCE_TYPE,
                    "derivedFrom": str(broker.get("derivedFrom") or ""),
                    "sampleCount": int(number(broker.get("sampleCount")) or 0),
                })
            if live_rate:
                row.update({
                    "marketRate": live_rate,
                    "marketProvider": str(live.get("provider") or "Alpha Vantage"),
                    "marketSourceType": str(live.get("sourceType") or DAILY_MARKET_FX_SOURCE_TYPE),
                    "marketLastUpdated": str(live.get("lastUpdated") or ""),
                    "marketFetchedAt": str(live.get("fetchedAt") or ""),
                    "marketCacheStatus": str(live.get("cacheStatus") or ""),
                })
            if fallback_rate:
                row["fallbackRate"] = fallback_rate
                row["fallbackProvider"] = "RuntimeSettings"
            rates[pair] = row
        if rates:
            signals["fxRates"] = rates

    def refresh_broker_fx_rates(self, signals: Dict[str, object], positions: List[Position] = None) -> None:
        if not isinstance(signals, dict):
            return
        fetched_at = str(signals.get("fetchedAt") or utc_now_iso())
        broker_rates = broker_fx_rates_from_positions(positions or [], fetched_at=fetched_at)
        if not broker_rates:
            return
        rates = signals.setdefault("fxRates", {})
        if not isinstance(rates, dict):
            rates = {}
            signals["fxRates"] = rates
        for pair, broker in broker_rates.items():
            if not isinstance(broker, dict):
                continue
            existing = rates.get(pair) if isinstance(rates.get(pair), dict) else {}
            rate = number(broker.get("rate"))
            if rate <= 0:
                continue
            row = dict(existing)
            is_market_fx = row.get("sourceType") in {LIVE_MARKET_FX_SOURCE_TYPE, DAILY_MARKET_FX_SOURCE_TYPE}
            previous_live_rate = number(row.get("marketRate") or (row.get("rate") if is_market_fx else 0.0))
            previous_live_provider = str(row.get("marketProvider") or (row.get("provider") if is_market_fx else "") or "")
            previous_live_updated = str(row.get("marketLastUpdated") or (row.get("lastUpdated") if is_market_fx else "") or "")
            previous_live_source_type = str(row.get("marketSourceType") or (row.get("sourceType") if is_market_fx else "") or "")
            previous_live_fetched_at = str(row.get("marketFetchedAt") or (row.get("fetchedAt") if is_market_fx else "") or "")
            previous_live_cache_status = str(row.get("marketCacheStatus") or (row.get("cacheStatus") if is_market_fx else "") or "")
            row.update({
                "provider": str(broker.get("provider") or "BrokerAccount"),
                "base": str(broker.get("base") or pair[:3]).upper(),
                "quote": str(broker.get("quote") or "KRW").upper(),
                "rate": rate,
                "value": rate,
                "lastUpdated": str(broker.get("lastUpdated") or row.get("lastUpdated") or ""),
                "fetchedAt": fetched_at,
                "sourceType": BROKER_FX_SOURCE_TYPE,
                "evidenceStrength": "account_applied",
                "valuationRate": rate,
                "valuationProvider": str(broker.get("provider") or "BrokerAccount"),
                "valuationSourceType": BROKER_FX_SOURCE_TYPE,
                "derivedFrom": str(broker.get("derivedFrom") or row.get("derivedFrom") or ""),
                "sampleCount": int(number(broker.get("sampleCount")) or number(row.get("sampleCount")) or 0),
            })
            if previous_live_rate:
                row["marketRate"] = previous_live_rate
                row["marketProvider"] = previous_live_provider or "Alpha Vantage"
                row["marketSourceType"] = previous_live_source_type or DAILY_MARKET_FX_SOURCE_TYPE
                row["marketLastUpdated"] = previous_live_updated
                row["marketFetchedAt"] = previous_live_fetched_at
                row["marketCacheStatus"] = previous_live_cache_status
            rates[pair] = row

    def live_fx_rates(self, signals: Dict[str, object], currencies: List[str]) -> Dict[str, Dict[str, object]]:
        if not self.fx_live_rate_enabled():
            return {}
        api_key = str(self.settings.get("alphaVantageApiKey") or "").strip()
        rows: Dict[str, Dict[str, object]] = {}
        for currency in currencies:
            base = str(currency or "").upper().strip()
            if not base or base == "KRW":
                continue
            cached = self.alpha_fx_daily_cache(base)
            if cached:
                rows[base] = cached
                self.status(signals, "Alpha Vantage", True, "fx:" + base + "KRW daily cache")
                continue
            try:
                def fetch_rate():
                    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode({
                        "function": "CURRENCY_EXCHANGE_RATE",
                        "from_currency": base,
                        "to_currency": "KRW",
                        "apikey": api_key,
                    })
                    payload = self.fetch_json(url, {"Accept": "application/json"})
                    data = payload.get("Realtime Currency Exchange Rate") if isinstance(payload.get("Realtime Currency Exchange Rate"), dict) else payload
                    rate = number(
                        data.get("5. Exchange Rate")
                        or data.get("exchangeRate")
                        or data.get("rate")
                        or data.get("value")
                    ) if isinstance(data, dict) else 0.0
                    if not rate:
                        reason = ""
                        if isinstance(payload, dict):
                            reason = str(payload.get("Note") or payload.get("Information") or payload.get("Error Message") or "")
                        raise RuntimeError(reason or "empty FX rate")
                    return {
                        "provider": "Alpha Vantage",
                        "rate": rate,
                        "lastUpdated": str(
                            data.get("6. Last Refreshed")
                            or data.get("lastRefreshed")
                            or data.get("lastUpdated")
                            or ""
                        ) if isinstance(data, dict) else "",
                        "fetchedAt": utc_now_iso(),
                        "sourceType": DAILY_MARKET_FX_SOURCE_TYPE,
                        "evidenceStrength": "daily_market",
                        "cacheStatus": "fresh-fetch",
                    }

                row = self.guarded_call("Alpha Vantage", "fx:" + base + "KRW", fetch_rate)
                self.save_alpha_fx_daily_cache(base, row)
                rows[base] = row
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "Alpha Vantage", "fx:" + base + "KRW", error)
                stale = self.alpha_fx_stored_cache(base)
                if stale:
                    rows[base] = stale
        return rows

    def alpha_fx_daily_cache_key(self, base: str) -> str:
        return "alpha-vantage:fx-daily:" + str(base or "").upper().strip() + "KRW"

    def alpha_fx_daily_cache_hours(self) -> int:
        return self.int_setting("externalFxRateFetchIntervalHours", 24, 1)

    def alpha_fx_daily_cache(self, base: str) -> Dict[str, object]:
        row = self.alpha_fx_stored_cache(base)
        if not row:
            return {}
        fetched_at = parse_iso(str(row.get("fetchedAt") or ""))
        if not fetched_at:
            return {}
        if datetime.now(timezone.utc) - fetched_at >= timedelta(hours=self.alpha_fx_daily_cache_hours()):
            return {}
        cached = dict(row)
        cached["cacheStatus"] = "daily-cache"
        return cached

    def alpha_fx_stored_cache(self, base: str) -> Dict[str, object]:
        key = self.alpha_fx_daily_cache_key(base)
        entry = self.provider_state.get(key) if isinstance(getattr(self, "provider_state", None), dict) else {}
        if not isinstance(entry, dict):
            return {}
        rate = number(entry.get("rate"))
        if rate <= 0:
            return {}
        row = {
            "provider": "Alpha Vantage",
            "base": str(base or "").upper().strip(),
            "quote": "KRW",
            "rate": rate,
            "lastUpdated": str(entry.get("lastUpdated") or ""),
            "fetchedAt": str(entry.get("fetchedAt") or ""),
            "sourceType": DAILY_MARKET_FX_SOURCE_TYPE,
            "evidenceStrength": "daily_market",
            "cacheStatus": str(entry.get("cacheStatus") or "stored-cache"),
        }
        return row

    def save_alpha_fx_daily_cache(self, base: str, row: Dict[str, object]) -> None:
        if not isinstance(getattr(self, "provider_state", None), dict):
            self.provider_state = {}
        rate = number((row or {}).get("rate"))
        if rate <= 0:
            return
        self.provider_state[self.alpha_fx_daily_cache_key(base)] = {
            "provider": "Alpha Vantage",
            "base": str(base or "").upper().strip(),
            "quote": "KRW",
            "rate": rate,
            "lastUpdated": str((row or {}).get("lastUpdated") or ""),
            "fetchedAt": str((row or {}).get("fetchedAt") or utc_now_iso()),
            "sourceType": DAILY_MARKET_FX_SOURCE_TYPE,
            "evidenceStrength": "daily_market",
            "cacheStatus": "stored-cache",
        }

    def add_opendart(self, signals: Dict[str, object], positions: List[Position]) -> None:
        if not self.external_api_enabled("externalDartEnabled"):
            return
        api_key = str(self.settings.get("opendartApiKey") or "").strip()
        if not api_key:
            return
        corp_codes = symbol_assignments(self.settings.get("externalDartCorpCodes") or "")
        cached_codes = self.provider_state.get("opendart:corp-code-assignments")
        if isinstance(cached_codes, dict):
            corp_codes.update({
                str(symbol or "").zfill(6): str(code or "").zfill(8)
                for symbol, code in cached_codes.items()
                if str(symbol or "").strip() and str(code or "").strip()
            })
        now = datetime.now(timezone.utc)
        lookback_days = int(number(self.settings.get("externalDartLookbackDays")) or 14)
        bgn_de = (now - timedelta(days=max(1, lookback_days))).strftime("%Y%m%d")
        end_de = now.strftime("%Y%m%d")
        positions_by_symbol = {str(position.symbol or "").upper(): position for position in positions}
        selected_symbols = self.limited_targets(
            signals,
            "OpenDART",
            self.dart_symbols(positions),
            "externalDartMaxSymbols",
            5,
        )
        missing_symbols = [symbol for symbol in selected_symbols if symbol not in corp_codes]
        if missing_symbols:
            try:
                raw_directory = self.guarded_call(
                    "OpenDART",
                    "corp-code-directory",
                    lambda: self.fetch_bytes(
                        OPENDART_CORP_CODE_URL + "?" + urllib.parse.urlencode({"crtfc_key": api_key}),
                        {"Accept": "application/zip,application/xml"},
                    ),
                )
                # The directory response already contains every listed
                # company. Parse it once and persist the complete mapping so
                # round-robin batches do not download the same ZIP again for
                # each newly selected symbol.
                corp_codes.update(parse_opendart_corp_codes(raw_directory))
                self.provider_state["opendart:corp-code-assignments"] = dict(corp_codes)
            except Exception as error:  # noqa: BLE001 - configured mappings remain usable.
                self.status_for_error(signals, "OpenDART", "corp-code directory ", error)
        for symbol in selected_symbols:
            raw_corp_code = str(corp_codes.get(symbol) or "").strip()
            if not raw_corp_code:
                continue
            corp_code = raw_corp_code.zfill(8)
            position = positions_by_symbol.get(symbol)
            try:
                def fetch_disclosure():
                    url = "https://opendart.fss.or.kr/api/list.json?" + urllib.parse.urlencode({
                        "crtfc_key": api_key,
                        "corp_code": corp_code,
                        "bgn_de": bgn_de,
                        "end_de": end_de,
                        "page_no": "1",
                        "page_count": "5",
                    })
                    payload = self.fetch_json(url, {"Accept": "application/json"})
                    status = str(payload.get("status") or "")
                    if status and status != "000":
                        raise RuntimeError(str(payload.get("message") or status))
                    items = payload.get("list") if isinstance(payload.get("list"), list) else []
                    latest = items[0] if items else {}
                    if not latest:
                        return {}
                    normalized_items = [
                        {
                            "provider": "OpenDART",
                            "corpCode": corp_code,
                            "corpName": str(item.get("corp_name") or (position.name if position else symbol)),
                            "reportName": str(item.get("report_nm") or ""),
                            "receiptNo": str(item.get("rcept_no") or ""),
                            "receiptDate": str(item.get("rcept_dt") or ""),
                            "flrName": str(item.get("flr_nm") or ""),
                            "remarks": str(item.get("rm") or ""),
                        }
                        for item in items
                        if isinstance(item, dict) and str(item.get("rcept_no") or "").strip()
                    ]
                    return {
                        "provider": "OpenDART",
                        "corpCode": corp_code,
                        "corpName": str(latest.get("corp_name") or (position.name if position else symbol)),
                        "reportName": str(latest.get("report_nm") or ""),
                        "receiptNo": str(latest.get("rcept_no") or ""),
                        "receiptDate": str(latest.get("rcept_dt") or ""),
                        "count": len(items),
                        # Preserve bounded filing metadata so collection does
                        # not silently discard every filing except the newest.
                        "items": normalized_items,
                    }

                disclosure = self.guarded_call("OpenDART", "list:" + symbol, fetch_disclosure)
                if disclosure:
                    disclosure["fetchedAt"] = utc_now_iso()
                    if self.dart_company_fundamentals_enabled():
                        self.attach_opendart_company_facts(
                            signals,
                            disclosure,
                            symbol,
                            corp_code,
                            api_key,
                            now,
                        )
                    receipt_no = str(disclosure.get("receiptNo") or "").strip()
                    if receipt_no and self.dart_document_text_enabled():
                        try:
                            def fetch_document():
                                url = "https://opendart.fss.or.kr/api/document.xml?" + urllib.parse.urlencode({
                                    "crtfc_key": api_key,
                                    "rcept_no": receipt_no,
                                })
                                return self.fetch_bytes(url, {"Accept": "application/zip,application/xml"})

                            raw_document = self.guarded_call("OpenDART", "document:" + symbol, fetch_document)
                            document_text = dart_document_text(raw_document, self.dart_document_text_max_chars())
                            disclosure.update({
                                "documentText": document_text,
                                "documentTextPreview": document_text[:700],
                                "documentTextQuality": "body" if len(document_text) >= 120 else "insufficient",
                            })
                        except Exception as error:  # noqa: BLE001 - list metadata remains available with an explicit fallback status.
                            disclosure.update({"documentText": "", "documentTextPreview": "", "documentTextQuality": "unavailable"})
                            self.status_for_error(signals, "OpenDART", symbol + " document ", error)
                    signals["dartDisclosures"][symbol] = disclosure
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "OpenDART", symbol + " ", error)

    def attach_opendart_company_facts(
        self,
        signals: Dict[str, object],
        disclosure: Dict[str, object],
        symbol: str,
        corp_code: str,
        api_key: str,
        now: datetime,
    ) -> None:
        """Attach bounded official company, statement and executive facts.

        A failure in an optional endpoint must not discard the already valid
        disclosure list.  The raw provider payload is reduced here because
        the external-signal cache is a current source record, not a DART data
        warehouse.
        """

        def fetch_api(endpoint: str, params: Dict[str, object]) -> Dict[str, object]:
            payload = self.fetch_json(
                "https://opendart.fss.or.kr/api/" + endpoint + ".json?" + urllib.parse.urlencode({
                    "crtfc_key": api_key,
                    "corp_code": corp_code,
                    **params,
                }),
                {"Accept": "application/json"},
            )
            status = str(payload.get("status") or "")
            if status and status != "000":
                raise RuntimeError(str(payload.get("message") or status))
            return payload

        try:
            company = self.guarded_call(
                "OpenDART",
                "company:" + symbol,
                lambda: fetch_api("company", {}),
            )
            if isinstance(company, dict):
                disclosure["company"] = {
                    key: company.get(key)
                    for key in (
                        "corp_name", "corp_name_eng", "stock_name", "stock_code",
                        "ceo_nm", "corp_cls", "jurir_no", "bizr_no", "adres",
                        "hm_url", "ir_url", "induty_code", "est_dt", "acc_mt",
                    )
                    if company.get(key) not in (None, "")
                }
        except Exception as error:  # noqa: BLE001 - list data remains usable.
            self.status_for_error(signals, "OpenDART", symbol + " company ", error)

        report_candidates = []
        if now.month >= 11:
            report_candidates.append((str(now.year), "11014"))
        elif now.month >= 8:
            report_candidates.append((str(now.year), "11012"))
        elif now.month >= 5:
            report_candidates.append((str(now.year), "11013"))
        report_candidates.append((str(now.year - 1), "11011"))
        for business_year, report_code in report_candidates:
            try:
                statements = self.guarded_call(
                    "OpenDART",
                    "financials:" + symbol + ":" + business_year + ":" + report_code,
                    lambda year=business_year, code=report_code: fetch_api("fnlttSinglAcntAll", {
                        "bsns_year": year,
                        "reprt_code": code,
                        "fs_div": "CFS",
                    }),
                )
                rows = statements.get("list") if isinstance(statements, dict) and isinstance(statements.get("list"), list) else []
                if rows:
                    disclosure["financialStatements"] = [
                        {
                            key: row.get(key)
                            for key in (
                                "rcept_no", "reprt_code", "bsns_year", "corp_code",
                                "sj_div", "sj_nm", "account_id", "account_nm",
                                "account_detail", "thstrm_nm", "thstrm_dt", "thstrm_amount",
                                "frmtrm_nm", "frmtrm_dt", "frmtrm_amount",
                                "bfefrmtrm_nm", "bfefrmtrm_dt", "bfefrmtrm_amount",
                                "ord", "currency",
                            )
                            if row.get(key) not in (None, "")
                        }
                        for row in rows[:180]
                        if isinstance(row, dict)
                    ]
                    disclosure["financialStatementBasis"] = {
                        "businessYear": business_year,
                        "reportCode": report_code,
                        "scope": "CFS",
                    }
                    break
            except Exception as error:  # noqa: BLE001 - try the annual fallback.
                if (business_year, report_code) == report_candidates[-1]:
                    self.status_for_error(signals, "OpenDART", symbol + " financials ", error)

        executive_year = str(now.year - 1)
        try:
            executives = self.guarded_call(
                "OpenDART",
                "executives:" + symbol + ":" + executive_year,
                lambda: fetch_api("exctvSttus", {
                    "bsns_year": executive_year,
                    "reprt_code": "11011",
                }),
            )
            rows = executives.get("list") if isinstance(executives, dict) and isinstance(executives.get("list"), list) else []
            if rows:
                disclosure["executives"] = [
                    {
                        key: row.get(key)
                        for key in (
                            "nm", "sexdstn", "birth_ym", "ofcps", "rgist_exctv_at",
                            "fte_at", "chrg_job", "main_career", "mxmm_shrholdr_relate",
                            "hffc_pd", "tenure_end_on", "stlm_dt",
                        )
                        if row.get(key) not in (None, "")
                    }
                    for row in rows[:40]
                    if isinstance(row, dict)
                ]
        except Exception as error:  # noqa: BLE001 - governance coverage is explicit but optional.
            self.status_for_error(signals, "OpenDART", symbol + " executives ", error)

    def dart_symbols(self, positions: List[Position]) -> List[str]:
        if not self.external_api_enabled("externalDartEnabled"):
            return []
        symbols = []
        seen = set()
        for position in positions:
            symbol = str(position.symbol or "").upper()
            if not symbol or symbol in seen or not symbol.isdigit():
                continue
            seen.add(symbol)
            symbols.append(symbol)
        return symbols
