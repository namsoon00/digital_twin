from digital_twin.modules.investment_calendar.domain.event_types import INVESTMENT_CALENDAR_EVENT_SAVED, INVESTMENT_CALENDAR_EVENT_REMOVED, INVESTMENT_CALENDAR_REMINDER_DUE
from digital_twin.shared_kernel.events import DomainEvent
from typing import Dict, Iterable, List, Mapping








def investment_calendar_event_saved_event(calendar_event) -> DomainEvent:
    payload = calendar_event.to_dict() if hasattr(calendar_event, "to_dict") else dict(calendar_event or {})
    symbols = list(payload.get("symbols") or [])
    markets = list(payload.get("markets") or [])
    return DomainEvent(
        name=INVESTMENT_CALENDAR_EVENT_SAVED,
        aggregate_id=str(payload.get("eventId") or ""),
        payload={
            "event": payload,
            "eventId": str(payload.get("eventId") or ""),
            "title": str(payload.get("title") or ""),
            "eventType": str(payload.get("eventType") or ""),
            "startsAt": str(payload.get("startsAt") or ""),
            "importance": int(payload.get("importance") or 0),
            "symbols": symbols[:100],
            "markets": markets[:50],
            "changedSymbols": symbols[:100],
            "changedCount": len(symbols),
        },
    )


def investment_calendar_event_removed_event(event_id: str) -> DomainEvent:
    return DomainEvent(
        name=INVESTMENT_CALENDAR_EVENT_REMOVED,
        aggregate_id=str(event_id or ""),
        payload={"eventId": str(event_id or "")},
    )


def investment_calendar_reminder_due_event(reminders: Iterable[object]) -> DomainEvent:
    items = [item.to_dict() if hasattr(item, "to_dict") else dict(item or {}) for item in reminders or []]
    event_ids = sorted(set(str(item.get("eventId") or "") for item in items if str(item.get("eventId") or "")))
    symbols = sorted(set(str(symbol or "").upper().strip() for item in items for symbol in (item.get("symbols") or []) if str(symbol or "").strip()))
    return DomainEvent(
        name=INVESTMENT_CALENDAR_REMINDER_DUE,
        aggregate_id="calendar:" + (",".join(event_ids) or "none")[:180],
        payload={
            "count": len(items),
            "eventIds": event_ids[:100],
            "symbols": symbols[:100],
            "reminders": items[:100],
        },
    )
