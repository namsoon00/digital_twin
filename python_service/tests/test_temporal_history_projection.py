"""Small history reads preserve source facts, replay, isolation and corrections."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from digital_twin.infrastructure.transactions import monitoring
from digital_twin.modules.market_data.infrastructure.transaction_writes import upsert_monitor_snapshot_history
from digital_twin.modules.reasoning.application.independent_reasoning_engine import IndependentReasoningInputAssembler
from digital_twin.modules.reasoning.domain import ontology_projection_input as projection
from digital_twin.modules.reasoning.domain import portfolio_ontology_temporal_concepts as temporal
from digital_twin.modules.reasoning.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.modules.reasoning.domain.projection_facts import factual_runtime_metadata, graph_for_graph_store_persistence
from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.modules.portfolio.domain.portfolio import account_snapshot_from_monitor_state
from stabilization_database import StabilizationDatabaseCase
from test_reasoning_snapshot_replay import monitor_state


def history_state(stamp="2026-07-29T00:01:00Z"):
    state = monitor_state(stamp)
    state["positions"]["AAPL"].update({
        "trade_strength": 115.3, "foreign_net_buy_volume": -125,
        "institution_net_buy_volume": 81, "volume_ratio": 1.8,
        "quote_observed_at": stamp, "customCoverage": {"source": "fixture", "valid": False},
    })
    state["externalSignals"] = {
        "researchEvidence": {"AAPL": [{"title": "Direct event", "evidenceId": "e:one",
            "observedAt": stamp, "polarity": "positive", "kind": "research",
            "payload": {"relationScope": "direct", "sourceTrustState": "verified", "eventType": "earnings"}}]},
        "equityQuotes": {"AAPL": {"price": 100, "observedAt": stamp, "provider": "fixture"}},
        "newsHeadlines": {"AAPL": [{"title": "Event", "publishedAt": stamp, "url": "https://example.test/event"}]},
        "secFilings": {"AAPL": {"filingDate": "2026-07-28", "form": "10-Q", "title": "Report"}},
        "dartDisclosures": {"AAPL": {"title": "공시", "publishedAt": stamp}},
        "companyOverviews": {"AAPL": {"name": "Apple", "description": "archive " * 5000}},
        "companyKnowledge": {"AAPL": {"symbol": "AAPL", "description": "retained archive " * 5000}},
    }
    return state


def legacy_signals(signals, symbol):
    full = projection.compact_external_signals_for_ontology(signals, target_symbols=[symbol])
    return {key: value for key, value in full.items() if key in projection.TEMPORAL_HISTORY_SIGNAL_GROUPS}


class TemporalHistoryProjectionTests(TestCase):
    def test_temporal_facts_and_persisted_abox_equal_the_previous_path(self):
        state = history_state()
        original = deepcopy(state)
        stored = projection.compact_monitor_state_for_ontology(state)
        legacy = projection.compact_monitor_state_for_ontology(stored)
        prepared = projection.compact_monitor_history_state(stored)
        self.assertEqual(original, state)
        self.assertEqual(legacy["positions"], prepared["positions"])
        self.assertNotIn("companyKnowledge", prepared["externalSignals"])
        for symbol in ["AAPL", "MSFT", ""]:
            self.assertEqual(legacy_signals(state["externalSignals"], symbol),
                             projection.temporal_research_signals_for_symbol(state["externalSignals"], symbol))
        snapshot = account_snapshot_from_monitor_state(monitor_state())

        def graph(history):
            metadata = factual_runtime_metadata({"monitorStateHistory": [history]}, target_symbols=["AAPL"])
            built = build_portfolio_ontology(snapshot.positions, snapshot.portfolio,
                portfolio_id="acct", runtime_context={"asOf": snapshot.generated_at, "metadata": metadata},
                include_tbox=False, include_presentation=False, include_derived_decision_items=False)
            rules = [rule.to_dict() for rule in default_graph_inference_rules()]
            relation_types = sorted({condition["relation_type"] for rule in rules
                for condition in rule.get("conditions", []) if condition.get("relation_type")})
            persisted = graph_for_graph_store_persistence(built, {"rules": rules, "inputRelationTypes": relation_types})
            # The unconsumed metadata envelope changes size. Compare every
            # factual entity/edge that actually reaches the native graph.
            self.assertGreater(len(persisted.entities), 30)
            return {"entities": [item.to_dict() for item in persisted.entities],
                    "relations": [item.to_dict() for item in persisted.relations]}

        with patch.object(temporal, "temporal_research_signals_for_symbol", legacy_signals):
            before = graph(legacy)
        self.assertEqual(before, graph(prepared))
        prepared["positions"]["AAPL"]["customCoverage"]["valid"] = True
        self.assertEqual(original, state)

    def test_assembler_uses_prepared_reader_and_does_not_swallow_failures(self):
        value = projection.compact_monitor_history_state(history_state())
        store = SimpleNamespace(load_temporal_history=Mock(return_value=[value]),
                                load_history=Mock(side_effect=AssertionError("broad history read")))
        assembler = IndependentReasoningInputAssembler(None, None, store)
        self.assertEqual([value], assembler.history("acct"))
        store.load_temporal_history.assert_called_once_with("acct", limit=96)
        store.load_temporal_history.side_effect = RuntimeError("history unavailable")
        with self.assertRaisesRegex(RuntimeError, "history unavailable"):
            assembler.history("acct")
        fallback = IndependentReasoningInputAssembler(None, None, SimpleNamespace(load_history=lambda *_a, **_k: [history_state()]))
        self.assertEqual([value], fallback.history("acct"))
        # Worker progress deliberately admits scalars only. Substage timings
        # must survive that boundary as well as the completed job receipt.
        from digital_twin.modules.reasoning.application.independent_reasoning_engine import V2ReasoningEngine, IndependentReasoningJobRunner
        from test_independent_reasoning_engine import descriptor, source_event, FakeExecutor, FakeCandidateBuilder
        prepared = SimpleNamespace(assemble=lambda _request: {"status": "deferred", "snapshots": [],
            "inputTimingsMs": {"inputTemporalHistoryMs": 7}, "preflight": {"ready": False}})
        engine = V2ReasoningEngine(descriptor(), prepared, FakeExecutor(), FakeCandidateBuilder())
        runner = IndependentReasoningJobRunner(SimpleNamespace(), engine, SimpleNamespace())
        result = engine.consume([source_event()], progress_callback=runner.record_progress)
        self.assertEqual(7, result["stage_durations_ms"]["inputTemporalHistoryMs"])
        self.assertEqual(7, runner.current_progress()["details"]["inputTemporalHistoryMs"])


class TemporalHistoryStorageTests(StabilizationDatabaseCase):
    def setUp(self):
        super().setUp()
        self.store = monitoring.MySQLOntologyReasoningMonitorStore(self.settings)
        self.account = self.deployment

    def save(self, state, account=None):
        with self.store.transaction() as connection:
            upsert_monitor_snapshot_history(connection, account or self.account, state["generatedAt"], state,
                projection.compact_monitor_state_for_ontology(state), "unchanged-clock")

    def row(self, stamp):
        with self.store.connect() as connection:
            return connection.execute("SELECT * FROM monitor_snapshot_history WHERE account_id=%s AND generated_at=%s",
                                      (self.account, stamp)).fetchone()

    def test_indexed_group_tails_preserve_windows_cutoff_accounts_rows_and_exact_watermark(self):
        from datetime import datetime, timezone, timedelta
        from digital_twin.modules.market_data.infrastructure import mysql_market_time_series as module
        from digital_twin.modules.market_data.infrastructure.mysql_market_time_series import MySQLMarketTimeSeriesStore
        from digital_twin.modules.market_data.infrastructure.mysql_temporal_reads import latest_group_rows
        import uuid
        account = "temporal-tail:" + uuid.uuid4().hex
        symbols = ["TAIL" + uuid.uuid4().hex[:8].upper() for _ in range(3)]
        base = datetime(2026,9,10,tzinfo=timezone.utc)
        stamp = lambda at: at.isoformat().replace("+00:00", "Z")
        cutoff = stamp(base + timedelta(minutes=90))
        store = MySQLMarketTimeSeriesStore({**self.settings, "marketTimeSeriesMaxPointsPerWindow": "20"})
        with store.transaction() as connection:
            for owner in [account, "__market_data__", account + ":foreign"]:
                for symbol in symbols:
                    for granularity, count in [("3m",40),("1d",25)]:
                        for index in range(count):
                            bucket = base + timedelta(minutes=index*3) if granularity == "3m" else base - timedelta(days=25-index)
                            observed = bucket + timedelta(minutes=1)
                            if index == 27:
                                observed = base + timedelta(days=1)
                            connection.execute(
                                "INSERT INTO market_time_series_observations "
                                "(account_id,symbol,granularity,bucket_at,observed_at,source_as_of,provider,source_role,market,currency,current_price,data_quality,investor_coverage_json) "
                                "VALUES (%s,%s,%s,%s,%s,%s,'fixture','market-history','KR','KRW',%s,'actual',%s)",
                                (owner,symbol,granularity,stamp(bucket),stamp(observed),stamp(bucket),100+index,json.dumps({"fixture":index})))
        definitions = temporal.parse_temporal_windows("1D=1d:2;20D=20d:2;30M=30m:2")

        def legacy(connection, accounts, subjects, granularity, boundary, limit):
            placeholders = ",".join(["%s"] * len(subjects))
            return connection.execute(
                "SELECT * FROM (SELECT observations.*, ROW_NUMBER() OVER (PARTITION BY account_id,symbol ORDER BY bucket_at DESC) AS row_number_value "
                "FROM market_time_series_observations observations WHERE account_id IN (%s,%s) AND granularity=%s "
                "AND symbol IN (" + placeholders + ")" + (" AND observations.observed_at <= %s" if boundary else "")
                + ") ranked WHERE ranked.row_number_value<=%s ORDER BY bucket_at DESC",
                (*accounts,granularity,*subjects,*((boundary,) if boundary else ()),limit)).fetchall()

        with store.connect() as connection:
            before = connection.execute("SELECT * FROM market_time_series_observations WHERE symbol IN (" + ",".join(["%s"]*len(symbols)) + ") ORDER BY account_id,symbol,granularity,bucket_at",symbols).fetchall()
            expected_watermark = connection.execute("SELECT MAX(observed_at) AS latest FROM market_time_series_observations").fetchone()["latest"]
        for boundary in [cutoff, "", stamp(base-timedelta(days=30))]:
            with patch.object(module, "latest_group_rows", side_effect=legacy):
                expected = store.load_temporal_windows(account,symbols,definitions,as_of=boundary)
            actual = store.load_temporal_windows(account,symbols,definitions,as_of=boundary)
            self.assertEqual(expected, actual)
        self.assertEqual(expected_watermark, store.watermark().observed_through)
        with store.connect() as connection:
            after = connection.execute("SELECT * FROM market_time_series_observations WHERE symbol IN (" + ",".join(["%s"]*len(symbols)) + ") ORDER BY account_id,symbol,granularity,bucket_at",symbols).fetchall()
            self.assertEqual(before,after)
            connection.execute("DELETE FROM market_time_series_observations WHERE symbol IN (" + ",".join(["%s"]*len(symbols)) + ") AND observed_at>%s", (*symbols,cutoff))
            current = connection.execute("SELECT MAX(observed_at) AS latest FROM market_time_series_observations").fetchone()["latest"]
        self.assertEqual(current,store.watermark().observed_through)
        # A failed later group never becomes an apparently complete history.
        calls = []
        def fail_later(sql, params):
            calls.append((sql,params))
            if len(calls) == 2:
                raise TimeoutError("later temporal group failed")
            return SimpleNamespace(fetchall=lambda: [{"retained":True}])
        with self.assertRaisesRegex(TimeoutError,"later temporal group failed"):
            latest_group_rows(SimpleNamespace(execute=fail_later),[account,"__market_data__"],symbols,"3m",cutoff,20)
        self.assertEqual(2,len(calls))
        self.assertTrue(all(sql.count("LIMIT %s")<=4 and "ROW_NUMBER" not in sql for sql,_ in calls))

    def test_prepared_reads_preserve_replay_order_limit_account_and_source(self):
        for minute in [1, 2, 3]:
            self.save(history_state("2026-07-29T00:0%d:00Z" % minute))
        self.save(history_state("2026-07-29T00:04:00Z"), account="other-" + self.account)
        expected = [projection.compact_monitor_history_state(item) for item in self.store.load_history(self.account, 2)]
        self.assertEqual(expected, self.store.load_temporal_history(self.account, 2))
        stamp = expected[0]["generatedAt"]
        stored = self.row(stamp)
        self.assertEqual(history_state(stamp), json.loads(stored["payload_json"]))
        self.assertEqual(json.loads(stored["projection_payload_json"]), self.store.reasoning_snapshot_state_at(self.account, stamp))
        # The prepared read must not decode the unrelated broad archive.
        with self.store.transaction() as connection:
            connection.execute("UPDATE monitor_snapshot_history SET projection_payload_json='invalid broad archive' WHERE account_id=%s",
                               (self.account,))
        self.assertEqual(expected, self.store.load_temporal_history(self.account, 2))

    def test_legacy_mixed_unknown_versions_and_raw_fallback_keep_existing_selection(self):
        for minute in range(1, 9):
            self.save(history_state("2026-07-29T00:0%d:00Z" % minute))
        with self.store.transaction() as connection:
            connection.execute("UPDATE monitor_snapshot_history SET temporal_projection_version='old' WHERE account_id=%s AND generated_at < %s",
                               (self.account, "2026-07-29T00:04:00Z"))
        self.assertEqual([projection.compact_monitor_history_state(item) for item in self.store.load_history(self.account, 96)],
                         self.store.load_temporal_history(self.account, 96))
        with self.store.transaction() as connection:
            connection.execute("UPDATE monitor_snapshot_history SET projection_payload_json=NULL,temporal_projection_version='' WHERE account_id=%s", (self.account,))
        self.assertEqual(6, len(self.store.load_temporal_history(self.account, 96)))
        self.assertEqual([projection.compact_monitor_history_state(item) for item in self.store.load_history(self.account, 96)],
                         self.store.load_temporal_history(self.account, 96))
        with self.store.transaction() as connection:
            connection.execute("UPDATE monitor_snapshot_history SET payload_json='invalid' WHERE account_id=%s", (self.account,))
        with self.assertRaises(ValueError):
            self.store.load_temporal_history(self.account, 96)

    def test_atomic_correction_rollback_and_invalid_prepared_data(self):
        state = history_state(); self.save(state)
        before = self.row(state["generatedAt"])
        corrected = deepcopy(state); corrected["positions"]["AAPL"]["current_price"] = 90
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.store.transaction() as connection:
                upsert_monitor_snapshot_history(connection, self.account, state["generatedAt"], corrected,
                    projection.compact_monitor_state_for_ontology(corrected), "new-clock")
                raise RuntimeError("rollback")
        self.assertEqual(before, self.row(state["generatedAt"]))
        self.save(corrected)
        self.assertEqual(90, self.store.load_temporal_history(self.account)[0]["positions"]["AAPL"]["current_price"])
        for invalid in ["bad JSON", "[]", "null", "{}", '{"generatedAt":"other-source"}']:
            with self.store.transaction() as connection:
                connection.execute("UPDATE monitor_snapshot_history SET temporal_payload_json=%s WHERE account_id=%s", (invalid, self.account))
            with self.assertRaises(ValueError):
                self.store.load_temporal_history(self.account)

    def test_backfill_is_idempotent_and_preserves_source_projection_and_clocks(self):
        from digital_twin.infrastructure.mysql_schema_tuning import ensure_mysql_columns, MYSQL_OPERATIONAL_COLUMNS
        state = history_state(); self.save(state)
        with self.store.transaction() as connection:
            # Rehearse upgrading an existing populated table, not only CREATE.
            connection.execute("ALTER TABLE monitor_snapshot_history DROP COLUMN temporal_payload_json, DROP COLUMN temporal_projection_version")
            created = ensure_mysql_columns(connection, {"monitor_snapshot_history": MYSQL_OPERATIONAL_COLUMNS["monitor_snapshot_history"]})
            self.assertEqual({"monitor_snapshot_history.temporal_payload_json", "monitor_snapshot_history.temporal_projection_version"}, set(created))
        before = self.row(state["generatedAt"])
        self.assertEqual({"selected": 1, "updated": 1, "racedOrDeleted": 0}, self.store.backfill_temporal_history(self.account))
        after = self.row(state["generatedAt"])
        for key in ["payload_json", "projection_payload_json", "created_at", "generated_at", "account_id"]:
            self.assertEqual(before[key], after[key])
        self.assertEqual(0, self.store.backfill_temporal_history(self.account)["selected"])

    def test_backfill_cannot_overwrite_a_concurrent_source_correction(self):
        state = history_state(); self.save(state)
        with self.store.transaction() as connection:
            connection.execute("UPDATE monitor_snapshot_history SET temporal_projection_version='' WHERE account_id=%s", (self.account,))
        corrected = deepcopy(state); corrected["positions"]["AAPL"]["current_price"] = 120

        def race(value):
            # Simulate an older writer which has not yet restarted, too.
            with self.store.transaction() as connection:
                connection.execute("UPDATE monitor_snapshot_history SET projection_payload_json=%s WHERE account_id=%s",
                                   (json.dumps(corrected), self.account))
            return projection.compact_monitor_history_state(value)

        with patch.object(monitoring, "compact_monitor_history_state", side_effect=race):
            self.assertEqual({"selected": 1, "updated": 0, "racedOrDeleted": 1}, self.store.backfill_temporal_history(self.account))
        self.assertEqual(120, self.store.load_temporal_history(self.account)[0]["positions"]["AAPL"]["current_price"])
