import unittest
from types import SimpleNamespace

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_projection_audit import (
    apply_projection_run_identity,
    build_ontology_projection_run,
    complete_ontology_projection_run,
    inference_reuse_scope_plan_for_targets,
    inference_reuse_scope_plan_fingerprint,
    projection_run_from_payload,
    projection_source_snapshot,
)
from digital_twin.domain.ontology_projection_fingerprint import (
    apply_material_graph_identity,
    material_graph_fingerprint,
)
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position
from digital_twin.infrastructure.mysql_ontology_projection_runs import MySQLOntologyProjectionRunStore
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder


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

    def test_projection_run_keeps_a_bounded_scope_identity_for_later_native_reuse(self):
        snapshot = source_snapshot()
        graph = abox_graph()
        graph.worldview["scopePlan"] = [{
            "scopeId": "symbol:005930:market",
            "scopeType": "symbol",
            "scopeFamily": "market",
            "impactScopeFamilies": ["market"],
            "semanticFingerprints": {"market": "price-a"},
            "generationId": "market-a",
            "fingerprint": "fingerprint-a",
            "baseFingerprint": "base-a",
            "dependencyScopeIds": [],
            # This source-only field must not be copied into the proof.
            "entityCount": 999,
        }]
        fingerprint = material_graph_fingerprint(graph)
        run = build_ontology_projection_run(snapshot, graph, fingerprint, "abox:proof", "typedb")
        topology = run.context_payload["scopeTopology"]

        self.assertEqual(1, len(topology["inferenceReuseScopePlan"]))
        self.assertNotIn("entityCount", topology["inferenceReuseScopePlan"][0])
        self.assertEqual(
            inference_reuse_scope_plan_fingerprint(topology["inferenceReuseScopePlan"]),
            topology["inferenceReuseScopePlanFingerprint"],
        )

    def test_target_reuse_scope_plan_keeps_only_target_dependencies(self):
        plan = [
            {
                "scopeId": "symbol:005930:market",
                "scopeType": "symbol",
                "scopeFamily": "market",
                "semanticFingerprints": {"market": "target-price"},
                "generationId": "target-market",
                "dependencyScopeIds": ["reference:global"],
            },
            {
                "scopeId": "reference:global",
                "scopeType": "reference",
                "scopeFamily": "reference",
                "semanticFingerprints": {"quality": "source-state"},
                "generationId": "reference",
                "dependencyScopeIds": [],
            },
            {
                "scopeId": "symbol:000660:state",
                "scopeType": "symbol",
                "scopeFamily": "state",
                "semanticFingerprints": {"state": "other-holding"},
                "generationId": "other-state",
                "dependencyScopeIds": [],
            },
        ]

        selected = inference_reuse_scope_plan_for_targets(plan, ["005930"])

        self.assertEqual(
            ["reference:global", "symbol:005930:market"],
            [item["scopeId"] for item in selected],
        )

    def test_recorder_uses_audited_target_scope_proof_when_active_inference_is_stale(self):
        class ReuseRepository:
            store_key = "typedb"

            def active_abox_metadata(self):
                return {"status": "ok", "aboxSnapshotId": "abox:current"}

            def inferencebox_snapshot(self, _symbols=None, _limit=0):
                return {"status": "stale-generation"}

        prior_scope_plan = [
            {
                "scopeId": "symbol:005930:market",
                "scopeType": "symbol",
                "scopeFamily": "market",
                "impactScopeFamilies": ["market"],
                "semanticFingerprints": {"market": "price-a"},
                "generationId": "market-a",
                "fingerprint": "market-a",
                "baseFingerprint": "market-a",
                "dependencyScopeIds": [],
            },
            {
                "scopeId": "symbol:005930:flow",
                "scopeType": "symbol",
                "scopeFamily": "flow",
                "impactScopeFamilies": ["flow"],
                "semanticFingerprints": {"flow": "flow-a"},
                "generationId": "flow-a",
                "fingerprint": "flow-a",
                "baseFingerprint": "flow-a",
                "dependencyScopeIds": [],
            },
            {
                "scopeId": "symbol:005930:quality",
                "scopeType": "symbol",
                "scopeFamily": "quality",
                "impactScopeFamilies": ["quality"],
                "semanticFingerprints": {"quality": "quality-a"},
                "generationId": "quality-a",
                "fingerprint": "quality-a",
                "baseFingerprint": "quality-a",
                "dependencyScopeIds": [],
            },
            {
                "scopeId": "symbol:000660:state",
                "scopeType": "symbol",
                "scopeFamily": "state",
                "impactScopeFamilies": ["state"],
                "semanticFingerprints": {"state": "other-a"},
                "generationId": "other-a",
                "fingerprint": "other-a",
                "baseFingerprint": "other-a",
                "dependencyScopeIds": [],
            },
        ]
        candidate_scope_plan = [
            {**prior_scope_plan[0], "generationId": "market-b", "fingerprint": "market-b", "semanticFingerprints": {"market": "price-b"}},
            *prior_scope_plan[1:-1],
            {**prior_scope_plan[-1], "generationId": "other-b", "fingerprint": "other-b", "semanticFingerprints": {"state": "other-b"}},
        ]
        scope_fingerprint = inference_reuse_scope_plan_fingerprint(prior_scope_plan)
        audit_store = SimpleNamespace(latest=lambda **_kwargs: [{
            "runId": "projection:prior",
            "status": "ok",
            "graphStore": "typedb",
            "sourceSymbols": ["005930"],
            "aboxSnapshotId": "abox:prior",
            "activeAboxSnapshotId": "abox:prior",
            "context": {
                "scopeTopology": {
                    "inferenceReuseScopePlan": prior_scope_plan,
                    "inferenceReuseScopePlanFingerprint": scope_fingerprint,
                },
            },
            "result": {
                "inferenceReuseProof": {
                    "status": "verified",
                    "coverageComplete": True,
                    "sourceAboxSnapshotId": "abox:prior",
                    "inferenceGenerationId": "inference:prior",
                    "targetSymbols": ["005930"],
                    "matchedRuleIds": ["flow-rule"],
                    "ruleboxRulesHash": "rulebox-current",
                    "tboxFingerprint": "tbox-current",
                    "scopePlanFingerprint": scope_fingerprint,
                },
            },
        }])
        recorder = PortfolioOntologyProjectionRecorder(ReuseRepository(), projection_run_store=audit_store)
        recorder._rulebox_impact_rules = [
            {
                "ruleId": "market-rule",
                "enabled": True,
                "conditions": [{"conditionId": "price", "kind": "subject_property", "field": "currentPrice"}],
            },
            {
                "ruleId": "flow-rule",
                "enabled": True,
                "conditions": [{"conditionId": "flow", "kind": "subject_property", "field": "volumeRatio"}],
            },
            {
                "ruleId": "quality-rule",
                "enabled": True,
                "conditions": [{"conditionId": "quality", "kind": "subject_property", "field": "freshnessStatus"}],
            },
        ]

        context = recorder.prior_rule_selection_context(
            source_snapshot(),
            ["005930"],
            candidate_scope_plan=candidate_scope_plan,
            rulebox_rules_hash="rulebox-current",
            tbox_fingerprint="tbox-current",
        )

        self.assertTrue(context["reusable"])
        self.assertEqual("audited-target-scope-proof", context["proofSource"])
        self.assertEqual("projection:prior", context["proofRunId"])
        self.assertEqual(["flow-rule"], context["matchedRuleIds"])
        self.assertEqual(["market-rule"], context["inferenceImpactPlan"]["candidateRuleIds"])
        self.assertEqual(1, context["recomputedChangedScopeCount"])

    def test_recorder_rejects_audited_proof_when_rulebox_version_changed(self):
        class ReuseRepository:
            store_key = "typedb"

            def active_abox_metadata(self):
                return {"status": "ok", "aboxSnapshotId": "abox:current"}

            def inferencebox_snapshot(self, _symbols=None, _limit=0):
                return {"status": "stale-generation"}

        scope_plan = [{
            "scopeId": "symbol:005930:market",
            "scopeType": "symbol",
            "scopeFamily": "market",
            "impactScopeFamilies": ["market"],
            "semanticFingerprints": {"market": "price-a"},
            "generationId": "market-a",
            "fingerprint": "market-a",
            "baseFingerprint": "market-a",
            "dependencyScopeIds": [],
        }]
        fingerprint = inference_reuse_scope_plan_fingerprint(scope_plan)
        audit_store = SimpleNamespace(latest=lambda **_kwargs: [{
            "runId": "projection:prior",
            "status": "ok",
            "graphStore": "typedb",
            "sourceSymbols": ["005930"],
            "activeAboxSnapshotId": "abox:prior",
            "context": {"scopeTopology": {
                "inferenceReuseScopePlan": scope_plan,
                "inferenceReuseScopePlanFingerprint": fingerprint,
            }},
            "result": {"inferenceReuseProof": {
                "status": "verified",
                "coverageComplete": True,
                "sourceAboxSnapshotId": "abox:prior",
                "targetSymbols": ["005930"],
                "matchedRuleIds": [],
                "ruleboxRulesHash": "rulebox-old",
                "tboxFingerprint": "tbox-current",
                "scopePlanFingerprint": fingerprint,
            }},
        }])
        recorder = PortfolioOntologyProjectionRecorder(ReuseRepository(), projection_run_store=audit_store)
        recorder._rulebox_impact_rules = [{
            "ruleId": "market-rule",
            "enabled": True,
            "conditions": [{"conditionId": "price", "kind": "subject_property", "field": "currentPrice"}],
        }]

        context = recorder.prior_rule_selection_context(
            source_snapshot(),
            ["005930"],
            candidate_scope_plan=[{**scope_plan[0], "generationId": "market-b", "fingerprint": "market-b", "semanticFingerprints": {"market": "price-b"}}],
            rulebox_rules_hash="rulebox-current",
            tbox_fingerprint="tbox-current",
        )

        self.assertFalse(context["reusable"])
        self.assertEqual("prior-aligned-inference-unavailable", context["fallbackReason"])

    def test_recorder_persists_a_complete_typedb_target_reuse_proof(self):
        snapshot = source_snapshot()
        graph = abox_graph()
        graph.worldview["scopePlan"] = [{
            "scopeId": "symbol:005930:market",
            "scopeType": "symbol",
            "scopeFamily": "market",
            "impactScopeFamilies": ["market"],
            "semanticFingerprints": {"market": "price-a"},
            "generationId": "market-a",
            "fingerprint": "market-a",
            "baseFingerprint": "market-a",
            "dependencyScopeIds": [],
        }]
        fingerprint = material_graph_fingerprint(graph)
        run = build_ontology_projection_run(
            snapshot,
            graph,
            fingerprint,
            "abox:proof",
            "typedb",
            target_symbols=["005930"],
            rulebox_metadata={"ruleboxRulesHash": "rulebox-current"},
        )
        result = {
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "inferenceBox": {
                "status": "ok",
                "inferenceGenerationId": "inference:proof",
                "sourceAboxSnapshotId": "abox:proof",
                "generationAligned": True,
                "nativeTypeDbReasoningCompleted": True,
                "targetSymbols": ["005930"],
            },
            "ruleboxExecution": {
                "nativeInferenceEvaluationComplete": True,
                "typedbNativeRuleMatchedCount": 1,
                "typedbNativeRuleMatchedRuleIds": ["market-rule"],
                "typedbNativeRuleParallelism": 4,
                "typedbNativeRuleParallelUsed": True,
                "nativeRuleSelectionApplied": False,
            },
        }

        PortfolioOntologyProjectionRecorder(SimpleNamespace(store_key="typedb")).attach_inference_reuse_proof(run, result)
        completed = complete_ontology_projection_run(run, result)

        self.assertEqual("verified", result["inferenceReuseProof"]["status"])
        self.assertTrue(result["inferenceReuseProof"]["coverageComplete"])
        self.assertEqual(["market-rule"], result["inferenceReuseProof"]["matchedRuleIds"])
        self.assertEqual(4, completed.result_payload["ruleboxExecution"]["typedbNativeRuleParallelism"])
        self.assertTrue(completed.result_payload["ruleboxExecution"]["typedbNativeRuleParallelUsed"])
        self.assertEqual(
            "verified",
            completed.result_payload["inferenceReuseProof"]["status"],
        )

    def test_run_keeps_tenant_and_world_identity_in_the_audit_contract(self):
        snapshot = source_snapshot()
        graph = abox_graph()
        graph.worldview.update({
            "tenantId": "tenant-a",
            "worldId": "portfolio:tenant-a:main",
            "worldType": "portfolio",
            "marketWorldId": "market:shared:kr",
        })
        fingerprint = material_graph_fingerprint(graph)
        run = build_ontology_projection_run(snapshot, graph, fingerprint, "abox:world", "typedb")

        self.assertEqual("tenant-a", run.tenant_id)
        self.assertEqual("portfolio:tenant-a:main", run.world_id)
        self.assertEqual("portfolio", run.world_type)
        self.assertEqual("market:shared:kr", run.market_world_id)
        self.assertEqual("portfolio:tenant-a:main", run.context_payload["world"]["worldId"])

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

        self.assertEqual(3, len(connection.calls))
        self.assertIn("aborted-stale", connection.calls[0][0])
        self.assertIn("INSERT INTO ontology_projection_runs", connection.calls[1][0])
        self.assertIn("UPDATE ontology_projection_runs", connection.calls[2][0])
        self.assertEqual(run.run_id, connection.calls[1][1][0])
        self.assertEqual(run.run_id, connection.calls[2][1][-1])
        self.assertEqual("ok", completed.status)
        self.assertEqual("generation:1", completed.inference_generation_id)

    def test_projection_audit_prefers_aligned_native_inference_source_when_pointer_is_absent(self):
        _snapshot, _graph, _fingerprint, run = self.build_run()
        completed = complete_ontology_projection_run(run, {
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "aboxSnapshotId": "abox:stale-fallback",
            "inferenceBox": {
                "status": "ok",
                "inferenceGenerationId": "generation:active",
                "sourceAboxSnapshotId": "abox:verified-active",
                "generationAligned": True,
                "nativeTypeDbReasoningUsed": True,
            },
        }, completed_at="2026-07-20T00:01:10Z")

        self.assertEqual("abox:verified-active", completed.active_abox_snapshot_id)

    def test_projection_audit_replaces_a_stale_save_pointer_with_this_run_aligned_inference(self):
        _snapshot, _graph, _fingerprint, run = self.build_run()
        completed = complete_ontology_projection_run(run, {
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "aboxSnapshotId": run.abox_snapshot_id,
            "aboxPersistenceVerification": {
                "activePointer": {"aboxSnapshotId": "abox:predecessor"},
                "activation": {"status": "activated", "snapshotId": run.abox_snapshot_id},
            },
            "inferenceBox": {
                "status": "ok",
                "inferenceGenerationId": "generation:current",
                "sourceAboxSnapshotId": run.abox_snapshot_id,
                "generationAligned": True,
                "nativeTypeDbReasoningUsed": True,
            },
        }, completed_at="2026-07-20T00:01:10Z")

        self.assertEqual(run.abox_snapshot_id, completed.active_abox_snapshot_id)

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

    def test_mysql_store_filters_projection_runs_by_world(self):
        connection = RecordingConnection(rows=[])
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        store.connect = lambda: ConnectionContext(connection)

        store.latest(world_id="portfolio:tenant-a:main", limit=10)

        self.assertIn("world_id = %s", connection.calls[0][0])
        self.assertEqual("portfolio:tenant-a:main", connection.calls[0][1][0])

    def test_projection_run_rehydrates_mysql_payload(self):
        _snapshot, _graph, _fingerprint, run = self.build_run()
        restored = projection_run_from_payload({
            "runId": run.run_id,
            "portfolioId": run.portfolio_id,
            "accountId": run.account_id,
            "sourceSnapshotAt": run.source_snapshot_at,
            "sourceSnapshotFingerprint": run.source_snapshot_fingerprint,
            "firstObservedAt": run.first_observed_at,
            "lastObservedAt": run.last_observed_at,
            "startedAt": run.started_at,
            "status": "projecting",
            "graphStore": run.graph_store,
            "projectionMode": run.projection_mode,
            "materialFingerprint": run.material_fingerprint,
            "aboxSnapshotId": run.abox_snapshot_id,
            "tboxVersion": run.tbox_version,
            "tboxFingerprint": run.tbox_fingerprint,
            "ruleboxRulesHash": run.rulebox_rules_hash,
            "entityCount": run.entity_count,
            "relationCount": run.relation_count,
            "sourceSymbols": run.source_symbols,
            "context": run.context_payload,
            "result": {},
        })

        self.assertEqual(run.run_id, restored.run_id)
        self.assertEqual(run.source_symbols, restored.source_symbols)
        self.assertEqual("projecting", restored.status)

    def test_recorder_recovers_interrupted_audit_only_from_aligned_typedb_generation(self):
        _snapshot, _graph, _fingerprint, run = self.build_run()
        stored_row = {
            "runId": run.run_id,
            "portfolioId": run.portfolio_id,
            "accountId": run.account_id,
            "sourceSnapshotAt": run.source_snapshot_at,
            "sourceSnapshotFingerprint": run.source_snapshot_fingerprint,
            "firstObservedAt": run.first_observed_at,
            "lastObservedAt": run.last_observed_at,
            "startedAt": run.started_at,
            "status": "projecting",
            "graphStore": "typedb",
            "projectionMode": run.projection_mode,
            "materialFingerprint": run.material_fingerprint,
            "aboxSnapshotId": run.abox_snapshot_id,
            "tboxVersion": run.tbox_version,
            "tboxFingerprint": run.tbox_fingerprint,
            "ruleboxRulesHash": run.rulebox_rules_hash,
            "entityCount": run.entity_count,
            "relationCount": run.relation_count,
            "sourceSymbols": ["005930"],
            "context": run.context_payload,
            "result": {},
        }
        completed = []
        store = SimpleNamespace(
            latest=lambda limit=0: [stored_row],
            complete=lambda item: completed.append(item),
        )
        repository = SimpleNamespace(
            store_key="typedb",
            active_abox_metadata=lambda: {
                "status": "ok",
                "aboxSnapshotId": run.abox_snapshot_id,
                "materialFingerprint": run.material_fingerprint,
                "projectionRunId": run.run_id,
            },
            inferencebox_snapshot=lambda symbols, limit: {
                "status": "ok",
                "nativeTypeDbReasoningUsed": True,
                "generationAligned": True,
                "sourceAboxSnapshotId": run.abox_snapshot_id,
                "targetSymbols": list(symbols),
                "inferenceGenerationId": "inference-generation:recovered",
                "traceCount": 2,
            },
        )
        recorder = PortfolioOntologyProjectionRecorder(repository, projection_run_store=store)

        result = recorder.reconcile_interrupted_projection_audit()

        self.assertEqual("recovered", result["status"])
        self.assertEqual(1, len(completed))
        self.assertEqual("ok", completed[0].status)
        self.assertEqual("inference-generation:recovered", completed[0].inference_generation_id)


if __name__ == "__main__":
    unittest.main()
