"""Repository capabilities owned by portfolio."""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Protocol
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.portfolio import AccountSnapshot, AlertEvent
from digital_twin.domain.investment_mandate import InvestmentMandate
from digital_twin.domain.investment_outcomes import DecisionReview, PerformanceAttribution
from digital_twin.domain.portfolio_decision_cycle import PortfolioDecisionCycle
from digital_twin.domain.portfolio_analytics import PortfolioRiskSnapshot
from digital_twin.modules.portfolio.contracts import PortfolioLedgerEntry, PortfolioReconciliation
from digital_twin.domain.portfolio_activity_episode import PortfolioSnapshotCheckpoint
from digital_twin.domain.portfolio_rebalancing import RebalanceProposal
from digital_twin.domain.risk_exposure import ExposureSnapshot
from digital_twin.domain.trade_execution import ActionPlan, ActionPlanReview, ExecutionEpisode


class InvestmentDomainRepository(Protocol):
    def save_mandate(self, mandate: InvestmentMandate) -> InvestmentMandate:
        ...

    def active_mandate(self, portfolio_id: str) -> Dict[str, object]:
        ...

    def mandate_history(self, portfolio_id: str, limit: int = 100) -> List[Dict[str, object]]:
        ...

    def append_ledger_entries(self, entries: Iterable[PortfolioLedgerEntry]) -> int:
        ...

    def ledger_entries(self, portfolio_id: str, limit: int = 10000) -> List[PortfolioLedgerEntry]:
        ...

    def snapshot_checkpoint(self, portfolio_id: str) -> Optional[PortfolioSnapshotCheckpoint]:
        ...

    def advance_snapshot_checkpoint(
        self,
        expected_checkpoint_version: int,
        checkpoint: PortfolioSnapshotCheckpoint,
    ) -> Dict[str, object]:
        ...

    def record_snapshot_quarantine(
        self,
        checkpoint: PortfolioSnapshotCheckpoint,
        reason: str,
        previous_checkpoint: Optional[PortfolioSnapshotCheckpoint] = None,
    ) -> Dict[str, object]:
        ...

    def commit_snapshot_observation(self, expected_checkpoint_version: int, checkpoint, ledger_entries, activity_episode, state_snapshot, reconciliation, exposure, rebalance_proposal, decision_cycle, decision_action_observations=None, domain_event=None, notification_job=None, reasoning_event=None, risk_snapshot=None, rebalance_state=None, rebalance_transition=None, rebalance_event=None, rebalance_reasoning_event=None) -> Dict[str, object]:
        ...

    def save_reconciliation(self, reconciliation: PortfolioReconciliation) -> PortfolioReconciliation:
        ...

    def save_exposure_snapshot(self, snapshot: ExposureSnapshot) -> ExposureSnapshot:
        ...

    def save_portfolio_decision_cycle(self, cycle: PortfolioDecisionCycle) -> PortfolioDecisionCycle:
        ...

    def save_risk_snapshot(self, snapshot: PortfolioRiskSnapshot) -> PortfolioRiskSnapshot:
        ...

    def latest_portfolio_risk_event(self, portfolio_id: str) -> Dict[str, object]:
        ...

    def latest_rebalance_state(self, portfolio_id: str) -> Dict[str, object]:
        ...

    def latest_rebalance_current_state(self, portfolio_id: str) -> Dict[str, object]:
        ...

    def save_portfolio_analysis_bundle(self, risk_snapshot, exposure, rebalance_proposal, decision_cycle, domain_event=None, reasoning_event=None, rebalance_state=None, rebalance_transition=None, rebalance_event=None, rebalance_reasoning_event=None) -> Dict[str, object]:
        ...

    def ontology_portfolio_lifecycle_context(self, portfolio_id: str) -> Dict[str, object]:
        ...

    def save_rebalance_proposal(self, proposal: RebalanceProposal) -> RebalanceProposal:
        ...

    def save_action_plan(self, plan: ActionPlan) -> ActionPlan:
        ...

    def latest_active_action_plan(self, portfolio_id: str, symbol: str, action: str) -> Optional[ActionPlan]:
        ...

    def action_plan(self, plan_id: str) -> Optional[ActionPlan]:
        ...

    def save_action_plan_review(self, review: ActionPlanReview) -> ActionPlanReview:
        ...

    def save_execution_episode(self, episode: ExecutionEpisode) -> ExecutionEpisode:
        ...

    def execution_episode_for_plan(self, plan_id: str) -> Optional[ExecutionEpisode]:
        ...

    def save_execution_with_ledger(self, episode: ExecutionEpisode, plan: ActionPlan, domain_event=None) -> Dict[str, object]:
        ...

    def execution_feedback_for_decisions(self, decision_episode_ids: Iterable[str]) -> Dict[str, Dict[str, object]]:
        ...

    def save_outcome_reviews(
        self,
        attributions: Iterable[PerformanceAttribution],
        reviews: Iterable[DecisionReview],
    ) -> Dict[str, int]:
        ...

    def save_decision_review(self, review: DecisionReview) -> DecisionReview:
        ...

    def save_performance_attribution(self, attribution: PerformanceAttribution) -> PerformanceAttribution:
        ...


