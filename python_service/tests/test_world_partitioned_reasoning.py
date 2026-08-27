import unittest
from types import SimpleNamespace

from digital_twin.application.independent_reasoning_engine import ScopedTypeDBInferenceExecutor
from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED
from digital_twin.domain.independent_reasoning import independent_reasoning_request
from digital_twin.domain.ontology_contracts import (
    OntologyEntity,
    OntologyRelation,
    PortfolioOntology,
    entity_id,
)
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_rulebox_contracts import (
    GraphInferenceRule,
    GraphRuleCondition,
    GraphRuleDerivation,
)
from digital_twin.domain.world_partitioned_reasoning import (
    SHARED_PREMISE_RELATION,
    account_overlay_graph,
    attach_shared_premise_evidence,
    compile_world_partitioned_rules,
    partitioned_phase_impact_plan,
    shared_premise_matches,
    shared_premise_world_graph,
)
from digital_twin.domain.ontology_worlds import shared_premise_world


def source_event(symbol="NVDA", account_ids=None):
    return DomainEvent(
        name=ONTOLOGY_REASONING_REQUESTED,
        aggregate_id="market-observation:" + symbol,
        occurred_at="2026-08-18T00:00:00Z",
        event_id="event:" + symbol,
        payload={
            "accountIds": ["acct"] if account_ids is None else list(account_ids),
            "affectedSymbols": [symbol],
            "factTypes": ["PRICE_OBSERVATION"],
            "sourceObservedAt": "2026-08-18T00:00:00Z",
            "workClass": "MARKET",
        },
    )


def derivation():
    return GraphRuleDerivation(
        relation_type="HAS_TEST_RESULT",
        target_kind="test-result",
        target_key="{symbol}:result",
        target_label="result",
        tbox_class="DerivedAssertion",
        decision_stage="reference",
    )


def mixed_rule():
    return GraphInferenceRule(
        rule_id="graph.test.mixed.v1",
        label="mixed test",
        version="1",
        source_kind="stock",
        conditions=[
            GraphRuleCondition(
                "market-price",
                "property",
                "market price",
                field="currentPrice",
                operator=">",
                value=0,
                hypothesis_scope="market",
            ),
            GraphRuleCondition(
                "account-return",
                "property",
                "account return",
                field="profitLossRate",
                operator="<",
                value=0,
                hypothesis_scope="account",
            ),
        ],
        derivations=[derivation()],
        action_group="risk",
        action_level="check",
        prompt_hint="test",
    )


