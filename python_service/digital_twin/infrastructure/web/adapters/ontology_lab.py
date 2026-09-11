"""Web ontology lab boundary."""

from digital_twin.infrastructure.service_factory import build_hypothesis_development_service
from digital_twin.infrastructure.service_factory import build_ontology_lab_service
from digital_twin.infrastructure.service_factory import build_rule_change_candidate_service
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.stale_read_model import StaleReadModelCache
from digital_twin.infrastructure.web.cache import cached_api_payload
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import request_bool
from typing import Dict
from typing import List


ONTOLOGY_EXPERIMENT_STATUS_READ_MODEL = StaleReadModelCache(
    "ontology-experiment-status",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)


ONTOLOGY_EXPERIMENT_LIST_READ_MODEL = StaleReadModelCache(
    "ontology-experiment-list",
    ttl_seconds=30,
    retry_cooldown_seconds=15,
)


def ontology_lab_service():
    return build_ontology_lab_service(runtime_settings())


def hypothesis_development_service():
    return build_hypothesis_development_service(runtime_settings())


def hypothesis_development_cases_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    try:
        limit = int(first_query(query, "limit") or 100)
    except ValueError:
        limit = 100
    return hypothesis_development_service().list(
        status=first_query(query, "status"),
        symbol=first_query(query, "symbol"),
        limit=max(1, min(500, limit)),
    )


def hypothesis_development_case_payload(case_id: str) -> Dict[str, object]:
    return hypothesis_development_service().report(case_id)


def process_hypothesis_development_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = dict(payload or {})
    if str(body.get("caseId") or ""):
        return hypothesis_development_service().process(str(body.get("caseId")))
    return hypothesis_development_service().process_pending(limit=max(1, min(20, int(body.get("limit") or 5))))


def approve_hypothesis_development_payload(case_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    service = hypothesis_development_service()
    report = service.report(case_id)
    case = report.get("case") if isinstance(report.get("case"), dict) else {}
    if str(case.get("status") or "") != "approval-required":
        return {
            "status": "not-ready",
            "reason": "hypothesis-development-case-not-validated",
            "case": case,
        }
    experiment_id = str(case.get("experimentId") or "")
    body = {
        **dict(payload or {}),
        "runRulebox": True,
        "rollbackOnInferenceFailure": True,
        "reviewApproved": True,
        "reviewedBy": str((payload or {}).get("reviewedBy") or "web-main"),
        "reviewReason": str((payload or {}).get("reviewReason") or "검증 탭에서 자동 검증 완료 가설의 운영 반영 승인"),
    }
    result = ontology_lab_service().apply_recommendations(experiment_id, body)
    development = service.mark_deployed(case_id, result.get("application") or result)
    return {"status": development.get("status") or result.get("status"), "development": development, "application": result}


def list_ontology_experiments_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = max(1, min(100, int(first_query(query, "limit") or 8)))
    offset = max(0, int(first_query(query, "offset") or 0))
    summary = not request_bool(first_query(query, "detail"), False)
    return cached_api_payload(
        ONTOLOGY_EXPERIMENT_LIST_READ_MODEL,
        "|".join([str(limit), str(offset), "summary" if summary else "detail"]),
        lambda: ontology_lab_service().list(limit=limit, offset=offset, summary=summary),
        force=request_bool(first_query(query, "refresh"), False),
    )


def _ontology_experiments_status_source_payload() -> Dict[str, object]:
    return ontology_lab_service().status()


def ontology_experiments_status_payload(force: bool = False) -> Dict[str, object]:
    return cached_api_payload(
        ONTOLOGY_EXPERIMENT_STATUS_READ_MODEL,
        "status",
        _ontology_experiments_status_source_payload,
        force=force,
        blocking_first_load=False,
    )


def create_ontology_experiment_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_lab_service().create(payload if isinstance(payload, dict) else {})


def suggest_ontology_experiments_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    symbols = body.get("symbols") if isinstance(body.get("symbols"), list) else []
    candidate_result = build_rule_change_candidate_service(runtime_settings()).propose(
        symbols=symbols,
        trigger=str(body.get("trigger") or "ontology-lab-suggest"),
        account_id=str(body.get("accountId") or body.get("account_id") or ""),
        tenant_id=str(body.get("tenantId") or body.get("tenant_id") or ""),
    )
    result = ontology_lab_service().suggest_from_rule_candidates(candidate_result, body)
    result["candidateResult"] = {
        "status": candidate_result.get("status"),
        "candidateCount": candidate_result.get("candidateCount"),
        "savedCount": candidate_result.get("savedCount"),
        "contextSummary": candidate_result.get("contextSummary") or {},
    }
    return result


def ontology_experiment_payload(experiment_id: str) -> Dict[str, object]:
    return ontology_lab_service().report(experiment_id)


def run_ontology_experiment_payload(experiment_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_lab_service().run(experiment_id, payload if isinstance(payload, dict) else {})


def apply_ontology_experiment_payload(experiment_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_lab_service().apply_recommendations(experiment_id, payload if isinstance(payload, dict) else {})


def apply_ontology_experiments_batch_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_lab_service().apply_recommendation_batch(payload if isinstance(payload, dict) else {})


def run_ontology_experiments_once_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    limit = int(body.get("limit") or 0)
    force = bool(body.get("force"))
    return ontology_lab_service().run_once(limit=limit, force=force)


def activate_ontology_experiment_payload(experiment_id: str) -> Dict[str, object]:
    return ontology_lab_service().activate(experiment_id)


def pause_ontology_experiment_payload(experiment_id: str) -> Dict[str, object]:
    return ontology_lab_service().pause(experiment_id)
