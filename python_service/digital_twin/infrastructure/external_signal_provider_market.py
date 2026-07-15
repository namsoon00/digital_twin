import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from ..domain.market_data import number
from ..domain.portfolio_calculations import (
    BROKER_FX_SOURCE_TYPE,
    FALLBACK_FX_SOURCE_TYPE,
    LIVE_MARKET_FX_SOURCE_TYPE,
    broker_fx_rates_from_positions,
)
from ..domain.portfolio import Position, utc_now_iso
from .external_signal_utils import symbol_assignments, symbol_list


class ExternalSignalMarketMixin:
    def add_coingecko(self, signals: Dict[str, object]) -> None:
        if not self.external_api_enabled("externalCoinGeckoEnabled"):
            return
        ids = self.limited_targets(
            signals,
            "CoinGecko",
            symbol_list(self.settings.get("externalCryptoIds") or "bitcoin,ethereum"),
            "externalCryptoMaxIds",
            50,
        )
        if not ids:
            return
        headers = {"Accept": "application/json", "User-Agent": "DigitalTwin/1.0"}
        api_key = str(self.settings.get("coingeckoApiKey") or "").strip()
        if api_key:
            headers["x-cg-demo-api-key"] = api_key
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
            for item in payload:
                if not isinstance(item, dict):
                    continue
                coin_id = str(item.get("id") or "").lower()
                if not coin_id:
                    continue
                signals["cryptoMarkets"][coin_id] = {
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
                }
        except Exception as error:  # noqa: BLE001
            self.status_for_error(signals, "CoinGecko", "", error)

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
        for series_id in series_ids:
            try:
                def fetch_series():
                    url = "https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode({
                        "series_id": series_id,
                        "api_key": api_key,
                        "file_type": "json",
                        "limit": "2",
                        "sort_order": "desc",
                    })
                    payload = self.fetch_json(url, {"Accept": "application/json"})
                    observations = payload.get("observations") if isinstance(payload.get("observations"), list) else []
                    latest = next((item for item in observations if number(item.get("value"))), None)
                    if not latest:
                        raise RuntimeError("empty observations")
                    return {
                        "provider": "FRED",
                        "date": str(latest.get("date") or ""),
                        "value": number(latest.get("value")),
                    }

                macro["series"][series_id] = self.guarded_call("FRED", "series:" + series_id, fetch_series)
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "FRED", series_id + " ", error)
        series = macro.get("series") or {}
        if "DGS10" in series and "DGS2" in series:
            macro["yieldSpread10y2y"] = number(series["DGS10"].get("value")) - number(series["DGS2"].get("value"))

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
                source_type = LIVE_MARKET_FX_SOURCE_TYPE
                evidence_strength = "live_market"
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
                    "marketSourceType": LIVE_MARKET_FX_SOURCE_TYPE,
                    "marketLastUpdated": str(live.get("lastUpdated") or ""),
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
            previous_live_rate = number(row.get("marketRate") or (row.get("rate") if row.get("sourceType") == LIVE_MARKET_FX_SOURCE_TYPE else 0.0))
            previous_live_provider = str(row.get("marketProvider") or (row.get("provider") if row.get("sourceType") == LIVE_MARKET_FX_SOURCE_TYPE else "") or "")
            previous_live_updated = str(row.get("marketLastUpdated") or (row.get("lastUpdated") if row.get("sourceType") == LIVE_MARKET_FX_SOURCE_TYPE else "") or "")
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
                row["marketSourceType"] = LIVE_MARKET_FX_SOURCE_TYPE
                row["marketLastUpdated"] = previous_live_updated
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
                    }

                rows[base] = self.guarded_call("Alpha Vantage", "fx:" + base + "KRW", fetch_rate)
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "Alpha Vantage", "fx:" + base + "KRW", error)
        return rows

    def add_opendart(self, signals: Dict[str, object], positions: List[Position]) -> None:
        if not self.external_api_enabled("externalDartEnabled"):
            return
        api_key = str(self.settings.get("opendartApiKey") or "").strip()
        if not api_key:
            return
        corp_codes = symbol_assignments(self.settings.get("externalDartCorpCodes") or "")
        if not corp_codes:
            return
        now = datetime.now(timezone.utc)
        lookback_days = int(number(self.settings.get("externalDartLookbackDays")) or 14)
        bgn_de = (now - timedelta(days=max(1, lookback_days))).strftime("%Y%m%d")
        end_de = now.strftime("%Y%m%d")
        positions_by_symbol = {str(position.symbol or "").upper(): position for position in positions}
        for symbol in self.limited_targets(signals, "OpenDART", self.dart_symbols(positions), "externalDartMaxSymbols", 5):
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
                    return {
                        "provider": "OpenDART",
                        "corpCode": corp_code,
                        "corpName": str(latest.get("corp_name") or (position.name if position else symbol)),
                        "reportName": str(latest.get("report_nm") or ""),
                        "receiptNo": str(latest.get("rcept_no") or ""),
                        "receiptDate": str(latest.get("rcept_dt") or ""),
                        "count": len(items),
                    }

                disclosure = self.guarded_call("OpenDART", "list:" + symbol, fetch_disclosure)
                if disclosure:
                    signals["dartDisclosures"][symbol] = disclosure
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "OpenDART", symbol + " ", error)

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
