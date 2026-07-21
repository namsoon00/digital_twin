import unittest

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_projection_audit import (
    apply_projection_run_identity,
    build_ontology_projection_run,
    complete_ontology_projection_run,
    projection_source_snapshot,
)
from digital_twin.domain.ontology_projection_fingerprint import (
    apply_material_graph_identity,
    material_graph_fingerprint,
)
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position
from digital_twin.infrastructure.mysql_ontology_projection_runs import MySQLOntologyProjectionRunStore


class Cursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def fetchall(self):
        return list(self.rows)


class RecordingConnection:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = list(rows or [])

    def execute(self, sql, params=()):
        self.calls.append((str(sql), tuple(params or ())))
        if str(sql).lstrip().upper().startswith("SELECT"):
            return Cursor(self.rows)
        return Cursor()


class ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return False


def source_snapshot():
    snapshot = AccountSnapshot(
        "main",
        "메인",
        "toss",
        "live",
        "ok",
        "2026-07-20T00:01:00Z",
        PortfolioSummary(total=700000, invested=700000, cash=0, markets=[], sectors=[], concentration=1),
        positions=[Position(
            "005930",
            "삼성전자",
            market="KR",
            currency="KRW",
            quantity=10,
            current_price=70000,
            market_value=700000,
        )],
        metadata={
            "previousMonitorState": {"generatedAt": "old"},
            "monitorStateHistory": [{"generatedAt": "older"}],
            "ontology": {"projection": {"derived": True}},
            "collectionSource": "KIS",
        },
    )
    return snapshot


def abox_graph():
    graph = PortfolioOntology(
        "main",
        worldview={
            "activeTBox": {"version": "tbox-v1", "fingerprint": "tbox-fingerprint"},
            "runtimeProjectionMode": "abox-facts-only-typedb-rulebox",
        },
    )
    graph.entities.extend([
        OntologyEntity("stock:005930", "삼성전자", "stock", {"ontologyBox": "ABox", "symbol": "005930"}),
        OntologyEntity("portfolio:main", "메인 포트폴리오", "portfolio", {"ontologyBox": "ABox"}),
    ])
    graph.relations.append(OntologyRelation(
        "stock:005930",
        "portfolio:main",
        "HELD_IN",
        properties={"ontologyBox": "ABox"},
    ))
    return graph


class OntologyProjectionAuditTests(unittest.TestCase):
    def build_run(self):
        snapshot = source_snapshot()
        graph = abox_graph()
        fingerprint = material_graph_fingerprint(graph)
        snapshot_id = apply_material_graph_identity(graph, snapshot.account_id, fingerprint)
        run = build_ontology_projection_run(
            snapshot,
            graph,
            fingerprint,
            snapshot_id,
            "typedb",
            target_symbols=["005930"],
            rulebox_metadata={"ruleboxRulesHash": "rulebox-hash"},
            started_at="2026-07-20T00:01:05Z",
        )
        return snapshot, graph, fingerprint, run

    def test_run_keeps_source_payload_out_of_recursive_projection_state(self):
        snapshot, graph, fingerprint, run = self.build_run()

        source = projection_source_snapshot(snapshot)

        self.assertNotIn("previousMonitorState", source["metadata"])
        self.assertNotIn("monitorStateHistory", source["metadata"])
        self.assertNotIn("ontology", source["metadata"])
        self.assertEqual("KIS", source["metadata"]["collectionSource"])
        self.assertEqual("tbox-v1", run.tbox_version)
        self.assertEqual("rulebox-hash", run.rulebox_rules_hash)
        self.assertEqual(["005930"], run.source_symbols)

        apply_projection_run_identity(graph, run.run_id)

        self.assertEqual(fingerprint, material_graph_fingerprint(graph))
        self.assertEqual(run.run_id, graph.worldview["projectionRunId"])
        self.assertTrue(all(item.properties["projectionRunId"] == run.run_id for item in graph.entities))

        repeated = build_ontology_projection_run(
            snapshot,
            graph,
            fingerprint,
            run.abox_snapshot_id,
            "typedb",
            started_at="2026-07-20T00:04:05Z",
        )
        self.assertNotEqual(run.run_id, repeated.run_id)

    def test_mysql_store_records_source_before_and_result_after_activation(self):
        _snapshot, _graph, _fingerprint, run = self.build_run()
        connection = RecordingConnection()
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        store.transaction = lambda: ConnectionContext(connection)

        store.begin(run)
        completed = complete_ontology_projection_run(run, {
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "aboxSnapshotId": run.abox_snapshot_id,
            "materialFingerprint": run.material_fingerprint,
            "entityCount": run.entity_count,
            "relationCount": run.relation_count,
            "inferenceBox": {"status": "ok", "inferenceGenerationId": "generation:1"},
        }, completed_at="2026-07-20T00:01:10Z")
        store.complete(completed)

        self.assertEqual(2, len(connection.calls))
        self.assertIn("INSERT INTO ontology_projection_runs", connection.calls[0][0])
        self.assertIn("UPDATE ontology_projection_runs", connection.calls[1][0])
        self.assertEqual(run.run_id, connection.calls[0][1][0])
        self.assertEqual(run.run_id, connection.calls[1][1][-1])
        self.assertEqual("ok", completed.status)
        self.assertEqual("generation:1", completed.inference_generation_id)

    def test_mysql_store_reads_bounded_latest_projection_runs(self):
        _snapshot, _graph, _fingerprint, run = self.build_run()
        row = {
            "run_id": run.run_id,
            "portfolio_id": run.portfolio_id,
            "account_id": run.account_id,
            "source_snapshot_at": run.source_snapshot_at,
            "source_snapshot_fingerprint": run.source_snapshot_fingerprint,
            "first_observed_at": run.first_observed_at,
            "last_observed_at": run.last_observed_at,
            "started_at": run.started_at,
            "completed_at": "2026-07-20T00:01:10Z",
            "activated_at": "2026-07-20T00:01:10Z",
            "status": "ok",
            "graph_store": "typedb",
            "projection_mode": run.projection_mode,
            "material_fingerprint": run.material_fingerprint,
            "abox_snapshot_id": run.abox_snapshot_id,
            "active_abox_snapshot_id": run.abox_snapshot_id,
            "tbox_version": run.tbox_version,
            "tbox_fingerprint": run.tbox_fingerprint,
            "rulebox_rules_hash": run.rulebox_rules_hash,
            "entity_count": run.entity_count,
            "relation_count": run.relation_count,
            "inference_generation_id": "generation:1",
            "inference_status": "ok",
            "source_symbols_json": '["005930"]',
            "context_payload_json": '{"sourceSnapshotReference":{"accountId":"main"}}',
            "result_payload_json": '{"status":"ok"}',
            "created_at": "2026-07-20T00:01:05Z",
            "updated_at": "2026-07-20T00:01:10Z",
        }
        connection = RecordingConnection(rows=[row])
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        store.connect = lambda: ConnectionContext(connection)

        latest = store.latest("main", limit=1000)

        self.assertEqual(1, len(latest))
        self.assertEqual(run.run_id, latest[0]["runId"])
        self.assertEqual(["005930"], latest[0]["sourceSymbols"])
        self.assertEqual("ok", latest[0]["result"]["status"])
        self.assertEqual(500, connection.calls[0][1][-1])


if __name__ == "__main__":
    unittest.main()
