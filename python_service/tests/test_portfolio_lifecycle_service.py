import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.investment_outcome_observation_service import InvestmentOutcomeObservationService
from digital_twin.application.investment_domain_service import InvestmentDomainService
from digital_twin.application.notification_ai_decision_context import NotificationAIDecisionContextEnricher
from digital_twin.application.portfolio_lifecycle_service import (
    DecisionActionPlanningService,
    PortfolioAccountingService,
    TradeExecutionService,
)
from digital_twin.domain.investment_brain import ObservedOutcome
from digital_twin.domain.investment_mandate import InvestmentMandate
from digital_twin.domain.investment_outcomes import decision_quality_summary
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position
from digital_twin.domain.portfolio_activity_episode import PortfolioSnapshotCheckpoint
from digital_twin.domain.portfolio_analytics import (
    portfolio_risk_event_materiality,
    portfolio_risk_snapshot,
    with_policy_limits,
)
from digital_twin.domain.portfolio_ledger import (
    INFERRED_CORPORATE_ACTION,
    INFERRED_POSITION_DECREASE,
    INFERRED_POSITION_EXIT,
    INFERRED_POSITION_INCREASE,
    SNAPSHOT_CASH_ADJUSTMENT,
    PortfolioLedger,
    PortfolioLedgerEntry,
    execution_ledger_entries,
)
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_validator import validate_ontology
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.trade_execution import ActionEnvelope, ActionPlan, ExecutionEpisode, OrderIntent, TradeFill
from digital_twin.infrastructure.mysql_operational_connection import MYSQL_SCHEMA
from digital_twin.infrastructure.event_bus import EventBus


NOW = "2026-08-12T06:00:00Z"


class MemoryInvestmentRepository:
    def __init__(self):
        self.entries = []
        self.reconciliations = {}
        self.exposures = {}
        self.risk_snapshots = {}
        self.proposals = {}
        self.plans = {}
        self.plan_reviews = []
        self.attributions = []
        self.decision_reviews = []
        self.executions = []
        self.decision_cycles = {}
        self.checkpoint = None
        self.activity_episodes = []
        self.state_snapshots = []
        self.action_observations = []
        self.notification_jobs = []
        self.snapshot_quarantines = []
        self.reasoning_events = []
        self.lifecycle = {}
        self.prior_decisions = {}
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

    def snapshot_checkpoint(self, _portfolio_id):
        return self.checkpoint

    def advance_snapshot_checkpoint(self, expected_checkpoint_version, checkpoint):
        actual = self.checkpoint.version if self.checkpoint else 0
        if actual != expected_checkpoint_version:
            return {"status": "checkpoint-conflict", "actualCheckpointVersion": actual}
        self.checkpoint = replace(checkpoint, version=actual + 1)
        return {"status": "unchanged", "actualCheckpointVersion": actual + 1}

    def latest_decision_before(self, _account_id, symbol, _observed_at):
        return dict(self.prior_decisions.get(symbol) or {})

    def record_snapshot_quarantine(self, checkpoint, reason, previous_checkpoint=None):
        payload = {
            **checkpoint.to_dict(),
            "reason": reason,
            "previousCheckpoint": previous_checkpoint.to_dict() if previous_checkpoint else {},
        }
        self.snapshot_quarantines.append(payload)
        return payload

    def commit_snapshot_observation(
        self,
        expected_checkpoint_version,
        checkpoint,
        ledger_entries,
        activity_episode,
        state_snapshot,
        reconciliation,
        exposure,
        rebalance_proposal,
        decision_cycle,
        decision_action_observations=None,
        domain_event=None,
        notification_job=None,
        reasoning_event=None,
        risk_snapshot=None,
    ):
        actual = self.checkpoint.version if self.checkpoint else 0
        if actual != expected_checkpoint_version:
            return {"status": "checkpoint-conflict", "actualCheckpointVersion": actual, "insertedCount": 0}
        inserted = self.append_ledger_entries(ledger_entries)
        if activity_episode:
            self.activity_episodes.append(activity_episode)
        self.state_snapshots.append(state_snapshot)
        self.save_reconciliation(reconciliation)
        self.save_exposure_snapshot(exposure)
        if risk_snapshot:
            self.save_risk_snapshot(risk_snapshot)
        if rebalance_proposal:
            self.save_rebalance_proposal(rebalance_proposal)
        self.save_portfolio_decision_cycle(decision_cycle)
        self.action_observations.extend(list(decision_action_observations or []))
        if notification_job:
            self.notification_jobs.append(notification_job)
        if reasoning_event:
            self.reasoning_events.append(reasoning_event)
        self.checkpoint = replace(checkpoint, version=actual + 1)
        return {
            "status": "committed",
            "actualCheckpointVersion": actual + 1,
            "insertedCount": inserted,
            "notificationQueued": bool(notification_job),
        }

    def save_reconciliation(self, item):
        self.reconciliations.setdefault(item.reconciliation_id, item)
        return item

    def save_exposure_snapshot(self, item):
        self.exposures.setdefault(item.snapshot_id, item)
        return item

    def save_risk_snapshot(self, item):
        self.risk_snapshots[item.risk_snapshot_id] = item
        return item

    def latest_portfolio_risk_event(self, _portfolio_id):
        for event in reversed(self.reasoning_events):
            if getattr(event, "name", "") == "portfolio.risk_observed":
                return dict(event.payload or {})
        return {}

    def save_portfolio_analysis_bundle(self, risk_snapshot, exposure, rebalance_proposal, decision_cycle, domain_event=None, reasoning_event=None):
        changed = risk_snapshot.risk_snapshot_id not in self.risk_snapshots
        self.save_risk_snapshot(risk_snapshot)
        self.save_exposure_snapshot(exposure)
        if rebalance_proposal:
            self.save_rebalance_proposal(rebalance_proposal)
        self.save_portfolio_decision_cycle(decision_cycle)
        if changed and domain_event:
            self.reasoning_events.append(domain_event)
        if changed and reasoning_event:
            self.reasoning_events.append(reasoning_event)
        return {"status": "saved", "riskChanged": changed}

    def save_rebalance_proposal(self, item):
        self.proposals[item.proposal_id] = item
        return item

    def save_portfolio_decision_cycle(self, item):
        self.decision_cycles[item.cycle_id] = item
        return item

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

    def execution_episode_for_plan(self, plan_id):
        return next((item for item in reversed(self.executions) if item.action_plan_id == plan_id), None)

    def save_execution_with_ledger(self, episode, plan, domain_event=None):
        del domain_event
        self.executions.append(episode)
        rows = execution_ledger_entries(episode, plan, self.entries)
        inserted = self.append_ledger_entries(rows)
        return {
            "status": episode.status,
            "executionEpisode": episode.to_dict(),
            "actualLedgerEntryCount": inserted,
            "supersededInferredEntryCount": sum(
                len(item.payload.get("supersedesEntryIds") or []) for item in rows
            ),
        }

    def lifecycle_trace(self, _episode_id):
        return {"executionEpisodes": [], "fills": []}

    def latest_portfolio_lifecycle(self, _portfolio_id):
        return dict(self.lifecycle)

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