class WorldPartitionedReasoningTests(unittest.TestCase):
    def test_default_rulebox_has_a_complete_world_partition(self):
        result = compile_world_partitioned_rules(default_graph_inference_rules())

        self.assertEqual("ready", result["status"])
        self.assertEqual([], result["failures"])
        self.assertGreater(result["mixedRuleCount"], 0)
        self.assertGreater(result["sharedRuleCount"], 0)
        self.assertGreater(result["overlayRuleCount"], 0)

    def test_mixed_rule_becomes_shared_premise_and_original_overlay_rule(self):
        result = compile_world_partitioned_rules([mixed_rule()])

        self.assertEqual("ready", result["status"])
        self.assertEqual(["shared.premise.graph.test.mixed.v1"], result["sharedRuleIds"])
        self.assertEqual(["graph.test.mixed.v1"], result["overlayRuleIds"])
        self.assertEqual(
            ["market-price"],
            [condition.condition_id for condition in result["sharedRules"][0].conditions],
        )
        self.assertEqual(
            ["account-return", "shared-market-premise:graph.test.mixed.v1"],
            [condition.condition_id for condition in result["overlayRules"][0].conditions],
        )

    def test_impact_plan_is_translated_to_each_physical_world_catalog(self):
        partition = compile_world_partitioned_rules([mixed_rule()])
        source_plan = {
            "version": "impact-v1",
            "candidateRuleIds": ["graph.test.mixed.v1"],
            "triggerRuleIds": ["graph.test.mixed.v1"],
            "invalidationRuleIds": [],
            "deferredRuleIds": [],
            "ruleRoutingComplete": True,
            "nativeRuleSelectionEligible": False,
            "diagnostics": {},
        }

        shared = partitioned_phase_impact_plan(
            source_plan,
            partition,
            "shared-premise",
        )
        overlay = partitioned_phase_impact_plan(
            source_plan,
            partition,
            "account-overlay",
        )

        self.assertEqual(
            ["shared.premise.graph.test.mixed.v1"],
            shared["candidateRuleIds"],
        )
        self.assertEqual(
            ["graph.test.mixed.v1"],
            overlay["candidateRuleIds"],
        )
        self.assertEqual("shared-premise", shared["ruleExecutionPhase"])
        self.assertEqual("account-overlay", overlay["ruleExecutionPhase"])

    def test_phase_plan_expands_one_cross_world_rule_to_all_shared_rules(self):
        base = mixed_rule()
        rule = GraphInferenceRule(
            **{
                **base.__dict__,
                "rule_id": "graph.test.cross-or-impact.v1",
                "conditions": [
                    base.conditions[0],
                    GraphRuleCondition(
                        "market-flow",
                        "property",
                        "market flow",
                        field="tradeStrength",
                        operator=">",
                        value=100,
                        role="any",
                        hypothesis_scope="market",
                    ),
                    GraphRuleCondition(
                        "account-profile",
                        "property",
                        "account profile",
                        field="investmentStrategyProfile",
                        operator="==",
                        value="aggressive",
                        role="any",
                        hypothesis_scope="account",
                    ),
                ],
                "any_condition_min_count": 1,
            }
        )
        partition = compile_world_partitioned_rules([rule, mixed_rule()])
        plan = partitioned_phase_impact_plan({
            "candidateRuleIds": [rule.rule_id],
            "triggerRuleIds": [rule.rule_id],
            "invalidationRuleIds": [],
            "ruleRoutingComplete": True,
        }, partition, "shared-premise")

        self.assertEqual([
            "shared.premise.graph.test.cross-or-impact.v1",
            "shared.premise.any.graph.test.cross-or-impact.v1",
        ], plan["candidateRuleIds"])
        self.assertTrue(plan["nativeRuleSelectionEligible"])
        self.assertEqual(
            ["shared.premise.graph.test.mixed.v1"],
            plan["deferredRuleIds"],
        )

    def test_cross_world_or_keeps_required_market_and_either_optional_path(self):
        base = mixed_rule()
        rule = GraphInferenceRule(
            **{
                **base.__dict__,
                "rule_id": "graph.test.cross-or.v1",
                "conditions": [
                    base.conditions[0],
                    GraphRuleCondition(
                        "market-flow",
                        "property",
                        "market flow",
                        field="tradeStrength",
                        operator=">",
                        value=100,
                        role="any",
                        hypothesis_scope="market",
                    ),
                    GraphRuleCondition(
                        "account-profile",
                        "property",
                        "account profile",
                        field="investmentStrategyProfile",
                        operator="==",
                        value="aggressive",
                        role="any",
                        hypothesis_scope="account",
                    ),
                ],
                "any_condition_min_count": 1,
            }
        )

        result = compile_world_partitioned_rules([rule])

        self.assertEqual("ready", result["status"])
        self.assertEqual(2, result["sharedRuleCount"])
        self.assertEqual(
            [
                "shared.premise.graph.test.cross-or.v1",
                "shared.premise.any.graph.test.cross-or.v1",
            ],
            result["sharedRuleIds"],
        )
        resolver = result["overlayRules"][0]
        self.assertEqual(1, resolver.any_condition_min_count)
        self.assertEqual(
            ["any", "required", "any"],
            [condition.role for condition in resolver.conditions],
        )

    def test_account_overlay_binds_pure_shared_crypto_premise_to_crypto_asset(self):
        crypto_rule = next(
            item for item in default_graph_inference_rules()
            if item.rule_id == "graph.crypto.market.24h.up.major.v1"
        )
        partition = compile_world_partitioned_rules([crypto_rule])
        self.assertEqual(
            ["shared.premise." + crypto_rule.rule_id],
            partition["sharedRuleIds"],
        )
        self.assertEqual([crypto_rule.rule_id], partition["overlayRuleIds"])
        self.assertEqual(
            "ESTABLISHES_SHARED_MARKET_PREMISE",
            partition["sharedRules"][0].derivations[0].relation_type,
        )
        self.assertEqual(
            ["shared-market-premise:" + crypto_rule.rule_id],
            [item.condition_id for item in partition["overlayRules"][0].conditions],
        )
        self.assertEqual(
            "reference-only",
            partition["overlayRules"][0].resolved_knowledge_basis.decision_eligibility,
        )
        premises = shared_premise_matches({
            "traces": [{
                "ruleId": "shared.premise." + crypto_rule.rule_id,
                "symbol": "BTC",
            }],
        })
        self.assertEqual({"BTC": [crypto_rule.rule_id]}, premises)
        asset_id = entity_id("crypto-asset", "BTC")
        path_id = entity_id("price-path", "BTC:crypto:1h-24h-7d")
        graph = PortfolioOntology(
            "acct",
            entities=[
                OntologyEntity(asset_id, "Bitcoin", "crypto-asset", {
                    "ontologyBox": "ABox",
                    "tboxClass": "CryptoAsset",
                    "symbol": "BTC",
                    "provider": "CoinGecko",
                }),
                OntologyEntity(path_id, "Bitcoin price path", "price-path", {
                    "ontologyBox": "ABox",
                    "change24h": 11.5,
                }),
            ],
            relations=[
                OntologyRelation(
                    asset_id,
                    path_id,
                    "HAS_PRICE_PATH",
                    properties={"ontologyBox": "ABox"},
                ),
            ],
        )

        overlay = account_overlay_graph(
            graph,
            partition["overlayRules"],
            premises,
            shared_generation_id="generation:crypto:1",
            source_abox_snapshot_id="abox:crypto:1",
        )

        asset = next(item for item in overlay.entities if item.entity_id == asset_id)
        premise_relation = next(
            item for item in overlay.relations
            if item.relation_type == SHARED_PREMISE_RELATION
        )
        self.assertEqual(asset_id, premise_relation.source)
        self.assertEqual("BTC", asset.properties["symbol"])
        self.assertNotIn("provider", asset.properties)
        self.assertNotIn(path_id, {item.entity_id for item in overlay.entities})

    def test_shared_premise_world_preserves_exact_public_model_contract_identity(self):
        rule = GraphInferenceRule(
            rule_id="graph.test.model-contract.v1",
            label="model contract test",
            version="1",
            source_kind="stock",
            conditions=[GraphRuleCondition(
                "exact-model-contract",
                "relation",
                "exact model contract",
                relation_type="HAS_MODEL_SIGNAL",
                target_kind="statistical-model-hypothesis-evidence",
                target_property_filters={
                    "hypothesisContractId": "graph.test.model-contract.v1",
                    "decisionEligibility": "conditional",
                },
                hypothesis_scope="market",
            )],
            derivations=[derivation()],
            action_group="quality",
            action_level="observe",
            prompt_hint="test",
        )
        partition = compile_world_partitioned_rules([rule])
        stock_id = entity_id("stock", "NVDA")
        evidence_id = entity_id(
            "statistical-model-hypothesis-evidence",
            "NVDA:graph.test.model-contract.v1",
        )
        graph = PortfolioOntology(
            "acct",
            entities=[
                OntologyEntity(stock_id, "NVIDIA", "stock", {"ontologyBox": "ABox"}),
                OntologyEntity(evidence_id, "model evidence", "statistical-model-hypothesis-evidence", {
                    "ontologyBox": "ABox",
                    "hypothesisContractId": "graph.test.model-contract.v1",
                    "hypothesisFamilyId": "trend-break",
                    "decisionEligibility": "conditional",
                    "aiPrompt": "must-not-cross-shared-world",
                }),
            ],
            relations=[
                OntologyRelation(stock_id, evidence_id, "HAS_MODEL_SIGNAL", properties={"ontologyBox": "ABox"}),
            ],
        )

        shared = shared_premise_world_graph(
            graph,
            partition["sharedRules"],
            shared_premise_world("us"),
        )
        evidence = next(item for item in shared.entities if item.entity_id == evidence_id)

        self.assertEqual(
            "graph.test.model-contract.v1",
            evidence.properties["hypothesisContractId"],
        )
        self.assertEqual("trend-break", evidence.properties["hypothesisFamilyId"])
        self.assertEqual("conditional", evidence.properties["decisionEligibility"])
        self.assertNotIn("aiPrompt", evidence.properties)

    def test_v2_executor_retries_one_transient_shared_premise_writer_handoff(self):
        class Recorder:
            def __init__(self):
                self.calls = 0
                self.context = {}

            @staticmethod
            def world_partitioned_reasoning_enabled():
                return True

            def prepare_shared_premises(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return {
                        "status": "shared-premise-projection-failed",
                        "ready": False,
                        "retryable": True,
                        "reasonCode": "deferred-projection-coordinator",
                        "failureStage": "shared-premise-projection",
                        "recommendedRetryAfterSeconds": 10,
                    }
                return {
                    "status": "ready",
                    "ready": True,
                    "inferenceGenerationId": "market-generation:2",
                    "sourceAboxSnapshotId": "market-abox:2",
                    "symbols": {"NVDA": {"relations": [], "traces": []}},
                }

            def record_snapshot(self, _snapshot, **kwargs):
                self.context = dict(kwargs.get("reasoning_context") or {})
                return {
                    "status": "ok",
                    "inferenceBox": {
                        "nativeTypeDbReasoningCompleted": True,
                        "generationAligned": True,
                        "sourceAboxSnapshotId": "account-abox:1",
                        "inferenceGenerationId": "account-generation:1",
                    },
                }

        recorder = Recorder()
        sleeps = []
        executor = ScopedTypeDBInferenceExecutor(
            recorder,
            settings={
                "reasoningEngineSharedPremiseInlineRetryCount": "1",
                "reasoningEngineSharedPremiseInlineRetryMaxSeconds": "2",
            },
            sleep=sleeps.append,
        )
        request = independent_reasoning_request(
            "ontology-v2-production",
            [source_event("NVDA", ["acct"])],
        )

        result = executor.execute(
            request,
            [SimpleNamespace(account_id="acct", metadata={})],
        )

        self.assertEqual("ok", result["acct"]["status"])
        self.assertEqual(2, recorder.calls)
        self.assertEqual([2], sleeps)
        self.assertEqual(
            2,
            len(recorder.context["sharedPremiseProof"]["preparationAttempts"]),
        )


if __name__ == "__main__":
    unittest.main()
