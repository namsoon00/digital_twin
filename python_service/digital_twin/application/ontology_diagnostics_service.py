from typing import Dict, Iterable, List

from ..domain.events import (
    MONITORING_ALERTS_DETECTED,
    MONITORING_SNAPSHOT_COLLECTED,
    ONTOLOGY_REASONING_COMPLETED,
)
from ..domain.notifications import NotificationJob, notification_debug_number
from ..domain.ontology_inference_ledger import inference_trace_ledger_payload
from ..domain.portfolio import utc_now_iso
from ..domain.portfolio_ontology_coverage import CATEGORY_LABELS, CATEGORY_RELATIONS


class OntologyDiagnosticsService:
    def __init__(
        self,
        ontology_repository,
        settings: Dict[str, object] = None,
        event_log=None,
        notification_queue=None,
        service_status_provider=None,
        strategy_proposal_service=None,
        decision_episode_store=None,
        projection_run_store=None,
    ):
        self.ontology_repository = ontology_repository
        self.settings = settings or {}
        self.event_log = event_log
        self.notification_queue = notification_queue
        self.service_status_provider = service_status_provider
        self.strategy_proposal_service = strategy_proposal_service
        self.decision_episode_store = decision_episode_store
        self.projection_run_store = projection_run_store

    def status(
        self,
        symbols: Iterable[str] = None,
        limit: int = 80,
        world_id: str = "",
    ) -> Dict[str, object]:
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        safe_limit = max(1, min(500, int(limit or 80)))
        clean_world_id = str(world_id or "").strip()
        tbox = self.safe_call("active_tbox_metadata", {})
        rulebox = self.safe_call("rulebox_snapshot", {})
        inference = self.safe_call(
            "inferencebox_snapshot",
            {},
            clean_symbols,
            safe_limit,
            world_id=clean_world_id,
        )
        abox_storage = self.safe_call("scoped_abox_storage_diagnostics", {}, world_id=clean_world_id)
        abox_coverage = self.abox_coverage(
            clean_symbols,
            world_id=clean_world_id,
            storage=abox_storage,
        )
        decision_performance = self.decision_performance_boundary(clean_symbols)
        notification_boundary = self.notification_boundary()
        runtime_observability = self.runtime_observation_boundary(clean_world_id)
        inference_summary = self.inferencebox_summary(inference)
        latest_runtime_inference = (
            (runtime_observability.get("latest") or {}).get("inference")
            if isinstance((runtime_observability.get("latest") or {}), dict)
            else {}
        )
        if isinstance(latest_runtime_inference, dict):
            runtime_execution = self.pick(latest_runtime_inference, [
                "status",
                "plannedTargetSymbolCount",
                "requestedTargetSymbolCount",
                "targetSymbolCount",
                "notEvaluatedSymbolCount",
                "targetCoverageStatus",
                "candidateRuleCount",
                "executedRuleCount",
                "deferredRuleCount",
                "nativeRuleSelectionApplied",
                "nativeRuleSelectionFallbackReason",
                "executionStatus",
            ])
            timing = latest_runtime_inference.get("nativeRuleTiming")
            if isinstance(timing, dict):
                runtime_execution["nativeRuleTiming"] = self.pick(timing, [
                    "wallClockMs",
                    "executedRuleCount",
                    "incompleteRuleCount",
                    "notApplicableRuleCount",
                    "aggregateRuleElapsedMs",
                    "aggregateQueryDurationMs",
                ])
                runtime_execution["nativeRuleTiming"]["slowestRules"] = [
                    self.pick(item, ["ruleId", "status", "queryComplexity", "queryCount", "elapsedMs", "queryDurationMs"])
                    for item in (timing.get("slowestRules") or [])[:3]
                    if isinstance(item, dict)
                ]
            if runtime_execution:
                inference_summary["runtimeExecution"] = runtime_execution
        return {
            "contract": "typedb-ontology-diagnostics-v1",
            "generatedAt": utc_now_iso(),
            "activeGraphStore": "typedb",
            "worldId": clean_world_id,
            "worlds": self.safe_list_call("list_ontology_worlds"),
            "typedb": self.typedb_settings(),
            "tbox": self.tbox_summary(tbox),
            "rulebox": self.rulebox_summary(rulebox),
            "aboxStorage": self.abox_storage_summary(abox_storage),
            "aboxCoverage": abox_coverage,
            "inferenceBox": inference_summary,
            "reasoningBoundary": self.reasoning_boundary(rulebox, inference),
            "runtimeObservability": runtime_observability,
            "ruleboxQuality": self.rulebox_quality_boundary(rulebox, inference, decision_performance),
            "latestEvents": self.latest_events(),
            "notificationBoundary": notification_boundary,
            "alertPipeline": self.alert_pipeline_boundary(inference, notification_boundary),
            "strategyProposalBoundary": self.strategy_proposal_boundary(),
            "decisionPerformanceBoundary": decision_performance,
            "serviceStatus": self.service_status(),
        }

    def decision_performance_boundary(self, symbols: Iterable[str]) -> Dict[str, object]:
        if not self.decision_episode_store or not hasattr(self.decision_episode_store, "performance"):
            return {"status": "unavailable"}
        clean_symbols = list(symbols or [])
        try:
            result = self.decision_episode_store.performance(symbol=clean_symbols[0] if len(clean_symbols) == 1 else "", limit=500)
        except Exception as error:  # noqa: BLE001 - diagnostics must remain available without feedback history.
            return {"status": "error", "reason": str(error)[:180]}
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        rule_outcomes = []
        for row in result.get("byRule") or []:
            if not isinstance(row, dict):
                continue
            rule_outcomes.append({
                "ruleId": str(row.get("key") or ""),
                "outcomeCount": int(row.get("outcomeCount") or 0),
                "decisiveOutcomeCount": int(row.get("decisiveOutcomeCount") or 0),
                "calibrationEligibleOutcomeCount": int(row.get("calibrationEligibleOutcomeCount") or 0),
                "corroborationState": str(row.get("corroborationState") or "insufficient-history"),
                "averageActionAdjustedReturnPct": float(row.get("averageActionAdjustedReturnPct") or 0),
                "promotionEligible": bool(row.get("promotionEligible")),
            })
        return {
            "status": str(result.get("status") or "insufficient-data"),
            "episodeCount": int(result.get("episodeCount") or 0),
            "outcomeCount": int(result.get("outcomeCount") or 0),
            "outcomeCoveragePct": float(result.get("outcomeCoveragePct") or 0),
            "corroborationState": str(summary.get("corroborationState") or "insufficient-history"),
            "actionReturnState": str(summary.get("actionReturnState") or "unavailable"),
            "averageActionAdjustedReturnPct": float(summary.get("averageActionAdjustedReturnPct") or 0),
            "sampleStatus": str(summary.get("sampleStatus") or "insufficient-sample"),
            "promotionEligible": bool(summary.get("promotionEligible")),
            "ruleCount": len(result.get("byRule") or []),
            "hypothesisCount": len(result.get("byHypothesis") or []),
            "ruleOutcomes": rule_outcomes[:80],
            "automaticDeployment": False,
        }

    def runtime_observation_boundary(self, world_id: str = "") -> Dict[str, object]:
        if not self.projection_run_store or not hasattr(self.projection_run_store, "runtime_summary"):
            return {"status": "unavailable", "reason": "Projection runtime audit store is not configured."}
        try:
            kwargs = {"limit": int(self.settings.get("ontologyRuntimeAuditWindowRuns") or 40)}
            if str(world_id or "").strip():
                kwargs["world_id"] = str(world_id).strip()
            try:
                return self.projection_run_store.runtime_summary(**kwargs)
            except TypeError as error:
                if "unexpected keyword" not in str(error) and "world_id" not in str(error):
                    raise
                return self.projection_run_store.runtime_summary(
                    limit=int(self.settings.get("ontologyRuntimeAuditWindowRuns") or 40),
                )
        except Exception as error:  # noqa: BLE001 - diagnostics must remain available without audit history.
            return {"status": "error", "reason": str(error)[:180]}

    def rulebox_quality_boundary(
        self,
        rulebox: Dict[str, object],
        inference: Dict[str, object],
        performance: Dict[str, object],
    ) -> Dict[str, object]:
        """Join native trace coverage with delayed outcome evidence.

        The output is audit-only: a rule cannot be auto-promoted, disabled,
        or edited from a performance result.
        """

        try:
            ledger = inference_trace_ledger_payload(inference, rulebox=rulebox, limit=300)
        except Exception as error:  # noqa: BLE001 - a ledger failure must not hide the live inference summary.
            return {"status": "error", "reason": str(error)[:180], "automaticDeployment": False}
        outcomes = {
            str(item.get("ruleId") or ""): item
            for item in performance.get("ruleOutcomes") or []
            if isinstance(item, dict) and str(item.get("ruleId") or "")
        }
        matched = set(ledger.get("ruleCoverage", {}).get("matchedRuleIds") or [])
        untraced = set(ledger.get("ruleCoverage", {}).get("untracedRuleIds") or [])
        active = sorted(matched | untraced | set(outcomes))
        rows = []
        for rule_id in active[:160]:
            outcome = outcomes.get(rule_id) or {}
            rows.append({
                "ruleId": rule_id,
                "activeInCurrentInference": rule_id in matched,
                "outcomeCount": int(outcome.get("outcomeCount") or 0),
                "calibrationEligibleOutcomeCount": int(outcome.get("calibrationEligibleOutcomeCount") or 0),
                "corroborationState": str(outcome.get("corroborationState") or "insufficient-history"),
                "averageActionAdjustedReturnPct": float(outcome.get("averageActionAdjustedReturnPct") or 0),
                "promotionEligible": bool(outcome.get("promotionEligible")),
                "governance": "human-review-required",
            })
        coverage = ledger.get("ruleCoverage") if isinstance(ledger.get("ruleCoverage"), dict) else {}
        summary = ledger.get("summary") if isinstance(ledger.get("summary"), dict) else {}
        native = bool(inference.get("nativeTypeDbReasoningUsed"))
        status = "ok" if native else "warning"
        if str(inference.get("status") or "") in {"error", "failed"}:
            status = "error"
        return {
            "status": status,
            "nativeTypeDbReasoningUsed": native,
            "activeRuleCount": int(summary.get("activeRuleCount") or 0),
            "matchedRuleCount": int(summary.get("matchedRuleCount") or 0),
            "untracedRuleCount": int(summary.get("untracedRuleCount") or 0),
            "coverageRatio": float(coverage.get("coverageRatio") or 0),
            "matchedRuleIds": list(coverage.get("matchedRuleIds") or [])[:80],
            "untracedRuleIds": list(coverage.get("untracedRuleIds") or [])[:80],
            "rules": rows,
            "automaticDeployment": False,
            "interpretation": "Rule activation and delayed outcomes are joined for human review; outcome performance never changes RuleBox automatically.",
        }

    def safe_call(self, method_name: str, fallback: Dict[str, object], *args, **kwargs):
        target = getattr(self.ontology_repository, method_name, None)
        if not target:
            return dict(fallback)
        try:
            result = target(*args, **kwargs)
            return result if isinstance(result, dict) else dict(fallback)
        except TypeError as error:
            # Test doubles and legacy graph adapters may not have gained the
            # optional world boundary yet. Production adapters must receive it;
            # this narrow fallback keeps diagnostics backward compatible only.
            if kwargs and ("unexpected keyword" in str(error) or "world_id" in str(error)):
                try:
                    result = target(*args)
                    return result if isinstance(result, dict) else dict(fallback)
                except Exception as nested_error:  # noqa: BLE001
                    payload = dict(fallback)
                    payload.update({"status": "error", "reason": str(nested_error)[:220]})
                    return payload
            payload = dict(fallback)
            payload.update({"status": "error", "reason": str(error)[:220]})
            return payload
        except Exception as error:  # noqa: BLE001 - diagnostics must be non-fatal.
            payload = dict(fallback)
            payload.update({"status": "error", "reason": str(error)[:220]})
            return payload

    def safe_list_call(self, method_name: str) -> List[Dict[str, object]]:
        target = getattr(self.ontology_repository, method_name, None)
        if not target:
            return []
        try:
            result = target()
            return [dict(item) for item in result or [] if isinstance(item, dict)]
        except Exception:
            return []

    def world_aware_read(self, method_name: str, *args, world_id: str = "", **kwargs):
        target = getattr(self.ontology_repository, method_name, None)
        if not callable(target):
            raise AttributeError(method_name + " is unavailable")
        if not str(world_id or "").strip():
            return target(*args, **kwargs)
        try:
            return target(*args, world_id=str(world_id), **kwargs)
        except TypeError as error:
            if "unexpected keyword" not in str(error) and "world_id" not in str(error):
                raise
            try:
                return target(*args, **kwargs)
            except TypeError as nested_error:
                if "unexpected keyword" not in str(nested_error):
                    raise
                return target(*args)

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

    def abox_storage_summary(self, payload: Dict[str, object]) -> Dict[str, object]:
        summary = self.pick(payload, [
            "configured",
            "status",
            "graphStore",
            "reason",
            "persistenceMode",
            "worldviewManifestId",
            "activeAboxSnapshotId",
            "activeScopeCount",
            "scopeTypeCounts",
            "scopeTopologyVersion",
            "scopeFamilyCounts",
            "logicalActiveEntityCount",
            "logicalActiveRelationCount",
            "physicalAboxEntityCount",
            "physicalAboxRelationCount",
            "storedManifestCount",
            "inactiveManifestCount",
            "storedScopeGenerationCount",
            "sharedHistoricalScopeGenerationCount",
            "keepInactiveManifestCount",
            "maxInactiveManifestsPrunedPerRun",
            "manifestInventoryStatus",
            "manifestInventoryReason",
            "physicalCountStatus",
            "physicalCountReason",
            "writeLease",
        ])
        scope_ids = [str(item or "") for item in payload.get("scopeIds") or [] if str(item or "")]
        if scope_ids:
            summary["scopeIds"] = scope_ids[:60]
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
            "nativeTypeDbReasoningCompleted",
            "typedbNativeRuleEvaluationCompleted",
            "nativeInferenceOutcome",
            "nativeInferenceNoMatch",
            "typedbBootstrapReasoningUsed",
            "pythonBootstrapDisabled",
            "inferenceGenerationId",
            "inferenceGenerationAt",
            "sourceAboxSnapshotId",
            "sourceAboxMaterialFingerprint",
            "generationAligned",
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
            "targetSymbols",
            "requestedSymbols",
            "evaluatedSymbols",
            "notEvaluatedSymbols",
            "targetCoverageStatus",
            "targetCoverageComplete",
            "targetCoverageReason",
            "impactPlanVersion",
            "inferenceImpactPlan",
            "ruleExecutionScope",
            "nativeRuleSelectionApplied",
        ])
        summary["relationTypes"] = sorted(set(
            str(item.get("type") or item.get("relationType") or "")
            for item in (payload.get("relations") or [])
            if isinstance(item, dict) and str(item.get("type") or item.get("relationType") or "")
        ))[:20]
        summary["recentTraces"] = [
            self.pick(item, ["id", "ruleId", "label", "symbol", "validationState", "dataState", "inferenceGenerationId"])
            for item in (payload.get("traces") or [])[:5]
            if isinstance(item, dict)
        ]
        query_metrics = payload.get("typedbQueryMetrics")
        if isinstance(query_metrics, dict):
            summary["queryMetrics"] = self.pick(query_metrics, [
                "enabled",
                "queryCount",
                "totalDurationMs",
            ])
            summary["queryMetrics"]["slowQueries"] = [
                self.pick(item, ["label", "status", "rowCount", "durationMs"])
                for item in (query_metrics.get("slowQueries") or [])[:3]
                if isinstance(item, dict)
            ]
        execution_plan = payload.get("executionPlan")
        if isinstance(execution_plan, dict):
            summary["executionPlan"] = self.pick(execution_plan, [
                "status",
                "targetSymbols",
                "queryLimit",
                "candidateRuleCount",
                "selectedRuleCount",
                "skippedRuleCount",
                "preflightEnabled",
                "preflightIncomingRelationsComplete",
                "preflightPrunedRuleCount",
                "preflightPrunedSymbolCount",
                "skippedByStatus",
            ])
            summary["executionPlan"]["selectedRules"] = [
                self.pick(item, ["ruleId", "candidateSymbols", "queryComplexity"])
                for item in (execution_plan.get("selectedRules") or [])[:8]
                if isinstance(item, dict)
            ]
        return summary

    def alert_pipeline_boundary(
        self,
        inference: Dict[str, object],
        notification_boundary: Dict[str, object] = None,
    ) -> Dict[str, object]:
        """Explain a quiet investment-alert path without inventing a signal."""
        payload = dict(inference or {})
        status = str(payload.get("status") or "").strip().lower()
        native_completed = bool(
            payload.get("nativeTypeDbReasoningCompleted")
            or payload.get("typedbNativeRuleEvaluationCompleted")
            or payload.get("nativeTypeDbReasoningUsed")
        )
        aligned = bool(payload.get("generationAligned"))
        source_abox = str(payload.get("sourceAboxSnapshotId") or "")
        if status == "not-evaluated" or str(payload.get("targetCoverageStatus") or "") == "partial":
            pipeline_status = "waiting-for-inference"
            reason = str(payload.get("reason") or "요청한 종목에 대한 TypeDB 추론이 아직 완료되지 않았습니다.")
        elif status == "empty" and native_completed and aligned and source_abox:
            pipeline_status = "no-signal"
            reason = "현재 ABox를 TypeDB 규칙으로 모두 확인했지만 투자 알림 후보가 될 관계는 성립하지 않았습니다."
        elif status in {"error", "failed", "stale-generation", "incomplete-abox", "missing"}:
            pipeline_status = "blocked"
            reason = str(payload.get("reason") or "InferenceBox is not ready for investment alert generation.")
        elif status == "ok" and bool(payload.get("nativeTypeDbReasoningUsed")):
            pipeline_status = "candidate-evaluation"
            reason = "관계 추론 결과가 생성되었습니다. 다음 단계에서 의미 변화, 쿨다운, 장 시간 정책을 확인합니다."
        else:
            pipeline_status = "waiting-for-inference"
            reason = str(payload.get("reason") or "현재 투자 알림 후보를 판정할 수 있는 완전한 추론 결과가 없습니다.")
        notification = dict(notification_boundary or {})
        return {
            "status": pipeline_status,
            "reason": reason,
            "inferenceStatus": status,
            "nativeTypeDbReasoningCompleted": native_completed,
            "generationAligned": aligned,
            "nativeInferenceOutcome": str(payload.get("nativeInferenceOutcome") or ""),
            "inferenceGenerationId": str(payload.get("inferenceGenerationId") or ""),
            "sourceAboxSnapshotId": source_abox,
            "targetSymbols": list(payload.get("targetSymbols") or [])[:80],
            "requestedSymbols": list(payload.get("requestedSymbols") or payload.get("symbols") or [])[:80],
            "evaluatedSymbols": list(payload.get("evaluatedSymbols") or payload.get("targetSymbols") or [])[:80],
            "notEvaluatedSymbols": list(payload.get("notEvaluatedSymbols") or [])[:80],
            "targetCoverageStatus": str(payload.get("targetCoverageStatus") or ""),
            "recentNotificationJobCount": int(notification.get("recentJobCount") or 0),
        }

    def abox_coverage(
        self,
        symbols: List[str] = None,
        world_id: str = "",
        storage: Dict[str, object] = None,
    ) -> Dict[str, object]:
        """Summarize current ABox coverage without probing an absent world.

        A scoped manifest is the source of truth for whether a world has an
        active ABox.  Reading every ABox node when that manifest is empty adds
        an unnecessary TypeDB query to the diagnostics page and can wait
        behind a concurrent projection.  Return the durable storage state
        directly in that case; a world with an active manifest retains the
        detailed per-symbol graph coverage calculation below.
        """
        storage_payload = storage if isinstance(storage, dict) else {}
        storage_status = str(storage_payload.get("status") or "").strip().lower()
        unavailable_states = {
            "empty",
            "pending",
            "incomplete",
            "error",
            "disabled",
            "driver-missing",
            "unavailable",
        }
        if storage_payload and storage_status in unavailable_states:
            active_entities = int(storage_payload.get("logicalActiveEntityCount") or 0)
            active_relations = int(storage_payload.get("logicalActiveRelationCount") or 0)
            coverage_status = "empty" if storage_status == "empty" else "unavailable"
            return {
                "status": coverage_status,
                "entityCount": active_entities,
                "relationCount": active_relations,
                "symbolCount": 0,
                "coverageRatio": 0.0,
                "primarySymbolCount": 0,
                "primaryCoverageRatio": 0.0,
                "contextSymbolCount": 0,
                "contextCoverageRatio": 0.0,
                "requiredCategories": sorted(CATEGORY_RELATIONS.keys()),
                "coverageGapCount": 0,
                "symbols": [],
                "primarySymbols": [],
                "contextSymbols": [],
                "coverageReadSkipped": True,
                "storageStatus": storage_status,
                "reason": (
                    "No active ABox manifest exists for this ontology world."
                    if storage_status == "empty"
                    else "ABox coverage read was skipped because storage is " + storage_status + "."
                ),
                "interpretation": "Coverage will be calculated after an active PortfolioWorld ABox is projected.",
            }
        if not hasattr(self.ontology_repository, "read_entity_rows") or not hasattr(self.ontology_repository, "read_relation_rows"):
            return {"status": "unavailable", "reason": "repository does not expose ABox row reads"}
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        coverage_read_mode = "full-abox-rows"
        entities = []
        stock_symbols = []
        relation_types_by_symbol: Dict[str, set] = {}
        relations = []
        relation_count = 0
        topology_reader = getattr(self.ontology_repository, "active_abox_relation_types_by_symbol", None)
        source_entity_reader = getattr(self.ontology_repository, "read_entity_rows_by_ids", None)
        storage_entity_reader = getattr(self.ontology_repository, "read_abox_entity_rows_by_storage_ids", None)
        if callable(topology_reader) and callable(source_entity_reader):
            active_abox_metadata = storage_payload.get("_activeAboxMetadata")
            active_abox_metadata = dict(active_abox_metadata or {}) if isinstance(active_abox_metadata, dict) else {}
            try:
                topology = self.world_aware_read(
                    "active_abox_relation_types_by_symbol",
                    clean_symbols,
                    world_id=world_id,
                    active_abox_metadata=active_abox_metadata,
                )
            except Exception as error:  # noqa: BLE001 - diagnostics must stay available.
                return {"status": "error", "reason": str(error)[:220]}
            if str((topology or {}).get("status") or "") == "ok":
                relation_count = int((topology or {}).get("relationCount") or 0)
                direct_types = (topology or {}).get("relationTypesBySymbol") or {}
                source_ids_by_symbol = (topology or {}).get("sourceIdsBySymbol") or {}
                stock_symbols = clean_symbols or sorted(
                    str(symbol or "").upper().strip()
                    for symbol in source_ids_by_symbol
                    if str(symbol or "").strip()
                )
                source_ids = sorted({
                    str(source_id or "").strip()
                    for symbol in stock_symbols
                    for source_id in source_ids_by_symbol.get(symbol, []) or []
                    if str(source_id or "").strip()
                })
                source_storage_ids = (topology or {}).get("sourceStorageIdsBySourceId") or {}
                storage_ids = [
                    str(source_storage_ids.get(source_id) or "").strip()
                    for source_id in source_ids
                    if str(source_storage_ids.get(source_id) or "").strip()
                ]
                try:
                    if storage_ids and callable(storage_entity_reader):
                        entities = self.world_aware_read(
                            "read_abox_entity_rows_by_storage_ids",
                            storage_ids,
                            world_id=world_id,
                        )
                        coverage_read_mode = "active-manifest-evidence-index"
                    elif source_ids:
                        entities = self.world_aware_read(
                            "read_entity_rows_by_ids",
                            source_ids,
                            ["ABox"],
                            world_id=world_id,
                        )
                        coverage_read_mode = "active-topology-source-ids"
                except Exception as error:  # noqa: BLE001 - coverage cannot invent missing root facts.
                    return {"status": "error", "reason": str(error)[:220]}
                loaded_source_ids = {
                    str(item.get("id") or "").strip()
                    for item in entities
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                }
                missing_source_ids = [source_id for source_id in source_ids if source_id not in loaded_source_ids]
                if missing_source_ids:
                    return {
                        "status": "error",
                        "reason": "Active ABox coverage topology did not return every requested stock source.",
                        "missingSourceIds": missing_source_ids[:20],
                    }
                relation_types_by_symbol = {symbol: set() for symbol in stock_symbols}
                for symbol in stock_symbols:
                    relation_types_by_symbol[symbol].update(
                        str(item or "").upper().strip()
                        for item in direct_types.get(symbol, [])
                        if str(item or "").strip()
                    )
            else:
                return {"status": "error", "reason": str((topology or {}).get("reason") or "Active ABox topology is unavailable.")[:220]}
        else:
            try:
                entities = self.world_aware_read("read_entity_rows", ["ABox"], world_id=world_id)
                stock_symbols = self.abox_symbols(entities, [])
                if clean_symbols:
                    stock_symbols = [item for item in stock_symbols if item in clean_symbols]
                relation_types_by_symbol = {symbol: set() for symbol in stock_symbols}
                relations = self.abox_coverage_relations(entities, stock_symbols, world_id=world_id)
            except Exception as error:  # noqa: BLE001 - diagnostics must stay available.
                return {"status": "error", "reason": str(error)[:220]}
        entity_classes_by_symbol: Dict[str, set] = {symbol: set() for symbol in stock_symbols}
        source_by_symbol: Dict[str, str] = {symbol: "" for symbol in stock_symbols}
        source_priority_by_symbol: Dict[str, int] = {symbol: -1 for symbol in stock_symbols}
        for entity in entities:
            symbol = self.row_symbol(entity)
            if symbol in entity_classes_by_symbol:
                tbox_class = str(entity.get("tboxClass") or "")
                tbox_classes = entity.get("tboxClasses") if isinstance(entity.get("tboxClasses"), list) else []
                kind = str(entity.get("kind") or entity.get("nodeKind") or "")
                if tbox_class:
                    entity_classes_by_symbol[symbol].add(tbox_class)
                for class_name in tbox_classes:
                    if str(class_name or "").strip():
                        entity_classes_by_symbol[symbol].add(str(class_name))
                if kind:
                    entity_classes_by_symbol[symbol].add(kind)
                source = str(entity.get("source") or "").strip()
                priority = self.coverage_source_priority(entity, symbol, source)
                if source and priority >= source_priority_by_symbol[symbol]:
                    source_by_symbol[symbol] = source
                    source_priority_by_symbol[symbol] = priority
        for relation in relations:
            symbol = self.row_symbol(relation) or self.symbol_from_relation_endpoints(relation)
            if symbol in relation_types_by_symbol:
                relation_type = str(relation.get("type") or relation.get("relationType") or "").upper().strip()
                if relation_type:
                    relation_types_by_symbol[symbol].add(relation_type)
        if relations:
            relation_count = len(relations)
        rows = []
        all_required_categories = sorted(CATEGORY_RELATIONS.keys())
        primary_rows = []
        context_rows = []
        total_present = 0
        total_expected = 0
        primary_present = 0
        primary_expected = 0
        context_present = 0
        context_expected = 0
        for symbol in stock_symbols:
            relation_types = relation_types_by_symbol.get(symbol) or set()
            classes = entity_classes_by_symbol.get(symbol) or set()
            scope = self.coverage_scope(classes, source_by_symbol.get(symbol) or "")
            required = self.required_categories_for_symbol(
                classes,
                source_by_symbol.get(symbol) or "",
            )
            present = sorted(name for name in required if relation_types.intersection(CATEGORY_RELATIONS.get(name, set())))
            missing = sorted(name for name in required if name not in present)
            total_present += len(present)
            total_expected += len(required)
            if scope == "primary":
                primary_present += len(present)
                primary_expected += len(required)
            else:
                context_present += len(present)
                context_expected += len(required)
            row = {
                "symbol": symbol,
                "diagnosticScope": scope,
                "present": present,
                "missing": missing,
                "missingLabels": [CATEGORY_LABELS.get(name, name) for name in missing],
                "relationTypes": sorted(relation_types)[:40],
                "entityClasses": sorted(entity_classes_by_symbol.get(symbol) or [])[:30],
                "requiredCategories": required,
                "coverageRatio": round(len(present) / max(1, len(required)), 3),
            }
            rows.append(row)
            if scope == "primary":
                primary_rows.append(row)
            else:
                context_rows.append(row)
        coverage_ratio = round(total_present / max(1, total_expected), 3)
        primary_coverage_ratio = round(primary_present / max(1, primary_expected), 3)
        context_coverage_ratio = round(context_present / max(1, context_expected), 3)
        status = "ok" if primary_rows and primary_coverage_ratio >= 0.75 else ("warning" if primary_rows or stock_symbols else "empty")
        coverage_gap_count = len([
            item for item in entities
            if str(item.get("kind") or item.get("nodeKind") or "") == "coverage-gap"
            or str(item.get("tboxClass") or "") == "CoverageGap"
        ])
        return {
            "status": status,
            "entityCount": len(entities),
            "relationCount": relation_count,
            "symbolCount": len(stock_symbols),
            "coverageRatio": coverage_ratio,
            "primarySymbolCount": len(primary_rows),
            "primaryCoverageRatio": primary_coverage_ratio,
            "contextSymbolCount": len(context_rows),
            "contextCoverageRatio": context_coverage_ratio,
            "requiredCategories": all_required_categories,
            "coverageGapCount": coverage_gap_count,
            "coverageReadMode": coverage_read_mode,
            "interpretation": self.coverage_interpretation(status, primary_coverage_ratio, len(primary_rows), len(context_rows)),
            "symbols": rows[:80],
            "primarySymbols": primary_rows[:80],
            "contextSymbols": context_rows[:80],
        }

    def abox_coverage_relations(
        self,
        entities: List[Dict[str, object]],
        stock_symbols: List[str],
        world_id: str = "",
    ) -> List[Dict[str, object]]:
        """Read only relation edges that can affect per-symbol coverage.

        The full active ABox contains many fact-to-fact relations. Joining every
        one with both endpoints is expensive on TypeDB and is unnecessary for
        this diagnostic: required coverage categories are all rooted at a
        stock/asset node. Repositories that expose the scoped reader retain the
        same coverage semantics without turning the status endpoint into a full
        graph export.
        """
        scoped_reader = getattr(self.ontology_repository, "read_relation_rows_by_source_ids", None)
        source_ids = []
        symbol_set = {str(item or "").upper().strip() for item in stock_symbols or [] if str(item or "").strip()}
        root_kinds = {"stock", "market-proxy", "etf", "crypto-asset", "crypto-market-signal"}
        for entity in entities or []:
            entity_id = str((entity or {}).get("id") or "").strip()
            kind = str((entity or {}).get("kind") or (entity or {}).get("nodeKind") or "").strip().lower()
            is_asset_root = entity_id.lower().startswith(("stock:", "crypto:")) or kind in root_kinds
            if entity_id and is_asset_root and self.row_symbol(entity) in symbol_set:
                source_ids.append(entity_id)
        if callable(scoped_reader) and source_ids:
            return list(self.world_aware_read(
                "read_relation_rows_by_source_ids",
                sorted(set(source_ids)),
                ["ABox"],
                world_id=world_id,
            ) or [])
        return list(self.world_aware_read("read_relation_rows", ["ABox"], world_id=world_id) or [])

    def required_categories_for_symbol(self, classes: set, source: str) -> List[str]:
        class_values = {str(item or "") for item in (classes or set())}
        if {"CryptoAsset", "crypto-asset", "CryptoMarketSignal", "crypto-market-signal"}.intersection(class_values):
            return ["price", "trendPath", "tradeFlow", "liquidity", "dataQuality", "externalEvidence"]
        base = ["price", "trendPath", "tradeFlow", "dataQuality", "externalEvidence", "macroRegime"]
        if str(source or "").lower() == "watchlist" or "WatchlistCandidate" in class_values:
            return base + ["valuation"]
        return base + ["liquidity", "execution", "valuation"]

    def coverage_scope(self, classes: set, source: str) -> str:
        class_values = {str(item or "") for item in (classes or set())}
        decision_markers = {
            "ActionPolicy",
            "PositionRole",
            "WatchlistCandidate",
            "PortfolioHolding",
            "Holding",
            "CryptoAsset",
            "crypto-asset",
            "CryptoMarketSignal",
            "crypto-market-signal",
        }
        if str(source or "").lower() in {"holding", "watchlist"}:
            return "primary"
        if decision_markers.intersection(class_values):
            return "primary"
        return "context"

    def coverage_source_priority(self, entity: Dict[str, object], symbol: str, source: str) -> int:
        """Prefer the root investable instrument's role over attached evidence.

        Many ABox facts share a symbol. TypeDB does not promise their read
        order, so assigning the last entity's source can turn a holding into a
        context symbol when a news or external-signal fact is read later.
        """
        source_key = str(source or "").lower()
        entity_id = str((entity or {}).get("id") or "").strip().lower()
        kind = str((entity or {}).get("kind") or (entity or {}).get("nodeKind") or "").strip().lower()
        is_root_instrument = entity_id in {
            "stock:" + str(symbol or "").lower(),
            "crypto:" + str(symbol or "").lower(),
        } or kind in {"stock", "market-proxy", "etf", "crypto-asset", "crypto-market-signal"}
        if source_key == "holding":
            return 40 if is_root_instrument else 30
        if source_key == "watchlist":
            return 35 if is_root_instrument else 25
        if is_root_instrument:
            return 10
        return 0

    def coverage_interpretation(self, status: str, primary_ratio: float, primary_count: int, context_count: int) -> str:
        if status == "empty":
            return "No ABox symbols were available for coverage diagnostics."
        if status == "ok":
            return (
                "Primary account/watchlist/decision symbols have sufficient ontology coverage. "
                + str(context_count)
                + " context or market-proxy symbols are reported separately and do not lower the primary status."
            )
        return (
            "Primary decision-symbol ontology coverage is below the operating threshold: "
            + str(round(primary_ratio * 100))
            + "% across "
            + str(primary_count)
            + " symbols."
        )

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
        elif str(inference.get("targetCoverageStatus") or "") == "partial":
            status = "warning"
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
            return "TypeDB native rules produced InferenceBox relations."
        if bootstrap_used:
            return "Legacy in-memory inference is present but no longer accepted for investment judgement."
        if mode:
            return "TypeDB native rule materialization is required, but no InferenceBox output was confirmed."
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

    def strategy_proposal_boundary(self) -> Dict[str, object]:
        if not self.strategy_proposal_service:
            return {"status": "unavailable", "reason": "strategy proposal service is not configured"}
        try:
            summary = self.strategy_proposal_service.status()
            listed = self.strategy_proposal_service.list()
        except Exception as error:  # noqa: BLE001 - diagnostics must not depend on proposal storage health.
            return {"status": "error", "reason": str(error)[:180]}
        proposals = [
            item for item in (listed.get("proposals") or [])
            if isinstance(item, dict)
        ] if isinstance(listed, dict) else []
        validated = int(summary.get("validatedCount") or 0) if isinstance(summary, dict) else 0
        approved = int(summary.get("approvedCount") or 0) if isinstance(summary, dict) else 0
        deployed = int(summary.get("deployedCount") or 0) if isinstance(summary, dict) else 0
        proposed = int(summary.get("proposedCount") or 0) if isinstance(summary, dict) else 0
        pending_approval = proposed + validated
        pending_deployment = approved
        status = "ok"
        next_action = ""
        if pending_deployment:
            status = "warning"
            next_action = "approved strategies are waiting for ontology-lab apply/deployment"
        elif pending_approval:
            status = "warning"
            next_action = "validated/proposed strategies are waiting for human approval"
        elif deployed:
            next_action = "strategy proposal loop has deployed strategies"
        else:
            next_action = "no active strategy proposal backlog"
        return {
            "status": status,
            "count": int(summary.get("count") or len(proposals)) if isinstance(summary, dict) else len(proposals),
            "proposedCount": proposed,
            "validatedCount": validated,
            "approvedCount": approved,
            "deployedCount": deployed,
            "retiredCount": int(summary.get("retiredCount") or 0) if isinstance(summary, dict) else 0,
            "pendingApprovalCount": pending_approval,
            "pendingDeploymentCount": pending_deployment,
            "nextAction": next_action,
            "proposals": [self.strategy_proposal_summary(item) for item in proposals[:10]],
        }

    def strategy_proposal_summary(self, proposal: Dict[str, object]) -> Dict[str, object]:
        validation = proposal.get("validation") if isinstance(proposal.get("validation"), dict) else {}
        readiness = validation.get("promotionReadiness") if isinstance(validation.get("promotionReadiness"), dict) else {}
        materialization = validation.get("materialization") if isinstance(validation.get("materialization"), dict) else {}
        return {
            "id": str(proposal.get("id") or ""),
            "title": str(proposal.get("title") or ""),
            "status": str(proposal.get("status") or ""),
            "updatedAt": str(proposal.get("updatedAt") or ""),
            "ruleIds": [str(item) for item in (proposal.get("ruleIds") or [])[:8]],
            "validationStatus": str(validation.get("status") or ""),
            "promotionReadinessStatus": str(readiness.get("status") or ""),
            "materializationStatus": str(materialization.get("status") or ""),
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
