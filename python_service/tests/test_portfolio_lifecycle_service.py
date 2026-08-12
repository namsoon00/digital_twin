import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.investment_outcome_observation_service import InvestmentOutcomeObservationService
from digital_twin.application.broker_activity_service import BrokerActivitySyncService
from digital_twin.application.portfolio_lifecycle_service import (
    DecisionActionPlanningService,
    PortfolioAccountingService,
    TradeExecutionService,
)
from digital_twin.domain.investment_brain import ObservedOutcome
from digital_twin.domain.investment_mandate import InvestmentMandate
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.domain.trade_execution import ActionEnvelope, ActionPlan, OrderIntent
from digital_twin.infrastructure.broker_activity_csv import parse_broker_activity_csv
from digital_twin.infrastructure.mysql_operational_connection import MYSQL_SCHEMA


NOW = "2026-08-12T06:00:00Z"


class MemoryInvestmentRepository:
    def __init__(self):
        self.entries = []
        self.reconciliations = {}
        self.exposures = {}
        self.proposals = {}
        self.plans = {}
        self.plan_reviews = []
        self.attributions = []
        self.decision_reviews = []
        self.executions = []
        self.decision_cycles = {}
        self.activity_sync = {}
        self.mandate = InvestmentMandate.from_profile(
            "main",
            "portfolio:main",
            {
                "maxPositionWeightPct": 25,
                "maxSectorWeightPct": 45,
                "fxExposureReviewPct": 40,
                "minCashWeightPct": 10,
            },
            NOW,
        )

    def active_mandate(self, _portfolio_id):
        return self.mandate.to_dict()

    def save_mandate(self, mandate):
        self.mandate = mandate
        return mandate

    def ledger_entries(self, _portfolio_id, limit=10000):
        return list(self.entries[:limit])

    def append_ledger_entries(self, entries):
        existing = {item.source_reference for item in self.entries}
        inserted = [item for item in entries if item.source_reference not in existing]
        self.entries.extend(inserted)
        return len(inserted)

    def save_reconciliation(self, item):
        self.reconciliations.setdefault(item.reconciliation_id, item)
        return item

    def save_exposure_snapshot(self, item):
        self.exposures.setdefault(item.snapshot_id, item)
        return item

    def save_rebalance_proposal(self, item):
        self.proposals[item.proposal_id] = item
        return item

    def save_portfolio_decision_cycle(self, item):
        self.decision_cycles[item.cycle_id] = item
        return item

    def save_broker_activity_sync_state(self, item):
        self.activity_sync[item.portfolio_id] = item.to_dict()
        return item

    def broker_activity_sync_state(self, portfolio_id):
        return dict(self.activity_sync.get(portfolio_id) or {})

    def save_action_plan(self, item):
        self.plans[item.plan_id] = item
        return item

    def action_plan(self, plan_id):
        return self.plans.get(plan_id)

    def latest_active_action_plan(self, portfolio_id, symbol, action):
        rows = [
            item for item in self.plans.values()
            if item.portfolio_id == portfolio_id
            and item.action == action
            and item.envelope
            and item.envelope.symbol == symbol
            and item.status in {"review-required", "approved"}
        ]
        return rows[-1] if rows else None

    def save_action_plan_review(self, item):
        self.plan_reviews.append(item)
        return item

    def save_execution_episode(self, item):
        self.executions.append(item)
        return item

    def lifecycle_trace(self, _episode_id):
        return {"executionEpisodes": [], "fills": []}

    def execution_feedback_for_decisions(self, episode_ids):
        return {
            episode_id: {"actionPlans": [], "executionEpisodes": [], "fills": []}
            for episode_id in episode_ids
        }

    def save_outcome_reviews(self, attributions, reviews):
        attribution_rows = list(attributions)
        review_rows = list(reviews)
        self.attributions.extend(attribution_rows)
        self.decision_reviews.extend(review_rows)
        return {"attributionCount": len(attribution_rows), "reviewCount": len(review_rows)}

    def save_performance_attribution(self, item):
        self.attributions.append(item)
        return item

    def save_decision_review(self, item):
        self.decision_reviews.append(item)
        return item