def live_snapshot(
    quantity=10,
    price=70000,
    cash=200000,
    average_price=65000,
    generated_at=NOW,
    mode="live",
    status="토스 계좌 동기화",
    complete=True,
    include_position=True,
    extra_positions=None,
):
    value = quantity * price
    positions = []
    if include_position:
        positions.append(Position(
            symbol="035420",
            name="NAVER",
            market="KR",
            currency="KRW",
            quantity=quantity,
            sellable_quantity=quantity,
            average_price=average_price,
            current_price=price,
            market_value=value,
            market_value_krw=value,
            sector="technology",
            source="holding",
        ))
    positions.extend(list(extra_positions or []))
    invested = sum(item.market_value_krw for item in positions)
    total = invested + cash
    return AccountSnapshot(
        account_id="main",
        account_label="Main",
        provider="toss",
        mode=mode,
        status=status,
        generated_at=generated_at,
        portfolio=PortfolioSummary(
            total=total,
            invested=invested,
            cash=cash,
            markets=[{"market": "KR", "value": invested, "ratio": invested / total * 100 if total else 0}],
            sectors=[{"sector": "technology", "value": invested, "ratio": invested / total * 100 if total else 0}],
            concentration=invested / total * 100 if total else 0,
        ),
        positions=positions,
        metadata={
            "accountSnapshotCompleteness": {
                "holdings": "complete" if complete else "incomplete",
                "cash": "complete" if complete else "incomplete",
                "source": "test-account-provider",
            },
        },
    )


class MemoryPortfolioTimeSeriesStore:
    def load_portfolio_analysis_series(self, _account_id, symbols, as_of="", limit_per_symbol=260):
        del as_of
        rows = {}
        for symbol in symbols:
            base = 100.0 if symbol == "KOSPI" else 80.0
            rows[symbol] = [
                {
                    "bucketAt": "2026-07-" + str(day).zfill(2) + "T00:00:00Z",
                    "marketSessionDate": "2026-07-" + str(day).zfill(2),
                    "currentPrice": base + day * (1.0 if symbol == "KOSPI" else 1.5),
                    "tradingValue": 1_000_000 + day,
                }
                for day in range(1, 31)
            ][-limit_per_symbol:]
        return rows


class MutablePortfolioTimeSeriesStore(MemoryPortfolioTimeSeriesStore):
    def __init__(self):
        self.latest_price_delta = 0.0

    def load_portfolio_analysis_series(self, account_id, symbols, as_of="", limit_per_symbol=260):
        rows = super().load_portfolio_analysis_series(account_id, symbols, as_of, limit_per_symbol)
        if rows.get("035420"):
            rows["035420"][-1]["currentPrice"] += self.latest_price_delta
        return rows


