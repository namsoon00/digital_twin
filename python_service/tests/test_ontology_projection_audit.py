import unittest
from dataclasses import replace
from types import SimpleNamespace

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_projection_audit import (
    apply_projection_run_identity,
    build_ontology_projection_run,
    compact_reasoning_request_context,
    complete_ontology_projection_run,
    inference_reuse_scope_plan_for_targets,
    inference_reuse_scope_plan_fingerprint,
    projection_analysis_telemetry,
    projection_result_summary,
    projection_run_from_payload,
    projection_source_snapshot,
)
from digital_twin.domain.ontology_projection_fingerprint import (
    apply_material_graph_identity,
    material_graph_fingerprint,
)
from digital_twin.domain.ontology_runtime_operations import (
    build_projection_runtime_observation,
)
from digital_twin.domain.portfolio import AccountSnapshot, DecisionItem, PortfolioSummary, Position
from digital_twin.infrastructure.mysql_ontology_projection_runs import MySQLOntologyProjectionRunStore
from digital_twin.infrastructure.ontology_projection import (
    PortfolioOntologyProjectionRecorder,
    compact_staged_abox_activation_lifecycle,
    shared_inference_from_result_slot_proof,
    shared_premise_evaluation_plan,
)


class Cursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return dict(self.rows[0]) if self.rows else None


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
    def assert_abox_runtime_modes_are_not_coerced_into_numeric_stages(self):
        stages = {}
        result = {
            "aboxPersistenceVerification": {
                "timing": {
                    "currentStateWriteStrategy": "copy-on-write-fresh-generation-v3",
                    "currentStateInventoryReadMs": 0,
                    "changedScopeWritePlan": {
                        "relationWriteMode": "given-batch",
                        "queryCount": 4,
                    },
                },
            },
        }

        PortfolioOntologyProjectionRecorder.attach_abox_persistence_runtime_stages(
            stages,
            result,
        )

        self.assertEqual(0, stages["aboxCurrentStateInventoryReadMs"])
        self.assertEqual(4, stages["aboxChangedScopeQueryCount"])
        self.assertNotIn("aboxCurrentStateWriteStrategy", stages)
        self.assertNotIn("aboxRelationWriteMode", stages)
        self.assertEqual(
            "copy-on-write-fresh-generation-v3",
            result["runtimeModes"]["aboxCurrentStateWriteStrategy"],
        )
        self.assertEqual(
            "given-batch",
            result["runtimeModes"]["aboxRelationWriteMode"],
        )

        observation = build_projection_runtime_observation(
            SimpleNamespace(
                run_id="projection-run-1",
                account_id="main",
                started_at="2026-09-01T00:00:00Z",
                completed_at="2026-09-01T00:00:01Z",
                abox_snapshot_id="abox-1",
                entity_count=0,
                relation_count=0,
            ),
            {**result, "runtimeStages": stages},
        )
        self.assertEqual(
            "copy-on-write-fresh-generation-v3",
            observation["modes"]["aboxCurrentStateWriteStrategy"],
        )
        self.assertNotIn("aboxCurrentStateWriteStrategy", observation["stages"])

    def test_staged_recovery_reuses_the_active_projection_audit_owner(self):
        self.assert_abox_runtime_modes_are_not_coerced_into_numeric_stages()

        class Repository:
            store_key = "typedb"

            @staticmethod
            def active_abox_metadata(world_id=""):
                return {
                    "status": "ok",
                    "worldId": world_id,
                    "aboxSnapshotId": "abox:active",
                    "projectionRunId": "run:active",
                }

        class RunStore:
            @staticmethod
            def latest(limit=20, world_id=""):
                return [{
                    "runId": "run:active",
                    "worldId": world_id,
                    "portfolioId": "main",
                    "accountId": "main",
                    "status": "projecting",
                    "graphStore": "typedb",
                    "aboxSnapshotId": "abox:active",
                }]

        recorder = PortfolioOntologyProjectionRecorder(
            Repository(),
            projection_run_store=RunStore(),
        )

        run = recorder.active_projection_audit_run("portfolio:local:main")

        self.assertIsNotNone(run)
        self.assertEqual("run:active", run.run_id)
        self.assertEqual("abox:active", run.abox_snapshot_id)

    def test_prior_rule_slot_reader_counts_only_executable_unique_rule_ids(self):
        class Repository:
            store_key = "typedb"

        class SlotStore:
            def __init__(self):
                self.request = {}

            def active_rule_result_slot_context(self, **kwargs):
                self.request = dict(kwargs)
                return {"reusable": True, "coverageComplete": True}

        slot_store = SlotStore()
        recorder = PortfolioOntologyProjectionRecorder(
            Repository(),
            projection_run_store=slot_store,
            settings={
                "_reasoningEngineDeploymentId": "ontology-v2-test",
                "_reasoningEngineReleaseFingerprint": "release-test",
                "typedbDatabase": "ontology-test",
            },
        )
        recorder.rulebox_rules_for_impact = lambda: [
            {"rule_id": "graph.rule.one", "enabled": True},
            {"rule_id": "graph.rule.two", "enabled": True},
            {"rule_id": "graph.rule.two", "enabled": True},
            {"kind": "compiler-metadata", "enabled": True},
            {"rule_id": "graph.rule.disabled", "enabled": False},
        ]

        result = recorder.audited_prior_rule_selection_context(
            source_snapshot(),
            ["005930"],
            rulebox_rules_hash="rules-hash",
            tbox_fingerprint="tbox-hash",
            world_id="portfolio:local:main",
        )

        self.assertTrue(result["reusable"])
        self.assertEqual(2, slot_store.request["expected_rule_count"])
        self.assertEqual(
            ["graph.rule.one", "graph.rule.two"],
            slot_store.request["catalog_rule_ids"],
        )

    def test_shared_result_slots_rehydrate_only_the_active_inference_generation(self):
        context = {
            "reusable": True,
            "coverageComplete": True,
            "fullGenerationReusable": True,
            "expectedRuleCount": 2,
            "inferenceGenerationId": "generation:active",
            "sourceAboxSnapshotId": "abox:active",
            "proofRunId": "run:active",
            "ruleStatesBySymbol": {
                "NVDA": {
                    "shared.premise.graph.rule.one": "matched",
                    "shared.premise.graph.rule.two": "not-matched",
                },
            },
        }
        recovery = {
            "status": "ok",
            "inferenceGenerationId": "generation:active",
            "sourceAboxSnapshotId": "abox:active",
            "targetSymbols": ["NVDA"],
            "nativeTypeDbReasoningCompleted": True,
        }

        result = shared_inference_from_result_slot_proof(
            world_id="premise:shared:global",
            active_abox={"aboxSnapshotId": "abox:active"},
            recovery_metadata=recovery,
            selection_context=context,
            symbols=["NVDA"],
        )
        rejected = shared_inference_from_result_slot_proof(
            world_id="premise:shared:global",
            active_abox={"aboxSnapshotId": "abox:newer"},
            recovery_metadata=recovery,
            selection_context=context,
            symbols=["NVDA"],
        )

        self.assertEqual("typedb-result-slot-generation-reuse", result["reasoningMode"])
        self.assertEqual(1, result["traceCount"])
        self.assertEqual(
            "shared.premise.graph.rule.one",
            result["traces"][0]["ruleId"],
        )
        self.assertTrue(result["resultSlotProofReused"])
        self.assertEqual({}, rejected)

    def test_shared_inference_reuse_never_expands_a_predecessor_without_slot_proof(self):
        class Repository:
            store_key = "typedb"

            @staticmethod
            def inferencebox_recovery_metadata(world_id=""):
                return {
                    "status": "ok",
                    "worldId": world_id,
                    "inferenceGenerationId": "generation:previous",
                    "sourceAboxSnapshotId": "abox:active",
                    "targetSymbols": ["NVDA"],
                    "nativeTypeDbReasoningCompleted": True,
                }

            @staticmethod
            def inferencebox_snapshot(**_kwargs):
                raise AssertionError("detailed predecessor inference must not be read")

        recorder = PortfolioOntologyProjectionRecorder(Repository())

        existing, reuse_mode = recorder.compact_shared_inference_reuse(
            active_abox={"aboxSnapshotId": "abox:active"},
            selection_context={"reusable": False},
            symbols=["NVDA"],
            world_id="premise:shared:global",
        )

        self.assertEqual(
            "skipped-missing-compact-result-slot-proof",
            existing["status"],
        )
        self.assertEqual("compact-result-slot-proof-unavailable", reuse_mode)

    EXECUTION_NAMESPACE = {
        "engineDeploymentId": "ontology-v2-shadow",
        "graphDatabase": "orbit_alpha_ontology_shadow_v2",
        "releaseFingerprint": "release-test-1",
        "validationCohortId": "cohort-test-1",
    }

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
            execution_namespace=self.EXECUTION_NAMESPACE,
            started_at="2026-07-20T00:01:05Z",
        )
        return snapshot, graph, fingerprint, run

    def test_reasoning_request_context_keeps_only_selected_symbol_provenance(self):
        context = compact_reasoning_request_context({
            "requestEventIds": ["request-b", "request-a"],
            "sourceEventIds": ["source-a"],
            "triggers": ["market-data-update"],
            "factTypes": ["MarketQuote", "TechnicalIndicator"],
            "requestedScopeFamilies": ["market", "temporal"],
            "requestedScopeFamiliesBySymbol": {
                "005930": ["market"],
                "000660": ["temporal"],
            },
            "targetSymbols": ["005930", "000660"],
            "sourceObservedAt": "2026-07-24T01:00:00Z",
            "changedFieldsBySymbol": {
                "005930": ["price", "volume"],
                "000660": ["price"],
            },
            "factRevisionsBySymbol": {
                "005930": "revision-1",
                "000660": "revision-2",
            },
            "observationFollowupSymbols": ["005930", "000660"],
            "rawFacts": {"must": "not be copied"},
        }, target_symbols=["005930"])

        self.assertEqual(["005930"], context["targetSymbols"])
        self.assertEqual({"005930": ["price", "volume"]}, context["changedFieldsBySymbol"])
        self.assertEqual({"005930": "revision-1"}, context["factRevisionsBySymbol"])
        self.assertEqual({"005930": ["market"]}, context["requestedScopeFamiliesBySymbol"])
        self.assertEqual(["005930"], context["observationFollowupSymbols"])
        self.assertNotIn("rawFacts", context)

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
        self.assertEqual("coherent-rule-result-slot-proof-unavailable", context["fallbackReason"])

    def test_rule_result_slots_reject_a_mixed_generation_even_with_full_row_count(self):
        rows = [
            {
                "symbol": "005930",
                "rule_id": "graph.rule.one",
                "matched": 1,
                "catalog_rule_count": 2,
                "inference_generation_id": "generation:1",
                "source_abox_snapshot_id": "abox:1",
                "source_run_id": "run:1",
                "scope_plan_fingerprint": "scope:1",
                "input_fingerprint": "input:1",
                "execution_namespace_id": "namespace:v2",
            },
            {
                "symbol": "005930",
                "rule_id": "graph.rule.two",
                "matched": 0,
                "catalog_rule_count": 2,
                "inference_generation_id": "generation:2",
                "source_abox_snapshot_id": "abox:2",
                "source_run_id": "run:2",
                "scope_plan_fingerprint": "scope:2",
                "input_fingerprint": "input:2",
                "execution_namespace_id": "namespace:v2",
            },
        ]
        connection = RecordingConnection(rows=rows)
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        store.connect = lambda: ConnectionContext(connection)

        context = store.active_rule_result_slot_context(
            world_id="portfolio:local:main",
            account_id="main",
            symbols=["005930"],
            rulebox_rules_hash="rules:1",
            tbox_fingerprint="tbox:1",
            expected_rule_count=2,
            execution_namespace_id="namespace:v2",
            engine_deployment_id="ontology-v2-shadow",
            graph_database="orbit_alpha_ontology_shadow_v2",
            release_fingerprint="release:2",
        )

        self.assertFalse(context["reusable"])
        self.assertEqual("result-slot-generation-incoherent", context["reason"])
        self.assertIn("execution_namespace_id = %s", connection.calls[0][0])
        self.assertIn("engine_deployment_id = %s", connection.calls[0][0])
        self.assertIn("graph_database = %s", connection.calls[0][0])
        self.assertNotIn("release_fingerprint = %s", connection.calls[0][0])

    def test_incremental_slot_write_inherits_one_generation_and_replaces_executed_rules(self):
        _snapshot, _graph, _fingerprint, run = self.build_run()
        connection = RecordingConnection()
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        result = {
            "status": "ok",
            "ruleboxExecution": {
                "status": "ok",
                "nativeRuleSelectionApplied": True,
                "nativeRuleSelectionFullRuleCount": 2,
                "nativeRuleSelectionExecutedRuleIds": ["graph.rule.two"],
                "typedbNativeRuleMatchedRuleIds": ["graph.rule.two"],
            },
            "inferenceBox": {
                "generationAligned": True,
                "inferenceGenerationId": "generation:current",
                "sourceAboxSnapshotId": run.abox_snapshot_id,
            },
            "inferenceReuseProof": {
                "scopePlanFingerprint": run.context_payload["scopeTopology"]["inferenceReuseScopePlanFingerprint"],
            },
            "_ruleResultSlotCatalogRuleIds": ["graph.rule.one", "graph.rule.two"],
            "_priorRuleStatesBySymbol": {"005930": {
                "graph.rule.one": "matched",
                "graph.rule.two": "not-matched",
            }},
        }
        trace = {
            "inferenceGenerationId": "generation:current",
            "ruleOutcomes": [{
                "ruleId": "graph.rule.two",
                "ruleVersion": "v2",
                "status": "matched",
                "matched": True,
                "matchedTargetSymbols": ["005930"],
            }],
        }

        store._upsert_rule_result_slots_with_connection(
            connection,
            run,
            result,
            trace,
            "2026-08-16T00:00:00Z",
        )

        inserts = [
            params
            for sql, params in connection.calls
            if "INSERT INTO ontology_reasoning_rule_result_slots" in sql
        ]
        self.assertEqual(2, len(inserts))
        self.assertEqual({"graph.rule.one", "graph.rule.two"}, {row[8] for row in inserts})
        self.assertTrue(all(row[14] == 1 for row in inserts))
        self.assertEqual({"generation:current"}, {row[16] for row in inserts})
        self.assertEqual(1, len({row[19] for row in inserts}))

    def test_shared_world_can_persist_a_direct_coherent_result_slot_generation(self):
        connection = RecordingConnection()
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        store.transaction = lambda: ConnectionContext(connection)

        result = store.record_rule_result_slots(
            world_id="premise:shared:us",
            account_id="",
            symbols=["NVDA"],
            catalog_rule_ids=[
                "shared.premise.graph.rule.one",
                "shared.premise.graph.rule.two",
            ],
            rulebox_rules_hash="shared-rules:1",
            tbox_fingerprint="tbox:1",
            scope_plan_fingerprint={
                "fingerprint": "scope:shared:1",
                "scopeManifest": {"symbol:NVDA:market": "large-payload"},
            },
            source_abox_snapshot_id="abox:shared:2",
            source_snapshot_fingerprint="facts:shared:2",
            execution={
                "status": "ok",
                "nativeRuleSelectionApplied": True,
                "nativeRuleSelectionFullRuleCount": 2,
                "nativeRuleSelectionExecutedRuleIds": [
                    "shared.premise.graph.rule.two",
                ],
                "typedbNativeRuleMatchedRuleIds": [
                    "shared.premise.graph.rule.two",
                ],
            },
            inference={
                "status": "ok",
                "generationAligned": True,
                "inferenceGenerationId": "generation:shared:2",
                "sourceAboxSnapshotId": "abox:shared:2",
                "traces": [{
                    "ruleId": "shared.premise.graph.rule.two",
                    "symbol": "NVDA",
                }],
            },
            execution_namespace_id="namespace:v2",
            engine_deployment_id="ontology-v2-production-r14",
            graph_database="orbit_alpha_ontology",
            release_fingerprint="release:r14",
            prior_rule_states_by_symbol={"NVDA": {
                "shared.premise.graph.rule.one": "matched",
                "shared.premise.graph.rule.two": "not-matched",
            }},
            revision_vectors_by_symbol={"NVDA": {"price": "revision:2"}},
        )

        inserts = [
            params
            for sql, params in connection.calls
            if "INSERT INTO ontology_reasoning_rule_result_slots" in sql
        ]
        self.assertEqual("ok", result["status"])
        self.assertEqual(2, result["slotCount"])
        self.assertEqual(2, len(inserts))
        self.assertEqual({"scope:shared:1"}, {row[12] for row in inserts})
        self.assertEqual({"premise:shared:us"}, {row[5] for row in inserts})
        self.assertEqual({""}, {row[6] for row in inserts})
        self.assertEqual({"NVDA"}, {row[7] for row in inserts})
        self.assertEqual({"generation:shared:2"}, {row[16] for row in inserts})
        self.assertTrue(all(row[14] == 1 for row in inserts))

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

    def test_execution_trace_resolves_generation_without_retained_projection_row(self):
        connection = RecordingConnection(rows=[{"run_id": "run:historical"}])
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        store.connect = lambda: ConnectionContext(connection)
        store.execution_trace = lambda **_kwargs: {
            "status": "ok",
            "runCount": 1,
            "runs": [{"runId": "run:historical"}],
        }

        payload = store.execution_trace_for_inference_generation(
            "generation:historical",
            account_id="main",
        )

        self.assertEqual(1, payload["runCount"])
        self.assertEqual("generation:historical", payload["inferenceGenerationId"])
        self.assertIn("ontology_reasoning_run_stages", connection.calls[0][0])
        self.assertNotIn("ontology_projection_runs", connection.calls[0][0])

    def test_mysql_store_recovers_only_stale_audit_rows_for_the_current_world(self):
        connection = RecordingConnection(rows=[])
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        store.runtime_settings = {
            "ontologyReasoningExecutionTimeoutSeconds": "240",
            "ontologyReasoningExecutionTimeoutGraceSeconds": "10",
        }
        store.transaction = lambda: ConnectionContext(connection)

        recovery = store.recover_stale_runs("portfolio:tenant-a:main")

        self.assertEqual(310, recovery["staleAfterSeconds"])
        self.assertEqual("portfolio:tenant-a:main", recovery["worldId"])
        self.assertIn("status = 'projecting'", connection.calls[0][0])
        self.assertIn("world_id = %s", connection.calls[0][0])
        self.assertEqual("portfolio:tenant-a:main", connection.calls[0][1][-1])

    def test_mysql_store_allows_an_explicit_stale_audit_boundary(self):
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        store.runtime_settings = {"ontologyProjectionAuditStaleAfterSeconds": "175"}

        self.assertEqual(175, store.projection_audit_stale_after_seconds())
        indexed_connection = RecordingConnection(rows=[])
        store.connect = lambda: ConnectionContext(indexed_connection)
        self.assertEqual(
            [],
            store.interrupted_projection_recovery_candidates(
                "portfolio:local:main",
                limit=1,
            ),
        )
        recovery_query, recovery_params = indexed_connection.calls[0]
        self.assertIn("updated_at >= %s", recovery_query)
        self.assertIn("world_id = %s", recovery_query)
        self.assertEqual("portfolio:local:main", recovery_params[1])
        self.assertEqual(1, recovery_params[-1])

        failed_connection = RecordingConnection(rows=[{
            "resume_stage": "source-bound",
            "detail_json": "{}",
            "completed_at": "",
            "inference_generation_id": "",
        }])
        failed_store = MySQLOntologyProjectionRunStore.__new__(
            MySQLOntologyProjectionRunStore
        )
        failed_store.transaction = lambda: ConnectionContext(failed_connection)
        failed_transition = failed_store.advance_current_state_transition(
            "projection:failed",
            "source-bound",
            status="failed",
            detail={"reason": "verification failed"},
        )
        self.assertEqual("ok", failed_transition["status"])
        update_params = failed_connection.calls[1][1]
        self.assertEqual("failed", update_params[1])
        self.assertTrue(update_params[4])
        self.assertFalse(any(
            "INSERT INTO ontology_current_state_heads" in sql
            for sql, _params in failed_connection.calls
        ))

        recovery_connection = RecordingConnection(rows=[{
            "run_id": "projection:committed",
            "resume_stage": "synthesis-persisted",
            "inference_generation_id": "generation:committed",
        }])
        recovery_store = MySQLOntologyProjectionRunStore.__new__(
            MySQLOntologyProjectionRunStore
        )
        recovery_store.connect = lambda: ConnectionContext(recovery_connection)
        recovery_advances = []
        recovery_store.advance_current_state_transition = (
            lambda run_id, stage, **kwargs: recovery_advances.append(
                (run_id, stage, kwargs)
            ) or {
                "status": "ok",
                "resumeStage": stage,
                "completed": stage == "completed",
            }
        )
        recovered = recovery_store.recover_committed_current_state_transitions(
            "portfolio:local:main"
        )
        self.assertEqual(["projection:committed"], recovered["recoveredRunIds"])
        self.assertEqual(["completed"], [row[1] for row in recovery_advances])
        self.assertIn("projection.active_abox_snapshot_id", recovery_connection.calls[0][0])

        _snapshot, _graph, _fingerprint, run = self.build_run()
        completed_run = complete_ontology_projection_run(run, {
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "aboxSnapshotId": run.abox_snapshot_id,
            "inferenceBox": {
                "status": "ok",
                "inferenceGenerationId": "generation:committed",
                "sourceAboxSnapshotId": run.abox_snapshot_id,
                "nativeTypeDbReasoningCompleted": True,
            },
        })
        transition_calls = []
        transition_recorder = PortfolioOntologyProjectionRecorder(
            SimpleNamespace(store_key="typedb"),
            projection_run_store=SimpleNamespace(
                advance_current_state_transition=lambda run_id, stage, **kwargs: (
                    transition_calls.append((run_id, stage, kwargs))
                    or {
                        "status": "ok",
                        "resumeStage": stage,
                        "completed": stage == "completed",
                    }
                ),
            ),
        )
        transition_result = {
            "saved": True,
            "currentStateTransition": {"status": "ok"},
            "inferenceBox": {
                "inferenceGenerationId": "generation:committed",
                "nativeTypeDbReasoningCompleted": True,
            },
        }
        finalization = transition_recorder.finalize_current_state_transition(
            completed_run,
            transition_result,
        )
        self.assertEqual("completed", finalization["status"])
        self.assertEqual(
            ["synthesis-persisted", "completed"],
            [row[1] for row in transition_calls],
        )
        self.assertTrue(
            transition_result["currentStateCompletionCheckpoint"]["completed"]
        )

        empty_recorder = PortfolioOntologyProjectionRecorder(
            SimpleNamespace(store_key="typedb"),
            projection_run_store=SimpleNamespace(
                interrupted_projection_recovery_candidates=lambda **_kwargs: [],
            ),
        )
        self.assertFalse(
            empty_recorder.interrupted_projection_recovery_required(
                "portfolio:local:main"
            )
        )
        interrupted_recorder = PortfolioOntologyProjectionRecorder(
            SimpleNamespace(store_key="typedb"),
            projection_run_store=SimpleNamespace(
                interrupted_projection_recovery_candidates=lambda **_kwargs: [
                    {"run_id": "projection:interrupted"}
                ],
            ),
        )
        self.assertTrue(
            interrupted_recorder.interrupted_projection_recovery_required(
                "portfolio:local:main"
            )
        )

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
            "executionNamespaceId": run.execution_namespace_id,
            "engineDeploymentId": run.engine_deployment_id,
            "graphDatabase": run.graph_database,
            "releaseFingerprint": run.release_fingerprint,
            "validationCohortId": run.validation_cohort_id,
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

    def test_recorder_repairs_missing_reuse_proof_for_active_recovered_audit(self):
        _snapshot, _graph, _fingerprint, run = self.build_run()
        scope_plan = [{
            "scopeId": "symbol:005930:market",
            "scopeType": "symbol",
            "scopeFamily": "market",
            "impactScopeFamilies": ["market"],
            "semanticFingerprints": {"market": "market-a"},
            "semanticDependencyFingerprintVersion": "rule-input-v2",
            "semanticDependencyFingerprints": {"field:currentprice": "price-a"},
            "generationId": "market-a",
            "fingerprint": "market-a",
            "baseFingerprint": "market-a",
            "dependencyScopeIds": [],
        }]
        reusable_plan = inference_reuse_scope_plan_for_targets(scope_plan, ["005930"])
        run.context_payload["scopeTopology"] = {
            "inferenceReuseScopePlan": reusable_plan,
            "inferenceReuseScopePlanFingerprint": inference_reuse_scope_plan_fingerprint(reusable_plan),
        }
        stored_row = {
            "runId": run.run_id,
            "portfolioId": run.portfolio_id,
            "accountId": run.account_id,
            "sourceSnapshotAt": run.source_snapshot_at,
            "sourceSnapshotFingerprint": run.source_snapshot_fingerprint,
            "firstObservedAt": run.first_observed_at,
            "lastObservedAt": run.last_observed_at,
            "startedAt": run.started_at,
            "status": "ok",
            "graphStore": "typedb",
            "executionNamespaceId": run.execution_namespace_id,
            "engineDeploymentId": run.engine_deployment_id,
            "graphDatabase": run.graph_database,
            "releaseFingerprint": run.release_fingerprint,
            "validationCohortId": run.validation_cohort_id,
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
                "inferenceGenerationId": "inference-generation:repaired",
                "matchedRuleIds": ["graph.test.market.v1"],
                "traceCount": 1,
            },
        )
        recorder = PortfolioOntologyProjectionRecorder(repository, projection_run_store=store)

        result = recorder.reconcile_interrupted_projection_audit()

        self.assertEqual("reuse-proof-repaired", result["status"])
        self.assertEqual("verified", result["inferenceReuseProof"]["status"])
        self.assertEqual(1, len(completed))
        self.assertEqual(
            "verified",
            completed[0].result_payload["inferenceReuseProof"]["status"],
        )

    def test_recorder_recovers_selected_execution_only_with_complete_durable_ledger(self):
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
            complete=lambda item: completed.append((item, {})),
            complete_with_execution_trace=lambda item, result: completed.append((item, result)),
        )
        inferencebox = {
            "status": "ok",
            "nativeTypeDbReasoningUsed": True,
            "nativeTypeDbReasoningCompleted": True,
            "generationAligned": True,
            "sourceAboxSnapshotId": run.abox_snapshot_id,
            "targetSymbols": ["005930"],
            "inferenceGenerationId": "inference-generation:selected-recovered",
            "nativeRuleSelectionApplied": True,
            "nativeRuleSelectionCandidateCount": 1,
            "nativeRuleSelectionExecutedCount": 2,
            "nativeRuleSelectionDeferredCount": 3,
            "nativeRuleSelectionFullRuleCount": 5,
            "nativeRuleSelectionExecutedRuleIds": ["graph.changed.v1", "graph.prior-match.v1"],
            "nativeRuleSelectionDeferredRuleIds": [
                "graph.unchanged-a.v1",
                "graph.unchanged-b.v1",
                "graph.unchanged-c.v1",
            ],
            "typedbNativeRuleExecutedCount": 2,
            "typedbNativeRuleMatchedCount": 1,
            "typedbNativeRuleMatchedRuleIds": ["graph.changed.v1"],
            "typedbNativeRuleTimingProfile": {
                "executedRuleCount": 2,
                "slowestRules": [{"ruleId": "graph.changed.v1", "elapsedMs": 900}],
            },
            "typedbNativeStageTimings": {"nativeRuleQueryMs": 1100},
        }
        repository = SimpleNamespace(
            store_key="typedb",
            active_abox_metadata=lambda: {
                "status": "ok",
                "aboxSnapshotId": run.abox_snapshot_id,
                "materialFingerprint": run.material_fingerprint,
                "projectionRunId": run.run_id,
            },
            inferencebox_snapshot=lambda symbols, limit: {**inferencebox, "targetSymbols": list(symbols)},
        )
        recorder = PortfolioOntologyProjectionRecorder(repository, projection_run_store=store)

        result = recorder.reconcile_interrupted_projection_audit()

        self.assertEqual("recovered", result["status"])
        self.assertEqual(1, len(completed))
        persisted = completed[0][1]
        self.assertEqual("verified-selected-delta", persisted["nativeReplayValidation"]["status"])
        self.assertTrue(persisted["nativeReplayValidation"]["selectedRuleLedgerComplete"])
        self.assertEqual(5, persisted["ruleboxExecution"]["nativeRuleSelectionFullRuleCount"])
        self.assertEqual(2, persisted["ruleboxExecution"]["typedbNativeRuleExecutedCount"])
        self.assertEqual(
            {"nativeRuleQueryMs": 1100},
            persisted["ruleboxExecution"]["typedbNativeStageTimings"],
        )

    def test_recorder_uses_only_matching_rulebox_audit_timing_for_preemptive_sharding(self):
        snapshot = source_snapshot()
        compatible = {
            "graphStore": "typedb",
            "ruleboxRulesHash": "rulebox-current",
            "result": {
                "runtimeObservation": {
                    "inference": {
                        "nativeRuleTiming": {
                            "slowestRules": [{
                                "ruleId": "graph.timeout-prone.v1",
                                "candidateSymbolCount": 4,
                                "timeoutFallbackUsed": True,
                                "elapsedMs": 38000,
                                "queryDurationMs": 16900,
                            }],
                        },
                    },
                },
            },
        }
        incompatible = {
            **compatible,
            "ruleboxRulesHash": "rulebox-old",
            "result": {
                "runtimeObservation": {
                    "inference": {
                        "nativeRuleTiming": {
                            "slowestRules": [{
                                "ruleId": "graph.old-rule.v1",
                                "candidateSymbolCount": 4,
                                "timeoutFallbackUsed": True,
                                "elapsedMs": 38000,
                                "queryDurationMs": 16900,
                            }],
                        },
                    },
                },
            },
        }
        store = SimpleNamespace(latest=lambda **_kwargs: [compatible, incompatible])
        recorder = PortfolioOntologyProjectionRecorder(
            None,
            projection_run_store=store,
            settings={"typedbNativeRuleAdaptiveTargetShardingLookbackRuns": "12"},
        )

        profile = recorder.adaptive_native_rule_target_sharding_profile(
            snapshot,
            world_id="portfolio:local:main",
            rulebox_rules_hash="rulebox-current",
        )

        self.assertEqual("active", profile["status"])
        self.assertEqual("projection-audit", profile["source"])
        self.assertEqual(1, profile["compatibleAuditRunCount"])
        self.assertEqual(["graph.timeout-prone.v1"], profile["preemptiveRuleIds"])


if __name__ == "__main__":
    unittest.main()
