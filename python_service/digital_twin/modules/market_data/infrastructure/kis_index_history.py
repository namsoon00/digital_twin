"""Bounded, timestamped index history for outcome comparisons, not stock quotes."""

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from digital_twin.infrastructure.kis_market_signals import KISMarketSignalProvider
from digital_twin.modules.market_data.domain.benchmark import INDEX_BENCHMARKS
from digital_twin.modules.market_data.domain.market_time_series import MarketTimeSeriesObservation, bucket_start


INDEX_CODES = {"KOSPI": "0001"}
INDEX_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice"
INDEX_TR_ID = "FHKUP03500200"
HISTORY_INTERVALS = {"1m": 60, "10m": 600}


def index_observations(symbol, rows, granularity, received_at):
    seconds = HISTORY_INTERVALS[granularity]
    identity = INDEX_BENCHMARKS[symbol]
    result = {}
    rejected = 0
    for row in list(rows or [])[:102]:
        try:
            date = str(row["stck_bsop_date"])
            clock = str(row["stck_cntg_hour"])
            if len(date) != 8 or len(clock) != 6 or not (date + clock).isdigit():
                raise ValueError("invalid source clock")
            bar_start = datetime.strptime(date + clock, "%Y%m%d%H%M%S").replace(
                tzinfo=ZoneInfo("Asia/Seoul"),
            ).astimezone(timezone.utc)
            if int(bar_start.timestamp()) % seconds:
                raise ValueError("misaligned bar start")
            completed_at = bar_start + timedelta(seconds=seconds)
            raw_price = row["bstp_nmix_prpr"]
            price = float(raw_price)
            if isinstance(raw_price, bool) or not math.isfinite(price) or price <= 0:
                raise ValueError("invalid index price")
            if completed_at > received_at or bar_start < received_at - timedelta(days=7):
                raise ValueError("unfinished or out-of-range observation")
        except (KeyError, TypeError, ValueError, OverflowError):
            rejected += 1
            continue
        # KIS labels a bar by its start, but bstp_nmix_prpr is its close.
        # Admit that value only at the interval's end, never at its start.
        source_at = (completed_at - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        observation = MarketTimeSeriesObservation(
            account_id="__market_data__", symbol=symbol, granularity=granularity,
            bucket_at=bucket_start(source_at, granularity),
            observed_at=received_at.isoformat().replace("+00:00", "Z"),
            source_as_of=source_at, provider="kis-index-chart", source_role="decision-outcome-benchmark",
            name=identity["name"], market=identity["market"], currency=identity["currency"],
            current_price=price, open_price=price, high_price=price, low_price=price,
        )
        # Keep a single canonical point per source bucket, independent of API row order.
        previous = result.get(observation.bucket_at)
        if previous is None or observation.source_as_of > previous.source_as_of:
            result[observation.bucket_at] = observation
    return list(sorted(result.values(), key=lambda item: item.source_as_of)), rejected


class KISIndexHistoryProvider:
    def __init__(self, settings, client_factory=None, now_provider=None):
        self.client_factory = client_factory or (lambda: KISMarketSignalProvider(settings=settings))
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._client = None

    def fetch_history(self, symbols):
        requested = sorted({str(symbol or "").upper().strip() for symbol in symbols})
        supported = [symbol for symbol in requested if symbol in INDEX_CODES]
        result = {
            "status": "skipped", "provider": "kis-index-chart", "symbols": supported,
            "unsupportedSymbols": [symbol for symbol in requested if symbol not in INDEX_CODES],
            "observations": [], "rejectedRowCount": 0, "failures": [],
        }
        if not supported:
            return result
        client = self._client or self.client_factory()
        if not client.enabled() or not client.configured():
            result.update(status="unavailable", reason="kis-index-provider-disabled-or-unconfigured")
            return result
        try:
            if self._client is None:
                client.fetch_access_token()
                self._client = client
        except Exception as error:  # noqa: BLE001 - auth failure must not abort account collection.
            result.update(status="error", reason="kis-index-authentication-failed", httpStatus=getattr(error, "http_status", 0))
            return result
        for symbol in supported:
            for granularity, seconds in HISTORY_INTERVALS.items():
                try:
                    payload = client.request(
                        "index-history", "GET", INDEX_PATH, client.auth_headers(INDEX_TR_ID),
                        query={
                            "FID_COND_MRKT_DIV_CODE": "U", "FID_ETC_CLS_CODE": "0",
                            "FID_INPUT_ISCD": INDEX_CODES[symbol], "FID_INPUT_HOUR_1": str(seconds),
                            "FID_PW_DATA_INCU_YN": "Y",
                        },
                        attempts=1,
                    )
                    rows = payload.get("output2")
                    if not isinstance(rows, list):
                        raise ValueError("missing dated index history")
                    observations, rejected = index_observations(symbol, rows, granularity, self.now_provider())
                    result["observations"].extend(observations)
                    result["rejectedRowCount"] += rejected
                    if not observations:
                        result["failures"].append({"symbol": symbol, "granularity": granularity, "reason": "no-valid-history"})
                except Exception as error:  # noqa: BLE001 - retain other successful history ranges.
                    self._client = None
                    result["failures"].append({
                        "symbol": symbol, "granularity": granularity,
                        "reason": "kis-index-history-request-failed", "httpStatus": getattr(error, "http_status", 0),
                    })
        result["status"] = "partial" if result["failures"] and result["observations"] else ("error" if result["failures"] else "ok")
        return result
