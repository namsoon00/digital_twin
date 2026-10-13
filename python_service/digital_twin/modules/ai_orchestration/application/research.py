"""Execute the captured question-specific intent through the source owner."""
import json
from copy import deepcopy
from digital_twin.modules.decisions.contracts import InvestmentQuestion
from digital_twin.modules.news_intelligence.contracts import NewsCollectionTarget, source_recovery_summary
from ..domain.planning import identity
from ..domain.research_request import executable_research_request
from ..domain.research_return import research_return
from ..domain.research_wait import AIResearchWait


def previous_recovery(job, research_store):
    """Read old receipts as operational history; never rewrite old answers."""
    if job.get("previousSourceRecovery"):
        return deepcopy(job["previousSourceRecovery"])
    reference = job.get("previousSourceRun") or {}
    if not reference.get("runId") or not reference.get("taskId"):
        return {}
    run = research_store.get_run(reference["runId"]) or {}
    expected_task = identity(reference["taskId"], "sources")
    if (run.get("accountId") != job["accountId"] or run.get("symbol") != job["symbol"]
            or run.get("runId") != reference["runId"] or run.get("taskIds") != [expected_task]):
        return {}  # A missing or unrelated receipt cannot influence this plan.
    audit = (run.get("executedPlan") or {}).get("sourceExploration") or {}
    return {**source_recovery_summary(audit, run.get("providerStatuses") or []),
            "runId": reference["runId"], "taskId": reference["taskId"], "historical": True}


def execute_research(job, research_store, orchestrator_factory):
    request = executable_research_request(job.get("researchRequest"), job["accountId"])
    run_id = "ai-control-" + job["taskId"][:40]
    source_task_id = identity(job["taskId"], "sources")
    existing = research_store.get_run(run_id)
    if existing and existing.get("status") not in {"queued", "processing", "failed", "research-cooldown"}:
        return {**research_return(existing, run_id, source_task_id, request), "reused": True}
    question = InvestmentQuestion.create(job["question"], subject_symbol=job["symbol"],
        subject_name=job["name"], account_id=job["accountId"], source="ai-control")
    sources = request.get("sourceTypes", ["official-filing", "news"])
    documentary = "official-filing" in sources
    collection_sources = [*sources, "company-ir"] if documentary else sources
    task = {"taskId": source_task_id, "question": job["question"],
        "purpose": "독립 관찰에서 제기한 가설의 근거와 반대 근거 확인", "status": "blocked-by-data",
        "requiredEvidenceTypes": ["news-full-text" if source == "news" else "official-document" for source in sources],
        "sourceTypes": collection_sources, "queryTerms": request.get("queryTerms", []),
        "requiresDocumentBody": documentary,
        "maxAgeMinutes": request.get("maxAgeMinutes", 1440), "decisionRelevance": "supporting"}
    if job.get("questionContract"):
        from ..domain.research_work import validate_contract
        validate_contract(job["questionContract"])
        # Purpose is already part of the source owner's immutable assessment
        # fingerprint and model input. Keep the original question intact.
        task["purpose"] = "원래 질문의 대상·기간·완료 기준을 모두 확인합니다: " + json.dumps(job["questionContract"], ensure_ascii=False, sort_keys=True)
    orchestrator = orchestrator_factory()
    # Keep the authored collection scope, but allow the source owner to review
    # the collected answer independently of market-observation quality gates.
    run = orchestrator.run(question, NewsCollectionTarget(job["symbol"], job["name"]),
        {"documentaryReviewOnly": True, "documentarySourceExploration": documentary,
         "previousSourceRecovery": previous_recovery(job, research_store),
         "sourceExplorationResume": ((existing or {}).get("executedPlan") or {}).get("sourceExploration"),
         "researchPlan": {"planId": job["taskId"], "tasks": [task], "unresolvedQuestions": [job["question"]]}},
        account_id=job["accountId"], run_id=run_id,
        request_context={"aiControlTaskId": job["taskId"], "source": "ai-control", "question": question.to_dict(),
                         "researchRequest": request})
    payload = run.to_dict()
    result = research_return(payload, run.run_id, source_task_id, request)
    if payload.get("status") == "research-cooldown":
        raise AIResearchWait(payload, result.get("documentaryAnswer"))
    return result
