"""Rebuild current PortfolioWorld projections from durable monitor snapshots."""

from __future__ import annotations

import time
from typing import Dict, Mapping

from ..domain.portfolio import account_snapshot_from_monitor_state, monitor_state_has_live_account_data
from ..domain.ontology_worlds import PORTFOLIO_WORLD_TYPE, world_from_metadata, world_from_snapshot


SUCCESS_STATUSES = {
    "ok",
    "unchanged-material-facts",
    "already-projected-material",
}


class OntologyPortfolioRebuildRunner:
    """Replay MySQL source snapshots into an isolated TypeDB candidate.

    The monitor snapshot is the durable source record. This recovery path does
    not call providers, enqueue notifications, mutate reasoning cursors, or
    consume the live mailbox. It only reconstructs the latest PortfolioWorld
    ABox and its aligned native InferenceBox before a blue-green cutover.
    """

    def __init__(self, snapshot_store, projection_recorder):
        self.snapshot_store = snapshot_store
        self.projection_recorder = projection_recorder

    def run(self, limit: int = 0) -> Dict[str, object]:
        started = time.perf_counter()
        try:
            states = dict(self.snapshot_store.load_previous() or {})
        except Exception as error:  # noqa: BLE001 - candidate must fail closed.
            return {
                "status": "source-read-failed",
                "reason": str(error)[:220],
                "readOnlySource": True,
                "mutatedLiveQueue": False,
            }

        rows = []
        projected_attempt_count = 0
        for account_id, state in sorted(states.items(), key=lambda item: str(item[0])):
            snapshot = account_snapshot_from_monitor_state(state)
            if snapshot is None:
                rows.append({
                    "accountId": str(account_id or ""),
                    "status": (
                        "error-invalid-live-snapshot"
                        if monitor_state_has_live_account_data(state)
                        else "skipped-invalid-snapshot"
                    ),
                })
                continue
            if not snapshot.has_live_account_data():
                rows.append({
                    "accountId": str(snapshot.account_id or account_id or ""),
                    "status": "skipped-non-live-snapshot",
                    "snapshotStatus": str(snapshot.status or ""),
                    "snapshotMode": str(snapshot.mode or ""),
                })
                continue
            if limit and projected_attempt_count >= max(1, int(limit)):
                rows.append({
                    "accountId": str(snapshot.account_id or account_id or ""),
                    "status": "error-rebuild-limit-exceeded",
                })
                continue
            projected_attempt_count += 1
            account_started = time.perf_counter()
            result = self.projection_recorder.record_snapshot(
                snapshot,
                reasoning_context={
                    "triggerTypes": ["typedb-blue-green-candidate-rebuild"],
                    "sourceObservedAt": str(snapshot.generated_at or ""),
                    "candidateRebuild": True,
                    "readOnlySource": True,
                },
            )
            status = str(result.get("status") or "error")
            rows.append({
                "accountId": str(snapshot.account_id or account_id or ""),
                "status": status,
                "projected": status in SUCCESS_STATUSES,
                "worldId": str(
                    (result.get("ontologyWorld") or {}).get("worldId") or ""
                    if isinstance(result.get("ontologyWorld"), dict)
                    else result.get("worldId") or ""
                ),
                "aboxSnapshotId": str(result.get("aboxSnapshotId") or result.get("activeAboxSnapshotId") or ""),
                "inferenceGenerationId": str(
                    (result.get("inferenceBox") or {}).get("inferenceGenerationId")
                    if isinstance(result.get("inferenceBox"), dict)
                    else ""
                ),
                "runtimeMs": int((time.perf_counter() - account_started) * 1000),
            })

        attempted = [
            item for item in rows
            if not str(item.get("status") or "").startswith("skipped-")
        ]
        failed = [
            item for item in rows
            if str(item.get("status") or "") not in SUCCESS_STATUSES
            and not str(item.get("status") or "").startswith("skipped-")
        ]
        projected = [item for item in rows if item.get("projected")]
        if failed:
            status = "error"
        elif projected:
            status = "ok"
        else:
            status = "empty"
        return {
            "status": status,
            "readOnlySource": True,
            "mutatedLiveQueue": False,
            "sourceSnapshotCount": len(states),
            "attemptedPortfolioWorldCount": len(attempted),
            "projectedPortfolioWorldCount": len(projected),
            "failedPortfolioWorldCount": len(failed),
            "rebuildLimit": max(0, int(limit or 0)),
            "rows": rows,
            "runtimeMs": int((time.perf_counter() - started) * 1000),
        }


