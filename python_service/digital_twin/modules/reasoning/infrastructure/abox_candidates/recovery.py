"""Recover pending activation through bounded markers and control operations."""

from typing import Callable, Dict, Iterable

from digital_twin.modules.reasoning.domain.ontology_worlds import SHARED_PREMISE_WORLD_TYPE, world_type_from_id
from digital_twin.modules.reasoning.infrastructure.abox_persistence.world_calls import typedb_call_for_world
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import clean_symbols_from_payload
from .ports import CandidateRecoveryStore


def inferencebox_matches_pending_abox_activation(inferencebox: Dict[str, object], candidate_snapshot_id: str, target_symbols: Iterable[str]=None) -> bool:
    status = str((inferencebox or {}).get("status") or "").strip().lower()
    native_used = bool((inferencebox or {}).get("nativeTypeDbReasoningUsed"))
    native_completed = bool(
        (inferencebox or {}).get("nativeTypeDbReasoningCompleted")
        or (inferencebox or {}).get("typedbNativeRuleEvaluationCompleted")
    )
    if status == "ok" and not native_used:
        return False
    if status == "empty" and not native_completed:
        return False
    if status not in {"ok", "empty"}:
        return False
    if not bool((inferencebox or {}).get("generationAligned")):
        return False
    if str((inferencebox or {}).get("sourceAboxSnapshotId") or "").strip() != str(candidate_snapshot_id or "").strip():
        return False
    expected = set(clean_symbols_from_payload(target_symbols or []))
    actual = set(clean_symbols_from_payload((inferencebox or {}).get("targetSymbols") or []))
    return not expected or expected.issubset(actual)


