import json
import unittest
from datetime import datetime, timezone

from digital_twin.application.mysql_minimal_retention_service import MySQLMinimalRetentionService
from digital_twin.domain.mysql_minimal_retention import mysql_minimal_retention_policy
from digital_twin.infrastructure.mysql_minimal_retention import MySQLMinimalRetentionRepository
from digital_twin.infrastructure.mysql_retention import (
    MYSQL_OPERATIONAL_HISTORY_RETENTION_TARGETS,
    apply_mysql_operational_history_retention,
)


class Cursor:
    def __init__(self, rowcount=0, one=None, rows=None):
        self.rowcount = rowcount
        self._one = one
        self._rows = list(rows or [])

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._rows)


class RepositorySpy:
    def __init__(self):
        self.preview_calls = []
        self.apply_calls = []
        self.recorded = []

    def preview(self, policy, now=None):
        self.preview_calls.append((policy, now))
        return {"eligibleRows": 2, "eligibleBytes": 128, "policies": {}}

    def apply(self, policy, now=None):
        self.apply_calls.append((policy, now))
        return {
            "status": "ok",
            "deleted": 2,
            "compacted": 1,
            "estimatedBytes": 128,
            "tables": {"ontology_world_projection_outbox": 2},
            "policies": {"worldProjection:completed": 2},
        }

    def record_run(self, result, now=None):
        self.recorded.append((dict(result), now))


class ApplyConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        rendered = str(sql)
        values = tuple(params or ())
        self.calls.append((rendered, values))
        if "SELECT GET_LOCK" in rendered:
            return Cursor(one={"acquired": 1})
        if "SELECT RELEASE_LOCK" in rendered:
            return Cursor(one={"released": 1})
        if "SELECT job_id" in rendered and "ontology_world_projection_outbox" in rendered:
            if values[:2] == ("completed", "superseded"):
                return Cursor(rows=[{"job_id": "completed-world", "payload_bytes": 512}])
        if rendered.lstrip().upper().startswith(("DELETE", "UPDATE")):
            return Cursor(rowcount=1)
        return Cursor()


class FailedPayloadConnection(ApplyConnection):
    def execute(self, sql, params=()):
        rendered = str(sql)
        values = tuple(params or ())
        self.calls.append((rendered, values))
        if "SELECT GET_LOCK" in rendered:
            return Cursor(one={"acquired": 1})
        if "SELECT RELEASE_LOCK" in rendered:
            return Cursor(one={"released": 1})
        if (
            "SELECT job_id" in rendered
            and "ontology_world_projection_outbox" in rendered
            and "status = 'failed'" in rendered
            and "payload_json <> '{}'" in rendered
        ):
            return Cursor(rows=[{"job_id": "failed-world", "payload_bytes": 2048}])
        if rendered.lstrip().upper().startswith(("DELETE", "UPDATE")):
            return Cursor(rowcount=1)
        return Cursor()


class AuditConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((str(sql), tuple(params or ())))
        return Cursor(rowcount=1)


class LifecycleArchiveConnection:
    def __init__(self, archive_fails=False):
        self.calls = []
        self.archive_fails = archive_fails
        self.archived = False

    def execute(self, sql, params=()):
        rendered = str(sql)
        values = tuple(params or ())
        self.calls.append((rendered, values))
        if "SELECT GET_LOCK" in rendered:
            return Cursor(one={"acquired": 1})
        if "SELECT RELEASE_LOCK" in rendered:
            return Cursor(one={"released": 1})
        if (
            "SELECT transition_id, lifecycle_key" in rendered
            and "investment_hypothesis_lifecycle_events" in rendered
        ):
            return Cursor(rows=[{
                "transition_id": "transition-1",
                "lifecycle_key": "hypothesis:MSTR:hold",
                "lifecycle_id": "lifecycle-1",
                "scope": "position",
                "account_id": "account-1",
                "market_id": "US",
                "symbol": "MSTR",
                "previous_state": "observed",
                "current_state": "strengthened",
                "inference_generation_id": "generation-2",
                "previous_generation_id": "generation-1",
                "occurred_at": "2026-07-29T10:00:00Z",
                "material_change": 1,
                "payload_bytes": 8192,
            }])
        if (
            "INSERT IGNORE INTO `investment_hypothesis_transition_history`" in rendered
            and "VALUES (" in rendered
        ):
            if self.archive_fails:
                raise RuntimeError("archive unavailable")
            self.archived = True
            return Cursor(rowcount=1)
        if rendered.lstrip().startswith("DELETE FROM `investment_hypothesis_lifecycle_events`"):
            return Cursor(rowcount=1 if self.archived else 0)
        return Cursor()


