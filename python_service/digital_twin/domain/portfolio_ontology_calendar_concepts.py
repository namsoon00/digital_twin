"""Project immutable investment-calendar facts into the factual ABox."""

from __future__ import annotations

from typing import Dict, Mapping
from datetime import datetime, timezone

from .evidence_time import event_time_contract
from .ontology_contracts import PortfolioOntology
from .ontology_external_abox import add_event_validity_concept
from .ontology_schema import add_entity, add_relation


def _calendar_facts(runtime_context: Dict[str, object]):
    values = runtime_context.get("reasoningSourceFacts") if isinstance(runtime_context, dict) else []
    return [dict(item) for item in values or [] if isinstance(item, Mapping)]


def _days_until(starts_at: str, evaluated_at: str):
    def parsed(value: str):
        text = str(value or "").replace("Z", "+00:00")
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
        return result.replace(tzinfo=result.tzinfo or timezone.utc).astimezone(timezone.utc)
    starts = parsed(starts_at)
    evaluated = parsed(evaluated_at)
    if not starts or not evaluated:
        return None
    return round((starts - evaluated).total_seconds() / 86400.0, 3)


def add_investment_calendar_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    symbol: str,
    runtime_context: Dict[str, object],
) -> None:
    evaluated_at = str((runtime_context or {}).get("asOf") or "")
    for fact in _calendar_facts(runtime_context):
        if str(fact.get("factType") or "") != "InvestmentCalendarEvent":
            continue
        subjects = {str(value or "").upper().strip() for value in fact.get("subjectIds") or []}
        if symbol not in subjects:
            continue
        payload = dict(fact.get("payload") or {})
        event_type = str(payload.get("eventType") or "custom")
        event_id = str(payload.get("eventId") or fact.get("aggregateId") or fact.get("factId") or "")
        if not event_id:
            continue
        starts_at = str(payload.get("startsAt") or fact.get("validFrom") or "")
        retrieved_at = str(fact.get("ingestedAt") or fact.get("observedAt") or "")
        time_contract = event_time_contract(
            event_kind="earnings" if event_type == "earnings" else "event",
            effective_at=starts_at,
            retrieved_at=retrieved_at,
            evaluated_at=evaluated_at,
        )
        status = str(payload.get("status") or "active")
        schedule_eligible = bool(
            starts_at
            and status in {"active", "tentative"}
            and str(fact.get("qualityState") or "") not in {"invalid", "rejected"}
        )
        days_until = _days_until(starts_at, evaluated_at)
        within_review_window = bool(
            schedule_eligible
            and days_until is not None
            and 0 <= days_until <= 14
        )
        kind = "earnings-calendar-event" if event_type == "earnings" else "investment-calendar-event"
        tbox_class = "EarningsCalendarEvent" if event_type == "earnings" else "InvestmentCalendarEvent"
        calendar_id = add_entity(graph, kind, event_id, str(payload.get("title") or event_id), {
            "tboxClass": tbox_class,
            "tboxClasses": [
                "Observation", "ExternalObservation", "ExternalSignal", "InvestmentCalendarEvent",
            ] + (["FundamentalObservation", "EarningsEvent", "EarningsCalendarEvent"] if event_type == "earnings" else []),
            "symbol": symbol,
            "eventId": event_id,
            "eventType": event_type,
            "eventStatus": status,
            "startsAt": starts_at,
            "endsAt": str(payload.get("endsAt") or fact.get("validTo") or ""),
            "timezone": str(payload.get("timezone") or ""),
            "allDay": bool(payload.get("allDay")),
            "importance": int(payload.get("importance") or 0),
            "provider": str(payload.get("source") or "investment-calendar"),
            "sourceUrl": str(payload.get("sourceUrl") or ""),
            "sourceFactId": str(fact.get("factId") or ""),
            "sourceFactRevision": str(fact.get("revision") or ""),
            "sourceEventId": str(fact.get("sourceEventId") or ""),
            "qualityState": str(fact.get("qualityState") or "unknown"),
            "calendarScheduleEligible": schedule_eligible,
            "eventDaysUntil": days_until,
            "eventWithinReviewWindow": within_review_window,
            **time_contract,
        })
        props = {
            "source": "reasoning-source-fact",
            "polarity": "context",
            "sourceFactId": str(fact.get("factId") or ""),
            "sourceFactRevision": str(fact.get("revision") or ""),
            "aiInfluenceLabel": "투자 일정: " + str(payload.get("title") or event_id),
        }
        add_relation(graph, stock_id, calendar_id, "HAS_OBSERVATION", weight=1.0, properties=props)
        add_relation(graph, stock_id, calendar_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
        add_relation(graph, calendar_id, stock_id, "AFFECTS", weight=0.8, properties=props)
        add_event_validity_concept(graph, calendar_id, symbol, event_type, time_contract)
