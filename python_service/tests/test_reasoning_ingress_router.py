import unittest
from contextlib import nullcontext
from unittest.mock import patch

from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED
from digital_twin.infrastructure.mysql_reasoning_ingress import (
    MySQLReasoningIngressRouter,
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
    def test_v2_ingress_targets_delivery_and_candidate_deployments(self):
        class Connection:
            def execute(self, sql, params=()):
                if "FROM reasoning_engine_control" in sql:
                    return Cursor({
                        "active_deployment_id": "v2-r15",
                        "delivery_deployment_id": "v2-r15",
                        "candidate_deployment_id": "v2-r14",
                    })
                if "FROM reasoning_engine_deployments" in sql:
                    return Cursor(many=[
                        {"deployment_id": "v2-r14"},
                        {"deployment_id": "v2-r15"},
                    ])
                if "FROM runtime_settings" in sql:
                    assert params == ("reasoningEngineV2DeploymentId",)
                    return Cursor({"value": "v2-r14"})
                raise AssertionError("unexpected SQL: " + sql)

        self.assertEqual(
            ["v2-r14", "v2-r15"],
            MySQLReasoningEngineJobStore.target_deployments_with_connection(Connection()),
        )

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

    def test_source_boundary_is_the_first_snapshot_covering_event_time(self):
        class BoundaryConnection:
            def execute(self, sql, params=()):
                self.sql = sql
                self.params = params
                return Cursor(many=[{
                    "snapshot_id": "reasoning-source:1",
                    "account_id": "account:1",
                    "generated_at": "2026-08-18T01:01:00Z",
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

    def test_market_event_uses_its_verified_observation_time(self):
        market_event = event()
        market_event.payload.update({
            "workClass": "MARKET",
            "sourceObservedAt": "2026-08-18T00:59:30Z",
        })

        self.assertEqual(
            "2026-08-18T00:59:30Z",
            MySQLReasoningEngineJobStore.required_source_boundary_at(market_event),
        )

    def test_market_facts_infer_work_class_for_verified_snapshot_boundary(self):
        market_event = event()
        market_event.payload.update({
            "factTypes": ["ExecutionFlow", "MarketQuote", "TechnicalIndicator"],
            "trigger": "verified-monitor-snapshot",
            "sourceObservedAt": "2026-08-18T00:59:30Z",
            "verifiedSourceSnapshot": {
                "snapshotId": "reasoning-source:market",
                "generatedAt": "2026-08-18T00:59:30Z",
            },
        })

        self.assertEqual(
            "2026-08-18T00:59:30Z",
            MySQLReasoningEngineJobStore.required_source_boundary_at(market_event),
        )

    @patch(
        "digital_twin.infrastructure.mysql_reasoning_ingress."
        "MySQLOntologyReasoningMailboxStore._refresh_queue_state_with_connection"
    )
    @patch.object(
        MySQLReasoningEngineJobStore,
        "backfill_source_boundaries_with_connection",
    )
    def test_reconcile_backfills_the_configured_candidate_before_promotion(
        self,
        backfill,
        _refresh,
    ):
        backfill.return_value = {
            "scannedCount": 6,
            "updatedCount": 6,
            "waitingCount": 0,
        }

        class Connection:
            def execute(self, sql, params=()):
                if "FROM reasoning_engine_control control" in sql:
                    return Cursor({
                        "active_deployment_id": "v2-r17",
                        "engine_version": "v2",
                    })
                if "FROM reasoning_engine_control WHERE" in sql:
                    return Cursor({
                        "active_deployment_id": "v2-r17",
                        "delivery_deployment_id": "v2-r17",
                        "candidate_deployment_id": "v2-r18",
                    })
                if "FROM reasoning_engine_deployments" in sql:
                    return Cursor(many=[
                        {"deployment_id": "v2-r17"},
                        {"deployment_id": "v2-r18"},
                    ])
                if "FROM runtime_settings" in sql:
                    return Cursor({"value": "v2-r18"})
                if "COUNT(*) AS row_count" in sql:
                    return Cursor({"row_count": 0})
                if sql.lstrip().startswith(("DELETE", "UPDATE")):
                    return Cursor()
                raise AssertionError("unexpected SQL: " + sql)

        class Router(MySQLReasoningIngressRouter):
            def transaction(self):
                return nullcontext(Connection())

        result = Router.__new__(Router).reconcile()

        self.assertEqual(2, backfill.call_count)
        self.assertEqual(
            {"v2-r17", "v2-r18"},
            {call.args[1] for call in backfill.call_args_list},
        )
        self.assertEqual(12, result["sourceBoundaryBackfill"]["updatedCount"])


if __name__ == "__main__":
    unittest.main()
