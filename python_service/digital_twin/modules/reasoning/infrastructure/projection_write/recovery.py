"""Recovery implementation; facade-independent dependencies."""

from __future__ import annotations
import inspect
from .recovery_ports import RecoveryPort
from digital_twin.domain.ontology_projection_audit import (
    complete_ontology_projection_run,
    projection_run_from_payload,
)
from digital_twin.domain.portfolio import AccountSnapshot
from typing import Dict


def repository_world_call(
    _store: RecoveryPort, method_name: str, *args, world_id: str = "", **kwargs
):
    """Call a world-aware adapter while retaining narrow test adapters.

    Older in-memory fakes deliberately implement only the original
    repository contract.  Production TypeDB receives an explicit world
    boundary; a fake without that optional keyword remains usable for
    projection unit tests.
    """
    method = getattr(_store.repository, method_name, None)
    if not callable(method):
        raise AttributeError(method_name + " is unavailable")
    call_kwargs = dict(kwargs)
    if world_id:
        call_kwargs["world_id"] = world_id
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        parameters = None
    if parameters is not None and not any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        for optional in ("world_id", "max_staged_target_symbols"):
            if optional not in parameters:
                call_kwargs.pop(optional, None)
    # Never retry a mutation after a TypeError raised inside the adapter.
    return method(*args, **call_kwargs)


def active_abox_metadata(_store: RecoveryPort, world_id: str = "") -> Dict[str, object]:
    if not hasattr(_store.repository, "active_abox_metadata"):
        return {}
    try:
        result = _store.repository_world_call("active_abox_metadata", world_id=world_id)
    except (
        Exception
    ):  # noqa: BLE001 - absence of comparison metadata falls back to persistence.
        return {}
    return dict(result or {}) if isinstance(result, dict) else {}


def recover_pending_abox_activation(
    _store: RecoveryPort, world_id: str = "", max_staged_target_symbols: int = 0
) -> Dict[str, object]:
    if _store.active_graph_store_key() != "typedb":
        return {"status": "skipped", "reason": "Active graph store is not TypeDB."}
    recovery = getattr(_store.repository, "recover_pending_abox_activation", None)
    if not callable(recovery):
        return {
            "status": "skipped",
            "reason": "Graph store has no pending ABox activation journal.",
        }
    # Recovery mutates the activation journal and therefore takes the
    # database-wide TypeDB writer lease. Most realtime cycles have no
    # pending candidate at all; perform the cheap read first so an empty
    # journal does not spend the entire alert budget contending for a
    # write lease.
    pending_reader = getattr(_store.repository, "pending_abox_activation", None)
    if callable(pending_reader):
        try:
            pending = _store.repository_world_call(
                "pending_abox_activation",
                world_id=world_id,
            )
        except Exception:
            pending = None
        if (
            isinstance(pending, dict)
            and str(pending.get("status") or "").strip() == "empty"
        ):
            return {
                "status": "skipped",
                "reason": "No pending ABox activation exists.",
                "recoveryPreflight": "empty-journal",
            }
    try:
        staged_cap = max(0, min(200, int(float(max_staged_target_symbols or 0))))
    except (TypeError, ValueError):
        staged_cap = 0
    try:
        result = _store.repository_world_call(
            "recover_pending_abox_activation",
            world_id=world_id,
            max_staged_target_symbols=staged_cap,
        )
    except (
        Exception
    ) as error:  # noqa: BLE001 - do not replace a potentially recoverable active generation.
        return {"status": "error", "reason": str(error)[:180]}
    return (
        dict(result or {})
        if isinstance(result, dict)
        else {
            "status": "error",
            "reason": "Graph store returned an invalid ABox activation recovery result.",
        }
    )