class PortfolioLifecycleServiceTests(unittest.TestCase):
    def test_portfolio_risk_keeps_cash_as_zero_return_weight(self):
        risk = portfolio_risk_snapshot(
            "portfolio:main",
            NOW,
            {
                "035420": [
                    {"bucketAt": "2026-08-01T00:00:00Z", "currentPrice": 100},
                    {"bucketAt": "2026-08-02T00:00:00Z", "currentPrice": 110},
                    {"bucketAt": "2026-08-03T00:00:00Z", "currentPrice": 121},
                ],
            },
            {"035420": 50},
        )

        self.assertEqual(10.25, risk.period_return_pct)

    def test_risk_snapshot_identity_changes_with_mandate_policy(self):
        measured = portfolio_risk_snapshot(
            "portfolio:main",
            NOW,
            {"035420": [
                {"bucketAt": "2026-08-01T00:00:00Z", "currentPrice": 100},
                {"bucketAt": "2026-08-02T00:00:00Z", "currentPrice": 90},
            ]},
            {"035420": 100},
        )

        conservative = with_policy_limits(
            measured,
            max_volatility_pct=10,
            max_drawdown_pct=5,
            max_correlation=0.5,
            policy_version="policy:conservative",
        )
        flexible = with_policy_limits(
            measured,
            max_volatility_pct=100,
            max_drawdown_pct=50,
            max_correlation=1,
            policy_version="policy:flexible",
        )

        self.assertNotEqual(conservative.risk_snapshot_id, flexible.risk_snapshot_id)
        self.assertEqual(-10, measured.maximum_drawdown_pct)
        self.assertGreater(conservative.drawdown_policy_delta_pct, 0)
        self.assertEqual(0, flexible.drawdown_policy_delta_pct)

    def test_risk_event_materiality_accumulates_against_last_emitted_baseline(self):
        baseline_risk = with_policy_limits(
            portfolio_risk_snapshot(
                "portfolio:main",
                NOW,
                {"035420": [
                    {"bucketAt": "2026-08-01T00:00:00Z", "currentPrice": 100},
                    {"bucketAt": "2026-08-02T00:00:00Z", "currentPrice": 90},
                    {"bucketAt": "2026-08-03T00:00:00Z", "currentPrice": 95},
                ]},
                {"035420": 50},
            ),
            max_volatility_pct=100,
            max_drawdown_pct=20,
            max_correlation=1,
            policy_version="policy:1",
        )
        baseline_event = InvestmentDomainService(MemoryInvestmentRepository()).risk_observed_event(
            baseline_risk,
            ["035420"],
        ).payload
        small = replace(
            baseline_risk,
            risk_snapshot_id="risk:small",
            annualized_volatility_pct=baseline_risk.annualized_volatility_pct + 0.4,
        )
        accumulated = replace(
            small,
            risk_snapshot_id="risk:accumulated",
            annualized_volatility_pct=baseline_risk.annualized_volatility_pct + 1.1,
        )

        self.assertFalse(portfolio_risk_event_materiality(baseline_event, small).material)
        result = portfolio_risk_event_materiality(baseline_event, accumulated)
        self.assertTrue(result.material)
        self.assertIn("annualizedVolatilityPct-material-change", result.reason_codes)

    def test_risk_event_materiality_always_emits_policy_and_data_transitions(self):
        risk = with_policy_limits(
            portfolio_risk_snapshot(
                "portfolio:main",
                NOW,
                {"035420": [
                    {"bucketAt": "2026-08-01T00:00:00Z", "currentPrice": 100},
                    {"bucketAt": "2026-08-02T00:00:00Z", "currentPrice": 90},
                ]},
                {"035420": 100},
            ),
            max_volatility_pct=100,
            max_drawdown_pct=20,
            max_correlation=1,
            policy_version="policy:1",
        )
        baseline = InvestmentDomainService(MemoryInvestmentRepository()).risk_observed_event(
            risk,
            ["035420"],
        ).payload
        breached = replace(
            risk,
            risk_snapshot_id="risk:breached",
            volatility_policy_delta_pct=0.1,
            data_state="complete",
            missing_data=[],
        )

        result = portfolio_risk_event_materiality(baseline, breached)

        self.assertTrue(result.material)
        self.assertIn("risk-policy-breach-transition", result.reason_codes)
        self.assertIn("risk-data-state-change", result.reason_codes)

    def test_decision_quality_uses_only_complete_attributions_for_return_statistics(self):
        summary = decision_quality_summary(
            [
                {"dataState": "complete", "activeReturnPct": 2, "horizonMinutes": 60},
                {"dataState": "complete", "activeReturnPct": -1, "horizonMinutes": 60},
                {"dataState": "partial", "activeReturnPct": 99, "horizonMinutes": 60},
            ],
            [{"executionCompliant": True, "evidenceStillValid": True}],
        )

        self.assertEqual(2, summary["completeSampleCount"])
        self.assertEqual(0.5, summary["meanActiveReturnPct"])
        self.assertEqual(50.0, summary["positiveActiveRatePct"])

    def test_action_plan_is_capped_by_matching_portfolio_policy_candidate(self):
        repository = MemoryInvestmentRepository()
        repository.mandate = InvestmentMandate.from_profile(
            "main", "portfolio:main", {"maxPositionWeightPct": 90, "minCashWeightPct": 0}, NOW,
        )
        repository.lifecycle = {
            "portfolioDecisionCycle": {
                "cycleId": "cycle:cap",
                "candidates": [{
                    "candidateId": "candidate:cap",
                    "candidateType": "INCREASE_UNDERWEIGHT_ALLOCATION",
                    "affectedSymbol": "035420",
                    "maximumNotional": 70_000,
                }],
            },
            "portfolioRiskSnapshot": {"riskSnapshotId": "risk:cap"},
        }
        state = live_snapshot(cash=2_000_000).to_monitor_state()
        episode = SimpleNamespace(
            episode_id="decision:cap", portfolio_id="portfolio:main", account_id="main",
            symbol="035420", action="ADD", mandate_version=repository.mandate.policy_version,
            inference_generation_id="generation:cap", decided_at=NOW,
            selected_hypothesis_id="", hypothesis_set=SimpleNamespace(hypotheses=[]),
        )

        plan = DecisionActionPlanningService(
            repository, SimpleNamespace(load_previous=lambda _account_id: state),
            {"investmentActionPlanSlicePct": 100},
        ).prepare(episode, {"ontologyRelationContext": {"allowedActions": ["ADD"]}})

        self.assertEqual(70_000, plan.sizing_basis["policyCandidateMaximumNotionalBase"])
        self.assertLessEqual(plan.order_intents[0].notional, 70_000)
        self.assertEqual(["candidate:cap"], plan.sizing_basis["policyCandidateIds"])

    def test_stored_history_refreshes_risk_when_account_balance_is_unchanged(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(
            repository,
            market_time_series_store=MemoryPortfolioTimeSeriesStore(),
        )

        first = service.observe_snapshot(live_snapshot())
        unchanged = service.observe_snapshot(live_snapshot(generated_at="2026-08-12T06:10:00Z"))

        self.assertEqual("complete", first["portfolioRiskSnapshot"]["dataState"])
        self.assertGreater(first["portfolioRiskSnapshot"]["sampleCount"], 20)
        self.assertIsNotNone(first["portfolioRiskSnapshot"]["positions"][0]["beta"])
        self.assertEqual("unchanged", unchanged["status"])
        self.assertIn("portfolioRiskSnapshot", unchanged)
        self.assertTrue(repository.risk_snapshots)

    def test_small_risk_measurement_change_does_not_enqueue_another_reasoning_event(self):
        repository = MemoryInvestmentRepository()
        series = MutablePortfolioTimeSeriesStore()
        domain_service = InvestmentDomainService(repository, EventBus())
        service = PortfolioAccountingService(
            repository,
            investment_domain_service=domain_service,
            market_time_series_store=series,
        )
        service.observe_snapshot(live_snapshot())
        series.latest_price_delta = 0.01
        service.observe_snapshot(live_snapshot(generated_at="2026-08-12T06:10:00Z"))
        initial_event_count = len(repository.reasoning_events)
        initial_risk_count = len(repository.risk_snapshots)

        series.latest_price_delta = 0.02
        service.observe_snapshot(live_snapshot(generated_at="2026-08-12T06:20:00Z"))

        self.assertGreater(len(repository.risk_snapshots), initial_risk_count)
        self.assertEqual(initial_event_count, len(repository.reasoning_events))

    def test_confirmed_fill_supersedes_matching_snapshot_inference_without_double_counting(self):
        portfolio_id = "portfolio:main"
        opening_position = PortfolioLedgerEntry.create(
            portfolio_id, "main", "OPENING_POSITION", "2026-08-12T05:00:00Z",
            entry_id="opening-position", source_reference="opening-position",
            symbol="035420", quantity=10, unit_price=100,
        )
        opening_cash = PortfolioLedgerEntry.create(
            portfolio_id, "main", "OPENING_CASH", "2026-08-12T05:00:00Z",
            entry_id="opening-cash", source_reference="opening-cash", amount=1000,
        )
        inferred_position = PortfolioLedgerEntry.create(
            portfolio_id, "main", INFERRED_POSITION_INCREASE, "2026-08-12T06:10:00Z",
            entry_id="inferred-position", source_reference="snapshot:1:position:035420",
            symbol="035420", quantity=1, unit_price=100,
            payload={"replaceableByActualActivity": True, "observationId": "snapshot:1"},
        )
        inferred_cash = PortfolioLedgerEntry.create(
            portfolio_id, "main", SNAPSHOT_CASH_ADJUSTMENT, "2026-08-12T06:10:00Z",
            entry_id="inferred-cash", source_reference="snapshot:1:cash:KRW", amount=-100,
            payload={"replaceableByActualActivity": True, "observationId": "snapshot:1"},
        )
        plan = replace(ActionPlan.create(
            portfolio_id, "decision:fill", "ADD", "policy:1", "generation:1",
            order_intents=[OrderIntent("intent:fill", "035420", "BUY", 1, limit_price=100)],
        ), status="approved")
        episode = ExecutionEpisode.for_plan(plan, "2026-08-12T06:05:00Z")
        episode.record_fill(TradeFill(
            "fill:1", "provider-fill:1", "intent:fill", "035420", "BUY",
            1, 100, 0, "KRW", "2026-08-12T06:05:00Z",
        ))
        episode.complete("2026-08-12T06:05:01Z")

        actual = execution_ledger_entries(
            episode, plan, [opening_position, opening_cash, inferred_position, inferred_cash]
        )
        state = PortfolioLedger(portfolio_id, "main").replay(
            [opening_position, opening_cash, inferred_position, inferred_cash, *actual]
        )

        self.assertEqual(Decimal("11.0"), state.quantity("035420"))
        self.assertEqual(Decimal("900.0"), state.cash["KRW"])
        self.assertEqual({"inferred-position", "inferred-cash"}, set(actual[0].payload["supersedesEntryIds"]))

    def test_split_fills_supersede_one_aggregate_snapshot_inference(self):
        repository = MemoryInvestmentRepository()
        portfolio_id = "portfolio:main"
        repository.entries = [
            PortfolioLedgerEntry.create(
                portfolio_id, "main", "OPENING_POSITION", "2026-08-12T05:00:00Z",
                entry_id="opening-position-split", source_reference="opening-position-split",
                symbol="035420", quantity=10, unit_price=100,
            ),
            PortfolioLedgerEntry.create(
                portfolio_id, "main", "OPENING_CASH", "2026-08-12T05:00:00Z",
                entry_id="opening-cash-split", source_reference="opening-cash-split", amount=1000,
            ),
            PortfolioLedgerEntry.create(
                portfolio_id, "main", INFERRED_POSITION_INCREASE, "2026-08-12T06:10:00Z",
                entry_id="inferred-position-split", source_reference="snapshot:split:position:035420",
                symbol="035420", quantity=2, unit_price=100,
                payload={"replaceableByActualActivity": True, "observationId": "snapshot:split"},
            ),
            PortfolioLedgerEntry.create(
                portfolio_id, "main", SNAPSHOT_CASH_ADJUSTMENT, "2026-08-12T06:10:00Z",
                entry_id="inferred-cash-split", source_reference="snapshot:split:cash:KRW", amount=-200,
                payload={"replaceableByActualActivity": True, "observationId": "snapshot:split"},
            ),
        ]
        plan = replace(ActionPlan.create(
            portfolio_id,
            "decision:split-fill",
            "ADD",
            repository.mandate.policy_version,
            "generation:1",
            order_intents=[OrderIntent("intent:split", "035420", "BUY", 2, limit_price=100)],
        ), status="approved")
        repository.save_action_plan(plan)
        service = TradeExecutionService(repository)
        fill_template = {
            "orderIntentId": "intent:split", "symbol": "035420", "side": "BUY",
            "quantity": 1, "price": 100, "fee": 0, "currency": "KRW",
        }

        service.record_fills(plan.plan_id, [{
            **fill_template,
            "providerExecutionId": "provider:split:1",
            "executedAt": "2026-08-12T06:01:00Z",
        }])
        service.record_fills(plan.plan_id, [{
            **fill_template,
            "providerExecutionId": "provider:split:2",
            "executedAt": "2026-08-12T06:02:00Z",
        }])
        state = PortfolioLedger(portfolio_id, "main").replay(repository.entries)

        self.assertEqual(Decimal("12.0"), state.quantity("035420"))
        self.assertEqual(Decimal("800.0"), state.cash["KRW"])
        actual_rows = [item for item in repository.entries if item.entry_type == "BUY"]
        self.assertEqual(
            {"inferred-position-split", "inferred-cash-split"},
            set(actual_rows[-1].payload["supersedesEntryIds"]),
        )

    def test_mandate_round_trip_preserves_explicit_zero_limits(self):
        payload = MemoryInvestmentRepository().mandate.to_dict()
        payload["min_cash_weight_pct"] = 0
        payload.pop("minCashWeightPct", None)

        mandate = InvestmentMandate.from_dict(payload)

        self.assertEqual(0, mandate.min_cash_weight_pct)

    def test_snapshot_bootstrap_and_inferred_increase_are_idempotent(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(repository, market_time_series_store=MemoryPortfolioTimeSeriesStore())

        first = service.observe_snapshot(live_snapshot())
        second = service.observe_snapshot(live_snapshot())
        changed = service.observe_snapshot(live_snapshot(quantity=11, generated_at="2026-08-12T06:10:00Z"))
        repeated = service.observe_snapshot(live_snapshot(quantity=11, generated_at="2026-08-12T06:20:00Z"))

        self.assertEqual(2, first["openingEntryCount"])
        self.assertEqual("matched", first["reconciliation"]["status"])
        self.assertEqual(0, second["openingEntryCount"])
        self.assertEqual(1, changed["inferredEntryCount"])
        self.assertEqual(INFERRED_POSITION_INCREASE, changed["inferredActivities"][0]["entryType"])
        self.assertEqual("matched", changed["reconciliation"]["status"])
        self.assertEqual(0, repeated["inferredEntryCount"])
        self.assertEqual(3, len(repository.entries))
        self.assertEqual(2, len(repository.decision_cycles))
        quantity_difference = next(
            item for item in changed["reconciliation"]["differences"]
            if item["differenceType"] == "position-quantity"
        )
        self.assertEqual("11", quantity_difference["expected"])
        self.assertEqual("11", quantity_difference["observed"])

    def test_repeated_round_trip_balance_changes_are_distinct_observations(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(repository)
        service.observe_snapshot(live_snapshot(quantity=10))

        service.observe_snapshot(live_snapshot(quantity=11, generated_at="2026-08-12T06:10:00Z"))
        service.observe_snapshot(live_snapshot(quantity=10, generated_at="2026-08-12T06:20:00Z"))
        final = service.observe_snapshot(live_snapshot(quantity=11, generated_at="2026-08-12T06:30:00Z"))

        self.assertEqual(1, final["inferredEntryCount"])
        self.assertEqual(5, len(repository.entries))
        self.assertEqual(3, len({
            item.source_reference
            for item in repository.entries
            if item.entry_type in {INFERRED_POSITION_INCREASE, INFERRED_POSITION_DECREASE}
        }))
        state = PortfolioLedger("portfolio:main", "main").replay(repository.entries)
        self.assertEqual("11", str(state.quantity("035420")))

    def test_exposure_breach_creates_review_only_rebalance_proposal(self):
        repository = MemoryInvestmentRepository()

        result = PortfolioAccountingService(repository).observe_snapshot(live_snapshot())

        self.assertGreater(result["exposureSnapshot"]["metrics"][0]["policyDeltaPct"], 0)
        self.assertEqual("review-required", result["rebalanceProposal"]["status"])
        self.assertTrue(result["rebalanceProposal"]["legs"])

    def test_target_allocation_replaces_duplicate_position_policy_band(self):
        repository = MemoryInvestmentRepository()
        repository.mandate = InvestmentMandate.from_profile(
            "main",
            "portfolio:main",
            {
                "maxPositionWeightPct": 25,
                "maxSectorWeightPct": 100,
                "minCashWeightPct": 0,
                "targetAllocations": {"position:035420": 20},
                "allocationBandPct": 5,
            },
            NOW,
        )

        result = PortfolioAccountingService(repository).observe_snapshot(live_snapshot())
        drifts = result["rebalanceProposal"]["drifts"]

        self.assertEqual(len(drifts), len({item["allocationKey"] for item in drifts}))
        position_drift = next(item for item in drifts if item["allocationKey"] == "position:035420")
        self.assertEqual(20, position_drift["band"]["target_weight_pct"])

    def test_confirmed_fills_merge_incrementally_and_reject_overfill(self):
        repository = MemoryInvestmentRepository()
        plan = replace(ActionPlan.create(
            "portfolio:main",
            "decision:incremental-fill",
            "ADD",
            repository.mandate.policy_version,
            "generation:1",
            order_intents=[OrderIntent("intent:incremental", "035420", "BUY", 2, limit_price=100)],
        ), status="approved")
        repository.save_action_plan(plan)
        service = TradeExecutionService(repository)

        first = service.record_fills(plan.plan_id, [{
            "providerExecutionId": "provider:fill:1",
            "orderIntentId": "intent:incremental",
            "symbol": "035420",
            "side": "BUY",
            "quantity": 1,
            "price": 100,
            "fee": 0,
            "currency": "KRW",
            "executedAt": "2026-08-12T06:01:00Z",
        }])
        second = service.record_fills(plan.plan_id, [{
            "providerExecutionId": "provider:fill:2",
            "orderIntentId": "intent:incremental",
            "symbol": "035420",
            "side": "BUY",
            "quantity": 1,
            "price": 101,
            "fee": 0,
            "currency": "KRW",
            "executedAt": "2026-08-12T06:02:00Z",
        }])
        duplicate = service.record_fills(plan.plan_id, [{
            "providerExecutionId": "provider:fill:2",
            "orderIntentId": "intent:incremental",
            "symbol": "035420",
            "side": "BUY",
            "quantity": 1,
            "price": 101,
            "fee": 0,
            "currency": "KRW",
            "executedAt": "2026-08-12T06:02:00Z",
        }])
        overfill = service.record_fills(plan.plan_id, [{
            "providerExecutionId": "provider:fill:3",
            "orderIntentId": "intent:incremental",
            "symbol": "035420",
            "side": "BUY",
            "quantity": 1,
            "price": 102,
            "fee": 0,
            "currency": "KRW",
            "executedAt": "2026-08-12T06:03:00Z",
        }])

        self.assertEqual(1, first["actualLedgerEntryCount"])
        self.assertEqual(1, second["actualLedgerEntryCount"])
        self.assertEqual(2, len(second["executionEpisode"]["fills"]))
        self.assertEqual(0, duplicate["actualLedgerEntryCount"])
        self.assertIn("fill-quantity-exceeds-intent", overfill["validationErrors"])

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

    def test_new_position_decrease_exit_and_cash_change_are_recorded_without_realized_profit(self):
        repository = MemoryInvestmentRepository()
        PortfolioAccountingService(repository).observe_snapshot(live_snapshot())
        lg = Position(
            symbol="066570",
            name="LG전자",
            market="KR",
            currency="KRW",
            quantity=3,
            sellable_quantity=3,
            average_price=100000,
            current_price=105000,
            market_value=315000,
            market_value_krw=315000,
            sector="technology",
        )
        service = PortfolioAccountingService(repository)

        added = service.observe_snapshot(live_snapshot(
            cash=150000,
            generated_at="2026-08-12T06:10:00Z",
            extra_positions=[lg],
        ))
        reduced = service.observe_snapshot(live_snapshot(
            quantity=6,
            cash=150000,
            generated_at="2026-08-12T06:20:00Z",
            extra_positions=[lg],
        ))
        exited = service.observe_snapshot(live_snapshot(
            quantity=0,
            cash=150000,
            generated_at="2026-08-12T06:30:00Z",
            include_position=False,
            extra_positions=[lg],
        ))

        self.assertEqual(
            {INFERRED_POSITION_INCREASE, SNAPSHOT_CASH_ADJUSTMENT},
            {item["entryType"] for item in added["inferredActivities"]},
        )
        self.assertEqual(INFERRED_POSITION_DECREASE, reduced["inferredActivities"][0]["entryType"])
        self.assertEqual(INFERRED_POSITION_EXIT, exited["inferredActivities"][0]["entryType"])
        state = PortfolioLedger("portfolio:main", "main").replay(repository.entries)
        self.assertEqual("0", str(state.quantity("035420")))
        self.assertEqual("3", str(state.quantity("066570")))
        self.assertEqual(150000, float(state.cash["KRW"]))
        self.assertEqual({}, state.realized_profit_loss)
        self.assertTrue(all(
            item.payload.get("realizedProfitLossKnown") is False
            for item in repository.entries
            if item.entry_type in {INFERRED_POSITION_DECREASE, INFERRED_POSITION_EXIT}
        ))

    def test_possible_split_is_low_confidence_and_does_not_claim_a_trade(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(repository)
        service.observe_snapshot(live_snapshot(quantity=10, average_price=100000))

        result = service.observe_snapshot(live_snapshot(
            quantity=20,
            average_price=50000,
            generated_at="2026-08-12T06:10:00Z",
        ))

        activity = result["inferredActivities"][0]
        self.assertEqual(INFERRED_CORPORATE_ACTION, activity["entryType"])
        self.assertEqual("possible-corporate-action", activity["classification"])
        self.assertEqual("low", activity["confidence"])
        self.assertTrue(activity["replaceableByActualActivity"])
        state = PortfolioLedger("portfolio:main", "main").replay(repository.entries)
        self.assertEqual("20", str(state.quantity("035420")))
        self.assertEqual("50000", str(state.average_cost("035420")))

    def test_incomplete_live_snapshot_cannot_mutate_the_ledger(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(repository)
        service.observe_snapshot(live_snapshot())

        result = service.observe_snapshot(live_snapshot(
            quantity=0,
            cash=0,
            include_position=False,
            status="계좌 식별값 없음",
            complete=False,
            generated_at="2026-08-12T06:10:00Z",
        ))

        self.assertEqual("skipped-untrusted-snapshot", result["status"])
        self.assertEqual("provider-declared-incomplete", result["snapshotTrust"]["reason"])
        self.assertEqual(2, len(repository.entries))

    def test_inferred_change_publishes_one_compact_ledger_event(self):
        repository = MemoryInvestmentRepository()
        event_bus = EventBus()
        service = PortfolioAccountingService(
            repository,
            investment_domain_service=InvestmentDomainService(repository, event_bus),
        )
        service.observe_snapshot(live_snapshot())

        service.observe_snapshot(live_snapshot(quantity=11, generated_at="2026-08-12T06:10:00Z"))

        event = event_bus.published[-1]
        self.assertEqual("portfolio.ledger_recorded", event.name)
        self.assertTrue(event.payload["materialSnapshotChange"])
        self.assertEqual(1, len(event.payload["inferredActivities"]))
        self.assertEqual("position-increase", event.payload["inferredActivities"][0]["classification"])

    def test_portfolio_lifecycle_projects_as_factual_abox_candidates(self):
        repository = MemoryInvestmentRepository()
        snapshot = live_snapshot()
        service = PortfolioAccountingService(repository, market_time_series_store=MemoryPortfolioTimeSeriesStore())
        service.observe_snapshot(snapshot)
        snapshot = live_snapshot(quantity=11, generated_at="2026-08-12T06:10:00Z")
        lifecycle = service.observe_snapshot(snapshot)

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
        self.assertIn("InferredPortfolioActivity", classes)
        self.assertIn("PortfolioActivityEpisode", classes)
        self.assertIn("PortfolioStateSnapshot", classes)
        self.assertIn("PortfolioRiskSnapshot", classes)
        self.assertIn("PositionRiskMetric", classes)
        self.assertIn("BenchmarkIndex", classes)
        self.assertIn("RebalanceScenario", classes)
        self.assertIn("EVALUATES_PORTFOLIO_CANDIDATE", relation_types)
        self.assertIn("RECORDS_PORTFOLIO_ACTIVITY", relation_types)
        self.assertIn("INFERRED_FROM_SNAPSHOT_CHANGE", relation_types)
        self.assertIn("HAS_PORTFOLIO_ACTIVITY", relation_types)
        self.assertIn("HAS_PORTFOLIO_STATE", relation_types)
        self.assertIn("HAS_RISK_SNAPSHOT", relation_types)
        self.assertIn("HAS_POSITION_RISK", relation_types)
        self.assertIn("HAS_BETA_TO", relation_types)
        self.assertIn("HAS_REBALANCE_SCENARIO", relation_types)
        candidates = [item for item in graph.entities if (item.properties or {}).get("tboxClass") == "PortfolioActionCandidate"]
        self.assertTrue(candidates)
        self.assertTrue(all((item.properties or {}).get("executable") is False for item in candidates))

    def test_target_scoped_lifecycle_omits_relations_to_out_of_scope_stocks(self):
        snapshot = live_snapshot()
        lifecycle = {
            "recentInferredActivities": [
                {
                    "entryId": "activity:outside",
                    "symbol": "028260",
                    "classification": "position-increase",
                }
            ],
            "recentActivityEpisodes": [
                {
                    "episodeId": "episode:outside",
                    "symbols": ["028260"],
                    "ledgerEntryIds": ["activity:outside"],
                    "classification": "probable-buy",
                }
            ],
            "portfolioState": {
                "stateId": "state:latest",
                "positions": [{"symbol": "028260", "increaseCount20d": 2}],
            },
            "decisionActionObservations": [
                {
                    "observationId": "observation:outside",
                    "symbol": "028260",
                    "priorDecisionEpisodeId": "decision-episode:outside",
                }
            ],
        }

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

        self.assertEqual("valid", validate_ontology(graph).status)
        self.assertFalse(any(
            item.source == "stock:028260" or item.target == "stock:028260"
            for item in graph.relations
        ))

    def test_lifecycle_storage_tables_are_declared(self):
        schema = "\n".join(MYSQL_SCHEMA)

        self.assertIn("CREATE TABLE IF NOT EXISTS portfolio_reconciliations", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS portfolio_exposure_snapshots", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS portfolio_risk_snapshots", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS investment_action_plan_reviews", schema)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS broker_activity_sync_states", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS portfolio_decision_cycles", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS portfolio_snapshot_checkpoints", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS portfolio_activity_episodes", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS portfolio_snapshot_quarantines", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS portfolio_state_snapshots", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS portfolio_decision_action_observations", schema)

    def test_checkpoint_advances_without_rewriting_unchanged_balance_and_rejects_stale_snapshot(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(repository)

        service.observe_snapshot(live_snapshot())
        unchanged = service.observe_snapshot(live_snapshot(generated_at="2026-08-12T06:10:00Z"))
        stale = service.observe_snapshot(live_snapshot(quantity=12, generated_at="2026-08-12T06:05:00Z"))

        self.assertEqual("unchanged", unchanged["status"])
        self.assertEqual(2, unchanged["snapshotCheckpoint"]["checkpointVersion"])
        self.assertEqual("stale", stale["status"])
        self.assertEqual("snapshot-older-than-checkpoint", stale["reason"])
        self.assertEqual(2, len(repository.entries))

    def test_sudden_empty_account_without_cash_offset_is_quarantined(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(repository)
        service.observe_snapshot(live_snapshot())

        result = service.observe_snapshot(live_snapshot(
            include_position=False,
            cash=200000,
            generated_at="2026-08-12T06:10:00Z",
        ))

        self.assertEqual("quarantined", result["status"])
        self.assertEqual("all-positions-disappeared-without-cash-offset", result["reason"])
        self.assertEqual("quarantined", result["snapshotCheckpoint"]["status"])
        self.assertEqual(1, len(repository.snapshot_quarantines))
        self.assertEqual(result["reason"], repository.snapshot_quarantines[0]["reason"])
        self.assertEqual(2, len(repository.entries))

    def test_position_exit_with_cash_offset_creates_probable_sell_episode_and_factual_outbox(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(
            repository,
            investment_domain_service=InvestmentDomainService(repository, EventBus()),
        )
        service.observe_snapshot(live_snapshot())

        result = service.observe_snapshot(live_snapshot(
            include_position=False,
            cash=900000,
            generated_at="2026-08-12T06:10:00Z",
        ))

        self.assertEqual("probable-sell", result["activityEpisode"]["classification"])
        self.assertTrue(result["factualNotificationQueued"])
        self.assertEqual("portfolioActivityObservation", repository.notification_jobs[0].message_type)
        self.assertIn("실제 주문·수수료·세금은 확인되지 않음", repository.notification_jobs[0].text)

    def test_repeated_increases_are_derived_into_portfolio_state(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(repository)
        service.observe_snapshot(live_snapshot())
        service.observe_snapshot(live_snapshot(quantity=11, cash=135000, generated_at="2026-08-12T06:10:00Z"))

        result = service.observe_snapshot(live_snapshot(quantity=12, cash=70000, generated_at="2026-08-12T06:20:00Z"))

        state = result["portfolioState"]["positions"][0]
        self.assertEqual(2, state["increaseCount20d"])
        self.assertEqual("probable-buy", result["activityEpisode"]["classification"])
        self.assertFalse(result["activityEpisode"]["executable"])

    def test_large_cash_mismatch_keeps_probable_activity_confidence_low(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(repository)
        service.observe_snapshot(live_snapshot())

        result = service.observe_snapshot(live_snapshot(
            quantity=11,
            cash=0,
            generated_at="2026-08-12T06:10:00Z",
        ))

        self.assertEqual("probable-buy", result["activityEpisode"]["classification"])
        self.assertEqual("low", result["activityEpisode"]["confidence"])

    def test_each_symbol_in_a_multi_position_change_is_compared_with_its_prior_decision(self):
        def second_position(quantity):
            return Position(
                symbol="005930",
                name="삼성전자",
                market="KR",
                currency="KRW",
                quantity=quantity,
                sellable_quantity=quantity,
                average_price=80000,
                current_price=82000,
                market_value=quantity * 82000,
                market_value_krw=quantity * 82000,
                sector="technology",
                source="holding",
            )

        repository = MemoryInvestmentRepository()
        repository.prior_decisions = {
            "035420": {"episodeId": "decision:naver", "action": "SELL", "decidedAt": "2026-08-12T05:50:00Z"},
            "005930": {"episodeId": "decision:samsung", "action": "ADD", "decidedAt": "2026-08-12T05:50:00Z"},
        }
        service = PortfolioAccountingService(
            repository,
            investment_domain_service=InvestmentDomainService(repository, EventBus()),
        )
        service.observe_snapshot(live_snapshot(extra_positions=[second_position(5)]))

        result = service.observe_snapshot(live_snapshot(
            quantity=11,
            cash=55000,
            generated_at="2026-08-12T06:10:00Z",
            extra_positions=[second_position(6)],
        ))

        observations = {item["symbol"]: item for item in result["decisionActionObservations"]}
        self.assertEqual({"005930", "035420"}, set(observations))
        self.assertEqual("contrary", observations["035420"]["correspondence"])
        self.assertEqual("aligned", observations["005930"]["correspondence"])
        self.assertIn("DecisionActionObservation", repository.reasoning_events[0].payload["factTypes"])

    def test_provider_account_fingerprint_change_is_quarantined(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(repository)
        first = live_snapshot()
        first.metadata["accountSourceFingerprint"] = "provider-account-a"
        service.observe_snapshot(first)
        changed = live_snapshot(generated_at="2026-08-12T06:10:00Z")
        changed.metadata["accountSourceFingerprint"] = "provider-account-b"

        result = service.observe_snapshot(changed)

        self.assertEqual("quarantined", result["status"])
        self.assertEqual("account-source-fingerprint-changed", result["reason"])

    def test_prior_ai_decision_is_compared_with_observed_account_direction_without_causality(self):
        repository = MemoryInvestmentRepository()
        repository.prior_decisions["035420"] = {
            "episodeId": "decision:previous",
            "action": "SELL",
            "decidedAt": "2026-08-12T05:50:00Z",
        }
        service = PortfolioAccountingService(repository)
        service.observe_snapshot(live_snapshot())

        result = service.observe_snapshot(live_snapshot(
            quantity=11,
            cash=135000,
            generated_at="2026-08-12T06:10:00Z",
        ))

        observed = result["decisionActionObservations"][0]
        self.assertEqual("contrary", observed["correspondence"])
        self.assertFalse(observed["causalityClaimed"])

    def test_checkpoint_conflict_retries_the_whole_observation(self):
        class ConflictOnceRepository(MemoryInvestmentRepository):
            def __init__(self):
                super().__init__()
                self.commit_calls = 0

            def commit_snapshot_observation(self, *args, **kwargs):
                self.commit_calls += 1
                if self.commit_calls == 2:
                    return {"status": "checkpoint-conflict", "actualCheckpointVersion": 1, "insertedCount": 0}
                return super().commit_snapshot_observation(*args, **kwargs)

        repository = ConflictOnceRepository()
        service = PortfolioAccountingService(repository)
        service.observe_snapshot(live_snapshot())

        result = service.observe_snapshot(live_snapshot(quantity=11, generated_at="2026-08-12T06:10:00Z"))

        self.assertEqual("ready", result["status"])
        self.assertEqual(3, repository.commit_calls)
        self.assertEqual(3, len(repository.entries))

    def test_portfolio_activity_rules_and_ai_context_are_connected(self):
        rule_ids = {item.rule_id for item in default_graph_inference_rules()}
        self.assertTrue({
            "graph.portfolio.repeated_loss_add.guard.v1",
            "graph.portfolio.activity.concentration.review.v1",
            "graph.portfolio.reentry.review.v1",
            "graph.portfolio.decision_action.divergence.v1",
        }.issubset(rule_ids))

        lifecycle = {
            "status": "ready",
            "portfolioId": "portfolio:main",
            "portfolioState": {"stateId": "state:1", "positions": [{"symbol": "035420"}]},
            "recentActivityEpisodes": [{"episodeId": "activity:1", "classification": "probable-buy"}],
            "decisionActionObservations": [{"observationId": "observed:1", "correspondence": "aligned"}],
            "portfolioRiskSnapshot": {"riskSnapshotId": "risk:1", "sampleCount": 60},
            "portfolioDecisionCycle": {"cycleId": "cycle:1"},
        }
        store = SimpleNamespace(latest_portfolio_lifecycle=lambda _portfolio_id: lifecycle)
        job = NotificationJob.create(
            "투자 판단",
            account_id="main",
            message_type="investmentInsight",
            context={"symbol": "035420", "messageType": "investmentInsight"},
        )

        NotificationAIDecisionContextEnricher(investment_domain_store=store)(job)

        self.assertEqual("state:1", job.context["portfolioLifecycle"]["portfolioState"]["stateId"])
        self.assertEqual("activity:1", job.context["portfolioLifecycle"]["recentActivityEpisodes"][0]["episodeId"])
        self.assertEqual("risk:1", job.context["portfolioLifecycle"]["portfolioRiskSnapshot"]["riskSnapshotId"])


if __name__ == "__main__":
    unittest.main()
