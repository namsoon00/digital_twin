"""Capture consistency, frozen notification fidelity and bounded memory."""
from contextlib import contextmanager
from copy import deepcopy
import itertools
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from digital_twin.modules.ai_orchestration.domain.recovery import retry_delays
from digital_twin.modules.notifications.application.ai_observation_diagnostic import render_ai_observation_diagnostic
from digital_twin.modules.notifications.domain.observation_market_snapshot import market_snapshot_sections
from digital_twin.modules.notifications.domain.relation_change import relation_change_snapshot
from digital_twin.modules.notifications.infrastructure.notification_payload import article_history_summary, compact_payload, expand_payload
from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.portfolio.domain.portfolio import PortfolioSummary, Position
from digital_twin.modules.reasoning.domain.ontology_relation_facts import position_signal_facts
from digital_twin.modules.reasoning.infrastructure.graph_reads import execution
from digital_twin.modules.reasoning.infrastructure.projection_input_cache import SharedProjectionRuntimeContextCache, SharedPortfolioGraphAssemblyCache
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.application.observation_evidence import ObservationEvidenceReader
from digital_twin.modules.reasoning.domain.observation_evidence import EvidenceContractError, EvidenceReadError
from test_ai_control import SUBJECT
import test_ai_control as control_helpers


def store_fixture():
    driver = MagicMock()
    tx = driver.transaction.return_value.__enter__.return_value
    store = Mock(address="local", database="test")
    store.driver_imports.return_value = ((None, None, None, None, SimpleNamespace(READ="read")), None)
    store.query_timeout_seconds.return_value = 30
    store.open_driver.return_value = driver
    store.create_driver.return_value = driver
    store.read_rows_in_transaction.return_value = []
    store.with_typedb_retries.side_effect = lambda operation, **_: operation()
    return store, driver, tx


