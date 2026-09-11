"""Calendar HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.web.adapters.calendar import approve_investment_calendar_candidate_payload
from digital_twin.infrastructure.web.adapters.calendar import delete_investment_calendar_event_payload
from digital_twin.infrastructure.web.adapters.calendar import discover_investment_calendar_payload
from digital_twin.infrastructure.web.adapters.calendar import investment_calendar_candidates_payload
from digital_twin.infrastructure.web.adapters.calendar import investment_calendar_payload
from digital_twin.infrastructure.web.adapters.calendar import investment_calendar_reminders_once_payload
from digital_twin.infrastructure.web.adapters.calendar import investment_calendar_sync_official_payload
from digital_twin.infrastructure.web.adapters.calendar import reject_investment_calendar_candidate_payload
from digital_twin.infrastructure.web.adapters.calendar import research_investment_calendar_candidates_payload
from digital_twin.infrastructure.web.adapters.calendar import save_investment_calendar_event_payload
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from typing import Callable
import re
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class CalendarRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    approve_investment_calendar_candidate_payload: Callable[..., object] = approve_investment_calendar_candidate_payload
    delete_investment_calendar_event_payload: Callable[..., object] = delete_investment_calendar_event_payload
    discover_investment_calendar_payload: Callable[..., object] = discover_investment_calendar_payload
    investment_calendar_candidates_payload: Callable[..., object] = investment_calendar_candidates_payload
    investment_calendar_payload: Callable[..., object] = investment_calendar_payload
    investment_calendar_reminders_once_payload: Callable[..., object] = investment_calendar_reminders_once_payload
    investment_calendar_sync_official_payload: Callable[..., object] = investment_calendar_sync_official_payload
    reject_investment_calendar_candidate_payload: Callable[..., object] = reject_investment_calendar_candidate_payload
    research_investment_calendar_candidates_payload: Callable[..., object] = research_investment_calendar_candidates_payload
    save_investment_calendar_event_payload: Callable[..., object] = save_investment_calendar_event_payload

    def route_investment_calendar_events(self, request, path: str, query: Query):
        if path == "/api/investment-calendar/events":
            if request.command == "GET":
                return request.send_payload(200, self.investment_calendar_payload(query))
            if request.command in {"POST", "PUT"}:
                if not request.ensure_writable("공유 모드에서는 투자 캘린더 이벤트를 변경할 수 없습니다."):
                    return
                return request.send_payload(200, self.save_investment_calendar_event_payload(request.read_json_body()))

        if path == "/api/investment-calendar/candidates" and request.command == "GET":
            return request.send_payload(200, self.investment_calendar_candidates_payload(query))

        if path == "/api/investment-calendar/candidates/research" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 AI 리서치 캘린더 후보를 생성할 수 없습니다."):
                return
            return request.send_payload(200, self.research_investment_calendar_candidates_payload(request.read_json_body()))

        if path == "/api/investment-calendar/discovery" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 투자 일정 탐색을 실행할 수 없습니다."):
                return
            return request.send_payload(200, self.discover_investment_calendar_payload(request.read_json_body()))

        calendar_candidate_match = re.match(r"^/api/investment-calendar/candidates/([^/]+)/(approve|reject)$", path)
        if calendar_candidate_match and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 투자 캘린더 후보를 검토할 수 없습니다."):
                return
            candidate_id = urllib.parse.unquote(calendar_candidate_match.group(1))
            action = calendar_candidate_match.group(2)
            if action == "approve":
                return request.send_payload(200, self.approve_investment_calendar_candidate_payload(candidate_id, request.read_json_body()))
            return request.send_payload(200, self.reject_investment_calendar_candidate_payload(candidate_id, request.read_json_body()))

        if path == "/api/investment-calendar/reminders/run" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 투자 캘린더 알림을 큐잉할 수 없습니다."):
                return
            return request.send_payload(200, self.investment_calendar_reminders_once_payload())

        if path == "/api/investment-calendar/sync-official" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 공식 투자 일정을 동기화할 수 없습니다."):
                return
            return request.send_payload(200, self.investment_calendar_sync_official_payload())

        calendar_event_match = re.match(r"^/api/investment-calendar/events/([^/]+)$", path)
        if calendar_event_match:
            event_id = urllib.parse.unquote(calendar_event_match.group(1))
            if request.command == "GET":
                payload = self.investment_calendar_payload({"limit": ["500"]})
                payload["event"] = next((item for item in payload.get("events") or [] if item.get("eventId") == event_id), None)
                return request.send_payload(200 if payload.get("event") else 404, payload if payload.get("event") else {"error": "투자 캘린더 이벤트를 찾지 못했습니다."})
            if request.command == "DELETE":
                if not request.ensure_writable("공유 모드에서는 투자 캘린더 이벤트를 변경할 수 없습니다."):
                    return
                return request.send_payload(200, self.delete_investment_calendar_event_payload(event_id))
        return NOT_HANDLED
