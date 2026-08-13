"""Use cases spanning investment domain stores through explicit events."""

from typing import Iterable

from ..domain.events import (
    INVESTMENT_ACTION_PLAN_PROPOSED,
    INVESTMENT_DECISION_REVIEWED,
    INVESTMENT_MANDATE_CHANGED,
    INVESTMENT_PERFORMANCE_ATTRIBUTED,
    PORTFOLIO_LEDGER_RECORDED,
    PORTFOLIO_REBALANCE_PROPOSED,
    PORTFOLIO_REBALANCE_RESOLVED,
    PORTFOLIO_RISK_OBSERVED,
    TRADE_EXECUTION_RECORDED,
    investment_lifecycle_event,
)
from ..domain.investment_mandate import InvestmentMandate
from ..domain.investment_outcomes import DecisionReview, PerformanceAttribution
from ..domain.portfolio_ledger import PortfolioLedgerEntry
from ..domain.portfolio_ledger import INFERRED_SNAPSHOT_ENTRY_TYPES
from ..domain.snapshot_portfolio_activity import activity_payload
from ..domain.portfolio_rebalancing import RebalanceProposal, RebalanceTransition
from ..domain.repositories import InvestmentDomainRepository
from ..domain.trade_execution import ActionPlan, ExecutionEpisode


