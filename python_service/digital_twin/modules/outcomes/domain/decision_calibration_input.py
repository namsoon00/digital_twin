"""The immutable claim fields needed to join an outcome to its decision."""

from copy import deepcopy
from typing import Mapping


DECISION_CALIBRATION_INPUT_VERSION = "decision-calibration-input-v2"


def calibration_ai_judgment(judgment):
    judgment = judgment if isinstance(judgment, Mapping) else {}
    return {"insightAssessment": deepcopy(judgment.get("insight_assessment") or judgment.get("insightAssessment") or {})}


def calibration_input(payload):
    facts = payload.get("factsAtDecision") or {}
    facts = facts if isinstance(facts, Mapping) else {}
    judgment = facts.get("aiJudgment") or {}
    return {"hypotheses": calibration_hypotheses(payload),
            "aiJudgment": calibration_ai_judgment(judgment),
            "insightAssessment": deepcopy(payload.get("insightAssessment") or {}),
            "assistantQualityObservation": deepcopy(facts.get("assistantQualityObservation") or {})}


def calibration_hypotheses(payload: Mapping[str, object]) -> list:
    hypothesis_set = payload.get("hypothesisSet")
    hypothesis_set = hypothesis_set if isinstance(hypothesis_set, dict) else {}
    hypotheses = hypothesis_set.get("hypotheses")
    if not isinstance(hypotheses, list):
        return []
    return [
        {
            "hypothesisId": str(item.get("hypothesisId") or ""),
            "claimContract": deepcopy(item.get("claimContract") or {}),
        }
        for item in hypotheses
        if isinstance(item, dict)
    ]
