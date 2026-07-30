"""Use case for previewing and applying minimal MySQL retention."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Mapping

from ..domain.mysql_minimal_retention import (
    MySQLMinimalRetentionPolicy,
    mysql_minimal_retention_policy,
    policy_cutoffs,
)


class MySQLMinimalRetentionService:
    """Coordinate a bounded retention pass without exposing storage payloads."""

    def __init__(self, repository, settings: Mapping[str, object] = None):
        self.repository = repository
        self.settings = dict(settings or {})

    def policy(self) -> MySQLMinimalRetentionPolicy:
        return mysql_minimal_retention_policy(self.settings)

    def run_once(
        self,
        now: datetime = None,
        force: bool = False,
        apply: bool = False,
        preview: bool = False,
        preview_before_apply: bool = False,
    ) -> Dict[str, object]:
        policy = self.policy()
        current = now or datetime.now(timezone.utc)
        if not policy.enabled and not force:
            return {
                "status": "disabled",
                "mode": "preview",
                "profile": policy.profile,
                "policy": policy.to_dict(),
                "deleted": 0,
                "compacted": 0,
                "tables": {},
            }

        mode = "apply" if apply else ("preview" if preview else policy.mode)
        preview_result = (
            self.repository.preview(policy, now=current)
            if mode == "preview" or preview_before_apply
            else {}
        )
        result = {
            "status": "preview",
            "mode": mode,
            "profile": policy.profile,
            "policy": policy.to_dict(),
            "cutoffs": policy_cutoffs(policy, now=current),
            "preview": preview_result,
            "deleted": 0,
            "compacted": 0,
            "tables": {},
        }
        if mode != "apply":
            self.repository.record_run(result, now=current)
            return result

        applied = self.repository.apply(policy, now=current)
        result.update({
            "status": str(applied.get("status") or "ok"),
            "deleted": int(applied.get("deleted") or 0),
            "compacted": int(applied.get("compacted") or 0),
            "estimatedBytes": int(applied.get("estimatedBytes") or 0),
            "tables": dict(applied.get("tables") or {}),
            "policies": dict(applied.get("policies") or {}),
            "skipped": str(applied.get("skipped") or ""),
        })
        self.repository.record_run(result, now=current)
        return result