class RuntimeStabilityTests(unittest.TestCase):
    def test_selected_price_clock_stays_with_its_provider_across_kis_merge(self):
        from digital_twin.infrastructure.kis_market_signals import KISMarketSignalProvider
        provider = object.__new__(KISMarketSignalProvider)
        position = Position("TEST", "Fixture", market="KR", currency="KRW", current_price=120,
            quote_source="Toss", freshness_status="fresh", source_as_of="2026-10-02T07:52:15Z",
            source_fetched_at="2026-10-02T07:52:53Z", source_timestamp_state="provider")
        signal = {"currentPrice": 100, "quoteSource": "KIS", "marketSignalCoverage": {"price": {
            "sourceAsOf": "2026-10-02T06:57:06Z", "fetchedAt": "2026-10-02T06:57:10Z",
            "sourceTimestampState": "provider", "freshnessStatus": "last-close"}}}
        original = deepcopy(signal)
        selected = provider.merge_position(position, signal)
        self.assertEqual((120, position.source_as_of), (selected.current_price, selected.source_as_of))
        facts = {"currentPrice": selected.current_price, "sourceAsOf": selected.source_as_of,
            "sourceFetchedAt": selected.source_fetched_at, "marketSignalCoverage": selected.market_signal_coverage}
        snapshot = relation_change_snapshot({"ontologyRelationContext": {"facts": facts}})
        self.assertEqual(position.source_as_of, snapshot["observedAt"])
        self.assertEqual(position.source_fetched_at, snapshot["sourceFetchedAt"])
        self.assertIn("10/02 16:52 KST", repr(market_snapshot_sections(snapshot)))
        position.freshness_status = "stale"
        selected = provider.merge_position(position, signal)
        self.assertEqual((100, "KIS", "2026-10-02T06:57:06Z", "last-close"),
                         (selected.current_price, selected.quote_source, selected.source_as_of, selected.freshness_status))
        self.assertEqual("2026-10-02T06:57:10Z", selected.source_fetched_at)
        missing = provider.merge_position(position, {"currentPrice": 90})
        self.assertEqual("", missing.source_as_of)
        self.assertEqual("unknown", missing.freshness_status)
        facts["sourceAsOf"], facts["sourceFetchedAt"] = "", ""
        unknown = relation_change_snapshot({"ontologyRelationContext": {"facts": facts}})
        self.assertEqual("", unknown["observedAt"], "a missing selected clock cannot borrow the other price clock")
        self.assertEqual(original, signal)

    def test_question_recovery_clock_matches_capture_successor(self):
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_brain_agenda import MySQLBrainAgendaStore
        agenda = object.__new__(MySQLBrainAgendaStore)
        agenda.save = Mock()
        connection = Mock()
        connection.execute.return_value.fetchall.return_value = [{"payload_json": json.dumps({"caseId": "case"})}]
        job = {**SUBJECT, "capability": "observe", "taskId": "task"}
        with patch("digital_twin.modules.ai_orchestration.infrastructure.mysql_brain_agenda.stamp", return_value="2026-10-02T00:00:00Z"):
            agenda.failed(connection, job, "evidence-read:snapshot:TimeoutError")
            self.assertEqual("2026-10-02T00:30:00Z", agenda.save.call_args.args[1]["nextCheckAt"])
            agenda.failed(connection, job, "TimeoutError")
            self.assertEqual("2026-10-02T00:05:00Z", agenda.save.call_args.args[1]["nextCheckAt"])

    def test_article_history_uses_cached_keys_and_reads_only_one_legacy_body(self):
        context = {"signalType": "news", "articles": [{"kind": "news", "title": "Example release", "url": "https://example.com/release"}],
                   "ontologyRelationContext": {"largeProof": "x" * 100000}}
        summary = article_history_summary(context)
        self.assertTrue(summary["keys"])
        self.assertLess(len(json.dumps(summary)), 2000)
        connection = Mock()
        small, keys = MySQLNotificationJobStore.article_history_row_with_connection(connection,
            {"job_id": "new", "article_summary": json.dumps(summary)})
        connection.execute.assert_not_called()
        self.assertEqual("news", small["signalType"])
        connection.execute.return_value.fetchone.return_value = {"payload_json": json.dumps({"context": context})}
        _, legacy_keys = MySQLNotificationJobStore.article_history_row_with_connection(connection, {"job_id": "old"})
        self.assertEqual(keys, legacy_keys)
        connection.execute.assert_called_once_with("SELECT payload_json FROM notification_jobs WHERE job_id=%s", ("old",))

    def test_linked_generation_filter_excludes_retired_physical_rows(self):
        from digital_twin.modules.reasoning.infrastructure.observation_evidence import TypeDBObservationEvidenceSource
        from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
        repository = Mock()
        repository.active_abox_members_clause.return_value = ""
        repository.active_abox_metadata.return_value = {"scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
                                                       "scopeGenerationIds": {"report-scope": "active"}}
        quote = {"id": "quote", "kind": "stock", "label": "quote", "json": json.dumps({"symbol": "TEST", "currentPrice": 100})}
        report = {"id": "report", "kind": "evidence:filing", "label": "report", "json": "{}", "scopeId": "report-scope", "generationId": "active"}
        repository.read_rows.side_effect = [[quote], [], [{"id": "quote", "storageId": "physical-quote"}],
            [{"storageId": "physical-report"}, {"storageId": "retired-report"}], [], [report, {**report, "id": "retired", "generationId": "retired"}]]
        facts = TypeDBObservationEvidenceSource(repository).candidates(SUBJECT["worldId"], "TEST")
        self.assertEqual({"quote", "report"}, {row["id"] for row in facts})
        self.assertIn("ontology-world-id", repository.read_rows.call_args.args[0])
        self.assertIn("scopeId", repository.read_rows.call_args.args[1])

    def test_staged_link_reads_keep_incoming_evidence_and_filter_other_subjects(self):
        from digital_twin.modules.reasoning.infrastructure.observation_evidence import TypeDBObservationEvidenceSource
        repository = Mock()
        repository.active_abox_members_clause.return_value = ""
        def row(identity, kind, **values):
            return {"id": identity, "kind": kind, "label": identity, "json": json.dumps(values)}
        quote = row("quote", "stock", symbol="TEST", currentPrice=100)
        linked = row("report", "evidence:filing", sourceAsOf="2026-10-01")
        other = row("other", "stock", symbol="OTHER", currentPrice=10)
        repository.read_rows.side_effect = [[quote], [], [{"id": "quote", "storageId": "current-quote"}],
            [{"storageId": "current-report"}], [{"storageId": "current-report"}, {"storageId": "current-other"}], [linked, other]]
        source = TypeDBObservationEvidenceSource(repository)
        self.assertEqual({"quote", "report"}, {item["id"] for item in source.candidates(SUBJECT["worldId"], "TEST")})
        queries = [call.args[0] for call in repository.read_rows.call_args_list]
        self.assertIn('ontology-storage-id "current-quote"', queries[3])
        self.assertIn("source: $n, target: $s", queries[4])
        self.assertTrue(all(len(call.args[0]) == 1 for call in repository.active_abox_members_clause.call_args_list))
        repository.read_rows.side_effect = [[quote], [], []]
        with self.assertRaisesRegex(EvidenceContractError, "storage identity incomplete"):
            source.candidates(SUBJECT["worldId"], "TEST")

    def test_scoped_evidence_hydrates_shared_and_linked_facts_once_with_generation_fences(self):
        from digital_twin.modules.reasoning.infrastructure.observation_evidence import TypeDBObservationEvidenceSource
        from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
        repository = Mock()
        repository.active_abox_metadata.return_value = {"status": "ok",
            "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            "scopeGenerationIds": {"scope": "active"}}
        def row(identity, kind, **values):
            return {"storageId": identity, "id": identity, "kind": kind, "label": identity,
                    "scopeId": "scope", "generationId": "active", "json": json.dumps(values)}
        quote = row("quote", "stock", symbol="TEST", currentPrice=100)
        macro = row("macro", "interest-rate", value=3)
        report = row("report", "evidence:filing", value=1)
        retired = {**row("retired", "evidence:filing"), "generationId": "retired"}
        other = row("other", "stock", symbol="OTHER", currentPrice=200)
        compact = lambda value: {key: value[key] for key in ("id", "storageId", "kind")}
        repository.read_rows.side_effect = [[compact(quote), compact(macro)], [compact(macro)],
            [{"storageId": "report"}, {"storageId": "quote"}],
            [{"storageId": "report"}, {"storageId": "retired"}, {"storageId": "other"}],
            [macro, other, quote, report, retired]]
        result = TypeDBObservationEvidenceSource(repository).candidates(SUBJECT["worldId"], "TEST")
        self.assertEqual({"quote", "macro", "report"}, {value["id"] for value in result})
        self.assertEqual(5, repository.read_rows.call_count)
        self.assertNotIn("ontology-json", repository.read_rows.call_args_list[0].args[0])
        self.assertNotIn("ontology-json", repository.read_rows.call_args_list[1].args[0])
        repository.active_abox_members_clause.assert_not_called()

    def test_scoped_evidence_missing_or_rebound_native_fact_fails_closed(self):
        from digital_twin.modules.reasoning.infrastructure.observation_inventory import scoped_candidates
        identity = {"id": "quote", "storageId": "physical", "kind": "stock"}
        fact = {**identity, "label": "quote", "json": '{"symbol":"TEST"}', "scopeId": "scope", "generationId": "active"}
        for rows in ([], [{**fact, "generationId": "retired"}], [{**fact, "id": "wrong"}],
                     [fact, fact], [{**fact, "storageId": "unsolicited"}]):
            repository = Mock()
            repository.read_rows.side_effect = [[identity], [], [], [], rows]
            with self.subTest(rows=rows), self.assertRaises(EvidenceContractError):
                scoped_candidates(repository, SUBJECT["worldId"], "TEST", {"scopeGenerationIds": {"scope": "active"}})

    def test_evidence_metadata_cache_is_scoped_to_capture_and_cleared_after_failure(self):
        from contextlib import contextmanager
        from digital_twin.modules.reasoning.infrastructure.observation_evidence import TypeDBObservationEvidenceSource
        class Repository:
            reads = 0
            @contextmanager
            def read_snapshot_scope(self):
                yield
            def active_abox_metadata(self, world):
                self.reads += 1
                return {"world": world, "capture": self.reads}
        repository = Repository()
        source = TypeDBObservationEvidenceSource(repository)
        with self.assertRaises(ValueError):
            with source.capture():
                self.assertEqual(source.metadata("world"), source.metadata("world"))
                self.assertEqual(1, repository.reads)
                raise ValueError("capture failed")
        with source.capture():
            self.assertEqual(2, source.metadata("world")["capture"])
        self.assertEqual(3, source.metadata("world")["capture"])

    def test_diagnostic_is_identical_after_json_key_reordering(self):
        diagnostic = {"review": {"sections": {"summary": "reason 1", "comparison": "reason 2", "notificationReason": "reason 3"}}}
        expected = render_ai_observation_diagnostic(diagnostic)
        for order in itertools.permutations(diagnostic["review"]["sections"]):
            changed = deepcopy(diagnostic)
            changed["review"]["sections"] = {key: diagnostic["review"]["sections"][key] for key in order}
            self.assertEqual(expected, render_ai_observation_diagnostic(json.loads(json.dumps(changed))))
        changed["review"]["sections"]["summary"] = "tampered"
        self.assertNotEqual(expected, render_ai_observation_diagnostic(changed))

    def test_capture_reads_share_native_snapshot_and_release_on_error(self):
        store, driver, tx = store_fixture()
        with self.assertRaisesRegex(RuntimeError, "capture failed"):
            with execution.read_snapshot_scope(store):
                execution.read_rows(store, "first", [])
                with execution.read_snapshot_scope(store):
                    execution.read_rows(store, "second", [])
                raise RuntimeError("capture failed")
        self.assertEqual(1, driver.transaction.call_count)
        store.ensure_database.assert_not_called()
        self.assertTrue(all(call.args[0] is tx for call in store.read_rows_in_transaction.call_args_list))
        driver.transaction.return_value.__exit__.assert_called_once()
        store.close_driver.assert_called_once_with(driver)
        execution.read_rows(store, "fresh", [])
        self.assertEqual(2, driver.transaction.call_count, "a failed scope cannot leak a closed snapshot")

    def test_snapshot_deadline_and_repository_thread_isolation(self):
        from concurrent.futures import ThreadPoolExecutor
        store, driver, _ = store_fixture()
        other, other_driver, other_tx = store_fixture()
        with patch.object(execution.time, "monotonic", return_value=100):
            with execution.read_snapshot_scope(store, timeout_seconds=3):
                with execution.read_snapshot_scope(other):
                    execution.read_rows(other, "other", [])
                    execution.read_rows(store, "outer", [])
                    self.assertEqual(1, driver.transaction.call_count, "nested repositories must preserve the outer snapshot")
                self.assertIs(other_tx, other.read_rows_in_transaction.call_args.args[0])
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(execution.read_rows, store, "thread", []).result()
                self.assertEqual(2, driver.transaction.call_count, "another thread needs its own transaction")
                with patch.object(execution.time, "monotonic", return_value=103):
                    with self.assertRaises(TimeoutError):
                        execution.read_rows(store, "expired", [])
        self.assertEqual(1, other_driver.transaction.call_count)
        self.assertEqual(2, store.read_rows_in_transaction.call_count, "expired scope must not issue a query")

    def test_snapshot_open_failure_defers_without_model_or_unsafe_diagnostics(self):
        class Source:
            @contextmanager
            def capture(self):
                raise TimeoutError("private query and provider credentials")
                yield
        reader = ObservationEvidenceReader(Source())
        with self.assertRaises(EvidenceReadError) as failed:
            reader(SUBJECT)
        self.assertEqual("evidence-read:snapshot:TimeoutError", failed.exception.code)
        service, store, planner = control_helpers.AIControlTests().runner(evidence=reader)
        self.assertEqual("deferred", service.run_once()["status"])
        planner.assert_not_called()
        store.save_execution_input.assert_not_called()
        self.assertNotIn("private", repr(store.fail.call_args))

    def test_ownership_and_generation_fences_survive_optional_snapshot_scope(self):
        source = SimpleNamespace(metadata=lambda _: {"status": "ok", "aboxSnapshotId": "old", "accountId": SUBJECT["accountId"]},
            snapshot_id=Mock(side_effect=["old", "new"]),
            candidates=lambda *_: [{"id": "quote", "kind": "stock", "currentPrice": 100}])
        with self.assertRaisesRegex(EvidenceContractError, "changed during capture"):
            ObservationEvidenceReader(source)(SUBJECT)
        source.metadata = lambda _: {"status": "ok", "aboxSnapshotId": "old", "accountId": "other"}
        with self.assertRaisesRegex(EvidenceContractError, "account mismatch"):
            ObservationEvidenceReader(source)(SUBJECT)

    def test_capture_and_execution_retries_keep_separate_bounded_policies(self):
        for reason in ("evidence-read:inventory-subject:TimeoutError", "evidence-contract:graph-changed-during-capture"):
            self.assertEqual((30, 1800), retry_delays("observe", reason, 1))
            self.assertEqual((120, 1800), retry_delays("observe", reason, 2))
        for reason in ("TimeoutError", "TimeoutExpired", "ai-execution:timeout"):
            self.assertEqual((60, 300), retry_delays("observe", reason, 1))
        for reason in ("evidence-contract:graph-account-mismatch", "evidence-contract:graph-fact-subject-mismatch"):
            self.assertEqual((1800, 21600), retry_delays("observe", reason, 1))
        self.assertEqual((1800, 21600), retry_delays("research", "evidence-read:snapshot:TimeoutError", 1))

    def test_quote_source_clock_survives_fact_and_notification_boundaries(self):
        position = Position("TEST", "Test", market="US", currency="USD", current_price=100,
            updated_at="2026-10-02T06:00:00Z", source_as_of="2026-10-01T20:00:00Z",
            source_fetched_at="2026-10-02T05:55:00Z", freshness_status="stale")
        facts = position_signal_facts(position, PortfolioSummary(0, 0, 0, [], [], 0))
        snapshot = relation_change_snapshot({"ontologyRelationContext": {"facts": facts, "subject": {"symbol": "TEST"}}})
        self.assertEqual(position.source_as_of, snapshot["observedAt"])
        self.assertEqual(position.source_fetched_at, snapshot["sourceFetchedAt"])
        self.assertEqual("", snapshot["capturedAt"])
        self.assertIn("10/02 05:00 KST", repr(market_snapshot_sections(snapshot)))
        self.assertIn("과거 시세 참고", repr(market_snapshot_sections(snapshot)))
        facts.pop("sourceAsOf")
        missing = relation_change_snapshot({"ontologyRelationContext": {"facts": facts}})
        self.assertEqual("", missing["observedAt"], "updatedAt/fetchedAt must never renew a missing quote clock")

    def test_duplicate_payload_roundtrip_preserves_every_value_and_legacy_rows(self):
        context = {"ontologyRelationContext": {"facts": {"currentPrice": 100}, "proof": "proof" * 10000},
                   "ontologyPromptContext": {"prompt": "context" * 10000},
                   "investmentSubjectDecisionCase": {"id": "top"}}
        context["metadata"] = {**deepcopy(context), "investmentSubjectDecisionCase": {"id": "different"}}
        job = NotificationJob.create("frozen body", context=context)
        original = job.to_dict()
        encoded = MySQLNotificationJobStore.compact_job_payload(job)
        self.assertLess(len(json.dumps(encoded)), len(json.dumps(original)) * .6)
        loaded = MySQLNotificationJobStore.job_from_row({"text": job.text, "payload_json": json.dumps(encoded, sort_keys=True)})
        self.assertEqual(original, loaded.to_dict())
        self.assertEqual(context, job.context, "saving must not mutate a pending job")
        loaded.context["metadata"]["ontologyRelationContext"]["facts"]["currentPrice"] = 1
        self.assertEqual(100, loaded.context["ontologyRelationContext"]["facts"]["currentPrice"])
        self.assertEqual(original, MySQLNotificationJobStore.job_from_row({"payload_json": json.dumps(original)}).to_dict())
        broken = deepcopy(encoded)
        broken["context"].pop("ontologyRelationContext")
        with self.assertRaises(ValueError):
            expand_payload(broken)
        typed = {"context": {"ontologyRelationContext": {"value": 1},
                             "metadata": {"ontologyRelationContext": {"value": True}}}}
        self.assertNotIn("contextEncoding", compact_payload(typed))

    def test_cache_byte_limits_evict_lru_and_skip_oversized_work_without_truncation(self):
        cache = SharedProjectionRuntimeContextCache(max_bytes=3500)
        for key in ("one", "two", "three"):
            cache.put(key, {"body": key * 300}, 100)
        self.assertEqual("miss", cache.get("one", 60)["status"])
        self.assertEqual("three" * 300, cache.get("three", 60)["context"]["body"])
        cache.put("three", {"body": "x" * 10000}, 100)
        self.assertEqual("miss", cache.get("three", 60)["status"])
        graph = PortfolioOntology("fixture", worldview={"proof": "x" * 10000})
        graphs = SharedPortfolioGraphAssemblyCache(max_bytes=4000)
        graphs.put("too-large", graph, graph, 10)
        self.assertEqual("miss", graphs.get("too-large", 60)["status"])
        self.assertEqual(10000, len(graph.worldview["proof"]), "uncached computation retains the entire graph")
