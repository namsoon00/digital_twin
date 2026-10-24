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
    previous_answer = job.get("knownSourceAnswer") or {}
    missing = job.get("evidenceRequirements") or previous_answer.get("missingRequirements", [])
    if missing:
        from digital_twin.modules.news_intelligence.contracts import source_research_focus
        task["sourceFocus"] = source_research_focus(missing)
    prior_plan = (existing or {}).get("executedPlan") or {}
    if job.get("questionContract") and not prior_plan.get("sourceExploration"):
        from digital_twin.modules.news_intelligence.contracts import source_requirements
        task["sourceRequirements"] = source_requirements(job["questionContract"], missing, previous_answer.get("sources", []))
    if prior_plan.get("sourceExploration"):
        # A deployed selector must not change the identity of an already
        # checkpointed exploration or reset its consumed call/read budget.
        prior_task = next((row for row in prior_plan.get("tasks", [])
                           if row.get("taskId") == source_task_id), {})
        if "sourceFocus" in prior_task:
            task["sourceFocus"] = deepcopy(prior_task["sourceFocus"])
        else:
            task.pop("sourceFocus", None)
        if "sourceRequirements" in prior_task:
            task["sourceRequirements"] = deepcopy(prior_task["sourceRequirements"])
    if job.get("evidenceRequirements"):
        task["purpose"] += "\n원문 조회에서 확인한 미확인 항목만 보완합니다: " + json.dumps(job["evidenceRequirements"], ensure_ascii=False)
    if previous_answer.get("status") == "partial" or (previous_answer.get("status") == "answered" and job.get("evidenceRequirements")):
        task["purpose"] += ("\n이미 확인한 답변은 보존하고 아래 미확인 항목만 추가 탐색하세요. "
            "이전 답변은 역사적 작업 기록이며 이번 답변의 근거를 대신하지 않습니다. "
            "완료 판단은 원래 질문 전체 기준을 유지합니다: " + json.dumps({
                "previousAnswer": previous_answer.get("text", ""),
                "missingRequirements": job.get("evidenceRequirements") or previous_answer.get("missingRequirements", []),
                "sourceEvidenceIds": [row.get("evidenceId") for row in previous_answer.get("sources", [])],
            }, ensure_ascii=False, sort_keys=True))
    orchestrator = orchestrator_factory()
    # Keep the authored collection scope, but allow the source owner to review
    # the collected answer independently of market-observation quality gates.
    run = orchestrator.run(question, NewsCollectionTarget(job["symbol"], job["name"]),
        {"documentaryReviewOnly": True, "documentarySourceExploration": documentary or "news" in sources,
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
