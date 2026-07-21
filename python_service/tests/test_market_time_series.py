import unittest
from datetime import datetime, timedelta, timezone

from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.market_time_series import MarketTimeSeriesObservation, market_session_date
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.infrastructure.mysql_market_time_series import MySQLMarketTimeSeriesStore
from digital_twin.infrastructure.mysql_retention import (
    market_time_series_retention_cutoffs,
    market_time_series_retention_days,
)


class Cursor:
    def __init__(self, rows=None, rowcount=1):
        self.rows = list(rows or [])
        self.rowcount = rowcount

    def fetchall(self):
        return list(self.rows)


class RecordingConnection:
    def __init__(self, latest_rows=None):
        self.calls = []
        self.latest_rows = list(latest_rows or [])

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params or ())))
        if "MAX(bucket_at)" in sql:
            return Cursor(self.latest_rows, rowcount=0)
        return Cursor()


class TransactionContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return False


def portfolio_summary():
    return PortfolioSummary(1000, 1000, 0, [], [], 1)


def position(price=120):
    return normalize_position({
        "symbol": "005930",
        "name": "삼성전자",
        "market": "KR",
        "currency": "KRW",
        "source": "holding",
        "currentPrice": price,
        "averagePrice": 100,
        "quantity": 10,
        "profitLossRate": 20,
        "volume": 100000,
        "ma20": 110,
        "ma60": 105,
        "ma20Distance": 9.09,
        "ma60Distance": 14.29,
        "updatedAt": "2026-07-20T06:00:00Z",
        "sourceAsOf": "2026-07-20T06:00:00Z",
        "dataQuality": "actual",
    })


