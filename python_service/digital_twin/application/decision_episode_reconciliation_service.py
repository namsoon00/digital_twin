"""Repair DecisionEpisode rows from immutable notification audit payloads."""

from collections import Counter
from typing import Dict

from ..domain.investment_brain import DecisionEpisode


class DecisionEpisodeReconciliationService:
    """Recover decisions that were delivered before their read model was saved.

    Notification payloads are the recovery source because the AI worker freezes
    the exact episode into that payload before delivery. The service never
    recreates a decision from current market data.
    """

    def __init__(self, decision_episode_store, notification_job_store):
        self.decision_episode_store = decision_episode_store
        self.notification_job_store = notification_job_store

    def reconcile(self, limit: int = 500, dry_run: bool = False) -> Dict[str, object]:
        bounded_limit = max(1, min(5000, int(limit or 500)))
        jobs = self._recent_jobs(bounded_limit)
        counts = Counter()
        recovered_ids = []
        for job in jobs:
            counts["scanned"] += 1
            context = job.context if isinstance(getattr(job, "context", None), dict) else {}
            payload = context.get("investmentDecisionEpisode")
            if not isinstance(payload, dict):
                counts["withoutEpisodePayload"] += 1
                continue
            episode_id = str(payload.get("episodeId") or "").strip()
            if not episode_id:
                counts["invalidPayload"] += 1
                continue
            if self.decision_episode_store.get(episode_id):
                counts["alreadyPersisted"] += 1
                continue
            if not str(payload.get("sourceAboxSnapshotId") or "").strip() or not str(
                payload.get("inferenceGenerationId") or ""
            ).strip():
                counts["incompleteContract"] += 1
                continue
            try:
                episode = DecisionEpisode.from_dict(payload)
                episode.facts_at_decision = {
                    **dict(episode.facts_at_decision or {}),
                    "reconciliationSource": "notification-audit-payload",
                }
                if not dry_run:
                    self.decision_episode_store.save(episode)
                counts["recoverable" if dry_run else "recovered"] += 1
                recovered_ids.append(episode_id)
            except Exception:  # noqa: BLE001 - report bad historical rows without stopping the repair.
                counts["failed"] += 1
        return {
            "status": "preview" if dry_run else ("ok" if not counts["failed"] else "partial"),
            "dryRun": bool(dry_run),
            "limit": bounded_limit,
            "counts": dict(counts),
            "recoveredEpisodeIds": recovered_ids[:100],
        }

    def _recent_jobs(self, limit: int):
        jobs = []
        for status in ("done", "sent"):
            offset = 0
            while len(jobs) < limit:
                page_size = min(100, limit - len(jobs))
                rows, total = self.notification_job_store.recent_page(
                    limit=page_size,
                    offset=offset,
                    message_type="investmentInsight",
                    status=status,
                )
                jobs.extend(rows or [])
                offset += len(rows or [])
                if not rows or offset >= int(total or 0):
                    break
        return jobs[:limit]
