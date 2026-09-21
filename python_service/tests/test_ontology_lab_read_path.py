import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from digital_twin.infrastructure.typedb_ontology import TypeDBOntologyGraphRepository
from digital_twin.infrastructure.schedulers import OntologyLabScheduler
from digital_twin.infrastructure.composition.reasoning_health import active_versioned_reasoning_queue_state
from digital_twin.modules.reasoning.infrastructure.graph_reads.execution import retryable_read_error
from digital_twin.modules.model_registry.application.ontology_rule_candidate_service import RuleChangeCandidateProposalService
from digital_twin.modules.model_registry.application.ontology_lab_service import OntologyLabService


WORLD = "portfolio:local:example"


class InferenceReadTests(unittest.TestCase):
    def repository(self):
        return TypeDBOntologyGraphRepository("127.0.0.1:1739")

    def test_online_generation_selection_reads_only_publication_markers(self):
        repo = self.repository()
        with patch.object(repo, "read_rows", return_value=[{
            "snapshotId": "gen:current", "updatedAt": "2026-09-21T12:00:00Z",
            "json": json.dumps({"publicationStatus": "active", "sourceAboxSnapshotId": "abox:1",
                                "expectedEntityCount": 20, "expectedRelationCount": 35}),
        }]) as reader:
            rows = repo.read_inference_generation_records(world_id=WORLD)
        self.assertEqual(1, reader.call_count)
        self.assertIn('has ontology-kind "inference-generation"', reader.call_args.args[0])
        self.assertIn(WORLD, reader.call_args.args[0])
        self.assertEqual("gen:current", rows[0]["generationId"])
        self.assertEqual("abox:1", rows[0]["sourceAboxSnapshotId"])
        self.assertEqual(21, rows[0]["entityCount"])
        with patch.object(repo, "read_rows", return_value=[]) as reader:
            repo.read_inferencebox_entity_rows("gen:current", ["TSLA"], 80, world_id=WORLD)
            self.assertEqual("typedb.inference-generation-nodes", reader.call_args.kwargs["label"])
            repo.read_inferencebox_relation_rows("gen:current", ["TSLA"], 80, world_id=WORLD)
            self.assertEqual("typedb.inference-generation-relations", reader.call_args.kwargs["label"])
            for call in reader.call_args_list:
                self.assertIn("gen:current", call.args[0])
                self.assertIn(WORLD, call.args[0])
                self.assertIn("limit 80", call.args[0])

    def test_inventory_aggregates_large_proof_sets_without_reading_json(self):
        repo = self.repository()
        queries = []

        def read(query, columns, **kwargs):
            queries.append((query, columns))
            if "markers" in kwargs["label"]:
                return []
            self.assertNotIn("ontology-json", query)
            self.assertIn("groupby $snapshotId, $updatedAt", query)
            return [{"snapshotId": "gen:old", "updatedAt": "2026-09-20T12:00:00Z", "rowCount": 100000}]

        with patch.object(repo, "read_rows", side_effect=read):
            rows = repo.read_inference_generation_records(False, WORLD)
        self.assertEqual(4, len(queries))
        self.assertEqual(100000, rows[0]["entityCount"])
        self.assertEqual(100000, rows[0]["relationCount"])
        self.assertEqual("staging", rows[0]["publicationStatus"])

    def test_missing_publication_never_scans_historical_facts(self):
        repo = self.repository()
        with patch.object(repo, "read_inference_generation_records", return_value=[]), \
                patch.object(repo, "read_entity_rows", side_effect=AssertionError("unbounded read")), \
                patch.object(repo, "read_relation_rows", side_effect=AssertionError("unbounded read")):
            result = repo.inferencebox_snapshot(["MSTR"], world_id=WORLD)
        self.assertEqual("missing-generation", result["status"])
        self.assertFalse(result["generationAligned"])
        self.assertEqual([], result["relations"])

    def test_timeout_is_not_immediately_retried_but_connection_error_is(self):
        for error in [TimeoutError("deadline"), RuntimeError("[TSV17] transaction timeout"),
                      RuntimeError("[TSV13] Execution interrupted by concurrent close")]:
            self.assertFalse(retryable_read_error(error))
        self.assertTrue(retryable_read_error(ConnectionError("connection refused")))


