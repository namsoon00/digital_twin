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

from .investment_decision_history import compact_decision_episode_memory


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
                    1 for item in follow_up_rows if item.get("status") in {"satisfied", "invalidated", "expired"}
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
            "onSatisfied", "currentValue", "status", "observable", "observedAt",
            "transitionAt", "expiresAt",
        ), 8),
        unsupported_follow_ups=_rows(unsupported_follow_ups or [], (
            "conditionId", "field", "operator", "threshold", "purpose", "label",
            "status", "observable", "reason",
        ), 4),
        observed_outcomes=_rows(observed_outcomes or [], (
            "outcomeId", "episodeId", "observedAt", "price", "profitLossRate",
            "priceChangeFromDecisionPct", "selectedHypothesisStatus",
            "contradictedEvidenceIds",
        ), 6),
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
            "lifecycleFeedback", "observationState", "summary", "sourceStatus", "sourceErrors",
        )
        if packet.get(key) not in (None, "", [], {})
    }
