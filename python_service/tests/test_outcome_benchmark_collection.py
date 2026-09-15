import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from digital_twin.infrastructure.mysql_retention import market_time_series_retention_days
from digital_twin.infrastructure.questdb_time_series import QuestDBTimeSeriesAdapter
from digital_twin.platform.domain.mysql_minimal_retention import mysql_minimal_retention_policy
from digital_twin.modules.market_data.application.market_data_collection_service import MarketDataCollectionRunner
from digital_twin.modules.market_data.application.time_series_platform import VersionedMarketTimeSeriesStore
from digital_twin.modules.market_data.infrastructure.kis_index_history import (
    INDEX_PATH, INDEX_TR_ID, KISIndexHistoryProvider, index_observations,
)
from digital_twin.modules.market_data.infrastructure.mysql_market_time_series import MySQLMarketTimeSeriesStore
from digital_twin.modules.portfolio.contracts import Position
from digital_twin.modules.market_data.infrastructure.benchmark_history import BenchmarkHistoryProvider, benchmark_observations, benchmark_history_windows
from digital_twin.modules.outcomes.contracts import benchmark_observation_window, outcome_recovery_state


NOW = datetime(2026, 9, 14, 6, 50, tzinfo=timezone.utc)


def wire_row(clock="145800", date="20260914", price="6712.08"):
    return {"stck_bsop_date": date, "stck_cntg_hour": clock, "bstp_nmix_prpr": price, "cntg_vol": "12"}


def observation():
    return index_observations("KOSPI", [wire_row()], "1m", NOW)[0][0]


class IndexClient:
    def __init__(self, failing_interval=""):
        self.calls = []
        self.failing_interval = failing_interval
        self.token_requests = 0

    def enabled(self):
        return True

    def configured(self):
        return True

    def fetch_access_token(self):
        self.token_requests += 1

    def auth_headers(self, tr_id):
        return {"tr_id": tr_id}

    def request(self, stage, method, path, headers, query, attempts):
        self.calls.append((stage, method, path, headers, query, attempts))
        if query["FID_INPUT_HOUR_1"] == self.failing_interval:
            raise RuntimeError("private transport details must not reach the summary")
        return {"output2": [wire_row(clock="145000"), wire_row(clock="888888")]}


class HistoryStore(MySQLMarketTimeSeriesStore):
    def __init__(self):
        self.rows = {}
        self.statements = []
        self.connection = SimpleNamespace(execute=self.execute)

    def enabled(self):
        return True

    @contextmanager
    def transaction(self):
        yield self.connection

    def execute(self, sql, values):
        self.statements.append(sql)
        key = tuple(values[:4])
        inserted = key not in self.rows
        if inserted:
            self.rows[key] = values
        return SimpleNamespace(rowcount=int(inserted))