def resume_staged_pending_abox_activation(
    _store: RecoveryPort,
    snapshot: AccountSnapshot,
    world_id: str,
    recovery: Dict[str, object],
) -> Dict[str, object]:
    """Complete a pending candidate before allowing a newer Manifest.

    A process can stop either after staging an immutable ABox candidate or
    after activating the initial candidate but before native inference
    completes. Rebuilding the latest snapshot cannot replace that
    candidate safely, so resume its exact target set first.
    """
    recovery = dict(recovery or {})
    pending = recovery.get("pendingActivation")
    pending = dict(pending or {}) if isinstance(pending, dict) else {}
    candidate_id = str(
        pending.get("candidateAboxSnapshotId")
        or recovery.get("candidateAboxSnapshotId")
        or ""
    ).strip()
    previous_id = str(
        pending.get("previousAboxSnapshotId")
        or recovery.get("previousAboxSnapshotId")
        or ""
    ).strip()
    target_symbols = [
        str(symbol or "").upper().strip()
        for symbol in (
            pending.get("targetSymbols") or recovery.get("targetSymbols") or []
        )
        if str(symbol or "").strip()
    ]
    target_symbols = list(dict.fromkeys(target_symbols))
    if not candidate_id or not target_symbols:
        return {
            "saved": False,
            "status": "blocked-pending-abox-activation",
            "graphStore": _store.active_graph_store_key(),
            "worldId": str(world_id or ""),
            "pendingAboxActivation": pending,
            "preservedActiveGeneration": True,
            "retryable": True,
            "recommendedRetryAfterSeconds": 10,
            "reason": "스테이징된 TypeDB ABox 후보의 대상 종목을 확인하지 못해 안전하게 재개하지 않았습니다.",
        }

    result = {
        "saved": False,
        "status": "resuming-pending-abox-activation",
        "graphStore": _store.active_graph_store_key(),
        "worldId": str(world_id or ""),
        "aboxSnapshotId": candidate_id,
        "worldviewManifestId": candidate_id,
        "pendingAboxActivation": {
            **pending,
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "targetSymbols": target_symbols,
        },
        "preservedActiveGeneration": True,
    }
    _store.attach_graph_store_inference_result(
        result,
        snapshot,
        target_symbols=target_symbols,
        world_id=world_id,
    )
    finalization = result.get("aboxActivationFinalization")
    finalization = dict(finalization or {}) if isinstance(finalization, dict) else {}
    if str(finalization.get("status") or "") == "ok":
        result.update(
            {
                "saved": True,
                "status": "ok",
                "resumedPendingAboxActivation": True,
                "reason": "중단된 TypeDB ABox 후보의 네이티브 추론과 완료 처리를 재개했습니다.",
            }
        )
    elif str(result.get("status") or "") == "resuming-pending-abox-activation":
        result.update(
            {
                "saved": False,
                "status": "blocked-pending-abox-activation",
                "retryable": True,
                "recommendedRetryAfterSeconds": 10,
                "reason": str(
                    finalization.get("reason")
                    or "스테이징된 TypeDB ABox 후보의 네이티브 추론 완료를 다시 확인해야 합니다."
                )[:220],
            }
        )
    return result


