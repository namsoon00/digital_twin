"""Operational coverage contract from material events to alert outcomes.

This module does not decide whether an investment is attractive.  It only
proves that every material source event reached a durable terminal outcome or
remains inside its processing deadline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, Mapping, Optional, Tuple


ALERT_COVERAGE_CONTRACT_VERSION = "investment-alert-coverage-v1"

RECEIVED = "RECEIVED"
REASONING_PENDING = "REASONING_PENDING"
REASONED = "REASONED"
NO_MATCH = "NO_MATCH"
CANDIDATE = "CANDIDATE"
DELIVERY_PENDING = "DELIVERY_PENDING"
REFERENCE_ONLY = "REFERENCE_ONLY"
REVIEW_ONLY = "REVIEW_ONLY"
SUPPRESSED = "SUPPRESSED"
SUPERSEDED = "SUPERSEDED"
BLOCKED = "BLOCKED"
DELIVERED = "DELIVERED"
FAILED = "FAILED"

TERMINAL_STATES = {
    NO_MATCH,
    REFERENCE_ONLY,
    REVIEW_ONLY,
    SUPPRESSED,
    SUPERSEDED,
    BLOCKED,
    DELIVERED,
    FAILED,
}

SUBJECT_TERMINAL_STATES = {
    "OBSERVATION": REFERENCE_ONLY,
    "REVIEW_ONLY": REVIEW_ONLY,
    "ABSTAINED": REVIEW_ONLY,
    "SUPPRESSED": SUPPRESSED,
    "SUPERSEDED": SUPERSEDED,
    "EXPIRED": SUPERSEDED,
    "BLOCKED": BLOCKED,
}


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _upper(value: object) -> str:
    return _text(value).upper()


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _parse_time(value: object) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _matched_symbol(item: Mapping[str, object], symbol: str) -> bool:
    subject = _upper(item.get("subject") or item.get("symbol"))
    return not subject or subject == _upper(symbol)


def material_event_assessment(
    event_payload: Mapping[str, object],
    symbol: str,
    *,
    candidate_present: bool = False,
) -> Tuple[bool, str]:
    """Classify scheduling materiality without creating investment meaning."""

    payload = _mapping(event_payload)
    clean_symbol = _upper(symbol)
    followups = {
        _upper(item)
        for item in payload.get("observationFollowupSymbols") or []
        if _upper(item)
    }
    if clean_symbol and clean_symbol in followups:
        return True, "verified-observation-followup"
    assessments = [
        _mapping(item)
        for item in payload.get("materialityAssessments") or []
        if isinstance(item, Mapping)
    ]
    matched = [item for item in assessments if _matched_symbol(item, clean_symbol)]
    passed = next((item for item in matched if bool(item.get("passed"))), None)
    if passed:
        conditions = [
            _text(item)
            for item in passed.get("matchedConditions") or []
            if _text(item)
        ]
        return True, "+".join(conditions) or _text(passed.get("reason")) or "materiality-passed"
    if candidate_present:
        return True, "typedb-alert-candidate"
    if matched:
        return False, _text(matched[0].get("reason")) or "materiality-not-passed"
    return False, "no-materiality-contract"


def derive_coverage_outcome(values: Mapping[str, object]) -> Dict[str, object]:
    """Derive one terminal or active state from persisted pipeline facts."""

    facts = _mapping(values)
    notification_status = _text(facts.get("notificationStatus")).lower()
    suppression_reason = _text(facts.get("suppressionReason"))
    if notification_status in {"done", "sent", "delivered"}:
        return {"state": DELIVERED, "terminal": True, "reasonCode": "notification-delivered"}
    if notification_status == "failed":
        return {
            "state": FAILED,
            "terminal": True,
            "reasonCode": "notification-failed",
            "reason": _text(facts.get("notificationError")),
        }
    if notification_status == "suppressed":
        return {
            "state": SUPPRESSED,
            "terminal": True,
            "reasonCode": suppression_reason or "notification-suppressed",
        }
    if notification_status == "superseded":
        return {"state": SUPERSEDED, "terminal": True, "reasonCode": "notification-superseded"}
    if notification_status in {"pending", "processing", "awaiting_ai", "retry"}:
        return {"state": DELIVERY_PENDING, "terminal": False, "reasonCode": "notification-pending"}

    subject_stage = _text(facts.get("subjectStage")).upper()
    if subject_stage in SUBJECT_TERMINAL_STATES:
        state = SUBJECT_TERMINAL_STATES[subject_stage]
        return {
            "state": state,
            "terminal": True,
            "reasonCode": "subject-" + subject_stage.lower().replace("_", "-"),
        }
    if subject_stage == "PUBLISHED":
        if bool(facts.get("publicationDelivered")):
            return {"state": DELIVERED, "terminal": True, "reasonCode": "publication-delivered"}
        return {"state": DELIVERY_PENDING, "terminal": False, "reasonCode": "publication-pending-delivery"}
    if subject_stage in {
        "CREATED", "READY", "AI_PENDING", "AI_COMPLETED", "VALIDATED",
    }:
        return {"state": CANDIDATE, "terminal": False, "reasonCode": "subject-" + subject_stage.lower()}

    reasoning_status = _text(facts.get("reasoningJobStatus")).lower()
    result_status = _text(facts.get("reasoningResultStatus")).lower()
    if reasoning_status in {"failed", "excluded"} or result_status in {"failed", "blocked", "error"}:
        return {
            "state": FAILED if reasoning_status == "failed" or result_status in {"failed", "error"} else BLOCKED,
            "terminal": True,
            "reasonCode": _text(facts.get("reasonCode")) or "reasoning-" + (result_status or reasoning_status),
            "reason": _text(facts.get("reason")),
        }
    if reasoning_status == "completed":
        if bool(facts.get("candidatePresent")):
            return {"state": CANDIDATE, "terminal": False, "reasonCode": "candidate-without-subject-outcome"}
        return {"state": NO_MATCH, "terminal": True, "reasonCode": "reasoning-no-material-candidate"}
    if reasoning_status in {"queued", "retry", "processing", "awaiting_source", "awaiting_world_projection"}:
        return {"state": REASONING_PENDING, "terminal": False, "reasonCode": "reasoning-pending"}
    if reasoning_status:
        return {"state": REASONED, "terminal": False, "reasonCode": "reasoning-status-" + reasoning_status}
    return {"state": RECEIVED, "terminal": False, "reasonCode": "source-event-received"}


@dataclass(frozen=True)
class AlertCoverageAssessment:
    coverage_id: str
    deployment_id: str
    source_event_id: str
    account_id: str
    symbol: str
    material: bool
    state: str
    terminal: bool
    reason_code: str = ""
    reason: str = ""
    event_at: str = ""
    candidate_present: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "contractVersion": ALERT_COVERAGE_CONTRACT_VERSION,
            "coverageId": _text(self.coverage_id),
            "deploymentId": _text(self.deployment_id),
            "sourceEventId": _text(self.source_event_id),
            "accountId": _text(self.account_id),
            "symbol": _upper(self.symbol),
            "material": bool(self.material),
            "state": _text(self.state).upper(),
            "terminal": bool(self.terminal),
            "reasonCode": _text(self.reason_code),
            "reason": _text(self.reason),
            "eventAt": _text(self.event_at),
            "candidatePresent": bool(self.candidate_present),
        }


def evaluate_alert_coverage_health(
    records: Iterable[Mapping[str, object]],
    *,
    now: Optional[datetime] = None,
    deadline_seconds: int = 300,
    starvation_min_candidates: int = 8,
) -> Dict[str, object]:
    """Evaluate unexplained silence without imposing an alert quota."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    rows = [dict(item or {}) for item in records or []]
    material = [item for item in rows if bool(item.get("material"))]
    terminal = [item for item in material if bool(item.get("terminal"))]
    overdue = []
    for item in material:
        if bool(item.get("terminal")):
            continue
        observed = _parse_time(item.get("eventAt") or item.get("createdAt"))
        if observed and (current - observed).total_seconds() >= max(1, int(deadline_seconds or 300)):
            overdue.append(item)
    failures = [item for item in material if _text(item.get("state")).upper() == FAILED]
    candidates = [item for item in material if bool(item.get("candidatePresent"))]
    delivered = [item for item in candidates if _text(item.get("state")).upper() == DELIVERED]
    starvation_states = {SUPPRESSED, SUPERSEDED, REVIEW_ONLY, REFERENCE_ONLY, BLOCKED}
    terminal_non_delivery = [
        item for item in candidates
        if _text(item.get("state")).upper() in starvation_states
    ]
    starvation = bool(
        len(candidates) >= max(1, int(starvation_min_candidates or 8))
        and not delivered
        and len(terminal_non_delivery) == len(candidates)
    )
    terminal_pct = round((len(terminal) / len(material) * 100.0), 1) if material else 100.0
    if failures:
        state = "critical"
        reason = "중요 사건 처리 중 실패가 발생했습니다."
    elif overdue:
        state = "warning"
        reason = "중요 사건이 제한 시간 안에 최종 처리 상태에 도달하지 못했습니다."
    elif starvation:
        state = "warning"
        reason = "중요 판단 후보가 연속 생성됐지만 사용자 전달 결과가 한 건도 없습니다."
    else:
        state = "healthy"
        reason = "중요 사건이 모두 전달 또는 설명 가능한 종료 상태에 도달했습니다."
    return {
        "contractVersion": ALERT_COVERAGE_CONTRACT_VERSION,
        "state": state,
        "reason": reason,
        "materialEventCount": len(material),
        "terminalEventCount": len(terminal),
        "terminalCoveragePct": terminal_pct,
        "overdueEventCount": len(overdue),
        "failedEventCount": len(failures),
        "candidateEventCount": len(candidates),
        "deliveredCandidateCount": len(delivered),
        "terminalNonDeliveryCandidateCount": len(terminal_non_delivery),
        "policyStarvation": starvation,
        "deadlineSeconds": max(1, int(deadline_seconds or 300)),
        "overdueCoverageIds": [_text(item.get("coverageId")) for item in overdue[:20]],
        "failedCoverageIds": [_text(item.get("coverageId")) for item in failures[:20]],
    }
