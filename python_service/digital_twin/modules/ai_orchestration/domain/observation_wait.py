"""Operational disposition and retry identity, separate from investment judgement."""
from datetime import datetime, timedelta, timezone
from digital_twin.modules.reasoning.contracts import content_hash
from .working_retrieval import POLICY


def retry_key(packet, research):
    from .research_review import VERSION as research_review_version
    from .research_evidence_use import ASSESSMENT_SCOPE_VERSION, QUESTION_DOCUMENT_READ_VERSION
    # Source-owned inventory hashes include changes to facts excluded from the
    # initial packet. Self-authored review prose and polling times are not new evidence.
    work = [{key: row[key] for key in ("kind", "caseId", "question", "capability", "status", "thesisId",
             "checkpoints", "researchRequest", "lastResearch", "runId", "developmentProgress") if key in row}
            for row in research]
    from .question_evidence import source_identity
    for item, row in zip(work, research):
        if row.get("kind") == "brain-case" and row.get("capability") == "research":
            # Source plans, failed attempts and paraphrased internal notes must
            # not wake an observation on the same underlying question/data.
            item.pop("researchRequest", None)
            item.pop("lastResearch", None)
            item["status"] = row.get("status") if row.get("status") in {"answered", "dismissed"} else "active"
            item["questionContract"] = row.get("questionContract", {})
            item["sourceAnswer"] = source_identity(row)
    from digital_twin.modules.research_knowledge.contracts import knowledge_context_identity
    from .scoped_retrieval import AUTHOR_SCOPE_POLICY
    return content_hash({"policy": AUTHOR_SCOPE_POLICY if packet.get("researchKnowledge") else POLICY, "researchReviewPolicy": research_review_version,
        "knowledgePolicy": knowledge_context_identity(packet["researchKnowledge"]) if packet.get("researchKnowledge") else "",
        "researchAssessmentScopePolicy": ASSESSMENT_SCOPE_VERSION,
        "questionDocumentReadPolicy": QUESTION_DOCUMENT_READ_VERSION,
        "account": packet.get("accountId"), "symbol": packet.get("symbol"),
        "world": packet.get("worldId"), "inventories": {k: v.get("inventoryHash") for k, v in packet.get("coverage", {}).items()},
        "quoteStates": [{k: v.get(k) for k in ("evidenceId", "status", "referenceState", "maxAgeMinutes")}
                        for v in packet.get("quoteAssessment", {}).get("quotes", [])],
        "work": sorted(work, key=content_hash)})


def disposition(packet, key, status, requirements, now=None):
    now = now or datetime.now(timezone.utc)
    return {"version": POLICY, "status": status, "retryKey": key,
        "requirements": list(requirements), "retryAt": (now+timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
        "resumeOn": ["changed-source-evidence", "changed-question-or-research-result", "changed-runtime-policy"],
        "sourceSnapshotId": packet.get("sourceSnapshotId"), "authority": "operational-only"}


def unchanged_wait(previous, key, now=None):
    if not isinstance(previous, dict) or previous.get("version") != POLICY or previous.get("retryKey") != key:
        return False
    # retryAt schedules a cheap source check, not permission to spend another
    # model call on identical failed input. Genuine evidence/work changes alter
    # key; verified follow-up transitions are admitted separately by the caller.
    return previous.get("status") in {"data-wait", "retrieval-blocked", "output-invalid",
                                      "research-review-required", "research-reviewed", "research-only"}
