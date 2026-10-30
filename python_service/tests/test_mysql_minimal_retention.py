import json
import time
import unittest
from datetime import datetime, timezone

from digital_twin.platform.application.mysql_minimal_retention_service import MySQLMinimalRetentionService
from digital_twin.platform.domain.mysql_minimal_retention import mysql_minimal_retention_policy
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

    def compact_delivered_payloads(self, policy, now=None):
        return {
            "status": "ok",
            "deleted": 0,
            "compacted": 3,
            "estimatedBytes": 4096,
            "tables": {"time_series_projection_outbox": 3},
            "policies": {"timeSeriesProjection:deliveredPayload": 3},
        }


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


class DeliveredPayloadConnection(ApplyConnection):
    def execute(self, sql, params=()):
        rendered = str(sql)
        values = tuple(params or ())
        self.calls.append((rendered, values))
        if "ROW_NUMBER() OVER" in rendered and "ontology_world_projection_outbox" in rendered:
            return Cursor(rows=[{"job_id": "world-obsolete", "payload_bytes": 8192}])
        if "FROM `time_series_projection_outbox`" in rendered and "payload_json <> '{}'" in rendered:
            return Cursor(rows=[{"job_id": "ts-completed", "payload_bytes": 4096}])
        if rendered.lstrip().startswith("UPDATE `ontology_world_projection_outbox`"):
            return Cursor(rowcount=1)
        if rendered.lstrip().startswith("UPDATE `time_series_projection_outbox`"):
            return Cursor(rowcount=1)
        return super().execute(sql, params)


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
    def _assert_research_audit_expiry_is_rechecked_at_preview_selection_and_delete(self):
        class ResearchConnection(ApplyConnection):
            def execute(self, sql, params=()):
                if "SELECT run_id" in str(sql) and "investment_research_runs" in str(sql):
                    self.calls.append((str(sql), tuple(params)))
                    return Cursor(rows=[{"run_id": "expired-audit", "updated_at": "2026-09-01T00:00:00Z", "payload_bytes": 128}])
                return super().execute(sql, params)

        connection = ResearchConnection()
        repository = MySQLMinimalRetentionRepository(connection)
        policy = mysql_minimal_retention_policy({"mysqlMinimalRetentionEnabled": "1", "mysqlMinimalRetentionBatchSize": "2"})
        now = datetime(2026, 9, 30, tzinfo=timezone.utc)
        repository.preview(policy, now=now)
        result = repository.apply(policy, now=now)
        self.assertEqual("ok", result["status"])
        queries = [(sql, params) for sql, params in connection.calls if "investment_research_runs" in sql]
        self.assertEqual(4, len(queries))
        for sql, params in queries:
            if "SELECT run_id, updated_at" in sql:
                self.assertNotIn("payload_json", sql)
                self.assertLessEqual(params[-1], 8)
            else:
                self.assertIn("$.auditRetainUntil", sql)
                self.assertIn("status NOT IN", sql)
            self.assertIn("2026-09-23T00:00:00Z", params)
            self.assertEqual(sql.count("%s"), len(params))

    def _assert_research_scan_resumes_without_losing_protected_or_unprocessed_rows(self):
        import os
        from dataclasses import replace
        from unittest.mock import patch
        from pymysql.cursors import DictCursor
        from pymysql.err import OperationalError
        from mysql_fixtures import mysql_test_connection
        from digital_twin.infrastructure.mysql_operational_connection import MYSQL_SCHEMA, MySQLConnectionProxy

        with patch.dict(os.environ, {"MYSQL_TEST_DATABASE": "orbit_alpha_test_research_retention"}):
            raw = mysql_test_connection()
        raw.cursorclass = DictCursor
        raw.autocommit(True)
        connection = MySQLConnectionProxy(raw)
        self.addCleanup(connection.close)
        for table in ("mysql_retention_scan_cursors", "investment_research_runs"):
            connection.execute("DROP TABLE IF EXISTS " + table)
            connection.execute(next(sql for sql in MYSQL_SCHEMA if "CREATE TABLE IF NOT EXISTS " + table + " (" in sql))
        policy = replace(mysql_minimal_retention_policy({}), batch_size=100)
        cutoff = "2026-10-01T00:00:00Z"
        old = "2026-01-01T00:00:00Z"
        def budget(**overrides):
            return {"started": time.monotonic(), "maxSeconds": 30,
                    "remainingBytes": 1000000, "deletedBytes": 0, **overrides}
        def insert(key, status="completed", audit="", completed=old, updated=old):
            connection.execute("INSERT INTO investment_research_runs "
                "(run_id,status,started_at,completed_at,payload_json,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (key, status, old, completed, json.dumps({"auditRetainUntil": audit, "body": "x" * 128}), old, updated))
        def remaining():
            return {row["run_id"] for row in connection.execute("SELECT run_id FROM investment_research_runs").fetchall()}
        def position():
            return connection.execute("SELECT after_time,after_id FROM mysql_retention_scan_cursors "
                "WHERE profile=%s AND policy_name='research:terminalRuns'", (policy.profile,)).fetchone()
        def reset():
            connection.execute("DELETE FROM investment_research_runs")
            connection.execute("DELETE FROM mysql_retention_scan_cursors")
        execute = connection.execute
        reads = []
        def trace(sql, params=()):
            if sql.startswith("SELECT") and "investment_research_runs" in sql:
                reads.append((sql, params))
            return execute(sql, params)
        def turn(turn_budget=None):
            # A fresh repository models the isolated maintenance worker.
            with patch.object(connection, "execute", side_effect=trace):
                return MySQLMinimalRetentionRepository(connection, candidate_query_timeout_ms=1500)._delete_terminal_research_runs(
                    policy, turn_budget if turn_budget is not None else budget(), cutoff)

        with self.subTest(case="protected prefix and same-time keyset survive worker replacement"):
            protected = {"audit-" + str(i).zfill(2) for i in range(12)}
            for key in protected:
                insert(key, audit="2027-01-01T00:00:00Z")
            for status in ("queued", "processing", "reasoning-queued"):
                insert(status, status=status)
            insert("unfinished", completed="")
            insert("recent", updated=cutoff)
            for i in range(5):
                insert("zz-expired-" + str(i))
            self.assertEqual(0, turn()["deleted"])
            self.assertEqual("audit-07", position()["after_id"])
            self.assertEqual(5, sum(turn()["deleted"] for _ in range(6)))
            self.assertEqual(protected | {"queued", "processing", "reasoning-queued", "unfinished", "recent"}, remaining())
            for sql, params in reads:
                if "payload_json" in sql:
                    self.assertIn("WHERE run_id IN (", sql)
                    self.assertLessEqual(len(params) - 5, 8)
                else:
                    self.assertEqual(8, params[-1])
            for metadata_sql, metadata_params in (reads[0], reads[2]):
                self.assertNotIn("CAST(", metadata_sql)
                plan = str(execute("EXPLAIN " + metadata_sql, metadata_params).fetchall())
                self.assertIn("idx_research_runs_retention", plan)
                self.assertNotIn("filesort", plan.lower())
                self.assertNotIn("Table scan on investment_research_runs", plan)
            # Previously protected rows must be reconsidered after wrap.
            for key in protected:
                execute("UPDATE investment_research_runs SET payload_json='{}' WHERE run_id=%s", (key,))
            self.assertEqual(12, sum(turn()["deleted"] for _ in range(6)))

        with self.subTest(case="delete rechecks audit horizon, status, completion and updated clock"):
            reset()
            for key in ("audit", "active", "unfinished", "renewed", "expired"):
                insert(key)
            raced = False
            def race(sql, params=()):
                nonlocal raced
                if sql.startswith("DELETE FROM `investment_research_runs`") and not raced:
                    raced = True
                    execute("UPDATE investment_research_runs SET payload_json=%s WHERE run_id='audit'", ('{"auditRetainUntil":"2027-01-01T00:00:00Z"}',))
                    execute("UPDATE investment_research_runs SET status='processing' WHERE run_id='active'")
                    execute("UPDATE investment_research_runs SET completed_at='' WHERE run_id='unfinished'")
                    execute("UPDATE investment_research_runs SET updated_at=%s WHERE run_id='renewed'", (cutoff,))
                return execute(sql, params)
            with patch.object(connection, "execute", side_effect=race):
                result = MySQLMinimalRetentionRepository(connection)._delete_terminal_research_runs(policy, budget(), cutoff)
            self.assertEqual(1, result["deleted"])
            self.assertEqual({"audit", "active", "unfinished", "renewed"}, remaining())

        with self.subTest(case="byte budget leaves the cursor before unprocessed candidates"):
            reset()
            insert("expired")
            self.assertEqual(0, turn(budget(remainingBytes=1, deletedBytes=20))["deleted"])
            self.assertIsNone(position())
            self.assertEqual({"expired"}, remaining())
            self.assertEqual(1, turn()["deleted"])

        for failure in ("read-deadline", "partial-delete", "time-budget"):
            with self.subTest(case=failure):
                reset()
                insert("expired-1")
                insert("expired-2")
                turn_budget = budget()
                count = 0
                def fail(sql, params=()):
                    nonlocal count
                    if sql.startswith("SELECT") and "OCTET_LENGTH(payload_json)" in sql:
                        if failure == "read-deadline":
                            raise OperationalError(3024, "deadline")
                        if failure == "time-budget":
                            turn_budget["started"] -= 100
                    if sql.startswith("DELETE FROM `investment_research_runs`"):
                        count += 1
                        if failure == "partial-delete" and count == 2:
                            raise OperationalError(1213, "deadlock")
                    return execute(sql, params)
                with patch.object(connection, "execute", side_effect=fail):
                    repository = MySQLMinimalRetentionRepository(connection, candidate_query_timeout_ms=1500)
                    if failure == "time-budget":
                        self.assertEqual(0, repository._delete_terminal_research_runs(policy, turn_budget, cutoff)["deleted"])
                    else:
                        with self.assertRaises(OperationalError):
                            repository._delete_terminal_research_runs(policy, turn_budget, cutoff)
                self.assertIsNone(position())
                expected = 1 if failure == "partial-delete" else 2
                self.assertEqual(expected, len(remaining()))
                self.assertEqual(expected, turn()["deleted"])

    def test_snapshot_history_preserves_live_sources_and_rechecks_before_delete(self):
        from mysql_fixtures import mysql_test_connection
        from digital_twin.infrastructure.mysql_operational_connection import MySQLConnectionProxy
        from pymysql.cursors import DictCursor
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {'MYSQL_TEST_DATABASE': 'orbit_alpha_test_retention_guard'}):
            raw = mysql_test_connection()
        raw.cursorclass = DictCursor
        connection = MySQLConnectionProxy(raw)
        tables = ('monitor_snapshot_history', 'verified_reasoning_source_snapshots',
                  'reasoning_engine_jobs', 'ontology_reasoning_mailbox_events')
        try:
            for table in tables:
                connection.execute('DROP TABLE IF EXISTS `' + table + '`')
            connection.execute("CREATE TABLE monitor_snapshot_history (account_id VARCHAR(40), generated_at VARCHAR(40), payload_json TEXT, projection_payload_json TEXT, PRIMARY KEY(account_id,generated_at))")
            connection.execute("CREATE TABLE verified_reasoning_source_snapshots (snapshot_id VARCHAR(40) PRIMARY KEY, account_id VARCHAR(40), generated_at VARCHAR(40))")
            connection.execute("CREATE TABLE reasoning_engine_jobs (job_id VARCHAR(40) PRIMARY KEY, source_snapshot_id VARCHAR(40), job_status VARCHAR(40))")
            connection.execute("CREATE TABLE ontology_reasoning_mailbox_events (event_id VARCHAR(40) PRIMARY KEY, source_snapshot_id VARCHAR(40), state VARCHAR(40))")
            for index in range(1, 7):
                clock = '2026-09-01T00:0' + str(index) + ':00Z'
                connection.execute("INSERT INTO monitor_snapshot_history VALUES ('acct',%s,'{}','{}')", (clock,))
                connection.execute("INSERT INTO verified_reasoning_source_snapshots VALUES (%s,'acct',%s)", (str(index), clock))
            from contextlib import nullcontext
            from digital_twin.infrastructure.transactions.monitoring import MySQLMonitorStore
            reader = object.__new__(MySQLMonitorStore)
            reader.connect = lambda: nullcontext(connection)
            connection.execute("UPDATE monitor_snapshot_history SET payload_json=%s,projection_payload_json=%s WHERE generated_at=%s",
                               ('{"kind":"full"}', '{"kind":"compact"}', clock))
            self.assertEqual({'kind': 'compact'}, reader.reasoning_snapshot_state_at('acct', clock))
            connection.execute("UPDATE monitor_snapshot_history SET projection_payload_json=NULL WHERE generated_at=%s", (clock,))
            self.assertEqual({'kind': 'full'}, reader.reasoning_snapshot_state_at('acct', clock))
            connection.execute("INSERT INTO reasoning_engine_jobs VALUES ('queued','2','queued')")
            connection.execute("INSERT INTO ontology_reasoning_mailbox_events VALUES ('mailbox','3','pending')")
            class ConcurrentReference:
                def execute(self, sql, params=()):
                    if sql.startswith('DELETE FROM `monitor_snapshot_history`'):
                        connection.execute("INSERT IGNORE INTO reasoning_engine_jobs VALUES ('raced','4','processing')")
                    return connection.execute(sql, params)
            repository = MySQLMinimalRetentionRepository(ConcurrentReference())
            policy = mysql_minimal_retention_policy({'mysqlMinimalRetentionEnabled': '1'})
            def budget():
                return {'started': time.monotonic(), 'maxSeconds': 30, 'remainingBytes': 1024, 'deletedBytes': 0}
            first = repository._delete_snapshot_history(policy, budget(), 2)
            self.assertEqual(1, first['deleted'])
            self.assertEqual(5, connection.execute('SELECT COUNT(*) AS n FROM monitor_snapshot_history').fetchone()['n'])
            connection.execute("UPDATE reasoning_engine_jobs SET job_status='completed'")
            connection.execute("UPDATE ontology_reasoning_mailbox_events SET state='completed'")
            second = repository._delete_snapshot_history(policy, budget(), 2)
            self.assertEqual(3, second['deleted'])
            self.assertEqual(2, connection.execute('SELECT COUNT(*) AS n FROM monitor_snapshot_history').fetchone()['n'])
        finally:
            try:
                for table in tables:
                    connection.execute('DROP TABLE IF EXISTS `' + table + '`')
            finally:
                connection.close()

    def test_reasoning_job_retention_preserves_pending_market_anchor_sources(self):
        connection = ApplyConnection()
        repository = MySQLMinimalRetentionRepository(connection)
        policy = mysql_minimal_retention_policy({
            "mysqlMinimalRetentionEnabled": "1",
            "mysqlMinimalRetentionMode": "apply",
        })
        budget = {
            "started": time.monotonic(),
            "maxSeconds": 30,
            "remainingBytes": 1024 * 1024,
            "deletedBytes": 0,
        }

        repository._delete_reasoning_engine_jobs(
            policy,
            budget,
            "2026-08-01T00:00:00Z",
        )

        select_sql = next(
            sql for sql, _params in connection.calls
            if "FROM `reasoning_engine_jobs`" in sql and sql.lstrip().startswith("SELECT")
        )
        self.assertIn("market_observation_reasoning_anchors", select_sql)
        self.assertIn("pending_event_id", select_sql)
        # Keep the curated suite's deliberate case budget while exercising
        # each cleanup failure/locking scenario with an isolated MySQL fixture.
        for scenario in (
            "bounded_orphans_preserve_all_existing_jobs_and_do_not_lock_live_lineage",
            "delete_rechecks_parent_created_after_selection",
            "deadlock_after_one_commit_resumes_without_another_parent_deletion",
            "cleanup_obeys_byte_and_time_budgets",
        ):
            with self.subTest(lineage=scenario):
                checks = MySQLReasoningLineageRetentionChecks()
                try:
                    checks.setUp()
                    getattr(checks, "check_" + scenario)()
                finally:
                    checks.doCleanups()

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
        self.assertEqual(24 * 2, policy.failed_world_projection_payload_retention_hours)
        self.assertEqual(24 * 30, policy.failed_world_projection_retention_hours)
        self.assertEqual(24 * 30, policy.terminal_notification_retention_hours)
        self.assertEqual(24 * 90, policy.investment_reasoning_case_retention_hours)
        self.assertEqual({"1m": 7, "3m": 7, "10m": 7, "15m": 30, "1h": 365, "1d": 1825}, policy.market_time_series_retention_days)

        accelerated = mysql_minimal_retention_policy({
            "mysqlMinimalRetentionEnabled": "1",
            "_effectiveMysqlMinimalRetentionBatchSize": "1000",
            "_effectiveMysqlMinimalRetentionMaxDeleteBytes": str(256 * 1024 * 1024),
            "_effectiveMysqlMinimalRetentionMaxRunSeconds": "60",
        })
        self.assertEqual(1000, accelerated.batch_size)
        self.assertEqual(256 * 1024 * 1024, accelerated.max_delete_bytes)
        self.assertEqual(60, accelerated.max_run_seconds)

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

    def test_delivered_payload_service_uses_the_dedicated_compaction_path(self):
        repository = RepositorySpy()
        service = MySQLMinimalRetentionService(repository, {
            "mysqlMinimalRetentionEnabled": "1",
        })

        result = service.compact_delivered_payloads(force=True)

        self.assertEqual("payload-compaction", result["mode"])
        self.assertEqual(3, result["compacted"])
        self.assertEqual(0, len(repository.preview_calls))
        self.assertEqual(0, len(repository.apply_calls))
        self.assertEqual(1, len(repository.recorded))

        self._assert_dedicated_payload_compaction_skips_other_retention_tables()

    def _assert_dedicated_payload_compaction_skips_other_retention_tables(self):
        connection = DeliveredPayloadConnection()
        repository = MySQLMinimalRetentionRepository(connection)
        policy = mysql_minimal_retention_policy({
            "mysqlMinimalRetentionEnabled": "1",
            "mysqlMinimalRetentionBatchSize": "10",
        })

        result = repository.compact_delivered_payloads(policy)

        self.assertEqual("ok", result["status"])
        self.assertEqual(2, result["compacted"])
        sql_text = "\n".join(sql for sql, _params in connection.calls)
        self.assertIn("time_series_projection_outbox", sql_text)
        self.assertIn("ontology_world_projection_outbox", sql_text)
        self.assertIn("ORDER BY completed_at DESC", sql_text)
        self.assertNotIn("SELECT 1 FROM `ontology_world_projection_outbox` newer", sql_text)
        world_updates = [
            sql for sql, _params in connection.calls
            if sql.lstrip().startswith("UPDATE `ontology_world_projection_outbox`")
        ]
        self.assertTrue(world_updates)
        self.assertIn("result_json = '{}'", world_updates[-1])
        self.assertNotIn("notification_jobs", sql_text)
        self.assertNotIn("monitor_snapshot_history", sql_text)
        self.assertNotIn("investment_research_runs", sql_text)

    def test_repository_deletes_only_expired_terminal_primary_keys(self):
        self._assert_research_audit_expiry_is_rechecked_at_preview_selection_and_delete()
        self._assert_research_scan_resumes_without_losing_protected_or_unprocessed_rows()
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

    def test_completed_time_series_payload_is_compacted_without_deleting_receipt(self):
        connection = DeliveredPayloadConnection()
        repository = MySQLMinimalRetentionRepository(connection)
        policy = mysql_minimal_retention_policy({
            "mysqlMinimalRetentionEnabled": "1",
            "mysqlMinimalRetentionMode": "apply",
        })
        budget = {
            "started": time.monotonic(),
            "maxSeconds": 30,
            "remainingBytes": 1024 * 1024,
            "deletedBytes": 0,
        }

        result = repository._compact_completed_time_series_projection_payloads(policy, budget)

        self.assertEqual(1, result["compacted"])
        self.assertEqual(4096, result["estimatedBytes"])
        self.assertTrue(any(
            "SET payload_json = '{}'" in sql and params == ("ts-completed",)
            for sql, params in connection.calls
        ))
        self.assertFalse(any(
            sql.lstrip().startswith("DELETE FROM `time_series_projection_outbox`")
            for sql, _params in connection.calls
        ))

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

    def test_legacy_history_targets_no_longer_delete_retryable_jobs_by_age(self):
        tables = {target.table for target in MYSQL_OPERATIONAL_HISTORY_RETENTION_TARGETS}

        self.assertNotIn("notification_jobs", tables)
        self.assertNotIn("model_review_jobs", tables)
        self.assertNotIn("verified_reasoning_source_snapshots", tables)

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
        news_analysis_queries = [sql for sql, _params in connection.calls if "news_analysis_work_items" in sql]
        self.assertTrue(any("status` = 'done'" in sql for sql in model_review_queries))
        self.assertFalse(any("'failed'" in sql for sql in model_review_queries))
        self.assertTrue(any("status` IN ('done', 'sent')" in sql for sql in notification_queries))
        self.assertFalse(any("WHERE `created_at` <" in sql for sql in notification_queries))
        self.assertTrue(any("work_state` = 'completed'" in sql for sql in news_analysis_queries))
        self.assertFalse(any("retrying" in sql or "running" in sql for sql in news_analysis_queries))


