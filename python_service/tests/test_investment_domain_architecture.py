import sys
import unittest
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.ontology_domain_tbox import tbox_domain_validation
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_rule_manifest import validate_rule_domain_manifests
from digital_twin.domain.ontology_schema import add_entity
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_tbox import BOUNDED_CONTEXTS, CLASS_DEFS, RELATION_DEFS
from digital_twin.domain.investment_outcomes import PerformanceAttribution
from digital_twin.domain.portfolio import PortfolioSummary, Position
from digital_twin.domain.portfolio_ledger import BUY, SELL, PortfolioLedger, PortfolioLedgerEntry
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.domain.portfolio_ontology_cognitive_concepts import add_investment_brain_concepts
from digital_twin.domain.portfolio_rebalancing import (
    AllocationBand,
    RebalanceLeg,
    RebalanceProposal,
    allocation_drifts,
)
from digital_twin.domain.trade_execution import ActionEnvelope, ActionPlan, OrderIntent
from digital_twin.infrastructure.mysql_operational_connection import MYSQL_SCHEMA


def account_config(profile="balanced"):
    return AccountConfig(
        account_id="main",
        label="Main",
        provider="toss",
        base_url="https://example.test",
        client_id="private-client",
        client_secret="private-secret",
        account_seq="01",
        watchlist_symbols=["035420", "MSTR", "035420"],
        telegram_bot_token="private-bot-token",
        telegram_chat_id="private-chat",
        investment_strategy_profile=profile,
    )


