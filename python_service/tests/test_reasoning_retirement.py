import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch
from digital_twin.infrastructure.mysql_operational_connection import MySQLConnectionProxy

from stabilization_database import StabilizationDatabaseCase
from digital_twin.modules.reasoning.application.reasoning_engine_platform import ReasoningEnginePlatformService
from digital_twin.modules.reasoning.domain.reasoning_engine_versions import EngineReleaseBundle, ReasoningEngineDescriptor
from digital_twin.modules.reasoning.infrastructure.mysql_retired_data import MySQLRetiredReasoningDataStore
from digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime import MySQLReasoningEngineJobStore


class ConsolidationAdmissionTests(unittest.TestCase):
    def test_explicit_validation_has_execution_priority_without_fabricating_market_severity(self):
        payload = {"reviewLevel": "observe", "candidateValidationReplay": True,
                   "trigger": "paired-current-source-validation", "validationPair": {"candidateDeploymentId": "next"}}
        self.assertEqual(100, MySQLReasoningEngineJobStore.event_priority(SimpleNamespace(payload=payload)))
        self.assertEqual("observe", payload["reviewLevel"])
        self.assertTrue(payload["candidateValidationReplay"])
        self.assertEqual(50, MySQLReasoningEngineJobStore.event_priority(SimpleNamespace(payload={**payload,"candidateValidationReplay":False})))
        self.assertEqual(50, MySQLReasoningEngineJobStore.event_priority(SimpleNamespace(payload={**payload,"trigger":"ordinary-market"})))

    def test_single_engine_path_never_waives_correctness_or_fabricates_performance(self):
        registry = Mock()
        registry.control.return_value.candidate_deployment_id = "next"
        platform = ReasoningEnginePlatformService(registry)
        readiness = {"mode": "independent-v2", "ready": False,
                     "blockers": ["candidate-absolute-latency-slo-breached"],
                     "minimumComparisons": 5, "comparisonEvidence": {"sampleCount": 5}}
        original = deepcopy(readiness)
        platform.independent_promotion_readiness = Mock(return_value=readiness)
        platform.activate = Mock(side_effect=RuntimeError("activation reached"))
        for blocker in ("independent-execution-failures", "independent-fact-parity-incomplete",
                        "independent-shadow-delivery-detected", "engine-unhealthy"):
            platform.independent_promotion_readiness.return_value = {**readiness, "blockers": readiness["blockers"] + [blocker]}
            self.assertIn(blocker, platform.consolidate_delivery("next", "owner asked for one version")["blockers"])
        platform.activate.assert_not_called()
        platform.independent_promotion_readiness.return_value = readiness
        with self.assertRaisesRegex(RuntimeError, "activation reached"):
            platform.consolidate_delivery("next", "owner asked for one version")
        activation = platform.activate.call_args.args[1]
        self.assertFalse(activation["ready"])
        self.assertEqual(readiness["blockers"], activation["outstandingPerformanceWarnings"])
        self.assertEqual(original, readiness)
        self.assertIn("explicit-owner-request-required", platform.consolidate_delivery("next", "")["blockers"])
        platform.independent_promotion_readiness.return_value = {**readiness, "comparisonEvidence": {"sampleCount": 4}}
        self.assertIn("insufficient-independent-comparisons", platform.consolidate_delivery("next", "owner")["blockers"])