class IndexedRetentionExecutionTests(unittest.TestCase):
    """Execute selection and guarded deletion against isolated relational rows."""

    def setUp(self):
        import sqlite3
        from dataclasses import replace
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.create_function("OCTET_LENGTH", 1, lambda value: len(value.encode()))
        self.db.execute("CREATE TABLE investment_reasoning_cases (case_id TEXT PRIMARY KEY, "
                        "stage TEXT, completed_at TEXT, updated_at TEXT, payload_json TEXT)")
        self.db.execute("CREATE TABLE statistical_model_signal_snapshots "
                        "(snapshot_id TEXT PRIMARY KEY, created_at TEXT, payload_json TEXT)")
        for table in ("statistical_model_signal_heads", "statistical_model_assessment_heads"):
            self.db.execute("CREATE TABLE " + table + " (snapshot_id TEXT)")
        self.db.execute("CREATE TABLE investment_subject_decision_cases "
                        "(subject_case_id TEXT PRIMARY KEY, completed_at TEXT, payload_json TEXT)")
        for table, key in (("decision_candidate_snapshots", "candidate_set_id"),
                           ("decision_publications", "publication_id")):
            self.db.execute("CREATE TABLE " + table + " (" + key + " TEXT PRIMARY KEY, "
                            "subject_case_id TEXT UNIQUE, payload_json TEXT)")
        from digital_twin.infrastructure.mysql_schema_tuning import MYSQL_OPERATIONAL_INDEXES
        for table in ("investment_reasoning_cases", "statistical_model_signal_snapshots",
                      "investment_subject_decision_cases"):
            for index in MYSQL_OPERATIONAL_INDEXES[table]:
                self.db.execute("CREATE INDEX " + index.name + " ON " + table + " (" + index.columns_sql + ")")
        self.db.execute("CREATE TABLE reasoning_engine_comparisons "
                        "(comparison_id TEXT PRIMARY KEY, created_at TEXT, payload_json TEXT)")
        index = next(index for index in MYSQL_OPERATIONAL_INDEXES["reasoning_engine_comparisons"]
                     if index.name == "idx_reasoning_comparison_retention")
        self.db.execute("CREATE INDEX " + index.name + " ON " + index.table + " (" + index.columns_sql + ")")
        self.policy = replace(mysql_minimal_retention_policy({}), batch_size=3)
        self.budget = {"started": time.monotonic(), "maxSeconds": 30,
                       "remainingBytes": 100, "deletedBytes": 0}
        self.before_delete = None
        self.repo = MySQLMinimalRetentionRepository(self)

    def tearDown(self):
        self.db.close()

    def execute(self, sql, params=()):
        if sql.startswith("DELETE") and self.before_delete:
            callback, self.before_delete = self.before_delete, None
            callback()
        sql = sql.replace("CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci", "?")
        cursor = self.db.execute(sql.replace("%s", "?"), params)
        rows = [dict(row) for row in cursor.fetchall()] if cursor.description else []
        return Cursor(rowcount=cursor.rowcount, rows=rows)

    def add_case(self, key, stage, completed="2026-01-01", updated="2026-01-01", payload="{}"):
        self.db.execute("INSERT INTO investment_reasoning_cases VALUES (?,?,?,?,?)",
                        (key, stage, completed, updated, payload))

    def remaining(self, table, key):
        return {row[0] for row in self.db.execute("SELECT " + key + " FROM " + table)}

    def seed_subjects(self):
        for key, completed in (("empty", "2025-01-01"), ("old-1", "2026-01-01"),
                               ("old-2", "2026-01-02"), ("old-3", "2026-01-03"),
                               ("old-4", "2026-01-04"), ("active", ""),
                               ("recent", "2026-10-12")):
            self.db.execute("INSERT INTO investment_subject_decision_cases VALUES (?,?,?)",
                            (key, completed, "{}"))
            if key != "empty":
                for table in ("decision_candidate_snapshots", "decision_publications"):
                    self.db.execute("INSERT INTO " + table + " VALUES (?,?,?)", (key, key, "{}"))

    def test_subject_children_expire_in_bounded_batches_without_orphaning(self):
        self.seed_subjects()
        # The oldest subject has no children: it must not starve later artifacts.
        for method in (self.repo._delete_decision_candidate_snapshots, self.repo._delete_decision_publications):
            result = method(self.policy, self.budget, "2026-10-01")
            self.assertEqual(3, result["deleted"])
        self.repo._delete_subject_decision_cases(self.policy, self.budget, "2026-10-01")
        self.assertIn("old-4", self.remaining("investment_subject_decision_cases", "subject_case_id"))
        for _ in range(3):
            self.repo._delete_decision_candidate_snapshots(self.policy, self.budget, "2026-10-01")
            self.repo._delete_decision_publications(self.policy, self.budget, "2026-10-01")
            self.repo._delete_subject_decision_cases(self.policy, self.budget, "2026-10-01")
        for table, key in (("investment_subject_decision_cases", "subject_case_id"),
                           ("decision_candidate_snapshots", "candidate_set_id"),
                           ("decision_publications", "publication_id")):
            self.assertEqual({"active", "recent"}, self.remaining(table, key))

    def test_subject_children_recheck_completion_and_obey_shared_byte_budget(self):
        self.seed_subjects()
        self.budget["remainingBytes"] = 2
        self.before_delete = lambda: self.db.execute(
            "UPDATE investment_subject_decision_cases SET completed_at='' WHERE subject_case_id='old-1'")
        result = self.repo._delete_decision_candidate_snapshots(self.policy, self.budget, "2026-10-01")
        self.assertEqual(0, result["deleted"])
        self.before_delete = lambda: self.db.execute(
            "UPDATE investment_subject_decision_cases SET completed_at='2026-10-12' WHERE subject_case_id='old-2'")
        result = self.repo._delete_decision_publications(self.policy, self.budget, "2026-10-01")
        self.assertEqual(0, result["deleted"])
        result = self.repo._delete_decision_candidate_snapshots(self.policy, self.budget, "2026-10-01")
        self.assertEqual(1, result["deleted"])
        self.assertEqual(0, self.budget["remainingBytes"])
        self.assertEqual(0, self.repo._delete_decision_publications(self.policy, self.budget, "2026-10-01")["deleted"])

    def test_subject_parent_rechecks_child_presence_and_child_read_failures_preserve_rows(self):
        self.seed_subjects()
        def add_child():
            self.db.execute("INSERT INTO decision_candidate_snapshots VALUES ('new-child','empty','{}')")
        self.before_delete = add_child
        self.assertEqual(0, self.repo._delete_subject_decision_cases(self.policy, self.budget, "2026-10-01")["deleted"])
        from unittest.mock import patch
        for method in (self.repo._delete_decision_candidate_snapshots, self.repo._delete_decision_publications):
            with patch.object(self, "execute", side_effect=RuntimeError("read timeout")):
                with self.assertRaisesRegex(RuntimeError, "read timeout"):
                    method(self.policy, self.budget, "2026-10-01")
        self.assertEqual(7, len(self.remaining("decision_candidate_snapshots", "candidate_set_id")))
        self.assertEqual(6, len(self.remaining("decision_publications", "publication_id")))

    def test_case_and_comparison_ranges_keep_current_rows_and_make_bounded_progress(self):
        expired = {"COMPLETED", "PUBLISHED", "BLOCKED", "FAILED", "SUPPRESSED", "SUPERSEDED", "EXPIRED", "VALIDATED"}
        for stage in expired:
            self.add_case(stage, stage)
        for stage in ("CREATED", "PROCESSING", "UNKNOWN"):
            self.add_case(stage, stage)
        self.add_case("no-clock", "COMPLETED", completed="")
        self.add_case("recent", "FAILED", completed="2026-10-11")
        self.add_case("boundary", "VALIDATED", updated="2026-10-01")
        total = 0
        for _ in range(4):
            result = self.repo._delete_investment_reasoning_cases(self.policy, self.budget, "2026-10-01")
            self.assertLessEqual(result["deleted"], 3)
            total += result["deleted"]
        self.assertEqual(8, total)
        self.assertEqual({"CREATED", "PROCESSING", "UNKNOWN", "no-clock", "recent", "boundary"},
                         self.remaining("investment_reasoning_cases", "case_id"))
        from dataclasses import replace
        for number in range(20):
            self.db.execute("INSERT INTO reasoning_engine_comparisons VALUES (?,?,?)",
                            (f"old-{number:02d}", "2026-01-01", "x" * 40))
        for key, date in (("boundary", "2026-10-01"), ("recent", "2026-10-12")):
            self.db.execute("INSERT INTO reasoning_engine_comparisons VALUES (?,?,?)", (key, date, "{}"))
        measured = []
        self.db.create_function("OCTET_LENGTH", 1, lambda value: measured.append(len(value)) or len(value))
        self.budget.update(remainingBytes=100, deletedBytes=0)
        result = self.repo._delete_reasoning_comparisons(replace(self.policy, batch_size=500), self.budget, "2026-10-01")
        self.assertEqual(8, len(measured))  # Payload reads stay small even with a large configured batch.
        self.assertEqual(2, result["deleted"])
        self.assertEqual(80, result["estimatedBytes"])
        self.assertEqual(20, self.budget["remainingBytes"])
        self.assertEqual({f"old-{number:02d}" for number in range(2, 20)} | {"boundary", "recent"},
                         self.remaining("reasoning_engine_comparisons", "comparison_id"))

    def test_case_and_comparison_delete_rechecks_state_and_clock_after_candidate_selection(self):
        self.add_case("reactivated", "FAILED")
        self.add_case("renewed", "VALIDATED")
        self.add_case("expired", "COMPLETED")
        def change():
            self.db.execute("UPDATE investment_reasoning_cases SET stage='PROCESSING' WHERE case_id='reactivated'")
            self.db.execute("UPDATE investment_reasoning_cases SET updated_at='2026-10-12' WHERE case_id='renewed'")
        self.before_delete = change
        result = self.repo._delete_investment_reasoning_cases(self.policy, self.budget, "2026-10-01")
        self.assertEqual(1, result["deleted"])
        self.assertEqual({"reactivated", "renewed"}, self.remaining("investment_reasoning_cases", "case_id"))
        for key in ("renewed", "expired"):
            self.db.execute("INSERT INTO reasoning_engine_comparisons VALUES (?, '2026-01-01', '{}')", (key,))
        self.before_delete = lambda: self.db.execute(
            "UPDATE reasoning_engine_comparisons SET created_at='2026-10-12' WHERE comparison_id='renewed'")
        result = self.repo._delete_reasoning_comparisons(self.policy, self.budget, "2026-10-01")
        self.assertEqual(1, result["deleted"])
        self.assertEqual({"renewed"}, self.remaining("reasoning_engine_comparisons", "comparison_id"))

    def test_case_union_shares_one_byte_budget_across_stages(self):
        for stage in ("FAILED", "VALIDATED", "EXPIRED"):
            self.add_case(stage, stage, payload="x" * 40)
        self.budget["remainingBytes"] = 60
        result = self.repo._delete_investment_reasoning_cases(self.policy, self.budget, "2026-10-01")
        self.assertEqual(1, result["deleted"])
        self.assertEqual(40, result["estimatedBytes"])
        self.assertEqual(20, self.budget["remainingBytes"])

    def test_signal_index_keeps_both_heads_and_rechecks_new_head_before_delete(self):
        for key in ("signal-head", "assessment-head", "new-head", "expired", "recent"):
            self.db.execute("INSERT INTO statistical_model_signal_snapshots VALUES (?,?,?)",
                            (key, "2026-10-12" if key == "recent" else "2026-01-01", "{}"))
        self.db.execute("INSERT INTO statistical_model_signal_heads VALUES ('signal-head')")
        self.db.execute("INSERT INTO statistical_model_assessment_heads VALUES ('assessment-head')")
        self.before_delete = lambda: self.db.execute("INSERT INTO statistical_model_signal_heads VALUES ('new-head')")
        result = self.repo._delete_statistical_model_signal_snapshots(self.policy, self.budget, "2026-10-01")
        self.assertEqual(1, result["deleted"])
        self.assertEqual({"signal-head", "assessment-head", "new-head", "recent"},
                         self.remaining("statistical_model_signal_snapshots", "snapshot_id"))


