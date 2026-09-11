from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from digital_twin.modules.investment_calendar.domain.official_calendar import OfficialCalendarEvent
from digital_twin.domain.portfolio import utc_now_iso
from digital_twin.application.runtime_checkpoint import checkpoint_datetime, checkpoint_iso


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}
OFFICIAL_CALENDAR_SYNC_CHECKPOINT_ID = "investment-calendar:official-sync:v1"


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
        candidate_service=None,
        settings: Dict[str, object] = None,
        now=None,
        checkpoint_store=None,
    ):
        self.calendar_service = calendar_service
        self.sources = list(sources or [])
        self.candidate_service = candidate_service
        self.settings = dict(settings or {})
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.checkpoint_store = checkpoint_store
        self.last_synced_at = None
        self.last_result: Dict[str, object] = {}
        self.checkpoint_error = ""
        self._checkpoint_loaded = False

    def restore_checkpoint(self) -> None:
        if self._checkpoint_loaded:
            return
        self._checkpoint_loaded = True
        if not self.checkpoint_store:
            return
        try:
            payload = self.checkpoint_store.load(OFFICIAL_CALENDAR_SYNC_CHECKPOINT_ID) or {}
            self.last_synced_at = checkpoint_datetime(payload, "lastRunAt")
            result = payload.get("lastResult")
            self.last_result = dict(result) if isinstance(result, dict) else {}
        except Exception as error:  # noqa: BLE001 - a lost checkpoint must not block schedule refresh.
            self.checkpoint_error = str(error)[:240]

    def save_checkpoint(self, result: Dict[str, object]) -> None:
        if not self.checkpoint_store:
            return
        payload = {
            "lastRunAt": checkpoint_iso(self.last_synced_at),
            "lastResult": dict(result or {}),
        }
        try:
            self.checkpoint_store.save(OFFICIAL_CALENDAR_SYNC_CHECKPOINT_ID, payload)
            self.checkpoint_error = ""
        except Exception as error:  # noqa: BLE001 - events already persisted remain valid.
            self.checkpoint_error = str(error)[:240]
            result["checkpointError"] = self.checkpoint_error

    def enabled(self) -> bool:
        return truthy(self.settings.get("investmentCalendarOfficialMacroSyncEnabled"), True)

    def interval_seconds(self) -> int:
        hours = int_setting(self.settings, "investmentCalendarOfficialMacroSyncIntervalHours", 12, 1, 168)
        return hours * 3600

    def due(self) -> bool:
        self.restore_checkpoint()
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
        superseded = 0
        quarantined = 0
        candidate_repair: Dict[str, object] = {}
        candidate_superseded = 0
        errors: List[str] = []
        event_ids: List[str] = []
        source_results: List[Dict[str, object]] = []
        seen = set()
        if hasattr(self.calendar_service, "quarantine_unverified_automatic_events"):
            try:
                quarantine_result = self.calendar_service.quarantine_unverified_automatic_events()
                quarantined = int((quarantine_result or {}).get("quarantinedCount") or 0)
            except Exception as error:  # noqa: BLE001 - source synchronization can still proceed.
                errors.append("legacy-auto-quarantine: " + str(error)[:240])
        if self.candidate_service and hasattr(self.candidate_service, "reconcile_all_candidates"):
            try:
                candidate_repair = self.candidate_service.reconcile_all_candidates()
            except Exception as error:  # noqa: BLE001 - official schedules still have priority.
                errors.append("legacy-candidate-repair: " + str(error)[:240])
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
                    if hasattr(self.calendar_service, "reconcile_unverified_events"):
                        reconciliation = self.calendar_service.reconcile_unverified_events(event)
                        superseded += int((reconciliation or {}).get("supersededCount") or 0)
                    if self.candidate_service and hasattr(self.candidate_service, "reconcile_official_event"):
                        candidate_result = self.candidate_service.reconcile_official_event(event)
                        candidate_superseded += int((candidate_result or {}).get("superseded") or 0)
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
        result = {
            "status": status,
            "generatedAt": utc_now_iso(),
            "fetchedCount": fetched,
            "savedCount": saved,
            "supersededCount": superseded,
            "quarantinedCount": quarantined,
            "candidateRepair": candidate_repair,
            "candidateSupersededCount": candidate_superseded,
            "eventIds": event_ids[:200],
            "sources": source_results,
            "errors": errors[:10],
        }
        self.last_result = dict(result)
        self.save_checkpoint(result)
        return result

    def status(self) -> Dict[str, object]:
        self.restore_checkpoint()
        return {
            "enabled": self.enabled(),
            "due": self.due(),
            "intervalSeconds": self.interval_seconds(),
            "lastRunAt": checkpoint_iso(self.last_synced_at),
            "lastResult": dict(self.last_result or {}),
            "checkpointStatus": "error" if self.checkpoint_error else ("persisted" if self.checkpoint_store else "memory"),
            "checkpointError": self.checkpoint_error,
        }
