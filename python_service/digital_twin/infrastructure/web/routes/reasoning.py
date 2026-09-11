"""Reasoning HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.web.adapters.cases import investment_reasoning_cases_payload
from digital_twin.infrastructure.web.adapters.ontology_audit import ontology_audit_payload
from digital_twin.infrastructure.web.adapters.ontology_catalog import ontology_catalog_api_payload
from digital_twin.infrastructure.web.adapters.ontology_catalog import ontology_rulebox_payload
from digital_twin.infrastructure.web.adapters.ontology_diagnostics import ontology_diagnostics_payload
from digital_twin.infrastructure.web.adapters.ontology_governance import ontology_language_payload
from digital_twin.infrastructure.web.adapters.ontology_governance import preview_ontology_language_payload
from digital_twin.infrastructure.web.adapters.ontology_governance import propose_ontology_rule_candidates_payload
from digital_twin.infrastructure.web.adapters.ontology_governance import run_ontology_rulebox_payload
from digital_twin.infrastructure.web.adapters.ontology_governance import save_ontology_language_payload
from digital_twin.infrastructure.web.adapters.ontology_governance import save_ontology_rulebox_payload
from digital_twin.infrastructure.web.adapters.ontology_governance import seed_ontology_payload
from digital_twin.infrastructure.web.adapters.ontology_governance import suggest_ontology_language_payload
from digital_twin.infrastructure.web.adapters.ontology_governance import validate_ontology_language_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import activate_ontology_experiment_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import apply_ontology_experiment_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import apply_ontology_experiments_batch_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import create_ontology_experiment_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import list_ontology_experiments_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import ontology_experiment_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import ontology_experiments_status_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import pause_ontology_experiment_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import run_ontology_experiment_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import run_ontology_experiments_once_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import suggest_ontology_experiments_payload
from digital_twin.infrastructure.web.adapters.ontology_ledger import ontology_inference_ledger_api_payload
from digital_twin.infrastructure.web.adapters.platforms import ontology_reasoning_status_payload
from digital_twin.infrastructure.web.adapters.platforms import reasoning_engine_comparisons_payload
from digital_twin.infrastructure.web.adapters.platforms import reasoning_engine_platform_status_payload
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import request_bool
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from typing import Callable
import re
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class ReasoningRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    activate_ontology_experiment_payload: Callable[..., object] = activate_ontology_experiment_payload
    apply_ontology_experiment_payload: Callable[..., object] = apply_ontology_experiment_payload
    apply_ontology_experiments_batch_payload: Callable[..., object] = apply_ontology_experiments_batch_payload
    create_ontology_experiment_payload: Callable[..., object] = create_ontology_experiment_payload
    investment_reasoning_cases_payload: Callable[..., object] = investment_reasoning_cases_payload
    list_ontology_experiments_payload: Callable[..., object] = list_ontology_experiments_payload
    ontology_audit_payload: Callable[..., object] = ontology_audit_payload
    ontology_catalog_api_payload: Callable[..., object] = ontology_catalog_api_payload
    ontology_diagnostics_payload: Callable[..., object] = ontology_diagnostics_payload
    ontology_experiment_payload: Callable[..., object] = ontology_experiment_payload
    ontology_experiments_status_payload: Callable[..., object] = ontology_experiments_status_payload
    ontology_inference_ledger_api_payload: Callable[..., object] = ontology_inference_ledger_api_payload
    ontology_language_payload: Callable[..., object] = ontology_language_payload
    ontology_reasoning_status_payload: Callable[..., object] = ontology_reasoning_status_payload
    ontology_rulebox_payload: Callable[..., object] = ontology_rulebox_payload
    pause_ontology_experiment_payload: Callable[..., object] = pause_ontology_experiment_payload
    preview_ontology_language_payload: Callable[..., object] = preview_ontology_language_payload
    propose_ontology_rule_candidates_payload: Callable[..., object] = propose_ontology_rule_candidates_payload
    reasoning_engine_comparisons_payload: Callable[..., object] = reasoning_engine_comparisons_payload
    reasoning_engine_platform_status_payload: Callable[..., object] = reasoning_engine_platform_status_payload
    run_ontology_experiment_payload: Callable[..., object] = run_ontology_experiment_payload
    run_ontology_experiments_once_payload: Callable[..., object] = run_ontology_experiments_once_payload
    run_ontology_rulebox_payload: Callable[..., object] = run_ontology_rulebox_payload
    save_ontology_language_payload: Callable[..., object] = save_ontology_language_payload
    save_ontology_rulebox_payload: Callable[..., object] = save_ontology_rulebox_payload
    seed_ontology_payload: Callable[..., object] = seed_ontology_payload
    suggest_ontology_experiments_payload: Callable[..., object] = suggest_ontology_experiments_payload
    suggest_ontology_language_payload: Callable[..., object] = suggest_ontology_language_payload
    validate_ontology_language_payload: Callable[..., object] = validate_ontology_language_payload

    def route_reasoning_engine_status(self, request, path: str, query: Query):
        if path == "/api/reasoning-engine/status" and request.command == "GET":
            return request.send_payload(200, self.reasoning_engine_platform_status_payload(query))

        if path == "/api/reasoning-engine/comparisons" and request.command == "GET":
            return request.send_payload(200, self.reasoning_engine_comparisons_payload(query))

        if path == "/api/investment-reasoning/cases" and request.command == "GET":
            return request.send_payload(200, self.investment_reasoning_cases_payload(query))

        if path == "/api/ontology/rulebox":
            if request.command == "GET":
                return request.send_payload(200, self.ontology_rulebox_payload(
                    force=request_bool(first_query(query, "refresh"), False),
                ))
            if request.command in {"POST", "PUT"}:
                if not request.ensure_writable("공유 모드에서는 TypeDB RuleBox를 변경할 수 없습니다."):
                    return
                return request.send_payload(200, self.save_ontology_rulebox_payload(request.read_json_body()))

        ontology_catalog_match = re.match(
            r"^/api/ontology/catalog/(summary|classes|relations|rules|hypotheses|inferences|lineage)$",
            path,
        )
        if ontology_catalog_match and request.command == "GET":
            return request.send_payload(200, self.ontology_catalog_api_payload(
                ontology_catalog_match.group(1),
                query,
            ))

        if path == "/api/ontology/language":
            if request.command == "GET":
                return request.send_payload(200, self.ontology_language_payload())
            if request.command in {"POST", "PUT"}:
                if not request.ensure_writable("공유 모드에서는 보편언어 사전을 변경할 수 없습니다."):
                    return
                return request.send_payload(200, self.save_ontology_language_payload(request.read_json_body()))

        if path == "/api/ontology/language/validate" and request.command == "POST":
            return request.send_payload(200, self.validate_ontology_language_payload(request.read_json_body()))

        if path == "/api/ontology/language/preview" and request.command == "POST":
            return request.send_payload(200, self.preview_ontology_language_payload(request.read_json_body()))

        if path == "/api/ontology/language/suggest" and request.command == "POST":
            return request.send_payload(200, self.suggest_ontology_language_payload(request.read_json_body()))

        if path == "/api/ontology/rulebox/run" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 TypeDB 네이티브 규칙 추론을 실행할 수 없습니다."):
                return
            return request.send_payload(200, self.run_ontology_rulebox_payload(request.read_json_body()))

        if path == "/api/ontology/diagnostics" and request.command == "GET":
            return request.send_payload(200, self.ontology_diagnostics_payload(query))

        if path == "/api/ontology/inference-ledger" and request.command == "GET":
            payload = self.ontology_inference_ledger_api_payload(query)
            status = 503 if request_bool(first_query(query, "direct"), False) and not payload.get("usable") else 200
            return request.send_payload(status, payload)

        if path == "/api/ontology/audit" and request.command == "GET":
            return request.send_payload(200, self.ontology_audit_payload(query))

        ontology_audit_match = re.match(r"^/api/ontology/audit/([^/]+)$", path)
        if ontology_audit_match and request.command == "GET":
            return request.send_payload(200, self.ontology_audit_payload(
                query,
                urllib.parse.unquote(ontology_audit_match.group(1)),
            ))

        if path == "/api/ontology/rulebox/candidates" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 TypeDB RuleBox 후보를 생성할 수 없습니다."):
                return
            return request.send_payload(200, self.propose_ontology_rule_candidates_payload(request.read_json_body()))

        if path == "/api/ontology/seed" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 온톨로지 그래프 시드를 실행할 수 없습니다."):
                return
            return request.send_payload(200, self.seed_ontology_payload(request.read_json_body()))

        if path == "/api/ontology/experiments" and request.command == "GET":
            return request.send_payload(200, self.list_ontology_experiments_payload(query))

        if path == "/api/ontology/experiments" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 온톨로지 실험을 생성할 수 없습니다."):
                return
            return request.send_payload(200, self.create_ontology_experiment_payload(request.read_json_body()))

        if path == "/api/ontology/experiments/status" and request.command == "GET":
            return request.send_payload(200, self.ontology_experiments_status_payload(
                force=request_bool(first_query(query, "refresh"), False),
            ))

        if path == "/api/ontology/reasoning/status" and request.command == "GET":
            return request.send_payload(200, self.ontology_reasoning_status_payload())

        if path == "/api/ontology/experiments/once" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 온톨로지 실험을 실행할 수 없습니다."):
                return
            return request.send_payload(200, self.run_ontology_experiments_once_payload(request.read_json_body()))

        if path == "/api/ontology/experiments/suggest" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 AI 온톨로지 실험 제안을 생성할 수 없습니다."):
                return
            return request.send_payload(200, self.suggest_ontology_experiments_payload(request.read_json_body()))

        if path == "/api/ontology/experiments/apply" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 온톨로지 실험 제안을 운영 반영할 수 없습니다."):
                return
            return request.send_payload(200, self.apply_ontology_experiments_batch_payload(request.read_json_body()))

        ontology_experiment_run_match = re.match(r"^/api/ontology/experiments/([^/]+)/run$", path)
        if ontology_experiment_run_match and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 온톨로지 실험을 실행할 수 없습니다."):
                return
            return request.send_payload(200, self.run_ontology_experiment_payload(
                urllib.parse.unquote(ontology_experiment_run_match.group(1)),
                request.read_json_body(),
            ))

        ontology_experiment_apply_match = re.match(r"^/api/ontology/experiments/([^/]+)/apply$", path)
        if ontology_experiment_apply_match and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 온톨로지 실험 제안을 운영 반영할 수 없습니다."):
                return
            return request.send_payload(200, self.apply_ontology_experiment_payload(
                urllib.parse.unquote(ontology_experiment_apply_match.group(1)),
                request.read_json_body(),
            ))

        ontology_experiment_activate_match = re.match(r"^/api/ontology/experiments/([^/]+)/activate$", path)
        if ontology_experiment_activate_match and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 온톨로지 실험 상태를 변경할 수 없습니다."):
                return
            return request.send_payload(200, self.activate_ontology_experiment_payload(
                urllib.parse.unquote(ontology_experiment_activate_match.group(1)),
            ))

        ontology_experiment_pause_match = re.match(r"^/api/ontology/experiments/([^/]+)/pause$", path)
        if ontology_experiment_pause_match and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 온톨로지 실험 상태를 변경할 수 없습니다."):
                return
            return request.send_payload(200, self.pause_ontology_experiment_payload(
                urllib.parse.unquote(ontology_experiment_pause_match.group(1)),
            ))

        ontology_experiment_match = re.match(r"^/api/ontology/experiments/([^/]+)$", path)
        if ontology_experiment_match and request.command == "GET":
            return request.send_payload(200, self.ontology_experiment_payload(urllib.parse.unquote(ontology_experiment_match.group(1))))
        return NOT_HANDLED
