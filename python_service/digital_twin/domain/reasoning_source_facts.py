"""Immutable source facts carried across the reasoning boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from datetime import datetime
from typing import Dict, Iterable, Mapping, Tuple

from .investment_calendar import InvestmentCalendarEvent


REASONING_SOURCE_FACT_VERSION = "reasoning-source-fact-v1"
_VOLATILE_KEYS = frozenset({
    "collectedAt", "createdAt", "fetchedAt", "ingestedAt", "retrievedAt", "updatedAt",
})


def _stable_value(value: object):
    if isinstance(value, Mapping):
        return {
            str(key): _stable_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in _VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    return value


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        _stable_value(value), ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReasoningSourceFact:
    fact_id: str
    fact_type: str
    aggregate_id: str
    subject_ids: Tuple[str, ...]
    revision: str
    source_event_id: str
    source_event_name: str
    observed_at: str
    ingested_at: str
    valid_from: str
    valid_to: str
    quality_state: str
    payload: Dict[str, object]
    version: str = REASONING_SOURCE_FACT_VERSION

    def to_dict(self) -> Dict[str, object]:
        value = asdict(self)
        value["subject_ids"] = list(self.subject_ids)
        return value

    def request_payload(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "factId": self.fact_id,
            "factType": self.fact_type,
            "aggregateId": self.aggregate_id,
            "subjectIds": list(self.subject_ids),
            "revision": self.revision,
            "sourceEventId": self.source_event_id,
            "sourceEventName": self.source_event_name,
            "observedAt": self.observed_at,
            "ingestedAt": self.ingested_at,
            "validFrom": self.valid_from,
            "validTo": self.valid_to,
            "qualityState": self.quality_state,
            "payload": _stable_value(self.payload),
        }


def investment_calendar_source_fact(
    event: InvestmentCalendarEvent,
    source_event,
) -> ReasoningSourceFact:
    fact_type = (
        "EarningsCalendarEvent"
        if str(event.event_type or "").strip() == "earnings"
        else "InvestmentCalendarEvent"
    )
    event_payload = _stable_value(event.to_dict())
    revision = _fingerprint({
        "factType": fact_type,
        "aggregateId": event.event_id,
        "payload": event_payload,
    })
    source_event_id = str(getattr(source_event, "event_id", "") or "")
    occurred_at = str(getattr(source_event, "occurred_at", "") or "")
    return ReasoningSourceFact(
        fact_id="reasoning-fact:" + revision[:32],
        fact_type=fact_type,
        aggregate_id=str(event.event_id or ""),
        subject_ids=tuple(sorted({str(symbol or "").upper() for symbol in event.symbols if str(symbol or "")})),
        revision=revision,
        source_event_id=source_event_id,
        source_event_name=str(getattr(source_event, "name", "") or ""),
        observed_at=occurred_at,
        ingested_at=occurred_at,
        valid_from=str(event.starts_at or ""),
        valid_to=str(event.ends_at or ""),
        quality_state=(
            "verified-source-boundary"
            if bool((event.payload or {}).get("officialSource")) or event.source not in {"", "manual"}
            else "user-supplied"
        ),
        payload=event_payload,
    )


def reasoning_source_facts_runtime_eligibility(
    source_facts: Iterable[Mapping[str, object]],
    now_at: datetime = None,
) -> Dict[str, object]:
    """Decide whether immutable source facts still belong in the live ABox.

    Calendar history remains durable in MySQL, but candidate release replay
    must not turn an expired schedule back into current ontology state. Mixed
    fact batches and explicit removal revisions remain eligible.
    """
    facts = [dict(item or {}) for item in source_facts or [] if isinstance(item, Mapping)]
    calendar_facts = [
        item for item in facts
        if str(item.get("factType") or item.get("fact_type") or "")
        in {"EarningsCalendarEvent", "InvestmentCalendarEvent"}
    ]
    if not calendar_facts or len(calendar_facts) != len(facts):
        return {
            "eligible": True,
            "status": "not-calendar-only",
            "factCount": len(facts),
            "calendarFactCount": len(calendar_facts),
        }

    event_ids = []
    for item in calendar_facts:
        payload = dict(item.get("payload") or {})
        event = InvestmentCalendarEvent.from_payload(payload)
        event_ids.append(str(event.event_id or item.get("aggregateId") or ""))
        if event.status in {"deleted", "superseded", "rejected"}:
            return {
                "eligible": True,
                "status": "calendar-removal-revision",
                "eventIds": event_ids,
            }
        if event.reasoning_eligible(now_at=now_at):
            return {
                "eligible": True,
                "status": "calendar-review-window-active",
                "eventIds": event_ids,
            }
    return {
        "eligible": False,
        "status": "expired-calendar-history",
        "reasonCode": "expired-calendar-history",
        "reason": "The calendar fact is outside the live reasoning review window.",
        "eventIds": event_ids,
    }