def recover_pending_abox_activation(store: CandidateRecoveryStore, world_id: str='', max_staged_target_symbols: int=0, *, error_code: Callable[[object], str]) -> Dict[str, object]:
    """Finish or roll back an interrupted ABox-to-InferenceBox hand-off."""
    try:
        pending = store.pending_abox_activation(world_id)
    except Exception as error:  # noqa: BLE001 - caller must block a new activation when control state is unreadable.
        return {
            "configured": bool(store.address),
            "status": "error",
            "graphStore": "typedb",
            "reasonCode": error_code(error),
            "reason": "ABox activation journal lookup failed: " + str(error)[:180],
        }
    if str(pending.get("status") or "") == "empty":
        return {
            "configured": True,
            "status": "skipped",
            "graphStore": "typedb",
            "reason": "No pending ABox activation exists.",
        }
    if str(pending.get("status") or "") != "pending":
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "pendingActivation": pending,
            "reason": "ABox activation journal is invalid.",
        }
    candidate_id = str(pending.get("candidateAboxSnapshotId") or "").strip()
    previous_id = str(pending.get("previousAboxSnapshotId") or "").strip()
    target_symbols = clean_symbols_from_payload(pending.get("targetSymbols") or [])
    try:
        staged_target_cap = max(0, min(200, int(float(max_staged_target_symbols or 0))))
    except (TypeError, ValueError):
        staged_target_cap = 0
    active = store.active_abox_metadata(world_id)
    active_id = str(active.get("aboxSnapshotId") or "").strip()
    activation_status = str(pending.get("activationStatus") or "pending-native-inference")
    if str(active.get("status") or "") != "ok":
        if activation_status == "staged-native-inference" and not previous_id:
            return {
                "configured": True,
                "status": "staged",
                "graphStore": "typedb",
                "candidateAboxSnapshotId": candidate_id,
                "previousAboxSnapshotId": previous_id,
                "activeAboxSnapshotId": active_id,
                "pendingActivation": pending,
                "reason": "Initial ABox candidate is staged and awaits its first native inference activation.",
            }
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "pendingActivation": pending,
            "activeAbox": active,
            "reason": "Pending ABox activation has no complete active generation.",
        }
    if active_id != candidate_id:
        if activation_status == "staged-native-inference":
            if (
                world_type_from_id(world_id) == SHARED_PREMISE_WORLD_TYPE
                and previous_id
                and active_id == previous_id
            ):
                # SharedPremiseWorld is rebuilt from the current bounded
                # source request before its RuleBox runs. A candidate that
                # never replaced the verified predecessor therefore has no
                # observable generation to resume. Clear only its journal
                # so the current request can stage a fresh targeted view.
                control = typedb_call_for_world(
                    store.activate_abox_generation,
                    active_id,
                    world_id=world_id,
                )
                cleared = str(control.get("status") or "") == "ok"
                return {
                    "configured": True,
                    "status": "discarded-staged-shared-premise" if cleared else "error",
                    "graphStore": "typedb",
                    "candidateAboxSnapshotId": candidate_id,
                    "previousAboxSnapshotId": previous_id,
                    "activeAboxSnapshotId": active_id,
                    "targetSymbols": target_symbols,
                    "control": control,
                    "recoveryMode": "discard-inactive-shared-premise-candidate",
                    "reason": (
                        "An inactive SharedPremiseWorld candidate was discarded so the current bounded request can stage a fresh generation."
                        if cleared
                        else str(control.get("reason") or "SharedPremiseWorld control restoration failed.")
                    ),
                }
            if (
                staged_target_cap
                and len(target_symbols) > staged_target_cap
                and previous_id
                and active_id == previous_id
            ):
                # The candidate never became active.  Retain the verified
                # predecessor and clear only this journal while the
                # projection coordinator is held, so a later scheduler
                # cannot resume an interrupted wider batch.
                control = typedb_call_for_world(
                    store.activate_abox_generation,
                    active_id,
                    world_id=world_id,
                )
                return {
                    "configured": True,
                    "status": "discarded-staged-batch" if str(control.get("status") or "") == "ok" else "error",
                    "graphStore": "typedb",
                    "candidateAboxSnapshotId": candidate_id,
                    "previousAboxSnapshotId": previous_id,
                    "activeAboxSnapshotId": active_id,
                    "targetSymbols": target_symbols,
                    "maxStagedTargetSymbols": staged_target_cap,
                    "control": control,
                    "reason": (
                        "An interrupted staged ABox batch exceeded the current scheduler target cap and was discarded before native inference."
                        if str(control.get("status") or "") == "ok"
                        else str(control.get("reason") or "ABox control restoration failed.")
                    ),
                }
            return {
                "configured": True,
                "status": "staged",
                "graphStore": "typedb",
                "candidateAboxSnapshotId": candidate_id,
                "previousAboxSnapshotId": previous_id,
                "activeAboxSnapshotId": active_id,
                "pendingActivation": pending,
                "reason": "A complete ABox candidate is staged and awaits native inference activation.",
            }
        # The pointer already moved by a successful rollback or a later
        # repair. Rewriting that verified pointer clears a stale journal.
        control = typedb_call_for_world(
            store.activate_abox_generation,
            active_id,
            world_id=world_id,
        )
        return {
            "configured": True,
            "status": "cleared-stale" if str(control.get("status") or "") == "ok" else "error",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "activeAboxSnapshotId": active_id,
            "previousAboxSnapshotId": previous_id,
            "control": control,
            "reason": "" if str(control.get("status") or "") == "ok" else str(control.get("reason") or "ABox control clear failed."),
        }
    if not target_symbols and not previous_id:
        # A journal with no target subject cannot ever prove that a
        # native InferenceBox covered a requested investment decision.
        # Keeping it pending instead blocks every successor Manifest and
        # used to tempt recovery into an unbounded historical read. The
        # candidate is already the verified active Manifest, so replacing
        # its control pointer without a pending journal is safe: no ABox
        # fact, scope generation, or prior decision is removed.
        finalization = typedb_call_for_world(
            store.finalize_abox_generation,
            candidate_id,
            previous_id,
            world_id=world_id,
        )
        finalized = str(finalization.get("status") or "") == "ok"
        return {
            "configured": True,
            "status": "finalized-empty-target" if finalized else "error",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "pendingActivation": pending,
            "finalization": finalization,
            "reason": (
                "Cleared an active ABox activation journal with no requested target symbols; the next bounded cycle may stage a successor Manifest."
                if finalized
                else str(finalization.get("reason") or "ABox activation journal clear failed.")
            ),
        }
    if not target_symbols:
        return {
            "configured": True,
            "status": "invalid-empty-target",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "pendingActivation": pending,
            "reason": (
                "Pending ABox activation lost its target symbols after a predecessor existed. "
                "Recovery is blocked to avoid an unbounded InferenceBox read or an unsafe rollback."
            ),
        }
    if (
        staged_target_cap
        and len(target_symbols) > staged_target_cap
        and previous_id
        and active_id == candidate_id
    ):
        # A hard-isolation timeout records a smaller next scheduler cap.
        # The candidate may already be the active ABox pointer even
        # though its native InferenceBox never verified.  Retrying its
        # original wide target set here would defeat that protection and
        # can hold every newer mailbox revision behind the same timeout.
        #
        # The pending journal proves that this candidate has no aligned
        # inference result yet, while ``previous_id`` is the last
        # verified generation retained specifically for recovery.  A
        # control-only rollback is therefore safe: it preserves the
        # verified judgement and lets the current bounded scheduler
        # stage fresh facts instead of resuming an obsolete wide batch.
        rollback = typedb_call_for_world(
            store.activate_abox_generation,
            previous_id,
            world_id=world_id,
        )
        restored = str(rollback.get("status") or "") == "ok"
        return {
            "configured": True,
            "status": "restored" if restored else "error",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "activeAboxSnapshotId": active_id,
            "targetSymbols": target_symbols,
            "maxStagedTargetSymbols": staged_target_cap,
            "pendingActivation": pending,
            "rollback": rollback,
            "recoveryMode": "rollback-oversized-active-candidate",
            "reason": (
                "An interrupted active ABox batch exceeded the current scheduler target cap and was rolled back before retrying native inference."
                if restored
                else str(rollback.get("reason") or "ABox control rollback failed.")
            ),
        }
    try:
        recovery_metadata = typedb_call_for_world(
            store.inferencebox_recovery_metadata,
            world_id=world_id,
        )
    except Exception as error:  # noqa: BLE001 - retain the candidate journal when the marker cannot be read.
        recovery_metadata = {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "reason": "InferenceBox recovery marker lookup failed: " + str(error)[:180],
        }
    recovery_metadata = dict(recovery_metadata or {}) if isinstance(recovery_metadata, dict) else {}
    recovery_outcome = str(recovery_metadata.get("nativeInferenceOutcome") or "").strip().lower()
    recovery_completed = bool(recovery_metadata.get("nativeTypeDbReasoningCompleted"))
    recovery_source_id = str(recovery_metadata.get("sourceAboxSnapshotId") or "").strip()
    recovery_targets = clean_symbols_from_payload(recovery_metadata.get("targetSymbols") or [])
    recovery_marker_ready = (
        str(recovery_metadata.get("status") or "") == "ok"
        and recovery_completed
        and recovery_outcome in {"matched", "no-match"}
    )
    # A recovery must not expand every entity, relation, and trace from a
    # historical InferenceBox.  The active generation marker is the same
    # provenance proof used by the realtime commit path: it contains the
    # source ABox, native-completion state, outcome, and target coverage.
    # Detailed rows remain an asynchronous audit concern.
    inferencebox = {
        "configured": True,
        "graphStore": "typedb",
        "status": (
            "ok" if recovery_marker_ready and recovery_outcome == "matched"
            else "empty" if recovery_marker_ready and recovery_outcome == "no-match"
            else "stale-generation"
        ),
        "nativeTypeDbReasoningUsed": bool(
            recovery_marker_ready and recovery_outcome == "matched"
        ),
        "nativeTypeDbReasoningCompleted": recovery_completed,
        "typedbNativeRuleEvaluationCompleted": recovery_completed,
        "nativeInferenceOutcome": recovery_outcome,
        "generationAligned": bool(
            recovery_marker_ready
            and recovery_source_id == candidate_id
            and set(target_symbols).issubset(set(recovery_targets))
        ),
        "sourceAboxSnapshotId": recovery_source_id,
        "targetSymbols": recovery_targets,
        "inferenceGenerationId": str(recovery_metadata.get("inferenceGenerationId") or "").strip(),
        "querySource": str(
            recovery_metadata.get("querySource")
            or "typedb-active-inference-generation-marker"
        ),
        "durableReadback": False,
        "recoveryMetadata": recovery_metadata,
    }
    if str(recovery_metadata.get("status") or "") == "error" and active_id != candidate_id:
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "reason": str(recovery_metadata.get("reason") or "InferenceBox recovery marker is unreadable.")[:220],
        }
    if store.inferencebox_matches_pending_abox_activation(
        inferencebox,
        candidate_id,
        target_symbols,
    ):
        finalization = typedb_call_for_world(
            store.finalize_abox_generation,
            candidate_id,
            previous_id,
            world_id=world_id,
        )
        return {
            "configured": True,
            "status": "finalized" if str(finalization.get("status") or "") == "ok" else "error",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "inferenceBox": inferencebox,
            "finalization": finalization,
            "reason": "" if str(finalization.get("status") or "") == "ok" else str(finalization.get("reason") or "ABox finalization failed."),
        }
    if (
        active_id == candidate_id
        and previous_id
        and world_type_from_id(world_id) == SHARED_PREMISE_WORLD_TYPE
    ):
        # SharedPremiseWorld is rebuilt from the current bounded source
        # request. After a process interruption, retaining an active
        # candidate with no aligned InferenceBox only makes every newer
        # request read a stale generation. Restore the last proven pair;
        # the current request will stage fresh premises and run them under
        # the atomic staged-ABox execution boundary.
        rollback = typedb_call_for_world(
            store.activate_abox_generation,
            previous_id,
            world_id=world_id,
        )
        restored = str(rollback.get("status") or "") == "ok"
        return {
            "configured": True,
            "status": "restored" if restored else "error",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "activeAboxSnapshotId": active_id,
            "targetSymbols": target_symbols,
            "pendingActivation": pending,
            "inferenceBox": inferencebox,
            "rollback": rollback,
            "recoveryMode": "rollback-interrupted-shared-premise-candidate",
            "reason": (
                "Interrupted SharedPremiseWorld candidate was restored to the last aligned generation."
                if restored
                else str(rollback.get("reason") or "SharedPremiseWorld rollback failed.")
            ),
        }
    if active_id == candidate_id:
        # The durable pointer already proves this candidate is a complete
        # ABox generation.  A missing or stale InferenceBox must not make
        # us scan and restore an older Manifest: that turns an interrupted
        # one-target cycle into a historical ABox read and can repeatedly
        # exceed the isolated worker timeout.  Keep the activation journal
        # in place (so no judgement can use this ABox yet) and resume the
        # exact bounded native inference on the active candidate.
        return {
            "configured": True,
            "status": "retry-required",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "activeAboxSnapshotId": active_id,
            "targetSymbols": target_symbols,
            "pendingActivation": pending,
            "inferenceBox": inferencebox,
            "recoveryMode": "resume-active-candidate",
            "reason": "The complete active ABox candidate is awaiting a retry of bounded TypeDB native inference.",
        }
    if not previous_id:
        # There is no prior verified generation to restore. The candidate
        # remains staged and cannot produce investment judgement until a
        # later same-material cycle retries native inference.
        return {
            "configured": True,
            "status": "retry-required",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "targetSymbols": target_symbols,
            "pendingActivation": pending,
            "inferenceBox": inferencebox,
            "reason": "Initial ABox activation is awaiting a retry of TypeDB native inference.",
        }
    rollback = typedb_call_for_world(
        store.activate_abox_generation,
        previous_id,
        world_id=world_id,
    )
    return {
        "configured": True,
        "status": "restored" if str(rollback.get("status") or "") == "ok" else "error",
        "graphStore": "typedb",
        "candidateAboxSnapshotId": candidate_id,
        "previousAboxSnapshotId": previous_id,
        "inferenceBox": inferencebox,
        "rollback": rollback,
        "reason": "" if str(rollback.get("status") or "") == "ok" else str(rollback.get("reason") or "ABox rollback failed."),
    }