class InvestmentDomainArchitectureTests(unittest.TestCase):
    def test_legacy_account_is_split_without_exposing_secrets(self):
        account = account_config()

        payload = account.domain_profile().to_dict()
        mandate = account.investment_mandate("2026-08-12T00:00:00Z")

        self.assertEqual("portfolio:main", payload["brokerageAccount"]["portfolioId"])
        self.assertEqual(["035420", "MSTR"], payload["universe"]["symbols"])
        self.assertTrue(payload["brokerageAccount"]["credentialRef"]["configured"])
        self.assertNotIn("private-secret", str(payload))
        self.assertNotIn("private-bot-token", str(payload))
        self.assertEqual(10.0, mandate.min_cash_weight_pct)
        self.assertTrue(mandate.policy_version.startswith("investment-mandate-v2:"))
        self.assertEqual(
            mandate.policy_version,
            account.investment_mandate("2027-01-01T00:00:00Z").policy_version,
        )

    def test_portfolio_ledger_replays_fifo_and_is_idempotent(self):
        entries = [
            PortfolioLedgerEntry.create(
                "portfolio:main",
                "main",
                BUY,
                "2026-08-01T00:00:00Z",
                entry_id="buy-1",
                source_reference="broker:buy-1",
                symbol="MSTR",
                currency="USD",
                quantity=10,
                unit_price=90,
                fee=10,
            ),
            PortfolioLedgerEntry.create(
                "portfolio:main",
                "main",
                SELL,
                "2026-08-02T00:00:00Z",
                entry_id="sell-1",
                source_reference="broker:sell-1",
                symbol="MSTR",
                currency="USD",
                quantity=4,
                unit_price=100,
                fee=4,
            ),
        ]
        ledger = PortfolioLedger("portfolio:main", "main")

        state = ledger.replay(entries)

        self.assertEqual(Decimal("6"), state.quantity("MSTR"))
        self.assertEqual(Decimal("91"), state.average_cost("MSTR"))
        self.assertEqual(Decimal("32"), state.realized_profit_loss["USD"])
        self.assertFalse(ledger.apply(entries[0]))
        self.assertEqual(Decimal("6"), state.quantity("MSTR"))

    def test_action_plan_is_guarded_by_policy_version_and_envelope(self):
        envelope = ActionEnvelope(
            portfolio_id="portfolio:main",
            symbol="035420",
            allowed_actions=["ADD", "HOLD"],
            max_buy_notional=1_000_000,
            max_buy_quantity=5,
            policy_version="policy:v1",
        )
        plan = ActionPlan.create(
            portfolio_id="portfolio:main",
            decision_episode_id="decision:1",
            action="ADD",
            policy_version="policy:v1",
            inference_generation_id="generation:1",
            order_intents=[OrderIntent("intent:1", "035420", "BUY", 2, limit_price=200_000)],
        )

        self.assertEqual([], plan.validate(envelope))
        self.assertEqual("review-required", plan.status)
        self.assertEqual(
            ["policy-version-mismatch"],
            plan.validate(ActionEnvelope(
                portfolio_id="portfolio:main",
                symbol="035420",
                allowed_actions=["ADD"],
                max_buy_notional=1_000_000,
                max_buy_quantity=5,
                policy_version="policy:v2",
            )),
        )

    def test_rebalance_proposal_only_allows_legs_for_real_band_drift(self):
        drifts = allocation_drifts(
            {"technology": 52, "cash": 8},
            [
                AllocationBand("technology", 35, 25, 45),
                AllocationBand("cash", 12, 10, 20),
            ],
        )
        proposal = RebalanceProposal.create(
            "portfolio:main",
            "policy:v1",
            "exposure:1",
            drifts,
            [
                RebalanceLeg("technology", "DECREASE", -7, 1_000_000),
                RebalanceLeg("cash", "INCREASE", 2, 1_000_000),
            ],
        )

        self.assertEqual([], proposal.validate())
        self.assertEqual(7, drifts[0].band_delta_pct)
        self.assertEqual(-2, drifts[1].band_delta_pct)
        self.assertEqual("review-required", proposal.status)

    def test_tbox_and_all_rule_manifests_are_referentially_complete(self):
        validation = tbox_domain_validation(BOUNDED_CONTEXTS, CLASS_DEFS, RELATION_DEFS)
        manifest_validation = validate_rule_domain_manifests(default_graph_inference_rules())
        class_names = {item.name for item in CLASS_DEFS}
        relation_names = {item.name for item in RELATION_DEFS}

        self.assertTrue(validation["valid"], validation)
        self.assertTrue(manifest_validation["valid"], manifest_validation)
        self.assertEqual([], manifest_validation["conservativeRuleIds"])
        self.assertTrue({
            "InvestmentMandate",
            "PortfolioLedgerEntry",
            "ExposureSnapshot",
            "ActionPlan",
            "ExecutionEpisode",
            "DecisionReview",
        }.issubset(class_names))
        self.assertTrue({
            "GOVERNED_BY_MANDATE",
            "HAS_RISK_LIMIT",
            "PROPOSES_ACTION_PLAN",
            "EXECUTES_ACTION_PLAN",
            "REVIEWS_DECISION",
        }.issubset(relation_names))

    def test_policy_limits_are_projected_and_concentration_rule_has_no_fixed_threshold(self):
        account = account_config("balanced")
        position = Position(
            symbol="035420",
            name="Kakao",
            market="KR",
            currency="KRW",
            quantity=10,
            current_price=4_000,
            market_value=40_000,
            market_value_krw=40_000,
            sector="technology",
            source="holding",
        )
        portfolio = PortfolioSummary(
            total=100_000,
            invested=40_000,
            cash=60_000,
            markets=[{"market": "KR", "value": 40_000, "ratio": 40}],
            sectors=[{"sector": "technology", "value": 40_000, "ratio": 40}],
            concentration=40,
        )
        graph = build_portfolio_ontology(
            [position],
            portfolio,
            portfolio_id="portfolio:main",
            runtime_context={"account": account.investment_strategy_context()},
            include_tbox=False,
            include_presentation=False,
            include_derived_decision_items=False,
        )
        position_node = next(item for item in graph.entities if item.kind == "position")
        mandate_node = next(item for item in graph.entities if item.properties.get("tboxClass") == "InvestmentMandate")
        concentration_rule = next(
            item for item in default_graph_inference_rules()
            if item.rule_id == "graph.portfolio.concentration.review.v1"
        )
        manifest = concentration_rule.to_dict()["domain_manifest"]

        self.assertEqual(25.0, position_node.properties["policyLimitRatio"])
        self.assertEqual(15.0, position_node.properties["policyDeltaRatio"])
        self.assertEqual(10.0, mandate_node.properties["minCashWeightPct"])
        self.assertEqual(
            ["maxPositionWeightPct", "maxSectorWeightPct", "fxExposureReviewPct"],
            manifest["policyKeys"],
        )
        self.assertNotIn("35", str(concentration_rule.to_dict()))
        for condition in concentration_rule.conditions:
            delta_filter = condition.target_property_filters["policyDeltaRatio"]
            self.assertEqual({"operator": ">", "value": 0}, delta_filter)

    def test_decision_action_execution_and_outcome_are_linked_in_abox(self):
        graph = PortfolioOntology("portfolio:main")
        add_entity(graph, "portfolio", "portfolio:main", "Main", {"tboxClass": "Portfolio"})
        add_entity(graph, "stock", "MSTR", "Strategy", {"tboxClass": "Stock", "symbol": "MSTR"})
        add_investment_brain_concepts(graph, "portfolio:main", [{
            "episodeId": "decision:1",
            "portfolioId": "portfolio:main",
            "symbol": "MSTR",
            "subjectName": "Strategy",
            "action": "TRIM",
            "mandateId": "investment-mandate:portfolio:main",
            "mandateVersion": "policy:v1",
            "sourceAboxSnapshotId": "abox:1",
            "inferenceGenerationId": "generation:1",
            "actionPlanId": "action-plan:1",
            "executionEpisodeIds": ["execution:1"],
            "outcomes": [{
                "outcomeId": "outcome:1",
                "observedAt": "2026-08-12T01:00:00Z",
                "price": 95,
                "selectedHypothesisStatus": "supported",
            }],
        }])
        relation_types = {item.relation_type for item in graph.relations}
        entity_classes = {item.properties.get("tboxClass") for item in graph.entities}

        self.assertTrue({"ActionPlan", "ExecutionEpisode", "ObservedOutcome"}.issubset(entity_classes))
        self.assertTrue({
            "PROPOSES_ACTION_PLAN",
            "EXECUTES_ACTION_PLAN",
            "MATCHES_DECISION",
            "PRODUCES_OUTCOME",
        }.issubset(relation_types))

    def test_mandate_history_and_performance_attribution_have_durable_tables(self):
        schema = "\n".join(MYSQL_SCHEMA)
        attribution = PerformanceAttribution(
            attribution_id="attribution:1",
            decision_episode_id="decision:1",
            market_return_pct=2.5,
            instrument_return_pct=7.25,
            execution_cost=1200,
        )

        self.assertIn("CREATE TABLE IF NOT EXISTS investment_mandate_versions", schema)
        self.assertIn("UNIQUE KEY uq_investment_mandate_version", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS investment_performance_attributions", schema)
        self.assertEqual(4.75, attribution.active_return_pct)


if __name__ == "__main__":
    unittest.main()
