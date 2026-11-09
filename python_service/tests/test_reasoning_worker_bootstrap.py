"""Historical diagnostics must never become the worker's startup dependency."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from digital_twin.modules.reasoning.application.reasoning_engine_platform import ReasoningEnginePlatformService
from digital_twin.modules.reasoning.application.independent_reasoning_engine import IndependentReasoningJobRunner
from digital_twin.modules.reasoning.domain.reasoning_engine_versions import EngineControlState
from stabilization_database import StabilizationDatabaseCase


class ReasoningWorkerBootstrapTests(unittest.TestCase):
    def test_worker_reconciles_control_without_reading_historical_diagnostics(self):
        registry = Mock()
        registry.control.return_value = EngineControlState("active", "active", "", 2)
        registry.list.return_value = [{"deploymentId": "active", "engineVersion": "v2"}]
        registry.retire_unselected.return_value = {"status": "unchanged"}
        platform = ReasoningEnginePlatformService(registry, {})
        platform.descriptors = Mock(return_value=[])
        platform.synchronize_control_capabilities = Mock(return_value={"status": "synchronized"})
        platform.independent_job_store = Mock()
        platform.shadow_queue = Mock()
        platform.comparison_store = Mock()
        for store in (platform.independent_job_store, platform.shadow_queue, platform.comparison_store):
            store.summary.side_effect = TimeoutError("historical metrics unavailable")
        result = platform.initialize(include_diagnostics=False)
        self.assertEqual("active", result["control"]["delivery_deployment_id"])
        self.assertEqual("not-requested", result["diagnosticsStatus"])
        platform.synchronize_control_capabilities.assert_called_once()
        registry.retire_unselected.assert_called_once()
        for store in (platform.independent_job_store, platform.shadow_queue, platform.comparison_store):
            store.summary.assert_not_called()

    def test_live_summary_uses_pending_state_but_candidate_evidence_keeps_full_history(self):
        class Queue:
            def runtime_summary(self, deployment_id):
                return {"deploymentId": deployment_id, "pendingCount": 2, "historyIncluded": False}
            def summary(self, deployment_id, release_fingerprint="", validation_cohort_id=""):
                return {"successfulRunCount": 4, "release": release_fingerprint, "cohort": validation_cohort_id}
        engine = Mock()
        engine.release_identity.return_value = {"releaseFingerprint": "release:exact", "validationCohortId": "cohort:exact"}
        runner = IndependentReasoningJobRunner(Queue(), engine, Mock())
        self.assertEqual(2, runner.queue_summary("active")["pendingCount"])
        self.assertEqual({"successfulRunCount": 4, "release": "release:exact", "cohort": "cohort:exact"},
                         runner.queue_summary("candidate", include_history=True))

    def test_diagnostic_failure_is_unknown_not_empty_or_a_job_retry(self):
        class Queue:
            def runtime_summary(self, deployment_id):
                raise TimeoutError("telemetry timed out")
            def summary(self, deployment_id):
                raise AssertionError("must not retry through the expensive history")
        runner = IndependentReasoningJobRunner(Queue(), Mock(), Mock())
        result = runner.queue_summary("active")
        self.assertEqual("unavailable", result["status"])
        self.assertNotIn("pendingCount", result)
        self.assertNotIn("successfulRunCount", result)

    def test_cli_watch_uses_execution_only_initialization(self):
        from digital_twin.infrastructure import cli, reasoning_engine_factory, service_factory
        platform = Mock()
        args = SimpleNamespace(reasoning_engine_action="v2-watch", worker_id="", role="delivery", deployment_id="")
        with patch.object(cli, "runtime_settings", return_value={}), \
             patch.object(reasoning_engine_factory, "build_reasoning_engine_platform", return_value=platform), \
             patch.object(service_factory, "build_v2_reasoning_job_runner"), \
             patch.object(cli, "watch_v2_reasoning_engine", return_value=0):
            self.assertEqual(0, cli.reasoning_engine_platform_command(args))
        platform.initialize.assert_called_once_with(include_diagnostics=False)

    def test_release_launch_survives_unavailable_historical_diagnostics(self):
        from digital_twin.infrastructure import reasoning_engine_factory
        from digital_twin.infrastructure.composition.reasoning_launch import prepare_v2_launch

        platform = Mock()

        def initialize(*, include_diagnostics=True):
            if include_diagnostics:
                raise TimeoutError("terminal history cannot be read during worker startup")
            return {"diagnosticsStatus": "not-requested"}

        platform.initialize.side_effect = initialize
        platform.deployment_descriptor.return_value = SimpleNamespace(
            deployment_id="frozen-candidate", engine_version="v2", status="shadow",
            time_series_backend_id="frozen-series",
            release_bundle=SimpleNamespace(feature_set_version="frozen-features"),
        )
        platform.registry.get.return_value = {"health": {"releaseFingerprint": "frozen-release"}}
        platform.graph_database_for.return_value = "frozen-graph"
        settings = {"typedbDatabase": "other-graph", "reasoningEngineV2DeploymentId": "other-deployment"}
        with patch.object(reasoning_engine_factory, "build_reasoning_engine_platform", return_value=platform):
            launch = prepare_v2_launch(settings, "frozen-candidate")
        platform.initialize.assert_called_once_with(include_diagnostics=False)
        platform.deployment_descriptor.assert_called_once_with("frozen-candidate")
        self.assertEqual("frozen-release", launch.deployment_health["releaseFingerprint"])
        self.assertEqual("frozen-graph", launch.candidate_settings["typedbDatabase"])
        self.assertEqual("frozen-series", launch.candidate_settings["timeSeriesActiveBackendId"])
        self.assertEqual("frozen-features", launch.candidate_settings["_reasoningFeatureSetVersion"])
        self.assertEqual("other-graph", settings["typedbDatabase"])


class ReasoningRuntimeSummaryTests(StabilizationDatabaseCase):
    def test_runtime_summary_reads_pending_lane_and_source_age_without_terminal_payloads(self):
        self.events.handle(self.source(suffix="runtime-summary"))
        value = self.jobs.runtime_summary(self.deployment)
        self.assertGreater(value["pendingCount"], 0)
        self.assertFalse(value["historyIncluded"])
        self.assertIsNotNone(value["oldestPendingAgeSeconds"])
        self.assertEqual(sum(value["pendingByLane"].values()), value["pendingCount"])
        self.assertNotIn("successfulRunCount", value)
        self.assertNotIn("durationP95Ms", value)
        self.assertEqual(0, self.jobs.runtime_summary("other-deployment")["pendingCount"])
        with self.assertRaises(ValueError):
            self.jobs.runtime_summary("")


class SubjectCaseReadIndexTests(StabilizationDatabaseCase):
    def test_index_install_preserves_frozen_cases_scope_order_and_source_lineage(self):
        import json
        from digital_twin.infrastructure.mysql_schema_tuning import MYSQL_OPERATIONAL_INDEXES, ensure_mysql_indexes

        names = {"idx_subject_decision_account_latest", "idx_subject_decision_stage_updated",
                 "idx_subject_decision_deployment_latest"}
        definitions = [d for d in MYSQL_OPERATIONAL_INDEXES["investment_subject_decision_cases"] if d.name in names]
        self.assertEqual(3, len(definitions))
        prefix = self.deployment
        account_a, account_b = prefix + ":a", prefix + ":b"
        cases = [
            ("old", account_a, "deployment-a", "ARCHIVED", "2026-11-07T00:00:00Z", "2026-11-09T06:10:00Z"),
            ("tie-1", account_a, "deployment-a", "ARCHIVED", "2026-11-09T00:00:00Z", "2026-11-09T06:00:00Z"),
            ("tie-2", account_a, "deployment-a", "READY", "2026-11-09T00:00:00Z", "2026-11-09T06:00:00Z"),
            ("other-account", account_b, "deployment-b", "READY", "2026-11-09T00:00:00Z", "2026-11-09T06:05:00Z"),
            ("other-deployment", account_a, "deployment-b", "READY", "2026-11-09T00:00:00Z", "2026-11-09T05:50:00Z"),
        ]
        with self.jobs.connect() as connection:
            for suffix, account, deployment, stage, created, updated in cases:
                payload = json.dumps({"sourceAt": created, "nativeGenerationId": "frozen:" + suffix,
                                      "facts": {"foreignNetVolume": -123, "institutionNetVolume": 456}})
                connection.execute(
                    "INSERT INTO investment_subject_decision_cases "
                    "(subject_case_id,batch_case_id,request_id,deployment_id,account_id,symbol,stage,"
                    "payload_json,created_at,updated_at,source_abox_snapshot_id,inference_generation_id) "
                    "VALUES (%s,%s,%s,%s,%s,'TEST',%s,%s,%s,%s,%s,%s)",
                    (prefix + ":" + suffix, prefix, prefix, deployment, account, stage,
                     payload, created, updated, "source:" + suffix, "generation:" + suffix),
                )
            for definition in definitions:
                connection.execute("ALTER TABLE investment_subject_decision_cases DROP INDEX `" + definition.name + "`")
            queries = [
                ("SELECT subject_case_id,payload_json FROM investment_subject_decision_cases WHERE account_id=%s "
                 "ORDER BY updated_at DESC,subject_case_id DESC LIMIT 3", (account_a,)),
                ("SELECT subject_case_id,payload_json FROM investment_subject_decision_cases WHERE deployment_id=%s "
                 "AND created_at >= %s ORDER BY updated_at DESC,subject_case_id DESC LIMIT 3",
                 ("deployment-a", "2026-11-08T00:00:00Z")),
                ("SELECT subject_case_id,payload_json FROM investment_subject_decision_cases WHERE stage='READY' "
                 "AND updated_at < %s ORDER BY updated_at ASC LIMIT 100", ("2026-11-09T05:55:00Z",)),
            ]
            before = [connection.execute(sql, params).fetchall() for sql, params in queries]
            frozen_before = connection.execute("SELECT * FROM investment_subject_decision_cases ORDER BY subject_case_id").fetchall()
            self.assertEqual(names, set(ensure_mysql_indexes(connection, {"investment_subject_decision_cases": definitions})))
            self.assertEqual([], ensure_mysql_indexes(connection, {"investment_subject_decision_cases": definitions}))
            self.assertEqual(frozen_before, connection.execute("SELECT * FROM investment_subject_decision_cases ORDER BY subject_case_id").fetchall())
            self.assertEqual(before, [connection.execute(sql, params).fetchall() for sql, params in queries])
            self.assertEqual([prefix + ":old", prefix + ":tie-2", prefix + ":tie-1"], [r["subject_case_id"] for r in before[0]])
            self.assertEqual([prefix + ":tie-2", prefix + ":tie-1"], [r["subject_case_id"] for r in before[1]])
            self.assertEqual([prefix + ":other-deployment"], [r["subject_case_id"] for r in before[2]])
            for row in connection.execute("SHOW INDEX FROM investment_subject_decision_cases").fetchall():
                if row["Key_name"] in names:
                    self.assertEqual(1, row["Non_unique"])
