from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from ..domain.official_calendar import OfficialCalendarEvent
from ..domain.portfolio import utc_now_iso


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int = 1, upper: int = 100000) -> int:
    try:
        parsed = int(float(str((settings or {}).get(key) or "").strip()))
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, min(upper, parsed))


class OfficialCalendarSyncService:
    def __init__(
        self,
        calendar_service,
        sources: Iterable[object] = None,
        settings: Dict[str, object] = None,
        now=None,
    ):
        self.calendar_service = calendar_service
        self.sources = list(sources or [])
        self.settings = dict(settings or {})
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.last_synced_at = None

    def enabled(self) -> bool:
        return truthy(self.settings.get("investmentCalendarOfficialMacroSyncEnabled"), True)

    def interval_seconds(self) -> int:
        hours = int_setting(self.settings, "investmentCalendarOfficialMacroSyncIntervalHours", 12, 1, 168)
        return hours * 3600

    def due(self) -> bool:
        if not self.enabled() or not self.sources:
            return False
        if not self.last_synced_at:
            return True
        return self.now() - self.last_synced_at >= timedelta(seconds=self.interval_seconds())

    def run_due(self) -> Dict[str, object]:
        if not self.due():
            return {"status": "not-due", "savedCount": 0, "fetchedCount": 0}
        return self.run_once(force=True)

    def run_once(self, force: bool = False) -> Dict[str, object]:
        if not self.enabled():
            return {"status": "disabled", "savedCount": 0, "fetchedCount": 0}
        if not force and not self.due():
            return {"status": "not-due", "savedCount": 0, "fetchedCount": 0}
        fetched = 0
        saved = 0
        errors: List[str] = []
        event_ids: List[str] = []
        source_results: List[Dict[str, object]] = []
        seen = set()
        for source in self.sources:
            label = source.__class__.__name__
            try:
                events = source.events()
            except Exception as error:  # noqa: BLE001 - one official source should not block reminders.
                errors.append(label + ": " + str(error)[:240])
                source_results.append({"source": label, "status": "error", "fetchedCount": 0, "savedCount": 0})
                continue
            source_saved = 0
            fetched += len(events)
            for event in events:
                if not isinstance(event, OfficialCalendarEvent) or event.event_id in seen:
                    continue
                seen.add(event.event_id)
                try:
                    self.calendar_service.save_event(event.to_calendar_payload())
                    saved += 1
                    source_saved += 1
                    event_ids.append(event.event_id)
                except Exception as error:  # noqa: BLE001 - keep syncing other events.
                    errors.append(event.event_id + ": " + str(error)[:240])
            source_results.append({
                "source": label,
                "status": "ok",
                "fetchedCount": len(events),
                "savedCount": source_saved,
            })
        self.last_synced_at = self.now()
        status = "ok"
        if errors and saved:
            status = "partial"
        elif errors:
            status = "error"
        return {
            "status": status,
            "generatedAt": utc_now_iso(),
            "fetchedCount": fetched,
            "savedCount": saved,
            "eventIds": event_ids[:200],
            "sources": source_results,
            "errors": errors[:10],
        }