class MarketTimeSeriesTests(unittest.TestCase):
    def test_daily_candle_date_stays_on_local_market_session(self):
        kr = MarketTimeSeriesObservation.from_daily_candle(
            "__market_data__", "005930", {"date": "2026-07-20", "close": 120}, market="KR", currency="KRW"
        )
        us = MarketTimeSeriesObservation.from_daily_candle(
            "__market_data__", "AAPL", {"date": "2026-07-20", "close": 210}, market="NASDAQ", currency="USD"
        )

        self.assertEqual("2026-07-20", market_session_date(kr.bucket_at, "KR", "KRW"))
        self.assertEqual("2026-07-20", market_session_date(us.bucket_at, "NASDAQ", "USD"))
        self.assertEqual("2026-07-19T15:00:00Z", kr.bucket_at)
        self.assertEqual("2026-07-20T04:00:00Z", us.bucket_at)

    def test_snapshot_write_creates_raw_and_three_rollups(self):
        store = MySQLMarketTimeSeriesStore.__new__(MySQLMarketTimeSeriesStore)
        store.runtime_settings = {"marketTimeSeriesEnabled": "1"}
        connection = RecordingConnection()
        snapshot = AccountSnapshot(
            "main", "Main", "toss", "live", "ok", "2026-07-20T06:01:00Z",
            portfolio_summary(), positions=[position()],
        )

        result = store.record_snapshots_with_connection(connection, [snapshot])

        self.assertEqual(1, result["savedCount"])
        self.assertEqual(3, result["aggregateCount"])
        self.assertEqual(4, len(connection.calls))
        self.assertIn("INSERT IGNORE INTO market_time_series_observations", connection.calls[0][0])
        self.assertTrue(all("ON DUPLICATE KEY UPDATE" in sql for sql, _params in connection.calls[1:]))

    def test_tiered_retention_is_configurable(self):
        settings = {
            "marketTimeSeriesRawRetentionDays": "5",
            "marketTimeSeries15mRetentionDays": "90",
            "marketTimeSeries1hRetentionDays": "400",
            "marketTimeSeriesDailyRetentionDays": "3000",
        }
        now = datetime(2026, 7, 20, tzinfo=timezone.utc)

        self.assertEqual({"3m": 5, "15m": 90, "1h": 400, "1d": 3000}, market_time_series_retention_days(settings))
        self.assertEqual("2026-07-15T00:00:00Z", market_time_series_retention_cutoffs(settings, now)["3m"])

    def test_daily_backfill_only_rewrites_latest_and_new_candles(self):
        store = MySQLMarketTimeSeriesStore.__new__(MySQLMarketTimeSeriesStore)
        store.runtime_settings = {"marketTimeSeriesEnabled": "1"}
        connection = RecordingConnection([{"symbol": "005930", "latest_bucket": "2026-07-18T15:00:00Z"}])
        store.transaction = lambda: TransactionContext(connection)

        result = store.record_daily_candles({
            "005930": [
                {"date": "2026-07-18", "close": 100},
                {"date": "2026-07-19", "close": 110},
                {"date": "2026-07-20", "close": 120},
            ],
        }, {"005930": {"market": "KR", "currency": "KRW"}})

        self.assertEqual(2, result["savedCount"])
        self.assertEqual(1, result["unchangedHistoricalCount"])
        self.assertEqual(3, len(connection.calls))

    def test_ontology_uses_stored_trading_sessions_for_multi_day_window(self):
        start = datetime(2026, 6, 23, 6, tzinfo=timezone.utc)
        rows = []
        for index in range(20):
            stamp = start + timedelta(days=index)
            rows.append({
                "generatedAt": stamp.isoformat().replace("+00:00", "Z"),
                "bucketAt": stamp.isoformat().replace("+00:00", "Z"),
                "marketSessionDate": stamp.date().isoformat(),
                "symbol": "005930",
                "currentPrice": 100 + index,
                "ma20Distance": -5 + index * 0.2,
                "foreignNetVolume": 100 + index,
                "institutionNetVolume": 50 + index,
                "observationSource": "mysql-market-time-series",
                "observationGranularity": "1d",
            })
        graph = build_portfolio_ontology(
            [position()], portfolio_summary(), portfolio_id="main",
            runtime_context={
                "asOf": "2026-07-20T06:00:00Z",
                "settings": {"temporalWindowPeriods": "20D=20:5"},
                "temporalObservationWindows": {"005930": {"20D": rows}},
            },
        )
        window = next(
            item for item in graph.entities
            if item.kind == "temporal-window" and item.properties.get("windowKey") == "20D"
        )

        self.assertTrue(window.properties["hasSufficientHistory"])
        self.assertEqual(20, window.properties["coveredSessionCount"])
        self.assertEqual("mysql-market-time-series", window.properties["source"])
        self.assertEqual("1d", window.properties["retentionTier"])

    def test_same_day_snapshots_do_not_satisfy_multi_day_history(self):
        state_history = []
        for minute in range(6):
            state_history.append({
                "generatedAt": "2026-07-20T06:" + str(minute).zfill(2) + ":00Z",
                "positions": {"005930": {"symbol": "005930", "currentPrice": 100 + minute}},
            })
        graph = build_portfolio_ontology(
            [position()], portfolio_summary(), portfolio_id="main",
            runtime_context={
                "asOf": "2026-07-20T06:10:00Z",
                "settings": {"temporalWindowPeriods": "20D=20:5"},
                "metadata": {"monitorStateHistory": state_history},
            },
        )
        window = next(
            item for item in graph.entities
            if item.kind == "temporal-window" and item.properties.get("windowKey") == "20D"
        )

        self.assertFalse(window.properties["hasSufficientHistory"])
        self.assertEqual(1, window.properties["coveredSessionCount"])
        window_relation = next(
            item for item in graph.relations
            if item.relation_type == "HAS_TEMPORAL_WINDOW" and item.target == window.entity_id
        )
        self.assertEqual("blocked", window_relation.properties["reviewLevel"])
        self.assertEqual("insufficient", window_relation.properties["dataState"])
        self.assertTrue(any(item.kind == "temporal-coverage-gap" for item in graph.entities))


if __name__ == "__main__":
    unittest.main()
