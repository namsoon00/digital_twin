"""Execute the captured question-specific intent through the source owner."""
from digital_twin.modules.decisions.contracts import InvestmentQuestion
from digital_twin.modules.news_intelligence.contracts import NewsCollectionTarget
from ..domain.planning import identity
from ..domain.research_request import executable_research_request
from ..domain.research_return import research_return


def execute_research(job, research_store, orchestrator_factory):
    request = executable_research_request(job.get("researchRequest"), job["accountId"])
    run_id = "ai-control-" + job["taskId"][:40]
    source_task_id = identity(job["taskId"], "sources")
    existing = research_store.get_run(run_id)
    if existing and existing.get("status") not in {"queued", "processing", "failed"}:
        return {**research_return(existing, run_id, source_task_id, request), "reused": True}
    question = InvestmentQuestion.create(job["question"], subject_symbol=job["symbol"],
        subject_name=job["name"], account_id=job["accountId"], source="ai-control")
    sources = request.get("sourceTypes", ["official-filing", "news"])
    task = {"taskId": source_task_id, "question": job["question"],
        "purpose": "독립 관찰에서 제기한 가설의 근거와 반대 근거 확인", "status": "blocked-by-data",
        "requiredEvidenceTypes": ["news-full-text" if source == "news" else source for source in sources],
        "sourceTypes": sources, "queryTerms": request.get("queryTerms", []),
        "requiresDocumentBody": "official-filing" in sources,
        "maxAgeMinutes": request.get("maxAgeMinutes", 1440), "decisionRelevance": "supporting"}
    orchestrator = orchestrator_factory()
    # Keep the authored collection scope, but allow the source owner to review
    # the collected answer independently of market-observation quality gates.
    run = orchestrator.run(question, NewsCollectionTarget(job["symbol"], job["name"]),
        {"documentaryReviewOnly": True,
         "researchPlan": {"planId": job["taskId"], "tasks": [task], "unresolvedQuestions": [job["question"]]}},
        account_id=job["accountId"], run_id=run_id,
        request_context={"aiControlTaskId": job["taskId"], "source": "ai-control", "question": question.to_dict(),
                         "researchRequest": request})
    return research_return(run.to_dict(), run.run_id, source_task_id, request)
