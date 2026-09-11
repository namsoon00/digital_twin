"""Verified candidate activation and aligned-inference finalization."""

from typing import Dict, Iterable

from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import clean_symbols_from_payload
from .controls import AtomicControlPatchTooLarge
from .ports import ABoxControlStore, ABoxRuntime
from .world_calls import typedb_call_for_world


def activate_scoped_abox_manifest(
    store: ABoxControlStore,
    manifest_id: str,
    previous_metadata: Dict[str, object] = None,
    pending_activation: bool = False,
    inference_target_symbols: Iterable[str] = None,
    world_id: str = "",
    *,
    runtime: ABoxRuntime,
) -> Dict[str, object]:
    """Activate a complete historical Manifest and optionally retain its journal."""
    clean_manifest_id = str(manifest_id or "").strip()
    metadata = store.scoped_manifest_metadata(clean_manifest_id, world_id)
    if str(metadata.get("status") or "") != "ok":
        return {
            "configured": bool(getattr(store, "address", "")),
            "status": "error",
            "graphStore": "typedb",
            "aboxSnapshotId": clean_manifest_id,
            "worldviewManifestId": clean_manifest_id,
            "reason": "Scoped ABox Manifest is missing or incomplete.",
        }
    imported = store.driver_imports()
    if imported[0] is None:
        return store.driver_missing_result(imported[1], PortfolioOntology("typedb-scoped-control"))
    previous = dict(previous_metadata or {})
    if not previous:
        try:
            previous = store.active_abox_metadata(world_id)
        except Exception:
            previous = {}
    control_world_id = str(world_id or metadata.get("worldId") or previous.get("worldId") or "").strip()
    control_delta = store.scoped_manifest_control_delta(metadata, previous)
    pointer_graph = store.scoped_manifest_control_graph(
        metadata,
        previous_metadata=previous,
        pending_activation=pending_activation,
        inference_target_symbols=inference_target_symbols,
        scope_ids=control_delta.get("changedScopeIds") or [],
    )
    control_update: Dict[str, object] = {}
    try:
        def operation():
            driver = store.open_driver(imported)
            try:
                store.ensure_database(driver)
                store.ensure_schema(driver, imported)
                control_update.update(store.replace_scoped_abox_control_graph(
                    driver,
                    imported,
                    pointer_graph,
                    world_id=control_world_id,
                    scope_ids=control_delta.get("replacedScopeIds") or [],
                    replace_all_scope_pointers=bool(control_delta.get("replaceAllScopePointers")),
                ))
            finally:
                store.close_driver(driver)

        store.with_typedb_retries(operation)
        active = store.active_abox_metadata(control_world_id)
        if (
            str(active.get("status") or "") != "ok"
            or str(active.get("worldviewManifestId") or active.get("aboxSnapshotId") or "") != clean_manifest_id
        ):
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_manifest_id,
                "worldviewManifestId": clean_manifest_id,
                "reason": "Scoped ABox Manifest pointer verification failed after activation.",
                "activeAbox": active,
            }
        return {
            "configured": True,
            "status": "ok",
            "graphStore": "typedb",
            "aboxSnapshotId": clean_manifest_id,
            "worldviewManifestId": clean_manifest_id,
            "worldId": control_world_id,
            "activeAbox": active,
            "controlUpdate": {
                **control_delta,
                **control_update,
            },
        }
    except Exception as error:  # noqa: BLE001 - preserve the current pointer on an activation failure.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "aboxSnapshotId": clean_manifest_id,
            "worldviewManifestId": clean_manifest_id,
            "worldId": control_world_id,
            "controlUpdate": {
                **control_delta,
                **control_update,
            },
            "reasonCode": error.reason_code if isinstance(error, AtomicControlPatchTooLarge) else runtime.error_code(error),
            "reason": str(error)[:220],
            **({
                "requiredControlQueryCount": error.query_count,
                "atomicControlQueryLimit": error.transaction_limit,
                "preservedPreviousAbox": True,
            } if isinstance(error, AtomicControlPatchTooLarge) else {}),
        }


