"""Recover bounded ETF comparison windows without fabricating current quotes."""

from datetime import datetime, timedelta, timezone
import math
import re

from digital_twin.modules.market_data.domain.market_time_series import MarketTimeSeriesObservation, bucket_start


def timestamp(value):
    try:
        stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except ValueError:
        return None


def benchmark_history_windows(targets, now):
    windows = {}
    for target in list(targets or [])[:200]:
        symbol = str(target.get("symbol") or "").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol):
            continue
        baseline = timestamp(target.get("baselineAt"))
        end = timestamp(target.get("targetAt"))
        try:
            delay = max(1, min(20160, int(target.get("maximumObservationDelayMinutes") or 180)))
        except (TypeError, ValueError):
            delay = 180
        ranges = []
        if baseline:
            ranges.append((baseline - timedelta(minutes=15), baseline))
        if end:
            deadline = timestamp(target.get("observedAt")) or end + timedelta(minutes=delay)
            ranges.append((end, deadline))
        for start, finish in ranges:
            start, finish = max(start, now - timedelta(days=7)), min(finish, now)
            if start < finish:
                windows.setdefault(symbol, []).append((start, finish))
    return windows


def benchmark_observations(symbol, rows, windows, received_at):
    observations = {}
    for row in list(rows or [])[:10080]:
        start = timestamp(row.get("Datetime") or row.get("Date"))
        if not start or int(start.timestamp()) % 60:
            continue
        completed = start + timedelta(minutes=1)
        source = completed - timedelta(seconds=1)
        if completed > received_at or not any(low <= source <= high for low, high in windows):
            continue
        raw = row.get("Close")
        try:
            price = float(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(raw, bool) or not math.isfinite(price) or price <= 0:
            continue
        source_at = source.isoformat().replace("+00:00", "Z")
        point = MarketTimeSeriesObservation(
            account_id="__market_data__", symbol=symbol, name=symbol, market="US", currency="USD",
            granularity="1m", bucket_at=bucket_start(source_at, "1m"), source_as_of=source_at,
            observed_at=received_at.isoformat().replace("+00:00", "Z"), provider="yfinance-benchmark-history",
            source_role="decision-outcome-benchmark", current_price=price,
            open_price=price, high_price=price, low_price=price,
        )
        observations[point.bucket_at] = point
    return sorted(observations.values(), key=lambda point: point.source_as_of)


class BenchmarkHistoryProvider:
    def __init__(self, settings, ticker_factory=None, now_provider=None):
        self.enabled = str(settings.get("externalYFinanceEnabled", "1")) != "0"
        self.ticker_factory = ticker_factory
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.next_attempt = {}

    def fetch_history(self, targets):
        now = self.now_provider()
        windows = benchmark_history_windows(targets, now)
        result = {"status": "skipped", "symbols": [], "observations": [], "failures": []}
        if not self.enabled:
            return {**result, "status": "disabled"}
        for symbol, ranges in list(windows.items())[:4]:
            if self.next_attempt.get(symbol, now) > now:
                continue
            self.next_attempt[symbol] = now + timedelta(minutes=15)
            result["symbols"].append(symbol)
            try:
                factory = self.ticker_factory
                if factory is None:
                    import yfinance
                    factory = yfinance.Ticker
                frame = factory(symbol).history(period="7d", interval="1m", auto_adjust=False,
                                                actions=False, prepost=True, timeout=15)
                rows = frame.reset_index().to_dict(orient="records")
                points = benchmark_observations(symbol, rows, ranges, now)
                result["observations"].extend(points)
                if not points:
                    result["failures"].append({"symbol": symbol, "reason": "historical-window-unavailable"})
            except Exception as error:
                result["failures"].append({"symbol": symbol, "reason": "benchmark-history-fetch-failed", "errorType": type(error).__name__})
        if result["symbols"]:
            result["status"] = "partial" if result["failures"] and result["observations"] else ("unavailable" if result["failures"] else "ok")
        return result