class InvestmentDomainService:
    def __init__(self, repository: InvestmentDomainRepository, event_publisher=None):
        self.repository = repository
        self.event_publisher = event_publisher

    def publish(self, event) -> None:
        if self.event_publisher:
            self.event_publisher.publish(event)

    def dispatch_recorded(self, event) -> None:
        """Dispatch an event already committed with the aggregate transaction."""
        if not self.event_publisher:
            return
        dispatcher = getattr(self.event_publisher, "dispatch_recorded", None)
        if callable(dispatcher):
            dispatcher(event)
        else:
            self.event_publisher.dispatch(event)

    def ledger_recorded_event(self, rows, inserted: int, activity_episode=None):
        rows = list(rows or [])
        if not inserted or not rows:
            return None
        inferred_activities = [
            activity_payload(item)
            for item in rows
            if item.entry_type in INFERRED_SNAPSHOT_ENTRY_TYPES
        ]
        payload = {
            "portfolioId": rows[0].portfolio_id,
            "accountId": rows[0].account_id,
            "entryIds": [item.entry_id for item in rows],
            "entryTypes": [item.entry_type for item in rows],
            "insertedCount": inserted,
            "inferredActivities": inferred_activities,
            "materialSnapshotChange": bool(inferred_activities),
        }
        if activity_episode:
            payload["activityEpisode"] = activity_episode.to_dict()
            payload["sourceObservedAt"] = activity_episode.observed_at
        return investment_lifecycle_event(
            PORTFOLIO_LEDGER_RECORDED,
            rows[0].portfolio_id,
            payload,
            "ledger:" + rows[0].portfolio_id,
        )

    def save_mandate(self, mandate: InvestmentMandate) -> InvestmentMandate:
        saved = self.repository.save_mandate(mandate)
        self.publish(investment_lifecycle_event(
            INVESTMENT_MANDATE_CHANGED,
            mandate.portfolio_id,
            {
                "mandateId": mandate.mandate_id,
                "portfolioId": mandate.portfolio_id,
                "accountId": mandate.account_id,
                "policyVersion": mandate.policy_version,
            },
            "mandate:" + mandate.portfolio_id,
        ))
        return saved

    def append_ledger_entries(self, entries: Iterable[PortfolioLedgerEntry]) -> int:
        rows = list(entries or [])
        inserted = self.repository.append_ledger_entries(rows)
        event = self.ledger_recorded_event(rows, inserted)
        if event:
            self.publish(event)
        return inserted

    def save_rebalance_proposal(self, proposal: RebalanceProposal) -> RebalanceProposal:
        if proposal.validate():
            raise ValueError("Invalid rebalance proposal: " + ",".join(proposal.validate()))
        saved = self.repository.save_rebalance_proposal(proposal)
        self.publish(investment_lifecycle_event(
            PORTFOLIO_REBALANCE_PROPOSED,
            proposal.portfolio_id,
            {
                "proposalId": proposal.proposal_id,
                "portfolioId": proposal.portfolio_id,
                "mandateVersion": proposal.mandate_version,
            },
            "rebalance:" + proposal.proposal_id,
        ))
        return saved

    def risk_observed_event(self, snapshot, symbols, materiality=None):
        return investment_lifecycle_event(
            PORTFOLIO_RISK_OBSERVED,
            snapshot.portfolio_id,
            {
                "portfolioId": snapshot.portfolio_id,
                "riskSnapshotId": snapshot.risk_snapshot_id,
                "sourceObservedAt": snapshot.observed_at,
                "symbols": sorted({str(item or "").upper() for item in symbols or [] if str(item or "").strip()}),
                "annualizedVolatilityPct": snapshot.annualized_volatility_pct,
                "maximumDrawdownPct": snapshot.maximum_drawdown_pct,
                "maximumPairwiseCorrelation": snapshot.maximum_pairwise_correlation,
                "volatilityPolicyDeltaPct": snapshot.volatility_policy_delta_pct,
                "drawdownPolicyDeltaPct": snapshot.drawdown_policy_delta_pct,
                "correlationPolicyDelta": snapshot.correlation_policy_delta,
                "dataState": snapshot.data_state,
                "missingData": list(snapshot.missing_data),
                "provenance": dict(snapshot.provenance or {}),
                "positionRiskSummary": [
                    {
                        "symbol": item.symbol,
                        "weightPct": item.weight_pct,
                        "beta": item.beta,
                    }
                    for item in snapshot.positions
                ],
                "materiality": dict(materiality or {}),
                "policyBreach": any([
                    snapshot.volatility_policy_delta_pct > 0,
                    snapshot.drawdown_policy_delta_pct > 0,
                    snapshot.correlation_policy_delta > 0,
                ]),
            },
            "portfolio-risk:" + snapshot.portfolio_id,
        )

    def rebalance_transition_event(self, transition: RebalanceTransition, symbols):
        current = transition.current_state
        event_name = (
            PORTFOLIO_REBALANCE_RESOLVED
            if transition.transition_type == "RESOLVED"
            else PORTFOLIO_REBALANCE_PROPOSED
        )
        clean_symbols = sorted({
            str(item or "").upper().strip()
            for item in symbols or []
            if str(item or "").strip()
        })
        return investment_lifecycle_event(
            event_name,
            current.portfolio_id,
            {
                "portfolioId": current.portfolio_id,
                "policyVersion": current.policy_version,
                "proposalId": current.proposal_id,
                "sourceObservedAt": current.observed_at,
                "symbols": clean_symbols,
                "transition": transition.to_dict(),
                "rebalanceStatus": current.status,
                "breachKeys": list(current.breach_keys),
                "adjustmentDirections": dict(current.adjustment_directions),
                "maximumNotionalBySymbol": dict(current.maximum_notional_by_symbol),
                "dataState": current.data_state,
                "materialSnapshotChange": True,
            },
            "portfolio-rebalance:" + current.portfolio_id,
        )

    def save_action_plan(self, plan: ActionPlan) -> ActionPlan:
        saved = self.repository.save_action_plan(plan)
        self.publish(investment_lifecycle_event(
            INVESTMENT_ACTION_PLAN_PROPOSED,
            plan.decision_episode_id,
            {
                "actionPlanId": plan.plan_id,
                "decisionEpisodeId": plan.decision_episode_id,
                "portfolioId": plan.portfolio_id,
                "policyVersion": plan.policy_version,
                "status": plan.status,
            },
            "decision:" + plan.decision_episode_id,
        ))
        return saved

    def save_execution(self, episode: ExecutionEpisode) -> ExecutionEpisode:
        saved = self.repository.save_execution_episode(episode)
        self.publish(self.execution_recorded_event(episode))
        return saved

    def execution_recorded_event(self, episode: ExecutionEpisode):
        return investment_lifecycle_event(
            TRADE_EXECUTION_RECORDED,
            episode.execution_episode_id,
            {
                "executionEpisodeId": episode.execution_episode_id,
                "actionPlanId": episode.action_plan_id,
                "portfolioId": episode.portfolio_id,
                "status": episode.status,
                "fillCount": len(episode.fills),
            },
            "action-plan:" + episode.action_plan_id,
        )

    def save_decision_review(self, review: DecisionReview) -> DecisionReview:
        saved = self.repository.save_decision_review(review)
        self.publish(investment_lifecycle_event(
            INVESTMENT_DECISION_REVIEWED,
            review.decision_episode_id,
            {
                "reviewId": review.review_id,
                "decisionEpisodeId": review.decision_episode_id,
                "selectedHypothesisStatus": review.selected_hypothesis_status,
                "policyCompliant": review.policy_compliant,
                "executionCompliant": review.execution_compliant,
            },
            "decision:" + review.decision_episode_id,
        ))
        return saved

    def save_performance_attribution(self, attribution: PerformanceAttribution) -> PerformanceAttribution:
        saved = self.repository.save_performance_attribution(attribution)
        self.publish(investment_lifecycle_event(
            INVESTMENT_PERFORMANCE_ATTRIBUTED,
            attribution.decision_episode_id,
            {
                "attributionId": attribution.attribution_id,
                "decisionEpisodeId": attribution.decision_episode_id,
                "actionPlanId": attribution.action_plan_id,
                "executionEpisodeId": attribution.execution_episode_id,
                "observedAt": attribution.observed_at,
            },
            "decision:" + attribution.decision_episode_id,
        ))
        return saved
