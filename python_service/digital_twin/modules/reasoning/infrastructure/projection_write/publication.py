"""Publication implementation; facade-independent dependencies."""

from __future__ import annotations
from .publication_ports import PublicationPort
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_current_state import (
    CURRENT_STATE_ABOX_PERSISTENCE_MODE,
)
from typing import Dict, List


def acquire_inference_write_lease(
    _store: PublicationPort, result: Dict[str, object], world_id: str = ""
) -> Dict[str, object]:
    """Serialize ABox preparation and native InferenceBox publication."""
    acquire = getattr(_store.repository, "acquire_scoped_abox_write_lease", None)
    if not callable(acquire):
        return {"status": "unsupported"}
    adopted_coordinator = result.get("_projectionCoordinatorLease")
    adopted_coordinator = (
        dict(adopted_coordinator or {}) if isinstance(adopted_coordinator, dict) else {}
    )
    coordinator_owned_here = False
    coordinator_lease = adopted_coordinator
    if not bool(coordinator_lease.get("acquired")):
        coordinator_lease = _store.acquire_projection_coordinator_lease(
            "native-inference",
            world_id,
        )
        coordinator_owned_here = bool(coordinator_lease.get("acquired"))
    if not bool(coordinator_lease.get("acquired")):
        return {
            "acquired": False,
            "status": "deferred-projection-coordinator",
            "reason": str(
                coordinator_lease.get("reason")
                or "다른 TypeDB World 투영이 데이터베이스 쓰기 경계를 사용 중입니다."
            )[:220],
            "recommendedRetryAfterSeconds": int(
                coordinator_lease.get("recommendedRetryAfterSeconds") or 10
            ),
            "projectionCoordinator": _store.projection_coordinator_summary(
                coordinator_lease
            ),
        }
    pending = (
        result.get("pendingAboxActivation")
        if isinstance(result.get("pendingAboxActivation"), dict)
        else {}
    )
    candidate_id = str(
        pending.get("candidateAboxSnapshotId")
        or result.get("aboxSnapshotId")
        or result.get("worldviewManifestId")
        or "native-rule"
    ).strip()
    try:
        lease = dict(
            _store.repository_world_call(
                "acquire_scoped_abox_write_lease",
                "inference:" + candidate_id,
                world_id=world_id,
            )
            or {}
        )
    except (
        Exception
    ) as error:  # noqa: BLE001 - do not activate without the writer boundary.
        lease = {"acquired": False, "status": "error", "reason": str(error)[:180]}
    coordinator_release = {}
    if not bool(lease.get("acquired")) and coordinator_owned_here:
        coordinator_release = _store.release_projection_coordinator_lease(
            coordinator_lease
        )
        coordinator_lease = {
            **coordinator_lease,
            "acquired": False,
            "status": "released-after-world-lease-failure",
        }
    return {
        **lease,
        "projectionCoordinator": {
            **_store.projection_coordinator_summary(coordinator_lease),
            **({"release": coordinator_release} if coordinator_release else {}),
        },
        "_projectionCoordinatorLease": coordinator_lease,
        "projectionCoordinatorLeaseOwned": coordinator_owned_here,
    }


def release_inference_write_lease(
    _store: PublicationPort, lease: Dict[str, object]
) -> Dict[str, object]:
    """Release the per-world lease first, then this call's global lease."""
    release = {"status": "not-owner"}
    releaser = getattr(_store.repository, "release_scoped_abox_write_lease", None)
    if callable(releaser):
        try:
            release = dict(releaser(lease) or {})
        except (
            Exception
        ) as error:  # noqa: BLE001 - the global release still must be attempted.
            release = {"status": "error", "reason": str(error)[:180]}
    coordinator_release = {"status": "adopted-by-caller"}
    coordinator = (
        lease.get("_projectionCoordinatorLease") if isinstance(lease, dict) else {}
    )
    if bool(lease.get("projectionCoordinatorLeaseOwned")) and isinstance(
        coordinator, dict
    ):
        coordinator_release = _store.release_projection_coordinator_lease(coordinator)
    return {
        "status": str(release.get("status") or "unknown"),
        "worldLease": release,
        "projectionCoordinator": coordinator_release,
    }


