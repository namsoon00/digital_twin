import json
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
from digital_twin.domain.events import DomainEvent, ontology_reasoning_requested_event
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
from digital_twin.domain.portfolio_rebalancing import (
    RebalanceState,
    rebalance_transition,
)
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_reasoning_queue import durable_mailbox_entries
from digital_twin.domain.ontology_validator import validate_ontology
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.trade_execution import ActionEnvelope, ActionPlan, ExecutionEpisode, OrderIntent, TradeFill
from digital_twin.infrastructure.mysql_operational_connection import MYSQL_SCHEMA
from digital_twin.infrastructure.event_bus import EventBus
from digital_twin.infrastructure.mysql_investment_domain import MySQLInvestmentDomainStore


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
        self.rebalance_state = {}
        self.current_rebalance_state = {}
        self.rebalance_transitions = []
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
        rebalance_state=None,
        rebalance_transition=None,
        rebalance_event=None,
        rebalance_reasoning_event=None,
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
        rebalance_recorded = self.record_rebalance_state(
            rebalance_state,
            rebalance_transition,
            rebalance_event,
            rebalance_reasoning_event,
        )
        self.checkpoint = replace(checkpoint, version=actual + 1)
        return {
            "status": "committed",
            "actualCheckpointVersion": actual + 1,
            "insertedCount": inserted,
            "notificationQueued": bool(notification_job),
            "rebalanceTransitionRecorded": rebalance_recorded,
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

    def latest_rebalance_state(self, _portfolio_id):
        return dict(self.rebalance_state or {})

    def latest_rebalance_current_state(self, _portfolio_id):
        return dict(self.current_rebalance_state or {})

    def record_rebalance_state(self, current, transition, domain_event=None, reasoning_event=None):
        if not current:
            return False
        self.current_rebalance_state = current.to_dict()
        from digital_twin.domain.portfolio_rebalancing import rebalance_transition as evaluate_transition

        previous = RebalanceState.from_dict(self.rebalance_state) if self.rebalance_state else None
        verified = evaluate_transition(previous, current)
        accepted = bool(
            transition
            and verified
            and domain_event
            and reasoning_event
            and transition.revision == verified.revision
        )
        if accepted:
            self.rebalance_state = current.to_dict()
            self.rebalance_transitions.append(transition)
            if domain_event:
                self.reasoning_events.append(domain_event)
            if reasoning_event:
                self.reasoning_events.append(reasoning_event)
        return accepted

    def save_portfolio_analysis_bundle(
        self,
        risk_snapshot,
        exposure,
        rebalance_proposal,
        decision_cycle,
        domain_event=None,
        reasoning_event=None,
        rebalance_state=None,
        rebalance_transition=None,
        rebalance_event=None,
        rebalance_reasoning_event=None,
    ):
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
        rebalance_recorded = self.record_rebalance_state(
            rebalance_state,
            rebalance_transition,
            rebalance_event,
            rebalance_reasoning_event,
        )
        return {
            "status": "saved",
            "riskChanged": changed,
            "rebalanceTransitionRecorded": rebalance_recorded,
        }

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
    cash_components=None,
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
    metadata = {
        "accountSnapshotCompleteness": {
            "holdings": "complete" if complete else "incomplete",
            "cash": "complete" if complete else "incomplete",
            "source": "test-account-provider",
        },
    }
    if cash_components is not None:
        metadata["cashBalanceComponents"] = {
            currency: {"amount": amount, "currency": currency, "source": "test-provider"}
            for currency, amount in cash_components.items()
        }
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
        metadata=metadata,
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
    def test_weekly_rebalance_review_window_is_recorded_once_with_portfolio_reasoning_contract(self):
        class ReviewRepository:
            def __init__(self):
                self.windows = set()
                self.recorded = []

            def record_rebalance_review_window(
                self,
                portfolio_id,
                review_window,
                observed_at,
                domain_event,
                reasoning_event,
            ):
                key = (portfolio_id, review_window)
                if key in self.windows:
                    return False
                self.windows.add(key)
                self.recorded.append((observed_at, domain_event, reasoning_event))
                return True

        repository = ReviewRepository()
        domain_service = InvestmentDomainService(repository, EventBus())
        service = PortfolioAccountingService(
            repository,
            investment_domain_service=domain_service,
            settings={"portfolioRebalanceReviewIntervalDays": 7},
        )
        snapshot = live_snapshot()
        rebalance_state = {"revision": "rebalance-state:one", "breachKeys": []}

        first = service.schedule_rebalance_review(snapshot, "portfolio:main", rebalance_state)
        second = service.schedule_rebalance_review(snapshot, "portfolio:main", rebalance_state)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(1, len(repository.recorded))
        _observed_at, source_event, reasoning_event = repository.recorded[0]
        self.assertEqual("portfolio.rebalance_review_due", source_event.name)
        self.assertEqual("portfolio-rebalance-review", reasoning_event.payload["trigger"])
        self.assertEqual("PORTFOLIO", reasoning_event.payload["subjectKind"])
        self.assertEqual([], reasoning_event.payload.get("symbols") or [])
        self.assertEqual(
            sorted(position.symbol for position in snapshot.positions if not position.is_cash()),
            reasoning_event.payload["affectedSymbols"],
        )
        self.assertEqual("ready", reasoning_event.payload["factChangeContract"]["status"])
        self.assertEqual(
            ["exposure", "portfolio"],
            reasoning_event.payload["factChangeContract"]["scopeFamilies"],
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS portfolio_rebalance_review_windows",
            "\n".join(MYSQL_SCHEMA),
        )
        mailbox_entries = durable_mailbox_entries(reasoning_event)
        self.assertEqual(1, len(mailbox_entries))
        self.assertEqual("PORTFOLIO", mailbox_entries[0]["workClass"])
        self.assertEqual("", mailbox_entries[0]["symbol"])

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

    def test_unchanged_snapshot_defers_heavy_portfolio_analysis_inside_five_minutes(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(
            repository,
            market_time_series_store=MemoryPortfolioTimeSeriesStore(),
        )
        service.observe_snapshot(live_snapshot())
        risk_count = len(repository.risk_snapshots)

        result = service.observe_snapshot(live_snapshot(generated_at="2026-08-12T06:02:00Z"))

        self.assertEqual("unchanged", result["status"])
        self.assertEqual("deferred-unchanged-snapshot", result["analysisStatus"])
        self.assertEqual(300, result["analysisIntervalSeconds"])
        self.assertEqual(risk_count, len(repository.risk_snapshots))

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

    def test_exposure_breach_creates_review_only_rebalance_proposal(self):
        repository = MemoryInvestmentRepository()

        result = PortfolioAccountingService(repository).observe_snapshot(live_snapshot())

        self.assertGreater(result["exposureSnapshot"]["metrics"][0]["policyDeltaPct"], 0)
        self.assertEqual("review-required", result["rebalanceProposal"]["status"])
        self.assertTrue(result["rebalanceProposal"]["legs"])

    def test_rebalance_transition_opens_once_and_ignores_small_repeated_changes(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(repository)
        snapshot = live_snapshot()
        mandate = repository.mandate
        exposure = service.exposure_snapshot(snapshot, "portfolio:main", mandate)
        risk = service.risk_snapshot(snapshot, "portfolio:main", mandate, exposure)
        proposal = service.rebalance_proposal(snapshot, mandate, exposure, risk)
        current = RebalanceState.from_analysis(
            "portfolio:main", mandate.policy_version, exposure, risk, proposal,
        )

        opened = rebalance_transition(None, current)
        repeated = rebalance_transition(current, replace(
            current,
            exposure_deltas_pct={
                key: value + 0.25 for key, value in current.exposure_deltas_pct.items()
            },
        ))

        self.assertEqual("OPENED", opened.transition_type)
        self.assertIsNone(repeated)

    def test_rebalance_transition_emits_material_update_and_resolution(self):
        repository = MemoryInvestmentRepository()
        service = PortfolioAccountingService(repository)
        snapshot = live_snapshot()
        mandate = repository.mandate
        exposure = service.exposure_snapshot(snapshot, "portfolio:main", mandate)
        risk = service.risk_snapshot(snapshot, "portfolio:main", mandate, exposure)
        proposal = service.rebalance_proposal(snapshot, mandate, exposure, risk)
        current = RebalanceState.from_analysis(
            "portfolio:main", mandate.policy_version, exposure, risk, proposal,
        )
        material = replace(
            current,
            exposure_deltas_pct={
                key: value + 1.1 for key, value in current.exposure_deltas_pct.items()
            },
        )
        resolved = replace(
            current,
            status="WITHIN_POLICY",
            breach_keys=[],
            exposure_deltas_pct={},
            adjustment_directions={},
            maximum_notional_by_symbol={},
        )

        updated = rebalance_transition(current, material)
        cleared = rebalance_transition(current, resolved)

        self.assertEqual("UPDATED", updated.transition_type)
        self.assertIn("exposure-delta-material-change", updated.reason_codes)
        self.assertEqual("RESOLVED", cleared.transition_type)

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

if __name__ == "__main__":
    unittest.main()
