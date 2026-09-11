"""Compact decision-memory contracts for notification AI continuity."""

from __future__ import annotations

from typing import Dict, Mapping

from digital_twin.modules.decisions.domain.investment_decision_actionability import persisted_decision_authorization


INVESTMENT_ACTIONS = {"BUY", "ADD", "HOLD", "TRIM", "SELL", "AVOID"}
EXECUTABLE_INVESTMENT_ACTIONS = {"BUY", "ADD", "TRIM", "SELL"}


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _value(payload: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return ""


def compact_decision_episode_memory(value: object) -> Dict[str, object]:
    """Keep only the prior final-decision fields required by the next review."""

    if hasattr(value, "to_dict") and callable(value.to_dict):
        payload = _mapping(value.to_dict())
    else:
        payload = _mapping(value)
    action = _text(_value(payload, "action")).upper()
    if action not in INVESTMENT_ACTIONS:
        return {}
    actionability = _mapping(
        payload.get("decisionActionability") or payload.get("decision_actionability")
    )
    assurance = _mapping(
        payload.get("decisionAssurance") or payload.get("decision_assurance")
    )
    source = _text(_value(payload, "source")).lower()
    if action in EXECUTABLE_INVESTMENT_ACTIONS:
        authorization = persisted_decision_authorization(payload)
        # Persisted production decisions must carry the fail-closed marker.
        # Source-less payloads remain accepted for legacy callers and tests.
        if source and not bool(authorization.get("authorized")):
            return {}
    return {
        "episodeId": _text(_value(payload, "episodeId", "episode_id")),
        "accountId": _text(_value(payload, "accountId", "account_id")),
        "symbol": _text(_value(payload, "symbol")).upper(),
        "subjectName": _text(_value(payload, "subjectName", "subject_name")),
        "action": action,
        "reviewLevel": _text(_value(payload, "reviewLevel", "review_level")),
        "dataState": _text(_value(payload, "dataState", "data_state")),
        "validationState": _text(_value(payload, "validationState", "validation_state")),
        "decisionReadiness": _text(_value(payload, "decisionReadiness", "decision_readiness")),
        "decisionActionabilityStatus": _text(actionability.get("status")),
        "executionEligibility": _text(assurance.get("executionEligibility")),
        "inferenceGenerationId": _text(_value(payload, "inferenceGenerationId", "inference_generation_id")),
        "selectedHypothesisId": _text(_value(payload, "selectedHypothesisId", "selected_hypothesis_id")),
        "decisionSummary": _text(_value(payload, "decisionSummary", "decision_summary")),
        "decidedAt": _text(_value(payload, "decidedAt", "decided_at")),
        "status": _text(_value(payload, "status")),
        "source": _text(_value(payload, "source")),
    }


def previous_decision_episode_value(context: Mapping[str, object]) -> Dict[str, object]:
    context = _mapping(context)
    direct = compact_decision_episode_memory(context.get("previousInvestmentDecisionEpisode"))
    if direct:
        return direct
    history = _mapping(context.get("investmentDecisionHistory"))
    return compact_decision_episode_memory(history.get("previousDecisionEpisode"))


def ai_decision_transition(context: Mapping[str, object], current_action: object) -> Dict[str, object]:
    previous = previous_decision_episode_value(context)
    current = _text(current_action).upper()
    if current not in INVESTMENT_ACTIONS:
        current = ""
    previous_action = _text(previous.get("action")).upper()
    if not previous:
        return {
            "version": "ai-decision-transition-v1",
            "kind": "initial",
            "historyAvailable": False,
            "previousAction": "",
            "currentAction": current,
            "material": False,
        }
    changed = bool(previous_action and current and previous_action != current)
    return {
        "version": "ai-decision-transition-v1",
        "kind": "action-changed" if changed else "unchanged",
        "historyAvailable": True,
        "previousAction": previous_action,
        "currentAction": current,
        "material": changed,
        "previousEpisodeId": _text(previous.get("episodeId")),
        "previousDecidedAt": _text(previous.get("decidedAt")),
        "previousInferenceGenerationId": _text(previous.get("inferenceGenerationId")),
    }


def context_with_ai_decision_transition(
    context: Mapping[str, object],
    current_action: object,
) -> Dict[str, object]:
    enriched = _mapping(context)
    enriched["aiDecisionTransition"] = ai_decision_transition(enriched, current_action)
    return enriched
