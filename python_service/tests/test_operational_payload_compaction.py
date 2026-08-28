import unittest
from contextlib import contextmanager

from digital_twin.infrastructure.mysql_ontology_world_projection_outbox import (
    MySQLOntologyWorldProjectionOutboxStore,
)
from digital_twin.infrastructure.mysql_versioned_runtime import (
    MySQLTimeSeriesProjectionOutboxStore,
)


class Cursor:
    def __init__(self, *, rowcount=0, one=None):
        self.rowcount = rowcount
        self.one = one

    def fetchone(self):
        return self.one


class WorldProjectionConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        rendered = str(sql)
        values = tuple(params or ())
        self.calls.append((rendered, values))
        if rendered.lstrip().startswith("SELECT dedupe_key"):
            return Cursor(one={
                "dedupe_key": "world-dedupe",
                "projection_kind": "market",
                "world_id": "market:KR",
            })
        if "SET status = %s" in rendered and "WHERE job_id = %s" in rendered:
            return Cursor(rowcount=1)
        return Cursor(rowcount=2)


class TimeSeriesConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((str(sql), tuple(params or ())))
        return Cursor(rowcount=1)


class OperationalPayloadCompactionTests(unittest.TestCase):
    def test_world_completion_keeps_current_payload_and_compacts_older_world_packets(self):
        connection = WorldProjectionConnection()

        class Store(MySQLOntologyWorldProjectionOutboxStore):
            @contextmanager
            def transaction(self):
                yield connection

        completed = object.__new__(Store).complete("job:new", "worker:1", {"ok": True})

        self.assertTrue(completed)
        compaction_sql, compaction_params = next(
            (sql, params)
            for sql, params in connection.calls
            if "payload_json = '{}'" in sql and "projection_kind = %s" in sql
        )
        self.assertIn("job_id != %s", compaction_sql)
        self.assertIn("payload_json != '{}' OR result_json != '{}'", compaction_sql)
        self.assertEqual("market", compaction_params[1])
        self.assertEqual("market:KR", compaction_params[2])
        self.assertEqual("job:new", compaction_params[3])

    def test_time_series_completion_releases_delivered_payload(self):
        connection = TimeSeriesConnection()

        class Store(MySQLTimeSeriesProjectionOutboxStore):
            @contextmanager
            def connect(self):
                yield connection

        object.__new__(Store).complete("ts-job:1")

        update_sql = connection.calls[0][0]
        self.assertIn("job_status = 'completed'", update_sql)
        self.assertIn("payload_json = '{}'", update_sql)


if __name__ == "__main__":
    unittest.main()
