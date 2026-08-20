"""Independent V2 reasoning execution without the monitoring runner."""

import inspect
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Dict, Iterable, Mapping

from ..domain.events import DomainEvent
from ..domain.independent_reasoning import (
    IndependentReasoningRequest,
    IndependentReasoningResult,
    independent_reasoning_request,
    reasoning_event_scope,
)
from ..domain.investment_reasoning import FactDelta
from ..domain.ontology_projection_input import compact_monitor_state_for_ontology
from ..domain.world_partitioned_reasoning import attach_shared_premise_evidence


VERIFIED_PROJECTION_STATUSES = {
    "ok",
    "partial",
    "unchanged-material-facts",
    "unchanged-material-facts-reasoning-retry",
    "reused-shared-account-inference",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _int_setting(settings: Mapping[str, object], key: str, fallback: int, minimum: int, maximum: int) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(minimum, min(maximum, value))


def projection_inference_identity(projection: object) -> Dict[str, object]:
    values = dict(projection or {}) if isinstance(projection, Mapping) else {}
    inference = values.get("inferenceBox") if isinstance(values.get("inferenceBox"), Mapping) else {}
    native_completed = bool(
        inference.get("nativeTypeDbReasoningCompleted")
        or inference.get("typedbNativeRuleEvaluationCompleted")
    )
    verified = bool(
        str(values.get("status") or "").lower() in VERIFIED_PROJECTION_STATUSES
        and native_completed
        and inference.get("generationAligned")
        and str(inference.get("sourceAboxSnapshotId") or "").strip()
    )
    return {
        "verified": verified,
        "status": str(values.get("status") or "missing"),
        "sourceAboxSnapshotId": str(inference.get("sourceAboxSnapshotId") or ""),
        "inferenceGenerationId": str(inference.get("inferenceGenerationId") or ""),
        "nativeTypeDbReasoningCompleted": native_completed,
        "generationAligned": bool(inference.get("generationAligned")),
        "relationCount": int(inference.get("relationCount") or len(inference.get("relations") or [])),
        "traceCount": int(inference.get("traceCount") or len(inference.get("traces") or [])),
    }


def projection_retry_policy(projection: object) -> Dict[str, object]:
    """Normalize transient projection failures, including native-rule details."""

    values = dict(projection or {}) if isinstance(projection, Mapping) else {}
    failure = (
        dict(values.get("nativeRuleFailure") or {})
        if isinstance(values.get("nativeRuleFailure"), Mapping)
        else {}
    )
    retryable = bool(values.get("retryable") or failure.get("retryable"))
    retry_after = int(
        values.get("recommendedRetryAfterSeconds")
        or failure.get("recommendedRetryAfterSeconds")
        or 0
    )
    reason = str(
        values.get("reason")
        or failure.get("reason")
        or ""
    )
    return {
        "retryable": retryable,
        "retryAfterSeconds": max(1, retry_after) if retryable else 0,
        "reason": reason,
        "reasonCode": str(failure.get("reasonCode") or ""),
        "failureStage": str(failure.get("stage") or ""),
        "blockingRuleId": str(failure.get("ruleId") or ""),
    }


def alert_event_payload(event: object) -> Dict[str, object]:
    source_metadata = deepcopy(getattr(event, "metadata", {}) or {})
    metadata = {
        key: source_metadata.get(key)
        for key in [
            "decisionEpisodeId",
            "decisionKey",
            "inferenceGenerationId",
            "sourceAboxSnapshotId",
            "reasoningEngineDeploymentId",
            "reasoningEngineVersion",
            "selectedRuleId",
            "matchedRuleIds",
            "relationCount",
            "traceCount",
            "reviewLevel",
            "dataState",
            "validationState",
            "investmentReasoningCaseId",
            "v2DecisionSynthesis",
        ]
        if source_metadata.get(key) not in (None, "", [], {})
    }
    return {
        "accountId": str(getattr(event, "account_id", "") or ""),
        "accountLabel": str(getattr(event, "account_label", "") or ""),
        "severity": str(getattr(event, "severity", "") or ""),
        "rule": str(getattr(event, "rule", "") or ""),
        "key": str(getattr(event, "key", "") or ""),
        "title": str(getattr(event, "title", "") or ""),
        "lines": list(getattr(event, "lines", []) or []),
        "symbol": str(getattr(event, "symbol", "") or ""),
        "criteria": list(getattr(event, "criteria", []) or []),
        "metadata": metadata,
        "generatedAt": str(getattr(event, "generated_at", "") or ""),
    }


def compact_projection_result(projection: object) -> Dict[str, object]:
    values = dict(projection or {}) if isinstance(projection, Mapping) else {}
    identity = projection_inference_identity(values)
    retry = projection_retry_policy(values)
    stages = (
        values.get("runtimeStages")
        if isinstance(values.get("runtimeStages"), Mapping)
        else values.get("stages")
        if isinstance(values.get("stages"), Mapping)
        else {}
    )
    slo = values.get("slo") if isinstance(values.get("slo"), Mapping) else {}
    audit = values.get("projectionAudit") if isinstance(values.get("projectionAudit"), Mapping) else {}
    shared = (
        values.get("sharedInstrumentInference")
        if isinstance(values.get("sharedInstrumentInference"), Mapping)
        else {}
    )
    return {
        "configured": bool(values.get("configured")),
        "saved": bool(values.get("saved")),
        "status": str(values.get("status") or ""),
        "reason": str(values.get("reason") or "")[:500],
        "graphStore": str(values.get("graphStore") or ""),
        "worldId": str(values.get("worldId") or ""),
        "accountId": str(values.get("accountId") or ""),
        "entityCount": int(values.get("entityCount") or 0),
        "relationCount": int(values.get("relationCount") or 0),
        "sourceAboxSnapshotId": identity["sourceAboxSnapshotId"],
        "inferenceGenerationId": identity["inferenceGenerationId"],
        "nativeTypeDbReasoningCompleted": identity["nativeTypeDbReasoningCompleted"],
        "generationAligned": identity["generationAligned"],
        "inferenceRelationCount": identity["relationCount"],
        "inferenceTraceCount": identity["traceCount"],
        "retryable": retry["retryable"],
        "retryAfterSeconds": retry["retryAfterSeconds"],
        "failureReasonCode": retry["reasonCode"],
        "failureStage": retry["failureStage"],
        "blockingRuleId": retry["blockingRuleId"],
        "stages": {
            str(key): int(value)
            for key, value in stages.items()
            if isinstance(value, (int, float))
        },
        "slo": {
            "state": str(slo.get("state") or ""),
            "violations": [
                {
                    "code": str(item.get("code") or ""),
                    "severity": str(item.get("severity") or ""),
                }
                for item in slo.get("violations") or []
                if isinstance(item, Mapping)
            ][:20],
        },
        "projectionAudit": {
            "status": str(audit.get("status") or ""),
            "runId": str(audit.get("runId") or ""),
            "activeAboxSnapshotId": str(audit.get("activeAboxSnapshotId") or ""),
        },
        "sharedInstrumentInference": {
            "contractVersion": str(shared.get("contractVersion") or ""),
            "executionMode": str(shared.get("executionMode") or ""),
            "decisionAuthority": str(shared.get("decisionAuthority") or ""),
            "sharedSymbolCount": int(shared.get("sharedSymbolCount") or 0),
            "symbols": {
                str(symbol): {
                    key: value.get(key)
                    for key in [
                        "overlayId", "overlayStatus", "sharedSnapshotIds",
                        "sharedSemanticFingerprints", "sharedSourceAsOf",
                        "sharedMarketRuleIds", "accountRuleIds", "reuseEligible",
                    ]
                    if key in value
                }
                for symbol, value in dict(shared.get("symbols") or {}).items()
                if isinstance(value, Mapping)
            },
        },
    }


class IndependentReasoningInputAssembler:
    """Build point-in-time snapshots from the durable monitor source."""

    def __init__(
        self,
        account_repository,
        snapshot_source,
        monitor_store,
        settings=None,
        instrument_subscription_index=None,
        instrument_subscription_state_source=None,
    ):
        self.account_repository = account_repository
        self.snapshot_source = snapshot_source
        self.monitor_store = monitor_store
        self.settings = dict(settings or {})
        self.instrument_subscription_index = instrument_subscription_index
        self.instrument_subscription_state_source = instrument_subscription_state_source or monitor_store

    def temporal_history_limit(self) -> int:
        return _int_setting(self.settings, "temporalWindowHistoryLimit", 96, 6, 500)

    def selected_accounts(self, request: IndependentReasoningRequest):
        requested = set(request.account_ids)
        accounts = list(self.account_repository.load() or [])
        selected = [
            account for account in accounts
            if not requested or str(getattr(account, "account_id", "") or "") in requested
        ]
        if requested or not request.symbols:
            return selected
        indexed_accounts = []
        lookup = getattr(self.instrument_subscription_index, "account_ids_for_symbols", None)
        if callable(lookup):
            try:
                indexed_accounts = list(lookup(request.symbols) or [])
            except Exception:
                indexed_accounts = []
        if not indexed_accounts:
            repair = getattr(self.instrument_subscription_index, "ensure_subscription_index", None)
            if callable(repair):
                try:
                    repair(
                        accounts,
                        getattr(self.instrument_subscription_state_source, "previous", {}) or {},
                    )
                    indexed_accounts = list(lookup(request.symbols) or []) if callable(lookup) else []
                except Exception:
                    indexed_accounts = []
        if indexed_accounts:
            indexed = set(str(value or "") for value in indexed_accounts)
            return [
                account for account in selected
                if str(getattr(account, "account_id", "") or "") in indexed
            ]
        target_symbols = set(request.symbols)
        previous = getattr(self.monitor_store, "previous", {}) or {}
        scoped = []
        for account in selected:
            account_id = str(getattr(account, "account_id", "") or "")
            state = previous.get(account_id) if isinstance(previous, dict) else {}
            state = state if isinstance(state, dict) else {}
            symbols = {
                str(symbol or "").upper().strip()
                for collection_name in ["positions", "watchlist"]
                for symbol in (
                    (state.get(collection_name) or {}).keys()
                    if isinstance(state.get(collection_name), dict)
                    else [
                        item.get("symbol")
                        for item in state.get(collection_name) or []
                        if isinstance(item, dict)
                    ]
                )
                if str(symbol or "").strip()
            }
            symbols.update(
                str(symbol or "").upper().strip()
                for symbol in getattr(account, "watchlist_symbols", []) or []
                if str(symbol or "").strip()
            )
            if symbols.intersection(target_symbols):
                scoped.append(account)
        return scoped

    def preflight(self, request: IndependentReasoningRequest, accounts) -> Dict[str, object]:
        if request.account_ids:
            selected = {
                str(getattr(account, "account_id", "") or "")
                for account in accounts or []
            }
            missing = sorted(set(request.account_ids) - selected)
            if missing:
                return {
                    "ready": False,
                    "permanent": True,
                    "status": "rejected-source-account",
                    "reason": "The requested account is not registered.",
                    "missingAccountIds": missing,
                }
        return dict(self.snapshot_source.preflight(accounts, dict(request.context)) or {})

    def previous_state(self, account_id: str, snapshot) -> Dict[str, object]:
        metadata = snapshot.metadata if isinstance(getattr(snapshot, "metadata", None), dict) else {}
        persisted = metadata.get("previousMonitorState")
        if isinstance(persisted, dict) and persisted:
            return deepcopy(persisted)
        previous = getattr(self.monitor_store, "previous", {}) or {}
        value = previous.get(str(account_id or "")) if isinstance(previous, dict) else {}
        return deepcopy(value) if isinstance(value, dict) else {}

    def history(self, account_id: str):
        reader = getattr(self.monitor_store, "load_history", None)
        if not callable(reader):
            return []
        values = list(reader(account_id, limit=self.temporal_history_limit()) or [])
        return [
            compact_monitor_state_for_ontology(value, settings=self.settings)
            for value in values
            if isinstance(value, dict) and value
        ][-self.temporal_history_limit():]

    def assemble(self, request: IndependentReasoningRequest) -> Dict[str, object]:
        accounts = self.selected_accounts(request)
        if not accounts:
            return {
                "status": "rejected",
                "accounts": [],
                "preflight": {
                    "ready": False,
                    "permanent": True,
                    "status": "no-affected-account",
                    "reason": "No account holds or watches the requested symbol scope.",
                },
                "snapshots": [],
            }
        preflight = self.preflight(request, accounts)
        if not preflight.get("ready"):
            return {"status": "deferred", "accounts": accounts, "preflight": preflight, "snapshots": []}
        snapshots = []
        previous_by_account = {}
        for account in accounts:
            account_id = str(getattr(account, "account_id", "") or "")
            snapshot = self.snapshot_source(account, reasoning_context=dict(request.context))
            previous = self.previous_state(account_id, snapshot)
            metadata = deepcopy(snapshot.metadata or {})
            if previous and not isinstance(metadata.get("previousMonitorState"), dict):
                metadata["previousMonitorState"] = compact_monitor_state_for_ontology(
                    previous,
                    settings=self.settings,
                )
            if not isinstance(metadata.get("monitorStateHistory"), list):
                metadata["monitorStateHistory"] = self.history(account_id)
            metadata["independentReasoningRequest"] = {
                "requestId": request.request_id,
                "scopeId": request.scope_id,
                "inputFingerprint": request.input_fingerprint,
                "deploymentId": request.deployment_id,
                "sourceObservedAt": request.source_observed_at,
            }
            snapshot.metadata = metadata
            snapshots.append(snapshot)
            previous_by_account[account_id] = previous
        return {
            "status": "ready",
            "accounts": accounts,
            "preflight": preflight,
            "snapshots": snapshots,
            "previousByAccount": previous_by_account,
        }


class ScopedTypeDBInferenceExecutor:
    """Project only the requested scope and read the resulting InferenceBox."""

    def __init__(self, projection_recorder, shared_inference_service=None):
        self.projection_recorder = projection_recorder
        self.shared_inference_service = shared_inference_service
        self.last_shared_publication = {"status": "not-configured"}
        self.last_shared_publication_ms = 0

    def execute(self, request: IndependentReasoningRequest, snapshots, progress_callback=None):
        results = {}
        publication_receipts = []
        publication_elapsed_ms = 0
        direct_shared_premise_count = 0
        for snapshot in snapshots or []:
            account_id = str(getattr(snapshot, "account_id", "") or "")

            def progress(stage: str, payload: Dict[str, object] = None) -> None:
                if callable(progress_callback):
                    progress_callback(stage, {"accountId": account_id, **dict(payload or {})})

            reasoning_context = dict(request.context)
            reuse_proof = {"status": "not-configured", "reuseEligible": False}
            reusable_projection = {"status": "not-configured", "reuseEligible": False}
            premise_proof = {}
            partitioned_reader = getattr(
                self.projection_recorder,
                "world_partitioned_reasoning_enabled",
                None,
            )
            partitioned_mode = bool(
                callable(partitioned_reader) and partitioned_reader()
            )
            if partitioned_mode:
                premise_builder = getattr(
                    self.projection_recorder,
                    "prepare_shared_premises",
                    None,
                )
                try:
                    premise_proof = dict(
                        premise_builder(
                            snapshot,
                            target_symbols=list(request.symbols),
                            reasoning_context=reasoning_context,
                            progress_callback=progress,
                        ) or {}
                    ) if callable(premise_builder) else {
                        "status": "shared-premise-builder-unavailable",
                        "ready": False,
                        "retryable": False,
                    }
                except Exception as error:  # noqa: BLE001 - fail closed before private projection.
                    premise_proof = {
                        "status": "shared-premise-preparation-error",
                        "ready": False,
                        "retryable": True,
                        "reason": str(error)[:240],
                    }
                if not bool(premise_proof.get("ready")):
                    result = {
                        "saved": False,
                        "status": str(
                            premise_proof.get("status")
                            or "shared-premise-not-ready"
                        ),
                        "reason": str(
                            premise_proof.get("reason")
                            or "SharedPremiseWorld must complete before account inference."
                        )[:240],
                        "retryable": bool(premise_proof.get("retryable", True)),
                        "recommendedRetryAfterSeconds": int(
                            premise_proof.get("recommendedRetryAfterSeconds") or 10
                        ),
                        "preservedActiveGeneration": True,
                        "worldPartitionedReasoning": premise_proof,
                    }
                    snapshot.metadata.setdefault("ontology", {})["projection"] = result
                    results[account_id] = result
                    progress(
                        "ontology_projection.shared_premise_blocked",
                        {"status": result["status"]},
                    )
                    continue
                reasoning_context["sharedPremiseProof"] = premise_proof
                reuse_proof = {
                    "status": "ready-direct-shared-premise-world",
                    "reuseEligible": True,
                    "symbols": dict(premise_proof.get("symbols") or {}),
                    "targetSymbols": list(request.symbols),
                    "marketRuleCatalogIds": list(premise_proof.get("sharedRuleIds") or []),
                    "matchedMarketRuleIds": sorted({
                        str(trace.get("ruleId") or "")
                        for trace in premise_proof.get("traces") or []
                        if isinstance(trace, dict) and str(trace.get("ruleId") or "")
                    }),
                }
            elif self.shared_inference_service is not None:
                reuse_reader = getattr(
                    self.shared_inference_service,
                    "reusable_portfolio_projection",
                    None,
                )
                if callable(reuse_reader):
                    try:
                        reusable_projection = dict(
                            reuse_reader(
                                reasoning_context,
                                request.symbols,
                                snapshot,
                            ) or {}
                        )
                    except Exception as error:
                        reusable_projection = {
                            "status": "error",
                            "reuseEligible": False,
                            "reason": str(error)[:180],
                        }
                if bool(reusable_projection.get("reuseEligible")):
                    result = dict(reusable_projection.get("projection") or {})
                    result["sharedInferenceExecution"] = {
                        "reuseProofStatus": "ready",
                        "reuseEligible": True,
                        "portfolioProjectionReused": True,
                        "publicationStatus": "not-required-existing-head",
                        "publishedSharedSymbolCount": 0,
                    }
                    results[account_id] = result
                    progress(
                        "ontology_projection.reused_exact_inference",
                        {
                            "status": "ready",
                            "targetSymbolCount": len(request.symbols),
                        },
                    )
                    continue
                try:
                    reuse_proof = dict(
                        self.shared_inference_service.execution_reuse_proof(
                            reasoning_context,
                            request.symbols,
                            snapshot=snapshot,
                        ) or {}
                    )
                except Exception as error:
                    reuse_proof = {
                        "status": "error",
                        "reuseEligible": False,
                        "reason": str(error)[:180],
                    }
            if bool(reuse_proof.get("reuseEligible")) and not partitioned_mode:
                reasoning_context["sharedInferenceReuseProof"] = reuse_proof

            recorder = self.projection_recorder.record_snapshot
            parameters = inspect.signature(recorder).parameters
            kwargs = {
                "target_symbols": list(request.symbols),
                "reasoning_context": reasoning_context,
            }
            if "progress_callback" in parameters:
                kwargs["progress_callback"] = progress
            try:
                result = dict(recorder(snapshot, **kwargs) or {})
            except Exception as error:  # noqa: BLE001 - the worker applies bounded retry policy.
                result = {
                    "saved": False,
                    "status": "error",
                    "reason": str(error)[:240],
                    "retryable": True,
                    "recommendedRetryAfterSeconds": 15,
                }
                snapshot.metadata.setdefault("ontology", {})["projection"] = result
            if partitioned_mode and bool(premise_proof.get("ready")):
                result = attach_shared_premise_evidence(result, premise_proof)
                direct_shared_premise_count += 1
            elif bool(reuse_proof.get("reuseEligible")) and self.shared_inference_service is not None:
                evidence_composer = getattr(
                    self.shared_inference_service,
                    "attach_shared_market_evidence",
                    None,
                )
                if callable(evidence_composer):
                    result = dict(evidence_composer(result, reuse_proof) or result)
            results[account_id] = result
            if partitioned_mode:
                result["sharedInferenceExecution"] = {
                    "status": "direct-shared-premise-world",
                    "reuseProofStatus": str(reuse_proof.get("status") or ""),
                    "reuseEligible": bool(reuse_proof.get("reuseEligible")),
                    "publicationStatus": "not-required",
                    "portfolioProjectionReused": False,
                    "decisionPathAffected": True,
                }
            elif self.shared_inference_service is not None:
                try:
                    account_publication_started = time.perf_counter()
                    publication = dict(
                        self.shared_inference_service.publish_verified_results(
                            {account_id: result},
                            request.symbols,
                            snapshots=[snapshot],
                            observed_at=request.source_observed_at,
                        ) or {}
                    )
                except Exception as error:
                    publication = {"status": "error", "reason": str(error)[:180]}
                finally:
                    publication_elapsed_ms += int(
                        (time.perf_counter() - account_publication_started) * 1000
                    )
                result["sharedInferenceExecution"] = {
                    "reuseProofStatus": str(reuse_proof.get("status") or ""),
                    "reuseEligible": bool(reuse_proof.get("reuseEligible")),
                    "publicationStatus": str(publication.get("status") or ""),
                    "publishedSharedSymbolCount": int(
                        publication.get("sharedSymbolCount") or 0
                    ),
                    "portfolioProjectionReused": False,
                }
                publication_receipts.append(publication)
        self.last_shared_publication_ms = publication_elapsed_ms
        if direct_shared_premise_count:
            self.last_shared_publication = {
                "status": "direct-shared-premise-world",
                "publicationCount": 0,
                "directAccountCount": direct_shared_premise_count,
                "decisionPathAffected": True,
            }
        elif publication_receipts:
            self.last_shared_publication = {
                "status": (
                    "error"
                    if any(str(item.get("status") or "").lower() == "error" for item in publication_receipts)
                    else "ready"
                ),
                "publicationCount": len(publication_receipts),
                "snapshotCount": sum(int(item.get("snapshotCount") or 0) for item in publication_receipts),
                "overlayCount": sum(int(item.get("overlayCount") or 0) for item in publication_receipts),
                "headUpdateCount": sum(int(item.get("headUpdateCount") or 0) for item in publication_receipts),
                "sharedSymbolCount": len({
                    symbol
                    for item in publication_receipts
                    for symbol in list(item.get("changedHeadSymbols") or [])
                    + list((item.get("consistencyBySymbol") or {}).keys())
                }),
                "changedHeadSymbols": sorted({
                    str(symbol or "").upper().strip()
                    for item in publication_receipts
                    for symbol in item.get("changedHeadSymbols") or []
                    if str(symbol or "").strip()
                }),
                "decisionPathAffected": False,
            }
        elif any(
            bool((value.get("sharedInferenceExecution") or {}).get("portfolioProjectionReused"))
            for value in results.values()
            if isinstance(value, Mapping)
        ):
            self.last_shared_publication = {
                "status": "reused-existing-head",
                "publicationCount": 0,
                "headUpdateCount": 0,
                "decisionPathAffected": False,
            }
        else:
            self.last_shared_publication = {"status": "not-configured"}
        return results


class V2ReasoningEngine:
    """Concrete independent engine implementation for the V2 deployment."""

    def __init__(
        self,
        descriptor,
        input_assembler: IndependentReasoningInputAssembler,
        inference_executor: ScopedTypeDBInferenceExecutor,
        candidate_builder,
        cycle_recorder=None,
        delivery_authorized_provider=None,
        settings=None,
        release_identity=None,
        reasoning_orchestrator=None,
        shared_inference_service=None,
    ):
        self._descriptor = descriptor
        self.input_assembler = input_assembler
        self.inference_executor = inference_executor
        self.candidate_builder = candidate_builder
        self.cycle_recorder = cycle_recorder
        self.delivery_authorized_provider = delivery_authorized_provider or (lambda: False)
        self.settings = dict(settings or {})
        self._release_identity = dict(release_identity or {})
        self.reasoning_orchestrator = reasoning_orchestrator
        self.shared_inference_service = shared_inference_service
        self.last_result = None
        self.results_by_request_id = {}

    def descriptor(self):
        return self._descriptor

    def release_manifest(self) -> Dict[str, object]:
        values = self._descriptor.to_dict()
        return {
            "engineId": values.get("engineId"),
            "engineVersion": values.get("engineVersion"),
            "deploymentId": values.get("deploymentId"),
            "graphStoreBinding": values.get("graphStoreBinding"),
            "timeSeriesBackendId": values.get("timeSeriesBackendId"),
            "releaseBundle": values.get("releaseBundle"),
            "releaseIdentity": dict(self._release_identity),
        }

    def release_identity(self) -> Dict[str, object]:
        return dict(self._release_identity)

    def consume(self, source_events: Iterable[Mapping[str, object]]) -> Dict[str, object]:
        request = independent_reasoning_request(
            self._descriptor.deployment_id,
            source_events,
            self.release_manifest(),
        )
        return self.execute(request).to_dict()

    def execute(self, request: IndependentReasoningRequest, force: bool = False) -> IndependentReasoningResult:
        started_at = utc_now_iso()
        started = time.perf_counter()
        stages = {}
        reasoning_case = (
            self.reasoning_orchestrator.start(request, self._release_identity)
            if self.reasoning_orchestrator is not None
            else None
        )
        input_started = time.perf_counter()
        assembled = self.input_assembler.assemble(request)
        stages["inputAssemblyMs"] = int((time.perf_counter() - input_started) * 1000)
        preflight = dict(assembled.get("preflight") or {})
        if assembled.get("status") != "ready":
            if reasoning_case is not None:
                reasoning_case = self.reasoning_orchestrator.defer(
                    reasoning_case.case_id,
                    str(preflight.get("reason") or "Point-in-time input is not ready."),
                    retryable=not bool(preflight.get("permanent")),
                )
            result = IndependentReasoningResult(
                request_id=request.request_id,
                deployment_id=request.deployment_id,
                status="rejected" if preflight.get("permanent") else "deferred",
                started_at=started_at,
                completed_at=utc_now_iso(),
                duration_ms=int((time.perf_counter() - started) * 1000),
                account_ids=request.account_ids,
                symbols=request.symbols,
                retryable=not bool(preflight.get("permanent")),
                retry_after_seconds=int(preflight.get("retryAfterSeconds") or 15),
                reason=str(preflight.get("reason") or "Point-in-time input is not ready."),
                stage_durations_ms=stages,
                reasoning_case_id=reasoning_case.case_id if reasoning_case else "",
                reasoning_case_stage=reasoning_case.stage if reasoning_case else "",
                reasoning_lane=reasoning_case.fact_delta.lane if reasoning_case else "",
                release_fingerprint=str(self._release_identity.get("releaseFingerprint") or ""),
                validation_cohort_id=str(self._release_identity.get("validationCohortId") or ""),
            )
            return self.remember(result)

        snapshots = list(assembled.get("snapshots") or [])
        if reasoning_case is not None:
            reasoning_case = self.reasoning_orchestrator.input_ready(reasoning_case.case_id)
        projection_started = time.perf_counter()
        projection_results = self.inference_executor.execute(request, snapshots)
        projection_total_ms = int((time.perf_counter() - projection_started) * 1000)
        # The scoped executor publishes after each successful leader account
        # so following accounts in the same batch can reuse the exact market
        # head. Publishing the whole batch again here previously doubled the
        # MySQL work and advanced equivalent shared heads unnecessarily.
        shared_publication = dict(
            getattr(self.inference_executor, "last_shared_publication", {})
            or {"status": "not-configured"}
        )
        stages["sharedInferencePublicationMs"] = int(
            getattr(self.inference_executor, "last_shared_publication_ms", 0) or 0
        )
        stages["projectionAndInferenceMs"] = max(
            0,
            projection_total_ms - stages["sharedInferencePublicationMs"],
        )
        identities = {
            account_id: projection_inference_identity(value)
            for account_id, value in projection_results.items()
        }
        verified_accounts = [account_id for account_id, value in identities.items() if value["verified"]]
        failed_accounts = [account_id for account_id, value in identities.items() if not value["verified"]]
        compact_projections = {
            account_id: compact_projection_result(value)
            for account_id, value in projection_results.items()
        }
        if reasoning_case is not None and verified_accounts:
            reasoning_case = self.reasoning_orchestrator.inference_completed(
                reasoning_case.case_id,
                identities,
                compact_projections,
                stages["projectionAndInferenceMs"],
            )
        candidate_started = time.perf_counter()
        candidates = self.candidate_builder.build(
            request,
            snapshots,
            assembled.get("previousByAccount") or {},
            projection_results,
            force=force,
        )
        stages["candidateBuildMs"] = int((time.perf_counter() - candidate_started) * 1000)
        detected_events = list(candidates.get("detected") or [])
        ready_events = list(candidates.get("ready") or [])
        decision_syntheses = list(candidates.get("syntheses") or [])
        if reasoning_case is not None and verified_accounts:
            reasoning_case = self.reasoning_orchestrator.hypotheses_ready(
                reasoning_case.case_id,
                detected_events,
            )
            reasoning_case = self.reasoning_orchestrator.decisions_synthesized(
                reasoning_case.case_id,
                decision_syntheses,
            )
            self.reasoning_orchestrator.attach_case_context(
                reasoning_case.case_id,
                [*detected_events, *ready_events],
            )
        delivery_authorized = bool(self.delivery_authorized_provider())
        delivery_events = []
        ai_handoff_status = "shadow-delivery-blocked"
        if delivery_authorized and self.cycle_recorder is not None:
            delivery_started = time.perf_counter()
            cycle = self.cycle_recorder.record_cycle(
                [str(getattr(snapshot, "account_id", "") or "") for snapshot in snapshots],
                snapshots,
                ready_events,
                dry_run=False,
                source_snapshot_replay=True,
            )
            delivery_events = list(getattr(cycle, "delivered_events", None) or ready_events)
            ai_handoff_status = "notification-queue-enqueued" if int(getattr(cycle, "queued", 0) or 0) else "no-delivery-candidate"
            stages["deliveryHandoffMs"] = int((time.perf_counter() - delivery_started) * 1000)
        elif delivery_authorized:
            ai_handoff_status = "delivery-recorder-unavailable"

        retry_policies = {
            account_id: projection_retry_policy(projection_results.get(account_id))
            for account_id in failed_accounts
        }
        retryable = any(bool(policy.get("retryable")) for policy in retry_policies.values())
        retry_after = max([
            int(policy.get("retryAfterSeconds") or 0)
            for policy in retry_policies.values()
        ] or [0])
        status = (
            "ok"
            if verified_accounts and not failed_accounts
            else "deferred"
            if retryable
            else "partial"
            if verified_accounts
            else "blocked"
        )
        if status == "ok":
            reason = "Independent V2 TypeDB inference completed."
        elif status == "partial":
            reason = "Independent V2 completed only part of the requested account scope."
        else:
            reason = str(next(
                (
                    retry_policies.get(account_id, {}).get("reason")
                    or (projection_results.get(account_id) or {}).get("reason")
                    for account_id in failed_accounts
                    if retry_policies.get(account_id, {}).get("reason")
                    or (projection_results.get(account_id) or {}).get("reason")
                ),
                "Independent V2 TypeDB inference is not ready.",
            ))
        if reasoning_case is not None:
            if not verified_accounts:
                reasoning_case = self.reasoning_orchestrator.defer(
                    reasoning_case.case_id,
                    reason,
                    retryable=retryable,
                )
            elif not delivery_authorized or not ready_events or ai_handoff_status == "no-delivery-candidate":
                reasoning_case = self.reasoning_orchestrator.complete_without_ai(
                    reasoning_case.case_id,
                    "Shadow execution or delivery policy produced no AI judgement request.",
                    source="typedb-shadow" if not delivery_authorized else "typedb-no-delivery",
                )
        source_ids = tuple(
            value["sourceAboxSnapshotId"] for value in identities.values()
            if value["sourceAboxSnapshotId"]
        )
        generation_ids = tuple(
            value["inferenceGenerationId"] for value in identities.values()
            if value["inferenceGenerationId"]
        )
        result = IndependentReasoningResult(
            request_id=request.request_id,
            deployment_id=request.deployment_id,
            status=status,
            started_at=started_at,
            completed_at=utc_now_iso(),
            duration_ms=int((time.perf_counter() - started) * 1000),
            account_ids=tuple(str(getattr(snapshot, "account_id", "") or "") for snapshot in snapshots),
            symbols=request.symbols,
            source_abox_snapshot_ids=source_ids,
            inference_generation_ids=generation_ids,
            projection_results=compact_projections,
            candidate_events=tuple(alert_event_payload(event) for event in detected_events),
            decision_syntheses=tuple(
                item.to_dict() if hasattr(item, "to_dict") else dict(item or {})
                for item in decision_syntheses
            ),
            delivery_events=tuple(alert_event_payload(event) for event in delivery_events),
            delivery_authorized=delivery_authorized,
            ai_handoff_status=ai_handoff_status,
            trace_complete=bool(identities) and all(
                value["verified"] and value["sourceAboxSnapshotId"] and value["inferenceGenerationId"]
                for value in identities.values()
            ),
            retryable=retryable,
            retry_after_seconds=max(1, retry_after) if retryable else 0,
            reason=reason,
            stage_durations_ms=stages,
            reasoning_case_id=reasoning_case.case_id if reasoning_case else "",
            reasoning_case_stage=reasoning_case.stage if reasoning_case else "",
            reasoning_lane=reasoning_case.fact_delta.lane if reasoning_case else "",
            release_fingerprint=str(self._release_identity.get("releaseFingerprint") or ""),
            validation_cohort_id=str(self._release_identity.get("validationCohortId") or ""),
            shared_inference=shared_publication,
        )
        return self.remember(result)

    def remember(self, result: IndependentReasoningResult) -> IndependentReasoningResult:
        self.last_result = result
        self.results_by_request_id[result.request_id] = result.to_dict()
        while len(self.results_by_request_id) > 100:
            self.results_by_request_id.pop(next(iter(self.results_by_request_id)))
        return result

    def health(self) -> Dict[str, object]:
        result = self.last_result.to_dict() if self.last_result else {}
        return {
            "status": "ready" if not result or result.get("status") in {"ok", "partial"} else "degraded",
            "engineVersion": "v2",
            "independentExecution": True,
            "directSourceEvents": True,
            "monitorRunnerUsed": False,
            "lastResult": result,
        }

    def explain(self, decision_id: str) -> Dict[str, object]:
        return dict(self.results_by_request_id.get(str(decision_id or "")) or {})


class IndependentReasoningJobRunner:
    """Lease direct V2 requests and execute them independently of V1."""

    def __init__(
        self,
        queue,
        engine: V2ReasoningEngine,
        registry,
        settings=None,
        worker_id: str = "",
        event_reader=None,
        execution_guard=None,
        route_reconciler=None,
    ):
        import os
        import socket
        import uuid

        self.queue = queue
        self.engine = engine
        self.registry = registry
        self.settings = dict(settings or {})
        self.event_reader = event_reader
        self.execution_guard = execution_guard
        self.route_reconciler = route_reconciler
        self.route_reconciliation = None
        self.route_reconciled_at = 0.0
        self.worker_id = worker_id or (socket.gethostname() + ":" + str(os.getpid()) + ":v2-" + uuid.uuid4().hex[:8])

    def enabled(self) -> bool:
        return str(self.settings.get("reasoningEngineV2IndependentEnabled") or "1").strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def run_once(self) -> Dict[str, object]:
        if not self.enabled():
            return {"status": "disabled", "processedCount": 0}
        descriptor = self.engine.descriptor()
        route_reconciliation = self.reconcile_ingress_route()
        lease_recovery = self.recover_dead_local_leases(descriptor.deployment_id)
        repaired = self.repair_ingress(descriptor.deployment_id)
        backlog_compaction = self.compact_backlog(descriptor.deployment_id)
        guard = self.execution_readiness()
        if not bool(guard.get("ready", True)):
            health = dict((self.registry.get(descriptor.deployment_id) or {}).get("health") or {})
            health.update({
                "status": "deferred",
                "independentExecution": True,
                "directSourceEvents": True,
                "monitorRunnerUsed": False,
                "lastRunAt": utc_now_iso(),
                "executionGuard": guard,
                "queue": self.queue_summary(descriptor.deployment_id),
            })
            self.registry.update_health(descriptor.deployment_id, health)
            return {
                "status": "deferred",
                "processedCount": 0,
                "retryable": True,
                "retryAfterSeconds": int(guard.get("retryAfterSeconds") or 30),
                "reason": str(
                    guard.get("reason")
                    or "The V2 TypeDB execution boundary is temporarily unavailable."
                )[:300],
                "executionGuard": guard,
                "repairedIngressCount": repaired,
                "leaseRecovery": lease_recovery,
                "backlogCompaction": backlog_compaction,
                "queue": health["queue"],
                "routeReconciliation": route_reconciliation,
            }
        lane_provider = getattr(self.queue, "next_lane", None)
        lane_hint = str(lane_provider(descriptor.deployment_id) or "") if callable(lane_provider) else ""
        claim_limit = (
            self.lane_batch_limit(lane_hint)
            if lane_hint
            else _int_setting(self.settings, "reasoningEngineV2BatchSize", 6, 1, 20)
        )
        claim_kwargs = {
            "limit": claim_limit,
            "lease_seconds": _int_setting(
                self.settings,
                "reasoningEngineV2LeaseSeconds",
                600,
                60,
                3600,
            ),
        }
        if lane_hint and "reasoning_lane" in inspect.signature(self.queue.claim).parameters:
            claim_kwargs["reasoning_lane"] = lane_hint
        jobs = self.queue.claim(
            descriptor.deployment_id,
            self.worker_id,
            **claim_kwargs,
        )
        if not jobs:
            return {
                "status": "idle",
                "processedCount": 0,
                "repairedIngressCount": repaired,
                "leaseRecovery": lease_recovery,
                "backlogCompaction": backlog_compaction,
                "queue": self.queue_summary(descriptor.deployment_id),
                "routeReconciliation": route_reconciliation,
            }
        jobs, resharded = self.reshard_oversized_jobs(jobs)
        if not jobs:
            return {
                "status": "resharded" if resharded else "deferred",
                "processedCount": 0,
                "reshardedJobCount": len(resharded),
                "reshardedJobs": resharded,
                "repairedIngressCount": repaired,
                "leaseRecovery": lease_recovery,
                "backlogCompaction": backlog_compaction,
                "queue": self.queue_summary(descriptor.deployment_id),
            }
        batch_key = self.batch_compatibility_key(jobs[0])
        reasoning_lane = self.reasoning_lane(jobs[0])
        compatible_jobs = [job for job in jobs if self.batch_compatibility_key(job) == batch_key]
        lane_limit = self.lane_batch_limit(reasoning_lane)
        selected_jobs, capacity_deferred_jobs = self.select_native_bounded_jobs(
            compatible_jobs[:lane_limit]
        )
        deferred_jobs = [job for job in jobs if job not in selected_jobs]
        for job in deferred_jobs:
            capacity_deferred = job in capacity_deferred_jobs
            self.queue.defer(
                job["jobId"],
                (
                    "The current V2 micro-batch reached the native TypeDB target-symbol limit."
                    if capacity_deferred
                    else "A different point-in-time boundary owns the current V2 batch."
                ),
                1,
            )
        job_ids = [job["jobId"] for job in selected_jobs]
        try:
            release_provider = getattr(self.engine, "release_identity", None)
            release_identity = dict(release_provider() or {}) if callable(release_provider) else {}
            binder = getattr(self.queue, "bind_release", None)
            if callable(binder):
                binder(job_ids, release_identity, reasoning_lane)
            events = [
                DomainEvent.from_dict(dict(job.get("sourceEvent") or {}))
                for job in selected_jobs
            ]
            stop_heartbeat = threading.Event()
            lease_lost = threading.Event()
            heartbeat = threading.Thread(
                target=self.heartbeat_loop,
                args=(job_ids, stop_heartbeat, lease_lost),
                name="v2-reasoning-heartbeat-" + self.worker_id[-12:],
                daemon=True,
            )
            heartbeat.start()
            try:
                result = self.engine.consume(events)
            finally:
                stop_heartbeat.set()
                heartbeat.join(timeout=max(1, self.heartbeat_seconds() + 1))
            if lease_lost.is_set():
                raise RuntimeError("The V2 reasoning lease was lost during TypeDB execution.")
            result["batch_job_count"] = len(selected_jobs)
            result["batch_job_ids"] = job_ids
            result["reasoning_lane"] = reasoning_lane
            result["native_target_symbol_limit"] = self.native_target_symbol_limit()
            result["capacity_deferred_job_count"] = len(capacity_deferred_jobs)
            status = str(result.get("status") or "")
            if status == "deferred" and result.get("retryable"):
                for job_id in job_ids:
                    self.queue.defer(
                        job_id,
                        str(result.get("reason") or "V2 input is not ready."),
                        int(result.get("retry_after_seconds") or result.get("retryAfterSeconds") or 15),
                    )
                outcome = "deferred"
            elif status == "rejected" and callable(getattr(self.queue, "supersede", None)):
                for job_id in job_ids:
                    self.queue.supersede(
                        job_id,
                        str(result.get("reason") or "The immutable reasoning source was rejected."),
                    )
                outcome = "superseded"
            else:
                for job_id in job_ids:
                    parameters = inspect.signature(self.queue.complete).parameters
                    if "worker_id" in parameters:
                        self.queue.complete(job_id, result, worker_id=self.worker_id)
                    else:
                        self.queue.complete(job_id, result)
                outcome = "completed"
            health = dict((self.registry.get(descriptor.deployment_id) or {}).get("health") or {})
            health.update(self.engine.health())
            health.update({
                "lastJobId": job_ids[-1],
                "lastJobIds": job_ids,
                "lastRunAt": utc_now_iso(),
                "queue": self.queue_summary(descriptor.deployment_id),
            })
            if str(health.get("status") or "") == "ready":
                health.pop("lastError", None)
                health.pop("executionGuard", None)
            health["routeReconciliation"] = route_reconciliation
            self.registry.update_health(descriptor.deployment_id, health)
            return {
                "status": outcome,
                "processedCount": len(selected_jobs),
                "reshardedJobCount": len(resharded),
                "repairedIngressCount": repaired,
                "leaseRecovery": lease_recovery,
                "backlogCompaction": backlog_compaction,
                "jobId": job_ids[-1],
                "jobIds": job_ids,
                "result": result,
                "queue": health["queue"],
                "routeReconciliation": route_reconciliation,
            }
        except Exception as error:  # noqa: BLE001 - durable retry owns recovery.
            retries = [
                self.queue.retry(
                    job_id,
                    str(error),
                    max_attempts=_int_setting(self.settings, "reasoningEngineV2MaxAttempts", 3, 1, 8),
                )
                for job_id in job_ids
            ]
            terminal = bool(retries) and all(retry.get("terminal") for retry in retries)
            health = dict((self.registry.get(descriptor.deployment_id) or {}).get("health") or {})
            health.update({
                "status": "blocked" if terminal else "degraded",
                "independentExecution": True,
                "directSourceEvents": True,
                "monitorRunnerUsed": False,
                "lastJobId": job_ids[-1],
                "lastJobIds": job_ids,
                "lastError": str(error)[:300],
                "lastRunAt": utc_now_iso(),
                "queue": self.queue_summary(descriptor.deployment_id),
            })
            self.registry.update_health(descriptor.deployment_id, health)
            return {
                "status": "failed" if terminal else "retry",
                "processedCount": len(selected_jobs),
                "reshardedJobCount": len(resharded),
                "repairedIngressCount": repaired,
                "leaseRecovery": lease_recovery,
                "backlogCompaction": backlog_compaction,
                "jobId": job_ids[-1],
                "jobIds": job_ids,
                "reason": str(error)[:300],
                "retries": retries,
                "routeReconciliation": route_reconciliation,
            }

    def reconcile_ingress_route(self) -> Dict[str, object]:
        interval = _int_setting(
            self.settings,
            "reasoningEngineIngressReconcileSeconds",
            60,
            5,
            3600,
        )
        now = time.monotonic()
        if self.route_reconciliation is not None and now - self.route_reconciled_at < interval:
            return dict(self.route_reconciliation)
        if not callable(self.route_reconciler):
            self.route_reconciliation = {"status": "not-configured"}
            return dict(self.route_reconciliation)
        try:
            self.route_reconciliation = dict(self.route_reconciler() or {"status": "unchanged"})
            self.route_reconciled_at = now
        except Exception as error:  # The active V2 queue remains independently recoverable.
            return {"status": "error", "reason": str(error)[:240]}
        return dict(self.route_reconciliation)

    def execution_readiness(self) -> Dict[str, object]:
        if not callable(self.execution_guard):
            return {"ready": True, "status": "not-configured"}
        try:
            result = self.execution_guard()
        except Exception as error:  # noqa: BLE001 - TypeDB writes fail closed when the guard is unavailable.
            return {
                "ready": False,
                "status": "unavailable",
                "reason": "V2 TypeDB execution guard failed: " + str(error)[:220],
                "retryAfterSeconds": 30,
            }
        values = dict(result or {}) if isinstance(result, Mapping) else {}
        values.setdefault("ready", False)
        values.setdefault("status", "unavailable")
        if not values.get("ready"):
            values.setdefault("retryAfterSeconds", 30)
        return values

    @staticmethod
    def batch_compatibility_key(job: Mapping[str, object]):
        event = dict(job.get("sourceEvent") or {})
        payload = dict(event.get("payload") or {})
        raw_account_ids = payload.get("accountIds") or []
        if isinstance(raw_account_ids, str):
            raw_account_ids = [raw_account_ids]
        account_ids = tuple(sorted({
            str(value or "").strip()
            for value in [*raw_account_ids, payload.get("accountId")]
            if str(value or "").strip()
        }))
        boundary = payload.get("verifiedSourceSnapshot")
        boundary = dict(boundary or {}) if isinstance(boundary, Mapping) else {}
        boundaries = [
            dict(value)
            for value in payload.get("verifiedSourceSnapshots") or []
            if isinstance(value, Mapping) and value
        ]
        boundary_key = tuple(sorted(
            (
                str(value.get("accountId") or ""),
                str(value.get("snapshotId") or ""),
                str(value.get("generatedAt") or ""),
            )
            for value in boundaries
        ))
        return (
            account_ids,
            str(boundary.get("accountId") or ""),
            str(boundary.get("snapshotId") or ""),
            str(boundary.get("generatedAt") or ""),
            boundary_key,
            IndependentReasoningJobRunner.reasoning_lane(job),
        )

    @staticmethod
    def reasoning_lane(job: Mapping[str, object]) -> str:
        event = DomainEvent.from_dict(dict(job.get("sourceEvent") or {}))
        deployment_id = str(job.get("deploymentId") or "reasoning-v2")
        request = independent_reasoning_request(deployment_id, [event])
        return FactDelta.from_request(request).lane

    def lane_batch_limit(self, lane: str) -> int:
        key, fallback = {
            "REALTIME": ("reasoningEngineV2RealtimeBatchSize", 2),
            "CONTEXT": ("reasoningEngineV2ContextBatchSize", 3),
            "RECONCILIATION": ("reasoningEngineV2ReconciliationBatchSize", 6),
        }.get(str(lane or "CONTEXT"), ("reasoningEngineV2ContextBatchSize", 3))
        return _int_setting(self.settings, key, fallback, 1, 20)

    def native_target_symbol_limit(self) -> int:
        return _int_setting(
            self.settings,
            "typedbNativeRuleTargetSymbolLimit",
            4,
            1,
            200,
        )

    @staticmethod
    def job_symbols(job: Mapping[str, object]):
        event = DomainEvent.from_dict(dict(job.get("sourceEvent") or {}))
        return tuple(reasoning_event_scope(event).get("symbols") or [])

    def select_native_bounded_jobs(self, jobs):
        """Fit compatible jobs into one complete native subject generation.

        The projection adapter enforces the same target-symbol limit. Keeping
        the queue selection within that boundary prevents a six-job
        reconciliation batch from completing even though TypeDB evaluated
        only its first four unique symbols.
        """
        selected = []
        deferred = []
        selected_symbols = set()
        limit = self.native_target_symbol_limit()
        for job in jobs or []:
            symbols = set(self.job_symbols(job))
            if len(symbols) > limit:
                deferred.append(job)
                continue
            combined = selected_symbols | symbols
            if selected and symbols and len(combined) > limit:
                deferred.append(job)
                continue
            selected.append(job)
            selected_symbols = combined
        return selected, deferred

    def reshard_oversized_jobs(self, jobs):
        """Never let TypeDB see a job wider than its native subject bound."""

        bounded = []
        resharded = []
        limit = self.native_target_symbol_limit()
        callback = getattr(self.queue, "reshard_claimed_job", None)
        for job in jobs or []:
            symbols = self.job_symbols(job)
            if len(symbols) <= limit:
                bounded.append(job)
                continue
            if not callable(callback):
                self.queue.defer(
                    job["jobId"],
                    "Oversized V2 source event requires durable symbol sharding before execution.",
                    5,
                )
                continue
            event = DomainEvent.from_dict(dict(job.get("sourceEvent") or {}))
            outcome = dict(callback(
                job["jobId"],
                event,
                limit,
                worker_id=self.worker_id,
            ) or {})
            if str(outcome.get("status") or "") == "unchanged":
                bounded.append(job)
                continue
            resharded.append({
                "jobId": str(job.get("jobId") or ""),
                "sourceSymbolCount": len(symbols),
                "nativeTargetSymbolLimit": limit,
                **outcome,
            })
        return bounded, resharded

    def heartbeat_seconds(self) -> int:
        return _int_setting(self.settings, "reasoningEngineV2HeartbeatSeconds", 15, 2, 120)

    def heartbeat_loop(self, job_ids, stop_event, lease_lost) -> None:
        callback = getattr(self.queue, "heartbeat", None)
        if not callable(callback):
            return
        lease_seconds = _int_setting(
            self.settings,
            "reasoningEngineV2LeaseSeconds",
            600,
            60,
            3600,
        )
        while not stop_event.wait(self.heartbeat_seconds()):
            try:
                alive = callback(job_ids, self.worker_id, lease_seconds)
            except Exception:
                continue
            if not alive:
                lease_lost.set()
                return

    def queue_summary(self, deployment_id: str) -> Dict[str, object]:
        release_provider = getattr(self.engine, "release_identity", None)
        release = dict(release_provider() or {}) if callable(release_provider) else {}
        parameters = inspect.signature(self.queue.summary).parameters
        if "release_fingerprint" in parameters:
            return self.queue.summary(
                deployment_id,
                release_fingerprint=str(release.get("releaseFingerprint") or ""),
                validation_cohort_id=str(release.get("validationCohortId") or ""),
            )
        return self.queue.summary(deployment_id)

    def recover_dead_local_leases(self, deployment_id: str) -> Dict[str, object]:
        callback = getattr(self.queue, "recover_dead_local_leases", None)
        if not callable(callback):
            return {"status": "unsupported", "recoveredCount": 0}
        try:
            result = callback(
                deployment_id,
                current_worker_id=self.worker_id,
            )
        except Exception as error:  # noqa: BLE001 - normal lease expiry remains available.
            return {
                "status": "error",
                "recoveredCount": 0,
                "reason": str(error)[:180],
            }
        return dict(result or {"status": "unchanged", "recoveredCount": 0})

    def compact_backlog(self, deployment_id: str) -> Dict[str, object]:
        callback = getattr(self.queue, "compact_supersedable_backlog", None)
        if not callable(callback):
            return {"status": "unsupported", "compactedCount": 0}
        try:
            return dict(callback(deployment_id) or {"status": "unchanged"})
        except Exception as error:  # The durable queue remains claimable without compaction.
            return {
                "status": "error",
                "compactedCount": 0,
                "reason": str(error)[:180],
            }

    def repair_ingress(self, deployment_id: str) -> int:
        reader = getattr(self.event_reader, "unmaterialized_reasoning_engine_events", None)
        ingress = getattr(self.queue, "ingress_event", None)
        if not callable(reader) or not callable(ingress):
            return 0
        hours = _int_setting(
            self.settings,
            "reasoningEngineV2IngressRepairLookbackHours",
            6,
            1,
            168,
        )
        limit = _int_setting(
            self.settings,
            "reasoningEngineV2IngressRepairBatchSize",
            20,
            1,
            200,
        )
        cutoff = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - (hours * 60 * 60),
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
        events = reader(deployment_id, after_occurred_at=cutoff, limit=limit)
        return sum(1 for event in events or [] if ingress(event).get("saved"))

    def watch(self) -> None:
        interval = _int_setting(self.settings, "reasoningEngineV2IntervalSeconds", 5, 1, 300)
        while True:
            result = self.run_once()
            if result.get("status") == "idle":
                time.sleep(interval)
            elif result.get("status") in {"disabled", "deferred"}:
                time.sleep(interval)
