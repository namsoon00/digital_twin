"""Canonical customer explanation for why a notification was delivered now."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping

from .message_types import INVESTMENT_INSIGHT
from .notification_ai_delivery import first_holding_review_delivery_is_authorized


CUSTOMER_DELIVERY_EXPLANATION_VERSION = "customer-delivery-explanation-v1"
VALID_CAUSE_CATEGORIES = {
    "verification",
    "replay",
    "action-transition",
    "readiness-transition",
    "material-evidence",
    "threshold-crossing",
    "scheduled-repeat",
    "initial-actionable",
}
ACTION_LABELS = {
    "BUY": "매수 검토",
    "ADD": "추가매수 검토",
    "HOLD": "보유 유지",
    "WATCH": "관심 유지",
    "NO_ACTION": "행동 변경 없음",
    "REDUCE": "분할축소 검토",
    "PARTIAL_SELL": "분할매도 검토",
    "SELL": "매도 검토",
    "AVOID": "신규 진입 회피",
}
ACTIONABLE_ACTIONS = {"BUY", "ADD", "REDUCE", "PARTIAL_SELL", "SELL"}
DECISION_ACTIONS = ACTIONABLE_ACTIONS | {"HOLD", "WATCH", "AVOID"}
FORBIDDEN_CUSTOMER_REASON_MARKERS = (
    "meaningful graph relation change",
    "graph context drift",
    "typedb 관계 분석 규칙 관계 성립",
    "has_inference_trace",
    "has_inferred_risk",
)


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _items(value: object) -> List[object]:
    return list(value) if isinstance(value, (list, tuple, set)) else []


def _unique(values: Iterable[object], limit: int = 0) -> List[str]:
    rows: List[str] = []
    for value in values or []:
        text = _text(value)
        if text and text not in rows:
            rows.append(text)
            if limit > 0 and len(rows) >= limit:
                break
    return rows


def _action_label(value: object, explicit: object = "") -> str:
    label = _text(explicit)
    if label:
        return label.split(" · ", 1)[0]
    action = _text(value).upper()
    return ACTION_LABELS.get(action, action)


@dataclass(frozen=True)
class CustomerDeliveryCause:
    code: str
    category: str
    summary: str
    label: str = "발송 계기"
    previous_value: object = ""
    current_value: object = ""
    observed_at: str = ""
    source_references: List[str] = field(default_factory=list)
    basis: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = {
            "code": self.code,
            "category": self.category,
            "label": self.label,
            "summary": self.summary,
            "previousValue": self.previous_value,
            "currentValue": self.current_value,
            "observedAt": self.observed_at,
            "sourceReferences": list(self.source_references or []),
            "basis": self.basis,
        }
        return {
            key: value
            for key, value in payload.items()
            if value not in (None, "", [], {})
        }


def _cause(
    code: str,
    category: str,
    summary: str,
    *,
    label: str = "발송 계기",
    previous_value: object = "",
    current_value: object = "",
    observed_at: str = "",
    source_references: Iterable[object] = (),
    basis: str = "",
) -> CustomerDeliveryCause:
    return CustomerDeliveryCause(
        code=code,
        category=category,
        summary=_text(summary),
        label=_text(label) or "발송 계기",
        previous_value=previous_value,
        current_value=current_value,
        observed_at=_text(observed_at),
        source_references=_unique(source_references, 12),
        basis=_text(basis),
    )


def _material_evidence_cause(context: Mapping[str, object]) -> CustomerDeliveryCause | None:
    for item in _items(context.get("deliveryTriggerLedger")):
        row = _mapping(item)
        if _text(row.get("kind")).lower() != "material-evidence":
            continue
        if _text(row.get("status")).lower() not in {"matched", "released", "eligible"}:
            continue
        title = _text(row.get("sourceTitle"))
        references = _unique([
            *_items(row.get("evidenceIds")),
            row.get("sourceUrl"),
            row.get("triggerId"),
        ])
        if not title and not references:
            continue
        provider = _text(row.get("sourceProvider"))
        summary = "새 판단 자료가 확인됐습니다"
        if title:
            summary += ": " + title
        if provider:
            summary += " (" + provider + ")"
        return _cause(
            "material-evidence",
            "material-evidence",
            summary,
            label="새 판단 자료",
            current_value=title,
            observed_at=row.get("sourceObservedAt"),
            source_references=references,
        )
    return None


def _transition_values(context: Mapping[str, object]) -> Dict[str, object]:
    transition = _mapping(context.get("investmentNotificationTransition"))
    ai_transition = _mapping(context.get("aiDecisionTransition"))
    previous_state = _mapping(transition.get("previousState"))
    current_state = _mapping(transition.get("currentState"))
    validated = _mapping(context.get("notificationAiValidatedResponse"))
    previous_action = _text(previous_state.get("action") or ai_transition.get("previousAction")).upper()
    current_action = _text(
        current_state.get("action")
        or ai_transition.get("currentAction")
        or validated.get("action")
    ).upper()
    return {
        "contractPresent": bool(transition),
        "transition": transition,
        "aiTransition": ai_transition,
        "changed": bool(transition.get("changed")),
        "material": bool(transition.get("material")),
        "changedFields": _unique(transition.get("changedFields") or []),
        "previousAction": previous_action,
        "currentAction": current_action,
        "previousLabel": _action_label(previous_action, previous_state.get("actionLabel")),
        "currentLabel": _action_label(current_action, current_state.get("actionLabel")),
        "previousStateLabel": _text(previous_state.get("label")),
        "currentStateLabel": _text(current_state.get("label")),
        "historyAvailable": bool(transition.get("historyAvailable")),
        "summary": _text(transition.get("summary")),
    }


def _relation_transition_cause(context: Mapping[str, object]) -> CustomerDeliveryCause | None:
    relation_diff = _mapping(context.get("ontologyRelationDiff"))
    transition = _mapping(relation_diff.get("decisionTransition"))
    if not (transition.get("changed") and transition.get("material")):
        return None
    previous_action = _text(transition.get("previousAction")).upper()
    current_action = _text(transition.get("currentAction")).upper()
    previous_readiness = _text(transition.get("previousDataReadiness")).lower()
    current_readiness = _text(transition.get("currentDataReadiness")).lower()
    readiness_labels = {
        "ready": "판단 가능",
        "partial": "일부 자료만 확인",
        "insufficient": "판단 자료 부족",
        "unavailable": "판단 자료 없음",
        "blocked": "판단 보류",
    }
    references = [relation_diff.get("previousFingerprint"), relation_diff.get("currentFingerprint")]
    if (
        previous_action in DECISION_ACTIONS
        and current_action in DECISION_ACTIONS
        and previous_action != current_action
    ):
        return _cause(
            "relation-action-envelope-transition",
            "readiness-transition",
            "판단 가능 범위가 " + _action_label(previous_action) + "에서 "
            + _action_label(current_action) + "로 바뀌어 다시 확인합니다.",
            label="판단 가능 범위 변화",
            previous_value=previous_action,
            current_value=current_action,
            source_references=references,
            basis="relation-decision-transition",
        )
    if previous_readiness and current_readiness and previous_readiness != current_readiness:
        return _cause(
            "relation-data-readiness-transition",
            "readiness-transition",
            "판단 자료 상태가 " + readiness_labels.get(previous_readiness, previous_readiness)
            + "에서 " + readiness_labels.get(current_readiness, current_readiness)
            + "으로 바뀌어 다시 점검합니다.",
            label="자료 상태 변화",
            previous_value=previous_readiness,
            current_value=current_readiness,
            source_references=references,
            basis="relation-decision-transition",
        )
    if transition:
        return _cause(
            "relation-decision-transition",
            "readiness-transition",
            "사용자 판단 조건이 바뀌어 현재 상태를 다시 확인합니다.",
            label="판단 조건 변화",
            source_references=references,
            basis="relation-decision-transition",
        )
    return None


def _normal_delivery_cause(context: Mapping[str, object]) -> CustomerDeliveryCause | None:
    values = _transition_values(context)
    previous_action = str(values["previousAction"] or "")
    current_action = str(values["currentAction"] or "")
    action_changed = bool(
        values["changed"]
        and previous_action in DECISION_ACTIONS
        and current_action in DECISION_ACTIONS
        and previous_action != current_action
    )
    if action_changed:
        return _cause(
            "final-action-transition",
            "action-transition",
            str(values["previousLabel"] or "이전 행동")
            + "에서 " + str(values["currentLabel"] or "현재 행동")
            + "로 최종 행동이 바뀌었습니다.",
            label="행동 변화",
            previous_value=previous_action,
            current_value=current_action,
        )
    if values["changed"] and values["material"]:
        previous_label = str(values["previousStateLabel"] or "이전 판단 상태")
        current_label = str(values["currentStateLabel"] or "현재 판단 상태")
        return _cause(
            "decision-readiness-transition",
            "readiness-transition",
            previous_label + "에서 " + current_label + "로 판단 상태가 바뀌었습니다.",
            label="판단 상태 변화",
            previous_value=previous_label,
            current_value=current_label,
        )
    evidence = _material_evidence_cause(context)
    if evidence is not None:
        return evidence
    relation_transition = _relation_transition_cause(context)
    if relation_transition is not None:
        return relation_transition
    cooldown_decision = _text(context.get("cooldownDecision")).lower()
    if cooldown_decision == "typedb-profit-loss-change":
        return _cause(
            "profit-loss-threshold-crossing",
            "threshold-crossing",
            "손익 관리 조건이 새로 성립해 현재 판단을 다시 확인합니다.",
            label="손익 조건 변화",
            current_value=context.get("profitLossRate"),
        )
    if cooldown_decision == "scheduled-summary":
        minutes = context.get("cooldownMinutes")
        summary = "같은 판단 상태를 정기적으로 다시 확인할 시점입니다."
        if minutes not in (None, "", 0, "0"):
            summary = "마지막 알림 후 " + _text(minutes) + "분이 지나 같은 판단 상태를 다시 확인합니다."
        return _cause(
            "scheduled-state-summary",
            "scheduled-repeat",
            summary,
            label="정기 재확인",
            current_value=context.get("cooldownLastSentAgeMinutes"),
        )
    if first_holding_review_delivery_is_authorized(context):
        relation = _mapping(context.get("ontologyRelationContext"))
        subject = _mapping(relation.get("subject"))
        envelope = _mapping(relation.get("actionEnvelope"))
        name = _text(subject.get("name") or subject.get("symbol") or context.get("symbol"))
        summary = "처음으로 조건 확인이 필요한 보유 판단이 완성돼 현재 보유 이유를 점검합니다."
        if name:
            summary = name + "에서 " + summary
        return _cause(
            "initial-holding-review",
            "initial-actionable",
            summary,
            label="첫 보유 점검",
            current_value=current_action or "HOLD",
            observed_at=context.get("reasoningSourceObservedAt"),
            source_references=[
                envelope.get("selectedRuleId"),
                relation.get("inferenceGenerationId"),
                relation.get("sourceAboxSnapshotId"),
            ],
            basis="first-holding-review",
        )
    if not values["historyAvailable"] and current_action in ACTIONABLE_ACTIONS:
        return _cause(
            "initial-actionable-decision",
            "initial-actionable",
            "처음으로 " + str(values["currentLabel"] or current_action) + " 판단이 가능해졌습니다.",
            label="첫 실행 판단",
            current_value=current_action,
        )
    return None


def validate_customer_delivery_explanation(
    explanation: Mapping[str, object],
    *,
    message_type: object,
    source_event_name: object,
    context: Mapping[str, object],
) -> Dict[str, object]:
    values = _mapping(explanation)
    primary = _mapping(values.get("primaryCause"))
    category = _text(primary.get("category")).lower()
    summary = _text(primary.get("summary"))
    purpose = _text(values.get("purpose")).lower()
    source_name = _text(source_event_name).lower()
    transition_values = _transition_values(context)
    errors: List[str] = []
    checks: List[str] = []
    if _text(message_type) != INVESTMENT_INSIGHT:
        return {"state": "not-required", "errors": [], "checks": ["investmentInsight 전용 계약"]}
    if not primary:
        errors.append("primary-cause-missing")
    if category not in VALID_CAUSE_CATEGORIES:
        errors.append("primary-cause-category-invalid")
    if not summary:
        errors.append("primary-cause-summary-missing")
    replay = bool(context.get("notificationReplay")) or source_name == "notification.replay_requested"
    verification = source_name.startswith("notification.verification") or bool(context.get("testDispatch"))
    if replay and category != "replay":
        errors.append("replay-cause-mismatch")
    if replay and purpose != "replay":
        errors.append("replay-purpose-mismatch")
    if verification and not replay and category != "verification":
        errors.append("verification-cause-mismatch")
    if verification and not replay and purpose != "verification":
        errors.append("verification-purpose-mismatch")
    if category == "action-transition":
        if not transition_values["changed"]:
            errors.append("action-transition-without-change")
        if (
            not transition_values["previousAction"]
            or not transition_values["currentAction"]
            or transition_values["previousAction"] == transition_values["currentAction"]
        ):
            errors.append("action-transition-values-invalid")
    if category == "readiness-transition":
        relation_transition = _mapping(_mapping(context.get("ontologyRelationDiff")).get("decisionTransition"))
        relation_basis_valid = bool(
            _text(primary.get("basis")) == "relation-decision-transition"
            and relation_transition.get("changed")
            and relation_transition.get("material")
        )
        if not (
            (transition_values["changed"] and transition_values["material"])
            or relation_basis_valid
        ):
            errors.append("readiness-transition-not-material")
    if category == "material-evidence" and not (
        primary.get("currentValue") or primary.get("sourceReferences")
    ):
        errors.append("material-evidence-source-missing")
    customer_summaries = [summary]
    customer_summaries.extend(
        _text(_mapping(item).get("summary"))
        for item in _items(values.get("supportingCauses"))
    )
    if any(
        marker in customer_summary.lower()
        for customer_summary in customer_summaries
        for marker in FORBIDDEN_CUSTOMER_REASON_MARKERS
    ):
        errors.append("internal-language-exposed")
    if len(_items(values.get("supportingCauses"))) > 2:
        errors.append("supporting-cause-limit-exceeded")
    checks.extend([
        "source-event-classified",
        "single-primary-cause",
        "transition-consistency-checked",
        "customer-language-checked",
    ])
    return {
        "state": "invalid" if errors else "valid",
        "errors": _unique(errors),
        "checks": checks,
    }


def build_customer_delivery_explanation(
    *,
    message_type: object,
    source_event_name: object,
    source_event_id: object = "",
    context: Mapping[str, object] = None,
) -> Dict[str, object]:
    values = _mapping(context)
    source_name = _text(source_event_name or values.get("sourceEventName"))
    replay = bool(values.get("notificationReplay")) or source_name.lower() == "notification.replay_requested"
    verification = source_name.lower().startswith("notification.verification") or bool(values.get("testDispatch"))
    supporting: List[CustomerDeliveryCause] = []
    if replay:
        original = _text(values.get("replaySourceNotificationNumber") or values.get("replaySourceJobId"))
        primary = _cause(
            "notification-replay",
            "replay",
            "사용자 요청으로 과거 알림" + ((" " + original) if original else "") + "을 원문 그대로 다시 보냈습니다.",
            label="재발송",
            current_value=original,
            source_references=[values.get("replaySourceJobId")],
        )
        purpose = "replay"
    elif verification:
        primary = _cause(
            "notification-verification",
            "verification",
            "실제 저장 데이터로 AI 판단, 규칙 근거와 알림 전달 경로를 확인하기 위한 검증 발송입니다.",
            label="검증 발송",
            source_references=[source_event_id],
        )
        supporting.append(_cause(
            "verification-no-new-signal",
            "verification",
            "새 투자 신호가 아니라 기존 판단을 재현한 메시지입니다.",
            label="새 투자 신호",
        ))
        purpose = "verification"
    else:
        primary = _normal_delivery_cause(values)
        purpose = "investment-change"
        if (
            primary
            and primary.category in {"action-transition", "readiness-transition"}
            and primary.basis != "relation-decision-transition"
        ):
            supporting.append(_cause(
                "material-transition-delivery",
                primary.category,
                "사용자에게 표시되는 최종 판단 상태가 바뀌어 발송 조건을 통과했습니다.",
                label="재알림 기준",
            ))
    payload = {
        "version": CUSTOMER_DELIVERY_EXPLANATION_VERSION,
        "purpose": purpose,
        "sourceEvent": {
            "id": _text(source_event_id),
            "name": source_name,
        },
        "primaryCause": primary.to_dict() if primary is not None else {},
        "supportingCauses": [item.to_dict() for item in supporting],
    }
    payload["validation"] = validate_customer_delivery_explanation(
        payload,
        message_type=message_type,
        source_event_name=source_name,
        context=values,
    )
    return payload


def customer_delivery_explanation_lines(context: Mapping[str, object]) -> List[str]:
    explanation = _mapping(context.get("customerDeliveryExplanation"))
    validation = _mapping(explanation.get("validation"))
    if validation.get("state") != "valid":
        return []
    causes = [explanation.get("primaryCause"), *_items(explanation.get("supportingCauses"))]
    rows: List[str] = []
    for item in causes:
        cause = _mapping(item)
        summary = _text(cause.get("summary"))
        label = _text(cause.get("label"))
        text = (label + ": " if label and label not in summary else "") + summary
        if text and text not in rows:
            rows.append(text)
    return rows[:3]
