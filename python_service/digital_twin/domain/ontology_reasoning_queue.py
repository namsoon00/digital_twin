"""Domain contract for durable ontology reasoning queue ingress.

This module contains only scheduling identity and provenance.  It never
evaluates an investment rule; TypeDB remains the source of investment
judgement after the queued facts reach the ABox and InferenceBox.
"""

from __future__ import annotations

import hashlib
from typing import Dict, List, Mapping, Tuple

from .events import DomainEvent, ONTOLOGY_REASONING_REQUESTED
from .verified_snapshot_reasoning import VERIFIED_MONITOR_SNAPSHOT_TRIGGER


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
    "research-evidence-lifecycle": 5,
    "investment-calendar-update": 5,
    "market-data-update": 4,
    "kis-realtime-websocket": 3,
    "kis-realtime-update": 3,
    "research-evidence-update": 2,
    "news-analysis-enrichment": 2,
    "portfolio-snapshot-update": 2,
    "portfolio-activity": 6,
    "data-update": 1,
}

# This is operational dispatch priority, not an investment action. It only
# decides when an already accepted fact set is projected into TypeDB.
REASONING_PRIORITY_ORDER = {
    "background": 0,
    "research": 1,
    "market": 2,
    "urgent": 3,
    "critical": 4,
    "observation": 5,
}

REASONING_LANES = (
    "REALTIME_INGEST",
    "REALTIME_REASONING",
    "CONTEXT_REASONING",
    "RECONCILIATION_REASONING",
    "ENRICHMENT",
    "MAINTENANCE",
)

WORK_CLASSES = (
    "MARKET",
    "EVIDENCE",
    "MACRO",
    "PORTFOLIO",
    "RECONCILIATION",
)

IMPACT_SCOPES = (
    "SUBJECT",
    "MARKET_CONTEXT",
    "PORTFOLIO",
    "RECONCILIATION",
)

REASONING_SUBJECT_KINDS = (
    "INSTRUMENT",
    "STOCK",
    "PORTFOLIO",
    "ACCOUNT",
    "MARKET",
)

MACRO_FACT_TYPES = {
    "InterestRate",
    "FxRate",
    "MacroIndicator",
    "MarketProxy",
    "MarketProxyInstrument",
}

EVIDENCE_FACT_TYPES = {
    "ResearchEvidence",
    "NewsArticle",
    "Disclosure",
    "InvestmentCalendarEvent",
}

COMPANY_FACT_TYPES = {
    "CompanyProfile",
    "FinancialFact",
    "FinancialStatement",
    "GovernanceChange",
    "CapitalStructureChange",
    "ValuationObservation",
}

PORTFOLIO_FACT_TYPES = {
    "Portfolio",
    "PortfolioSnapshot",
    "Position",
    "Account",
    "PortfolioActivityEpisode",
    "PortfolioStateSnapshot",
    "DecisionActionObservation",
    "PortfolioRiskSnapshot",
    "PositionRiskMetric",
    "RebalanceScenario",
}

MARKET_FACT_TYPES = {
    "MarketQuote",
    "PriceMetric",
    "TradeFlow",
    "InvestorFlow",
    "TechnicalIndicator",
    "CryptoMarket",
}


def reasoning_lane_for_priority(priority: object) -> str:
    """Map existing dispatch priority to one bounded processing lane.

    This is an operational scheduling contract only. It neither evaluates a
    market fact nor changes which RuleBox rules TypeDB considers.
    """
    clean = str(priority or "").strip().lower()
    if clean in {"observation", "critical", "urgent"}:
        return "REALTIME_REASONING"
    return "CONTEXT_REASONING"


def event_reasoning_lane(event: object) -> str:
    payload = event_payload(event)
    mailbox = payload.get("_reasoningMailbox")
    mailbox = dict(mailbox or {}) if isinstance(mailbox, Mapping) else {}
    persisted = str(mailbox.get("reasoningLane") or "").strip().upper()
    if persisted in REASONING_LANES:
        return persisted
    work_class = event_work_class(event)
    if work_class == "RECONCILIATION":
        return "RECONCILIATION_REASONING"
    if work_class == "MARKET":
        return "REALTIME_REASONING"
    return "CONTEXT_REASONING"

# A persisted material price observation has a current-state TypeDB follow-up.
# It should not wait behind ordinary queue pressure, but the marker remains
# scheduling provenance only.
OBSERVATION_FOLLOWUP_PRIORITY_HINT = 100000