class RetiredReasoningDataTests(StabilizationDatabaseCase):
    def test_cleanup_preserves_current_lineage_canonical_history_and_shared_graph(self):
        bundle = EngineReleaseBundle("tbox", "rules", "prompt", "features")
        current, old, shared, candidate = [self.deployment + suffix for suffix in (":current", ":old", ":shared", ":candidate")]
        for name, status, graph in ((current, "active", "orbit_shared_test"), (old, "retired", "orbit_old_test"),
                                    (shared, "retired", "orbit_shared_test"), (candidate, "shadow", "orbit_candidate_test")):
            self.registry.upsert(ReasoningEngineDescriptor("fixture", "v2", name, status, graph, "test", bundle))
        self.deployment = current
        control = self.registry.set_control(current, current, candidate)
        store = MySQLRetiredReasoningDataStore(self.settings)
        with self.assertRaisesRegex(RuntimeError, "live native"):
            store.purge(control.version, "owner", apply=True)
        with self.assertRaisesRegex(RuntimeError, "control changed"):
            store.purge(control.version - 1, "owner", apply=True)
        event = self.source(suffix="retirement-live")
        self.events.handle(event)
        jobs = self.jobs.claim(current, "retirement-fixture", 1, 60)
        self.assertEqual(1, len(jobs))
        self.jobs.complete(jobs[0]["jobId"], {"status": "ok", "delivery_authorized": True, "trace_complete": True,
            "projection_results": {"fixture": {"nativeTypeDbReasoningCompleted": True, "generationAligned": True}}},
            worker_id="retirement-fixture", claimed_at=jobs[0]["claimedAt"])
        with self.jobs.connect() as c:
            for name, deployment in (("current", current), ("garbage", old), ("canonical", shared), ("candidate", candidate),
                                     ("learning", old), ("outcome", old), ("queued", old), ("raced", old)):
                c.execute("INSERT INTO investment_subject_decision_cases "
                    "(subject_case_id,batch_case_id,request_id,deployment_id,payload_json,created_at,updated_at) "
                    "VALUES (%s,'batch','request',%s,%s,'2026-11-09','2026-11-09')",
                    (name, deployment, json.dumps({"source": name, "frozen": [-123,456]})))
                c.execute("INSERT INTO decision_candidate_snapshots "
                    "(candidate_set_id,subject_case_id,fingerprint,payload_json,created_at) "
                    "VALUES (%s,%s,%s,%s,'2026-11-09')", (name, name, name, json.dumps({"frozen": name})))
            c.execute("INSERT INTO decision_publications (publication_id,subject_case_id,outcome_kind,publication_fingerprint,payload_json,created_at) "
                      "VALUES ('canonical','canonical','PUBLISHED','canonical','{}','2026-11-09')")
            c.execute("INSERT INTO investment_hypothesis_observation_episodes (episode_id,candidate_set_id,payload_json,created_at,updated_at) "
                      "VALUES ('learning','learning','{}','2026-11-09','2026-11-09')")
            c.execute("UPDATE investment_subject_decision_cases SET inference_generation_id='verified-generation' WHERE subject_case_id='outcome'")
            c.execute("UPDATE investment_subject_decision_cases SET ai_request_id='queued-request' WHERE subject_case_id='queued'")
            c.execute("INSERT INTO investment_decision_episodes (episode_id,inference_generation_id,decided_at,payload_json,created_at,updated_at) "
                      "VALUES ('outcome','verified-generation','2026-11-09','{}','2026-11-09','2026-11-09')")
            frozen = c.execute("SELECT * FROM investment_subject_decision_cases WHERE subject_case_id<>'garbage' ORDER BY subject_case_id").fetchall()
            lineage = c.execute("SELECT * FROM reasoning_engine_job_sources WHERE deployment_id=%s", (current,)).fetchall()
        preview = store.purge(control.version, "owner")
        self.assertEqual(2, preview["tables"]["investment_subject_decision_cases"])
        deleted = 0
        execute = MySQLConnectionProxy.execute
        raced = []
        def publish_between_discovery_and_lock(connection, sql, params=None):
            cursor = execute(connection, sql, params)
            if sql.startswith("SELECT target.") and "FROM `decision_candidate_snapshots` target" in sql and "FOR UPDATE" not in sql:
                rows = cursor.fetchall()
                if not raced and any(row["candidate_set_id"] == "raced" for row in rows):
                    with self.jobs.connect() as other:
                        other.execute("INSERT INTO decision_publications (publication_id,subject_case_id,outcome_kind,publication_fingerprint,payload_json,created_at) "
                                      "VALUES ('raced','raced','PUBLISHED','raced','{}','2026-11-09')")
                    raced.append(True)
                return SimpleNamespace(fetchall=lambda: rows)
            return cursor
        with patch.object(MySQLConnectionProxy, "execute", publish_between_discovery_and_lock):
            for _ in range(30):
                result = store.purge(control.version, "owner", apply=True, limit=1)
                self.assertLessEqual(result["deletedRows"], 1)
                deleted += result["deletedRows"]
                if result["status"] == "complete": break
        self.assertTrue(raced)
        self.assertEqual("complete", result["status"])
        self.assertEqual(2, deleted)
        self.assertEqual("empty", store.purge(control.version, "owner", apply=True)["status"])
        with self.jobs.connect() as c:
            self.assertEqual(frozen, c.execute("SELECT * FROM investment_subject_decision_cases ORDER BY subject_case_id").fetchall())
            self.assertEqual(lineage, c.execute("SELECT * FROM reasoning_engine_job_sources WHERE deployment_id=%s", (current,)).fetchall())
            self.assertEqual(0, c.execute("SELECT COUNT(*) n FROM notification_jobs").fetchone()["n"])
        self.assertFalse(self.jobs.ingress_event(event)["saved"])
        graphs = {"orbit_shared_test", "orbit_candidate_test", "orbit_old_test"}
        removed = store.prune_graphs(control.version, "owner", lambda name: name in graphs, graphs.remove)
        self.assertEqual(["orbit_old_test"], removed["removedDatabases"])
        self.assertEqual({"orbit_shared_test", "orbit_candidate_test"}, graphs)


class ConsolidationStateTests(StabilizationDatabaseCase):
    def test_consolidation_clears_previous_worker_and_does_not_resurrect_legacy(self):
        old = self.deployment
        candidate = old + ":next"
        bundle = EngineReleaseBundle("tbox", "rules", "prompt", "features")
        self.registry.upsert(ReasoningEngineDescriptor("fixture", "v2", candidate, "shadow", "orbit_next", "test", bundle))
        self.registry.set_control(old, old, candidate)
        self.events.handle(self.source(suffix="old-pending"))
        platform = ReasoningEnginePlatformService(self.registry, self.settings, independent_job_store=self.jobs)
        readiness = {"mode": "independent-v2", "ready": False,
                     "blockers": ["candidate-queue-wait-slo-breached"], "minimumComparisons": 5,
                     "comparisonEvidence": {"sampleCount": 5}}
        platform.independent_promotion_readiness = Mock(return_value=readiness)
        result = platform.consolidate_delivery(candidate, "owner wants one running version")
        self.assertEqual("consolidated", result["status"])
        control = self.registry.control()
        self.assertEqual(candidate, control.active_deployment_id)
        self.assertEqual(candidate, control.delivery_deployment_id)
        self.assertEqual("", control.candidate_deployment_id)
        self.assertEqual("retired", self.registry.get(old)["status"])
        self.assertFalse(self.registry.get(candidate)["health"]["ownerConsolidation"]["promotionPerformancePassed"])
        rows = self.sql("SELECT job_status FROM reasoning_engine_jobs WHERE deployment_id=%s", (old,), all_rows=True)
        self.assertTrue(rows)
        self.assertEqual({"superseded"}, {row["job_status"] for row in rows})
        legacy = ReasoningEngineDescriptor("fixture", "v1", "legacy-default", "candidate", "old-store", "test", bundle)
        platform.descriptors = Mock(return_value=[legacy])
        platform.initialize(include_diagnostics=False)
        self.assertFalse(self.registry.get("legacy-default"))