class OutcomeBenchmarkCollectionTests(unittest.TestCase):
    def test_benchmark_repair_preserves_original_window_and_stops_at_original_outcome(self):
        target = {"benchmarkSymbol": "SPY", "baselineAt": "2026-09-11T13:00:00Z", "targetAt": "2026-09-14T05:00:00Z",
                  "previousOutcome": {"observedAt": "2026-09-11T14:02:00Z", "payload": {"targetAt": "2026-09-11T14:00:00Z"}}}
        window = benchmark_observation_window(target)
        windows = benchmark_history_windows([window], NOW)
        rows = [{"Datetime": "2026-09-11T14:00:00Z", "Close": 700},
                {"Datetime": "2026-09-11T14:02:00Z", "Close": 800},
                {"Datetime": "2026-09-14T05:00:00Z", "Close": 900},
                {"Datetime": "2026-09-11T14:01:00Z", "Close": float("nan")}]
        points = benchmark_observations("SPY", rows, windows["SPY"], NOW)
        self.assertEqual([700], [point.current_price for point in points])
        self.assertEqual("2026-09-11T14:00:59Z", points[0].source_as_of)
        self.assertTrue(points[0].valid_price_history())
        self.assertEqual({}, benchmark_history_windows([{**window, "targetAt": "2026-08-01T14:00:00Z", "baselineAt": "" , "observedAt": "2026-08-01T14:02:00Z"}], NOW))

    def test_benchmark_provider_bounds_fetch_and_throttles_missing_history(self):
        ticker = Mock()
        ticker.history.return_value.reset_index.return_value.to_dict.return_value = []
        clock = [NOW]
        provider = BenchmarkHistoryProvider({}, ticker_factory=lambda _: ticker, now_provider=lambda: clock[0])
        windows = [{"symbol": "SPY", "targetAt": "2026-09-11T14:00:00Z"}]
        self.assertEqual("unavailable", provider.fetch_history(windows)["status"])
        self.assertEqual("skipped", provider.fetch_history(windows)["status"])
        ticker.history.assert_called_once_with(period="7d", interval="1m", auto_adjust=False, actions=False, prepost=True, timeout=15)
        clock[0] += timedelta(minutes=15)
        self.assertEqual("unavailable", provider.fetch_history(windows)["status"])

    def test_benchmark_windows_for_multiple_targets_survive_symbol_deduplication(self):
        reader = SimpleNamespace(outcome_collection_targets=lambda *a, **kw: [
            {"symbol": "NVDA", "benchmarkSymbol": "SPY", "targetAt": stamp}
            for stamp in ["2026-09-11T14:00:00Z", "2026-09-11T15:00:00Z"]])
        runner = self.runner(decision_episode_store=reader)
        targets = runner.outcome_observation_targets([{"account": SimpleNamespace(account_id="main")}], ["NVDA", "SPY"])
        self.assertEqual(1, len(targets))
        self.assertEqual(2, len(targets[0][1]["outcomeHistoryWindows"]))
        provider = SimpleNamespace(fetch_history=Mock(side_effect=RuntimeError("private")))
        runner.benchmark_history_provider = provider
        runner.time_series_store = SimpleNamespace(record_price_history=Mock())
        self.assertEqual("error", runner.collect_benchmark_history(targets)["status"])
        self.assertEqual(2, len(provider.fetch_history.call_args.args[0]))

    def test_unrecoverable_data_gaps_back_off_and_never_become_calibration_successes(self):
        current = {"payload": {"calibrationEligibility": "excluded-criterion-data-gap"}}
        previous = {}
        waits = []
        for attempt in range(1, 9):
            state = outcome_recovery_state(previous, current, NOW.isoformat())
            self.assertEqual(attempt, state["attemptCount"])
            self.assertEqual(attempt == 8, state["automaticRetryStopped"])
            if state["nextRetryAt"]:
                waits.append((datetime.fromisoformat(state["nextRetryAt"].replace("Z", "+00:00")) - NOW).total_seconds() / 60)
            previous = {"payload": {**current["payload"], "evaluationRecovery": state}}
        self.assertEqual([15, 30, 60, 120, 240, 360, 360], waits)
        self.assertEqual("unavailable", state["state"])
        self.assertEqual("excluded-criterion-data-gap", current["payload"]["calibrationEligibility"])
        self.assertEqual("complete", outcome_recovery_state(previous, {"payload": {"calibrationEligibility": "eligible"}}, NOW.isoformat())["state"])

    def test_sql_benchmark_query_caps_at_original_observation_not_later_data(self):
        store = HistoryStore()
        read = Mock(return_value=SimpleNamespace(fetchall=lambda: []))
        @contextmanager
        def connect():
            yield SimpleNamespace(execute=read)
        store.connect = connect
        target = {"requestId": "original", "symbol": "SPY", "targetAt": "2026-09-11T14:00:00Z", "maximumObservationAt": "2026-09-11T14:02:00Z"}
        self.assertEqual({}, store.load_outcome_observations("main", [target]))
        self.assertIn("2026-09-11T14:02:00Z", read.call_args.args[1])
        read.reset_mock()
        self.assertEqual({}, store.load_outcome_observations("main", [{**target, "maximumObservationAt": "invalid"}]))
        read.assert_not_called()

    def runner(self, **kwargs):
        return MarketDataCollectionRunner(
            None, SimpleNamespace(enrich=lambda symbol: {"market": "NASDAQ", "currency": "USD"}),
            SimpleNamespace(load=lambda *args: {}), {}, None, sleep_fn=lambda *_: None, **kwargs,
        )

    def position(self, symbol, source="decision-outcome-benchmark"):
        return Position(symbol=symbol, name=symbol, market="KR", currency="KRW", current_price=100, source=source)

    def test_source_clock_and_availability_are_distinct_and_index_units_are_not_currency(self):
        point = observation()
        self.assertEqual("2026-09-14T05:58:59Z", point.source_as_of)
        self.assertEqual("2026-09-14T06:50:00Z", point.observed_at)
        self.assertEqual("2026-09-14T05:58:00Z", point.bucket_at)
        self.assertEqual("POINTS", point.currency)
        self.assertEqual(0, point.volume)
        self.assertTrue(point.valid_price_history())

    def test_invalid_summary_clocks_future_stale_and_nonfinite_prices_are_rejected(self):
        invalid = [
            wire_row(clock="888888"), wire_row(clock="255000"), wire_row(date="20261301"),
            wire_row(clock="164900"), wire_row(clock="154959"), wire_row(date="20260801"),
            wire_row(price="NaN"), wire_row(price="inf"), wire_row(price="-1"), wire_row(price=True), {}, None,
        ]
        rows, rejected = index_observations("KOSPI", invalid, "1m", NOW)
        self.assertEqual([], rows)
        self.assertEqual(len(invalid), rejected)

    def test_history_is_bounded_and_source_buckets_are_deduplicated(self):
        inputs = [wire_row(clock="145000"), wire_row(clock="145000")] * 60
        rows, _ = index_observations("KOSPI", inputs, "10m", NOW)
        self.assertEqual(1, len(rows))
        self.assertEqual("2026-09-14T05:50:00Z", rows[0].bucket_at)
        self.assertEqual("2026-09-14T05:59:59Z", rows[0].source_as_of)
        self.assertEqual(rows, index_observations("KOSPI", list(reversed(inputs)), "10m", NOW)[0])

    def test_vendor_uses_index_api_and_bounded_partial_history_without_etf_substitution(self):
        client = IndexClient(failing_interval="600")
        provider = KISIndexHistoryProvider({}, client_factory=lambda: client, now_provider=lambda: NOW)
        result = provider.fetch_history(["KOSPI", "KOSPI", "SPY"])
        self.assertEqual("partial", result["status"])
        self.assertEqual(["SPY"], result["unsupportedSymbols"])
        self.assertEqual(1, len(result["observations"]))
        self.assertEqual(1, client.token_requests)
        self.assertEqual(2, len(client.calls))
        for call in client.calls:
            self.assertEqual(INDEX_PATH, call[2])
            self.assertEqual(INDEX_TR_ID, call[3]["tr_id"])
            self.assertEqual("0001", call[4]["FID_INPUT_ISCD"])
            self.assertEqual("U", call[4]["FID_COND_MRKT_DIV_CODE"])
            self.assertEqual(1, call[5])
        self.assertNotIn("private", str(result))

    def test_disabled_or_auth_failure_is_explicit_and_never_a_zero_price(self):
        client = IndexClient()
        client.enabled = lambda: False
        provider = KISIndexHistoryProvider({}, client_factory=lambda: client)
        self.assertEqual("unavailable", provider.fetch_history(["KOSPI"])["status"])
        self.assertEqual(0, client.token_requests)
        client.enabled = lambda: True
        client.fetch_access_token = Mock(side_effect=RuntimeError("private"))
        result = provider.fetch_history(["KOSPI"])
        self.assertEqual("error", result["status"])
        self.assertEqual([], result["observations"])
        self.assertEqual([], client.calls)

    def test_watch_cycles_reuse_authentication_and_refresh_after_vendor_failure(self):
        client = IndexClient()
        factory = Mock(return_value=client)
        provider = KISIndexHistoryProvider({}, client_factory=factory, now_provider=lambda: NOW)
        for _ in range(2):
            self.assertEqual("ok", provider.fetch_history(["KOSPI"])["status"])
        self.assertEqual(1, client.token_requests)
        self.assertEqual(1, factory.call_count)
        client.failing_interval = "600"
        provider.fetch_history(["KOSPI"])
        client.failing_interval = ""
        self.assertEqual("ok", provider.fetch_history(["KOSPI"])["status"])
        self.assertEqual(2, client.token_requests)

    def test_replica_keeps_index_granularity_clocks_and_retention(self):
        adapter = QuestDBTimeSeriesAdapter.__new__(QuestDBTimeSeriesAdapter)
        adapter.settings = {"marketTimeSeriesRawRetentionDays": "1"}
        for granularity in ("1m", "10m"):
            point = index_observations("KOSPI", [wire_row(clock="145000")], granularity, NOW)[0][0]
            line = adapter.market_line(point.to_row())
            self.assertTrue(line.startswith("market_observations_" + granularity + ","))
            self.assertIn("granularity=" + granularity, line)
            self.assertIn("currency=POINTS", line)
            self.assertIn("source_as_of=", line)
            self.assertIn("observed_at=", line)
            self.assertEqual(1, adapter.expected_ttl_days()["market_observations_" + granularity])

    def test_index_target_identity_does_not_use_default_us_stock_enrichment(self):
        reader = SimpleNamespace(outcome_collection_targets=lambda *_args, **_kwargs: [
            {"symbol": "000660", "market": "KR", "currency": "KRW", "benchmarkSymbol": "KOSPI"},
        ])
        runner = self.runner(decision_episode_store=reader)
        targets = runner.outcome_observation_targets([{"account": SimpleNamespace(account_id="main")}], ["000660"])
        self.assertEqual(1, len(targets))
        self.assertEqual("KOSPI", targets[0][0].symbol)
        self.assertEqual("KR", targets[0][1]["market"])
        self.assertEqual("POINTS", targets[0][1]["currency"])

    def test_index_never_enters_toss_stock_quotes_candles_or_market_signal_results(self):
        history = Mock(return_value={"status": "ok", "observations": [observation()]})
        store = SimpleNamespace(record_price_history=Mock(return_value={"savedCount": 1}))
        runner = self.runner(index_history_provider=SimpleNamespace(fetch_history=history), time_series_store=store)
        toss = Mock()
        entries, auxiliary, summary = runner.merge_focus_market_data(toss, "", [], [(self.position("KOSPI"), {})])
        toss.fetch_access_token.assert_not_called()
        toss.fetch_prices.assert_not_called()
        toss.fetch_daily_candles.assert_not_called()
        history.assert_called_once_with(["KOSPI"])
        self.assertEqual([], entries)
        self.assertEqual([], auxiliary)
        self.assertEqual([], summary["symbols"])
        self.assertEqual(1, summary["indexBenchmarkHistory"]["savedCount"])
        self.assertNotIn("observations", summary["indexBenchmarkHistory"])

    def test_generic_comparison_symbols_are_stored_along_with_outcome_instruments(self):
        record = Mock(return_value={"savedCount": 2})
        runner = self.runner(time_series_store=SimpleNamespace(record_positions=record))
        targets = [(self.position("AAPL", "decision-outcome"), {}), (self.position("SPY"), {}), (self.position("QQQ", "market-signal"), {})]
        self.assertEqual(2, runner.record_outcome_time_series(targets)["savedCount"])
        self.assertEqual(["AAPL", "SPY"], [position.symbol for position in record.call_args.args[1]])

    def test_history_failures_do_not_block_other_stock_quotes(self):
        runner = self.runner(
            index_history_provider=SimpleNamespace(fetch_history=Mock(side_effect=RuntimeError("failed"))),
            time_series_store=SimpleNamespace(record_price_history=Mock()),
        )
        toss = SimpleNamespace(
            fetch_prices=Mock(return_value=({}, "token")), fetch_daily_candles=Mock(return_value=([], "token")),
            merge_market_data=lambda position, *_args, **_kwargs: position,
        )
        _, auxiliary, summary = runner.merge_focus_market_data(
            toss, "token", [], [(self.position("KOSPI"), {}), (self.position("SPY"), {})],
        )
        self.assertEqual("error", summary["indexBenchmarkHistory"]["status"])
        toss.fetch_prices.assert_called_once_with("token", ["SPY"])
        self.assertEqual(["SPY"], [position.symbol for position, _ in auxiliary])

    def test_history_write_is_immutable_and_replica_enqueue_uses_same_transaction(self):
        baseline = HistoryStore()
        store = VersionedMarketTimeSeriesStore.__new__(VersionedMarketTimeSeriesStore)
        store.baseline = baseline
        store.active_backend_id = lambda: "mysql-primary"
        store.enqueue_rows_with_connection = Mock(side_effect=lambda connection, rows: len(rows))
        first = store.record_price_history([observation()])
        later = store.record_price_history([replace(observation(), current_price=9000, observed_at="2026-09-14T07:00:00Z")])
        self.assertEqual(1, first["savedCount"])
        self.assertEqual(1, first["projectionQueuedCount"])
        self.assertEqual(0, later["savedCount"])
        self.assertEqual(0, later["projectionQueuedCount"])
        self.assertEqual(1, len(baseline.rows))
        self.assertTrue(all(sql.startswith("INSERT IGNORE") for sql in baseline.statements))
        call = store.enqueue_rows_with_connection.call_args_list[0]
        self.assertIs(baseline.connection, call.args[0])
        self.assertEqual("2026-09-14T06:50:00Z", call.args[1][0]["observed_at"])

    def test_history_storage_rejects_invalid_clocks_prices_and_quality(self):
        baseline = HistoryStore()
        point = observation()
        for changes in [
            {"source_as_of": "2026-09-14T09:00:00Z"}, {"observed_at": "2026-09-14T06:50:00"},
            {"source_as_of": ""}, {"current_price": float("inf")}, {"current_price": True},
            {"data_quality": "demo"}, {"bucket_at": "2026-09-14T04:00:00Z"}, {"current_price": None},
            {"observed_at": "2026-09-14T05:58:59Z"},
        ]:
            with self.subTest(changes=changes):
                self.assertEqual(0, baseline.record_price_history([replace(point, **changes)])["savedCount"])
        self.assertEqual([], baseline.statements)

    def test_index_history_obeys_existing_raw_retention_in_both_cleanup_paths(self):
        regular = market_time_series_retention_days({"marketTimeSeriesRawRetentionDays": "1"})
        minimal = mysql_minimal_retention_policy({"mysqlMinimalTimeSeries3mRetentionDays": "1"}).market_time_series_retention_days
        for policies in (regular, minimal):
            self.assertEqual({"1m": 1, "3m": 1, "10m": 1}, {key: policies[key] for key in ("1m", "3m", "10m")})


if __name__ == "__main__":
    unittest.main()
