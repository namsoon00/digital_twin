"""Immutable source facts carried across the reasoning boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from datetime import datetime
from typing import Dict, Iterable, List, Mapping, Tuple

from .investment_calendar import InvestmentCalendarEvent


REASONING_SOURCE_FACT_VERSION = "reasoning-source-fact-v1"
_VOLATILE_KEYS = frozenset({
    "collectedAt", "createdAt", "fetchedAt", "ingestedAt", "retrievedAt", "updatedAt",
})
_PRIVATE_KEY_TOKENS = frozenset({
    "apikey", "api_key", "authorization", "cookie", "credential", "password",
    "secret", "token",
})
_DOCUMENT_BODY_KEYS = frozenset({
    "articlebody", "body", "bodyhtml", "content", "contenthtml", "documenthtml",
    "fulltext", "html", "rawbody", "rawcontent", "rawhtml", "responsebody",
})
_SOURCE_FACT_MAX_DEPTH = 6
_SOURCE_FACT_MAX_MAPPING_ITEMS = 48
_SOURCE_FACT_MAX_LIST_ITEMS = 12
_SOURCE_FACT_MAX_TEXT_LENGTH = 1400


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


def compact_reasoning_source_fact_payload(
    value: object,
    *,
    depth: int = 0,
):
    """Return a bounded, secret-free fact payload suitable for durable replay.

    Full article/disclosure bodies remain in their canonical stores.  The
    reasoning boundary carries identifiers, verified claims, summaries,
    numeric observations, quality state, and provenance only.
    """

    if depth > _SOURCE_FACT_MAX_DEPTH:
        return None
    if isinstance(value, Mapping):
        result: Dict[str, object] = {}
        for raw_key, raw_item in value.items():
            key = str(raw_key or "").strip()
            normalized = key.lower().replace("-", "").replace("_", "")
            if not key:
                continue
            if any(token.replace("_", "") in normalized for token in _PRIVATE_KEY_TOKENS):
                continue
            if normalized in _DOCUMENT_BODY_KEYS or normalized.endswith("html"):
                continue
            item = compact_reasoning_source_fact_payload(raw_item, depth=depth + 1)
            if item not in (None, "", [], {}):
                result[key[:120]] = item
            if len(result) >= _SOURCE_FACT_MAX_MAPPING_ITEMS:
                break
        return result
    if isinstance(value, (list, tuple, set)):
        result: List[object] = []
        candidates = list(value)
        if isinstance(value, set):
            candidates.sort(key=lambda item: json.dumps(
                _stable_value(item),
                ensure_ascii=True,
                sort_keys=True,
                default=str,
            ))
        for raw_item in candidates:
            item = compact_reasoning_source_fact_payload(raw_item, depth=depth + 1)
            if item not in (None, "", [], {}):
                result.append(item)
            if len(result) >= _SOURCE_FACT_MAX_LIST_ITEMS:
                break
        return result
    if isinstance(value, str):
        return " ".join(value.split())[:_SOURCE_FACT_MAX_TEXT_LENGTH].rstrip()
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:_SOURCE_FACT_MAX_TEXT_LENGTH]


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
            "payload": compact_reasoning_source_fact_payload(_stable_value(self.payload)) or {},
        }

    @classmethod
    def from_request_payload(cls, value: Mapping[str, object]) -> "ReasoningSourceFact":
        payload = dict(value or {})
        fact_id = str(payload.get("factId") or payload.get("fact_id") or "").strip()
        fact_type = str(payload.get("factType") or payload.get("fact_type") or "").strip()
        aggregate_id = str(
            payload.get("aggregateId") or payload.get("aggregate_id") or ""
        ).strip()
        revision = str(payload.get("revision") or "").strip()
        if not fact_id or not fact_type or not aggregate_id or not revision:
            raise ValueError("Reasoning source fact identity is incomplete")
        subject_ids = payload.get("subjectIds") or payload.get("subject_ids") or []
        if isinstance(subject_ids, str):
            subject_ids = [subject_ids]
        return cls(
            fact_id=fact_id[:191],
            fact_type=fact_type[:96],
            aggregate_id=aggregate_id[:191],
            subject_ids=tuple(sorted({
                str(item or "").upper().strip()[:64]
                for item in subject_ids
                if str(item or "").strip()
            })),
            revision=revision[:64],
            source_event_id=str(
                payload.get("sourceEventId") or payload.get("source_event_id") or ""
            ).strip()[:191],
            source_event_name=str(
                payload.get("sourceEventName") or payload.get("source_event_name") or ""
            ).strip()[:191],
            observed_at=str(
                payload.get("observedAt") or payload.get("observed_at") or ""
            ).strip()[:40],
            ingested_at=str(
                payload.get("ingestedAt") or payload.get("ingested_at") or ""
            ).strip()[:40],
            valid_from=str(
                payload.get("validFrom") or payload.get("valid_from") or ""
            ).strip()[:40],
            valid_to=str(
                payload.get("validTo") or payload.get("valid_to") or ""
            ).strip()[:40],
            quality_state=str(
                payload.get("qualityState") or payload.get("quality_state")
                or "verified-source-boundary"
            ).strip()[:64],
            payload=dict(
                compact_reasoning_source_fact_payload(payload.get("payload") or {}) or {}
            ),
            version=str(payload.get("version") or REASONING_SOURCE_FACT_VERSION)[:64],
        )


def reasoning_source_fact(
    *,
    fact_type: str,
    aggregate_id: str,
    subject_ids: Iterable[str],
    source_event,
    payload: Mapping[str, object],
    revision: str = "",
    observed_at: str = "",
    valid_from: str = "",
    valid_to: str = "",
    quality_state: str = "verified-source-boundary",
) -> ReasoningSourceFact:
    """Build one immutable source fact without importing a transport type."""

    clean_type = str(fact_type or "ReasoningSourceFact").strip()[:96]
    clean_aggregate = str(aggregate_id or "").strip()[:191]
    clean_payload = dict(compact_reasoning_source_fact_payload(payload or {}) or {})
    semantic_revision = str(revision or "").strip()
    if not semantic_revision:
        semantic_revision = _fingerprint({
            "factType": clean_type,
            "aggregateId": clean_aggregate,
            "payload": clean_payload,
        })
    elif len(semantic_revision) != 64:
        semantic_revision = _fingerprint({
            "sourceRevision": semantic_revision,
            "factType": clean_type,
            "aggregateId": clean_aggregate,
            "payload": clean_payload,
        })
    occurred_at = str(getattr(source_event, "occurred_at", "") or "")
    fact_identity = _fingerprint({
        "factType": clean_type,
        "aggregateId": clean_aggregate,
        "revision": semantic_revision,
    })
    return ReasoningSourceFact(
        fact_id="reasoning-fact:" + fact_identity[:32],
        fact_type=clean_type,
        aggregate_id=clean_aggregate,
        subject_ids=tuple(sorted({
            str(symbol or "").upper().strip()[:64]
            for symbol in subject_ids or []
            if str(symbol or "").strip()
        })),
        revision=semantic_revision,
        source_event_id=str(getattr(source_event, "event_id", "") or "")[:191],
        source_event_name=str(getattr(source_event, "name", "") or "")[:191],
        observed_at=str(observed_at or occurred_at)[:40],
        ingested_at=occurred_at[:40],
        valid_from=str(valid_from or observed_at or occurred_at)[:40],
        valid_to=str(valid_to or "")[:40],
        quality_state=str(quality_state or "verified-source-boundary")[:64],
        payload=clean_payload,
    )


def reasoning_source_fact_lineage_for_symbol(
    source_facts: Iterable[Mapping[str, object]],
    symbol: str,
) -> Dict[str, object]:
    target = str(symbol or "").upper().strip()
    ids_by_type: Dict[str, List[str]] = {}
    revisions_by_type: Dict[str, List[str]] = {}
    for raw in source_facts or []:
        if not isinstance(raw, Mapping):
            continue
        subjects = {
            str(item or "").upper().strip()
            for item in raw.get("subjectIds") or raw.get("subject_ids") or []
            if str(item or "").strip()
        }
        if target and target not in subjects:
            continue
        fact_type = str(raw.get("factType") or raw.get("fact_type") or "").strip()
        fact_id = str(raw.get("factId") or raw.get("fact_id") or "").strip()
        revision = str(raw.get("revision") or "").strip()
        if not fact_type or not fact_id:
            continue
        ids_by_type.setdefault(fact_type, [])
        if fact_id not in ids_by_type[fact_type]:
            ids_by_type[fact_type].append(fact_id)
        if revision:
            revisions_by_type.setdefault(fact_type, [])
            if revision not in revisions_by_type[fact_type]:
                revisions_by_type[fact_type].append(revision)
    return {
        "sourceFactIds": sorted({
            fact_id for values in ids_by_type.values() for fact_id in values
        }),
        "sourceFactIdsByType": {
            key: sorted(values) for key, values in sorted(ids_by_type.items())
        },
        "sourceFactRevisionsByType": {
            key: sorted(values) for key, values in sorted(revisions_by_type.items())
        },
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
