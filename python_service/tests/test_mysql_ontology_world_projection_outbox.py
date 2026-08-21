import unittest
from unittest.mock import patch

from digital_twin.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.domain.ontology_worlds import market_world
from digital_twin.infrastructure.mysql_ontology_world_projection_outbox import (
    MySQLOntologyWorldProjectionOutboxStore,
)


class _Cursor:
    def fetchone(self):
        return {
            "job_id": "world-projection:completed",
            "completed_at": "2026-08-16T00:00:00Z",
        }


class _Connection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _Cursor()


class _ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return False


class OntologyWorldProjectionOutboxTests(unittest.TestCase):
    def test_completed_material_skips_projection_payload_serialization(self):
        store = MySQLOntologyWorldProjectionOutboxStore.__new__(
            MySQLOntologyWorldProjectionOutboxStore
        )
        store.runtime_settings = {}
        connection = _Connection()
        store.connect = lambda: _ConnectionContext(connection)
        graph = PortfolioOntology(
            "portfolio:test",
            entities=[OntologyEntity(
                "stock:TEST",
                "Test",
                "stock",
                {"ontologyBox": "ABox", "symbol": "TEST"},
            )],
        )

        with patch(
            "digital_twin.infrastructure.mysql_ontology_world_projection_outbox.serialize_portfolio_ontology"
        ) as serialize:
            result = store.enqueue(
                "market",
                market_world("us"),
                graph,
                source_world_id="portfolio:test",
            )

        self.assertEqual("already-projected-material", result["status"])
        self.assertTrue(result["payloadSerializationSkipped"])
        self.assertEqual(0, result["payloadBytes"])
        serialize.assert_not_called()
        self.assertEqual(1, len(connection.calls))

    def test_claim_does_not_admit_a_second_shared_world_writer(self):
        class Cursor:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

            def fetchall(self):
                raise AssertionError("pending jobs must not be read while a writer is active")

        class Connection:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params=None):
                self.calls.append((sql, params))
                if "SELECT job_id, lease_owner, lease_expires_at" in sql:
                    return Cursor({
                        "job_id": "world-projection:active",
                        "lease_owner": "worker:other",
                        "lease_expires_at": "2099-01-01T00:00:00Z",
                    })
                return Cursor()

        store = MySQLOntologyWorldProjectionOutboxStore.__new__(
            MySQLOntologyWorldProjectionOutboxStore
        )
        connection = Connection()
        store.transaction = lambda: _ConnectionContext(connection)

        claimed = store.claim("worker:new", limit=4, lease_seconds=300)

        self.assertEqual([], claimed)
        self.assertTrue(any(
            "SELECT job_id, lease_owner, lease_expires_at" in sql
            for sql, _params in connection.calls
        ))


if __name__ == "__main__":
    unittest.main()
