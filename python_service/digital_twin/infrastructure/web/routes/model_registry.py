"""Model Registry HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.service_factory import build_investment_brain_service
from digital_twin.infrastructure.web.adapters.brain import hypothesis_policy_versions_api_payload
from digital_twin.infrastructure.web.adapters.brain import hypothesis_templates_api_payload
from digital_twin.infrastructure.web.adapters.brain import investment_brain_hypothesis_lifecycles_api_payload
from digital_twin.infrastructure.web.adapters.brain import investment_brain_hypothesis_workspace_api_payload
from digital_twin.infrastructure.web.adapters.investment_model import investment_model_api_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import approve_hypothesis_development_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import hypothesis_development_case_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import hypothesis_development_cases_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import process_hypothesis_development_payload
from digital_twin.infrastructure.web.adapters.strategy_proposals import approve_investment_strategy_proposal_payload
from digital_twin.infrastructure.web.adapters.strategy_proposals import investment_strategy_proposal_payload
from digital_twin.infrastructure.web.adapters.strategy_proposals import investment_strategy_proposal_performance_payload
from digital_twin.infrastructure.web.adapters.strategy_proposals import investment_strategy_proposals_status_payload
from digital_twin.infrastructure.web.adapters.strategy_proposals import list_investment_strategy_proposals_payload
from digital_twin.infrastructure.web.adapters.strategy_proposals import record_investment_strategy_proposal_performance_payload
from digital_twin.infrastructure.web.adapters.strategy_proposals import validate_investment_strategy_proposal_payload
from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.common import request_bool
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from typing import Callable
import re
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class ModelRegistryRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    approve_hypothesis_development_payload: Callable[..., object] = approve_hypothesis_development_payload
    approve_investment_strategy_proposal_payload: Callable[..., object] = approve_investment_strategy_proposal_payload
    build_investment_brain_service: Callable[..., object] = build_investment_brain_service
    hypothesis_development_case_payload: Callable[..., object] = hypothesis_development_case_payload
    hypothesis_development_cases_payload: Callable[..., object] = hypothesis_development_cases_payload
    hypothesis_policy_versions_api_payload: Callable[..., object] = hypothesis_policy_versions_api_payload
    hypothesis_templates_api_payload: Callable[..., object] = hypothesis_templates_api_payload
    investment_brain_hypothesis_lifecycles_api_payload: Callable[..., object] = investment_brain_hypothesis_lifecycles_api_payload
    investment_brain_hypothesis_workspace_api_payload: Callable[..., object] = investment_brain_hypothesis_workspace_api_payload
    investment_model_api_payload: Callable[..., object] = investment_model_api_payload
    investment_strategy_proposal_payload: Callable[..., object] = investment_strategy_proposal_payload
    investment_strategy_proposal_performance_payload: Callable[..., object] = investment_strategy_proposal_performance_payload
    investment_strategy_proposals_status_payload: Callable[..., object] = investment_strategy_proposals_status_payload
    list_investment_strategy_proposals_payload: Callable[..., object] = list_investment_strategy_proposals_payload
    operational_read_settings: Callable[..., object] = operational_read_settings
    process_hypothesis_development_payload: Callable[..., object] = process_hypothesis_development_payload
    record_investment_strategy_proposal_performance_payload: Callable[..., object] = record_investment_strategy_proposal_performance_payload
    validate_investment_strategy_proposal_payload: Callable[..., object] = validate_investment_strategy_proposal_payload

    def route_investment_strategy_proposals(self, request, path: str, query: Query):
        if path == "/api/investment-strategy-proposals" and request.command == "GET":
            return request.send_payload(200, self.list_investment_strategy_proposals_payload(query))

        if path == "/api/investment-strategy-proposals/status" and request.command == "GET":
            return request.send_payload(200, self.investment_strategy_proposals_status_payload())

        strategy_proposal_action_match = re.match(r"^/api/investment-strategy-proposals/([^/]+)/(validate|approve|performance)$", path)
        if strategy_proposal_action_match:
            proposal_id = urllib.parse.unquote(strategy_proposal_action_match.group(1))
            action = strategy_proposal_action_match.group(2)
            if action == "performance" and request.command == "GET":
                return request.send_payload(200, self.investment_strategy_proposal_performance_payload(proposal_id))
            if request.command == "POST":
                if action == "validate":
                    if not request.ensure_writable("공유 모드에서는 투자 전략 제안을 검증할 수 없습니다."):
                        return
                    return request.send_payload(200, self.validate_investment_strategy_proposal_payload(proposal_id, request.read_json_body()))
                if action == "approve":
                    if not request.ensure_writable("공유 모드에서는 투자 전략 제안을 승인할 수 없습니다."):
                        return
                    return request.send_payload(200, self.approve_investment_strategy_proposal_payload(proposal_id, request.read_json_body()))
                if action == "performance":
                    if not request.ensure_writable("공유 모드에서는 투자 전략 성과를 기록할 수 없습니다."):
                        return
                    return request.send_payload(200, self.record_investment_strategy_proposal_performance_payload(proposal_id, request.read_json_body()))

        strategy_proposal_match = re.match(r"^/api/investment-strategy-proposals/([^/]+)$", path)
        if strategy_proposal_match and request.command == "GET":
            return request.send_payload(200, self.investment_strategy_proposal_payload(urllib.parse.unquote(strategy_proposal_match.group(1))))
        return NOT_HANDLED

    def route_investment_model(self, request, path: str, query: Query):
        if path == "/api/investment-model" and request.command == "GET":
            return request.send_payload(200, self.investment_model_api_payload(
                force=request_bool(first_query(query, "refresh"), False),
            ), cache_control="no-store")
        return NOT_HANDLED

    def route_investment_brain_hypothesis_templates(self, request, path: str, query: Query):
        if path == "/api/investment-brain/hypothesis-templates" and request.command == "GET":
            return request.send_payload(200, self.hypothesis_templates_api_payload(
                force=request_bool(first_query(query, "refresh"), False),
            ))

        if path == "/api/investment-brain/hypothesis-lifecycles" and request.command == "GET":
            return request.send_payload(200, self.investment_brain_hypothesis_lifecycles_api_payload(query))

        if path == "/api/investment-brain/hypotheses" and request.command == "GET":
            return request.send_payload(200, self.investment_brain_hypothesis_workspace_api_payload(query))

        hypothesis_detail_match = re.match(r"^/api/investment-brain/hypotheses/([^/]+)$", path)
        if hypothesis_detail_match and request.command == "GET":
            return request.send_payload(200, self.build_investment_brain_service(self.operational_read_settings()).hypothesis_workspace_detail(
                urllib.parse.unquote(hypothesis_detail_match.group(1)),
            ))

        if path == "/api/investment-brain/hypothesis-policy-versions" and request.command == "GET":
            try:
                limit = int(first_query(query, "limit") or 40)
            except ValueError:
                limit = 40
            return request.send_payload(200, self.hypothesis_policy_versions_api_payload(
                limit=limit,
                force=request_bool(first_query(query, "refresh"), False),
            ))

        if path == "/api/investment-brain/hypothesis-policy-versions/baseline" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 RuleBox 기준선 버전을 기록할 수 없습니다."):
                return
            body = request.read_json_body()
            return request.send_payload(200, self.build_investment_brain_service().record_hypothesis_policy_baseline(
                author=configured(body.get("author")) or "web-main",
            ))
        return NOT_HANDLED

    def route_investment_brain_hypothesis_policies_preview(self, request, path: str, query: Query):
        lifecycle_policy_preview_match = re.match(r"^/api/investment-brain/hypothesis-policies/([^/]+)/preview$", path)
        if lifecycle_policy_preview_match and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 가설 수명주기 정책을 미리보기할 수 없습니다."):
                return
            body = request.read_json_body()
            policy = body.get("policy") if isinstance(body.get("policy"), dict) else body
            return request.send_payload(200, self.build_investment_brain_service().preview_hypothesis_lifecycle_policy(
                urllib.parse.unquote(lifecycle_policy_preview_match.group(1)),
                policy,
                configured(body.get("changeReason")),
                symbols=body.get("symbols") or body.get("symbol"),
                world_id=configured(body.get("worldId")),
            ))

        lifecycle_policy_approve_match = re.match(r"^/api/investment-brain/hypothesis-policies/([^/]+)/approve$", path)
        if lifecycle_policy_approve_match and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 가설 수명주기 정책을 승인할 수 없습니다."):
                return
            body = request.read_json_body()
            policy = body.get("policy") if isinstance(body.get("policy"), dict) else body
            return request.send_payload(200, self.build_investment_brain_service().approve_hypothesis_lifecycle_policy(
                urllib.parse.unquote(lifecycle_policy_approve_match.group(1)),
                policy,
                configured(body.get("changeReason")),
                author=configured(body.get("author")) or "web-main",
                symbols=body.get("symbols") or body.get("symbol"),
                world_id=configured(body.get("worldId")),
            ))

        policy_version_restore_match = re.match(r"^/api/investment-brain/hypothesis-policy-versions/([^/]+)/restore$", path)
        if policy_version_restore_match and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 RuleBox 버전을 복원할 수 없습니다."):
                return
            body = request.read_json_body()
            return request.send_payload(200, self.build_investment_brain_service().restore_hypothesis_policy_version(
                urllib.parse.unquote(policy_version_restore_match.group(1)),
                configured(body.get("changeReason")),
                author=configured(body.get("author")) or "web-main",
                symbols=body.get("symbols") or body.get("symbol"),
                world_id=configured(body.get("worldId")),
            ))

        lifecycle_policy_match = re.match(r"^/api/investment-brain/hypothesis-policies/([^/]+)$", path)
        if lifecycle_policy_match and request.command == "PATCH":
            if not request.ensure_writable("공유 모드에서는 가설 수명주기 정책을 변경할 수 없습니다."):
                return
            body = request.read_json_body()
            policy = body.get("policy") if isinstance(body.get("policy"), dict) else body
            # Backward-compatible route: retain the endpoint but remove the
            # old direct-write bypass from the web surface.
            return request.send_payload(200, self.build_investment_brain_service().approve_hypothesis_lifecycle_policy(
                urllib.parse.unquote(lifecycle_policy_match.group(1)),
                policy,
                configured(body.get("changeReason")),
                author=configured(body.get("author")) or "web-main",
                symbols=body.get("symbols") or body.get("symbol"),
                world_id=configured(body.get("worldId")),
            ))
        return NOT_HANDLED

    def route_investment_brain_hypothesis_proposals(self, request, path: str, query: Query):
        if path == "/api/investment-brain/hypothesis-proposals" and request.command == "GET":
            try:
                limit = int(first_query(query, "limit") or 50)
            except ValueError:
                limit = 50
            return request.send_payload(200, self.build_investment_brain_service().hypothesis_proposals(
                status=first_query(query, "status"),
                symbol=first_query(query, "symbol"),
                limit=limit,
            ))

        if path == "/api/investment-brain/hypothesis-development" and request.command == "GET":
            return request.send_payload(200, self.hypothesis_development_cases_payload(query))

        if path == "/api/investment-brain/hypothesis-development/process" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 가설 자동 검증을 실행할 수 없습니다."):
                return
            return request.send_payload(200, self.process_hypothesis_development_payload(request.read_json_body()))

        hypothesis_development_approve_match = re.match(r"^/api/investment-brain/hypothesis-development/([^/]+)/approve$", path)
        if hypothesis_development_approve_match and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 검증된 가설을 운영 반영할 수 없습니다."):
                return
            return request.send_payload(200, self.approve_hypothesis_development_payload(
                urllib.parse.unquote(hypothesis_development_approve_match.group(1)),
                request.read_json_body(),
            ))

        hypothesis_development_match = re.match(r"^/api/investment-brain/hypothesis-development/([^/]+)$", path)
        if hypothesis_development_match and request.command == "GET":
            return request.send_payload(200, self.hypothesis_development_case_payload(
                urllib.parse.unquote(hypothesis_development_match.group(1)),
            ))

        hypothesis_proposal_match = re.match(r"^/api/investment-brain/hypothesis-proposals/([^/]+)$", path)
        if hypothesis_proposal_match and request.command == "PATCH":
            body = request.read_json_body()
            return request.send_payload(200, self.build_investment_brain_service().review_hypothesis_proposal(
                hypothesis_proposal_match.group(1),
                configured(body.get("status")),
                configured(body.get("note")),
            ))

        if path == "/api/investment-brain/learning-proposals" and request.command == "GET":
            try:
                limit = int(first_query(query, "limit") or 50)
            except ValueError:
                limit = 50
            return request.send_payload(200, self.build_investment_brain_service().learning_proposals(
                status=first_query(query, "status"),
                limit=limit,
            ))

        learning_match = re.match(r"^/api/investment-brain/learning-proposals/([^/]+)$", path)
        if learning_match and request.command == "PATCH":
            body = request.read_json_body()
            return request.send_payload(200, self.build_investment_brain_service().review_learning_proposal(
                learning_match.group(1),
                configured(body.get("status")),
                configured(body.get("note")),
            ))
        return NOT_HANDLED