def live_snapshot(quantity=10, price=70000, cash=200000):
    value = quantity * price
    return AccountSnapshot(
        account_id="main",
        account_label="Main",
        provider="toss",
        mode="live",
        status="토스 계좌 동기화",
        generated_at=NOW,
        portfolio=PortfolioSummary(
            total=value + cash,
            invested=value,
            cash=cash,
            markets=[{"market": "KR", "value": value, "ratio": value / (value + cash) * 100}],
            sectors=[{"sector": "technology", "value": value, "ratio": value / (value + cash) * 100}],
            concentration=value / (value + cash) * 100,
        ),
        positions=[Position(
            symbol="035420",
            name="NAVER",
            market="KR",
            currency="KRW",
            quantity=quantity,
            sellable_quantity=quantity,
            average_price=65000,
            current_price=price,
            market_value=value,
            market_value_krw=value,
            sector="technology",
            source="holding",
        )],
    )


class PortfolioLifecycleServiceTests(unittest.TestCase):
    def test_mandate_round_trip_preserves_explicit_zero_limits(self):
        payload = MemoryInvestmentRepository().mandate.to_dict()
        payload["min_cash_weight_pct"] = 0
        payload.pop("minCashWeightPct", None)

        mandate = InvestmentMandate.from_dict(payload)

        self.assertEqual(0, mandate.min_cash_weight_pct)

    def test_snapshot_bootstrap_is_idempotent_and_later_difference_does_not_rewrite_lots(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(repository)

        first = service.observe_snapshot(live_snapshot())
        second = service.observe_snapshot(live_snapshot())
        changed = service.observe_snapshot(live_snapshot(quantity=11))

        self.assertEqual(2, first["openingEntryCount"])
        self.assertEqual("matched", first["reconciliation"]["status"])
        self.assertEqual(0, second["openingEntryCount"])
        self.assertEqual(2, len(repository.entries))
        self.assertEqual("discrepancy", changed["reconciliation"]["status"])
        self.assertEqual(2, len(repository.decision_cycles))
        quantity_difference = next(
            item for item in changed["reconciliation"]["differences"]
            if item["differenceType"] == "position-quantity"
        )
        self.assertEqual("10", quantity_difference["expected"])
        self.assertEqual("11", quantity_difference["observed"])

    def test_exposure_breach_creates_review_only_rebalance_proposal(self):
        repository = MemoryInvestmentRepository()

        result = PortfolioAccountingService(repository).observe_snapshot(live_snapshot())

        self.assertGreater(result["exposureSnapshot"]["metrics"][0]["policyDeltaPct"], 0)
        self.assertEqual("review-required", result["rebalanceProposal"]["status"])
        self.assertTrue(result["rebalanceProposal"]["legs"])

    def test_ai_action_is_compiled_to_policy_bounded_plan_and_requires_approval(self):
        repository = MemoryInvestmentRepository()
        repository.mandate = InvestmentMandate.from_profile(
            "main",
            "portfolio:main",
            {
                "maxPositionWeightPct": 90,
                "maxSectorWeightPct": 95,
                "fxExposureReviewPct": 90,
                "minCashWeightPct": 10,
            },
            NOW,
        )
        state = live_snapshot().to_monitor_state()
        monitor_store = SimpleNamespace(load_previous=lambda _account_id: state)
        planner = DecisionActionPlanningService(
            repository,
            monitor_store,
            {"investmentActionPlanExpiryMinutes": "30", "investmentActionPlanSlicePct": "25"},
        )
        hypothesis = SimpleNamespace(hypothesis_id="hypothesis:1", invalidation_conditions=["20일선 이탈"])
        episode = SimpleNamespace(
            episode_id="decision:1",
            portfolio_id="portfolio:main",
            account_id="main",
            symbol="035420",
            action="ADD",
            mandate_version=repository.mandate.policy_version,
            inference_generation_id="generation:1",
            decided_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            selected_hypothesis_id="hypothesis:1",
            hypothesis_set=SimpleNamespace(hypotheses=[hypothesis]),
        )
        context = {
            "ontologyRelationContext": {
                "actionEnvelope": {"allowedActions": ["ADD", "HOLD"]},
            },
        }

        plan = planner.prepare(episode, context)
        repository.save_action_plan(plan)
        approval = TradeExecutionService(repository).review_plan(plan.plan_id, "approved", "owner")
        submission = TradeExecutionService(repository).submit_plan(plan.plan_id)

        self.assertEqual("review-required", plan.status)
        self.assertEqual(1, len(plan.order_intents))
        self.assertGreaterEqual(len(plan.slices), 1)
        self.assertTrue(plan.account_snapshot_fingerprint.startswith("account-snapshot:"))
        self.assertLessEqual(plan.order_intents[0].notional, plan.envelope.max_buy_notional)
        self.assertEqual("approved", approval["plan"]["status"])
        self.assertEqual("blocked", submission["status"])
        self.assertIn("broker-order-provider-not-configured", submission["validationErrors"])

    def test_foreign_currency_buy_envelope_converts_base_currency_headroom(self):
        repository = MemoryInvestmentRepository()
        repository.mandate = InvestmentMandate.from_profile(
            "main",
            "portfolio:main",
            {"maxPositionWeightPct": 90, "minCashWeightPct": 10},
            NOW,
        )
        state = live_snapshot().to_monitor_state()
        state["portfolio"] = {**state["portfolio"], "total": 5_000_000, "cash": 3_000_000}
        state["positions"]["035420"].update({
            "currency": "USD",
            "current_price": 100,
            "market_value": 1_000,
            "market_value_krw": 1_300_000,
            "exchange_rate": 1_300,
        })
        planner = DecisionActionPlanningService(
            repository,
            SimpleNamespace(load_previous=lambda: {"main": state}),
        )
        episode = SimpleNamespace(
            episode_id="decision:fx",
            portfolio_id="portfolio:main",
            account_id="main",
            symbol="035420",
            action="ADD",
            mandate_version=repository.mandate.policy_version,
            inference_generation_id="generation:fx",
            decided_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            selected_hypothesis_id="",
            hypothesis_set=SimpleNamespace(hypotheses=[]),
        )

        plan = planner.prepare(episode, {"ontologyRelationContext": {"allowedActions": ["ADD"]}})

        self.assertEqual("USD", plan.envelope.notional_currency)
        self.assertAlmostEqual(3_000_000 - 500_000, plan.sizing_basis["maxBuyNotionalBase"])
        self.assertAlmostEqual((3_000_000 - 500_000) / 1_300, plan.envelope.max_buy_notional)
        self.assertLessEqual(plan.order_intents[0].notional, plan.envelope.max_buy_notional)

    def test_approval_revalidates_current_account_snapshot_freshness(self):
        repository = MemoryInvestmentRepository()
        created = datetime.now(timezone.utc)
        envelope = ActionEnvelope(
            portfolio_id="portfolio:main",
            symbol="035420",
            allowed_actions=["ADD"],
            max_buy_notional=100_000,
            max_buy_quantity=1,
            minimum_cash_after=90_000,
            policy_version=repository.mandate.policy_version,
        )
        plan = ActionPlan.create(
            "portfolio:main",
            "decision:stale",
            "ADD",
            repository.mandate.policy_version,
            "generation:stale",
            order_intents=[OrderIntent("intent:stale", "035420", "BUY", 1, limit_price=70_000)],
            created_at=created.isoformat().replace("+00:00", "Z"),
            expires_at=(created + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
            envelope=envelope,
        )
        repository.save_action_plan(plan)
        state = live_snapshot().to_monitor_state()
        state["generatedAt"] = "2000-01-01T00:00:00Z"
        service = TradeExecutionService(
            repository,
            monitor_store=SimpleNamespace(load_previous=lambda _account_id: state),
            settings={"investmentExecutionSnapshotMaxAgeMinutes": "10"},
        )

        result = service.review_plan(plan.plan_id, "approved", "owner")

        self.assertEqual("rejected", result["plan"]["status"])
        self.assertIn("current-account-snapshot-stale", result["validationErrors"])

    def test_outcome_review_keeps_missing_benchmark_explicit(self):
        repository = MemoryInvestmentRepository()
        episode = SimpleNamespace(
            episode_id="decision:1",
            action_plan_id="action-plan:1",
            action="HOLD",
            mandate_version=repository.mandate.policy_version,
            facts_at_decision={},
        )
        decision_store = SimpleNamespace(episodes_by_ids=lambda _ids: {"decision:1": episode})
        service = InvestmentOutcomeObservationService(
            decision_episode_store=decision_store,
            investment_domain_store=repository,
        )
        outcome = ObservedOutcome(
            outcome_id="outcome:1",
            episode_id="decision:1",
            observed_at=NOW,
            price=105,
            price_change_from_decision_pct=5,
            selected_hypothesis_status="supported",
            payload={"horizonMinutes": 60, "calibrationEligibility": "eligible"},
        )

        result = service.review_outcomes([outcome])

        self.assertEqual({"attributionCount": 1, "reviewCount": 1}, result)
        self.assertEqual("partial", repository.attributions[0].data_state)
        self.assertEqual(["benchmarkReturnPct"], repository.attributions[0].missing_data)
        self.assertTrue(repository.decision_reviews[0].evidence_still_valid)

    def test_csv_broker_activity_imports_only_incremental_rows(self):
        repository = MemoryInvestmentRepository()
        PortfolioAccountingService(repository).observe_snapshot(live_snapshot())
        csv_content = "\n".join([
            "type,occurred_at,source_reference,symbol,currency,quantity,unit_price,amount,fee",
            "매수,2026-08-12T05:00:00Z,before,035420,KRW,1,70000,0,0",
            "매수,2026-08-12T07:00:00Z,after,035420,KRW,1,70000,0,10",
        ])

        parsed = parse_broker_activity_csv("main", "toss", csv_content)
        result = BrokerActivitySyncService(repository).import_activities(
            "main", "toss", parsed["activities"], parsed["rejected"]
        )

        self.assertEqual(1, result["insertedCount"])
        self.assertEqual(1, result["rejectedCount"])
        self.assertEqual("activity-not-after-opening-balance", result["rejected"][0]["reason"])
        self.assertEqual(3, len(repository.entries))

    def test_portfolio_lifecycle_projects_as_factual_abox_candidates(self):
        repository = MemoryInvestmentRepository()
        snapshot = live_snapshot()
        lifecycle = PortfolioAccountingService(repository).observe_snapshot(snapshot)

        graph = build_portfolio_ontology(
            snapshot.positions,
            snapshot.portfolio,
            portfolio_id=snapshot.account_id,
            runtime_context={
                "account": {"accountId": snapshot.account_id},
                "portfolioLifecycle": lifecycle,
            },
            include_tbox=False,
            include_presentation=False,
        )

        classes = {
            str((item.properties or {}).get("tboxClass") or "")
            for item in graph.entities
        }
        relation_types = {item.relation_type for item in graph.relations}
        self.assertIn("PortfolioDecisionCycle", classes)
        self.assertIn("PortfolioActionCandidate", classes)
        self.assertIn("EVALUATES_PORTFOLIO_CANDIDATE", relation_types)
        candidates = [item for item in graph.entities if (item.properties or {}).get("tboxClass") == "PortfolioActionCandidate"]
        self.assertTrue(candidates)
        self.assertTrue(all((item.properties or {}).get("executable") is False for item in candidates))

    def test_lifecycle_storage_tables_are_declared(self):
        schema = "\n".join(MYSQL_SCHEMA)

        self.assertIn("CREATE TABLE IF NOT EXISTS portfolio_reconciliations", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS portfolio_exposure_snapshots", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS investment_action_plan_reviews", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS broker_activity_sync_states", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS portfolio_decision_cycles", schema)


if __name__ == "__main__":
    unittest.main()
