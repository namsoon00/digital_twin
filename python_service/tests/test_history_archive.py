import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from digital_twin.infrastructure.history_archive import LocalHistoryArchive, content_chunks
from digital_twin.infrastructure.mysql_history_archive import archive_delete_row, read_archived_row, restore_archived_row
from digital_twin.platform.domain.storage_preservation import PROTECTED_HISTORY_TABLES


class HistoryArchiveTests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        space = patch('digital_twin.infrastructure.history_archive.shutil.disk_usage',
                      return_value=SimpleNamespace(free=20 * 1024 ** 3))
        space.start()
        self.addCleanup(space.stop)

    def test_low_disk_defers_without_touching_mysql_or_writing_archive(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as root:
            archive = LocalHistoryArchive(Path(root) / 'not-created')
            connection = Mock()
            with patch('digital_twin.infrastructure.history_archive.shutil.disk_usage',
                       return_value=SimpleNamespace(free=6 * 1024 ** 3)):
                self.assertEqual(0, archive_delete_row(connection, 'verified_reasoning_source_snapshots',
                    {'snapshot_id': 's'}, '1=1', archive=archive))
            connection.execute.assert_not_called()
            self.assertFalse(archive.root.exists())

    def test_exact_restore_reuses_content_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as root:
            archive = LocalHistoryArchive(root)
            row = {"snapshot_id": "s1", "payload_json": json.dumps({"text": "한글🙂" * 5000}),
                   "clock": "2026-10-01T00:00:00Z", "nullable": None}
            first = archive.put(row)
            second = archive.put(row)
            self.assertEqual(row, archive.get(first["manifestSha256"]))
            self.assertEqual(0, second["writtenBytes"])
            self.assertEqual(first["manifestSha256"], second["manifestSha256"])
            manifest = json.loads(archive._read(first["manifestSha256"], 2 * 1024 * 1024))
            archive._path(manifest["chunks"][0]["sha256"]).write_bytes(b"broken")
            with self.assertRaises(Exception):
                archive.get(first["manifestSha256"])

    def test_chunks_are_bounded_and_reuse_unchanged_blocks(self):
        # Repeated compressed snapshots still benefit from unchanged chunks,
        # and reconstruction never depends on an older snapshot being retained.
        data = bytes(range(256)) * 1000
        chunks = list(content_chunks(data))
        self.assertEqual(data, b"".join(chunks))
        self.assertLessEqual(max(map(len, chunks)), 65536)
        with tempfile.TemporaryDirectory() as root:
            archive = LocalHistoryArchive(root)
            first = archive.put({"value": data.hex(), "id": "one"})
            next_row = {"value": data.hex(), "id": "two"}
            second = archive.put(next_row)
            self.assertEqual(next_row, archive.get(second["manifestSha256"]))
            self.assertLess(second["writtenBytes"], first["writtenBytes"])

    def test_symlink_cannot_redirect_private_archive(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "target"
            target.mkdir()
            link = Path(root) / "link"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                LocalHistoryArchive(link).put({"private": "data"})

    def test_retention_never_deletes_decision_roots(self):
        from digital_twin.infrastructure.mysql_minimal_retention import MySQLMinimalRetentionRepository
        from unittest.mock import Mock
        connection = Mock()
        repository = MySQLMinimalRetentionRepository(connection)
        for table in PROTECTED_HISTORY_TABLES:
            self.assertEqual((0, 0), repository._delete_candidates(table, "id", [{"id": "1"}], "1=1", (), {}))
        connection.execute.assert_not_called()

    def test_mysql_eviction_restore_correction_and_failed_receipt_are_atomic(self):
        from pymysql.cursors import DictCursor
        from mysql_fixtures import mysql_test_connection
        from digital_twin.infrastructure.mysql_operational_connection import MYSQL_SCHEMA, MySQLConnectionProxy
        with patch.dict(os.environ, {"MYSQL_TEST_DATABASE": "orbit_alpha_test_history_archive"}):
            raw = mysql_test_connection()
        raw.cursorclass = DictCursor
        raw.autocommit(True)
        connection = MySQLConnectionProxy(raw)
        self.addCleanup(connection.close)
        for table in ("storage_archive_records", "verified_reasoning_source_snapshots"):
            connection.execute("DROP TABLE IF EXISTS " + table)
            connection.execute(next(sql for sql in MYSQL_SCHEMA if "CREATE TABLE IF NOT EXISTS " + table + " (" in sql))
        row = {"snapshot_id": "source-1", "account_id": "test", "fingerprint": "a" * 64,
               "symbols_json": "[]", "payload_json": '{"original":"한글"}',
               "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}
        fields = ",".join(row)
        def insert():
            connection.execute("INSERT INTO verified_reasoning_source_snapshots (" + fields
                + ") VALUES (" + ",".join(["%s"] * len(row)) + ")", tuple(row.values()))
        key = {"snapshot_id": row["snapshot_id"]}
        with tempfile.TemporaryDirectory() as root:
            archive = LocalHistoryArchive(root)
            insert()
            original = connection.execute("SELECT * FROM verified_reasoning_source_snapshots").fetchone()
            # A correction after readback must survive compare-before-delete.
            put = archive.put
            def racing_put(value):
                result = put(value)
                connection.execute("UPDATE verified_reasoning_source_snapshots SET payload_json='{}' WHERE snapshot_id=%s", (key["snapshot_id"],))
                return result
            with patch.object(archive, "put", side_effect=racing_put):
                self.assertEqual(0, archive_delete_row(connection, "verified_reasoning_source_snapshots", key, "1=1", archive=archive))
            self.assertIsNone(read_archived_row(connection, "verified_reasoning_source_snapshots", key, archive=archive))
            connection.execute("UPDATE verified_reasoning_source_snapshots SET payload_json=%s", (row["payload_json"],))
            execute = connection.execute
            def failed_receipt(sql, params=()):
                if sql.startswith("UPDATE storage_archive_records SET evicted"):
                    raise RuntimeError("simulated interrupted promotion")
                return execute(sql, params)
            with patch.object(connection, "execute", side_effect=failed_receipt), self.assertRaises(RuntimeError):
                archive_delete_row(connection, "verified_reasoning_source_snapshots", key, "1=1", archive=archive)
            self.assertEqual(1, execute("SELECT COUNT(*) AS n FROM verified_reasoning_source_snapshots").fetchone()["n"])
            # A prior receipt may be stale/corrupt. Eviction must point to the
            # manifest just verified, not silently reuse INSERT IGNORE state.
            execute("UPDATE storage_archive_records SET manifest_hash=%s", ('0' * 64,))
            self.assertEqual(1, archive_delete_row(connection, "verified_reasoning_source_snapshots", key, "1=1", archive=archive))
            self.assertEqual(original, read_archived_row(connection, "verified_reasoning_source_snapshots", key, archive=archive))
            self.assertEqual(0, archive_delete_row(connection, "verified_reasoning_source_snapshots", key, "1=1", archive=archive))
            with patch('digital_twin.infrastructure.mysql_history_archive.archive_for_connection', return_value=archive):
                self.assertTrue(restore_archived_row(connection, "verified_reasoning_source_snapshots", key)['verified'])
            restored = execute('SELECT * FROM verified_reasoning_source_snapshots WHERE snapshot_id=%s', (key['snapshot_id'],)).fetchone()
            self.assertEqual(original, restored)
            self.assertEqual(0, archive_delete_row(connection, "verified_reasoning_source_snapshots", key,
                                                   "1=1", archive=archive, may_evict=lambda: False))
            self.assertEqual(1, execute('SELECT COUNT(*) AS n FROM verified_reasoning_source_snapshots').fetchone()['n'])

    def test_mysql_ai_ttl_preserves_decisions_receipts_and_active_requests(self):
        from mysql_fixtures import mysql_test_connection
        from pymysql.cursors import DictCursor
        from digital_twin.infrastructure.mysql_operational_connection import MySQLConnectionProxy
        from digital_twin.infrastructure.mysql_retention import _delete_terminal_ai_inference_rows, _unreferenced_ai_request_sql
        with patch.dict(os.environ, {'MYSQL_TEST_DATABASE': 'orbit_alpha_test_archive_ai'}):
            raw = mysql_test_connection()
        raw.cursorclass = DictCursor
        raw.autocommit(True)
        connection = MySQLConnectionProxy(raw)
        self.addCleanup(connection.close)
        schemas = {
            'ai_inference_requests': 'request_id VARCHAR(40) PRIMARY KEY, notification_job_id VARCHAR(40), status VARCHAR(20), completed_at VARCHAR(40), subject_key VARCHAR(40)',
            'ai_inference_subject_heads': 'subject_key VARCHAR(40) PRIMARY KEY, latest_request_id VARCHAR(40)',
            'ai_inference_results': 'result_id VARCHAR(40) PRIMARY KEY, request_id VARCHAR(40), notification_job_id VARCHAR(40), created_at VARCHAR(40)',
            'ai_inference_execution_audits': 'request_id VARCHAR(40) PRIMARY KEY, notification_job_id VARCHAR(40), created_at VARCHAR(40)',
            'investment_subject_decision_cases': 'ai_request_id VARCHAR(40) PRIMARY KEY',
            'investment_ai_insight_episodes': 'request_id VARCHAR(40) PRIMARY KEY',
            'notification_delivery_attempts': 'job_id VARCHAR(40) PRIMARY KEY, status VARCHAR(20)',
        }
        for table, definition in schemas.items():
            connection.execute('DROP TABLE IF EXISTS ' + table)
            connection.execute('CREATE TABLE ' + table + ' (' + definition + ') ENGINE=InnoDB')
        for name in ('subject', 'insight', 'delivered', 'active', 'unlinked'):
            status = 'processing' if name == 'active' else 'completed'
            connection.execute('INSERT INTO ai_inference_requests VALUES (%s,%s,%s,%s,%s)', (name, name, status, '2026-01-01', name))
            connection.execute('INSERT INTO ai_inference_results VALUES (%s,%s,%s,%s)', (name, name, name, '2026-01-01'))
            connection.execute('INSERT INTO ai_inference_execution_audits VALUES (%s,%s,%s)', (name, name, '2026-01-01'))
        connection.execute("INSERT INTO investment_subject_decision_cases VALUES ('subject')")
        connection.execute("INSERT INTO investment_ai_insight_episodes VALUES ('insight')")
        connection.execute("INSERT INTO notification_delivery_attempts VALUES ('delivered','delivered')")
        self.assertEqual({'requests': 1, 'results': 1},
                         _delete_terminal_ai_inference_rows(connection, '2026-02-01', 20))
        connection.execute('DELETE FROM ai_inference_execution_audits WHERE created_at < %s AND '
                           + _unreferenced_ai_request_sql('ai_inference_execution_audits')
                           + ' ORDER BY created_at, request_id LIMIT 20', ('2026-02-01',))
        for table in ('ai_inference_requests', 'ai_inference_results', 'ai_inference_execution_audits'):
            self.assertEqual({'subject', 'insight', 'delivered', 'active'},
                             {row['request_id'] for row in connection.execute('SELECT request_id FROM ' + table).fetchall()})
        # The queue's own TTL entry point must obey the same reference policy.
        from contextlib import nullcontext
        from digital_twin.infrastructure.transactions.ai_publication import MySQLAIInferenceQueueStore
        queue = MySQLAIInferenceQueueStore.__new__(MySQLAIInferenceQueueStore)
        connection.execute("INSERT INTO ai_inference_requests VALUES ('unlinked','unlinked','completed','2026-01-01','unlinked')")
        with patch.object(queue, 'transaction', return_value=nullcontext(connection)):
            self.assertEqual(1, queue.prune_terminal())
        self.assertEqual({'subject', 'insight', 'delivered', 'active'},
                         {row['request_id'] for row in connection.execute('SELECT request_id FROM ai_inference_requests').fetchall()})



class PhysicalTablePreservationTests(unittest.TestCase):
    def test_offline_rebuild_preserves_every_account_key_null_and_original_payload(self):
        from pymysql.cursors import DictCursor
        from mysql_fixtures import mysql_test_connection
        from digital_twin.infrastructure.mysql_operational_connection import MySQLConnectionProxy
        from digital_twin.infrastructure.mysql_retention import (
            operational_table_fingerprint, optimize_verified_mysql_table,
        )
        with patch.dict(os.environ, {"MYSQL_TEST_DATABASE": "orbit_alpha_test_physical_reclaim"}):
            raw = mysql_test_connection()
        raw.cursorclass = DictCursor
        raw.autocommit(True)
        connection = MySQLConnectionProxy(raw)
        self.addCleanup(connection.close)
        connection.execute("DROP TABLE IF EXISTS monitor_snapshot_history")
        connection.execute("CREATE TABLE monitor_snapshot_history (account_id VARCHAR(40),"
                           "snapshot_id VARCHAR(40),payload_json LONGTEXT,source_at VARCHAR(40),"
                           "PRIMARY KEY(account_id,snapshot_id)) ENGINE=InnoDB")
        for account in ("scope-one", "scope-two", "scope-three"):
            for identity in ("snapshot-one", "snapshot-two"):
                connection.execute("INSERT INTO monitor_snapshot_history VALUES (%s,%s,%s,%s)",
                    (account, identity, '{"original":"한글 원문","nested":{"receiptVerified":true}}',
                     None if identity.endswith("one") else "2026-01-01T00:00:00Z"))
        before = connection.execute("SELECT * FROM monitor_snapshot_history ORDER BY account_id,snapshot_id").fetchall()
        proof = optimize_verified_mysql_table(connection, "monitor_snapshot_history", writers_paused=True)
        after = connection.execute("SELECT * FROM monitor_snapshot_history ORDER BY account_id,snapshot_id").fetchall()
        self.assertEqual(before, after)
        self.assertEqual(proof["before"], proof["after"])
        self.assertEqual(proof["before"]["rows"], 6)
        connection.execute("UPDATE monitor_snapshot_history SET source_at='corrected' "
                           "WHERE account_id='scope-two' AND snapshot_id='snapshot-two'")
        self.assertNotEqual(proof["after"], operational_table_fingerprint(connection, "monitor_snapshot_history"))
        with self.assertRaises(ValueError):
            optimize_verified_mysql_table(connection, "monitor_snapshot_history")
        with self.assertRaises(ValueError):
            operational_table_fingerprint(connection, "not_an_approved_table")

if __name__ == "__main__":
    unittest.main()