def reconcile_abox_activation_after_inference(
    _store: PublicationPort,
    result: Dict[str, object],
    inference_symbols: List[str],
    world_id: str = "",
) -> None:
    """Keep active ABox and InferenceBox on the same verified generation.

    ABox candidate persistence must precede TypeDB function evaluation, so
    the pointer is briefly switched before the InferenceBox is known. The
    predecessor stays retained until this method confirms an aligned native
    generation. Any failed or incomplete native execution restores the
    predecessor instead of exposing an ABox that cannot support judgement.
    """
    if _store.active_graph_store_key(result) != "typedb":
        return
    verification = result.get("aboxPersistenceVerification")
    verification = verification if isinstance(verification, dict) else {}
    activation = (
        verification.get("activation")
        if isinstance(verification.get("activation"), dict)
        else {}
    )
    active_snapshot_id = str(
        activation.get("snapshotId") or result.get("aboxSnapshotId") or ""
    ).strip()
    previous_snapshot_id = str(activation.get("previousSnapshotId") or "").strip()
    activation_is_new = str(activation.get("status") or "") == "activated" and bool(
        result.get("saved")
    )
    if not activation_is_new:
        pending_reader = getattr(_store.repository, "pending_abox_activation", None)
        if not callable(pending_reader):
            return
        try:
            pending = _store.repository_world_call(
                "pending_abox_activation", world_id=world_id
            )
        except (
            Exception
        ):  # noqa: BLE001 - the current inference result remains independently observable.
            return
        if str((pending or {}).get("status") or "") != "pending":
            return
        active_snapshot_id = str(
            (pending or {}).get("candidateAboxSnapshotId") or active_snapshot_id
        ).strip()
        previous_snapshot_id = str(
            (pending or {}).get("previousAboxSnapshotId") or ""
        ).strip()
        if not active_snapshot_id:
            return
    inferencebox = (
        result.get("inferenceBox")
        if isinstance(result.get("inferenceBox"), dict)
        else {}
    )
    alignment = _store.inference_alignment_diagnostics(
        inferencebox,
        active_snapshot_id,
        inference_symbols,
    )
    result["inferenceAlignment"] = alignment
    if _store.inference_result_is_reusable(
        inferencebox,
        {"aboxSnapshotId": active_snapshot_id},
        inference_symbols,
    ):
        finalizer = getattr(_store.repository, "finalize_abox_generation", None)
        if not callable(finalizer):
            return
        try:
            result["aboxActivationFinalization"] = _store.repository_world_call(
                "finalize_abox_generation",
                active_snapshot_id,
                previous_snapshot_id,
                world_id=world_id,
            )
        except (
            Exception
        ) as error:  # noqa: BLE001 - cleanup may be retried without invalidating aligned reasoning.
            result["aboxActivationFinalization"] = {
                "status": "error",
                "reason": str(error)[:180],
                "activeAboxSnapshotId": active_snapshot_id,
                "previousAboxSnapshotId": previous_snapshot_id,
            }
        finalization = (
            dict(result.get("aboxActivationFinalization") or {})
            if isinstance(result.get("aboxActivationFinalization"), dict)
            else {}
        )
        if str(finalization.get("status") or "") != "ok":
            # The native generation was already proven against this
            # active ABox.  A failure to clear the small activation
            # journal is a control-plane retry, not evidence that the
            # TypeDB projection itself is unsafe.  Keep the event pending
            # and resume finalization through the bounded recovery path;
            # importantly, do not let one journal write outage open the
            # global queue circuit for every other symbol.
            result["saved"] = False
            result["status"] = "inference-finalization-pending"
            result["preservedActiveGeneration"] = True
            result["retryable"] = True
            result["recommendedRetryAfterSeconds"] = 10
            result["reason"] = (
                "TypeDB 네이티브 추론 세대는 검증됐지만 ABox 완료 표식 정리가 보류되었습니다. "
                + str(
                    finalization.get("reason")
                    or "다음 짧은 재시도에서 완료 처리합니다."
                )[:180]
            )
        return

    rollback = {
        "status": "unavailable",
        "reason": "No verified predecessor ABox generation is available for restoration.",
    }
    restore = getattr(_store.repository, "activate_abox_generation", None)
    if previous_snapshot_id and callable(restore):
        try:
            rollback = _store.repository_world_call(
                "activate_abox_generation",
                previous_snapshot_id,
                world_id=world_id,
            )
        except (
            Exception
        ) as error:  # noqa: BLE001 - preserve the explicit blocked state when restore itself fails.
            rollback = {"status": "error", "reason": str(error)[:180]}
    result["activationRollback"] = rollback
    result["saved"] = False
    result["preservedActiveGeneration"] = str(rollback.get("status") or "") == "ok"
    result["status"] = (
        "inference-failed-rolled-back"
        if result["preservedActiveGeneration"]
        else "inference-failed-no-rollback"
    )
    native_failure = result.get("nativeRuleFailure")
    native_failure = (
        dict(native_failure or {}) if isinstance(native_failure, dict) else {}
    )
    failure_rule_id = str(native_failure.get("ruleId") or "").strip()
    failure_reason = str(native_failure.get("reason") or "").strip()
    if failure_reason:
        result["reason"] = (
            "TypeDB 네이티브 규칙 실행 실패"
            + ((" (" + failure_rule_id + ")") if failure_rule_id else "")
            + ": "
            + failure_reason[:500]
            + " "
            + (
                "이전 검증 세대로 복원했습니다."
                if result["preservedActiveGeneration"]
                else "투자 추론을 차단했습니다."
            )
            + " 정렬 진단: "
            + str(alignment.get("summary") or "")
        )
    else:
        result["reason"] = (
            "TypeDB native InferenceBox가 새 ABox 세대와 정렬되지 않아 "
            + (
                "이전 검증 세대로 복원했습니다."
                if result["preservedActiveGeneration"]
                else "투자 추론을 차단했습니다."
            )
            + " "
            + str(alignment.get("summary") or "")
        )
    if result["preservedActiveGeneration"]:
        # The prior verified generation is still active. Keep the source
        # event pending and retry with back-pressure instead of opening a
        # failure circuit against a safe rollback.
        result["retryable"] = True
        result["recommendedRetryAfterSeconds"] = int(
            native_failure.get("recommendedRetryAfterSeconds") or 30
        )
    if isinstance(rollback.get("activeAbox"), dict):
        verification["activePointer"] = dict(rollback.get("activeAbox") or {})
        result["aboxPersistenceVerification"] = verification
    if result["preservedActiveGeneration"] and active_snapshot_id:
        # The previous active Manifest is restored synchronously because
        # that preserves judgement correctness. Physical deletion of the
        # failed immutable candidate can involve thousands of rows and
        # belongs to the same idle maintenance pass as normal retention.
        result["failedCandidateCleanup"] = {
            "status": "deferred",
            "aboxSnapshotId": active_snapshot_id,
            "reason": "Failed scoped ABox candidate is retained for idle maintenance cleanup.",
        }


