from typing import Dict, Iterable, List

from ..domain.events import (
    MONITORING_ALERTS_DETECTED,
    MONITORING_SNAPSHOT_COLLECTED,
    ONTOLOGY_REASONING_COMPLETED,
)
from ..domain.notifications import NotificationJob, notification_debug_number
from ..domain.portfolio import utc_now_iso


class OntologyDiagnosticsService:
    def __init__(
        self,
        ontology_repository,
        settings: Dict[str, object] = None,
        event_log=None,
        notification_queue=None,
        service_status_provider=None,
    ):
        self.ontology_repository = ontology_repository
        self.settings = settings or {}
        self.event_log = event_log
        self.notification_queue = notification_queue
        self.service_status_provider = service_status_provider

    def status(self, symbols: Iterable[str] = None, limit: int = 80) -> Dict[str, object]:
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        safe_limit = max(1, min(500, int(limit or 80)))
        tbox = self.safe_call("active_tbox_metadata", {})
        rulebox = self.safe_call("rulebox_snapshot", {})
        inference = self.safe_call("inferencebox_snapshot", {}, clean_symbols, safe_limit)
        return {
            "contract": "typedb-ontology-diagnostics-v1",
            "generatedAt": utc_now_iso(),
            "activeGraphStore": "typedb",
            "typedb": self.typedb_settings(),
            "tbox": self.tbox_summary(tbox),
            "rulebox": self.rulebox_summary(rulebox),
            "inferenceBox": self.inferencebox_summary(inference),
            "reasoningBoundary": self.reasoning_boundary(rulebox, inference),
            "latestEvents": self.latest_events(),
            "notificationBoundary": self.notification_boundary(),
            "serviceStatus": self.service_status(),
        }

    def safe_call(self, method_name: str, fallback: Dict[str, object], *args):
        target = getattr(self.ontology_repository, method_name, None)
        if not target:
            return dict(fallback)
        try:
            result = target(*args)
            return result if isinstance(result, dict) else dict(fallback)
        except Exception as error:  # noqa: BLE001 - diagnostics must be non-fatal.
            payload = dict(fallback)
            payload.update({"status": "error", "reason": str(error)[:220]})
            return payload

    def typedb_settings(self) -> Dict[str, object]:
        return {
            "enabled": True,
            "addressConfigured": bool(str(self.settings.get("typeDbAddress") or self.settings.get("typedbAddress") or "").strip()),
            "database": str(self.settings.get("typeDbDatabase") or self.settings.get("typedbDatabase") or "orbit_alpha_ontology"),
            "tlsEnabled": self.string_bool(self.settings.get("typeDbTlsEnabled") or self.settings.get("typedbTlsEnabled")),
        }

    def tbox_summary(self, payload: Dict[str, object]) -> Dict[str, object]:
        return self.pick(payload, [
            "configured",
            "status",
            "source",
            "storeSource",
            "graphStore",
            "reason",
            "entityCount",
            "relationCount",
            "version",
            "fingerprint",
            "updatedAt",
        ])

    def rulebox_summary(self, payload: Dict[str, object]) -> Dict[str, object]:
        summary = self.pick(payload, [
            "configured",
            "saved",
            "status",
            "source",
            "graphStore",
            "reason",
            "engineVersion",
            "ruleCount",
            "conditionCount",
            "derivationCount",
            "versionCount",
            "defaultsFallbackUsed",
            "bootstrapAvailable",
            "bootstrapRuleCount",
            "pythonBootstrapDisabled",
        ])
        profile = payload.get("nativeReasoningProfile")
        if isinstance(profile, dict):
            summary["nativeReasoningProfile"] = self.pick(profile, [
                "status",
                "supportedRuleCount",
                "unsupportedRuleCount",
                "functionCount",
                "relationTypeCount",
                "reason",
            ])
        return summary

    def inferencebox_summary(self, payload: Dict[str, object]) -> Dict[str, object]:
        summary = self.pick(payload, [
            "configured",
            "saved",
            "status",
            "source",
            "graphStore",
            "reasoningMode",
            "querySource",
            "typedbReadStatus",
            "typedbReadReason",
            "reason",
            "entityCount",
            "relationCount",
            "traceCount",
            "nativeEntityCount",
            "nativeRelationCount",
            "nativeTraceCount",
            "nativeTypeDbReasoningUsed",
            "typedbBootstrapReasoningUsed",
            "pythonBootstrapDisabled",
            "inferenceGenerationId",
            "inferenceGenerationAt",
            "generationScoped",
            "generationCount",
            "inactiveGenerationEntityCount",
            "inactiveGenerationRelationCount",
            "ignoredNonNativeRelationCount",
            "ignoredNonNativeTraceCount",
            "symbols",
        ])
        summary["relationTypes"] = sorted(set(
            str(item.get("type") or item.get("relationType") or "")
            for item in (payload.get("relations") or [])
            if isinstance(item, dict) and str(item.get("type") or item.get("relationType") or "")
        ))[:20]
        summary["recentTraces"] = [
            self.pick(item, ["id", "ruleId", "label", "symbol", "confidence", "inferenceGenerationId"])
            for item in (payload.get("traces") or [])[:5]
            if isinstance(item, dict)
        ]
        return summary

    def reasoning_boundary(self, rulebox: Dict[str, object], inference: Dict[str, object]) -> Dict[str, object]:
        mode = str(inference.get("reasoningMode") or rulebox.get("reasoningMode") or "").strip()
        native_used = bool(inference.get("nativeTypeDbReasoningUsed"))
        bootstrap_used = bool(inference.get("typedbBootstrapReasoningUsed"))
        status = "ok"
        if str(inference.get("status") or "") == "error":
            status = "error"
        elif bootstrap_used and not native_used:
            status = "error"
        elif not native_used:
            status = "warning"
        return {
            "status": status,
            "reasoningMode": mode,
            "nativeTypeDbReasoningUsed": native_used,
            "typedbBootstrapReasoningUsed": bootstrap_used,
            "nativeTypeDbReasoningReady": bool((rulebox.get("nativeReasoningProfile") or {}).get("status") in {"ready", "partial"})
            if isinstance(rulebox.get("nativeReasoningProfile"), dict)
            else False,
            "interpretation": self.reasoning_interpretation(mode, native_used, bootstrap_used),
        }

    def reasoning_interpretation(self, mode: str, native_used: bool, bootstrap_used: bool) -> str:
        if native_used:
            return "TypeDB RuleBox materialization produced InferenceBox relations."
        if bootstrap_used:
            return "Legacy in-memory inference is present but no longer accepted for investment judgement."
        if mode:
            return "TypeDB RuleBox materialization is required, but no InferenceBox output was confirmed."
        return "No TypeDB InferenceBox output was confirmed."

    def latest_events(self) -> Dict[str, object]:
        if not self.event_log or not hasattr(self.event_log, "latest_events_by_name"):
            return {}
        names = [MONITORING_SNAPSHOT_COLLECTED, MONITORING_ALERTS_DETECTED, ONTOLOGY_REASONING_COMPLETED]
        try:
            events = self.event_log.latest_events_by_name(names)
        except Exception as error:  # noqa: BLE001 - diagnostics must continue without event log.
            return {"status": "error", "reason": str(error)[:180]}
        return {name: self.event_summary(events.get(name)) for name in names if events.get(name)}

    def notification_boundary(self) -> Dict[str, object]:
        latest_alert = self.latest_alert_event()
        recent_jobs = self.recent_notification_jobs()
        latest_alert_jobs = [job for job in recent_jobs if latest_alert and job.source_event_id == latest_alert.event_id]
        inference_missing_jobs = [job for job in recent_jobs if job.message_type == "ontologyInferenceMissing"]
        missing_source = [job for job in recent_jobs if not str(job.source_event_id or "").strip()]
        status = "ok"
        reason = ""
        if latest_alert and not latest_alert_jobs:
            status = "warning"
            reason = "latest monitoring.alerts_detected event has no recent notification job"
        if not latest_alert and recent_jobs:
            status = "unknown"
            reason = "recent notification jobs exist but no monitoring.alerts_detected event was found"
        return {
            "status": status,
            "reason": reason,
            "latestAlertEvent": self.event_summary(latest_alert),
            "jobsForLatestAlert": [self.job_summary(job) for job in latest_alert_jobs],
            "recentOntologyInferenceMissing": [self.job_summary(job) for job in inference_missing_jobs[:8]],
            "recentJobsMissingSourceEventId": len(missing_source),
            "recentJobCount": len(recent_jobs),
            "checks": [
                "monitoring.alerts_detected and notification jobs are expected to be recorded in one operational DB transaction.",
                "A non-zero jobsForLatestAlert confirms the event-to-outbox boundary for the latest alert event.",
            ],
        }

    def latest_alert_event(self):
        if not self.event_log or not hasattr(self.event_log, "latest_events_by_name"):
            return None
        try:
            return self.event_log.latest_events_by_name([MONITORING_ALERTS_DETECTED]).get(MONITORING_ALERTS_DETECTED)
        except Exception:  # noqa: BLE001 - diagnostics must continue without event log.
            return None

    def recent_notification_jobs(self) -> List[NotificationJob]:
        if not self.notification_queue:
            return []
        try:
            if hasattr(self.notification_queue, "recent"):
                return list(self.notification_queue.recent(limit=80))
            if hasattr(self.notification_queue, "jobs"):
                return list(reversed(self.notification_queue.jobs()))[:80]
        except Exception:  # noqa: BLE001 - diagnostics must continue without queue data.
            return []
        return []

    def service_status(self) -> Dict[str, object]:
        if not self.service_status_provider:
            return {}
        try:
            payload = self.service_status_provider()
            return payload if isinstance(payload, dict) else {}
        except Exception as error:  # noqa: BLE001 - diagnostics must continue without service status.
            return {"status": "error", "reason": str(error)[:180]}

    def event_summary(self, event) -> Dict[str, object]:
        if not event:
            return {}
        payload = dict(getattr(event, "payload", {}) or {})
        return {
            "eventId": str(getattr(event, "event_id", "") or ""),
            "name": str(getattr(event, "name", "") or ""),
            "aggregateId": str(getattr(event, "aggregate_id", "") or ""),
            "occurredAt": str(getattr(event, "occurred_at", "") or ""),
            "count": payload.get("count"),
            "accountIds": list(payload.get("accountIds") or [])[:20],
            "symbols": list(payload.get("symbols") or [])[:50],
            "status": str(payload.get("status") or ""),
        }

    def job_summary(self, job: NotificationJob) -> Dict[str, object]:
        return {
            "jobId": job.job_id,
            "notificationNumber": notification_debug_number(job.job_id),
            "messageType": job.message_type,
            "status": job.status,
            "attempts": job.attempts,
            "sourceEventId": job.source_event_id,
            "sourceEventName": job.source_event_name,
            "lastError": job.last_error,
            "createdAt": job.created_at,
            "updatedAt": job.updated_at,
        }

    def pick(self, payload: Dict[str, object], keys: List[str]) -> Dict[str, object]:
        source = payload if isinstance(payload, dict) else {}
        return {key: source.get(key) for key in keys if key in source}

    def string_bool(self, value: object) -> bool:
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