class MySQLReasoningLineageRetentionChecks(unittest.TestCase):
    """Exercise the real MySQL guards and resume after partial autocommit work."""

    def setUp(self):
        import os
        from dataclasses import replace
        from unittest.mock import patch
        from mysql_fixtures import mysql_test_connection
        from pymysql.cursors import DictCursor
        from digital_twin.infrastructure.mysql_operational_connection import MySQLConnectionProxy
        self.env = patch.dict(os.environ, {"MYSQL_TEST_DATABASE": "orbit_alpha_test_retention_lineage"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.raw = mysql_test_connection()
        self.raw.cursorclass = DictCursor
        self.raw.autocommit(True)
        self.connection = MySQLConnectionProxy(self.raw)
        self.addCleanup(self.connection.close)
        for table in ("reasoning_engine_job_sources", "reasoning_engine_jobs"):
            self.connection.execute("DROP TABLE IF EXISTS " + table)
        self.connection.execute("CREATE TABLE reasoning_engine_jobs (job_id VARCHAR(40) PRIMARY KEY, job_status VARCHAR(40)) ENGINE=InnoDB")
        self.connection.execute("CREATE TABLE reasoning_engine_job_sources ("
            "survivor_job_id VARCHAR(40), source_event_id VARCHAR(40), account_id VARCHAR(40), symbol VARCHAR(40), "
            "deployment_id VARCHAR(40), source_snapshot_id VARCHAR(40), source_snapshot_at VARCHAR(40), "
            "representation_mode VARCHAR(40), created_at VARCHAR(40), "
            "PRIMARY KEY(survivor_job_id, source_event_id, account_id, symbol)) ENGINE=InnoDB")
        self.policy = replace(mysql_minimal_retention_policy({}), batch_size=3)
        self.budget = {"started": time.monotonic(), "maxSeconds": 30,
                       "remainingBytes": 10000, "deletedBytes": 0}
        self.repo = MySQLMinimalRetentionRepository(self.connection)

    def source(self, job, event="event", account="", symbol=""):
        self.connection.execute("INSERT INTO reasoning_engine_job_sources VALUES (%s,%s,%s,%s,'v2','','','direct','2026-01-01')",
                                (job, event, account, symbol))

    def remaining(self):
        return {(row["survivor_job_id"], row["source_event_id"])
                for row in self.connection.execute("SELECT survivor_job_id, source_event_id FROM reasoning_engine_job_sources").fetchall()}

    def check_bounded_orphans_preserve_all_existing_jobs_and_do_not_lock_live_lineage(self):
        from mysql_fixtures import mysql_test_connection
        for status in ("queued", "processing", "completed", "failed"):
            self.connection.execute("INSERT INTO reasoning_engine_jobs VALUES (%s,%s)", (status, status))
            self.source(status)
        for i in range(5):
            self.source("orphan-" + str(i))
        # A concurrent ingestion transaction owns a live lineage row. Orphan
        # cleanup must not wait for that unrelated lock.
        other = mysql_test_connection()
        try:
            other.autocommit(False)
            with other.cursor() as cursor:
                cursor.execute("SELECT * FROM reasoning_engine_job_sources WHERE survivor_job_id='processing' FOR UPDATE")
            self.connection.execute("SET SESSION innodb_lock_wait_timeout=1")
            result = self.repo._delete_reasoning_job_sources(self.policy, self.budget)
        finally:
            other.rollback()
            other.close()
        self.assertEqual(3, result["deleted"])
        self.assertEqual({"reasoning_engine_job_sources": 3}, result["tables"])
        self.assertGreater(result["estimatedBytes"], 0)
        self.assertEqual(10000 - result["estimatedBytes"], self.budget["remainingBytes"])
        self.assertEqual({(status, "event") for status in ("queued", "processing", "completed", "failed")}
                         | {("orphan-3", "event"), ("orphan-4", "event")}, self.remaining())

    def check_delete_rechecks_parent_created_after_selection(self):
        from unittest.mock import patch
        self.source("raced")
        execute = self.connection.execute
        def race(sql, params=()):
            if sql.startswith("DELETE FROM `reasoning_engine_job_sources`"):
                execute("INSERT INTO reasoning_engine_jobs VALUES ('raced','processing')")
            return execute(sql, params)
        with patch.object(self.connection, "execute", side_effect=race):
            self.assertEqual(0, self.repo._delete_reasoning_job_sources(self.policy, self.budget)["deleted"])
        self.assertEqual({("raced", "event")}, self.remaining())

    def check_deadlock_after_one_commit_resumes_without_another_parent_deletion(self):
        from unittest.mock import patch
        from pymysql.err import OperationalError
        for event in ("a", "b", "c"):
            self.source("gone", event)
        execute = self.connection.execute
        def fail(sql, params=()):
            if sql.startswith("DELETE FROM `reasoning_engine_job_sources`") and params[1] == "b":
                raise OperationalError(1213, "injected deadlock")
            return execute(sql, params)
        with patch.object(self.connection, "execute", side_effect=fail):
            with self.assertRaises(OperationalError):
                self.repo._delete_reasoning_job_sources(self.policy, self.budget)
        self.assertEqual({("gone", "b"), ("gone", "c")}, self.remaining())
        result = self.repo._delete_reasoning_job_sources(self.policy, self.budget)
        self.assertEqual(2, result["deleted"])
        self.assertEqual(set(), self.remaining())
        self.assertEqual(0, self.repo._delete_reasoning_job_sources(self.policy, self.budget)["deleted"])

    def check_cleanup_obeys_byte_and_time_budgets(self):
        self.source("orphan", "a")
        self.source("orphan", "b")
        size = 6 + 1 + 2 + len("direct") + len("2026-01-01")
        self.budget["remainingBytes"] = size
        self.assertEqual(1, self.repo._delete_reasoning_job_sources(self.policy, self.budget)["deleted"])
        self.assertEqual(0, self.budget["remainingBytes"])
        self.budget.update(remainingBytes=10000, started=time.monotonic() - 31)
        self.assertEqual(0, self.repo._delete_reasoning_job_sources(self.policy, self.budget)["deleted"])
        self.assertEqual({("orphan", "b")}, self.remaining())
        # The server must interrupt a slow candidate read before the socket
        # expires, leave the row untouched, and keep the connection usable.
        from pymysql.err import OperationalError
        self.budget["started"] = time.monotonic()
        bounded = MySQLMinimalRetentionRepository(self.connection, candidate_query_timeout_ms=50)
        with self.assertRaises(OperationalError) as raised:
            bounded._byte_bounded_candidates(
                "SELECT survivor_job_id, 1 AS payload_bytes FROM reasoning_engine_job_sources WHERE SLEEP(1) = 0 LIMIT 1",
                (), "survivor_job_id", self.policy, self.budget)
        self.assertEqual(3024, raised.exception.args[0])
        self.assertEqual({("orphan", "b")}, self.remaining())


if __name__ == "__main__":
    unittest.main()
