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
        abox_coverage = self.abox_coverage(clean_symbols)
        return {
            "contract": "typedb-ontology-diagnostics-v1",
            "generatedAt": utc_now_iso(),
            "activeGraphStore": "typedb",
            "typedb": self.typedb_settings(),
            "tbox": self.tbox_summary(tbox),
            "rulebox": self.rulebox_summary(rulebox),
            "aboxCoverage": abox_coverage,
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
            "reasonCode",
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
            "reasonCode",
            "engineVersion",
            "ruleCount",
            "conditionCount",
            "derivationCount",
            "versionCount",
            "defaultsFallbackUsed",
            "bootstrapAvailable",
            "bootstrapRuleCount",
            "pythonBootstrapDisabled",
            "ruleboxRulesHash",
            "ruleboxShortHash",
            "ruleboxRuleCount",
            "ruleboxConditionCount",
            "ruleboxDerivationCount",
            "ruleboxEngineVersion",
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
            "reasonCode",
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
            "ruleboxRulesHash",
            "ruleboxShortHash",
            "ruleboxRuleCount",
            "ruleboxConditionCount",
            "ruleboxDerivationCount",
            "ruleboxEngineVersion",
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

    def abox_coverage(self, symbols: List[str] = None) -> Dict[str, object]:
        if not hasattr(self.ontology_repository, "read_entity_rows") or not hasattr(self.ontology_repository, "read_relation_rows"):
            return {"status": "unavailable", "reason": "repository does not expose ABox row reads"}
        try:
            entities = self.ontology_repository.read_entity_rows(["ABox"])
            relations = self.ontology_repository.read_relation_rows(["ABox"])
        except Exception as error:  # noqa: BLE001 - diagnostics must stay available.
            return {"status": "error", "reason": str(error)[:220]}
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        stock_symbols = self.abox_symbols(entities, relations)
        if clean_symbols:
            stock_symbols = [item for item in stock_symbols if item in clean_symbols]
        relation_types_by_symbol: Dict[str, set] = {symbol: set() for symbol in stock_symbols}
        entity_classes_by_symbol: Dict[str, set] = {symbol: set() for symbol in stock_symbols}
        for entity in entities:
            symbol = self.row_symbol(entity)
            if symbol in entity_classes_by_symbol:
                tbox_class = str(entity.get("tboxClass") or "")
                kind = str(entity.get("kind") or entity.get("nodeKind") or "")
                if tbox_class:
                    entity_classes_by_symbol[symbol].add(tbox_class)
                if kind:
                    entity_classes_by_symbol[symbol].add(kind)
        for relation in relations:
            symbol = self.row_symbol(relation) or self.symbol_from_relation_endpoints(relation)
            if symbol in relation_types_by_symbol:
                relation_type = str(relation.get("type") or relation.get("relationType") or "").upper().strip()
                if relation_type:
                    relation_types_by_symbol[symbol].add(relation_type)
        required = {
            "price": {"HAS_PRICE"},
            "trendPath": {"HAS_PRICE_PATH", "HAS_TREND_PHASE", "HAS_TREND_TRANSITION"},
            "tradeFlow": {"HAS_TRADE_FLOW"},
            "liquidity": {"HAS_LIQUIDITY_PROFILE"},
            "execution": {"HAS_EXECUTION_METRIC", "HAS_EXECUTION_CAPACITY"},
            "dataQuality": {"HAS_DATA_QUALITY"},
            "externalEvidence": {"HAS_RESEARCH_EVIDENCE", "HAS_EVENT_EVIDENCE", "HAS_DISCLOSURE", "MENTIONS"},
        }
        rows = []
        total_expected = len(required)
        total_present = 0
        for symbol in stock_symbols:
            relation_types = relation_types_by_symbol.get(symbol) or set()
            present = sorted(name for name, relation_set in required.items() if relation_types.intersection(relation_set))
            missing = sorted(name for name in required if name not in present)
            total_present += len(present)
            rows.append({
                "symbol": symbol,
                "present": present,
                "missing": missing,
                "relationTypes": sorted(relation_types)[:40],
                "entityClasses": sorted(entity_classes_by_symbol.get(symbol) or [])[:30],
                "coverageRatio": round(len(present) / total_expected, 3) if total_expected else 1.0,
            })
        coverage_ratio = round(total_present / max(1, total_expected * max(1, len(stock_symbols))), 3)
        status = "ok" if stock_symbols and coverage_ratio >= 0.75 else ("warning" if stock_symbols else "empty")
        return {
            "status": status,
            "entityCount": len(entities),
            "relationCount": len(relations),
            "symbolCount": len(stock_symbols),
            "coverageRatio": coverage_ratio,
            "requiredCategories": sorted(required.keys()),
            "symbols": rows[:80],
        }

    def abox_symbols(self, entities: List[Dict[str, object]], relations: List[Dict[str, object]]) -> List[str]:
        values = set()
        for row in list(entities or []) + list(relations or []):
            symbol = self.row_symbol(row) or self.symbol_from_relation_endpoints(row)
            if symbol:
                values.add(symbol)
        return sorted(values)

    def row_symbol(self, row: Dict[str, object]) -> str:
        if not isinstance(row, dict):
            return ""
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol:
            return symbol
        row_id = str(row.get("id") or "").upper().strip()
        return row_id.split("STOCK:", 1)[1].split(":", 1)[0] if "STOCK:" in row_id else ""

    def symbol_from_relation_endpoints(self, row: Dict[str, object]) -> str:
        if not isinstance(row, dict):
            return ""
        for key in ["source", "target", "sourceId", "targetId"]:
            value = str(row.get(key) or "").upper().strip()
            if value.startswith("STOCK:"):
                return value.split("STOCK:", 1)[1].split(":", 1)[0]
        return ""

    def reasoning_boundary(self, rulebox: Dict[str, object], inference: Dict[str, object]) -> Dict[str, object]:
        mode = str(inference.get("reasoningMode") or rulebox.get("reasoningMode") or "").strip()
        native_used = bool(inference.get("nativeTypeDbReasoningUsed"))
        bootstrap_used = bool(inference.get("typedbBootstrapReasoningUsed"))
        rulebox_hash = str(rulebox.get("ruleboxRulesHash") or "").strip()
        inference_hash = str(inference.get("ruleboxRulesHash") or "").strip()
        hash_match = bool(rulebox_hash and inference_hash and rulebox_hash == inference_hash)
        hash_status = "unknown"
        if rulebox_hash and inference_hash:
            hash_status = "ok" if hash_match else "stale"
        status = "ok"
        if str(inference.get("status") or "") == "error":
            status = "error"
        elif bootstrap_used and not native_used:
            status = "error"
        elif rulebox_hash and inference_hash and not hash_match:
            status = "warning"
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
            "ruleboxHashStatus": hash_status,
            "ruleboxRulesHash": rulebox_hash,
            "inferenceRuleboxRulesHash": inference_hash,
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
