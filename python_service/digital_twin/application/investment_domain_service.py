"""Use cases spanning investment domain stores through explicit events."""

from typing import Iterable

from ..domain.events import (
    INVESTMENT_ACTION_PLAN_PROPOSED,
    INVESTMENT_DECISION_REVIEWED,
    INVESTMENT_MANDATE_CHANGED,
    INVESTMENT_PERFORMANCE_ATTRIBUTED,
    PORTFOLIO_LEDGER_RECORDED,
    PORTFOLIO_REBALANCE_PROPOSED,
    TRADE_EXECUTION_RECORDED,
    investment_lifecycle_event,
)
from ..domain.investment_mandate import InvestmentMandate
from ..domain.investment_outcomes import DecisionReview, PerformanceAttribution
from ..domain.portfolio_ledger import PortfolioLedgerEntry
from ..domain.portfolio_ledger import INFERRED_SNAPSHOT_ENTRY_TYPES
from ..domain.snapshot_portfolio_activity import activity_payload
from ..domain.portfolio_rebalancing import RebalanceProposal
from ..domain.repositories import InvestmentDomainRepository
from ..domain.trade_execution import ActionPlan, ExecutionEpisode


class InvestmentDomainService:
    def __init__(self, repository: InvestmentDomainRepository, event_publisher=None):
        self.repository = repository
        self.event_publisher = event_publisher

    def publish(self, event) -> None:
        if self.event_publisher:
            self.event_publisher.publish(event)

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
        if inserted and rows:
            inferred_activities = [
                activity_payload(item)
                for item in rows
                if item.entry_type in INFERRED_SNAPSHOT_ENTRY_TYPES
            ]
            self.publish(investment_lifecycle_event(
                PORTFOLIO_LEDGER_RECORDED,
                rows[0].portfolio_id,
                {
                    "portfolioId": rows[0].portfolio_id,
                    "accountId": rows[0].account_id,
                    "entryIds": [item.entry_id for item in rows],
                    "entryTypes": [item.entry_type for item in rows],
                    "insertedCount": inserted,
                    "inferredActivities": inferred_activities,
                    "materialSnapshotChange": bool(inferred_activities),
                },
                "ledger:" + rows[0].portfolio_id,
            ))
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
        self.publish(investment_lifecycle_event(
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
        ))
        return saved

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