def reconcile_interrupted_projection_audit(
    _store: RecoveryPort, world_id: str = ""
) -> Dict[str, object]:
    """Finish one audit row only when TypeDB already proves activation.

    The source row is written before an ABox pointer moves.  A process can
    stop after TypeDB has activated an aligned InferenceBox but before the
    final MySQL audit update.  This recovery is deliberately proof-based:
    it never promotes a row from a timer or a partial graph write.
    """
    if _store.active_graph_store_key() != "typedb":
        return {"status": "skipped", "reason": "Active graph store is not TypeDB."}
    if not _store.projection_run_store or not hasattr(
        _store.projection_run_store, "latest"
    ):
        return {
            "status": "skipped",
            "reason": "Projection audit store does not support recovery lookup.",
        }
    if not hasattr(_store.projection_run_store, "complete") or not hasattr(
        _store.repository, "inferencebox_snapshot"
    ):
        return {
            "status": "skipped",
            "reason": "Projection audit recovery dependencies are unavailable.",
        }
    active_abox = _store.active_abox_metadata(world_id)
    if str(active_abox.get("status") or "") != "ok":
        return {
            "status": "skipped",
            "reason": "No complete active ABox is available for audit recovery.",
        }
    run_id = str(active_abox.get("projectionRunId") or "").strip()
    active_snapshot_id = str(active_abox.get("aboxSnapshotId") or "").strip()
    if not run_id or not active_snapshot_id:
        return {
            "status": "skipped",
            "reason": "Active ABox has no recoverable projection audit identity.",
        }
    try:
        try:
            rows = _store.projection_run_store.latest(limit=80, world_id=world_id)
        except TypeError as error:
            if "unexpected keyword" not in str(error) and "world_id" not in str(error):
                raise
            rows = _store.projection_run_store.latest(limit=80)
    except (
        Exception
    ) as error:  # noqa: BLE001 - audit recovery must not block a new graph cycle.
        return {"status": "error", "reason": str(error)[:180]}
    row = next(
        (
            item
            for item in rows or []
            if isinstance(item, dict)
            and str(item.get("runId") or "") == run_id
            and str(item.get("status") or "").lower() == "projecting"
            and (
                not str(world_id or "").strip()
                or str(item.get("worldId") or "").strip() == str(world_id or "").strip()
            )
        ),
        None,
    )
    recovery_mode = "interrupted-audit"
    if not row:
        # Older workers could prove a TypeDB activation after their
        # process was interrupted, but persisted only a thin recovery
        # audit. That loses the reusable native-rule coverage proof and
        # makes every later target run fall back to the full catalogue.
        # Repair only the active run, and only from the same aligned
        # TypeDB InferenceBox proof used for an interrupted audit.
        row = next(
            (
                item
                for item in rows or []
                if isinstance(item, dict)
                and str(item.get("runId") or "") == run_id
                and str(item.get("status") or "").lower() == "ok"
                and (
                    not str(world_id or "").strip()
                    or str(item.get("worldId") or "").strip()
                    == str(world_id or "").strip()
                )
                and str(
                    ((item.get("result") or {}).get("inferenceReuseProof") or {}).get(
                        "status"
                    )
                    if isinstance(item.get("result"), dict)
                    else ""
                )
                != "verified"
                and not bool(
                    (
                        (item.get("result") or {}).get("nativeReplayValidation") or {}
                    ).get("verified")
                    if isinstance(item.get("result"), dict)
                    else False
                )
            ),
            None,
        )
        recovery_mode = "reuse-proof-repair"
    if not row:
        return {
            "status": "skipped",
            "reason": "No interrupted audit or missing active reuse proof matches the active ABox.",
        }
    run = projection_run_from_payload(row)
    if not run.run_id or run.abox_snapshot_id != active_snapshot_id:
        return {
            "status": "skipped",
            "reason": "Active ABox identity does not match the recoverable projection audit.",
        }
    try:
        inferencebox = _store.repository_world_call(
            "inferencebox_snapshot",
            symbols=list(run.source_symbols or []),
            limit=_store.inference_snapshot_limit(),
            world_id=world_id,
        )
    except (
        Exception
    ) as error:  # noqa: BLE001 - preserve the durable projecting row for the next retry.
        return {"status": "error", "reason": str(error)[:180]}
    if not _store.inference_result_is_reusable(
        inferencebox, active_abox, list(run.source_symbols or [])
    ):
        return {
            "status": "skipped",
            "reason": "Active InferenceBox is not aligned with the interrupted ABox audit row.",
            "runId": run.run_id,
        }
    prior_result = row.get("result") if isinstance(row.get("result"), dict) else {}
    prior_execution = (
        prior_result.get("ruleboxExecution")
        if isinstance(prior_result.get("ruleboxExecution"), dict)
        else {}
    )
    prior_reuse = (
        prior_result.get("priorInferenceReuse")
        if isinstance(prior_result.get("priorInferenceReuse"), dict)
        else {}
    )
    matched_rule_ids = _store.matched_rule_ids_from_inference_payload(inferencebox)
    selection_applied = bool(
        inferencebox.get("nativeRuleSelectionApplied")
        if "nativeRuleSelectionApplied" in inferencebox
        else prior_execution.get("nativeRuleSelectionApplied")
    )
    recovered_timing = (
        dict(inferencebox.get("typedbNativeRuleTimingProfile") or {})
        if isinstance(inferencebox.get("typedbNativeRuleTimingProfile"), dict)
        else {}
    )
    recovered_stage_timings = (
        dict(inferencebox.get("typedbNativeStageTimings") or {})
        if isinstance(inferencebox.get("typedbNativeStageTimings"), dict)
        else {}
    )
    result = {
        "saved": True,
        "status": "ok",
        "reason": (
            "TypeDB ABox와 InferenceBox 정합성을 확인해 중단된 투영 감사를 복구했습니다."
            if recovery_mode == "interrupted-audit"
            else "활성 TypeDB ABox와 InferenceBox에서 누락된 규칙 재사용 증명을 복구했습니다."
        ),
        "graphStore": "typedb",
        "projectionMode": run.projection_mode,
        "aboxSnapshotId": active_snapshot_id,
        "materialFingerprint": str(
            active_abox.get("materialFingerprint") or run.material_fingerprint
        ),
        "entityCount": run.entity_count,
        "relationCount": run.relation_count,
        "inferenceBox": inferencebox,
        "ruleboxExecution": {
            "status": "ok",
            "reason": "Recovered from active TypeDB InferenceBox alignment.",
            "nativeInferenceEvaluationComplete": bool(
                inferencebox.get("nativeTypeDbReasoningCompleted")
                or inferencebox.get("typedbNativeRuleEvaluationCompleted")
                or inferencebox.get("nativeTypeDbReasoningUsed")
            ),
            "nativeRuleSelectionApplied": selection_applied,
            "nativeRuleSelectionCandidateCount": int(
                inferencebox.get("nativeRuleSelectionCandidateCount") or 0
            ),
            "nativeRuleSelectionExecutedCount": int(
                inferencebox.get("nativeRuleSelectionExecutedCount") or 0
            ),
            "nativeRuleSelectionDeferredCount": int(
                inferencebox.get("nativeRuleSelectionDeferredCount") or 0
            ),
            "nativeRuleSelectionFullRuleCount": int(
                inferencebox.get("nativeRuleSelectionFullRuleCount") or 0
            ),
            "nativeRuleSelectionExecutedRuleIds": list(
                inferencebox.get("nativeRuleSelectionExecutedRuleIds") or []
            )[:80],
            "nativeRuleSelectionDeferredRuleIds": list(
                inferencebox.get("nativeRuleSelectionDeferredRuleIds") or []
            )[:80],
            "typedbNativeRuleExecutedCount": int(
                inferencebox.get("typedbNativeRuleExecutedCount") or 0
            ),
            "typedbNativeRuleMatchedRuleIds": list(
                inferencebox.get("typedbNativeRuleMatchedRuleIds") or matched_rule_ids
            )[:160],
            "typedbNativeRuleMatchedCount": max(
                int(inferencebox.get("typedbNativeRuleMatchedCount") or 0),
                len(matched_rule_ids),
                int(inferencebox.get("traceCount") or 0),
            ),
            "typedbNativeRuleTimingProfile": recovered_timing,
            "typedbNativeStageTimings": recovered_stage_timings,
        },
        "aboxPersistenceVerification": {
            "activePointer": {
                "status": str(active_abox.get("status") or ""),
                "aboxSnapshotId": active_snapshot_id,
                "projectionRunId": run.run_id,
            },
            "activation": {
                "status": "recovered-after-runtime-interruption",
                "snapshotId": active_snapshot_id,
                "atomic": True,
            },
        },
        "recoveredAfterRuntimeInterruption": True,
        "recoveryMode": recovery_mode,
    }
    if prior_reuse:
        result["priorInferenceReuse"] = prior_reuse
    # A recovery must preserve the same verified coverage contract as an
    # uninterrupted projection. Without this, the next narrow change
    # cannot reuse unaffected TypeDB matches and re-runs the full catalog.
    _store.attach_inference_reuse_proof(run, result)
    replay_validation = dict(result.get("nativeReplayValidation") or {})
    if not bool(replay_validation.get("verified")):
        result.update(
            {
                "saved": False,
                "status": "incomplete-native-coverage",
                "reason": str(
                    replay_validation.get("reason")
                    or "Recovered TypeDB generation has no complete native execution ledger."
                )[:300],
                "preservedActiveGeneration": True,
            }
        )
    try:
        completed = complete_ontology_projection_run(run, result)
        complete_with_trace = getattr(
            _store.projection_run_store,
            "complete_with_execution_trace",
            None,
        )
        if callable(complete_with_trace):
            complete_with_trace(completed, result)
        else:
            _store.projection_run_store.complete(completed)
    except (
        Exception
    ) as error:  # noqa: BLE001 - a later cycle can prove and retry the same row.
        return {"status": "error", "reason": str(error)[:180], "runId": run.run_id}
    if not bool(replay_validation.get("verified")):
        return {
            "status": "incomplete-native-coverage",
            "runId": run.run_id,
            "aboxSnapshotId": active_snapshot_id,
            "inferenceGenerationId": str(
                inferencebox.get("inferenceGenerationId") or ""
            ),
            "nativeReplayValidation": replay_validation,
            "reason": str(result.get("reason") or "")[:300],
        }
    return {
        "status": (
            "recovered"
            if recovery_mode == "interrupted-audit"
            else "reuse-proof-repaired"
        ),
        "runId": run.run_id,
        "aboxSnapshotId": active_snapshot_id,
        "inferenceGenerationId": str(inferencebox.get("inferenceGenerationId") or ""),
        "inferenceReuseProof": dict(result.get("inferenceReuseProof") or {}),
    }