COALESCIBLE_REALTIME_TRIGGERS = {
    VERIFIED_MONITOR_SNAPSHOT_TRIGGER,
    "market-data-update",
    "kis-realtime-update",
    "kis-realtime-websocket",
    "portfolio-snapshot-update",
}

# Calendar and portfolio-risk mutations are latest-state source facts too. A
# later snapshot replaces the older fact set for the same account/symbol; it
# must not accumulate as direct replay work behind TypeDB.
COALESCIBLE_LATEST_STATE_TRIGGERS = COALESCIBLE_REALTIME_TRIGGERS | {
    "investment-calendar-update",
    "portfolio-risk-change",
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

# A price tick, a cached market refresh, and the monitor's persisted snapshot
# all describe the same current-world input for one account/symbol.  TypeDB
# rebuilds that current ABox rather than replaying each transport-specific
# fact family, so retaining separate slots here only lets stale observations
# queue ahead of the newest verified snapshot.
REALTIME_LATEST_STATE_SLOT = "RealtimeObservationLatestState"


def mailbox_fact_family(fact_type: object) -> str:
    """Map transport fact names to independently replaceable world facts."""

    value = str(fact_type or "").strip()
    normalized = value.lower().replace("_", "").replace("-", "")
    if normalized in {"marketquote", "pricemetric"}:
        return "price"
    if normalized in {"tradeflow", "executionflow", "investorflow", "orderbook"}:
        return "flow"
    if normalized in {"technicalindicator"}:
        return "technical"
    if normalized in {"interestrate"}:
        return "rates"
    if normalized in {"fxrate"}:
        return "fx"
    if normalized in {"cryptomarket"}:
        return "crypto"
    if normalized in {"macroindicator", "marketproxy", "marketproxyinstrument"}:
        return "macro-market"
    if value in EVIDENCE_FACT_TYPES:
        return "evidence"
    if value in PORTFOLIO_FACT_TYPES:
        return "portfolio"
    if normalized in {"dataquality"}:
        return "quality"
    if normalized in {"companyprofile"}:
        return "profile"
    if normalized in {"financialfact", "financialstatement"}:
        return "fundamental"
    if normalized in {"governancechange"}:
        return "governance"
    if normalized in {"capitalstructurechange"}:
        return "capital"
    if normalized in {"valuationobservation"}:
        return "company-valuation"
    return normalized or "marketquote"


def event_payload(event: object) -> Dict[str, object]:
    return dict(getattr(event, "payload", {}) or {})


def event_symbols(event: object) -> List[str]:
    symbols: List[str] = []
    for symbol in event_payload(event).get("symbols") or []:
        clean = str(symbol or "").upper().strip()
        if clean and clean not in symbols:
            symbols.append(clean)
    return symbols


def event_subject_kind(event: object) -> str:
    payload = event_payload(event)
    mailbox = payload.get("_reasoningMailbox")
    mailbox = dict(mailbox or {}) if isinstance(mailbox, Mapping) else {}
    explicit = str(mailbox.get("subjectKind") or payload.get("subjectKind") or "").upper().strip()
    if explicit in REASONING_SUBJECT_KINDS:
        return explicit
    return "INSTRUMENT" if event_symbols(event) else "MARKET"


def event_subject_id(event: object) -> str:
    payload = event_payload(event)
    mailbox = payload.get("_reasoningMailbox")
    mailbox = dict(mailbox or {}) if isinstance(mailbox, Mapping) else {}
    return str(mailbox.get("subjectId") or payload.get("subjectId") or "").strip()[:191]


def event_affected_symbols(event: object) -> List[str]:
    symbols: List[str] = []
    for symbol in event_payload(event).get("affectedSymbols") or []:
        clean = str(symbol or "").upper().strip()
        if clean and clean not in symbols:
            symbols.append(clean)
    return symbols[:200]


def event_subject_revision(event: object) -> str:
    payload = event_payload(event)
    mailbox = payload.get("_reasoningMailbox")
    mailbox = dict(mailbox or {}) if isinstance(mailbox, Mapping) else {}
    return str(mailbox.get("subjectRevision") or payload.get("subjectRevision") or "").strip()[:191]


def event_fact_types_for_symbol(event: object, symbol: object) -> Tuple[str, ...]:
    """Return the source fact types attributed to one mailbox subject.

    Older events only have a batch-wide ``factTypes`` list, so they retain the
    conservative fallback. Newer verified snapshots carry ``factTypesBySymbol``
    and must not let one symbol's research or flow fact raise another symbol's
    scheduling priority or routing family.
    """

    clean_symbol = str(symbol or "").upper().strip()
    payload = event_payload(event)
    mailbox = payload.get("_reasoningMailbox")
    mailbox = dict(mailbox or {}) if isinstance(mailbox, Mapping) else {}
    routed_families = mailbox.get("ruleFamilies")
    if isinstance(routed_families, str):
        routed_families = [routed_families]
    if isinstance(routed_families, (list, tuple, set)):
        clean_routed = tuple(sorted({
            str(value or "").strip()
            for value in routed_families
            if str(value or "").strip()
        }))
        if clean_routed:
            return clean_routed
    by_symbol = payload.get("factTypesBySymbol")
    if clean_symbol and isinstance(by_symbol, Mapping):
        values = None
        for raw_symbol, candidate in by_symbol.items():
            if str(raw_symbol or "").upper().strip() == clean_symbol:
                values = candidate
                break
        if isinstance(values, str):
            values = [values]
        if isinstance(values, (list, tuple, set)):
            return tuple(sorted({
                str(value or "").strip()
                for value in values
                if str(value or "").strip()
            }))
    return tuple(sorted({
        str(value or "").strip()
        for value in payload.get("factTypes") or []
        if str(value or "").strip()
    }))


def work_class_for_fact_types(
    fact_types: Iterable[object],
    trigger: object = "",
    full_reconciliation: bool = False,
) -> str:
    """Classify source facts without evaluating their investment meaning."""
    clean_trigger = str(trigger or "").strip().lower()
    clean_fact_types = {
        str(value or "").strip()
        for value in fact_types or []
        if str(value or "").strip()
    }
    if (
        full_reconciliation
        or "reconciliation" in clean_trigger
        or "rulebox" in clean_trigger
        or "tbox" in clean_trigger
        or "schema" in clean_trigger
    ):
        return "RECONCILIATION"
    if clean_fact_types & EVIDENCE_FACT_TYPES:
        return "EVIDENCE"
    if clean_fact_types & COMPANY_FACT_TYPES:
        return "EVIDENCE"
    if clean_fact_types & MACRO_FACT_TYPES:
        return "MACRO"
    if clean_fact_types & PORTFOLIO_FACT_TYPES:
        return "PORTFOLIO"
    if clean_fact_types & MARKET_FACT_TYPES:
        return "MARKET"
    if clean_trigger in COALESCIBLE_RESEARCH_TRIGGERS or "research" in clean_trigger or "news" in clean_trigger:
        return "EVIDENCE"
    if "macro" in clean_trigger or "interest-rate" in clean_trigger or "fx-rate" in clean_trigger:
        return "MACRO"
    if "portfolio" in clean_trigger and clean_trigger not in COALESCIBLE_REALTIME_TRIGGERS:
        return "PORTFOLIO"
    return "MARKET"


def event_work_class(event: object, symbol: object = "") -> str:
    """Classify one reasoning request without evaluating investment meaning."""
    payload = event_payload(event)
    mailbox = payload.get("_reasoningMailbox")
    mailbox = dict(mailbox or {}) if isinstance(mailbox, Mapping) else {}
    persisted = str(mailbox.get("workClass") or "").strip().upper()
    if persisted in WORK_CLASSES:
        return persisted
    return work_class_for_fact_types(
        event_fact_types_for_symbol(event, symbol),
        trigger=payload.get("trigger"),
        full_reconciliation=bool(payload.get("fullReconciliation")),
    )


def work_class_impact_scope(work_class: object) -> str:
    clean = str(work_class or "").strip().upper()
    if clean == "MACRO":
        return "MARKET_CONTEXT"
    if clean == "PORTFOLIO":
        return "PORTFOLIO"
    if clean == "RECONCILIATION":
        return "RECONCILIATION"
    return "SUBJECT"


def work_class_reasoning_lane(work_class: object, priority: object = "") -> str:
    del priority
    clean = str(work_class or "").strip().upper()
    if clean == "RECONCILIATION":
        return "RECONCILIATION_REASONING"
    if clean == "MARKET":
        return "REALTIME_REASONING"
    return "CONTEXT_REASONING"


def event_revision_vector(event: object, symbol: object = "") -> Dict[str, str]:
    """Return the bounded source revisions that make a mailbox item current."""
    payload = event_payload(event)
    source = payload.get("verifiedSourceSnapshot")
    source = dict(source or {}) if isinstance(source, Mapping) else {}
    values = {
        "fact": event_fact_revision(event, symbol) or event_subject_revision(event),
        "source": str(source.get("generatedAt") or payload.get("sourceGeneratedAt") or "").strip(),
        "rules": str(payload.get("ruleboxRulesHash") or payload.get("ruleboxRevision") or "").strip(),
        "tbox": str(payload.get("tboxFingerprint") or payload.get("tboxRevision") or "").strip(),
    }
    return {key: value[:191] for key, value in values.items() if value}


def observation_followup_symbols(event: object) -> List[str]:
    """Return raw-alert symbols that need a prompt current-state recheck.

    A virtual mailbox event may inherit the priority from a displaced source
    revision. In that case the durable mailbox marker is authoritative even
    though the newest source payload no longer carries the original raw-alert
    symbol list.
    """
    symbols = event_symbols(event)
    payload = event_payload(event)
    mailbox = payload.get("_reasoningMailbox")
    if isinstance(mailbox, Mapping) and bool(mailbox.get("observationFollowup")):
        return symbols
    raw = payload.get("observationFollowupSymbols") or []
    if isinstance(raw, str):
        raw = raw.split(",")
    requested = {
        str(symbol or "").upper().strip()
        for symbol in raw
        if str(symbol or "").strip()
    } if isinstance(raw, (list, tuple, set)) else set()
    return [symbol for symbol in symbols if symbol in requested]


def is_observation_followup_symbol(event: object, symbol: object) -> bool:
    clean = str(symbol or "").upper().strip()
    return bool(clean and clean in observation_followup_symbols(event))


def mailbox_entry_priority(
    event: object,
    symbol: object,
    subject_priority: int = 0,
) -> int:
    """Build a durable scheduling priority without evaluating investment facts."""
    payload = event_payload(event)
    trigger = str(payload.get("trigger") or "").strip()
    fact_types = set(event_fact_types_for_symbol(event, symbol))
    try:
        account_priority = max(0, int(subject_priority or 0))
    except (TypeError, ValueError):
        account_priority = 0
    return (
        (OBSERVATION_FOLLOWUP_PRIORITY_HINT if is_observation_followup_symbol(event, symbol) else 0)
        + account_priority * 10000
        + REASONING_PRIORITY_ORDER.get(event_reasoning_priority(event), 0) * 1500
        + REVIEW_LEVEL_ORDER.get(event_review_level(event), 0) * 1000
        + TRIGGER_ORDER.get(trigger, 0) * 100
        + (1 if "MarketQuote" in fact_types else 0)
    )


def event_changed_count(event: object) -> int:
    try:
        return int(float(event_payload(event).get("changedCount") or 0))
    except (TypeError, ValueError):
        return 0


def event_has_reasoning_work(event: object) -> bool:
    """Return whether a request needs a current-state TypeDB turn.

    An outboxed critical price observation is a delivery fact, rather than a
    newly persisted ABox fact. It still needs one follow-up projection even
    when its snapshot has zero ordinary fact changes.
    """
    return bool(event_changed_count(event) > 0 or observation_followup_symbols(event))


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


def event_reasoning_priority(event: object) -> str:
    """Classify the queue cadence without evaluating a market outcome.

    Existing source materiality and lifecycle contracts are the only inputs.
    In particular, an ordinary news refresh stays in the research lane even
    when its source was collected successfully; it is no longer promoted to
    the same cadence as a verified quote or an urgent fact withdrawal.
    """
    payload = event_payload(event)
    trigger = str(payload.get("trigger") or "").strip().lower()
    review_level = event_review_level(event)
    if any(is_observation_followup_symbol(event, symbol) for symbol in event_symbols(event)):
        return "observation"
    if review_level == "immediate":
        return "critical"
    if trigger == "investment-calendar-update":
        return "urgent"
    if trigger == "portfolio-activity":
        return "urgent"
    if trigger == "research-evidence-lifecycle":
        transitions = {
            str(item.get("transition") or "").strip().lower()
            for item in payload.get("evidenceDeltas") or []
            if isinstance(item, Mapping)
        }
        if transitions & {"retraction", "expiration", "demotion", "supersession"}:
            return "urgent"
    if is_verified_monitor_snapshot_event(event):
        return "urgent" if review_level in {"act", "immediate"} else "market"
    if trigger in COALESCIBLE_RESEARCH_TRIGGERS or "research" in trigger:
        return "urgent" if review_level == "act" else "research"
    if review_level == "act":
        return "urgent"
    if trigger in COALESCIBLE_REALTIME_TRIGGERS:
        return "market"
    return "background"


def is_realtime_latest_state(event: object) -> bool:
    """Whether an event is a replaceable realtime current-state observation.

    ``immediate`` remains intentionally outside this slot.  It has an
    explicit delivery/audit contract and must never disappear behind a later
    ordinary quote or monitor snapshot.
    """

    payload = event_payload(event)
    return bool(
        str(payload.get("trigger") or "").strip() in COALESCIBLE_REALTIME_TRIGGERS
        and event_review_level(event) != "immediate"
    )


def mailbox_slot_family(
    event: object,
    fact_types: Tuple[str, ...],
    fact_family: str = "",
) -> str:
    """Return the durable latest-state identity without losing event facts.

    ``factFamily`` remains on the mailbox row for audit/readability. This
    value is used only to build the slot key. Generic research updates share
    one slot because TypeDB reads the whole current evidence set on the next
    projection.
    """
    if is_generic_research_latest_state(event):
        return GENERIC_RESEARCH_LATEST_STATE_SLOT
    if is_realtime_latest_state(event):
        family = str(fact_family or "").strip() or mailbox_fact_family(
            next(iter(fact_types or ("MarketQuote",)), "MarketQuote")
        )
        # Preserve the existing price slot during migration. Independent
        # current-state families receive their own suffix and cannot overwrite
        # an unprocessed flow or technical revision for the same symbol.
        return (
            REALTIME_LATEST_STATE_SLOT
            if family == "price"
            else REALTIME_LATEST_STATE_SLOT + ":" + family
        )
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
    subject_kind = event_subject_kind(event)
    subject_id = event_subject_id(event)
    if not symbols and not (subject_kind in {"PORTFOLIO", "ACCOUNT"} and subject_id):
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
    if not event_has_reasoning_work(event):
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
    entries = []
    subject_kind = event_subject_kind(event)
    subject_id = event_subject_id(event)
    mailbox_subjects = event_symbols(event)
    if not mailbox_subjects and subject_kind in {"PORTFOLIO", "ACCOUNT"} and subject_id:
        mailbox_subjects = [""]
    for symbol in mailbox_subjects:
        symbol_fact_types = event_fact_types_for_symbol(event, symbol)
        priority = event_reasoning_priority(event)
        revision_vector = event_revision_vector(event, symbol)
        grouped_fact_types: Dict[Tuple[str, str], List[str]] = {}
        for fact_type in symbol_fact_types or ("MarketQuote",):
            work_class = work_class_for_fact_types(
                [fact_type],
                trigger=trigger,
                full_reconciliation=bool(event_payload(event).get("fullReconciliation")),
            )
            fact_family = mailbox_fact_family(fact_type)
            grouped_fact_types.setdefault((work_class, fact_family), []).append(fact_type)
        for (work_class, fact_family), routed_fact_types in grouped_fact_types.items():
            routed_fact_types = sorted(set(routed_fact_types))
            symbol_family = ",".join(routed_fact_types) or "MarketQuote"
            slot_family = mailbox_slot_family(event, routed_fact_types, fact_family)
            impact_scope = work_class_impact_scope(work_class)
            reasoning_lane = work_class_reasoning_lane(work_class, priority)
            seed = "|".join([
                account_scope,
                subject_kind,
                subject_id or symbol,
                work_class,
                slot_family,
            ])
            entry_priority = mailbox_entry_priority(event, symbol)
            entries.append({
                "mailboxKey": hashlib.sha256(seed.encode("utf-8")).hexdigest(),
                "sourceEventId": event_id,
                "sourceEvent": source_event,
                "accountScope": account_scope,
                "symbol": symbol,
                "subjectKind": subject_kind,
                "subjectId": subject_id,
                "affectedSymbols": event_affected_symbols(event),
                "subjectRevision": event_subject_revision(event),
                "factFamily": symbol_family,
                "ruleFamilies": routed_fact_types,
                "mailboxSlotFamily": slot_family,
                "workClass": work_class,
                "impactScope": impact_scope,
                "reasoningLane": reasoning_lane,
                "marketScope": str(event_payload(event).get("marketScope") or "market").strip() or "market",
                "revisionVector": revision_vector,
                "trigger": trigger,
                "reviewLevel": event_review_level(event),
                "priorityHint": entry_priority if work_class == "MARKET" else min(entry_priority, 1000),
                "observationFollowup": bool(
                    work_class == "MARKET"
                    and is_observation_followup_symbol(event, symbol)
                ),
                "occurredAt": str(getattr(event, "occurred_at", "") or ""),
                "factRevision": event_fact_revision(event, symbol) or event_subject_revision(event),
            })
    return entries
