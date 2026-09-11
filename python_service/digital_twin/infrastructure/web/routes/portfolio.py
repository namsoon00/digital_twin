"""Portfolio HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.web.adapters.portfolio import execute_action_plan_payload
from digital_twin.infrastructure.web.adapters.portfolio import portfolio_lifecycle_payload
from digital_twin.infrastructure.web.adapters.portfolio import record_action_plan_fills_payload
from digital_twin.infrastructure.web.adapters.portfolio import review_action_plan_payload
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from typing import Callable
import re
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class PortfolioRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    execute_action_plan_payload: Callable[..., object] = execute_action_plan_payload
    portfolio_lifecycle_payload: Callable[..., object] = portfolio_lifecycle_payload
    record_action_plan_fills_payload: Callable[..., object] = record_action_plan_fills_payload
    review_action_plan_payload: Callable[..., object] = review_action_plan_payload

    def route_portfolio_lifecycle(self, request, path: str, query: Query):
        if path == "/api/portfolio-lifecycle" and request.command == "GET":
            return request.send_payload(200, self.portfolio_lifecycle_payload(query), cache_control="no-store")

        action_plan_match = re.match(r"^/api/action-plans/([^/]+)/(approve|reject|execute)$", path)
        if action_plan_match and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 실행계획을 검토하거나 제출할 수 없습니다."):
                return
            plan_id = urllib.parse.unquote(action_plan_match.group(1))
            action = action_plan_match.group(2)
            if action == "execute":
                payload = self.execute_action_plan_payload(plan_id)
            else:
                payload = self.review_action_plan_payload(
                    plan_id,
                    "approved" if action == "approve" else "rejected",
                    request.read_json_body(),
                )
            return request.send_payload(200 if payload.get("status") != "error" else 400, payload)

        action_plan_fills_match = re.match(r"^/api/action-plans/([^/]+)/fills$", path)
        if action_plan_fills_match and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 실제 체결을 기록할 수 없습니다."):
                return
            payload = self.record_action_plan_fills_payload(
                urllib.parse.unquote(action_plan_fills_match.group(1)),
                request.read_json_body(),
            )
            return request.send_payload(200 if payload.get("status") != "error" else 400, payload)
        return NOT_HANDLED
