import unittest
from unittest.mock import patch

from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED
from digital_twin.infrastructure.mysql_reasoning_ingress import (
    ingress_reasoning_event_with_connection,
)
from digital_twin.infrastructure.mysql_versioned_runtime import MySQLReasoningEngineJobStore


class Cursor:
    def __init__(self, one=None, many=None):
        self.one = one
        self.many = many or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class ActiveConnection:
    def __init__(self, version):
        self.version = version

    def execute(self, sql, _params=()):
        if "FROM reasoning_engine_control control" in sql:
            return Cursor({
                "active_deployment_id": "ontology-" + self.version + "-active",
                "engine_version": self.version,
            })
        raise AssertionError("unexpected SQL: " + sql)


def event():
    return DomainEvent(
        name=ONTOLOGY_REASONING_REQUESTED,
        aggregate_id="market-observation:005930",
        event_id="event:ingress-route",
        occurred_at="2026-08-18T01:00:00Z",
        payload={"accountIds": ["account:1"], "affectedSymbols": ["005930"]},
    )


class ReasoningIngressRouterTests(unittest.TestCase):
    @patch.object(MySQLReasoningEngineJobStore, "bind_source_boundaries_with_connection")
    @patch.object(MySQLReasoningEngineJobStore, "ingress_event_with_connection")
    @patch("digital_twin.infrastructure.mysql_reasoning_ingress.MySQLOntologyReasoningMailboxStore.ingress_event_with_connection")
    def test_active_v2_does_not_accumulate_v1_mailbox(self, legacy_ingress, v2_ingress, bind):
        bind.side_effect = lambda _connection, value: value
        v2_ingress.return_value = {"saved": True, "savedJobIds": ["job:v2"]}

        result = ingress_reasoning_event_with_connection(ActiveConnection("v2"), event())

        legacy_ingress.assert_not_called()
        v2_ingress.assert_called_once()
        self.assertEqual("inactive", result["legacyV1"]["status"])
        self.assertTrue(result["independentV2"]["saved"])

    @patch.object(MySQLReasoningEngineJobStore, "bind_source_boundaries_with_connection")
    @patch.object(MySQLReasoningEngineJobStore, "ingress_event_with_connection")
    @patch("digital_twin.infrastructure.mysql_reasoning_ingress.MySQLOntologyReasoningMailboxStore.ingress_event_with_connection")
    def test_active_v1_keeps_rollback_path_and_v2_candidate(self, legacy_ingress, v2_ingress, bind):
        bind.side_effect = lambda _connection, value: value
        legacy_ingress.return_value = {"saved": True}
        v2_ingress.return_value = {"saved": True, "savedJobIds": ["job:candidate"]}

        result = ingress_reasoning_event_with_connection(ActiveConnection("v1"), event())

        legacy_ingress.assert_called_once()
        v2_ingress.assert_called_once()
        self.assertTrue(result["legacyV1"]["saved"])
        self.assertTrue(result["independentV2"]["saved"])

    def test_source_boundary_is_selected_at_or_before_event_time(self):
        class BoundaryConnection:
            def execute(self, sql, params=()):
                self.sql = sql
                self.params = params
                return Cursor(many=[{
                    "snapshot_id": "reasoning-source:1",
                    "account_id": "account:1",
                    "generated_at": "2026-08-18T00:59:00Z",
                    "contract_version": "reasoning-source-v1",
                    "fingerprint": "abc",
                }])

        connection = BoundaryConnection()
        bounded = MySQLReasoningEngineJobStore.bind_source_boundaries_with_connection(
            connection,
            event(),
        )

        self.assertEqual("2026-08-18T01:00:00Z", connection.params[0])
        self.assertEqual("account:1", connection.params[1])
        self.assertEqual(
            "reasoning-source:1",
            bounded.payload["verifiedSourceSnapshot"]["snapshotId"],
        )


if __name__ == "__main__":
    unittest.main()