class LabSchedulingTests(unittest.TestCase):
    def service(self):
        return SimpleNamespace(auto_suggest_enabled=lambda: True, auto_suggest_configured=lambda: True,
                               auto_suggest_interval_seconds=lambda: 3600, auto_suggest=Mock())

    def test_failures_back_off_without_marking_success(self):
        service = self.service()
        service.auto_suggest.side_effect = RuntimeError("query timeout")
        scheduler = OntologyLabScheduler(service, 300, error_reporter=Mock())
        for now, delay in [(100, 300), (400, 900), (1300, 2700), (4000, 3600)]:
            with patch("digital_twin.infrastructure.schedulers.time.monotonic", return_value=now):
                with self.assertRaises(RuntimeError):
                    scheduler.run_auto_suggest()
            self.assertFalse(scheduler.auto_suggest_due(now + delay - 1))
            self.assertTrue(scheduler.auto_suggest_due(now + delay))
            self.assertEqual(0, scheduler.last_auto_suggest_at)

    def test_deferral_is_not_a_success_and_success_resets_backoff(self):
        service = self.service()
        scheduler = OntologyLabScheduler(service, 300, error_reporter=Mock())
        scheduler.auto_suggest_failures = 2
        with patch("digital_twin.infrastructure.schedulers.time.monotonic", return_value=100):
            service.auto_suggest.return_value = {"status": "deferred-reasoning-queue"}
            scheduler.run_auto_suggest()
            self.assertEqual(400, scheduler.next_auto_suggest_at)
            self.assertEqual(0, scheduler.last_auto_suggest_at)
            service.auto_suggest.return_value = {"status": "created"}
            scheduler.run_auto_suggest()
        self.assertEqual(0, scheduler.auto_suggest_failures)
        self.assertEqual(3700, scheduler.next_auto_suggest_at)

    def test_executing_candidate_is_included_even_when_not_the_configured_v2(self):
        registry = SimpleNamespace(
            control=lambda: SimpleNamespace(active_deployment_id="live", delivery_deployment_id="live", candidate_deployment_id="candidate"),
            get=lambda _: {"engineVersion": "v2"},
        )
        jobs = SimpleNamespace(live_queue_state=lambda ident: {
            "effectivePendingCount": 2 if ident == "candidate" else 0,
            "processingCount": 2 if ident == "candidate" else 0, "queuedCount": 0,
        })
        result = active_versioned_reasoning_queue_state(registry, jobs, "live")
        self.assertEqual(["live", "candidate"], result["deploymentIds"])
        self.assertEqual(2, result["effectivePendingCount"])

    def test_unavailable_queue_probe_defers_background_work(self):
        service = OntologyLabService(None, None, reasoning_queue_probe=Mock(side_effect=RuntimeError("unavailable")))
        self.assertEqual("deferred-reasoning-queue", service.reasoning_queue_deferral()["status"])


class LabAuthoringContextTests(unittest.TestCase):
    def repository(self):
        return SimpleNamespace(
            rulebox_snapshot=lambda: {"status": "ok"},
            inferencebox_recovery_metadata=Mock(return_value={
                "status": "ok", "inferenceGenerationId": "gen:tsla", "sourceAboxSnapshotId": "abox:tsla",
                "targetSymbols": ["TSLA"],
            }),
            inferencebox_snapshot=Mock(return_value={"status": "ok", "relations": [], "generationAligned": True}),
        )

    def test_authoring_pins_actual_subject_and_generation_and_reports_omissions(self):
        repo = self.repository()
        service = RuleChangeCandidateProposalService(repo, Mock())
        context = service.build_context(["MSTR", "TSLA"], "lab", world_id=WORLD)
        self.assertEqual(["TSLA"], context["symbols"])
        self.assertEqual(["MSTR"], context["notEvaluatedSymbols"])
        repo.inferencebox_snapshot.assert_called_once_with(
            ["TSLA"], limit=80, world_id=WORLD,
            inference_generation_id="gen:tsla", source_abox_snapshot_id="abox:tsla",
        )

    def test_unrelated_generation_does_not_call_ai(self):
        repo = self.repository()
        advisor = Mock()
        service = RuleChangeCandidateProposalService(repo, advisor)
        result = service.propose(["MSTR"], account_id="example")
        self.assertEqual("deferred-inference", result["status"])
        advisor.propose.assert_not_called()
        repo.inferencebox_snapshot.assert_not_called()

    def test_missing_marker_does_not_call_ai_or_fallback_to_unscoped_read(self):
        repo = self.repository()
        repo.inferencebox_recovery_metadata.return_value = {"status": "missing"}
        advisor = Mock()
        result = RuleChangeCandidateProposalService(repo, advisor).propose(["TSLA"], account_id="example")
        self.assertEqual("deferred-inference", result["status"])
        advisor.propose.assert_not_called()
        repo.inferencebox_snapshot.assert_not_called()

    def test_error_includes_safe_query_phase_and_partial_row_count(self):
        repo = self.repository()
        repo.inferencebox_snapshot.return_value = {
            "status": "error", "reason": "read timeout", "typedbQueryMetrics": {"slowQueries": [
                {"status": "error", "label": "typedb.inference-generation-nodes", "rowCount": 80,
                 "durationMs": 20000, "queryHash": "abc", "queryPreview": "PRIVATE QUERY"},
            ]},
        }
        with self.assertRaises(RuntimeError) as raised:
            RuleChangeCandidateProposalService(repo, Mock()).propose(["TSLA"], account_id="example")
        self.assertIn("rows=80", str(raised.exception))
        self.assertNotIn("PRIVATE", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
