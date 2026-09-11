"""Web cases boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.common import safe_int
from digital_twin.modules.read_models.public import InvestmentCaseQueryService
from digital_twin.modules.read_models.public import InvestmentFlowQueryService
from typing import Dict
from typing import List


def investment_reasoning_cases_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    settings = runtime_settings()
    store = stores.investment_reasoning_case_store(settings)
    subject_store = stores.subject_decision_case_store(settings)
    subject_case_id = str(first_query(query, "subjectCaseId") or "").strip()
    if subject_case_id:
        subject_case = subject_store.get(subject_case_id)
        batch_case = store.get(subject_case.batch_case_id) if subject_case else None
        detail = investment_case_api_payload({}, case_id=subject_case_id) if subject_case else {}
        return {
            "status": "ok" if subject_case else "not-found",
            "subjectCase": subject_case.to_dict() if subject_case else {},
            "batchCase": batch_case.to_dict() if batch_case else {},
            "auditTrail": subject_store.audit_trail(subject_case_id) if subject_case else [],
            "lineage": detail.get("reasoningLineage") or {},
        }
    case_id = str(first_query(query, "caseId") or "").strip()
    if case_id:
        reasoning_case = store.get(case_id)
        return {
            "status": "ok" if reasoning_case else "not-found",
            "case": reasoning_case.to_dict() if reasoning_case else {},
            "subjectCases": [
                item.to_dict() for item in subject_store.for_batch(case_id)
            ] if reasoning_case else [],
        }
    deployment_id = str(first_query(query, "deploymentId") or "").strip()
    symbol = str(first_query(query, "symbol") or "").upper().strip()
    release_fingerprint = str(first_query(query, "releaseFingerprint") or "").strip()
    try:
        limit = max(1, min(200, int(first_query(query, "limit") or 20)))
    except (TypeError, ValueError):
        limit = 20
    cases = store.latest(deployment_id=deployment_id, symbol=symbol, limit=limit)
    return {
        "status": "ok",
        "summary": store.summary(deployment_id, release_fingerprint),
        "cases": [reasoning_case.to_dict() for reasoning_case in cases],
    }


def investment_flow_api_payload(query: Dict[str, List[str]], episode_id: str = "") -> Dict[str, object]:
    """Read the persisted decision lineage without running TypeDB on an HTTP request."""

    settings = operational_read_settings()
    service = InvestmentFlowQueryService(
        decision_episode_store=stores.investment_decision_episode_store(settings),
        notification_job_store=stores.notification_job_store(settings),
        hypothesis_lifecycle_store=stores.hypothesis_lifecycle_store(settings),
    )
    if episode_id:
        return service.detail(str(episode_id or ""))
    return service.summary(
        account_id=str(first_query(query, "accountId") or first_query(query, "account") or "").strip(),
        symbol=str(first_query(query, "symbol") or "").upper().strip(),
        limit=safe_int(first_query(query, "limit"), 100, 1, 500),
    )


def investment_case_api_payload(
    query: Dict[str, List[str]],
    case_id: str = "",
    section: str = "",
) -> Dict[str, object]:
    """Read user-facing cases from persisted decisions without invoking TypeDB."""

    settings = operational_read_settings()
    decision_episode_store = stores.investment_decision_episode_store(settings)
    service = InvestmentCaseQueryService(
        decision_episode_store=decision_episode_store,
        notification_job_store=stores.notification_job_store(settings),
        hypothesis_lifecycle_store=stores.hypothesis_lifecycle_store(settings),
        monitor_store=stores.monitor_store(settings) if case_id else None,
        evidence_repository=stores.research_evidence_store(settings) if case_id else None,
        investment_domain_store=stores.investment_domain_store(settings) if case_id else None,
        symbol_repository=stores.symbol_universe_store(settings),
        subject_case_repository=stores.subject_decision_case_store(settings),
        ai_insight_repository=stores.ai_inference_queue_store(settings),
        reasoning_case_repository=stores.investment_reasoning_case_store(settings),
        hypothesis_observation_repository=decision_episode_store,
    )
    if case_id and section == "history":
        return service.history(
            str(case_id or ""),
            limit=safe_int(first_query(query, "limit"), 30, 1, 200),
        )
    if case_id and section == "trace":
        return service.trace(str(case_id or ""))
    if case_id:
        return service.detail(str(case_id or ""))
    return service.list_cases(
        account_id=str(first_query(query, "accountId") or first_query(query, "account") or "").strip(),
        symbol=str(first_query(query, "symbol") or "").upper().strip(),
        limit=safe_int(first_query(query, "limit"), 100, 1, 500),
        include_operator=str(
            first_query(query, "audience") or first_query(query, "includeOperator") or ""
        ).strip().lower() in {"operator", "1", "true", "yes"},
    )
