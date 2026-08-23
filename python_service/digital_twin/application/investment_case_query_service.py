"""Query persisted decisions through the canonical investment-case contract."""

from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Tuple

from ..domain.investment_case import (
    INVESTMENT_CASE_VERSION,
    investment_case_history_item,
    investment_case_id,
    investment_case_snapshot,
    parse_investment_case_id,
)
from ..domain.investment_analysis import investment_decision_key
from ..domain.investment_flow import (
    FLOW_STAGE_LABELS,
    FLOW_STATE_LABELS,
    decision_flow_projection,
    item_dict,
    text,
)
from .investment_flow_query_service import InvestmentFlowQueryService


class InvestmentCaseQueryService:
    """Build user cases from MySQL-backed DecisionEpisodes only.

    TypeDB remains authoritative for semantic inference, but no TypeDB query is
    executed here.  Trace data is read lazily from identifiers frozen into the
    persisted episode.
    """

    def __init__(self, decision_episode_store, notification_job_store=None, hypothesis_lifecycle_store=None):
        self.decision_episode_store = decision_episode_store
        self.notification_job_store = notification_job_store
        self.flow_service = InvestmentFlowQueryService(
            decision_episode_store=decision_episode_store,
            notification_job_store=notification_job_store,
            hypothesis_lifecycle_store=hypothesis_lifecycle_store,
        )

    def list_cases(
        self,
        account_id: str = "",
        symbol: str = "",
        limit: int = 100,
        include_operator: bool = False,
    ) -> Dict[str, object]:
        rows = self.flow_service._episodes(  # The flow adapter owns the optimized head-reader fallback.
            account_id,
            symbol,
            max(1, min(500, int(limit or 100))),
        )
        latest = []
        seen = set()
        for row in rows:
            payload = item_dict(row)
            key = (
                text(payload.get("accountId") or payload.get("account_id")) or "default",
                text(payload.get("symbol")).upper(),
            )
            if not key[1] or key in seen:
                continue
            seen.add(key)
            latest.append(payload)

        episode_ids = [text(row.get("episodeId") or row.get("episode_id")) for row in latest]
        jobs_by_episode = self.flow_service._jobs_by_episode(episode_ids)
        snapshots = [
            investment_case_snapshot(
                row,
                jobs_by_episode.get(text(row.get("episodeId") or row.get("episode_id")), []),
            )
            for row in latest
        ]
        snapshots.sort(key=lambda item: (item.updated_at, item.episode_id), reverse=True)
        items = [item.to_dict(compact=True) for item in snapshots]
        status_counts = Counter(item.status for item in snapshots)
        readiness_counts = Counter(item.readiness_state for item in snapshots)
        phase_counts = Counter(item.phase for item in snapshots)
        decision_state_counts = Counter(
            text(next((row for row in item.status_dimensions if row.get("id") == "decision"), {}).get("state")) or "warning"
            for item in snapshots
        )
        data_state_counts = Counter(
            text(next((row for row in item.status_dimensions if row.get("id") == "data"), {}).get("state")) or "warning"
            for item in snapshots
        )
        inference_state_counts = Counter(
            text(next((row for row in item.status_dimensions if row.get("id") == "inference"), {}).get("state")) or "warning"
            for item in snapshots
        )
        ai_state_counts = Counter(
            text(next((row for row in item.status_dimensions if row.get("id") == "ai"), {}).get("state")) or "warning"
            for item in snapshots
        )
        operator_view = {"loaded": False, "stages": [], "issues": []}
        if include_operator:
            flow_items = [
                decision_flow_projection(
                    row,
                    jobs_by_episode.get(text(row.get("episodeId") or row.get("episode_id")), []),
                )
                for row in latest
            ]
            operator_view = {"loaded": True, **self._operator_view(flow_items)}
        return {
            "version": INVESTMENT_CASE_VERSION,
            "status": "ok",
            "readOnly": True,
            "accountId": account_id,
            "symbol": symbol.upper(),
            "count": len(items),
            "summary": {
                "total": len(items),
                "ready": readiness_counts.get("pass", 0),
                "review": sum(readiness_counts.get(value, 0) for value in ("warning", "pending")),
                "blocked": sum(readiness_counts.get(value, 0) for value in ("blocked", "error")),
                "awaitingOutcome": sum(1 for item in snapshots if item.outcome.get("state") == "pending"),
                "statuses": dict(status_counts),
                "readiness": dict(readiness_counts),
                "phases": dict(phase_counts),
                # Compatibility for clients that still read the old validation summary.
                "validation": dict(readiness_counts),
                "attentionRequired": len(items) - readiness_counts.get("pass", 0),
                "dimensions": {
                    "decision": dict(decision_state_counts),
                    "data": dict(data_state_counts),
                    "inference": dict(inference_state_counts),
                    "ai": dict(ai_state_counts),
                },
            },
            "items": items,
            "operatorView": operator_view,
        }

    def detail(self, case_id: str) -> Dict[str, object]:
        episode, snapshot = self._resolve(case_id)
        if not episode or not snapshot:
            return self._not_found(case_id)
        episode_id = snapshot.episode_id
        jobs = self.flow_service._jobs_by_episode([episode_id]).get(episode_id, [])
        snapshot = investment_case_snapshot(episode, jobs)
        payload = snapshot.to_dict(compact=False)
        payload.update({
            "status": "ok",
            "readOnly": True,
            "requestedKey": text(case_id),
            "resolvedFromLegacyKey": text(case_id) not in {snapshot.case_id, snapshot.episode_id},
            "canonicalUrl": f"/?tab=modeling&detail=investment-case&detailKey={snapshot.episode_id}",
            "availableViews": ["summary", "current", "evidence", "reasoning", "history"],
            "historyEndpoint": f"/api/investment-cases/{snapshot.case_id}/history",
            "traceEndpoint": f"/api/investment-cases/{snapshot.case_id}/trace",
        })
        return payload

    def history(self, case_id: str, limit: int = 30) -> Dict[str, object]:
        episode, head = self._resolve(case_id)
        if not episode or not head:
            return self._not_found(case_id)
        bounded_limit = max(1, min(200, int(limit or 30)))
        rows = self._history_rows(head.account_id, head.symbol, bounded_limit)
        if not rows and head.account_id == "default":
            rows = [
                row for row in self._history_rows("", head.symbol, bounded_limit)
                if investment_case_snapshot(row).account_id == "default"
            ]
        if not rows:
            rows = [episode]
        snapshots = [investment_case_snapshot(row) for row in rows]
        snapshots.sort(key=lambda item: (item.decided_at, item.episode_id), reverse=True)
        items = [
            investment_case_history_item(
                snapshot,
                snapshots[index + 1] if index + 1 < len(snapshots) else None,
            )
            for index, snapshot in enumerate(snapshots)
        ]
        return {
            "version": INVESTMENT_CASE_VERSION,
            "status": "ok",
            "readOnly": True,
            "caseId": head.case_id,
            "accountId": head.account_id,
            "symbol": head.symbol,
            "count": len(items),
            "items": items,
        }

    def trace(self, case_id: str) -> Dict[str, object]:
        _episode, snapshot = self._resolve(case_id)
        if not snapshot:
            return self._not_found(case_id)
        payload = self.flow_service.detail(snapshot.episode_id)
        if payload.get("status") != "ok":
            return self._not_found(case_id)
        payload["caseExplanation"] = dict(snapshot.explanation)
        payload["statusDimensions"] = [dict(item) for item in snapshot.status_dimensions]
        payload["evidence"] = dict(snapshot.evidence)
        return {
            "version": INVESTMENT_CASE_VERSION,
            "status": "ok",
            "readOnly": True,
            "audience": "operator",
            "caseId": snapshot.case_id,
            "episodeId": snapshot.episode_id,
            "accountId": snapshot.account_id,
            "symbol": snapshot.symbol,
            "trace": payload,
        }

    def _resolve(self, case_id: str) -> Tuple[object, object]:
        key = text(case_id)
        if not key:
            return None, None
        if key.startswith("decision-episode:") or key.startswith("episode:"):
            episode = self.flow_service._episode(key)
            return self._resolved_pair(episode)
        identity = parse_investment_case_id(key)
        if identity:
            rows = self.flow_service._episodes(identity["accountId"], identity["symbol"], 1)
            for row in rows:
                if investment_case_snapshot(row).case_id == key:
                    return self._resolved_pair(row)

            # "default" is the public account label, while older persisted rows
            # may still contain an empty account id. Resolve by the canonical id
            # instead of making a stale link fail after a read-model migration.
            rows = self.flow_service._episodes("", identity["symbol"], 500)
            for row in rows:
                if investment_case_snapshot(row).case_id == key:
                    return self._resolved_pair(row)

        # Investment-flow links existed before the stable case contract. Keep
        # those URLs usable so an open mobile tab survives an app deployment.
        if key.startswith("flow:"):
            for row in self.flow_service._episodes("", "", 500):
                payload = item_dict(row)
                if text(payload.get("flowId") or payload.get("flow_id")) == key:
                    return self._resolved_pair(row)
        if key.startswith("decision:"):
            # Early action-list links were hashes over account, symbol, and an
            # optional episode id. Resolve them against persisted heads so the
            # link remains usable after the mutable action queue is refreshed.
            for row in self.flow_service._episodes("", "", 500):
                snapshot = investment_case_snapshot(row)
                candidates = {
                    investment_decision_key(snapshot.account_id, snapshot.symbol, snapshot.episode_id),
                    investment_decision_key(snapshot.account_id, snapshot.symbol, ""),
                }
                if key in candidates:
                    return self._resolved_pair(row)
        return None, None

    def _resolved_pair(self, row: object) -> Tuple[object, object]:
        if not row:
            return None, None
        snapshot = investment_case_snapshot(row)
        episode = self.flow_service._episode(snapshot.episode_id) or row
        return episode, investment_case_snapshot(episode)

    def _history_rows(self, account_id: str, symbol: str, limit: int) -> List[object]:
        reader = getattr(self.decision_episode_store, "list", None)
        if not callable(reader):
            return []
        try:
            return list(reader(account_id=account_id, symbol=symbol, limit=limit) or [])
        except TypeError:
            return list(reader(account_id, symbol, limit) or [])

    def _operator_view(self, flow_items: Iterable[Dict[str, object]]) -> Dict[str, object]:
        stage_counts = defaultdict(Counter)
        issues = []
        for item in flow_items:
            for stage in item.get("stages") or []:
                stage_counts[text(stage.get("id"))][text(stage.get("state")) or "warning"] += 1
            if item.get("readinessState") != "pass":
                issues.append({
                    "caseId": investment_case_id(item.get("accountId"), item.get("symbol")),
                    "episodeId": item.get("episodeId"),
                    "accountId": item.get("accountId"),
                    "symbol": item.get("symbol"),
                    "name": item.get("name"),
                    "stage": item.get("blockingStage"),
                    "stageLabel": item.get("blockingStageLabel"),
                    "state": item.get("readinessState"),
                    "reason": item.get("blockingReason"),
                    "nextAction": item.get("nextAction"),
                    "updatedAt": item.get("updatedAt"),
                })
        stages = []
        for stage_id, label in FLOW_STAGE_LABELS.items():
            counts = stage_counts.get(stage_id, Counter())
            state = "error" if counts.get("error") else (
                "blocked" if counts.get("blocked") else (
                    "warning" if counts.get("warning") else (
                        "pending" if counts.get("pending") else "pass"
                    )
                )
            )
            stages.append({
                "id": stage_id,
                "label": label,
                "state": state,
                "stateLabel": FLOW_STATE_LABELS[state],
                "affectedCount": sum(counts.get(value, 0) for value in ("error", "blocked", "warning", "pending")),
                "counts": dict(counts),
            })
        return {"stages": stages, "issues": issues}

    def _not_found(self, case_id: str) -> Dict[str, object]:
        return {
            "version": INVESTMENT_CASE_VERSION,
            "status": "not-found",
            "readOnly": True,
            "caseId": text(case_id),
            "error": "투자 케이스가 갱신되었거나 현재 목록에 없습니다. 목록을 새로고침해 주세요.",
        }
