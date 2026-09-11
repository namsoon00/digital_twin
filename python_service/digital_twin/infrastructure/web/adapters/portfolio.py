"""Web portfolio boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.service_factory import build_trade_execution_service
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import operational_read_settings
from typing import Dict
from typing import List


def portfolio_lifecycle_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    account_id = first_query(query, "accountId") or "default"
    portfolio_id = first_query(query, "portfolioId") or "portfolio:" + account_id
    return stores.investment_domain_store(operational_read_settings()).latest_portfolio_lifecycle(portfolio_id)


def review_action_plan_payload(plan_id: str, decision: str, body: Dict[str, object]) -> Dict[str, object]:
    try:
        return build_trade_execution_service().review_plan(
            plan_id,
            decision,
            str(body.get("reviewer") or "local-user"),
            str(body.get("reason") or ""),
        )
    except ValueError as error:
        return {"status": "error", "error": str(error)}


def execute_action_plan_payload(plan_id: str) -> Dict[str, object]:
    try:
        return build_trade_execution_service().submit_plan(plan_id)
    except ValueError as error:
        return {"status": "error", "error": str(error)}


def record_action_plan_fills_payload(plan_id: str, body: Dict[str, object]) -> Dict[str, object]:
    try:
        return build_trade_execution_service().record_fills(
            plan_id,
            body.get("fills") if isinstance(body.get("fills"), list) else [],
            str(body.get("completedAt") or ""),
        )
    except (TypeError, ValueError) as error:
        return {"status": "error", "error": str(error)}
