"""Finite follow-up opportunities for verified gaps, independent of model wording."""
from .planning import identity
from .question_evidence import VERSION as EVIDENCE_VERSION

VERSION = "question-recovery-v1"


def gap_key(case):
    progress, contract = case.get("evidenceProgress") or {}, case.get("questionContract") or {}
    from .question_assessment import disputed_source_answer
    disputed = disputed_source_answer(case)
    if disputed:
        progress = case["questionAssessment"]
    if not disputed and (progress.get("version") != EVIDENCE_VERSION or progress.get("status") != "waiting"):
        return ""
    from .research_work import source_answer_complete
    if (not progress.get("executionInputId") or not progress.get("missingRequirements")
            or not all(contract.get(key) for key in ("target", "period", "answerCriterion"))
            or source_answer_complete(case)):
        return ""
    from .research_work import contract_key
    return identity(VERSION, case["caseId"], contract_key(contract))


def gap_opportunity(case, action):
    key = gap_key(case)
    used = (case.get("researchRepeat") or {}).get("gapRecovery") or {}
    if not key or used.get(action):
        return ""  # One follow-up per original case; rewording/contract edits cannot reset it.
    if action == "scheduled":
        plan = case.get("researchPlan") or {}
        if plan.get("gapRecoveryKey") != key or plan.get("action") != "research":
            return ""
    return key


def answer_review_key(case):
    answer = case.get("answerResolution") or {}
    if answer.get("status") != "answered" or not answer.get("assessmentFingerprint"):
        return ""
    return identity(VERSION, case["caseId"], answer["assessmentFingerprint"], case.get("questionContract", {}))