def prepare_pending_abox_activation_for_inference(
    store: ABoxControlStore,
    world_id: str = "",
) -> Dict[str, object]:
    """Move one fully staged Manifest into the short native-inference phase.

    This is the only place a staged candidate replaces the active pointer.
    The pending journal is retained in the same ABoxControl transaction, so
    API readers and recovery logic can reject an unaligned InferenceBox
    until finalization succeeds.
    """
    try:
        pending = store.pending_abox_activation(world_id)
    except Exception as error:  # noqa: BLE001 - a missing journal must never imply a safe switch.
        return {
            "configured": bool(getattr(store, "address", "")),
            "status": "error",
            "graphStore": "typedb",
            "reason": "Pending ABox activation lookup failed: " + str(error)[:180],
        }
    pending_status = str(pending.get("status") or "")
    if pending_status == "empty":
        return {
            "configured": True,
            "status": "skipped",
            "graphStore": "typedb",
            "reason": "No staged ABox activation exists.",
        }
    if pending_status != "pending":
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "pendingActivation": pending,
            "reason": "Pending ABox activation journal is invalid.",
        }
    candidate_id = str(pending.get("candidateAboxSnapshotId") or "").strip()
    previous_id = str(pending.get("previousAboxSnapshotId") or "").strip()
    target_symbols = clean_symbols_from_payload(pending.get("targetSymbols") or [])
    activation_status = str(pending.get("activationStatus") or "pending-native-inference")
    if not target_symbols:
        return {
            "configured": True,
            "status": "invalid-empty-target",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "pendingActivation": pending,
            "reason": (
                "Pending ABox activation has no target symbols. Native inference is blocked rather than "
                "running an unbounded query or finalizing an unverifiable generation."
            ),
        }
    try:
        active = store.active_abox_metadata(world_id)
    except Exception as error:  # noqa: BLE001 - do not activate when the live pointer cannot be verified.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "reason": "Active ABox lookup failed before activation: " + str(error)[:180],
        }
    active_id = str(active.get("worldviewManifestId") or active.get("aboxSnapshotId") or "").strip()
    if active_id == candidate_id:
        return {
            "configured": True,
            "status": "ready",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "pendingActivation": pending,
            "activeAbox": active,
        }
    if activation_status != "staged-native-inference":
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "pendingActivation": pending,
            "activeAbox": active,
            "reason": "Pending ABox is not in a staged activation phase.",
        }
    if previous_id and active_id != previous_id:
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "activeAbox": active,
            "reason": "Active ABox changed after the candidate was staged.",
        }
    activation = typedb_call_for_world(
        store.activate_scoped_abox_manifest,
        candidate_id,
        previous_metadata=active,
        pending_activation=True,
        inference_target_symbols=target_symbols,
        world_id=world_id,
    )
    if str(activation.get("status") or "") != "ok":
        return {
            **dict(activation or {}),
            "status": "error",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "pendingActivation": pending,
        }
    try:
        active_after = store.active_abox_metadata(world_id)
        pending_after = store.pending_abox_activation(world_id)
    except Exception as error:  # noqa: BLE001 - pointer write without journal verification is unsafe.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "reason": "ABox activation verification failed: " + str(error)[:180],
        }
    active_after_id = str(active_after.get("worldviewManifestId") or active_after.get("aboxSnapshotId") or "").strip()
    if (
        str(active_after.get("status") or "") != "ok"
        or active_after_id != candidate_id
        or str(pending_after.get("status") or "") != "pending"
        or str(pending_after.get("candidateAboxSnapshotId") or "") != candidate_id
        or clean_symbols_from_payload(pending_after.get("targetSymbols") or []) != target_symbols
    ):
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "activeAbox": active_after,
            "pendingActivation": pending_after,
            "reason": "ABox activation pointer or journal verification failed.",
        }
    return {
        "configured": True,
        "status": "activated",
        "graphStore": "typedb",
        "candidateAboxSnapshotId": candidate_id,
        "previousAboxSnapshotId": previous_id,
        "activeAbox": active_after,
        "pendingActivation": pending_after,
    }


