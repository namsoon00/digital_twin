"""Outcomes HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.service_factory import build_historical_replay_job_service
from digital_twin.infrastructure.service_factory import build_investment_brain_service
from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from typing import Callable
import re
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class OutcomesRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    build_historical_replay_job_service: Callable[..., object] = build_historical_replay_job_service
    build_investment_brain_service: Callable[..., object] = build_investment_brain_service
    operational_read_settings: Callable[..., object] = operational_read_settings

    def route_investment_brain_performance(self, request, path: str, query: Query):
        if path == "/api/investment-brain/performance" and request.command == "GET":
            try:
                limit = int(first_query(query, "limit") or 500)
            except ValueError:
                limit = 500
            return request.send_payload(200, self.build_investment_brain_service().performance(
                account_id=first_query(query, "accountId"),
                symbol=first_query(query, "symbol"),
                limit=limit,
            ))
        return NOT_HANDLED

    def route_investment_brain_replay_jobs(self, request, path: str, query: Query):
        replay_job_match = re.match(r"^/api/investment-brain/replay-jobs/([^/]+)$", path)
        if replay_job_match and request.command == "GET":
            replay_service = self.build_historical_replay_job_service(
                self.operational_read_settings(), execution_enabled=False,
            )
            job = replay_service.get(urllib.parse.unquote(replay_job_match.group(1)))
            return request.send_payload(200 if job else 404, {
                "status": str(job.get("status") or "not-found") if job else "not-found",
                "job": job,
            })

        if path == "/api/investment-brain/hypothesis-replay" and request.command in {"GET", "POST"}:
            replay_service = self.build_historical_replay_job_service(
                self.operational_read_settings(), execution_enabled=False,
            )
            if request.command == "GET":
                return request.send_payload(200, replay_service.list(replay_kind="hypothesis", limit=20))
            if not request.ensure_writable("공유 보기 모드에서는 과거 가설 재현 작업을 시작할 수 없습니다."):
                return
            body = request.read_json_body()
            try:
                limit = int(body.get("limit") or 500)
            except (TypeError, ValueError):
                limit = 500
            job = replay_service.enqueue("hypothesis", {
                "accountId": configured(body.get("accountId")),
                "symbol": configured(body.get("symbol")),
                "limit": max(1, min(2000, limit)),
            })
            return request.send_payload(202, {"status": "queued", "job": job})

        if path == "/api/investment-brain/decision-replay" and request.command in {"GET", "POST"}:
            replay_service = self.build_historical_replay_job_service(
                self.operational_read_settings(), execution_enabled=False,
            )
            if request.command == "GET":
                return request.send_payload(200, replay_service.list(replay_kind="decision", limit=20))
            if not request.ensure_writable("공유 보기 모드에서는 과거 판단 재현 작업을 시작할 수 없습니다."):
                return
            body = request.read_json_body()
            try:
                limit = int(body.get("limit") or 500)
            except (TypeError, ValueError):
                limit = 500
            try:
                case_limit = int(body.get("caseLimit") or 30)
            except (TypeError, ValueError):
                case_limit = 30
            include_cases_value = body.get("includeCases")
            include_cases = str(include_cases_value or "").strip().lower() in {"1", "true", "yes", "on"}
            job = replay_service.enqueue("decision", {
                "accountId": str(body.get("accountId") or ""),
                "symbol": str(body.get("symbol") or ""),
                "limit": max(1, min(2000, limit)),
                "includeCases": include_cases,
                "caseLimit": max(1, min(100, case_limit)),
                "replayMode": str(body.get("replayMode") or "strict-replay"),
            })
            return request.send_payload(202, {"status": "queued", "job": job})

        if path == "/api/investment-brain/hypothesis-quality-review" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 가설 품질 검토 제안을 저장할 수 없습니다."):
                return
            body = request.read_json_body()
            return request.send_payload(200, self.build_investment_brain_service().review_hypothesis_quality(
                account_id=configured(body.get("accountId")),
                symbol=configured(body.get("symbol")),
                market_id=configured(body.get("marketId")),
                scope=configured(body.get("scope")),
                reviewed_by=configured(body.get("reviewedBy")) or "web-main",
            ))
        return NOT_HANDLED
