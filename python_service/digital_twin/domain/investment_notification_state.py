"""User-facing state transitions derived from persisted final AI decisions."""

from __future__ import annotations

from typing import Dict, Mapping

from .investment_decision_history import previous_decision_episode_value


INVESTMENT_NOTIFICATION_STATE_VERSION = "investment-notification-state-v1"
INVESTMENT_NOTIFICATION_TRANSITION_VERSION = "investment-notification-transition-v1"

ACTION_LABELS = {
    "BUY": "매수 검토",
    "ADD": "추가매수 검토",
    "HOLD": "유지",
    "TRIM": "분할축소 검토",
    "SELL": "매도 검토",
    "AVOID": "신규 진입 회피",
}

READINESS_LABELS = {
    "ready": "판단 가능",
    "conditional": "추가 확인",
    "blocked": "판단 보류",
}

FIELD_LABELS = {
    "action": "행동",
    "reviewLevel": "확인 단계",
    "dataState": "자료 상태",
    "validationState": "검증 상태",
}

FIELD_VALUE_LABELS = {
    "reviewLevel": {
        "normal": "평소 관찰",
        "observe": "변화 관찰",
        "check": "조건 확인",
        "act": "대응 준비",
        "immediate": "즉시 재확인",
        "blocked": "판단 보류",
    },
    "dataState": {
        "sufficient": "충분",
        "partial": "일부 확인",
        "insufficient": "부족",
        "unavailable": "사용 불가",
    },
    "validationState": {
        "ready": "검증 완료",
        "conditional": "조건부",
        "blocked": "검증 보류",
    },
}


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _readiness(review_level: str, data_state: str, validation_state: str) -> str:
    if review_level == "blocked" or data_state in {"insufficient", "unavailable"} or validation_state == "blocked":
        return "blocked"
    if data_state != "sufficient" or validation_state != "ready":
        return "conditional"
    return "ready"


def _dimension_value_label(key: str, value: str, state: Mapping[str, object]) -> str:
    if key == "action":
        return _text(state.get("actionLabel")) or ACTION_LABELS.get(value, value)
    return FIELD_VALUE_LABELS.get(key, {}).get(value, value)


def investment_notification_state_from_values(
    values: Mapping[str, object],
    *,
    target_role: object = "",
) -> Dict[str, object]:
    """Normalize final-decision fields without creating investment meaning."""

    values = _mapping(values)
    action = _text(values.get("action")).upper()
    review_level = _text(values.get("reviewLevel") or values.get("review_level")).lower()
    data_state = _text(values.get("dataState") or values.get("data_state")).lower()
    validation_state = _text(values.get("validationState") or values.get("validation_state")).lower()
    readiness = _readiness(review_level, data_state, validation_state)
    role = _text(target_role).lower()
    action_label = _text(values.get("actionLabel") or values.get("action_label"))
    if not action_label:
        action_label = ACTION_LABELS.get(action, action or "조건 확인")
    if action == "HOLD":
        action_label = "관심 유지" if role == "watchlist" else "보유 유지"
    dimensions = {
        "action": action,
        "reviewLevel": review_level,
        "dataState": data_state,
        "validationState": validation_state,
    }
    code = "|".join(str(dimensions[key] or "unknown") for key in FIELD_LABELS)
    return {
        "version": INVESTMENT_NOTIFICATION_STATE_VERSION,
        "code": code,
        "action": action,
        "actionLabel": action_label,
        "reviewLevel": review_level,
        "dataState": data_state,
        "validationState": validation_state,
        "readiness": readiness,
        "readinessLabel": READINESS_LABELS[readiness],
        "label": action_label + " · " + READINESS_LABELS[readiness],
        "source": "final-ai-decision",
    }


def investment_notification_state(context: Mapping[str, object]) -> Dict[str, object]:
    context = _mapping(context)
    validated = _mapping(context.get("notificationAiValidatedResponse"))
    if not validated:
        return {}
    relation = _mapping(context.get("ontologyRelationContext"))
    envelope = _mapping(relation.get("actionEnvelope"))
    target_role = envelope.get("targetRole") or relation.get("targetRole") or context.get("targetRole")
    return investment_notification_state_from_values(validated, target_role=target_role)


def investment_notification_transition(context: Mapping[str, object]) -> Dict[str, object]:
    context = _mapping(context)
    current = investment_notification_state(context)
    previous_episode = previous_decision_episode_value(context)
    relation = _mapping(context.get("ontologyRelationContext"))
    envelope = _mapping(relation.get("actionEnvelope"))
    target_role = envelope.get("targetRole") or relation.get("targetRole") or context.get("targetRole")
    previous = (
        investment_notification_state_from_values(previous_episode, target_role=target_role)
        if previous_episode
        else {}
    )
    if not current:
        return {}
    if not previous:
        return {
            "version": INVESTMENT_NOTIFICATION_TRANSITION_VERSION,
            "kind": "initial",
            "historyAvailable": False,
            "changed": False,
            "material": False,
            "changedFields": [],
            "previousState": {},
            "currentState": current,
            "summary": "첫 최종 판단 상태를 기준선으로 확인했습니다.",
        }

    changed_fields = []
    changes = []
    for key, label in FIELD_LABELS.items():
        before = _text(previous.get(key))
        after = _text(current.get(key))
        if before and after and before != after:
            changed_fields.append(key)
            changes.append(
                label
                + " "
                + _dimension_value_label(key, before, previous)
                + " → "
                + _dimension_value_label(key, after, current)
            )
    changed = bool(changed_fields)
    if "action" in changed_fields:
        kind = "action-changed"
    elif any(key in changed_fields for key in ("dataState", "validationState")):
        kind = "readiness-changed"
    elif "reviewLevel" in changed_fields:
        kind = "review-level-changed"
    else:
        kind = "unchanged"
    summary = previous["label"] + " → " + current["label"]
    if changes:
        summary += " (" + ", ".join(changes) + ")"
    return {
        "version": INVESTMENT_NOTIFICATION_TRANSITION_VERSION,
        "kind": kind,
        "historyAvailable": True,
        "changed": changed,
        "material": changed,
        "changedFields": changed_fields,
        "changedFieldLabels": [FIELD_LABELS[key] for key in changed_fields],
        "previousState": previous,
        "currentState": current,
        "previousEpisodeId": _text(previous_episode.get("episodeId")),
        "previousDecidedAt": _text(previous_episode.get("decidedAt")),
        "summary": summary,
    }


def context_with_investment_notification_state(context: Mapping[str, object]) -> Dict[str, object]:
    enriched = _mapping(context)
    state = investment_notification_state(enriched)
    transition = investment_notification_transition(enriched)
    if state:
        enriched["investmentNotificationState"] = state
    if transition:
        enriched["investmentNotificationTransition"] = transition
    return enriched


def investment_notification_transition_line(context: Mapping[str, object]) -> str:
    transition = _mapping(_mapping(context).get("investmentNotificationTransition"))
    if not transition.get("changed"):
        return ""
    return "판단 상태 변경: " + _text(transition.get("summary"))
