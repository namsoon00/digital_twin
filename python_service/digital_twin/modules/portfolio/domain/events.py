from digital_twin.modules.portfolio.domain.event_types import INVESTMENT_MANDATE_CHANGED, PORTFOLIO_LEDGER_RECORDED, PORTFOLIO_REBALANCE_PROPOSED, PORTFOLIO_REBALANCE_RESOLVED, PORTFOLIO_REBALANCE_REVIEW_DUE, PORTFOLIO_RISK_OBSERVED, INVESTMENT_ACTION_PLAN_PROPOSED
from digital_twin.modules.outcomes.contracts import INVESTMENT_DECISION_REVIEWED
from digital_twin.modules.outcomes.contracts import INVESTMENT_PERFORMANCE_ATTRIBUTED
from digital_twin.modules.outcomes.contracts import TRADE_EXECUTION_RECORDED
from digital_twin.shared_kernel.events import DomainEvent
from typing import Dict, Iterable, List, Mapping
















def investment_lifecycle_event(
    name: str,
    aggregate_id: str,
    payload: Dict[str, object],
    correlation_id: str = "",
) -> DomainEvent:
    allowed = {
        INVESTMENT_MANDATE_CHANGED,
        PORTFOLIO_LEDGER_RECORDED,
        PORTFOLIO_REBALANCE_PROPOSED,
        PORTFOLIO_REBALANCE_RESOLVED,
        PORTFOLIO_REBALANCE_REVIEW_DUE,
        PORTFOLIO_RISK_OBSERVED,
        INVESTMENT_ACTION_PLAN_PROPOSED,
        TRADE_EXECUTION_RECORDED,
        INVESTMENT_DECISION_REVIEWED,
        INVESTMENT_PERFORMANCE_ATTRIBUTED,
    }
    if name not in allowed:
        raise ValueError("Unsupported investment lifecycle event: " + str(name or ""))
    return DomainEvent(
        name=name,
        aggregate_id=str(aggregate_id or "")[:191],
        payload=dict(payload or {}),
        correlation_id=str(correlation_id or "")[:191],
    )
