"""Execute the captured question-specific intent through the source owner."""
from digital_twin.modules.decisions.contracts import InvestmentQuestion
from digital_twin.modules.news_intelligence.contracts import NewsCollectionTarget
from ..domain.planning import identity
from ..domain.research_request import executable_research_request


def execute_research(job, research_store, orchestrator_factory):
    request = executable_research_request(job.get("researchRequest"), job["accountId"])
    run_id = "ai-control-" + job["taskId"][:40]
    existing = research_store.get_run(run_id)
    if existing and existing.get("status") not in {"queued", "processing", "failed"}:
        return {"runId": run_id, "status": existing["status"], "reused": True, "researchRequest": request}
    question = InvestmentQuestion.create(job["question"], subject_symbol=job["symbol"],
        subject_name=job["name"], account_id=job["accountId"], source="ai-control")
    sources = request.get("sourceTypes", ["official-filing", "news"])
    task = {"taskId": identity(job["taskId"], "sources"), "question": job["question"],
        "purpose": "독립 관찰에서 제기한 가설의 근거와 반대 근거 확인", "status": "blocked-by-data",
        "requiredEvidenceTypes": ["news-full-text" if source == "news" else source for source in sources],
        "sourceTypes": sources, "queryTerms": request.get("queryTerms", []),
        "maxAgeMinutes": request.get("maxAgeMinutes", 1440), "decisionRelevance": "supporting"}
    orchestrator = orchestrator_factory()
    # The author already specified intent. Source verification and question
    # assessments remain owned by research; no second planning call is needed.
    orchestrator.hypothesis_research_planner = None
    run = orchestrator.run(question, NewsCollectionTarget(job["symbol"], job["name"]),
        {"researchPlan": {"planId": job["taskId"], "tasks": [task], "unresolvedQuestions": [job["question"]]}},
        account_id=job["accountId"], run_id=run_id,
        request_context={"aiControlTaskId": job["taskId"], "source": "ai-control", "question": question.to_dict(),
                         "researchRequest": request})
    return {"runId": run.run_id, "status": run.status, "changedEvidenceCount": run.changed_evidence_count,
            "stopReason": run.stop_reason, "researchRequest": request,
            "searchScope": "question-specific" if request else "legacy-subject-refresh", "authority": "research-only"}