class OntologyPortfolioScopeRepairRunner:
    """Repair one PortfolioWorld subject from its durable MySQL snapshot."""

    def __init__(self, snapshot_store, projection_recorder, settings=None):
        self.snapshot_store = snapshot_store
        self.projection_recorder = projection_recorder
        self.settings = dict(settings or {})

    def enqueue_scope_repair(
        self,
        world_id: str,
        request_id: str,
        repair_requests_by_symbol: Mapping[str, object],
        source_account_id: str = "",
        source_observed_at: str = "",
    ) -> Dict[str, object]:
        started = time.perf_counter()
        requested = {
            str(symbol or "").upper().strip(): dict(value or {})
            for symbol, value in dict(repair_requests_by_symbol or {}).items()
            if str(symbol or "").strip() and isinstance(value, Mapping)
        }
        if not requested:
            return {"status": "not-required", "saved": False, "worldId": str(world_id or "")}
        try:
            states = dict(self.snapshot_store.load_previous() or {})
        except Exception as error:  # noqa: BLE001 - maintenance retries the durable request.
            return {
                "status": "deferred-portfolio-snapshot-read",
                "saved": False,
                "worldId": str(world_id or ""),
                "reason": str(error)[:220],
            }

        selected_snapshot = None
        clean_account_id = str(source_account_id or "").strip()
        for account_id, state in sorted(states.items(), key=lambda item: str(item[0])):
            snapshot = account_snapshot_from_monitor_state(state)
            if snapshot is None or not snapshot.has_live_account_data():
                continue
            snapshot_world_id = world_from_snapshot(snapshot, self.settings).world_id
            if str(snapshot_world_id or "") != str(world_id or ""):
                continue
            if clean_account_id and clean_account_id not in {
                str(account_id or "").strip(),
                str(snapshot.account_id or "").strip(),
            }:
                continue
            selected_snapshot = snapshot
            break
        if selected_snapshot is None:
            return {
                "status": "deferred-portfolio-snapshot-missing",
                "saved": False,
                "worldId": str(world_id or ""),
                "missingSymbols": sorted(requested),
                "reason": "No live durable monitor snapshot matches the PortfolioWorld repair request.",
            }

        result = dict(self.projection_recorder.record_snapshot(
            selected_snapshot,
            target_symbols=sorted(requested),
            reasoning_context={
                "triggerTypes": ["scope-integrity-repair-maintenance"],
                "sourceObservedAt": str(source_observed_at or selected_snapshot.generated_at or ""),
                "scopeRepairRequestsBySymbol": requested,
                "maintenanceRepair": True,
                "mutatedLiveReasoningQueue": False,
            },
        ) or {})
        result_status = str(result.get("status") or "error").strip()
        successful = result_status in SUCCESS_STATUSES
        return {
            "status": (
                "completed-portfolio-scope-repair"
                if successful
                else "deferred-portfolio-scope-repair"
            ),
            "saved": successful,
            "worldId": str(world_id or ""),
            "requestId": str(request_id or ""),
            "queuedSymbolCount": len(requested) if successful else 0,
            "queuedSymbols": sorted(requested) if successful else [],
            "missingSymbols": [] if successful else sorted(requested),
            "projectionStatus": result_status,
            "projection": result,
            "mutatedLiveReasoningQueue": False,
            "durationMs": int((time.perf_counter() - started) * 1000),
            "reason": str(result.get("reason") or "")[:220],
        }


class OntologyScopeRepairRouter:
    """Route repairs to the owning world without crossing inference queues."""

    def __init__(self, shared_world_outbox, portfolio_repair_runner):
        self.shared_world_outbox = shared_world_outbox
        self.portfolio_repair_runner = portfolio_repair_runner

    def enqueue_scope_repair(self, **payload) -> Dict[str, object]:
        world = world_from_metadata({"worldId": str(payload.get("world_id") or "")})
        target = (
            self.portfolio_repair_runner
            if world.world_type == PORTFOLIO_WORLD_TYPE
            else self.shared_world_outbox
        )
        enqueue = getattr(target, "enqueue_scope_repair", None)
        if not callable(enqueue):
            return {
                "status": "not-configured",
                "saved": False,
                "worldId": world.world_id,
                "reason": "No scope repair adapter is configured for this ontology world.",
            }
        result = dict(enqueue(**payload) or {})
        result["repairRoute"] = (
            "portfolio-durable-snapshot"
            if world.world_type == PORTFOLIO_WORLD_TYPE
            else "shared-world-projection-outbox"
        )
        return result
