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
    def test_shared_premise_evaluation_plan_reports_cold_target_full_catalog(self):
        plan = shared_premise_evaluation_plan(
            {
                "reusable": True,
                "coverageComplete": False,
                "incompleteSymbols": ["005930"],
                "coldTargetSymbols": ["005930"],
            },
            {
                "route": "RUN_SHARED",
                "candidateRuleCount": 2,
            },
            104,
        )

        self.assertEqual("full-catalog-cold-target-warmup", plan["mode"])
        self.assertEqual(104, plan["plannedRuleCount"])
        self.assertEqual(2, plan["directChangeCandidateRuleCount"])
        self.assertEqual(["005930"], plan["coldTargetSymbols"])

    def test_shared_premise_evaluation_plan_reports_incremental_selection(self):
        plan = shared_premise_evaluation_plan(
            {
                "reusable": True,
                "coverageComplete": True,
                "incompleteSymbols": [],
                "coldTargetSymbols": [],
            },
            {
                "route": "RUN_SHARED",
                "candidateRuleCount": 2,
            },
            104,
        )

        self.assertEqual("incremental-dependency-selection", plan["mode"])
        self.assertEqual(2, plan["plannedRuleCount"])

    def test_shared_premise_evaluation_plan_reports_complete_reuse(self):
        plan = shared_premise_evaluation_plan(
            {
                "reusable": True,
                "coverageComplete": True,
            },
            {
                "route": "REUSE_SHARED",
                "candidateRuleCount": 0,
                "sharedReuseEligible": True,
            },
            104,
        )

        self.assertEqual("reuse-complete-generation", plan["mode"])
        self.assertEqual(0, plan["plannedRuleCount"])

    def test_shared_premise_preflight_skips_graph_for_account_only_change(self):
        shared_rule_id = "shared.premise.graph.market.price.v1"

        class Repository:
            store_key = "typedb"

            @staticmethod
            def active_abox_metadata(world_id=""):
                return {
                    "status": "ok",
                    "worldId": world_id,
                    "aboxSnapshotId": "abox:active",
                    "scopePlan": [],
                }

            @staticmethod
            def inferencebox_recovery_metadata(world_id=""):
                return {
                    "status": "ok",
                    "worldId": world_id,
                    "inferenceGenerationId": "generation:active",
                    "sourceAboxSnapshotId": "abox:active",
                    "targetSymbols": ["005930"],
                    "nativeTypeDbReasoningCompleted": True,
                }

        slot_store = SimpleNamespace(
            active_rule_result_slot_context=lambda **_kwargs: {
                "reusable": True,
                "proofSource": "typedb-rule-result-slots",
                "expectedRuleCount": 1,
                "matchedRuleIds": [shared_rule_id],
                "ruleStatesBySymbol": {
                    "005930": {shared_rule_id: "matched"},
                },
                "revisionVectorsBySymbol": {
                    "005930": {"fact": "quote:6"},
                },
                "inferenceGenerationId": "generation:active",
                "sourceAboxSnapshotId": "abox:active",
                "proofRunId": "run:active",
            },
        )
        recorder = PortfolioOntologyProjectionRecorder(
            Repository(),
            projection_run_store=slot_store,
            settings={
                "_reasoningEngineVersion": "v2",
                "_reasoningEngineDeploymentId": "ontology-v2-production",
                "typedbDatabase": "ontology-v2",
            },
            frozen_tbox_metadata={
                "version": "tbox-v1",
                "fingerprint": "tbox:1",
            },
        )
        shared_rule = {
            "ruleId": shared_rule_id,
            "enabled": True,
            "conditions": [{
                "conditionId": "price",
                "kind": "subject_property",
                "field": "currentPrice",
            }],
        }
        recorder.ensure_rulebox_ready = lambda: {
            "status": "ready",
            "ruleCount": 1,
            "ruleboxRulesHash": "all-rules:1",
            "runtimeCatalogSource": "frozen-v2-release",
        }
        recorder.world_rule_partition = lambda _catalog: {
            "status": "ready",
            "sharedRules": [shared_rule],
            "sharedRuleIds": [shared_rule_id],
            "sharedRuleCount": 1,
            "overlayRules": [],
            "overlayRuleIds": [],
        }
        recorder.catalog_for_rules = lambda _catalog, _rules: {
            "compiledRuleboxRulesHash": "shared-rules:1",
        }
        recorder.build_graph_assembly = lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                AssertionError("preflight reuse must skip graph assembly")
            )
        )

        result = recorder.prepare_shared_premises(
            source_snapshot(),
            target_symbols=["005930"],
            reasoning_context={
                "eventFactBoundaryAuthoritative": True,
                "eventDependencyBoundaryAuthoritative": True,
                "requestedScopeFamilies": ["position", "portfolio"],
                "requestedScopeFamiliesBySymbol": {
                    "005930": ["position", "portfolio"],
                },
            },
        )

        self.assertTrue(result["ready"])
        self.assertEqual(
            "reused-pre-projection-generation", result["projectionStatus"],
        )
        self.assertEqual(
            "REUSE_SHARED", result["dynamicInferencePreflight"]["route"],
        )
        self.assertEqual(
            ["graph.market.price.v1"],
            result["premisesBySymbol"]["005930"],
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

    def test_inference_symbols_does_not_replace_a_missing_requested_subject(self):
        recorder = PortfolioOntologyProjectionRecorder.__new__(
            PortfolioOntologyProjectionRecorder
        )
        snapshot = source_snapshot()

        self.assertEqual(
            ["005930"],
            recorder.inference_symbols(snapshot, ["005930", "035420"]),
        )
        self.assertEqual([], recorder.inference_symbols(snapshot, ["035420"]))
        self.assertEqual(["005930"], recorder.inference_symbols(snapshot))

    def test_rule_evaluation_namespace_survives_non_semantic_release_change(self):
        repository = SimpleNamespace(store_key="typedb")
        first = PortfolioOntologyProjectionRecorder(repository, settings={
            "_reasoningEngineDeploymentId": "ontology-v2-shadow",
            "typedbDatabase": "orbit_alpha_ontology_shadow_v2",
            "_reasoningEngineReleaseFingerprint": "release:a",
            "_reasoningEngineValidationCohortId": "cohort:a",
            "typedbNativeRuleEngineVersion": "typedb-direct-typeql-rule-engine-v1",
        }).execution_namespace()
        second = PortfolioOntologyProjectionRecorder(repository, settings={
            "_reasoningEngineDeploymentId": "ontology-v2-shadow",
            "typedbDatabase": "orbit_alpha_ontology_shadow_v2",
            "_reasoningEngineReleaseFingerprint": "release:b",
            "_reasoningEngineValidationCohortId": "cohort:b",
            "typedbNativeRuleEngineVersion": "typedb-direct-typeql-rule-engine-v1",
        }).execution_namespace()
        changed_engine = PortfolioOntologyProjectionRecorder(repository, settings={
            "_reasoningEngineDeploymentId": "ontology-v2-shadow",
            "typedbDatabase": "orbit_alpha_ontology_shadow_v2",
            "_reasoningEngineReleaseFingerprint": "release:b",
            "typedbNativeRuleEngineVersion": "typedb-direct-typeql-rule-engine-v2",
        }).execution_namespace()

        self.assertEqual(first["executionNamespaceId"], second["executionNamespaceId"])
        self.assertNotEqual(first["releaseFingerprint"], second["releaseFingerprint"])
        self.assertNotEqual(
            first["executionNamespaceId"],
            changed_engine["executionNamespaceId"],
        )

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

    def test_projection_analysis_telemetry_distinguishes_complete_and_missing_execution_data(self):
        complete = projection_analysis_telemetry({
            "runtimeStages": {"nativeInferenceMs": 1250},
            "ruleboxExecution": {
                "nativeInferenceEvaluationComplete": True,
                "nativeRuleSelectionApplied": True,
                "nativeRuleSelectionCandidateCount": 1,
                "nativeRuleSelectionExecutedCount": 2,
                "nativeRuleSelectionDeferredCount": 3,
                "nativeRuleSelectionFullRuleCount": 5,
                "typedbNativeStageTimings": {"nativeRuleQueryMs": 1100},
                "typedbNativeRuleTimingProfile": {
                    "executedRuleCount": 2,
                    "slowestRules": [{"ruleId": "graph.test.v1", "elapsedMs": 800}],
                },
            },
            "nativeReplayValidation": {
                "status": "verified-selected-delta",
                "verified": True,
            },
        })
        missing = projection_analysis_telemetry({
            "ruleboxExecution": {
                "nativeInferenceEvaluationComplete": True,
                "nativeRuleSelectionApplied": True,
                "nativeRuleSelectionCandidateCount": 1,
                "nativeRuleSelectionExecutedCount": 1,
                "nativeRuleSelectionDeferredCount": 0,
            },
        })

        self.assertTrue(complete["complete"])
        self.assertEqual("complete", complete["executionLedgerStatus"])
        self.assertEqual("complete", complete["stageTimingStatus"])
        self.assertEqual("complete", complete["ruleTimingStatus"])
        self.assertFalse(missing["complete"])
        self.assertEqual("incomplete", missing["executionLedgerStatus"])
        self.assertIn("nativeRuleSelectionFullRuleCount", missing["missingFields"])
        self.assertIn("completeSelectedDeferredLedger", missing["missingFields"])
        self.assertIn("runtimeStages", missing["missingFields"])
        self.assertIn("typedbNativeStageTimings", missing["missingFields"])
        self.assertIn("perRuleTiming", missing["missingFields"])

    def test_projection_result_summary_keeps_native_rule_failure_context(self):
        summary = projection_result_summary({
            "status": "inference-failed-rolled-back",
            "nativeRuleFailure": {
                "version": "typedb-native-rule-failure-v1",
                "stage": "native-rule-query",
                "status": "query-timeout",
                "executionStatus": "error",
                "reasonCode": "typedbNativeRuleQueryTimeout",
                "ruleId": "graph.price.reclaim.thesis_support.v1",
                "blockingRuleStatus": "query-timeout",
                "targetSymbols": ["MSTR", "035420"],
                "queryMode": "typedb-manifest-evidence-index",
                "retryable": True,
                "recommendedRetryAfterSeconds": 30,
                "reason": "Blocking rule timed out.",
            },
        })

        failure = summary["nativeRuleFailure"]
        self.assertEqual("query-timeout", failure["status"])
        self.assertEqual("graph.price.reclaim.thesis_support.v1", failure["ruleId"])
        self.assertEqual(["035420", "MSTR"], failure["targetSymbols"])
        self.assertEqual(30, failure["recommendedRetryAfterSeconds"])

    def test_projection_result_summary_keeps_bounded_model_signal_bridge_execution(self):
        summary = projection_result_summary({
            "ruleboxExecution": {
                "status": "ok",
                "modelSignalBridgeExecution": {
                    "status": "ok",
                    "logicalModelSignalPolicyCount": 74,
                    "batchedSimplePolicyCount": 59,
                    "constrainedPolicyCount": 15,
                    "modelSignalBridgeReadCount": 3,
                    "eliminatedModelSignalPolicyQueryCount": 56,
                    "ignoredContractIds": ["unknown-contract"],
                    "subjectCount": 1,
                    "rawRows": [{"large": "payload"}],
                },
            },
        })

        execution = summary["ruleboxExecution"]["modelSignalBridgeExecution"]
        self.assertEqual(74, execution["logicalModelSignalPolicyCount"])
        self.assertEqual(3, execution["modelSignalBridgeReadCount"])
        self.assertEqual(56, execution["eliminatedModelSignalPolicyQueryCount"])
        self.assertNotIn("rawRows", execution)

    def test_projection_result_summary_keeps_relation_write_breakdown(self):
        summary = projection_result_summary({
            "relationPersistence": {
                "version": "scoped-abox-relation-persistence-v1",
                "requested": {
                    "relationCount": 3,
                    "byRelationType": {"distinctCount": 1, "items": [{"key": "HAS_PRICE", "count": 3}], "remainingCount": 0},
                    "byScopeFamily": {"distinctCount": 1, "items": [{"key": "market", "count": 3}], "remainingCount": 0},
                    "bySymbol": {"distinctCount": 1, "items": [{"key": "005930", "count": 3}], "remainingCount": 0},
                    "byScope": {"distinctCount": 1, "items": [{"key": "link:symbol:005930:market", "count": 3}], "remainingCount": 0},
                },
            },
        })

        metrics = summary["relationPersistence"]
        self.assertEqual("scoped-abox-relation-persistence-v1", metrics["version"])
        self.assertEqual(3, metrics["requested"]["relationCount"])
        self.assertEqual("market", metrics["requested"]["byScopeFamily"]["items"][0]["key"])

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

    def test_run_keeps_source_payload_out_of_recursive_projection_state(self):
        snapshot, graph, fingerprint, run = self.build_run()

        source = projection_source_snapshot(snapshot)

        self.assertNotIn("previousMonitorState", source["metadata"])
        self.assertNotIn("monitorStateHistory", source["metadata"])
        self.assertNotIn("ontology", source["metadata"])
        self.assertNotIn("decisions", source)
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

    def test_projection_source_excludes_derived_decision_output(self):
        snapshot = source_snapshot()
        snapshot.decisions = [DecisionItem(
            symbol="005930",
            name="삼성전자",
            sector="반도체",
            market="KR",
            currency="KRW",
            market_value=700000,
            profit_loss=0,
            profit_loss_rate=0,
            decision="보유",
            tone="neutral",
            relation_rule_context={"activeRules": [{"ruleId": "graph.test"}]},
            active_investment_opinion={"thesis": "derived output"},
            ai_context={"prompt": "derived output"},
        )]

        source = projection_source_snapshot(snapshot)

        self.assertNotIn("decisions", source)

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

    def test_target_reuse_scope_plan_excludes_non_target_storage_dependencies(self):
        plan = [
            {
                "scopeId": "symbol:005930:link",
                "scopeType": "symbol",
                "scopeFamily": "link",
                "impactScopeFamilies": ["evidence", "link"],
                "semanticFingerprints": {"evidence": "same-relation"},
                "generationId": "target-link",
                "dependencyScopeIds": [
                    "reference:global",
                    "symbol:005930:market",
                    "symbol:000660:state",
                ],
            },
            {
                "scopeId": "symbol:005930:market",
                "scopeType": "symbol",
                "scopeFamily": "market",
                "semanticFingerprints": {"market": "target-price"},
                "generationId": "target-market",
                "dependencyScopeIds": [],
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
            [
                "reference:global",
                "symbol:005930:link",
                "symbol:005930:market",
            ],
            [item["scopeId"] for item in selected],
        )

    def test_target_reuse_scope_plan_keeps_family_link_for_the_target(self):
        plan = [
            {
                "scopeId": "link:symbol:005930:market",
                "scopeType": "link",
                "scopeFamily": "market",
                "impactScopeFamilies": ["market"],
                "semanticFingerprints": {"market": "price-link"},
                "generationId": "target-market-link",
                "dependencyScopeIds": ["symbol:005930:market", "symbol:000660:state"],
            },
            {
                "scopeId": "symbol:005930:market",
                "scopeType": "symbol",
                "scopeFamily": "market",
                "semanticFingerprints": {"market": "target-price"},
                "generationId": "target-market",
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
            ["link:symbol:005930:market", "symbol:005930:market"],
            [item["scopeId"] for item in selected],
        )

    def test_recorder_uses_audited_target_scope_proof_when_active_inference_is_stale(self):
        class ReuseRepository:
            store_key = "typedb"

            def __init__(self):
                self.active_read_count = 0
                self.inference_read_count = 0

            def active_abox_metadata(self):
                self.active_read_count += 1
                return {"status": "ok", "aboxSnapshotId": "abox:current"}

            def inferencebox_snapshot(self, _symbols=None, _limit=0):
                self.inference_read_count += 1
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
        audit_store = SimpleNamespace(
            active_rule_result_slot_context=lambda **_kwargs: {
                "reusable": True,
                "proofSource": "typedb-rule-result-slots",
                "matchedRuleIds": ["flow-rule"],
                "matchedRuleCount": 1,
                "ruleStatesBySymbol": {"005930": {
                    "market-rule": "not-matched",
                    "flow-rule": "matched",
                    "quality-rule": "not-matched",
                }},
                "proofRunId": "projection:prior",
                "inferenceGenerationId": "inference:prior",
                "sourceAboxSnapshotId": "abox:prior",
            },
            latest=lambda **_kwargs: [{
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
            }],
        )
        repository = ReuseRepository()
        recorder = PortfolioOntologyProjectionRecorder(repository, projection_run_store=audit_store)
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
        self.assertEqual("typedb-rule-result-slots", context["proofSource"])
        self.assertEqual("projection:prior", context["proofRunId"])
        self.assertEqual(["flow-rule"], context["matchedRuleIds"])
        self.assertEqual(0, repository.active_read_count)
        self.assertEqual(0, repository.inference_read_count)

    def test_recorder_combines_independent_audited_proofs_for_a_multi_symbol_batch(self):
        class ReuseRepository:
            store_key = "typedb"

            def active_abox_metadata(self):
                return {"status": "ok", "aboxSnapshotId": "abox:current"}

            def inferencebox_snapshot(self, _symbols=None, _limit=0):
                return {"status": "stale-generation"}

        def scope(symbol, generation):
            return {
                "scopeId": "symbol:" + symbol + ":market",
                "scopeType": "symbol",
                "scopeFamily": "market",
                "impactScopeFamilies": ["market"],
                "semanticFingerprints": {"market": generation},
                "generationId": generation,
                "fingerprint": generation,
                "baseFingerprint": generation,
                "dependencyScopeIds": [],
            }

        prior_a = [scope("005930", "market-a")]
        prior_b = [scope("000660", "market-a")]
        candidate_scope_plan = [
            scope("005930", "market-b"),
            scope("000660", "market-b"),
        ]

        def audit_row(symbol, prior_scope_plan, run_id):
            fingerprint = inference_reuse_scope_plan_fingerprint(prior_scope_plan)
            return {
                "runId": run_id,
                "status": "ok",
                "graphStore": "typedb",
                "sourceSymbols": [symbol],
                "aboxSnapshotId": "abox:" + symbol,
                "activeAboxSnapshotId": "abox:" + symbol,
                "context": {"scopeTopology": {
                    "inferenceReuseScopePlan": prior_scope_plan,
                    "inferenceReuseScopePlanFingerprint": fingerprint,
                }},
                "result": {"inferenceReuseProof": {
                    "status": "verified",
                    "coverageComplete": True,
                    "sourceAboxSnapshotId": "abox:" + symbol,
                    "inferenceGenerationId": "inference:" + symbol,
                    "targetSymbols": [symbol],
                    "matchedRuleIds": ["flow-rule"],
                    "ruleboxRulesHash": "rulebox-current",
                    "tboxFingerprint": "tbox-current",
                    "scopePlanFingerprint": fingerprint,
                }},
            }

        audit_store = SimpleNamespace(
            active_rule_result_slot_context=lambda **_kwargs: {
                "reusable": True,
                "proofSource": "typedb-rule-result-slots",
                "matchedRuleIds": ["flow-rule"],
                "matchedRuleCount": 1,
                "reusedTargetSymbols": ["000660", "005930"],
                "ruleStatesBySymbol": {
                    symbol: {
                        "market-rule": "not-matched",
                        "flow-rule": "matched",
                        "quality-rule": "not-matched",
                    }
                    for symbol in ["000660", "005930"]
                },
            },
            latest=lambda **_kwargs: [
                audit_row("005930", prior_a, "projection:005930"),
                audit_row("000660", prior_b, "projection:000660"),
            ],
        )
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
            ["005930", "000660"],
            candidate_scope_plan=candidate_scope_plan,
            rulebox_rules_hash="rulebox-current",
            tbox_fingerprint="tbox-current",
        )

        self.assertTrue(context["reusable"])
        self.assertEqual("typedb-rule-result-slots", context["proofSource"])
        self.assertEqual(["000660", "005930"], context["reusedTargetSymbols"])
        self.assertEqual(["flow-rule"], context["matchedRuleIds"])

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
            execution_namespace=self.EXECUTION_NAMESPACE,
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
                "typedbNativeManifestEvidencePreflightEnabled": True,
                "typedbNativeRelationEvidencePreflightEnabled": True,
                "typedbNativeManifestEvidencePreflightPrunedSymbolCount": 3,
                "typedbNativeRuleParallelism": 4,
                "typedbNativeRuleParallelUsed": True,
                "typedbNativeRuleSubjectRuleParallelism": 2,
                "typedbNativeRuleTotalReadParallelismCap": 4,
                "typedbNativeRuleEffectiveTotalReadParallelism": 4,
                "nativeRuleSelectionApplied": False,
                "typedbNativeRuleTimingProfile": {
                    "wallClockMs": 8200,
                    "executedRuleCount": 1,
                    "incompleteRuleCount": 0,
                    "aggregateRuleElapsedMs": 8100,
                    "aggregateQueryDurationMs": 7900,
                    "slowestRules": [{
                        "ruleId": "market-rule",
                        "elapsedMs": 8100,
                        "queryDurationMs": 7900,
                    }],
                },
            },
        }

        PortfolioOntologyProjectionRecorder(SimpleNamespace(store_key="typedb")).attach_inference_reuse_proof(run, result)
        completed = complete_ontology_projection_run(run, result)

        self.assertEqual("verified", result["inferenceReuseProof"]["status"])
        self.assertTrue(result["inferenceReuseProof"]["coverageComplete"])
        self.assertEqual(["market-rule"], result["inferenceReuseProof"]["matchedRuleIds"])
        self.assertEqual(4, completed.result_payload["ruleboxExecution"]["typedbNativeRuleParallelism"])
        self.assertTrue(
            completed.result_payload["ruleboxExecution"]["typedbNativeManifestEvidencePreflightEnabled"]
        )
        self.assertTrue(
            completed.result_payload["ruleboxExecution"]["typedbNativeRelationEvidencePreflightEnabled"]
        )
        self.assertEqual(
            3,
            completed.result_payload["ruleboxExecution"]["typedbNativeManifestEvidencePreflightPrunedSymbolCount"],
        )
        self.assertTrue(completed.result_payload["ruleboxExecution"]["typedbNativeRuleParallelUsed"])
        self.assertEqual(
            2,
            completed.result_payload["ruleboxExecution"]["typedbNativeRuleSubjectRuleParallelism"],
        )
        self.assertEqual(
            4,
            completed.result_payload["ruleboxExecution"]["typedbNativeRuleTotalReadParallelismCap"],
        )
        self.assertEqual(
            4,
            completed.result_payload["ruleboxExecution"]["typedbNativeRuleEffectiveTotalReadParallelism"],
        )
        self.assertEqual(
            "market-rule",
            completed.result_payload["ruleboxExecution"]["nativeRuleTiming"]["slowestRules"][0]["ruleId"],
        )
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

    def test_mysql_store_commits_projection_and_normalized_execution_trace_together(self):
        _snapshot, _graph, _fingerprint, run = self.build_run()
        connection = RecordingConnection()
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        store.runtime_settings = {
            "notificationAiGateEnabled": "0",
            "notificationAiQueueWorkerCount": "0",
        }
        store.transaction = lambda: ConnectionContext(connection)
        result = {
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "aboxSnapshotId": run.abox_snapshot_id,
            "ruleboxExecution": {
                "status": "ok",
                "nativeRuleSelectionApplied": True,
                "nativeRuleSelectionFullRuleCount": 1,
                "nativeRuleSelectionExecutedRuleIds": ["graph.test.core.v1"],
                "nativeMatchResult": {
                    "status": "ok",
                    "matches": [{"ruleId": "graph.test.core.v1"}],
                    "executedRules": [{
                        "ruleId": "graph.test.core.v1",
                        "status": "ok",
                        "executionStage": "core",
                        "elapsedMs": 25,
                        "queryDurationMs": 20,
                    }],
                },
            },
            "inferenceBox": {
                "status": "ok",
                "inferenceGenerationId": "generation:trace",
                "sourceAboxSnapshotId": run.abox_snapshot_id,
                "generationAligned": True,
            },
            "inferenceReuseProof": {
                "scopePlanFingerprint": run.context_payload["scopeTopology"]["inferenceReuseScopePlanFingerprint"],
            },
            "_ruleResultSlotCatalogRuleIds": ["graph.test.core.v1"],
            "_priorRuleStatesBySymbol": {
                "005930": {"graph.test.core.v1": "not-matched"},
            },
        }
        completed = complete_ontology_projection_run(
            run,
            result,
            completed_at="2026-07-20T00:01:10Z",
        )

        store.complete_with_execution_trace(completed, result)

        statements = [sql for sql, _params in connection.calls]
        self.assertTrue(any("UPDATE ontology_projection_runs" in sql for sql in statements))
        self.assertTrue(any("DELETE FROM ontology_reasoning_run_stages" in sql for sql in statements))
        self.assertTrue(any("INSERT INTO ontology_reasoning_run_stages" in sql for sql in statements))
        self.assertTrue(any("INSERT INTO ontology_reasoning_rule_runs" in sql for sql in statements))
        self.assertTrue(any("INSERT INTO ontology_reasoning_rule_result_slots" in sql for sql in statements))
        rule_call = next(
            item for item in connection.calls
            if "INSERT INTO ontology_reasoning_rule_runs" in item[0]
        )
        self.assertEqual(run.run_id, rule_call[1][0])
        self.assertEqual("generation:trace", rule_call[1][5])
        self.assertEqual("graph.test.core.v1", rule_call[1][8])
        self.assertEqual("matched", rule_call[1][10])
        slot_call = next(
            item for item in connection.calls
            if "INSERT INTO ontology_reasoning_rule_result_slots" in item[0]
        )
        self.assertEqual(run.execution_namespace_id, slot_call[1][0])
        self.assertEqual(run.world_id, slot_call[1][5])
        self.assertEqual("005930", slot_call[1][7])
        self.assertEqual("graph.test.core.v1", slot_call[1][8])
        self.assertEqual(1, slot_call[1][14])

    def test_rule_result_slots_are_reusable_only_with_complete_catalog_coverage(self):
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
                "execution_namespace_id": "namespace:1",
            },
            {
                "symbol": "005930",
                "rule_id": "graph.rule.two",
                "matched": 0,
                "catalog_rule_count": 2,
                "inference_generation_id": "generation:1",
                "source_abox_snapshot_id": "abox:1",
                "source_run_id": "run:1",
                "scope_plan_fingerprint": "scope:1",
                "input_fingerprint": "input:1",
                "execution_namespace_id": "namespace:1",
            },
        ]
        connection = RecordingConnection(rows=rows)
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        store.connect = lambda: ConnectionContext(connection)

        complete = store.active_rule_result_slot_context(
            world_id="portfolio:local:main",
            account_id="main",
            symbols=["005930"],
            rulebox_rules_hash="rules:1",
            tbox_fingerprint="tbox:1",
            expected_rule_count=2,
            execution_namespace_id="namespace:1",
        )
        incomplete = store.active_rule_result_slot_context(
            world_id="portfolio:local:main",
            account_id="main",
            symbols=["005930"],
            rulebox_rules_hash="rules:1",
            tbox_fingerprint="tbox:1",
            expected_rule_count=3,
            execution_namespace_id="namespace:1",
        )

        self.assertTrue(complete["reusable"])
        self.assertEqual(["graph.rule.one"], complete["matchedRuleIds"])
        self.assertEqual({
            "graph.rule.one": "matched",
            "graph.rule.two": "not-matched",
        }, complete["ruleStatesBySymbol"]["005930"])
        self.assertFalse(incomplete["reusable"])
        self.assertEqual(["005930"], incomplete["incompleteSymbols"])

        summary = store.rule_result_slot_summary(
            world_id="portfolio:local:main",
            account_id="main",
            symbols=["005930"],
        )
        self.assertEqual(2, summary["slotCount"])
        self.assertEqual(1, summary["completeSymbolCount"])
        self.assertEqual(["graph.rule.one"], summary["symbols"][0]["matchedRuleIds"])

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

    def test_rule_result_slots_reuse_warm_symbols_and_backfill_cold_symbols(self):
        rows = [
            {
                "symbol": "AAPL",
                "rule_id": rule_id,
                "matched": int(rule_id.endswith("one")),
                "catalog_rule_count": 2,
                "inference_generation_id": "generation:1",
                "source_abox_snapshot_id": "abox:1",
                "source_run_id": "run:1",
                "scope_plan_fingerprint": "scope:1",
                "input_fingerprint": "input:1",
                "execution_namespace_id": "namespace:v2",
                "revision_vector_json": '{"market-observation":"quote:7"}',
            }
            for rule_id in ["graph.rule.one", "graph.rule.two"]
        ]
        connection = RecordingConnection(rows=rows)
        store = MySQLOntologyProjectionRunStore.__new__(
            MySQLOntologyProjectionRunStore
        )
        store.connect = lambda: ConnectionContext(connection)

        context = store.active_rule_result_slot_context(
            world_id="premise:shared:global",
            account_id="",
            symbols=["AAPL", "MSTR"],
            rulebox_rules_hash="rules:1",
            tbox_fingerprint="tbox:1",
            expected_rule_count=2,
            execution_namespace_id="namespace:v2",
            catalog_rule_ids=["graph.rule.one", "graph.rule.two"],
        )

        self.assertTrue(context["reusable"])
        self.assertTrue(context["selectionReusable"])
        self.assertFalse(context["fullGenerationReusable"])
        self.assertFalse(context["coverageComplete"])
        self.assertTrue(context["partialCatalogProof"])
        self.assertEqual(["AAPL"], context["reusedTargetSymbols"])
        self.assertEqual(["MSTR"], context["coldTargetSymbols"])
        self.assertEqual(
            ["graph.rule.one", "graph.rule.two"],
            context["missingRuleIdsBySymbol"]["MSTR"],
        )

    def test_partial_slot_proof_cannot_rehydrate_a_full_generation(self):
        result = shared_inference_from_result_slot_proof(
            world_id="premise:shared:global",
            active_abox={"aboxSnapshotId": "abox:1"},
            recovery_metadata={
                "status": "ok",
                "inferenceGenerationId": "generation:1",
                "sourceAboxSnapshotId": "abox:1",
                "targetSymbols": ["AAPL", "MSTR"],
                "nativeTypeDbReasoningCompleted": True,
            },
            selection_context={
                "reusable": True,
                "coverageComplete": False,
                "partialCatalogProof": True,
                "fullGenerationReusable": False,
                "expectedRuleCount": 2,
                "inferenceGenerationId": "generation:1",
                "sourceAboxSnapshotId": "abox:1",
                "ruleStatesBySymbol": {
                    "AAPL": {"graph.rule.one": "matched", "graph.rule.two": "not-matched"},
                    "MSTR": {},
                },
            },
            symbols=["AAPL", "MSTR"],
        )

        self.assertEqual({}, result)

    def test_rule_result_slots_return_one_coherent_revision_vector(self):
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
                "revision_vector_json": '{"fact":"quote:7","source":"clock:1"}',
            },
            {
                "symbol": "005930",
                "rule_id": "graph.rule.two",
                "matched": 0,
                "catalog_rule_count": 2,
                "inference_generation_id": "generation:1",
                "source_abox_snapshot_id": "abox:1",
                "source_run_id": "run:1",
                "scope_plan_fingerprint": "scope:1",
                "input_fingerprint": "input:1",
                "execution_namespace_id": "namespace:v2",
                "revision_vector_json": '{"source":"clock:1","fact":"quote:7"}',
            },
        ]
        connection = RecordingConnection(rows=rows)
        store = MySQLOntologyProjectionRunStore.__new__(
            MySQLOntologyProjectionRunStore
        )
        store.connect = lambda: ConnectionContext(connection)

        context = store.active_rule_result_slot_context(
            world_id="shared-premise:kr",
            account_id="",
            symbols=["005930"],
            rulebox_rules_hash="rules:1",
            tbox_fingerprint="tbox:1",
            expected_rule_count=2,
            execution_namespace_id="namespace:v2",
        )

        self.assertTrue(context["reusable"])
        self.assertEqual(
            {"005930": {"fact": "quote:7", "source": "clock:1"}},
            context["revisionVectorsBySymbol"],
        )
        self.assertIn("revision_vector_json", connection.calls[0][0])

    def test_rule_result_slots_reject_mixed_revision_vectors(self):
        rows = []
        for rule_id, revision in [
            ("graph.rule.one", "quote:7"),
            ("graph.rule.two", "quote:8"),
        ]:
            rows.append({
                "symbol": "005930",
                "rule_id": rule_id,
                "matched": int(rule_id.endswith("one")),
                "catalog_rule_count": 2,
                "inference_generation_id": "generation:1",
                "source_abox_snapshot_id": "abox:1",
                "source_run_id": "run:1",
                "scope_plan_fingerprint": "scope:1",
                "input_fingerprint": "input:1",
                "execution_namespace_id": "namespace:v2",
                "revision_vector_json": '{"fact":"' + revision + '"}',
            })
        connection = RecordingConnection(rows=rows)
        store = MySQLOntologyProjectionRunStore.__new__(
            MySQLOntologyProjectionRunStore
        )
        store.connect = lambda: ConnectionContext(connection)

        context = store.active_rule_result_slot_context(
            world_id="shared-premise:kr",
            account_id="",
            symbols=["005930"],
            rulebox_rules_hash="rules:1",
            tbox_fingerprint="tbox:1",
            expected_rule_count=2,
            execution_namespace_id="namespace:v2",
        )

        self.assertFalse(context["reusable"])
        self.assertEqual(
            "result-slot-revision-vector-incoherent", context["reason"],
        )

    def test_rule_result_slots_do_not_prove_partial_revision_coverage(self):
        rows = []
        for rule_id, revision_json in [
            ("graph.rule.one", '{"fact":"quote:7"}'),
            ("graph.rule.two", ""),
        ]:
            rows.append({
                "symbol": "005930",
                "rule_id": rule_id,
                "matched": 0,
                "catalog_rule_count": 2,
                "inference_generation_id": "generation:1",
                "source_abox_snapshot_id": "abox:1",
                "source_run_id": "run:1",
                "scope_plan_fingerprint": "scope:1",
                "input_fingerprint": "input:1",
                "execution_namespace_id": "namespace:v2",
                "revision_vector_json": revision_json,
            })
        connection = RecordingConnection(rows=rows)
        store = MySQLOntologyProjectionRunStore.__new__(
            MySQLOntologyProjectionRunStore
        )
        store.connect = lambda: ConnectionContext(connection)

        context = store.active_rule_result_slot_context(
            world_id="shared-premise:kr",
            account_id="",
            symbols=["005930"],
            rulebox_rules_hash="rules:1",
            tbox_fingerprint="tbox:1",
            expected_rule_count=2,
            execution_namespace_id="namespace:v2",
        )

        self.assertTrue(context["reusable"])
        self.assertEqual({}, context["revisionVectorsBySymbol"])
        self.assertFalse(
            context["revisionVectorCoverageCompleteBySymbol"]["005930"]
        )

    def test_rule_result_slot_summary_keeps_shared_and_portfolio_catalogues_separate(self):
        rows = []
        for world_id, account_id, rules_hash, rule_count in [
            ("shared-premise:kr", "", "shared-rules", 2),
            ("portfolio:local:main", "main", "portfolio-rules", 3),
        ]:
            for index in range(rule_count):
                rows.append({
                    "execution_namespace_id": "namespace:v2",
                    "engine_deployment_id": "ontology-v2-production-r38",
                    "graph_database": "orbit_alpha_ontology_blue",
                    "release_fingerprint": "release:38",
                    "world_id": world_id,
                    "account_id": account_id,
                    "symbol": "005930",
                    "rule_id": f"{rules_hash}.{index}",
                    "rule_version": "1",
                    "rulebox_rules_hash": rules_hash,
                    "tbox_fingerprint": "tbox:1",
                    "result_state": "matched" if index == 0 else "not-matched",
                    "matched": 1 if index == 0 else 0,
                    "catalog_rule_count": rule_count,
                    "inference_generation_id": f"generation:{rules_hash}",
                    "source_abox_snapshot_id": f"abox:{rules_hash}",
                    "source_run_id": f"run:{rules_hash}",
                    "scope_plan_fingerprint": f"scope:{rules_hash}",
                    "input_fingerprint": f"input:{rules_hash}",
                    "revision_vector_json": "{}",
                    "updated_at": "2026-08-24T00:00:00Z",
                })
        connection = RecordingConnection(rows=rows)
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        store.connect = lambda: ConnectionContext(connection)

        summary = store.rule_result_slot_summary(symbols=["005930"])

        self.assertEqual(5, summary["slotCount"])
        self.assertEqual(2, summary["symbolCount"])
        self.assertEqual(2, summary["completeSymbolCount"])
        self.assertEqual(
            {"SharedPremiseWorld", "PortfolioWorld"},
            {item["worldType"] for item in summary["symbols"]},
        )
        self.assertEqual(
            {2, 3},
            {item["coveredRuleCount"] for item in summary["symbols"]},
        )

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

    def test_incremental_slot_write_backfills_a_cold_symbol_when_all_missing_rules_execute(self):
        _snapshot, _graph, _fingerprint, base_run = self.build_run()
        run = replace(base_run, source_symbols=["AAPL", "MSTR"])
        connection = RecordingConnection()
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        result = {
            "status": "ok",
            "ruleboxExecution": {
                "status": "ok",
                "nativeRuleSelectionApplied": True,
                "nativeRuleSelectionFullRuleCount": 2,
                "nativeRuleSelectionExecutedRuleIds": ["graph.rule.one", "graph.rule.two"],
                "typedbNativeRuleMatchedRuleIds": ["graph.rule.one"],
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
            "_priorRuleStatesBySymbol": {
                "AAPL": {"graph.rule.one": "matched", "graph.rule.two": "not-matched"},
                "MSTR": {},
            },
        }
        trace = {
            "inferenceGenerationId": "generation:current",
            "ruleOutcomes": [{
                "ruleId": "graph.rule.one",
                "matched": True,
                "matchedTargetSymbols": ["MSTR"],
                "matchIdentityComplete": True,
            }, {
                "ruleId": "graph.rule.two",
                "matched": False,
                "matchedTargetSymbols": [],
                "matchIdentityComplete": True,
            }],
        }

        store._upsert_rule_result_slots_with_connection(
            connection,
            run,
            result,
            trace,
            "2026-08-26T00:00:00Z",
        )

        inserts = [
            params
            for sql, params in connection.calls
            if "INSERT INTO ontology_reasoning_rule_result_slots" in sql
        ]
        self.assertEqual(4, len(inserts))
        self.assertEqual({"AAPL", "MSTR"}, {row[7] for row in inserts})
        self.assertEqual({"graph.rule.one", "graph.rule.two"}, {row[8] for row in inserts})

    def test_multi_symbol_aggregate_match_cannot_create_false_complete_slots(self):
        _snapshot, _graph, _fingerprint, base_run = self.build_run()
        run = replace(base_run, source_symbols=["005930", "000660"])
        connection = RecordingConnection()
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        result = {
            "status": "ok",
            "ruleboxExecution": {
                "status": "ok",
                "nativeRuleSelectionApplied": False,
                "nativeRuleSelectionFullRuleCount": 1,
                "typedbNativeRuleMatchedRuleIds": ["graph.rule.one"],
            },
            "inferenceBox": {
                "generationAligned": True,
                "inferenceGenerationId": "generation:aggregate",
                "sourceAboxSnapshotId": run.abox_snapshot_id,
            },
            "inferenceReuseProof": {
                "scopePlanFingerprint": run.context_payload["scopeTopology"]["inferenceReuseScopePlanFingerprint"],
            },
            "_ruleResultSlotCatalogRuleIds": ["graph.rule.one"],
        }
        trace = {
            "inferenceGenerationId": "generation:aggregate",
            "ruleOutcomes": [{
                "ruleId": "graph.rule.one",
                "matched": True,
                "targetSymbols": ["005930", "000660"],
                "matchedTargetSymbols": [],
                "matchIdentityComplete": False,
            }],
        }

        store._upsert_rule_result_slots_with_connection(
            connection,
            run,
            result,
            trace,
            "2026-08-25T00:00:00Z",
        )

        self.assertFalse(any(
            "INSERT INTO ontology_reasoning_rule_result_slots" in sql
            for sql, _params in connection.calls
        ))

    def test_staged_abox_lifecycle_compacts_active_manifest_metadata(self):
        compact = compact_staged_abox_activation_lifecycle({
            "aboxActivationPreparation": {
                "status": "activated",
                "candidateAboxSnapshotId": "abox:candidate",
                "previousAboxSnapshotId": "abox:previous",
                "activeAbox": {
                    "status": "ok",
                    "aboxSnapshotId": "abox:candidate",
                    "worldviewManifestId": "abox:candidate",
                    "worldId": "premise:shared:global",
                    "scopePlan": [{"scopeId": "large"}] * 1000,
                    "marketScopeObservedAt": {"large": "payload"},
                },
            },
            "stagedAboxInferenceAlignment": {
                "verified": True,
                "sourceAboxSnapshotId": "abox:candidate",
                "targetSymbols": ["NVDA"],
            },
        })

        self.assertEqual("activated", compact["preparation"]["status"])
        self.assertEqual(
            "abox:candidate",
            compact["preparation"]["activeAbox"]["aboxSnapshotId"],
        )
        self.assertNotIn("scopePlan", compact["preparation"]["activeAbox"])
        self.assertNotIn(
            "marketScopeObservedAt",
            compact["preparation"]["activeAbox"],
        )

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

    def test_direct_multi_symbol_slots_report_unresolved_match_target(self):
        connection = RecordingConnection()
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        store.transaction = lambda: ConnectionContext(connection)

        result = store.record_rule_result_slots(
            world_id="premise:shared:us",
            account_id="",
            symbols=["NVDA", "MSTR"],
            catalog_rule_ids=["shared.rule.one"],
            rulebox_rules_hash="shared-rules:1",
            tbox_fingerprint="tbox:1",
            scope_plan_fingerprint="scope:shared:1",
            source_abox_snapshot_id="abox:shared:3",
            source_snapshot_fingerprint="facts:shared:3",
            execution={
                "status": "ok",
                "nativeRuleSelectionApplied": False,
                "nativeRuleSelectionFullRuleCount": 1,
                "typedbNativeRuleMatchedRuleIds": ["shared.rule.one"],
            },
            inference={
                "status": "ok",
                "generationAligned": True,
                "inferenceGenerationId": "generation:shared:3",
                "sourceAboxSnapshotId": "abox:shared:3",
                "traces": [{"ruleId": "shared.rule.one"}],
            },
            execution_namespace_id="namespace:v2",
            engine_deployment_id="ontology-v2-production-r14",
            graph_database="orbit_alpha_ontology",
            release_fingerprint="release:r14",
        )

        self.assertFalse(result["saved"])
        self.assertEqual("skipped-unresolved-match-target", result["status"])
        self.assertEqual(["shared.rule.one"], result["unresolvedRuleIds"])
        self.assertEqual([], connection.calls)

    def test_projection_audit_keeps_runtime_identity_and_patch_fallback(self):
        _snapshot, _graph, _fingerprint, run = self.build_run()
        completed = complete_ontology_projection_run(run, {
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "runtimeIdentity": {
                "contract": "orbit-runtime-identity-v1",
                "version": "release-1",
                "revision": "abc123",
                "source": "test",
                "python": "3.test",
            },
            "projectionScope": {
                "targetScopedManifestPatch": {
                    "status": "full-global-impact",
                    "mode": "full-manifest-fallback",
                    "fallbackReason": "global-value-context-without-explicit-subject",
                    "targetSymbols": ["005930"],
                    "selectedIncomingScopeCount": 0,
                    "reusedActiveScopeCount": 0,
                    "deferredScopeCount": 3,
                    "scopeIntegrityAuditIntervalMinutes": 30,
                    "scopeIntegrityAuditDue": True,
                    "automaticFullProjectionBlocked": True,
                },
            },
        }, completed_at="2026-07-20T00:01:10Z")

        self.assertEqual("abc123", completed.result_payload["runtimeIdentity"]["revision"])
        patch = completed.result_payload["targetScopedManifestPatch"]
        self.assertEqual("full-global-impact", patch["status"])
        self.assertEqual(
            "global-value-context-without-explicit-subject",
            patch["fallbackReason"],
        )

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

    def test_rule_runtime_summary_uses_nearest_rank_p95_for_small_samples(self):
        connection = RecordingConnection(rows=[
            {
                "run_id": "run:1",
                "rule_run_key": "rule-run:1",
                "rule_id": "graph.slow.v1",
                "duration_ms": 100,
                "status": "evaluated-no-match",
                "updated_at": "2026-07-20T00:00:00Z",
            },
            {
                "run_id": "run:2",
                "rule_run_key": "rule-run:2",
                "rule_id": "graph.slow.v1",
                "duration_ms": 9000,
                "status": "matched",
                "matched": 1,
                "updated_at": "2026-07-20T00:01:00Z",
            },
        ])
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        store.connect = lambda: ConnectionContext(connection)

        payload = store.rule_runtime_summary(limit=10)

        self.assertEqual(9000, payload["rules"][0]["p95DurationMs"])
        self.assertEqual(1, payload["rules"][0]["matchedCount"])

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

    def test_recorder_does_not_retry_reuse_proof_repair_after_selected_delta_is_verified(self):
        _snapshot, _graph, _fingerprint, run = self.build_run()
        stored_row = {
            "runId": run.run_id,
            "status": "ok",
            "aboxSnapshotId": run.abox_snapshot_id,
            "result": {
                "inferenceReuseProof": {"status": "incomplete"},
                "nativeReplayValidation": {
                    "status": "verified-selected-delta",
                    "verified": True,
                },
            },
        }
        store = SimpleNamespace(latest=lambda limit=0: [stored_row], complete=lambda _item: None)
        repository = SimpleNamespace(
            store_key="typedb",
            active_abox_metadata=lambda: {
                "status": "ok",
                "aboxSnapshotId": run.abox_snapshot_id,
                "projectionRunId": run.run_id,
            },
            inferencebox_snapshot=lambda symbols, limit: {
                "status": "ok",
                "nativeTypeDbReasoningUsed": True,
                "generationAligned": True,
                "sourceAboxSnapshotId": run.abox_snapshot_id,
                "targetSymbols": list(symbols),
            },
        )
        recorder = PortfolioOntologyProjectionRecorder(repository, projection_run_store=store)

        result = recorder.reconcile_interrupted_projection_audit()

        self.assertEqual("skipped", result["status"])
        self.assertIn("No interrupted audit", result["reason"])

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
