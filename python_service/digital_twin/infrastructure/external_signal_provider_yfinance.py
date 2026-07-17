import io
import logging
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from typing import Dict, Iterable, List, Tuple

from ..domain.market_data import number
from ..domain.portfolio import Position, utc_now_iso
from ..domain.symbol_universe import normalize_market


def json_safe_value(value):
    if value is None:
        return None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return None if value != value else value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "isoformat") and callable(value.isoformat):
        try:
            return value.isoformat()
        except Exception:
            pass
    if hasattr(value, "item") and callable(value.item):
        try:
            return json_safe_value(value.item())
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(key): json_safe_value(item) for key, item in value.items() if json_safe_value(item) is not None}
    if isinstance(value, (list, tuple, set)):
        return [item for item in (json_safe_value(item) for item in value) if item is not None]
    if hasattr(value, "reset_index") and hasattr(value, "to_dict"):
        try:
            records = value.reset_index().to_dict(orient="records")
            return json_safe_value(records[:40])
        except Exception:
            pass
    try:
        text = str(value)
    except Exception:
        return None
    if text.lower() in {"nan", "nat", "none"}:
        return None
    return text[:500]


def is_empty_value(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return True
    if hasattr(value, "empty"):
        try:
            return bool(value.empty)
        except Exception:
            return False
    return False


def is_expected_yfinance_missing_error(error: Exception) -> bool:
    text = str(error or "").lower()
    if not text:
        return False
    return any(marker in text for marker in [
        "no fundamentals data found",
        "quotesummary",
        '"code":"not found"',
        '"code": "not found"',
        "404",
    ])


def parse_iso_datetime(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text + "T00:00:00+00:00")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def age_minutes(value: object, now=None):
    parsed = parse_iso_datetime(value)
    if not parsed:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0, int((current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() // 60))


def first_news_timestamp(items: object) -> str:
    if not isinstance(items, list):
        return ""
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        for key in ["pubDate", "displayTime", "providerPublishTime", "publishedAt", "date"]:
            value = item.get(key) if key in item else content.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, (int, float)):
                try:
                    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")
                except (OSError, ValueError):
                    continue
            return str(value)
    return ""


@contextmanager
def quiet_yfinance_io():
    logger = logging.getLogger("yfinance")
    previous_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            yield
    finally:
        logger.setLevel(previous_level)


def frame_rows(frame, limit: int = 40) -> List[Dict[str, object]]:
    if is_empty_value(frame) or not hasattr(frame, "reset_index"):
        return []
    try:
        rows_frame = frame.reset_index()
        if limit:
            rows_frame = rows_frame.tail(max(1, int(limit or 1)))
        rows: List[Dict[str, object]] = []
        for raw in rows_frame.to_dict(orient="records"):
            row = {str(key): json_safe_value(value) for key, value in raw.items()}
            rows.append({key: value for key, value in row.items() if value is not None})
        return rows
    except Exception:
        return []


def series_rows(series, limit: int = 40) -> List[Dict[str, object]]:
    if is_empty_value(series) or not hasattr(series, "tail"):
        return []
    try:
        rows = []
        for index, value in series.tail(max(1, int(limit or 1))).items():
            safe_value = json_safe_value(value)
            if safe_value is None:
                continue
            rows.append({"date": json_safe_value(index), "value": safe_value})
        return rows
    except Exception:
        return []


def statement_rows(frame, max_periods: int = 4) -> List[Dict[str, object]]:
    if is_empty_value(frame) or not hasattr(frame, "iterrows"):
        return []
    try:
        columns = list(frame.columns)[: max(1, int(max_periods or 1))]
        rows: List[Dict[str, object]] = []
        for metric, values in frame.iterrows():
            payload = {}
            for column in columns:
                safe_value = json_safe_value(values.get(column))
                if safe_value is not None:
                    payload[str(json_safe_value(column))] = safe_value
            if payload:
                rows.append({"metric": str(metric), "values": payload})
        return rows
    except Exception:
        return []


def latest_history_quote(history_rows: List[Dict[str, object]]) -> Dict[str, object]:
    if not history_rows:
        return {}
    latest = history_rows[-1]
    previous = history_rows[-2] if len(history_rows) > 1 else {}
    price = number(latest.get("Close") or latest.get("close"))
    previous_close = number(previous.get("Close") or previous.get("close"))
    change = price - previous_close if price and previous_close else 0.0
    change_percent = (change / previous_close * 100) if previous_close else 0.0
    return {
        "price": price,
        "previousClose": previous_close,
        "change": change,
        "changePercent": change_percent,
        "volume": number(latest.get("Volume") or latest.get("volume")),
        "latestTradingDay": str(latest.get("Date") or latest.get("Datetime") or latest.get("index") or ""),
    }


def option_summary(calls: List[Dict[str, object]], puts: List[Dict[str, object]]) -> Dict[str, object]:
    call_open_interest = sum(number(row.get("openInterest")) for row in calls)
    put_open_interest = sum(number(row.get("openInterest")) for row in puts)
    call_volume = sum(number(row.get("volume")) for row in calls)
    put_volume = sum(number(row.get("volume")) for row in puts)
    return {
        "callCount": len(calls),
        "putCount": len(puts),
        "callOpenInterest": call_open_interest,
        "putOpenInterest": put_open_interest,
        "putCallOpenInterestRatio": (put_open_interest / call_open_interest) if call_open_interest else 0.0,
        "callVolume": call_volume,
        "putVolume": put_volume,
        "putCallVolumeRatio": (put_volume / call_volume) if call_volume else 0.0,
    }


def pick_info(info: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(info, dict):
        return {}
    picked = {}
    for key, value in info.items():
        safe = json_safe_value(value)
        if safe in (None, "", [], {}):
            continue
        picked[str(key)] = safe
    return picked


def overview_from_yfinance(symbol: str, payload: Dict[str, object]) -> Dict[str, object]:
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    quote = payload.get("quote") if isinstance(payload.get("quote"), dict) else {}
    return {
        "provider": "yfinance",
        "symbol": symbol,
        "name": str(info.get("longName") or info.get("shortName") or symbol),
        "assetType": str(info.get("quoteType") or ""),
        "exchange": str(info.get("exchange") or ""),
        "currency": str(info.get("currency") or ""),
        "country": str(info.get("country") or ""),
        "sector": str(info.get("sector") or ""),
        "industry": str(info.get("industry") or ""),
        "latestQuarter": str(info.get("mostRecentQuarter") or ""),
        "marketCapitalization": number(info.get("marketCap")),
        "revenueTTM": number(info.get("totalRevenue")),
        "grossProfitTTM": number(info.get("grossProfits")),
        "ebitda": number(info.get("ebitda")),
        "profitMargin": number(info.get("profitMargins")),
        "operatingMarginTTM": number(info.get("operatingMargins")),
        "peRatio": number(info.get("trailingPE")),
        "pegRatio": number(info.get("pegRatio")),
        "forwardPE": number(info.get("forwardPE")),
        "beta": number(info.get("beta")),
        "dividendYield": number(info.get("dividendYield")),
        "analystTargetPrice": number((payload.get("analystPriceTargets") or {}).get("mean") or info.get("targetMeanPrice")),
        "currentPrice": number(quote.get("price") or info.get("currentPrice") or info.get("regularMarketPrice")),
    }


def earnings_report_from_yfinance(symbol: str, payload: Dict[str, object]) -> Dict[str, object]:
    earnings_rows = payload.get("earningsDates") if isinstance(payload.get("earningsDates"), list) else []
    latest = earnings_rows[-1] if earnings_rows else {}
    if not isinstance(latest, dict):
        latest = {}
    return {
        "provider": "yfinance",
        "symbol": symbol,
        "latestQuarter": {
            "fiscalDateEnding": str(latest.get("Earnings Date") or latest.get("index") or latest.get("Date") or ""),
            "reportedDate": str(latest.get("Earnings Date") or latest.get("index") or latest.get("Date") or ""),
            "reportedEPS": number(latest.get("Reported EPS")),
            "estimatedEPS": number(latest.get("EPS Estimate")),
            "surprise": number(latest.get("Surprise(%)")),
            "surprisePercentage": number(latest.get("Surprise(%)")),
        },
    }


YFINANCE_FRESHNESS_PROFILES = {
    "price": ("externalYFinancePriceMaxAgeMinutes", 30),
    "options": ("externalYFinanceOptionsMaxAgeMinutes", 30),
    "news": ("externalYFinanceNewsMaxAgeMinutes", 1440),
    "analyst": ("externalYFinanceAnalystMaxAgeMinutes", 10080),
    "fundamental": ("externalYFinanceFundamentalMaxAgeMinutes", 129600),
}

YFINANCE_MODULE_PROFILES = {
    "quote": "price",
    "history": "price",
    "historyMetadata": "price",
    "fastInfo": "price",
    "options": "options",
    "optionChains": "options",
    "news": "news",
    "analystPriceTargets": "analyst",
    "earningsEstimate": "analyst",
    "revenueEstimate": "analyst",
    "epsTrend": "analyst",
    "epsRevisions": "analyst",
    "recommendations": "analyst",
    "recommendationsSummary": "analyst",
    "upgradesDowngrades": "analyst",
    "institutionalHolders": "analyst",
    "mutualfundHolders": "analyst",
    "majorHolders": "analyst",
    "insiderTransactions": "analyst",
    "insiderPurchases": "analyst",
    "insiderRosterHolders": "analyst",
    "info": "fundamental",
    "calendar": "fundamental",
    "actions": "fundamental",
    "dividends": "fundamental",
    "splits": "fundamental",
    "capitalGains": "fundamental",
    "incomeStatement": "fundamental",
    "quarterlyIncomeStatement": "fundamental",
    "balanceSheet": "fundamental",
    "quarterlyBalanceSheet": "fundamental",
    "cashFlow": "fundamental",
    "quarterlyCashFlow": "fundamental",
    "earningsDates": "fundamental",
    "sustainability": "fundamental",
    "isin": "fundamental",
    "fundsData": "fundamental",
    "shares": "fundamental",
}


class ExternalSignalYFinanceMixin:
    def yfinance_enabled(self) -> bool:
        return self.external_api_enabled("externalYFinanceEnabled")

    def yfinance_target_symbols(self, positions: Iterable[Position]) -> List[Tuple[str, str]]:
        if not self.yfinance_enabled():
            return []
        targets: List[Tuple[str, str]] = []
        seen = set()
        for position in positions or []:
            if position.is_cash():
                continue
            symbol = str(position.symbol or "").upper().strip()
            if not symbol or symbol in seen:
                continue
            query = self.yfinance_query_symbol(position)
            if not query:
                continue
            seen.add(symbol)
            targets.append((symbol, query))
        limit = self.int_setting("externalYFinanceMaxSymbols", 8, 1)
        return targets[:limit]

    def yfinance_query_symbol(self, position: Position) -> str:
        symbol = str(position.symbol or "").upper().strip()
        if not symbol:
            return ""
        market = normalize_market(str(position.market or ""))
        if "." in symbol:
            return symbol
        if symbol.isdigit() and len(symbol) == 6:
            return symbol + (".KQ" if market == "KOSDAQ" else ".KS")
        return symbol

    def add_yfinance(self, signals: Dict[str, object], positions: List[Position]) -> None:
        if not self.yfinance_enabled():
            return
        try:
            import yfinance as yf  # noqa: PLC0415 - optional runtime dependency.
        except Exception as error:  # noqa: BLE001
            self.status_for_error(signals, "yfinance", "패키지 미설치 · ", error)
            return
        targets = self.yfinance_target_symbols(positions)
        if len(targets) < len([p for p in positions or [] if not p.is_cash()]):
            self.status(signals, "yfinance", True, "bulk cap " + str(len(targets)) + "/" + str(len([p for p in positions or [] if not p.is_cash()])))
        for symbol, query_symbol in targets:
            try:
                def fetch():
                    return self.fetch_yfinance_symbol(yf, symbol, query_symbol)

                payload = self.guarded_call("yfinance", "ticker:" + query_symbol, fetch)
                if payload:
                    signals.setdefault("yfinanceData", {})[symbol] = payload
                    self.merge_yfinance_summaries(signals, symbol, payload)
            except Exception as error:  # noqa: BLE001
                if is_expected_yfinance_missing_error(error):
                    self.status(signals, "yfinance", True, symbol + " fundamentals unavailable")
                else:
                    self.status_for_error(signals, "yfinance", symbol + " ", error)

    def fetch_yfinance_symbol(self, yf, symbol: str, query_symbol: str) -> Dict[str, object]:
        with quiet_yfinance_io():
            ticker = yf.Ticker(query_symbol)
        collected_at = utc_now_iso()
        errors: List[Dict[str, object]] = []
        expected_missing: List[Dict[str, object]] = []
        period = str(self.settings.get("externalYFinanceHistoryPeriod") or "1y")
        interval = str(self.settings.get("externalYFinanceHistoryInterval") or "1d")
        history_rows_limit = self.int_setting("externalYFinanceHistoryRows", 90, 1)
        table_rows_limit = self.int_setting("externalYFinanceTabularRows", 40, 1)
        statement_periods = self.int_setting("externalYFinanceFinancialPeriods", 4, 1)
        option_expirations = self.int_setting("externalYFinanceOptionExpirations", 2, 0)
        option_rows_limit = self.int_setting("externalYFinanceOptionsMaxRows", 40, 1)
        earnings_limit = self.int_setting("externalYFinanceEarningsLimit", 16, 1)
        news_limit = self.int_setting("externalYFinanceNewsLimit", 10, 0)

        def capture(name: str, getter, transform=None):
            try:
                with quiet_yfinance_io():
                    value = getter()
                if transform:
                    value = transform(value)
                else:
                    value = json_safe_value(value)
                return None if is_empty_value(value) else value
            except Exception as error:  # noqa: BLE001 - yfinance modules are best-effort.
                if is_expected_yfinance_missing_error(error):
                    expected_missing.append({
                        "section": name,
                        "status": "expected-missing",
                        "reason": "fundamentals-not-available",
                    })
                    return None
                errors.append({"section": name, "message": str(error)[:180]})
                return None

        history_rows = capture(
            "history",
            lambda: ticker.history(period=period, interval=interval, auto_adjust=False, actions=True),
            lambda value: frame_rows(value, history_rows_limit),
        ) or []
        payload: Dict[str, object] = {
            "provider": "yfinance",
            "symbol": symbol,
            "querySymbol": query_symbol,
            "collectedAt": collected_at,
            "historyPeriod": period,
            "historyInterval": interval,
            "quote": latest_history_quote(history_rows),
            "history": history_rows,
        }

        sections = {
            "historyMetadata": lambda: getattr(ticker, "history_metadata", {}),
            "fastInfo": lambda: dict(getattr(ticker, "fast_info", {}) or {}),
            "info": lambda: pick_info(ticker.get_info() if hasattr(ticker, "get_info") else getattr(ticker, "info", {})),
            "calendar": lambda: getattr(ticker, "calendar", {}),
            "actions": lambda: frame_rows(getattr(ticker, "actions", None), table_rows_limit),
            "dividends": lambda: series_rows(getattr(ticker, "dividends", None), table_rows_limit),
            "splits": lambda: series_rows(getattr(ticker, "splits", None), table_rows_limit),
            "capitalGains": lambda: series_rows(getattr(ticker, "capital_gains", None), table_rows_limit),
            "incomeStatement": lambda: statement_rows(getattr(ticker, "income_stmt", None), statement_periods),
            "quarterlyIncomeStatement": lambda: statement_rows(getattr(ticker, "quarterly_income_stmt", None), statement_periods),
            "balanceSheet": lambda: statement_rows(getattr(ticker, "balance_sheet", None), statement_periods),
            "quarterlyBalanceSheet": lambda: statement_rows(getattr(ticker, "quarterly_balance_sheet", None), statement_periods),
            "cashFlow": lambda: statement_rows(getattr(ticker, "cashflow", None), statement_periods),
            "quarterlyCashFlow": lambda: statement_rows(getattr(ticker, "quarterly_cashflow", None), statement_periods),
            "earningsDates": lambda: frame_rows(ticker.get_earnings_dates(limit=earnings_limit), earnings_limit) if hasattr(ticker, "get_earnings_dates") else [],
            "earningsEstimate": lambda: frame_rows(getattr(ticker, "earnings_estimate", None), table_rows_limit),
            "revenueEstimate": lambda: frame_rows(getattr(ticker, "revenue_estimate", None), table_rows_limit),
            "epsTrend": lambda: frame_rows(getattr(ticker, "eps_trend", None), table_rows_limit),
            "epsRevisions": lambda: frame_rows(getattr(ticker, "eps_revisions", None), table_rows_limit),
            "recommendations": lambda: frame_rows(getattr(ticker, "recommendations", None), table_rows_limit),
            "recommendationsSummary": lambda: frame_rows(getattr(ticker, "recommendations_summary", None), table_rows_limit),
            "upgradesDowngrades": lambda: frame_rows(getattr(ticker, "upgrades_downgrades", None), table_rows_limit),
            "analystPriceTargets": lambda: json_safe_value(getattr(ticker, "analyst_price_targets", {})),
            "majorHolders": lambda: frame_rows(getattr(ticker, "major_holders", None), table_rows_limit),
            "institutionalHolders": lambda: frame_rows(getattr(ticker, "institutional_holders", None), table_rows_limit),
            "mutualfundHolders": lambda: frame_rows(getattr(ticker, "mutualfund_holders", None), table_rows_limit),
            "insiderTransactions": lambda: frame_rows(getattr(ticker, "insider_transactions", None), table_rows_limit),
            "insiderPurchases": lambda: frame_rows(getattr(ticker, "insider_purchases", None), table_rows_limit),
            "insiderRosterHolders": lambda: frame_rows(getattr(ticker, "insider_roster_holders", None), table_rows_limit),
            "sustainability": lambda: frame_rows(getattr(ticker, "sustainability", None), table_rows_limit),
            "isin": lambda: json_safe_value(getattr(ticker, "isin", "")),
            "news": lambda: json_safe_value((getattr(ticker, "news", []) or [])[:news_limit]) if news_limit else [],
            "fundsData": lambda: self.yfinance_funds_data(getattr(ticker, "funds_data", None), table_rows_limit),
            "shares": lambda: series_rows(ticker.get_shares_full(), table_rows_limit) if hasattr(ticker, "get_shares_full") else [],
        }
        for name, getter in sections.items():
            value = capture(name, getter)
            if not is_empty_value(value):
                payload[name] = value

        options = capture("options", lambda: list(getattr(ticker, "options", []) or [])) or []
        payload["options"] = options
        chains = []
        for expiration in options[:option_expirations]:
            try:
                with quiet_yfinance_io():
                    chain = ticker.option_chain(expiration)
            except Exception as error:  # noqa: BLE001 - option expirations can disappear intraday.
                if is_expected_yfinance_missing_error(error):
                    expected_missing.append({
                        "section": "optionChain:" + str(expiration),
                        "status": "expected-missing",
                        "reason": "option-chain-not-available",
                    })
                    continue
                errors.append({"section": "optionChain:" + str(expiration), "message": str(error)[:180]})
                continue
            calls = frame_rows(getattr(chain, "calls", None), option_rows_limit)
            puts = frame_rows(getattr(chain, "puts", None), option_rows_limit)
            chains.append({
                "expiration": str(expiration),
                "summary": option_summary(calls, puts),
                "calls": calls,
                "puts": puts,
            })
        if chains:
            payload["optionChains"] = chains

        modules = [key for key, value in payload.items() if key not in {"provider", "symbol", "querySymbol", "collectedAt"} and not is_empty_value(value)]
        payload["modulesCollected"] = modules
        payload["freshness"] = self.yfinance_freshness_summary(payload)
        payload["moduleFreshness"] = self.yfinance_module_freshness(payload)
        if expected_missing:
            payload["dataQualityNotes"] = expected_missing[:20]
        if errors:
            payload["errors"] = errors[:20]
        return payload

    def yfinance_max_age_minutes(self, profile: str) -> int:
        key, fallback = YFINANCE_FRESHNESS_PROFILES.get(str(profile or ""), YFINANCE_FRESHNESS_PROFILES["fundamental"])
        return self.int_setting(key, fallback, 1)

    def yfinance_module_timestamp(self, payload: Dict[str, object], module: str) -> Tuple[str, str]:
        collected_at = str(payload.get("collectedAt") or "")
        if module == "news":
            return first_news_timestamp(payload.get("news")) or collected_at, "publishedAt"
        return collected_at, "collectedAt"

    def yfinance_module_freshness_record(self, module: str, payload: Dict[str, object]) -> Dict[str, object]:
        profile = YFINANCE_MODULE_PROFILES.get(module, "fundamental")
        max_age = self.yfinance_max_age_minutes(profile)
        timestamp, basis = self.yfinance_module_timestamp(payload, module)
        age = age_minutes(timestamp)
        if age is None:
            status = "unknown"
            reason = "기준시각 없음"
        elif age <= max_age:
            status = "fresh"
            reason = "신선도 기준 통과"
        else:
            status = "stale"
            reason = "기준 " + str(max_age) + "분 초과"
        return {
            "module": module,
            "profile": profile,
            "status": status,
            "reason": reason,
            "ageMinutes": age,
            "maxAgeMinutes": max_age,
            "sourceTimestamp": str(timestamp or ""),
            "basis": basis,
        }

    def yfinance_module_freshness(self, payload: Dict[str, object]) -> Dict[str, object]:
        result: Dict[str, object] = {}
        for module in payload.get("modulesCollected") or []:
            text = str(module or "").strip()
            if not text:
                continue
            result[text] = self.yfinance_module_freshness_record(text, payload)
        return result

    def yfinance_freshness_summary(self, payload: Dict[str, object]) -> Dict[str, object]:
        records = list(self.yfinance_module_freshness(payload).values())
        stale = [item for item in records if str(item.get("status") or "") == "stale"]
        unknown = [item for item in records if str(item.get("status") or "") == "unknown"]
        if stale:
            status = "stale"
            reason = ", ".join(str(item.get("module") or "") + " " + str(item.get("reason") or "") for item in stale[:3])
        elif unknown:
            status = "unknown"
            reason = ", ".join(str(item.get("module") or "") + " " + str(item.get("reason") or "") for item in unknown[:3])
        else:
            status = "fresh"
            reason = "모든 yfinance 모듈 신선도 기준 통과"
        ages = [item.get("ageMinutes") for item in records if isinstance(item.get("ageMinutes"), int)]
        return {
            "status": status,
            "reason": reason,
            "ageMinutes": max(ages) if ages else None,
            "staleModules": [str(item.get("module") or "") for item in stale[:20]],
            "unknownModules": [str(item.get("module") or "") for item in unknown[:20]],
            "checkedAt": utc_now_iso(),
        }

    def yfinance_funds_data(self, funds_data, rows_limit: int) -> Dict[str, object]:
        if not funds_data:
            return {}
        result: Dict[str, object] = {}
        for name in [
            "description",
            "fund_overview",
            "fund_operations",
            "asset_classes",
            "top_holdings",
            "equity_holdings",
            "bond_holdings",
            "bond_ratings",
            "sector_weightings",
        ]:
            try:
                value = getattr(funds_data, name)
            except Exception:
                continue
            if hasattr(value, "reset_index"):
                value = frame_rows(value, rows_limit)
            else:
                value = json_safe_value(value)
            if not is_empty_value(value):
                result[name] = value
        return result

    def merge_yfinance_summaries(self, signals: Dict[str, object], symbol: str, payload: Dict[str, object]) -> None:
        quote = payload.get("quote") if isinstance(payload.get("quote"), dict) else {}
        if quote and symbol not in signals.setdefault("equityQuotes", {}):
            signals["equityQuotes"][symbol] = {
                "provider": "yfinance",
                "price": number(quote.get("price")),
                "change": number(quote.get("change")),
                "changePercent": number(quote.get("changePercent")),
                "volume": number(quote.get("volume")),
                "latestTradingDay": str(quote.get("latestTradingDay") or ""),
            }
        if symbol not in signals.setdefault("companyOverviews", {}):
            overview = overview_from_yfinance(symbol, payload)
            if any(overview.get(key) not in (None, "", 0, 0.0) for key in ["name", "marketCapitalization", "revenueTTM", "analystTargetPrice"]):
                signals["companyOverviews"][symbol] = overview
        if symbol not in signals.setdefault("earningsReports", {}):
            earnings = earnings_report_from_yfinance(symbol, payload)
            latest = earnings.get("latestQuarter") if isinstance(earnings.get("latestQuarter"), dict) else {}
            if any(latest.get(key) not in (None, "", 0, 0.0) for key in ["reportedDate", "reportedEPS", "estimatedEPS"]):
                signals["earningsReports"][symbol] = earnings
