"""Bounded memory contract connecting one investment decision to the next.

The contract contains observations only.  A portfolio balance change is not
treated as proof that a user followed an alert, and an absent change is not
treated as an intentional HOLD decision.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Tuple

from digital_twin.modules.decisions.domain.investment_decision_history import compact_decision_episode_memory


DECISION_CONTINUITY_PACKET_VERSION = "decision-continuity-packet-v2"


def _mapping(value: object) -> Dict[str, object]:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return dict(value.to_dict() or {})
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[:max(1, int(limit or 1))]


def _rows(values: Iterable[object], fields: Tuple[str, ...], limit: int) -> Tuple[Dict[str, object], ...]:
    result = []
    for value in values or []:
        source = _mapping(value)
        row = {
            key: source.get(key)
            for key in fields
            if source.get(key) not in (None, "", [], {})
        }
        for key, item in list(row.items()):
            if isinstance(item, str):
                row[key] = _text(item)
        if row:
            result.append(row)
        if len(result) >= max(1, int(limit or 1)):
            break
    return tuple(result)


def _feedback(value: object, keys: Tuple[str, ...], limit: int = 3) -> Dict[str, object]:
    source = _mapping(value)
    return {
        key: list(_rows(source.get(key) or [], (
            "planId", "actionPlanId", "executionEpisodeId", "fillId", "reviewId",
            "attributionId", "action", "status", "decision", "side", "quantity",
            "price", "observedAt", "reviewedAt", "executedAt", "activeReturnPct",
            "instrumentReturnPct", "marketReturnPct", "realizedProfitLoss",
            "selectedHypothesisStatus", "evidenceStillValid", "policyCompliant",
            "executionCompliant",
        ), limit))
        for key in keys
        if source.get(key)
    }


def _material_fingerprint(payload: Mapping[str, object]) -> str:
    material = {
        key: value
        for key, value in dict(payload or {}).items()
        if key not in {"packetId", "capturedAt", "materialFingerprint"}
    }
    canonical = json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _outcome_rows(values: Iterable[object]) -> Tuple[Dict[str, object], ...]:
    rows = []
    for value in values or []:
        source = _mapping(value)
        payload = _mapping(source.get("payload"))
        rows.append({**payload, **source})
    return _rows(rows, (
        "outcomeId", "episodeId", "observedAt", "price", "profitLossRate",
        "priceChangeFromDecisionPct", "selectedHypothesisStatus", "contradictedEvidenceIds",
        "calibrationEligibility", "missingRequiredMetricIds", "missingObservationDomains",
        "benchmarkReturnPct", "excessReturnPct", "horizonMinutes", "targetAt",
        "contractFingerprint", "marketIndependenceKey", "accountIndependenceKey",
    ), 6)


def decision_review_summary(packet: Mapping[str, object]) -> Dict[str, object]:
    """Explain recorded checks, never infer an investment action or missing result."""
    previous = _mapping(packet.get("previousDecision"))
    hypothesis = _mapping(packet.get("selectedHypothesis"))
    conditions = [_mapping(item) for item in packet.get("followUpConditions") or []]
    changes = [
        {"conditionId": item.get("conditionId"), "label": _text(item.get("label") or "이전 판단의 확인 조건"),
         "status": item.get("status"), "observedAt": item.get("transitionAt")}
        for item in conditions
        if item.get("transitionVerified") is True and item.get("transitionAt")
        and (item.get("status") == "expired" or (
            item.get("status") in {"satisfied", "invalidated"}
            and item.get("previousMatched") is False and item.get("currentMatched") is True
        ))
    ][:4]
    outcomes = []
    for row in packet.get("observedOutcomes") or []:
        eligibility = str(row.get("calibrationEligibility") or "")
        state = "evaluated" if eligibility == "eligible" else "data-gap" if eligibility in {
            "excluded-contract-data-gap", "excluded-criterion-data-gap"
        } else "excluded"
        verdict = {
            "supported": "관측 결과가 가설을 지지했습니다.",
            "directionally-corroborated": "예상한 가격 방향과 관측 결과가 일치했습니다.",
            "weakened": "관측 결과에서 가설의 근거가 약해졌습니다.",
            "rejected": "미리 정한 반증 조건이 확인됐습니다.",
            "invalidated": "미리 정한 무효화 조건이 확인됐습니다.",
            "directionally-contradicted": "관측 가격이 예상 방향과 반대로 움직였습니다.",
        }.get(str(row.get("selectedHypothesisStatus") or ""), "관측은 완료했지만 가설의 성립 여부는 확정되지 않았습니다.")
        explanation = verdict if state == "evaluated" else (
            "필수 자료가 부족해 가설의 성공·실패 판정을 보류했습니다." if state == "data-gap"
            else "관측 시점 또는 평가 계약을 확인하지 못해 성과 평가에서 제외했습니다."
        )
        outcomes.append({
            **{key: row.get(key) for key in (
                "outcomeId", "observedAt", "horizonMinutes", "priceChangeFromDecisionPct",
                "benchmarkReturnPct", "excessReturnPct", "calibrationEligibility",
                "missingRequiredMetricIds", "missingObservationDomains",
            ) if row.get(key) is not None},
            "state": state, "explanation": explanation,
        })
    states = {row["state"] for row in outcomes}
    state = ("partial" if len(states) > 1 else next(iter(states))) if states else "pending" if previous else "not-recorded"
    return {
        "state": state,
        "previousSummary": _text(previous.get("decisionSummary")),
        "previousAction": previous.get("action") or "",
        "previousDecidedAt": previous.get("decidedAt") or "",
        "claim": _text(hypothesis.get("claim")),
        "verifiedChanges": changes,
        "outcomes": outcomes,
        "nextChecks": [_text(item.get("label") or "이전 판단의 확인 조건") for item in conditions if item.get("status") == "pending"][:4],
        "interpretation": "자료 부족은 가설 실패가 아니며, 관측 수익률은 실제 매매 수익을 뜻하지 않습니다.",
    }


@dataclass(frozen=True)
class DecisionContinuityPacket:
    account_id: str
    symbol: str
    captured_at: str
    previous_decision: Mapping[str, object] = field(default_factory=dict)
    selected_hypothesis: Mapping[str, object] = field(default_factory=dict)
    follow_up_conditions: Tuple[Mapping[str, object], ...] = field(default_factory=tuple)
    unsupported_follow_ups: Tuple[Mapping[str, object], ...] = field(default_factory=tuple)
    observed_outcomes: Tuple[Mapping[str, object], ...] = field(default_factory=tuple)
    action_observations: Tuple[Mapping[str, object], ...] = field(default_factory=tuple)
    current_position: Mapping[str, object] = field(default_factory=dict)
    execution_feedback: Mapping[str, object] = field(default_factory=dict)
    lifecycle_feedback: Mapping[str, object] = field(default_factory=dict)
    source_status: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        previous = compact_decision_episode_memory(self.previous_decision)
        statuses = dict(self.source_status or {})
        errors = sorted(key for key, value in statuses.items() if str(value).lower() == "error")
        has_previous = bool(previous)
        action_rows = [dict(item) for item in self.action_observations]
        outcome_rows = [dict(item) for item in self.observed_outcomes]
        follow_up_rows = [dict(item) for item in self.follow_up_conditions]
        verified_transitions = [
            item for item in follow_up_rows
            if bool(item.get("transitionVerified"))
            and item.get("status") in {"satisfied", "invalidated", "expired"}
        ]
        execution_feedback = dict(self.execution_feedback or {})
        lifecycle_feedback = dict(self.lifecycle_feedback or {})
        payload = {
            "contractVersion": DECISION_CONTINUITY_PACKET_VERSION,
            "accountId": _text(self.account_id, 120),
            "symbol": _text(self.symbol, 64).upper(),
            "capturedAt": _text(self.captured_at, 64),
            "status": (
                "partial" if errors else "available" if has_previous else "no-prior-decision"
            ),
            "previousDecision": previous,
            "selectedHypothesis": dict(self.selected_hypothesis or {}),
            "followUpConditions": follow_up_rows,
            "unsupportedFollowUps": [dict(item) for item in self.unsupported_follow_ups],
            "observedOutcomes": outcome_rows,
            "actionObservations": action_rows,
            "currentPosition": dict(self.current_position or {}),
            "executionFeedback": execution_feedback,
            "lifecycleFeedback": lifecycle_feedback,
            "observationState": {
                "userAction": "observed" if action_rows else "not-observed" if has_previous else "not-applicable",
                "outcome": "observed" if outcome_rows else "pending" if has_previous else "not-applicable",
                "followUp": "tracked" if follow_up_rows else "not-defined" if has_previous else "not-applicable",
                "noActionMeansHold": False,
                "causalityClaimed": False,
            },
            "summary": {
                "followUpCount": len(follow_up_rows),
                "pendingFollowUpCount": sum(1 for item in follow_up_rows if item.get("status") == "pending"),
                "transitionedFollowUpCount": sum(
                    1 for item in verified_transitions
                ),
                "outcomeCount": len(outcome_rows),
                "actionObservationCount": len(action_rows),
                "actionPlanRecorded": bool(execution_feedback.get("actionPlans")),
                "executionRecorded": bool(
                    execution_feedback.get("executionEpisodes") or execution_feedback.get("fills")
                ),
                "lifecycleReviewRecorded": bool(
                    lifecycle_feedback.get("decisionReviews")
                    or lifecycle_feedback.get("performanceAttributions")
                ),
            },
            "sourceStatus": statuses,
            "sourceErrors": errors,
        }
        payload["reviewSummary"] = decision_review_summary(payload)
        payload["observationState"]["outcome"] = payload["reviewSummary"]["state"]
        fingerprint = _material_fingerprint(payload)
        payload["materialFingerprint"] = fingerprint
        payload["packetId"] = "decision-continuity:" + fingerprint[:24]
        return payload


def build_decision_continuity_packet(
    *,
    account_id: str,
    symbol: str,
    captured_at: str,
    previous_decision: object = None,
    follow_up_conditions: Iterable[object] = None,
    unsupported_follow_ups: Iterable[object] = None,
    observed_outcomes: Iterable[object] = None,
    action_observations: Iterable[object] = None,
    current_position: object = None,
    execution_feedback: object = None,
    lifecycle_feedback: object = None,
    selected_hypothesis: object = None,
    source_status: object = None,
) -> Dict[str, object]:
    return DecisionContinuityPacket(
        account_id=account_id,
        symbol=symbol,
        captured_at=captured_at,
        previous_decision=_mapping(previous_decision),
        selected_hypothesis=_mapping(selected_hypothesis),
        follow_up_conditions=_rows(follow_up_conditions or [], (
            "conditionId", "field", "operator", "threshold", "purpose", "label",
            "onSatisfied", "baselineValue", "previousValue", "currentValue",
            "baselineObserved", "baselineMatched", "previousMatched", "currentMatched", "armed",
            "status", "observable", "observedAt", "transitionAt", "transitionId",
            "transitionKind", "transitionVerified", "legacyBaselineCaptured", "expiresAt",
        ), 8),
        unsupported_follow_ups=_rows(unsupported_follow_ups or [], (
            "conditionId", "field", "operator", "threshold", "purpose", "label",
            "status", "observable", "reason",
        ), 4),
        observed_outcomes=_outcome_rows(observed_outcomes or []),
        action_observations=_rows(action_observations or [], (
            "observationId", "observedAt", "activityEpisodeId", "priorDecisionEpisodeId",
            "priorAction", "observedDirection", "correspondence", "elapsedMinutes",
            "previousQuantity", "observedQuantity", "quantityDelta", "confidence",
            "causalityClaimed",
        ), 4),
        current_position=_mapping(current_position),
        execution_feedback=_feedback(
            execution_feedback,
            ("actionPlans", "executionEpisodes", "fills"),
        ),
        lifecycle_feedback=_feedback(
            lifecycle_feedback,
            ("decisionReviews", "performanceAttributions"),
        ),
        source_status=_mapping(source_status),
    ).to_dict()


def compact_decision_continuity_packet(value: object) -> Dict[str, object]:
    """Normalize an already captured packet without changing its identity."""

    packet = _mapping(value)
    if packet.get("contractVersion") != DECISION_CONTINUITY_PACKET_VERSION:
        return {}
    return {
        key: packet.get(key)
        for key in (
            "contractVersion", "packetId", "materialFingerprint", "accountId", "symbol",
            "capturedAt", "status", "previousDecision", "selectedHypothesis",
            "followUpConditions", "unsupportedFollowUps", "observedOutcomes",
            "actionObservations", "currentPosition", "executionFeedback",
            "lifecycleFeedback", "observationState", "summary", "sourceStatus", "sourceErrors", "reviewSummary",
        )
        if packet.get(key) not in (None, "", [], {})
    }