def attach_abox_persistence_runtime_stages(
    runtime_stages: Dict[str, int], result: Dict[str, object]
) -> None:
    """Expose scoped ABox cost and categorical modes without mixing their types."""
    verification = result.get("aboxPersistenceVerification")
    timing = (
        dict(verification.get("timing") or {}) if isinstance(verification, dict) else {}
    )
    runtime_modes = result.setdefault("runtimeModes", {})
    if not isinstance(runtime_modes, dict):
        runtime_modes = {}
        result["runtimeModes"] = runtime_modes

    def record(source_key: str, target_key: str, source: Dict[str, object]) -> None:
        try:
            value = float(source.get(source_key))
        except (TypeError, ValueError):
            return
        runtime_stages[target_key] = int(round(value))

    for source_key, target_key in {
        "candidateCleanupMs": "aboxCandidateCleanupMs",
        "currentStateInventoryReadMs": "aboxCurrentStateInventoryReadMs",
        "changedScopeWriteMs": "aboxChangedScopeWriteMs",
        "changedScopeVerificationMs": "aboxChangedScopeVerificationMs",
        "manifestControlWriteMs": "aboxManifestControlWriteMs",
        "totalMs": "aboxScopedPersistenceTotalMs",
    }.items():
        record(source_key, target_key, timing)
    write_strategy = str(timing.get("currentStateWriteStrategy") or "").strip()
    if write_strategy:
        runtime_modes["aboxCurrentStateWriteStrategy"] = write_strategy
    write_plan = timing.get("changedScopeWritePlan")
    if isinstance(write_plan, dict):
        for source_key, target_key in {
            "totalQueryMs": "aboxChangedScopeQueryMs",
            "slowestQueryMs": "aboxChangedScopeSlowestQueryMs",
            "queryCount": "aboxChangedScopeQueryCount",
            "plannedRelationQueryCount": "aboxPlannedRelationQueryCount",
            "transactionCount": "aboxChangedScopeTransactionCount",
            "transactionQueryCount": "aboxChangedScopeTransactionQueryCount",
            "insertedNodeCount": "aboxInsertedNodeCount",
            "insertedRelationCount": "aboxInsertedRelationCount",
            "reusedNodeCount": "aboxReusedNodeCount",
            "reusedRelationCount": "aboxReusedRelationCount",
            "relationGivenBatchCount": "aboxRelationGivenBatchCount",
            "relationGivenRowCount": "aboxRelationGivenRowCount",
            "relationGivenFallbackCount": "aboxRelationGivenFallbackCount",
        }.items():
            record(source_key, target_key, write_plan)
        relation_write_mode = str(write_plan.get("relationWriteMode") or "").strip()
        if relation_write_mode:
            runtime_modes["aboxRelationWriteMode"] = relation_write_mode
        delta_delete = write_plan.get("deltaDelete")
        if isinstance(delta_delete, dict):
            for source_key, target_key in {
                "durationMs": "aboxCurrentStateDeleteMs",
                "queryCount": "aboxCurrentStateDeleteQueryCount",
                "transactionCount": "aboxCurrentStateDeleteTransactionCount",
            }.items():
                record(source_key, target_key, delta_delete)
    physical_verification = timing.get("changedScopeStorageIdentityVerification")
    if isinstance(physical_verification, dict):
        for source_key, target_key in {
            "manifestScopedReadCount": "aboxManifestVerificationReadCount",
            "reusedStorageIdentityCount": "aboxReusedPhysicalRowCount",
            "conflictCount": "aboxStorageIdentityConflictCount",
        }.items():
            record(source_key, target_key, physical_verification)


