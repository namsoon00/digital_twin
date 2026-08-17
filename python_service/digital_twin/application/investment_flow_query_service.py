"""Compose existing stores into user and operator investment-flow views."""

from collections import Counter, defaultdict
from typing import Dict, Iterable, List

from ..domain.investment_flow import (
    FLOW_STAGE_LABELS,
    FLOW_STATE_LABELS,
    INVESTMENT_FLOW_VERSION,
    decision_flow_projection,
    flow_nodes_and_links,
    item_dict,
    text,
)


class InvestmentFlowQueryService:
    def __init__(self, decision_episode_store, notification_job_store=None, hypothesis_lifecycle_store=None):
        self.decision_episode_store = decision_episode_store
        self.notification_job_store = notification_job_store
        self.hypothesis_lifecycle_store = hypothesis_lifecycle_store

    def summary(self, account_id: str = "", symbol: str = "", limit: int = 100) -> Dict[str, object]:
        episodes = self._episodes(account_id, symbol, max(1, min(500, int(limit or 100))))
        latest = []
        seen = set()
        for episode in episodes:
            payload = item_dict(episode)
            key = (text(payload.get("accountId") or payload.get("account_id")), text(payload.get("symbol")).upper())
            if not key[1] or key in seen:
                continue
            seen.add(key)
            latest.append(episode)
        jobs_by_episode = self._jobs_by_episode([
            text(item_dict(episode).get("episodeId") or item_dict(episode).get("episode_id"))
            for episode in latest
        ])
        items = [
            decision_flow_projection(
                episode,
                jobs_by_episode.get(text(item_dict(episode).get("episodeId") or item_dict(episode).get("episode_id")), []),
            )
            for episode in latest
        ]
        items.sort(key=lambda item: (text(item.get("updatedAt")), text(item.get("episodeId"))), reverse=True)
        validation_counts = Counter(text(item.get("validationState")) or "warning" for item in items)
        readiness_counts = Counter(text(item.get("readinessState")) or "warning" for item in items)
        stage_counts = defaultdict(Counter)
        issues = []
        for item in items:
            for stage in item.get("stages") or []:
                stage_counts[text(stage.get("id"))][text(stage.get("state"))] += 1
            if item.get("readinessState") != "pass":
                issues.append({
                    "flowId": item.get("flowId"),
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
        operator_stages = []
        for stage_id, label in FLOW_STAGE_LABELS.items():
            counts = stage_counts.get(stage_id, Counter())
            state = "error" if counts.get("error") else (
                "blocked" if counts.get("blocked") else (
                    "warning" if counts.get("warning") else (
                        "pending" if counts.get("pending") else "pass"
                    )
                )
            )
            affected = sum(counts.get(key, 0) for key in ("error", "blocked", "warning", "pending"))
            operator_stages.append({
                "id": stage_id,
                "label": label,
                "state": state,
                "stateLabel": FLOW_STATE_LABELS[state],
                "affectedCount": affected,
                "counts": dict(counts),
            })
        return {
            "version": INVESTMENT_FLOW_VERSION,
            "status": "ok",
            "readOnly": True,
            "accountId": account_id,
            "symbol": symbol.upper(),
            "count": len(items),
            "summary": {
                "total": len(items),
                "validation": dict(validation_counts),
                "readiness": dict(readiness_counts),
                "attentionRequired": len(issues),
            },
            "items": [self._compact(item) for item in items],
            "operatorView": {
                "stages": operator_stages,
                "issues": issues,
            },
        }

    def detail(self, episode_id: str) -> Dict[str, object]:
        episode = self._episode(episode_id)
        if not episode:
            return {
                "version": INVESTMENT_FLOW_VERSION,
                "status": "not-found",
                "readOnly": True,
                "episodeId": episode_id,
            }
        jobs = self._jobs_by_episode([episode_id]).get(episode_id, [])
        projection = decision_flow_projection(episode, jobs)
        graph = flow_nodes_and_links(projection)
        lifecycles = self._hypothesis_lifecycles(projection)
        projection.pop("raw", None)
        gaps = []
        for stage in projection.get("stages") or []:
            if stage.get("state") in {"warning", "pending", "blocked", "error"}:
                gaps.append({
                    "stage": stage.get("id"),
                    "stageLabel": stage.get("label"),
                    "state": stage.get("state"),
                    "detail": stage.get("detail"),
                })
        return {
            **projection,
            "status": "ok",
            "readOnly": True,
            "lineage": graph,
            "hypothesisLifecycles": lifecycles,
            "gaps": gaps,
        }

    def _episodes(self, account_id: str, symbol: str, limit: int) -> List[object]:
        reader = getattr(self.decision_episode_store, "list", None)
        if not callable(reader):
            return []
        try:
            return list(reader(account_id=account_id, symbol=symbol.upper(), limit=limit) or [])
        except TypeError:
            return list(reader(account_id, symbol.upper(), limit) or [])

    def _episode(self, episode_id: str):
        reader = getattr(self.decision_episode_store, "get", None)
        if callable(reader):
            try:
                return reader(episode_id)
            except Exception:  # noqa: BLE001 - API reports not-found instead of leaking storage details.
                return None
        for episode in self._episodes("", "", 500):
            payload = item_dict(episode)
            if text(payload.get("episodeId") or payload.get("episode_id")) == episode_id:
                return episode
        return None

    def _jobs_by_episode(self, episode_ids: Iterable[str]) -> Dict[str, List[object]]:
        ids = [item for item in dict.fromkeys(text(value) for value in episode_ids or []) if item]
        result: Dict[str, List[object]] = {item: [] for item in ids}
        if not ids or not self.notification_job_store:
            return result
        batch_reader = getattr(self.notification_job_store, "jobs_for_decision_episodes", None)
        if callable(batch_reader):
            try:
                jobs = batch_reader(ids, limit=max(100, len(ids) * 4)) or []
            except Exception:  # noqa: BLE001
                jobs = []
            for job in jobs:
                payload = item_dict(job)
                episode_id = text(payload.get("decisionEpisodeId") or payload.get("decision_episode_id"))
                if episode_id in result:
                    result[episode_id].append(job)
            return result
        reader = getattr(self.notification_job_store, "recent_page", None)
        if not callable(reader):
            return result
        for episode_id in ids[:30]:
            try:
                jobs, _total = reader(limit=20, query=episode_id)
            except Exception:  # noqa: BLE001
                continue
            for job in jobs or []:
                payload = item_dict(job)
                if text(payload.get("decisionEpisodeId") or payload.get("decision_episode_id")) == episode_id:
                    result[episode_id].append(job)
        return result

    def _hypothesis_lifecycles(self, projection: Dict[str, object]) -> List[Dict[str, object]]:
        store = self.hypothesis_lifecycle_store
        selected_id = text(projection.get("selectedHypothesisId"))
        if not store or not selected_id:
            return []
        reader = getattr(store, "list_current", None)
        if not callable(reader):
            return []
        try:
            rows = reader(
                account_id=text(projection.get("accountId")),
                symbol=text(projection.get("symbol")),
                limit=100,
            )
        except Exception:  # noqa: BLE001
            return []
        result = []
        for row in rows or []:
            payload = item_dict(row)
            if selected_id not in {
                text(payload.get("lifecycleId") or payload.get("lifecycle_id")),
                text(payload.get("lifecycleKey") or payload.get("lifecycle_key")),
            }:
                continue
            result.append(payload)
        return result

    def _compact(self, item: Dict[str, object]) -> Dict[str, object]:
        omitted = {"raw", "hypotheses", "guardrails", "evidenceIds", "ruleIds", "abstention"}
        return {key: value for key, value in item.items() if key not in omitted}
