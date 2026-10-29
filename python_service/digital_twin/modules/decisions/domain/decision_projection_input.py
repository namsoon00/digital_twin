"""Lossless input to the bounded decision-memory ABox projection.

The audit, prompts and research documents remain in DecisionEpisode storage.
This read model retains every field read by decision_episode_ontology_context;
outcomes are joined separately at the requested observation cutoff.
"""

from copy import deepcopy
from typing import Mapping


DECISION_PROJECTION_INPUT_VERSION = "decision-projection-input-v1"


def decision_projection_input(payload: Mapping[str, object]) -> dict:
    keys = (
        "episodeId", "accountId", "symbol", "subjectName", "question",
        "hypothesisSet", "action", "reviewLevel", "dataState", "validationState",
        "selectedHypothesisId", "hypothesisReviews", "hypothesisComparisonState",
        "hypothesisSelectionSource", "inferenceGenerationId", "unresolvedQuestions",
        "decidedAt", "status", "source",
    )
    result = {key: deepcopy(payload[key]) for key in keys if key in payload}
    facts = payload.get("factsAtDecision") or {}
    result["factsAtDecision"] = {
        "hypothesisOutcomeContract": deepcopy(facts.get("hypothesisOutcomeContract") or {})
    } if isinstance(facts, Mapping) else {}
    return result
