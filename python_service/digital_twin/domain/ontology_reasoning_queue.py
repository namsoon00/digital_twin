"""Domain contract for durable ontology reasoning queue ingress.

This module contains only scheduling identity and provenance.  It never
evaluates an investment rule; TypeDB remains the source of investment
judgement after the queued facts reach the ABox and InferenceBox.
"""

from __future__ import annotations

import hashlib
from typing import Dict, List, Mapping, Tuple

from .events import DomainEvent, ONTOLOGY_REASONING_REQUESTED
from .verified_snapshot_reasoning import (
    VERIFIED_MONITOR_SNAPSHOT_SLOT_FAMILY,
    VERIFIED_MONITOR_SNAPSHOT_TRIGGER,
)


REVIEW_LEVEL_ORDER = {
    "normal": 0,
    "observe": 1,
    "check": 2,
    "act": 3,
    "immediate": 4,
    "blocked": -1,
}

TRIGGER_ORDER = {
    VERIFIED_MONITOR_SNAPSHOT_TRIGGER: 7,
    "research-evidence-update": 6,
    "news-analysis-enrichment": 6,
    "research-evidence-lifecycle": 6,
    "investment-calendar-update": 5,
    "market-data-update": 4,
    "kis-realtime-websocket": 3,
    "kis-realtime-update": 3,
    "portfolio-snapshot-update": 2,
    "data-update": 1,
}

COALESCIBLE_REALTIME_TRIGGERS = {
    VERIFIED_MONITOR_SNAPSHOT_TRIGGER,
    "market-data-update",
    "kis-realtime-update",
    "kis-realtime-websocket",
    "portfolio-snapshot-update",
}

# Investment-calendar mutations are latest-state source facts too. A later
# calendar snapshot replaces the older calendar fact set for the same
# account/symbol; it must not accumulate as direct replay work behind TypeDB.
COALESCIBLE_LATEST_STATE_TRIGGERS = COALESCIBLE_REALTIME_TRIGGERS | {
    "investment-calendar-update",
}

# These updates mutate durable ResearchEvidence facts. The ABox projection
# reads the latest evidence set, so older article-analysis or lifecycle ticks
# add no independent inference value once a newer revision for the same
# account/symbol/fact family is waiting. Hypothesis handoffs remain outside
# this set because their run-to-generation contract is non-fungible.
COALESCIBLE_RESEARCH_TRIGGERS = {
    "research-evidence-update",
    "news-analysis-enrichment",
    "research-evidence-lifecycle",
}

# A generic research update causes a new projection of the complete current
# ResearchEvidence set. It is not a request to replay only the article
# analysis, news event, or lifecycle fact family that happened to trigger it.
# Keep one latest-state mailbox slot per account/symbol so those equivalent
# triggers cannot accumulate behind a slow TypeDB projection.
GENERIC_RESEARCH_LATEST_STATE_SLOT = "ResearchEvidenceLatestState"


def event_payload(event: object) -> Dict[str, object]:
    return dict(getattr(event, "payload", {}) or {})


def event_symbols(event: object) -> List[str]:
    symbols: List[str] = []
    for symbol in event_payload(event).get("symbols") or []:
        clean = str(symbol or "").upper().strip()
        if clean and clean not in symbols:
            symbols.append(clean)
    return symbols


def event_changed_count(event: object) -> int:
    try:
        return int(float(event_payload(event).get("changedCount") or 0))
    except (TypeError, ValueError):
        return 0


def materiality_assessments(event: object) -> List[Dict[str, object]]:
    raw = event_payload(event).get("materialityAssessments") or []
    if isinstance(raw, Mapping):
        raw = list(raw.values())
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def event_review_level(event: object) -> str:
    levels = [
        str(item.get("reviewLevel") or "normal").strip().lower()
        for item in materiality_assessments(event)
    ]
    levels = [level for level in levels if level in REVIEW_LEVEL_ORDER]
    return max(levels or ["normal"], key=lambda item: REVIEW_LEVEL_ORDER.get(item, 0))


def event_fact_revision(event: object, symbol: object) -> str:
    clean_symbol = str(symbol or "").upper().strip()
    revisions = event_payload(event).get("factRevisionsBySymbol")
    if not clean_symbol or not isinstance(revisions, Mapping):
        return ""
    value = revisions.get(clean_symbol)
    if value is None:
        for key, candidate in revisions.items():
            if str(key or "").upper().strip() == clean_symbol:
                value = candidate
                break
    return str(value or "").strip()[:160]


def is_generic_research_latest_state(event: object) -> bool:
    """Whether an event may replace an older generic research trigger.

    Hypothesis research handoffs remain non-fungible: each carries a
    run-to-generation acknowledgement contract and must not be collapsed into
    the ordinary current ResearchEvidence observation.
    """
    payload = event_payload(event)
    trigger = str(payload.get("trigger") or "").strip()
    return bool(
        trigger in COALESCIBLE_RESEARCH_TRIGGERS
        and not str(payload.get("researchRunId") or "").strip()
        and not bool(payload.get("reasoningHandoff"))
        and event_review_level(event) != "immediate"
    )


