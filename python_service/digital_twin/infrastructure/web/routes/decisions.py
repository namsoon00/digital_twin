"""Decisions HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.service_factory import build_investment_brain_service
from digital_twin.infrastructure.web.adapters.brain import investment_brain_episodes_api_payload
from digital_twin.infrastructure.web.adapters.brain import investment_brain_question_payload
from digital_twin.infrastructure.web.adapters.brain import investment_brain_research_runs_api_payload
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from typing import Callable
import re
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class DecisionsRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    build_investment_brain_service: Callable[..., object] = build_investment_brain_service
    investment_brain_episodes_api_payload: Callable[..., object] = investment_brain_episodes_api_payload
    investment_brain_question_payload: Callable[..., object] = investment_brain_question_payload
    investment_brain_research_runs_api_payload: Callable[..., object] = investment_brain_research_runs_api_payload

    def route_investment_brain_questions(self, request, path: str, query: Query):
        if path == "/api/investment-brain/questions" and request.command == "POST":
            if request.share_access().shared:
                return request.send_payload(403, {"error": "투자 브레인 질의는 이 컴퓨터에서 직접 접속할 때만 사용할 수 있습니다."})
            return request.send_payload(200, self.investment_brain_question_payload(request.read_json_body()))
        return NOT_HANDLED

    def route_investment_brain_episodes(self, request, path: str, query: Query):
        if path == "/api/investment-brain/episodes" and request.command == "GET":
            return request.send_payload(200, self.investment_brain_episodes_api_payload(query))

        episode_detail_match = re.match(r"^/api/investment-brain/episodes/([^/]+)$", path)
        if episode_detail_match and request.command == "GET":
            payload = self.build_investment_brain_service().episode_detail(
                urllib.parse.unquote(episode_detail_match.group(1)),
            )
            return request.send_payload(200 if payload.get("status") == "ok" else 404, payload)
        return NOT_HANDLED

    def route_investment_brain_research_runs(self, request, path: str, query: Query):
        if path == "/api/investment-brain/research-runs" and request.command == "GET":
            return request.send_payload(200, self.investment_brain_research_runs_api_payload(query))

        research_run_detail_match = re.match(r"^/api/investment-brain/research-runs/([^/]+)$", path)
        if research_run_detail_match and request.command == "GET":
            payload = self.build_investment_brain_service().research_run_detail(
                urllib.parse.unquote(research_run_detail_match.group(1)),
            )
            return request.send_payload(200 if payload.get("status") == "ok" else 404, payload)
        return NOT_HANDLED