class SnapshotProvider(Protocol):
    def build_snapshot(self, account: AccountConfig) -> AccountSnapshot:
        ...


class MonitorStateRepository(Protocol):
    @property
    def previous(self) -> Dict[str, object]:
        ...

    @property
    def sent(self) -> Dict[str, object]:
        ...

    def save_snapshot(self, snapshot: AccountSnapshot) -> None:
        ...

    def mark_sent(self, events: Iterable[AlertEvent]) -> None:
        ...

    def write(self) -> None:
        ...


class MonitorSnapshotReader(Protocol):
    @property
    def previous(self) -> Dict[str, object]:
        ...


class SnapshotMonitor(Protocol):
    def events_for_snapshot(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        ...

    def apply_cadence(self, events: List[AlertEvent], store: MonitorStateRepository, force: bool = False) -> List[AlertEvent]:
        ...


@dataclass
class MonitoringCycleRecordResult:
    delivered: bool
    queued: int = 0
    reason: str = ""
    # The transactional delivery guard may remove an obsolete mailbox revision
    # immediately before outbox creation. Keep that outcome visible to the
    # reasoning worker so it never reports a suppressed alert as sent.
    delivered_events: List[AlertEvent] = field(default_factory=list)
    details: Dict[str, object] = field(default_factory=dict)


class MonitoringCycleRecorder(Protocol):
    def record_cycle(
        self,
        account_ids: List[str],
        snapshots: List[AccountSnapshot],
        alert_events: List[AlertEvent],
        dry_run: bool = False,
        delivery_guard=None,
        source_snapshot_replay: bool = False,
    ) -> MonitoringCycleRecordResult:
        ...


@dataclass
class MonitorAccountJob:
    account_id: str
    status: str = "pending"
    priority: int = 100
    next_run_at: str = ""
    locked_by: str = ""
    locked_until: str = ""
    attempts: int = 0
    last_started_at: str = ""
    last_finished_at: str = ""
    last_error: str = ""
    updated_at: str = ""


class MonitorAccountJobRepository(Protocol):
    def sync_accounts(self, accounts: Iterable[AccountConfig], default_interval_seconds: int) -> None:
        ...

    def claim_due(
        self,
        limit: int,
        worker_id: str,
        lock_seconds: int,
        default_interval_seconds: int,
    ) -> List[MonitorAccountJob]:
        ...

    def mark_done(self, account_id: str, next_run_at: str) -> None:
        ...

    def mark_failed(self, account_id: str, error: str, next_run_at: str) -> None:
        ...

    def request_refresh(self, account_id: str, priority: int = 10) -> Dict[str, object]:
        ...

    def summary(self) -> Dict[str, object]:
        ...