def is_verified_monitor_snapshot_event(event: object) -> bool:
    """Whether a request is anchored to the snapshot the worker replays."""

    payload = event_payload(event)
    return bool(
        str(payload.get("trigger") or "").strip() == VERIFIED_MONITOR_SNAPSHOT_TRIGGER
        and isinstance(payload.get("verifiedSourceSnapshot"), Mapping)
        and str((payload.get("verifiedSourceSnapshot") or {}).get("generatedAt") or "").strip()
    )


def mailbox_slot_family(event: object, fact_types: Tuple[str, ...]) -> str:
    """Return the durable latest-state identity without losing event facts.

    ``factFamily`` remains on the mailbox row for audit/readability. This
    value is used only to build the slot key. Generic research updates share
    one slot because TypeDB reads the whole current evidence set on the next
    projection.
    """
    if is_generic_research_latest_state(event):
        return GENERIC_RESEARCH_LATEST_STATE_SLOT
    if is_verified_monitor_snapshot_event(event):
        return VERIFIED_MONITOR_SNAPSHOT_SLOT_FAMILY
    return ",".join(fact_types) or "MarketQuote"


def realtime_coalescing_key(event: object) -> Tuple[str, str, Tuple[str, ...]]:
    """Return the durable latest-state identity for fungible observations."""
    payload = event_payload(event)
    trigger = str(payload.get("trigger") or "").strip()
    generic_research = is_generic_research_latest_state(event)
    if trigger not in COALESCIBLE_LATEST_STATE_TRIGGERS and not generic_research:
        return ()
    if event_review_level(event) == "immediate":
        return ()
    symbols = event_symbols(event)
    if not symbols:
        return ()
    fact_types = tuple(sorted({
        str(value or "").strip()
        for value in payload.get("factTypes") or []
        if str(value or "").strip()
    }))
    account_ids = []
    raw = payload.get("accountIds") or payload.get("accountId") or []
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, (list, tuple, set)):
        raw = []
    for value in [payload.get("accountId")] + list(raw):
        clean = str(value or "").strip()
        if clean and clean not in account_ids:
            account_ids.append(clean)
    return ",".join(sorted(account_ids)) or "market", trigger, fact_types


def event_as_dict(event: DomainEvent) -> Dict[str, object]:
    if hasattr(event, "to_dict"):
        payload = event.to_dict()
        if isinstance(payload, Mapping):
            return dict(payload)
    return {
        "name": str(getattr(event, "name", "") or ""),
        "aggregate_id": str(getattr(event, "aggregate_id", "") or ""),
        "payload": event_payload(event),
        "occurred_at": str(getattr(event, "occurred_at", "") or ""),
        "event_id": str(getattr(event, "event_id", "") or ""),
        "correlation_id": str(getattr(event, "correlation_id", "") or ""),
    }


def durable_mailbox_entries(event: DomainEvent) -> List[Dict[str, object]]:
    """Build coalescible queue entries at domain-event ingress time.

    Non-fungible research handoffs and immediate events intentionally remain in
    the audited event path.  They cannot be replaced by a later snapshot.
    """
    if str(getattr(event, "name", "") or "") != ONTOLOGY_REASONING_REQUESTED:
        return []
    if event_changed_count(event) <= 0:
        return []
    coalescing_key = realtime_coalescing_key(event)
    if not coalescing_key:
        return []
    account_scope, trigger, fact_types = coalescing_key
    event_id = str(getattr(event, "event_id", "") or "").strip()
    if not event_id:
        return []
    source_event = event_as_dict(event)
    source_event["event_id"] = event_id
    source_event.setdefault("occurred_at", str(getattr(event, "occurred_at", "") or ""))
    family = ",".join(fact_types) or "MarketQuote"
    slot_family = mailbox_slot_family(event, fact_types)
    priority = (
        REVIEW_LEVEL_ORDER.get(event_review_level(event), 0) * 1000
        + TRIGGER_ORDER.get(trigger, 0) * 100
        + (1 if "MarketQuote" in fact_types else 0)
    )
    entries = []
    for symbol in event_symbols(event):
        seed = "|".join([account_scope, symbol, slot_family])
        entries.append({
            "mailboxKey": hashlib.sha256(seed.encode("utf-8")).hexdigest(),
            "sourceEventId": event_id,
            "sourceEvent": source_event,
            "accountScope": account_scope,
            "symbol": symbol,
            "factFamily": family,
            "mailboxSlotFamily": slot_family,
            "trigger": trigger,
            "reviewLevel": event_review_level(event),
            "priorityHint": priority,
            "occurredAt": str(getattr(event, "occurred_at", "") or ""),
            "factRevision": event_fact_revision(event, symbol),
        })
    return entries