def finalize_scoped_abox_manifest(
    store: ABoxControlStore,
    active_manifest_id: str,
    previous_manifest_id: str = "",
    world_id: str = "",
) -> Dict[str, object]:
    """Clear a scoped activation journal only after aligned native inference."""
    active_id = str(active_manifest_id or "").strip()
    previous_id = str(previous_manifest_id or "").strip()
    active = store.active_abox_metadata(world_id)
    if (
        str(active.get("status") or "") != "ok"
        or str(active.get("worldviewManifestId") or active.get("aboxSnapshotId") or "") != active_id
    ):
        return {
            "configured": bool(getattr(store, "address", "")),
            "status": "error",
            "graphStore": "typedb",
            "activeAboxSnapshotId": active_id,
            "previousAboxSnapshotId": previous_id,
            "reason": "Active Worldview Manifest changed before finalization.",
        }
    # The caller has already read an aligned InferenceBox, but a separate
    # projection can finish between that read and this control write.
    # Re-read the durable candidate contract while the scoped writer lease
    # is still held.  Do this from the small active-generation marker, not
    # from a full InferenceBox expansion: the latter is an asynchronous
    # audit concern and made finalization both slow and susceptible to a
    # stale detailed read racing the just-published generation.
    pending = store.pending_abox_activation(world_id)
    if str(pending.get("status") or "") == "pending":
        candidate_id = str(pending.get("candidateAboxSnapshotId") or "").strip()
        target_symbols = clean_symbols_from_payload(pending.get("targetSymbols") or [])
        if candidate_id != active_id:
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "activeAboxSnapshotId": active_id,
                "previousAboxSnapshotId": previous_id,
                "pendingActivation": pending,
                "reason": "Pending ABox candidate changed before finalization.",
            }
        try:
            recovery_metadata = store.inferencebox_recovery_metadata(world_id=world_id)
        except Exception as error:  # noqa: BLE001 - do not clear a recoverable journal on an unreadable proof.
            recovery_metadata = {
                "status": "error",
                "reason": "InferenceBox active-generation marker lookup failed: " + str(error)[:180],
            }
        recovery_metadata = (
            dict(recovery_metadata or {})
            if isinstance(recovery_metadata, dict)
            else {"status": "invalid"}
        )
        native_completed = bool(recovery_metadata.get("nativeTypeDbReasoningCompleted"))
        native_outcome = str(recovery_metadata.get("nativeInferenceOutcome") or "").strip().lower()
        marker_ready = (
            str(recovery_metadata.get("status") or "") == "ok"
            and native_completed
            and native_outcome in {"matched", "no-match"}
        )
        marker_targets = clean_symbols_from_payload(recovery_metadata.get("targetSymbols") or [])
        inferencebox = {
            "configured": True,
            "graphStore": "typedb",
            "status": (
                "ok" if marker_ready and native_outcome == "matched"
                else "empty" if marker_ready and native_outcome == "no-match"
                else "stale-generation"
            ),
            "nativeTypeDbReasoningUsed": bool(marker_ready and native_outcome == "matched"),
            "nativeTypeDbReasoningCompleted": native_completed,
            "typedbNativeRuleEvaluationCompleted": native_completed,
            "nativeInferenceOutcome": native_outcome,
            "generationAligned": bool(
                marker_ready
                and str(recovery_metadata.get("sourceAboxSnapshotId") or "").strip() == candidate_id
                and set(target_symbols).issubset(set(marker_targets))
            ),
            "sourceAboxSnapshotId": str(
                recovery_metadata.get("sourceAboxSnapshotId") or ""
            ).strip(),
            "targetSymbols": marker_targets,
            "inferenceGenerationId": str(
                recovery_metadata.get("inferenceGenerationId") or ""
            ).strip(),
            "querySource": "typedb-active-inference-generation-marker",
            "durableReadback": False,
            "recoveryMetadata": recovery_metadata,
        }
        if not store.inferencebox_matches_pending_abox_activation(
            inferencebox,
            candidate_id,
            target_symbols,
        ):
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "activeAboxSnapshotId": active_id,
                "previousAboxSnapshotId": previous_id,
                "pendingActivation": pending,
                "inferenceBox": inferencebox,
                "reason": "Current TypeDB active InferenceBox generation marker no longer proves the active ABox candidate.",
            }
    control = typedb_call_for_world(
        store.clear_scoped_abox_pending_activation,
        world_id=world_id,
    )
    cleared = str(control.get("status") or "") == "ok"
    # Pointer finalization is on the realtime inference path; deleting a
    # retired immutable generation is deliberately not.  Maintenance
    # acquires the same writer lease during an idle window and performs a
    # bounded, reference-aware prune without delaying a valid judgement.
    cleanup_required = bool(cleared and previous_id and previous_id != active_id)
    cleanup = {
        "status": "deferred" if cleanup_required else "not-required" if cleared else "blocked",
        "previousAboxSnapshotId": previous_id,
        "reason": (
            "Inactive scoped ABox cleanup is deferred to an idle maintenance pass."
            if cleanup_required
            else "No prior scoped Manifest requires cleanup."
            if cleared
            else "Activation journal was not cleared."
        ),
        "legacyPredecessorPending": bool(
            cleanup_required and not previous_id.startswith("abox-manifest:")
        ),
    }
    return {
        "configured": True,
        "status": "ok" if cleared else "error",
        "graphStore": "typedb",
        "activeAboxSnapshotId": active_id,
        "previousAboxSnapshotId": previous_id,
        "clearedPendingActivation": cleared,
        "cleanupDeferred": str(cleanup.get("status") or "") == "deferred",
        "cleanup": cleanup,
        "control": control,
        "reason": "" if cleared else str(control.get("reason") or "Scoped ABox activation journal clear failed."),
    }