def active_projection_audit_run(_store: RecoveryPort, world_id: str = ""):
    """Return the audit owner of the active ABox after staged recovery.

    A restart can finish TypeDB activation before the original MySQL audit
    and result slots are committed.  Reusing that original run lets the
    recovery turn persist the proof immediately; otherwise the following
    live request has to expand the complete durable InferenceBox only to
    reconstruct operational evidence that was already in memory.
    """
    if not _store.projection_run_store or not hasattr(
        _store.projection_run_store,
        "latest",
    ):
        return None
    try:
        active = _store.active_abox_metadata(world_id)
    except Exception:
        return None
    run_id = str(active.get("projectionRunId") or "").strip()
    snapshot_id = str(active.get("aboxSnapshotId") or "").strip()
    if not run_id or not snapshot_id:
        return None
    try:
        try:
            rows = _store.projection_run_store.latest(limit=20, world_id=world_id)
        except TypeError as error:
            if "unexpected keyword" not in str(error) and "world_id" not in str(error):
                raise
            rows = _store.projection_run_store.latest(limit=20)
    except Exception:
        return None
    row = next(
        (
            item
            for item in rows or []
            if isinstance(item, dict)
            and str(item.get("runId") or "") == run_id
            and str(item.get("aboxSnapshotId") or "") == snapshot_id
        ),
        None,
    )
    if not row:
        return None
    run = projection_run_from_payload(row)
    return run if run.run_id == run_id and run.abox_snapshot_id == snapshot_id else None
