import hashlib
from datetime import datetime, timezone
from typing import Dict, Iterable, List

from digital_twin.modules.investment_calendar.domain.official_calendar import OfficialCalendarEvent
from digital_twin.modules.investment_calendar.application.collection_schedule import collection_attempt, collection_due
from digital_twin.modules.investment_calendar.domain.investment_calendar import parse_utc
from digital_twin.modules.portfolio.contracts import utc_now_iso
from digital_twin.platform.application.runtime_checkpoint import checkpoint_datetime, checkpoint_iso


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
        unsupported_symbols=None,
        source_reader=None,
    ):
        self.calendar_service = calendar_service
        self.sources = list(sources or [])
        self.candidate_service = candidate_service
        self.settings = dict(settings or {})
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.checkpoint_store = checkpoint_store
        self.unsupported_symbols = list(unsupported_symbols or [])
        self.source_reader = source_reader
        self.source_states = {}
        self.target_symbols = []
        self._legacy_source_states = {}
        self._source_read_at = None
        self._source_read_error = ""
        self._plan_changed = False
        self.last_synced_at = None
        self.last_maintenance_at = None
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
            self.last_maintenance_at = checkpoint_datetime(payload, "lastMaintenanceAt") or self.last_synced_at
            result = payload.get("lastResult")
            self.last_result = dict(result) if isinstance(result, dict) else {}
            self.target_symbols = list(self.last_result.get("targetSymbols") or [])
            self.source_states = dict(payload.get("sourceStates") or {})
            if not self.source_states and self.last_synced_at:
                for row in self.last_result.get("sources") or []:
                    failed = row.get("status") in {"error", "partial"}
                    state = collection_attempt({}, self.last_synced_at, self.interval_seconds(), failed)
                    self._legacy_source_states[row.get("source")] = {**state, "result": dict(row)}
        except Exception as error:  # noqa: BLE001 - a lost checkpoint must not block schedule refresh.
            self.checkpoint_error = str(error)[:240]

    def save_checkpoint(self, result: Dict[str, object]) -> None:
        if not self.checkpoint_store:
            return
        payload = {
            "lastRunAt": checkpoint_iso(self.last_synced_at),
            "lastMaintenanceAt": checkpoint_iso(self.last_maintenance_at),
            "lastResult": dict(result or {}),
            "sourceStates": dict(self.source_states),
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

    def refresh_sources(self):
        if not self.source_reader:
            return
        now = self.now()
        if self._source_read_at and (now - self._source_read_at).total_seconds() < 60:
            return
        self._source_read_at = now
        try:
            plan = self.source_reader()
            sources = list(plan.get("sources") or [])
            unsupported = list(plan.get("unsupportedSymbols") or [])
            targets = list(plan.get("targetSymbols") or [])
            self._plan_changed = self._plan_changed or targets != self.target_symbols or unsupported != self.unsupported_symbols
            self.sources, self.unsupported_symbols, self.target_symbols = sources, unsupported, targets
            self._source_read_error = ""
        except Exception as error:
            # A portfolio read failure is not an empty portfolio. Preserve the
            # preceding plan and expose the failure without private DB details.
            self._source_read_error = "calendar target refresh failed: " + type(error).__name__

    def source_key(self, source):
        label = getattr(source, "label", source.__class__.__name__)
        targets = getattr(source, "target_symbols", None)
        scope = hashlib.sha256("|".join(sorted(targets)).encode()).hexdigest()[:16] if targets else ""
        return label + (":" + scope if scope else "")

    def source_state(self, source):
        key = self.source_key(source)
        if key not in self.source_states:
            label = getattr(source, "label", source.__class__.__name__)
            if label in self._legacy_source_states:
                self.source_states[key] = self._legacy_source_states.pop(label)
        return self.source_states.get(key, {})

    def due(self) -> bool:
        self.restore_checkpoint()
        self.refresh_sources()
        if not self.enabled():
            return False
        return bool(self._source_read_error or self._plan_changed) or any(collection_due(self.source_state(source), self.now()) for source in self.sources)

    def run_due(self) -> Dict[str, object]:
        if not self.due():
            return {"status": "not-due", "savedCount": 0, "fetchedCount": 0}
        return self.run_once()

    def run_once(self, force: bool = False) -> Dict[str, object]:
        self.restore_checkpoint()
        self.refresh_sources()
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
        errors: List[str] = [self._source_read_error] if self._source_read_error else []
        event_ids: List[str] = []
        source_results: List[Dict[str, object]] = []
        seen = set()
        maintenance_due = force or not self.last_maintenance_at or (self.now() - self.last_maintenance_at).total_seconds() >= self.interval_seconds()
        if maintenance_due:
            self.last_maintenance_at = self.now()
        if maintenance_due and hasattr(self.calendar_service, "quarantine_unverified_automatic_events"):
            try:
                quarantine_result = self.calendar_service.quarantine_unverified_automatic_events()
                quarantined = int((quarantine_result or {}).get("quarantinedCount") or 0)
            except Exception as error:  # noqa: BLE001 - source synchronization can still proceed.
                errors.append("legacy-auto-quarantine: " + str(error)[:240])
        if maintenance_due and self.candidate_service and hasattr(self.candidate_service, "reconcile_all_candidates"):
            try:
                candidate_repair = self.candidate_service.reconcile_all_candidates()
            except Exception as error:  # noqa: BLE001 - official schedules still have priority.
                errors.append("legacy-candidate-repair: " + str(error)[:240])
        for source in self.sources:
            label = getattr(source, "label", source.__class__.__name__)
            previous = self.source_state(source)
            if not force and not collection_due(previous, self.now()):
                source_results.append(dict(previous.get("result") or {"source": label, "status": "pending"}))
                continue
            source_errors = []
            try:
                events = source.events()
            except Exception as error:  # noqa: BLE001 - one official source should not block reminders.
                errors.append(label + ": " + str(error)[:240])
                state = collection_attempt(previous, self.now(), self.interval_seconds(), failed=True)
                row = {"source": label, "status": "error", "fetchedCount": 0, "savedCount": 0,
                       "coverageState": "collection-error", **state}
                self.source_states[self.source_key(source)] = {**state, "result": row}
                source_results.append(row)
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
                    source_errors.append(event.event_id)
            state = collection_attempt(previous, self.now(), self.interval_seconds(), failed=bool(source_errors))
            upcoming = [event for event in events if parse_utc(event.starts_at) and parse_utc(event.starts_at) >= self.now()]
            disabled = callable(getattr(source, "enabled", None)) and not source.enabled()
            row = {
                "source": label,
                "status": "partial" if source_errors else "disabled" if disabled else "ok",
                "fetchedCount": len(events),
                "savedCount": source_saved,
                "upcomingCount": len(upcoming),
                "dateOnlyCount": sum(event.all_day for event in upcoming),
                "coverageState": "save-error" if source_errors else "disabled" if disabled else "scheduled" if upcoming else "no-upcoming-confirmed",
                **state,
            }
            self.source_states[self.source_key(source)] = {**state, "result": row}
            source_results.append(row)
        # A removed target is not future work; do not grow checkpoints forever.
        active_keys = {self.source_key(source) for source in self.sources}
        self.source_states = {key: value for key, value in self.source_states.items() if key in active_keys}
        self.last_synced_at = self.now()
        status = "ok"
        failed_sources = [row for row in source_results if row.get("status") in {"error", "partial"}]
        if (errors or failed_sources) and any(row.get("status") == "ok" for row in source_results):
            status = "partial"
        elif errors or failed_sources:
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
            "unsupportedSymbols": self.unsupported_symbols,
            "targetSymbols": self.target_symbols,
            "errors": errors[:10],
        }
        self.last_result = dict(result)
        self.save_checkpoint(result)
        self._plan_changed = False
        return result

    def status(self) -> Dict[str, object]:
        self.restore_checkpoint()
        return {
            "enabled": self.enabled(),
            "due": self.due(),
            "intervalSeconds": self.interval_seconds(),
            "lastRunAt": checkpoint_iso(self.last_synced_at),
            "lastResult": dict(self.last_result or {}),
            "sourceStates": dict(self.source_states),
            "checkpointStatus": "error" if self.checkpoint_error else ("persisted" if self.checkpoint_store else "memory"),
            "checkpointError": self.checkpoint_error,
        }
