"""Read the latest verified monitor snapshot for bounded ontology replay."""

import copy
from datetime import datetime, timezone
from typing import Dict

from ..domain.portfolio import (
    AccountSnapshot,
    PortfolioSummary,
    account_snapshot_from_monitor_state,
    monitor_state_has_live_account_data,
    utc_now_iso,
)


def _timestamp(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class LatestMonitorSnapshotReasoningSource:
    """Provide TypeDB projection with persisted current facts, never a refetch.

    The source monitor owns provider collection and persists its verified
    snapshot first.  The isolated reasoning worker then reads that state.  If
    the selected mailbox revision is newer than the stored source snapshot,
    it returns an explicit non-live placeholder so the runner retries after a
    monitor refresh instead of reasoning from stale facts.
    """

    def __init__(self, monitor_store, settings: Dict[str, object] = None, now_provider=None):
        self.monitor_store = monitor_store
        self.settings = dict(settings or {})
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def monitor_interval_seconds(self) -> int:
        try:
            configured = int(float(str(self.settings.get("monitorAccountIntervalSeconds") or "180")))
        except (TypeError, ValueError):
            configured = 180
        return max(30, min(3600, configured))

    def retry_after_seconds(self) -> int:
        try:
            configured = int(float(str(self.settings.get("ontologyReasoningProjectionRetrySeconds") or "30")))
        except (TypeError, ValueError):
            configured = 30
        return max(5, min(self.monitor_interval_seconds(), configured))

    def maximum_snapshot_age_seconds(self) -> int:
        # This is a source-freshness bound derived from the existing monitor
        # cadence. It is not an investment threshold or a decision rule.
        return max(60, min(3600, self.monitor_interval_seconds() * 2))

    def current_state(self, account_id: str) -> Dict[str, object]:
        previous = getattr(self.monitor_store, "previous", {}) or {}
        state = previous.get(str(account_id or "")) if isinstance(previous, dict) else {}
        return copy.deepcopy(state) if isinstance(state, dict) else {}

    def current_state_metadata(self, account_id: str) -> Dict[str, object]:
        """Read only the durable source boundary needed before TypeDB work.

        The full persisted monitor snapshot can contain a sizeable research
        archive.  A stale-source check must not deserialize or copy that
        payload before it knows a current snapshot exists.  MySQL-backed
        stores expose a narrow metadata read; lightweight compatibility
        stores retain the in-memory fallback.
        """
        reader = getattr(self.monitor_store, "snapshot_metadata", None)
        if callable(reader):
            try:
                value = reader(str(account_id or ""))
            except Exception:  # noqa: BLE001 - the authoritative replay remains fail-safe below.
                value = None
            if isinstance(value, dict):
                return dict(value)
        previous = getattr(self.monitor_store, "previous", {}) or {}
        state = previous.get(str(account_id or "")) if isinstance(previous, dict) else {}
        if not isinstance(state, dict):
            return {}
        return {
            "accountId": str(state.get("accountId") or state.get("account_id") or account_id or ""),
            "mode": str(state.get("mode") or ""),
            "status": str(state.get("status") or ""),
            "generatedAt": str(state.get("generatedAt") or state.get("generated_at") or ""),
        }

    def snapshot_freshness_at(self, generated_at: object, reasoning_context: Dict[str, object] = None) -> Dict[str, object]:
        """Evaluate a compact persisted source timestamp without rehydration."""
        source_context = dict(reasoning_context or {})
        snapshot_at = _timestamp(generated_at)
        source_at_text = str(source_context.get("sourceObservedAt") or "").strip()
        source_at = _timestamp(source_at_text)
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        if not snapshot_at:
            return {
                "status": "deferred",
                "reason": "The stored monitor snapshot has no usable source timestamp.",
                "retryAfterSeconds": self.retry_after_seconds(),
                "sourceObservedAt": source_at_text,
                "snapshotGeneratedAt": str(generated_at or ""),
            }
        if source_at and snapshot_at < source_at:
            return {
                "status": "deferred",
                "reason": "The latest monitor snapshot predates the requested fact revision.",
                "retryAfterSeconds": self.retry_after_seconds(),
                "sourceObservedAt": source_at_text,
                "snapshotGeneratedAt": str(generated_at or ""),
                "snapshotLagSeconds": max(0, int((source_at - snapshot_at).total_seconds())),
            }
        age_seconds = max(0, int((now - snapshot_at).total_seconds()))
        if not source_at and age_seconds > self.maximum_snapshot_age_seconds():
            return {
                "status": "deferred",
                "reason": "The requested fact has no source timestamp and the stored monitor snapshot is too old to replay safely.",
                "retryAfterSeconds": self.retry_after_seconds(),
                "sourceObservedAt": source_at_text,
                "snapshotGeneratedAt": str(generated_at or ""),
                "snapshotAgeSeconds": age_seconds,
            }
        return {
            "status": "ready",
            "reason": "The latest verified monitor snapshot covers the requested source revision.",
            "sourceObservedAt": source_at_text,
            "snapshotGeneratedAt": str(generated_at or ""),
            "snapshotAgeSeconds": age_seconds,
            "sourceTimestampState": "verified" if source_at else "unknown",
        }

    def snapshot_freshness(self, snapshot: AccountSnapshot, reasoning_context: Dict[str, object] = None) -> Dict[str, object]:
        return self.snapshot_freshness_at(snapshot.generated_at, reasoning_context)

    def preflight(self, accounts, reasoning_context: Dict[str, object] = None) -> Dict[str, object]:
        """Prove source freshness before claiming a mailbox or opening TypeDB.

        This is intentionally a timestamp/control-plane check only.  It does
        not use any source field as an investment condition and it never
        advances, releases, or acknowledges queued work.
        """
        account_rows = []
        for account in accounts or []:
            account_id = str(getattr(account, "account_id", "") or "").strip()
            if not account_id:
                continue
            metadata = self.current_state_metadata(account_id)
            if not monitor_state_has_live_account_data(metadata):
                account_rows.append({
                    "accountId": account_id,
                    "status": "deferred",
                    "reason": "No verified live monitor snapshot is available for this account.",
                    "retryAfterSeconds": self.retry_after_seconds(),
                    "sourceObservedAt": str((reasoning_context or {}).get("sourceObservedAt") or ""),
                    "snapshotGeneratedAt": str(metadata.get("generatedAt") or ""),
                })
                continue
            account_rows.append({
                "accountId": account_id,
                **self.snapshot_freshness_at(metadata.get("generatedAt"), reasoning_context),
            })
        deferred = [item for item in account_rows if str(item.get("status") or "") != "ready"]
        if deferred:
            first = deferred[0]
            return {
                "ready": False,
                "status": "deferred-source-snapshot",
                "reason": str(first.get("reason") or "The latest verified monitor snapshot is not ready."),
                "retryAfterSeconds": max(1, int(first.get("retryAfterSeconds") or self.retry_after_seconds())),
                "accounts": account_rows,
            }
        return {
            "ready": True,
            "status": "ready",
            "reason": "All selected accounts have a persisted monitor snapshot covering this source revision.",
            "accounts": account_rows,
        }

    def unavailable_snapshot(self, account, replay: Dict[str, object]) -> AccountSnapshot:
        return AccountSnapshot(
            account_id=str(getattr(account, "account_id", "") or ""),
            account_label=str(getattr(account, "label", "") or "투자 계좌"),
            provider=str(getattr(account, "provider", "") or ""),
            mode="deferred",
            status="Reasoning source snapshot is not ready",
            generated_at=utc_now_iso(),
            portfolio=PortfolioSummary(0.0, 0.0, 0.0, [], [], 0.0),
            metadata={"reasoningSnapshotReplay": dict(replay or {})},
        )

    def __call__(self, account, reasoning_context: Dict[str, object] = None) -> AccountSnapshot:
        state = self.current_state(getattr(account, "account_id", ""))
        snapshot = account_snapshot_from_monitor_state(state)
        if not snapshot or not snapshot.has_live_account_data():
            return self.unavailable_snapshot(
                account,
                {
                    "status": "deferred",
                    "reason": "No verified live monitor snapshot is available for this account.",
                    "retryAfterSeconds": self.retry_after_seconds(),
                    "sourceObservedAt": str((reasoning_context or {}).get("sourceObservedAt") or ""),
                },
            )
        freshness = self.snapshot_freshness(snapshot, reasoning_context)
        if str(freshness.get("status") or "") != "ready":
            return self.unavailable_snapshot(account, freshness)
        metadata = copy.deepcopy(snapshot.metadata or {})
        account_context = getattr(account, "ontology_account_context", None)
        if callable(account_context):
            metadata["accountContext"] = dict(account_context() or {})
        metadata["reasoningSnapshotReplay"] = {
            **freshness,
            "mode": "persisted-verified-monitor-snapshot",
        }
        snapshot.metadata = metadata
        return snapshot