def native_preflight_projection_graph(
    _store: PublicationPort, graph: PortfolioOntology, persistence: Dict[str, object]
) -> PortfolioOntology:
    """Align the in-memory preflight graph with persisted physical slots.

    Current-state ABox persistence maps logical snapshot ids onto bounded
    physical slots. The repository's exact matched-evidence proof compares
    physical storage ids, so passing the logical graph would force a
    durable reread after every successful native query. Rebuild only that
    persistence view; inference semantics and TypeDB rule evaluation stay
    unchanged.
    """

    values = dict(persistence or {}) if isinstance(persistence, dict) else {}
    physical_mode = str(
        values.get("physicalStateMode") or values.get("persistenceMode") or ""
    )
    scope_plan = list(values.get("scopePlan") or [])
    mapper = getattr(_store.repository, "current_state_physical_graph", None)
    if (
        physical_mode != CURRENT_STATE_ABOX_PERSISTENCE_MODE
        or not scope_plan
        or not callable(mapper)
    ):
        return graph
    try:
        prepared = mapper(graph, scope_plan)
    except (
        Exception
    ):  # noqa: BLE001 - exact reuse is optional and fails closed downstream.
        return graph
    if isinstance(prepared, PortfolioOntology):
        prepared.worldview["nativePreflightPhysicalization"] = (
            "current-state-scope-plan"
        )
        return prepared
    return graph


def inference_alignment_diagnostics(
    inferencebox: Dict[str, object],
    expected_snapshot_id: str,
    required_symbols: List[str],
) -> Dict[str, object]:
    """Describe native generation alignment for retries and audit, not judgement."""
    payload = dict(inferencebox or {})
    expected_id = str(expected_snapshot_id or "").strip()
    actual_id = str(payload.get("sourceAboxSnapshotId") or "").strip()
    expected = sorted(
        {
            str(value or "").upper().strip()
            for value in required_symbols or []
            if str(value or "").strip()
        }
    )
    actual = sorted(
        {
            str(value or "").upper().strip()
            for value in payload.get("targetSymbols") or []
            if str(value or "").strip()
        }
    )
    native_completed = bool(
        payload.get("nativeTypeDbReasoningCompleted")
        or payload.get("typedbNativeRuleEvaluationCompleted")
        or payload.get("nativeTypeDbReasoningUsed")
    )
    issues: List[str] = []
    if not native_completed:
        issues.append("native-evaluation-not-complete")
    if not actual_id:
        issues.append("source-generation-missing")
    elif expected_id and actual_id != expected_id:
        issues.append("source-generation-mismatch")
    if payload.get("generationAligned") is False:
        issues.append("generation-alignment-flag-false")
    missing_symbols = sorted(set(expected).difference(actual))
    if missing_symbols:
        issues.append("target-symbol-coverage-missing")
    summary_by_issue = {
        "native-evaluation-not-complete": "네이티브 규칙 실행 완료 증거가 없습니다.",
        "source-generation-missing": "InferenceBox에 원본 ABox 세대가 없습니다.",
        "source-generation-mismatch": "InferenceBox 원본 ABox 세대가 후보 세대와 다릅니다.",
        "generation-alignment-flag-false": "InferenceBox가 세대 정렬 실패로 표시됐습니다.",
        "target-symbol-coverage-missing": "요청 종목 전체를 포함한 InferenceBox 결과가 아닙니다.",
    }
    return {
        "status": "aligned" if not issues else "misaligned",
        "retryable": bool(issues),
        "expectedAboxSnapshotId": expected_id,
        "actualSourceAboxSnapshotId": actual_id,
        "expectedTargetSymbols": expected,
        "actualTargetSymbols": actual,
        "missingTargetSymbols": missing_symbols,
        "nativeEvaluationCompleted": native_completed,
        "generationAligned": payload.get("generationAligned"),
        "issues": issues,
        "summary": " ".join(summary_by_issue[item] for item in issues),
    }
