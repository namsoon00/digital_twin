from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List

from .investment_calendar import clean_text, normalized_list, normalize_market, normalize_symbol, reminder_offsets_from_payload
from .portfolio import utc_now_iso


CANDIDATE_STATUS_PENDING = "pending"
CANDIDATE_STATUS_REGISTERED = "registered"
CANDIDATE_STATUS_REJECTED = "rejected"
CANDIDATE_FINAL_STATUSES = {CANDIDATE_STATUS_REGISTERED, CANDIDATE_STATUS_REJECTED}


def bounded_int(value: object, fallback: int, lower: int = 0, upper: int = 100) -> int:
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, min(upper, parsed))


def bounded_float(value: object, fallback: float, lower: float = 0.0, upper: float = 1.0) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, min(upper, parsed))


@dataclass
class InvestmentCalendarReviewCandidate:
    candidate_id: str
    proposed_event_id: str
    title: str
    event_type: str
    starts_at: str = ""
    timezone: str = "Asia/Seoul"
    all_day: bool = True
    status: str = CANDIDATE_STATUS_PENDING
    review_reason: str = "needsReview"
    importance: int = 60
    confidence: float = 0.0
    symbols: List[str] = field(default_factory=list)
    markets: List[str] = field(default_factory=list)
    account_ids: List[str] = field(default_factory=list)
    source: str = "research-evidence"
    source_url: str = ""
    notes: str = ""
    reminder_offsets_minutes: List[int] = field(default_factory=lambda: [1440, 180, 60, 0])
    source_evidence_id: str = ""
    payload: Dict[str, object] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = ""
    reviewed_at: str = ""
    review_note: str = ""

    @classmethod
    def from_payload(cls, payload: Dict[str, object]):
        payload = payload if isinstance(payload, dict) else {}
        body = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        return cls(
            candidate_id=clean_text(payload.get("candidateId") or payload.get("candidate_id"), 191),
            proposed_event_id=clean_text(payload.get("proposedEventId") or payload.get("proposed_event_id"), 191),
            title=clean_text(payload.get("title"), 255),
            event_type=clean_text(payload.get("eventType") or payload.get("event_type") or "custom", 64) or "custom",
            starts_at=clean_text(payload.get("startsAt") or payload.get("starts_at"), 40),
            timezone=clean_text(payload.get("timezone") or "Asia/Seoul", 80) or "Asia/Seoul",
            all_day=bool(payload.get("allDay", payload.get("all_day", True))),
            status=clean_text(payload.get("status") or CANDIDATE_STATUS_PENDING, 32) or CANDIDATE_STATUS_PENDING,
            review_reason=clean_text(payload.get("reviewReason") or payload.get("review_reason") or "needsReview", 80),
            importance=bounded_int(payload.get("importance"), 60),
            confidence=bounded_float(payload.get("confidence"), 0.0),
            symbols=normalized_list(payload.get("symbols"), normalize_symbol, 100),
            markets=normalized_list(payload.get("markets"), normalize_market, 50),
            account_ids=normalized_list(
                payload.get("accountIds") if "accountIds" in payload else payload.get("account_ids"),
                lambda value: clean_text(value, 191),
                50,
            ),
            source=clean_text(payload.get("source") or "research-evidence", 120) or "research-evidence",
            source_url=clean_text(payload.get("sourceUrl") or payload.get("source_url"), 1000),
            notes=clean_text(payload.get("notes"), 2000),
            reminder_offsets_minutes=reminder_offsets_from_payload(
                payload.get("reminderOffsetsMinutes")
                if "reminderOffsetsMinutes" in payload
                else payload.get("reminder_offsets_minutes")
            ),
            source_evidence_id=clean_text(payload.get("sourceEvidenceId") or payload.get("source_evidence_id"), 191),
            payload=body,
            created_at=clean_text(payload.get("createdAt") or payload.get("created_at"), 40) or utc_now_iso(),
            updated_at=clean_text(payload.get("updatedAt") or payload.get("updated_at"), 40),
            reviewed_at=clean_text(payload.get("reviewedAt") or payload.get("reviewed_at"), 40),
            review_note=clean_text(payload.get("reviewNote") or payload.get("review_note"), 1000),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "candidateId": payload["candidate_id"],
            "proposedEventId": payload["proposed_event_id"],
            "title": payload["title"],
            "eventType": payload["event_type"],
            "startsAt": payload["starts_at"],
            "timezone": payload["timezone"],
            "allDay": payload["all_day"],
            "status": payload["status"],
            "reviewReason": payload["review_reason"],
            "importance": payload["importance"],
            "confidence": payload["confidence"],
            "symbols": payload["symbols"],
            "markets": payload["markets"],
            "accountIds": payload["account_ids"],
            "source": payload["source"],
            "sourceUrl": payload["source_url"],
            "notes": payload["notes"],
            "reminderOffsetsMinutes": payload["reminder_offsets_minutes"],
            "sourceEvidenceId": payload["source_evidence_id"],
            "payload": payload["payload"],
            "createdAt": payload["created_at"],
            "updatedAt": payload["updated_at"],
            "reviewedAt": payload["reviewed_at"],
            "reviewNote": payload["review_note"],
        }

    def to_calendar_payload(self, starts_at: str = "", account_ids: Iterable[str] = None) -> Dict[str, object]:
        body = dict(self.payload or {})
        body.update({
            "reviewCandidateId": self.candidate_id,
            "reviewCandidateStatus": self.status,
            "reviewReason": self.review_reason,
            "sourceEvidenceId": self.source_evidence_id,
        })
        return {
            "eventId": self.proposed_event_id,
            "title": self.title,
            "eventType": self.event_type,
            "startsAt": clean_text(starts_at or self.starts_at, 80),
            "timezone": self.timezone or "Asia/Seoul",
            "allDay": False,
            "status": "active",
            "importance": self.importance,
            "symbols": list(self.symbols or []),
            "markets": list(self.markets or []),
            "accountIds": normalized_list(account_ids if account_ids is not None else self.account_ids, lambda value: clean_text(value, 191), 50),
            "source": self.source,
            "sourceUrl": self.source_url,
            "notes": self.notes,
            "reminderOffsetsMinutes": list(self.reminder_offsets_minutes or []),
            "payload": body,
        }
