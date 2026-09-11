"""Web ontology governance boundary."""

from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
from digital_twin.infrastructure.service_factory import build_rule_change_candidate_service
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.settings import save_runtime_settings
from digital_twin.infrastructure.web.adapters.ontology_access import ontology_world_id_from_values
from digital_twin.infrastructure.web.adapters.ontology_catalog import ONTOLOGY_RULEBOX_READ_MODEL
from digital_twin.infrastructure.web.common import now
from digital_twin.infrastructure.web.events import new_domain_event
from digital_twin.modules.model_registry.domain.investment_ubiquitous_language import LANGUAGE_REGISTRY_SETTING_KEY
from digital_twin.modules.model_registry.domain.investment_ubiquitous_language import audit_user_facing_investment_text
from digital_twin.modules.model_registry.domain.investment_ubiquitous_language import investment_language_registry
from digital_twin.modules.model_registry.domain.investment_ubiquitous_language import normalize_investment_language_registry
from digital_twin.modules.model_registry.domain.investment_ubiquitous_language import propose_investment_language_changes
from digital_twin.modules.model_registry.domain.investment_ubiquitous_language import validate_investment_language_registry
from digital_twin.modules.reasoning.domain.ontology_worlds import PORTFOLIO_WORLD_TYPE
from digital_twin.modules.reasoning.domain.ontology_worlds import world_type_from_id
from digital_twin.platform.domain.event_types import SETTINGS_UPDATED
from typing import Dict
import json


def save_ontology_rulebox_payload(payload: Dict[str, object]) -> Dict[str, object]:
    result = ontology_repository_from_settings(runtime_settings()).save_rulebox(payload)
    if isinstance(result, dict) and result:
        ONTOLOGY_RULEBOX_READ_MODEL.store_success("active", result)
    return result


def ontology_language_payload() -> Dict[str, object]:
    settings = runtime_settings()
    registry = investment_language_registry(settings)
    validation = validate_investment_language_registry(registry)
    return {
        "registry": registry,
        "validation": {key: value for key, value in validation.items() if key != "registry"},
        "typeDb": {
            "configured": bool(str(settings.get("typedbAddress") or "").strip()),
            "ontologyBox": "LanguageGovernance",
            "projection": "보편언어 사전은 TypeDB 관리 개념으로 저장되며 투자 규칙과 별도로 버전 관리됩니다.",
        },
    }


def save_ontology_language_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    registry_input = body.get("registry") if isinstance(body.get("registry"), dict) else body
    registry = normalize_investment_language_registry(registry_input)
    registry["updatedAt"] = now()
    registry["source"] = "admin-approved"
    validation = validate_investment_language_registry(registry)
    if not validation.get("valid"):
        raise ValueError("보편언어 사전에 오류가 있어 저장하지 않았습니다: " + "; ".join(
            str(item.get("message") or "") for item in validation.get("errors") or []
        ))
    saved_settings = save_runtime_settings({
        LANGUAGE_REGISTRY_SETTING_KEY: json.dumps(registry, ensure_ascii=False, sort_keys=True),
    })
    type_db_sync: Dict[str, object] = {"status": "skipped", "reason": "활성 TypeDB 규칙을 확인하지 못했습니다."}
    repository = ontology_repository_from_settings(saved_settings)
    try:
        rulebox = repository.rulebox_snapshot()
        active_rules = rulebox.get("rules") if isinstance(rulebox.get("rules"), list) else []
        if active_rules:
            type_db_sync = repository.save_rulebox({"rules": active_rules})
        elif not str(saved_settings.get("typedbAddress") or "").strip():
            type_db_sync = {"status": "disabled", "saved": False, "reason": "TypeDB가 설정되지 않아 로컬 사전만 저장했습니다."}
    except Exception as error:  # noqa: BLE001 - the approved registry remains locally recoverable.
        type_db_sync = {"status": "error", "saved": False, "reason": str(error)[:220]}
    result = ontology_language_payload()
    result["saved"] = True
    result["typeDbSync"] = type_db_sync
    new_domain_event(
        SETTINGS_UPDATED,
        "investment-language",
        {
            "keys": [LANGUAGE_REGISTRY_SETTING_KEY],
            "registryVersion": registry.get("version"),
            "termCount": len(registry.get("terms") or []),
            "typeDbStatus": type_db_sync.get("status"),
        },
    )
    return result


def validate_ontology_language_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    registry_input = body.get("registry") if isinstance(body.get("registry"), dict) else body
    validation = validate_investment_language_registry(registry_input)
    return {key: value for key, value in validation.items() if key != "registry"}


def preview_ontology_language_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    settings = runtime_settings()
    if isinstance(body.get("registry"), dict):
        settings = {**settings, LANGUAGE_REGISTRY_SETTING_KEY: body.get("registry")}
    return audit_user_facing_investment_text(
        body.get("text") or "",
        settings,
        str(body.get("level") or "absoluteBeginner"),
    )


def suggest_ontology_language_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    settings = runtime_settings()
    if isinstance(body.get("registry"), dict):
        settings = {**settings, LANGUAGE_REGISTRY_SETTING_KEY: body.get("registry")}
    return propose_investment_language_changes(
        body.get("text") or "",
        settings,
        str(body.get("level") or "absoluteBeginner"),
    )


def run_ontology_rulebox_payload(payload: Dict[str, object]) -> Dict[str, object]:
    values = dict(payload or {})
    world_id = ontology_world_id_from_values(values)
    if not world_id:
        return {
            "status": "world-required",
            "reason": "TypeDB native RuleBox inference requires accountId or an explicit PortfolioWorld worldId.",
            "preservedActiveGeneration": True,
        }
    if world_type_from_id(world_id) != PORTFOLIO_WORLD_TYPE:
        return {
            "status": "portfolio-world-required",
            "reason": "TypeDB native investment inference can run only for a PortfolioWorld, not a shared MarketWorld.",
            "worldId": world_id,
            "preservedActiveGeneration": True,
        }
    values["worldId"] = world_id
    return ontology_repository_from_settings(runtime_settings()).run_rulebox(values)


def propose_ontology_rule_candidates_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    symbols = body.get("symbols") if isinstance(body.get("symbols"), list) else []
    result = build_rule_change_candidate_service(runtime_settings()).propose(
        symbols=symbols,
        trigger=str(body.get("trigger") or "manual"),
        account_id=str(body.get("accountId") or body.get("account_id") or ""),
        tenant_id=str(body.get("tenantId") or body.get("tenant_id") or ""),
    )
    snapshot = ontology_repository_from_settings(runtime_settings()).rulebox_snapshot()
    result["rulebox"] = snapshot
    return result


def seed_ontology_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_repository_from_settings(runtime_settings()).seed_ontology(payload)
