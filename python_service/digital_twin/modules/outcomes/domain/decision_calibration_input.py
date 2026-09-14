"""The immutable claim fields needed to join an outcome to its decision."""

from copy import deepcopy
from typing import Mapping


DECISION_CALIBRATION_INPUT_VERSION = "decision-calibration-input-v1"


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
