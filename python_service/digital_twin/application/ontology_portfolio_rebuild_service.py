"""Rebuild current PortfolioWorld projections from durable monitor snapshots."""

from __future__ import annotations

import time
from typing import Dict

from ..domain.portfolio import account_snapshot_from_monitor_state, monitor_state_has_live_account_data


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
