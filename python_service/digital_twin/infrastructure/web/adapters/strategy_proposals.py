"""Web strategy proposals boundary."""

from digital_twin.infrastructure.event_bus import EventBus
from digital_twin.infrastructure.service_factory import build_investment_strategy_proposal_service
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.common import request_bool
from digital_twin.infrastructure.web.common import safe_int
from typing import Dict
from typing import List


def investment_strategy_proposal_service():
    return build_investment_strategy_proposal_service(runtime_settings())


def investment_strategy_proposal_read_service():
    # Read projections never publish lifecycle events. Avoid constructing the
    # durable event bus, whose schema checks belong to write/service startup.
    return build_investment_strategy_proposal_service(
        operational_read_settings(),
        event_publisher=EventBus(),
    )


def list_investment_strategy_proposals_payload(query: Dict[str, List[str]] = None) -> Dict[str, object]:
    query = query or {}
    return investment_strategy_proposal_read_service().list(
        limit=safe_int(first_query(query, "limit"), 100, 1, 500),
        detail="summary" if request_bool(first_query(query, "summary"), False) else "full",
    )


def investment_strategy_proposals_status_payload() -> Dict[str, object]:
    return investment_strategy_proposal_read_service().status()


def investment_strategy_proposal_payload(proposal_id: str) -> Dict[str, object]:
    return investment_strategy_proposal_read_service().get(proposal_id)


def validate_investment_strategy_proposal_payload(proposal_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return investment_strategy_proposal_service().validate_materialization(proposal_id, payload if isinstance(payload, dict) else {})


def approve_investment_strategy_proposal_payload(proposal_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return investment_strategy_proposal_service().approve(proposal_id, payload if isinstance(payload, dict) else {})


def investment_strategy_proposal_performance_payload(proposal_id: str) -> Dict[str, object]:
    return investment_strategy_proposal_read_service().performance(proposal_id)


def record_investment_strategy_proposal_performance_payload(proposal_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return investment_strategy_proposal_service().record_performance_sample(proposal_id, payload if isinstance(payload, dict) else {})
