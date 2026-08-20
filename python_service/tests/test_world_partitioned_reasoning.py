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

    def test_account_overlay_removes_market_mirror_and_keeps_private_facts(self):
        partition = compile_world_partitioned_rules([mixed_rule()])
        stock_id = entity_id("stock", "NVDA")
        portfolio_id = entity_id("portfolio", "acct")
        path_id = entity_id("price-path", "NVDA:1d")
        graph = PortfolioOntology(
            "acct",
            entities=[
                OntologyEntity(
                    stock_id,
                    "NVIDIA",
                    "stock",
                    {
                        "ontologyBox": "ABox",
                        "symbol": "NVDA",
                        "currentPrice": 200.0,
                        "volume": 1000,
                        "profitLossRate": -2.0,
                    },
                ),
                OntologyEntity(portfolio_id, "portfolio", "portfolio", {"ontologyBox": "ABox"}),
                OntologyEntity(path_id, "path", "price-path", {"ontologyBox": "ABox"}),
            ],
            relations=[
                OntologyRelation(portfolio_id, stock_id, "HOLDS", properties={"ontologyBox": "ABox"}),
                OntologyRelation(stock_id, path_id, "HAS_PRICE_PATH", properties={"ontologyBox": "ABox"}),
            ],
        )

        overlay = account_overlay_graph(
            graph,
            partition["overlayRules"],
            {"NVDA": ["graph.test.mixed.v1"]},
            shared_generation_id="generation:market:1",
            source_abox_snapshot_id="abox:market:1",
        )

        stock = next(item for item in overlay.entities if item.entity_id == stock_id)
        self.assertNotIn("currentPrice", stock.properties)
        self.assertNotIn("volume", stock.properties)
        self.assertEqual(-2.0, stock.properties["profitLossRate"])
        self.assertNotIn("HAS_PRICE_PATH", [item.relation_type for item in overlay.relations])
        self.assertIn(SHARED_PREMISE_RELATION, [item.relation_type for item in overlay.relations])
        self.assertEqual(0, len(overlay.evidence))

    def test_account_overlay_binds_pure_shared_crypto_premise_to_crypto_asset(self):
        crypto_rule = next(
            item for item in default_graph_inference_rules()
            if item.rule_id == "graph.crypto.market.24h.up.major.v1"
        )
        partition = compile_world_partitioned_rules([crypto_rule])
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
            {"BTC": [crypto_rule.rule_id]},
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

    def test_account_overlay_filters_generic_relation_by_rule_endpoint_kinds(self):
        account_rule = GraphInferenceRule(
            rule_id="graph.test.account-observation.v1",
            label="account observation",
            version="1",
            source_kind="stock",
            conditions=[GraphRuleCondition(
                "account-change",
                "relation",
                "account fact change",
                relation_type="HAS_OBSERVATION",
                target_kind="fact-change",
                hypothesis_scope="account",
            )],
            derivations=[derivation()],
            action_group="risk",
            action_level="check",
            prompt_hint="test",
        )
        partition = compile_world_partitioned_rules([account_rule])
        stock_id = entity_id("stock", "NVDA")
        fact_id = entity_id("fact-change", "NVDA:profit-loss")
        price_id = entity_id("price-metric", "NVDA:current")
        graph = PortfolioOntology(
            "acct",
            entities=[
                OntologyEntity(stock_id, "NVIDIA", "stock", {"ontologyBox": "ABox", "symbol": "NVDA"}),
                OntologyEntity(fact_id, "P/L change", "fact-change", {"ontologyBox": "ABox"}),
                OntologyEntity(price_id, "Price", "price-metric", {"ontologyBox": "ABox"}),
            ],
            relations=[
                OntologyRelation(stock_id, fact_id, "HAS_OBSERVATION", properties={"ontologyBox": "ABox"}),
                OntologyRelation(stock_id, price_id, "HAS_OBSERVATION", properties={"ontologyBox": "ABox"}),
            ],
        )

        overlay = account_overlay_graph(graph, partition["overlayRules"], {})

        self.assertIn(fact_id, {item.entity_id for item in overlay.entities})
        self.assertNotIn(price_id, {item.entity_id for item in overlay.entities})
        self.assertEqual(1, len(overlay.relations))

    def test_shared_premise_world_combines_market_and_company_rule_inputs(self):
        market_rule = mixed_rule()
        company_rule = GraphInferenceRule(
            rule_id="graph.test.company.v1",
            label="company test",
            version="1",
            source_kind="stock",
            conditions=[GraphRuleCondition(
                "financial-state",
                "relation",
                "financial state",
                relation_type="HAS_FINANCIAL_STATE",
                target_kind="company-financial-state",
                hypothesis_scope="market",
            )],
            derivations=[derivation()],
            action_group="quality",
            action_level="observe",
            prompt_hint="test",
        )
        partition = compile_world_partitioned_rules([market_rule, company_rule])
        stock_id = entity_id("stock", "NVDA")
        financial_id = entity_id("company-financial-state", "NVDA:2026Q2")
        account_id = entity_id("account", "acct")
        graph = PortfolioOntology(
            "acct",
            entities=[
                OntologyEntity(stock_id, "NVIDIA", "stock", {"ontologyBox": "ABox", "currentPrice": 200}),
                OntologyEntity(financial_id, "financial", "company-financial-state", {"ontologyBox": "ABox", "revenue": 10}),
                OntologyEntity(account_id, "account", "account", {"ontologyBox": "ABox", "cash": 100}),
            ],
            relations=[
                OntologyRelation(stock_id, financial_id, "HAS_FINANCIAL_STATE", properties={"ontologyBox": "ABox"}),
                OntologyRelation(account_id, stock_id, "WATCHES", properties={"ontologyBox": "ABox"}),
            ],
        )

        shared = shared_premise_world_graph(
            graph,
            partition["sharedRules"],
            shared_premise_world("us"),
        )

        self.assertEqual("premise:shared:us", shared.worldview["worldId"])
        self.assertIn("HAS_FINANCIAL_STATE", [item.relation_type for item in shared.relations])
        self.assertNotIn("WATCHES", [item.relation_type for item in shared.relations])
        self.assertNotIn(account_id, [item.entity_id for item in shared.entities])

    def test_direct_shared_premise_evidence_does_not_need_legacy_service(self):
        projection = {
            "inferenceBox": {
                "relations": [{"relationId": "account:1", "symbol": "NVDA"}],
                "traces": [{"traceId": "account-trace:1", "symbol": "NVDA"}],
            }
        }
        proof = {
            "ready": True,
            "worldId": "premise:shared:us",
            "inferenceGenerationId": "premise-generation:1",
            "sourceAboxSnapshotId": "premise-abox:1",
            "symbols": {
                "NVDA": {
                    "relations": [{"relationId": "shared:1", "symbol": "NVDA"}],
                    "traces": [{"traceId": "shared-trace:1", "symbol": "NVDA"}],
                }
            },
        }

        result = attach_shared_premise_evidence(projection, proof)

        self.assertEqual(2, result["inferenceBox"]["relationCount"])
        self.assertEqual(2, result["inferenceBox"]["traceCount"])
        self.assertTrue(result["inferenceBox"]["sharedPremiseEvidenceAttached"])
        self.assertEqual(
            "premise-generation:1",
            result["inferenceBox"]["sharedPremiseInferenceGenerationId"],
        )

    def test_v2_executor_requires_direct_shared_premises_before_account_projection(self):
        class Recorder:
            def __init__(self):
                self.context = {}

            def world_partitioned_reasoning_enabled(self):
                return True

            def prepare_shared_premises(self, *_args, **_kwargs):
                return {
                    "status": "ready",
                    "ready": True,
                    "premisesBySymbol": {"NVDA": ["graph.test.mixed.v1"]},
                    "inferenceGenerationId": "market-generation:1",
                    "sourceAboxSnapshotId": "market-abox:1",
                    "symbols": {"NVDA": {"snapshotId": "market-generation:1", "relations": [], "traces": []}},
                }

            def record_snapshot(self, snapshot, **kwargs):
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
        executor = ScopedTypeDBInferenceExecutor(recorder)
        request = independent_reasoning_request(
            "ontology-v2-production",
            [source_event("NVDA", ["acct"])],
        )

        result = executor.execute(
            request,
            [SimpleNamespace(account_id="acct", metadata={})],
        )

        self.assertEqual("ok", result["acct"]["status"])
        self.assertTrue(recorder.context["sharedPremiseProof"]["ready"])


if __name__ == "__main__":
    unittest.main()
