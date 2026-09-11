"""native_execution: staged through explicit injected capabilities."""

from digital_twin.modules.reasoning.infrastructure.backend_constants import (
    TYPEDB_NATIVE_BLOCKED_MODE,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
)
from typing import Dict
from .staged_ports import NativeExecutionStagedStore, NativeExecutionStagedRuntime


def run_rulebox_for_staged_abox(
    _store: NativeExecutionStagedStore,
    payload: Dict[str, object] = None,
    *,
    _bindings: NativeExecutionStagedRuntime
) -> Dict[str, object]:
    """Activate, infer, and finalize one staged ABox under one writer lease.

    SharedPremiseWorld used to publish its candidate Manifest before native
    inference. A timeout then left the new ABox paired with the predecessor
    InferenceBox, making both the current attempt and every retry stale.
    This boundary keeps the candidate journal durable, switches the pointer
    only while the world writer lease is held, and restores the last
    verified predecessor whenever native completion cannot be proven.
    """
    if not _store.address:
        return _bindings.NullTypeDBOntologyGraphRepository().run_rulebox(payload)
    values = dict(payload or {})
    world_id = str(values.get("worldId") or values.get("ontologyWorldId") or "").strip()
    expected_abox_snapshot_id = str(values.pop("expectedAboxSnapshotId", "") or "").strip()
    target_symbols = clean_symbols_from_payload(
        values.get("symbols") or values.get("targetSymbols") or values.get("changedSymbols")
    )
    lease = _store.acquire_scoped_abox_write_lease(
        "staged-abox-native-rule",
        world_id=world_id,
    )
    if not lease.get("acquired"):
        return {
            "configured": True,
            "status": "deferred-inference-write-lease",
            "graphStore": "typedb",
            "source": "typedbNativeRule",
            "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
            "nativeTypeDbReasoningUsed": False,
            "preservedPreviousInference": True,
            "retryable": True,
            "recommendedRetryAfterSeconds": int(lease.get("recommendedRetryAfterSeconds") or 10),
            "reason": (
                "Another ABox activation or native InferenceBox generation "
                "is running for this world."
            ),
            "inferenceWriteLease": _bindings.typedb_projection_coordinator_summary(lease),
        }

    result: Dict[str, object] = {}
    candidate_id = ""
    previous_id = ""
    try:
        try:
            preparation = _store.prepare_pending_abox_activation_for_inference(world_id)
        except Exception as error:  # noqa: BLE001 - never infer against an uncertain pointer.
            preparation = {
                "status": "error",
                "reason": "ABox activation preparation failed: " + str(error)[:180],
            }
        result["aboxActivationPreparation"] = preparation
        preparation_status = str(preparation.get("status") or "")
        candidate_id = str(preparation.get("candidateAboxSnapshotId") or "").strip()
        previous_id = str(preparation.get("previousAboxSnapshotId") or "").strip()
        if preparation_status not in {"skipped", "ready", "activated"}:
            result.update(
                {
                    "configured": True,
                    "status": "blocked-pending-abox-activation",
                    "graphStore": "typedb",
                    "source": "typedbNativeRule",
                    "nativeTypeDbReasoningUsed": False,
                    "retryable": True,
                    "recommendedRetryAfterSeconds": 10,
                    "reason": str(
                        preparation.get("reason")
                        or "ABox candidate could not be prepared for native inference."
                    )[:220],
                }
            )
            return result

        active = dict(_store.active_abox_metadata(world_id) or {})
        active_id = str(
            active.get("worldviewManifestId") or active.get("aboxSnapshotId") or ""
        ).strip()
        if not candidate_id:
            candidate_id = active_id
        if expected_abox_snapshot_id and active_id != expected_abox_snapshot_id:
            result.update(
                {
                    "configured": True,
                    "status": "stale-staged-abox-candidate",
                    "graphStore": "typedb",
                    "source": "typedbNativeRule",
                    "nativeTypeDbReasoningUsed": False,
                    "retryable": True,
                    "recommendedRetryAfterSeconds": 10,
                    "expectedAboxSnapshotId": expected_abox_snapshot_id,
                    "activeAboxSnapshotId": active_id,
                    "candidateAboxSnapshotId": candidate_id,
                    "preservedPreviousInference": True,
                    "reason": (
                        "The staged ABox candidate changed before native inference "
                        "could claim its writer lease."
                    ),
                }
            )
            return result
        if not active_id:
            result.update(
                {
                    "configured": True,
                    "status": "invalid-abox-generation",
                    "graphStore": "typedb",
                    "source": "typedbNativeRule",
                    "nativeTypeDbReasoningUsed": False,
                    "retryable": True,
                    "reason": "No complete active ABox generation is available.",
                }
            )
            return result

        values["worldId"] = world_id
        values["_nativeInferenceWriteLeaseHeld"] = True
        try:
            execution = _store._run_rulebox_unlocked(values)
        except Exception as error:  # noqa: BLE001 - rollback below preserves the predecessor.
            execution = {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "source": "typedbNativeRule",
                "nativeTypeDbReasoningUsed": False,
                "nativeTypeDbReasoningCompleted": False,
                "reason": str(error)[:220],
            }
        result.update(dict(execution or {}))
        result["aboxActivationPreparation"] = preparation
        inferencebox = (
            dict(result.get("inferenceBox") or {})
            if isinstance(result.get("inferenceBox"), dict)
            else {}
        )
        aligned = bool(
            str(result.get("status") or "") == "ok"
            and _store.inferencebox_matches_pending_abox_activation(
                inferencebox,
                active_id,
                target_symbols,
            )
        )
        result["stagedAboxInferenceAlignment"] = {
            "verified": aligned,
            "candidateAboxSnapshotId": candidate_id,
            "activeAboxSnapshotId": active_id,
            "sourceAboxSnapshotId": str(inferencebox.get("sourceAboxSnapshotId") or ""),
            "targetSymbols": target_symbols,
        }
        pending_was_activated = preparation_status in {"ready", "activated"}
        if aligned and pending_was_activated:
            finalization = _store.finalize_abox_generation(
                active_id,
                previous_id,
                world_id,
            )
            result["aboxActivationFinalization"] = finalization
            if str(finalization.get("status") or "") != "ok":
                result.update(
                    {
                        "status": "inference-finalization-pending",
                        "retryable": True,
                        "recommendedRetryAfterSeconds": 10,
                        "preservedActiveGeneration": True,
                        "reason": str(
                            finalization.get("reason")
                            or "Aligned native inference could not clear its ABox activation journal."
                        )[:220],
                    }
                )
            return result
        if aligned:
            return result

        rollback = {
            "status": "not-available",
            "reason": "No verified predecessor ABox generation is available.",
        }
        if previous_id:
            rollback = _store.activate_abox_generation(
                previous_id,
                world_id,
            )
        result["activationRollback"] = rollback
        restored = str(rollback.get("status") or "") == "ok"
        result["preservedActiveGeneration"] = restored
        result["retryable"] = True
        result.setdefault("recommendedRetryAfterSeconds", 10)
        if restored:
            result["status"] = "inference-failed-rolled-back"
            result["reason"] = (
                str(result.get("reason") or "Native inference did not complete.")[:180]
                + " The previous aligned SharedPremise generation was restored."
            )
        return result
    finally:
        release = _store.release_scoped_abox_write_lease(lease)
        result["inferenceWriteLease"] = {
            key: value for key, value in dict(lease or {}).items() if key != "propertiesJson"
        }
        result["inferenceWriteLeaseRelease"] = release
