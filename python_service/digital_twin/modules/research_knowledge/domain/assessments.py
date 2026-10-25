"""Use source-owner evaluations without promoting them to causal validation."""
from copy import deepcopy
from .knowledge import digest


def assessment_for_claim(claim, business_memories):
    source = next((row for row in business_memories if row.get("caseId") == claim.get("businessThesisId")), None)
    if source is None:
        return deepcopy(claim.get("assessment", {"status": "awaiting-observation", "qualification": "not-empirically-qualified"}))
    observations = deepcopy(source.get("observations", []))
    from digital_twin.modules.outcomes.contracts import RESEARCH_EXPECTATION_TERMINAL
    observed = [row for row in observations if row.get("status") in {"direction-observed", "direction-not-observed"} | RESEARCH_EXPECTATION_TERMINAL]
    failed = [row for row in observed if row["status"] in {"direction-not-observed", "below-expected-range", "above-expected-range"}]
    status = "checkpoint-not-observed" if failed else "checkpoint-observed" if observed and len(observed) == len(observations) else "awaiting-observation"
    assessment = {"status": status, "sourceOwner": "ai_orchestration.business-research", "sourceCaseId": source["caseId"],
                  "observations": observations, "qualification": "metric-observation-only",
                  "causalValidation": "not-established", "predictionQualification": "not-empirically-qualified"}
    assessment["assessmentId"] = digest([source["caseId"], observations])
    if failed:
        assessment["methodProposal"] = {
            "id": digest([claim["claimId"], assessment["assessmentId"], "recheck-mechanism"]),
            "sourceAssessmentId": assessment["assessmentId"], "frameworkId": claim["frameworkId"],
            "proposal": ("실제 지표가 사전에 정한 예상 범위를 벗어났습니다. 수치 오차와 가정·기간·범위·대안 설명을 다시 조사하세요."
                         if any(row.get("status") in RESEARCH_EXPECTATION_TERMINAL for row in failed) else
                         "사전에 정한 지표 방향이 확인되지 않았습니다. 원래 가정과 대안 설명, 비교 기간·범위의 일치를 다시 조사하세요."),
            "applicability": {"symbol": claim["symbol"], "purpose": claim["purpose"], "claimId": claim["claimId"]},
            "status": "proposed", "authority": "research-method-proposal-only", "adopted": False}
    return assessment