class LifecycleBaselineConnection(LifecycleArchiveConnection):
    def execute(self, sql, params=()):
        rendered = str(sql)
        if (
            "INSERT IGNORE INTO `investment_hypothesis_transition_history`" in rendered
            and "FROM `investment_hypothesis_lifecycle_states` state" in rendered
        ):
            self.calls.append((rendered, tuple(params or ())))
            return Cursor(rowcount=2)
        if (
            "SELECT transition_id, lifecycle_key" in rendered
            and "investment_hypothesis_lifecycle_events" in rendered
        ):
            self.calls.append((rendered, tuple(params or ())))
            return Cursor()
        return super().execute(sql, params)


class MySQLMinimalRetentionTests(unittest.TestCase):
    def test_minimal_policy_is_opt_in_without_runtime_defaults_and_bounds_inputs(self):
        self.assertFalse(mysql_minimal_retention_policy({}).enabled)

        policy = mysql_minimal_retention_policy({
            "mysqlMinimalRetentionEnabled": "yes",
            "mysqlMinimalRetentionMode": "apply",
            "mysqlMinimalRetentionBatchSize": "1000",
            "mysqlMinimalRetentionMaxDeleteBytes": "1",
            "mysqlMinimalRetentionAuditKeepCount": "1",
        })

        self.assertTrue(policy.enabled)
        self.assertEqual("apply", policy.mode)
        self.assertEqual(100, policy.batch_size)
        self.assertEqual(256 * 1024, policy.max_delete_bytes)
        self.assertEqual(30, policy.max_run_seconds)
        self.assertEqual(10, policy.audit_keep_count)
        self.assertEqual(24, policy.failed_world_projection_payload_retention_hours)
        self.assertEqual(24 * 7, policy.failed_world_projection_retention_hours)

        accelerated = mysql_minimal_retention_policy({
            "mysqlMinimalRetentionEnabled": "1",
            "_effectiveMysqlMinimalRetentionBatchSize": "1000",
            "_effectiveMysqlMinimalRetentionMaxDeleteBytes": str(256 * 1024 * 1024),
            "_effectiveMysqlMinimalRetentionMaxRunSeconds": "60",
        })
        self.assertEqual(1000, accelerated.batch_size)
        self.assertEqual(256 * 1024 * 1024, accelerated.max_delete_bytes)
        self.assertEqual(60, accelerated.max_run_seconds)

    def test_disabled_profile_does_not_query_or_record_anything(self):
        repository = RepositorySpy()

        result = MySQLMinimalRetentionService(repository, {}).run_once()

        self.assertEqual("disabled", result["status"])
        self.assertEqual([], repository.preview_calls)
        self.assertEqual([], repository.apply_calls)
        self.assertEqual([], repository.recorded)

    def test_cli_style_preview_overrides_an_enabled_apply_policy(self):
        repository = RepositorySpy()
        service = MySQLMinimalRetentionService(repository, {
            "mysqlMinimalRetentionEnabled": "1",
            "mysqlMinimalRetentionMode": "apply",
        })

        result = service.run_once(force=True, preview=True)

        self.assertEqual("preview", result["status"])
        self.assertEqual("preview", result["mode"])
        self.assertEqual(1, len(repository.preview_calls))
        self.assertEqual([], repository.apply_calls)
        self.assertEqual(1, len(repository.recorded))

    def test_explicit_apply_can_include_a_preview_and_records_only_aggregate_result(self):
        repository = RepositorySpy()
        service = MySQLMinimalRetentionService(repository, {
            "mysqlMinimalRetentionEnabled": "1",
            "mysqlMinimalRetentionMode": "preview",
        })

        result = service.run_once(apply=True, preview_before_apply=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual(2, result["deleted"])
        self.assertEqual(1, result["compacted"])
        self.assertEqual(1, len(repository.preview_calls))
        self.assertEqual(1, len(repository.apply_calls))
        self.assertEqual(1, len(repository.recorded))

    def test_background_apply_skips_expensive_full_preview(self):
        repository = RepositorySpy()
        service = MySQLMinimalRetentionService(repository, {
            "mysqlMinimalRetentionEnabled": "1",
            "mysqlMinimalRetentionMode": "apply",
        })

        result = service.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual({}, result["preview"])
        self.assertEqual([], repository.preview_calls)
        self.assertEqual(1, len(repository.apply_calls))

    def test_repository_deletes_only_terminal_projection_primary_keys(self):
        connection = ApplyConnection()
        repository = MySQLMinimalRetentionRepository(connection)
        policy = mysql_minimal_retention_policy({
            "mysqlMinimalRetentionEnabled": "1",
            "mysqlMinimalRetentionMode": "apply",
            "mysqlMinimalRetentionBatchSize": "2",
            "mysqlMinimalRetentionMaxDeleteBytes": "1048576",
        })

        result = repository.apply(policy, now=datetime(2026, 7, 30, tzinfo=timezone.utc))

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["deleted"])
        projection_deletes = [
            (sql, params)
            for sql, params in connection.calls
            if sql.startswith("DELETE FROM `ontology_world_projection_outbox`")
        ]
        self.assertEqual(1, len(projection_deletes))
        self.assertTrue(any(params[0] == "completed-world" for _sql, params in projection_deletes))
        self.assertFalse(any("pending" in params or "processing" in params for _sql, params in projection_deletes))
        self.assertFalse(any("failed" in params for _sql, params in projection_deletes))
        self.assertFalse(any("investment_decision_episodes" in sql for sql, _params in connection.calls))

    def test_repository_compacts_old_failed_projection_payload_without_touching_live_work(self):
        connection = FailedPayloadConnection()
        repository = MySQLMinimalRetentionRepository(connection)
        policy = mysql_minimal_retention_policy({
            "mysqlMinimalRetentionEnabled": "1",
            "mysqlMinimalRetentionMode": "apply",
        })

        result = repository.apply(policy, now=datetime(2026, 7, 30, tzinfo=timezone.utc))

        self.assertEqual(1, result["compacted"])
        updates = [
            (sql, params)
            for sql, params in connection.calls
            if sql.lstrip().startswith("UPDATE `ontology_world_projection_outbox`")
        ]
        self.assertEqual(1, len(updates))
        self.assertIn("SET payload_json = '{}', result_json = '{}'", updates[0][0])
        self.assertIn("status = 'failed'", updates[0][0])
        self.assertEqual("failed-world", updates[0][1][0])
        self.assertFalse(any("pending" in sql or "processing" in sql for sql, _params in updates))

    def test_research_cleanup_requires_completion_and_excludes_live_work_states(self):
        connection = ApplyConnection()
        repository = MySQLMinimalRetentionRepository(connection)
        policy = mysql_minimal_retention_policy({
            "mysqlMinimalRetentionEnabled": "1",
            "mysqlMinimalRetentionMode": "apply",
        })

        repository.apply(policy, now=datetime(2026, 7, 30, tzinfo=timezone.utc))

        research_queries = [
            sql for sql, _params in connection.calls
            if "investment_research_runs" in sql
        ]
        self.assertTrue(research_queries)
        self.assertTrue(any("completed_at <> ''" in sql for sql in research_queries))
        self.assertTrue(any("status NOT IN (%s, %s, %s)" in sql for sql in research_queries))
        self.assertFalse(any("status IN ('completed'" in sql for sql in research_queries))

    def test_lifecycle_payload_is_deleted_only_after_compact_history_is_archived(self):
        connection = LifecycleArchiveConnection()
        repository = MySQLMinimalRetentionRepository(connection)
        policy = mysql_minimal_retention_policy({
            "mysqlMinimalRetentionEnabled": "1",
            "mysqlMinimalRetentionMode": "apply",
        })

        result = repository.apply(policy, now=datetime(2026, 7, 30, tzinfo=timezone.utc))

        self.assertEqual(1, result["archived"])
        self.assertEqual(1, result["deleted"])
        archive_index = next(
            index for index, (sql, _params) in enumerate(connection.calls)
            if "INSERT IGNORE INTO `investment_hypothesis_transition_history`" in sql
        )
        delete_index = next(
            index for index, (sql, _params) in enumerate(connection.calls)
            if sql.lstrip().startswith("DELETE FROM `investment_hypothesis_lifecycle_events`")
        )
        delete_sql, delete_params = connection.calls[delete_index]
        self.assertLess(archive_index, delete_index)
        self.assertIn("EXISTS", delete_sql)
        self.assertIn("investment_hypothesis_transition_history", delete_sql)
        self.assertEqual("transition-1", delete_params[0])
        self.assertEqual("transition-1", delete_params[2])

    def test_lifecycle_payload_is_not_deleted_when_history_archive_fails(self):
        connection = LifecycleArchiveConnection(archive_fails=True)
        repository = MySQLMinimalRetentionRepository(connection)
        policy = mysql_minimal_retention_policy({
            "mysqlMinimalRetentionEnabled": "1",
            "mysqlMinimalRetentionMode": "apply",
        })

        with self.assertRaisesRegex(RuntimeError, "archive unavailable"):
            repository.apply(policy, now=datetime(2026, 7, 30, tzinfo=timezone.utc))

        self.assertFalse(any(
            sql.lstrip().startswith("DELETE FROM `investment_hypothesis_lifecycle_events`")
            for sql, _params in connection.calls
        ))

    def test_current_lifecycle_states_seed_compact_history_baselines(self):
        connection = LifecycleBaselineConnection()
        repository = MySQLMinimalRetentionRepository(connection)
        policy = mysql_minimal_retention_policy({
            "mysqlMinimalRetentionEnabled": "1",
            "mysqlMinimalRetentionMode": "apply",
        })

        result = repository.apply(policy, now=datetime(2026, 7, 30, tzinfo=timezone.utc))

        self.assertEqual(2, result["archived"])
        self.assertEqual(2, result["policies"]["lifecycle:stateBaselines"])
        baseline_sql = next(
            sql for sql, _params in connection.calls
            if "FROM `investment_hypothesis_lifecycle_states` state" in sql
            and "INSERT IGNORE" in sql
        )
        self.assertIn("baseline:", baseline_sql)
        self.assertIn("NOT EXISTS", baseline_sql)

    def test_audit_report_excludes_preview_payloads(self):
        connection = AuditConnection()
        repository = MySQLMinimalRetentionRepository(connection)
        repository.record_run({
            "status": "preview",
            "mode": "preview",
            "profile": "minimal-mysql-retention-v1",
            "preview": {
                "eligibleRows": 1,
                "eligibleBytes": 64,
                "rawPayload": "must-not-be-stored",
                "policies": {
                    "worldProjection": {
                        "candidateRows": 1,
                        "candidateBytes": 64,
                        "rawPayload": "must-not-be-stored",
                    },
                },
            },
        })

        insert = next(params for sql, params in connection.calls if "INSERT INTO `mysql_retention_runs`" in sql)
        report = json.loads(insert[7])
        self.assertNotIn("rawPayload", report["preview"])
        self.assertEqual(1, report["preview"]["eligibleRows"])
        self.assertEqual(64, report["preview"]["policies"]["worldProjection"]["candidateBytes"])

    def test_legacy_history_targets_no_longer_delete_retryable_jobs_by_age(self):
        tables = {target.table for target in MYSQL_OPERATIONAL_HISTORY_RETENTION_TARGETS}

        self.assertNotIn("notification_jobs", tables)
        self.assertNotIn("model_review_jobs", tables)

    def test_legacy_cleanup_limits_job_deletes_to_terminal_statuses(self):
        connection = ApplyConnection()

        apply_mysql_operational_history_retention(
            connection,
            {"operationalHistoryRetentionEnabled": "1"},
            now=datetime(2026, 7, 30, tzinfo=timezone.utc),
            use_lock=False,
        )

        model_review_queries = [sql for sql, _params in connection.calls if "model_review_jobs" in sql]
        notification_queries = [sql for sql, _params in connection.calls if "notification_jobs" in sql]
        self.assertTrue(any("status` = 'done'" in sql for sql in model_review_queries))
        self.assertFalse(any("'failed'" in sql for sql in model_review_queries))
        self.assertTrue(any("status` IN ('done', 'sent')" in sql for sql in notification_queries))
        self.assertFalse(any("WHERE `created_at` <" in sql for sql in notification_queries))


if __name__ == "__main__":
    unittest.main()
