"""Application use case for read-only hypothesis review context.

It joins TypeDB-derived lifecycle audit records with later decision outcomes.
The resulting brief is context for AI and people; it does not choose or modify
an investment action.
"""

from typing import Dict, Iterable, List, Mapping

from ..domain.hypothesis_review import (
    lifecycle_references_from_episodes,
    lifecycle_review_item,
    outcome_assessment_for_lifecycle,
    text,
    upper,
    values,
)
from ..domain.ontology_rulebox_contracts import GraphInferenceRule


HYPOTHESIS_DECISION_BRIEF_VERSION = "hypothesis-decision-brief-v1"


def bounded_int(settings: Mapping[str, object], key: str, fallback: int, lower: int, upper_bound: int) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(lower, min(upper_bound, value))


def item_dict(value: object) -> Dict[str, object]:
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
        return dict(payload or {}) if isinstance(payload, dict) else {}
    return dict(value or {}) if isinstance(value, Mapping) else {}


def unique_rows(rows: Iterable[object], key_name: str) -> List[Dict[str, object]]:
    result = []
    seen = set()
    for raw in rows or []:
        row = item_dict(raw)
        key = text(row.get(key_name))
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


class HypothesisReviewService:
    """Read model for current hypotheses, their changes, and observed results."""

    def __init__(
        self,
        hypothesis_lifecycle_store=None,
        decision_episode_store=None,
        ontology_repository=None,
        settings: Dict[str, object] = None,
    ):
        self.hypothesis_lifecycle_store = hypothesis_lifecycle_store
        self.decision_episode_store = decision_episode_store
        self.ontology_repository = ontology_repository
        self.settings = dict(settings or {})

    def minimum_samples(self) -> int:
        return bounded_int(
            self.settings,
            "hypothesisOutcomeReviewMinimumSamples",
            bounded_int(self.settings, "investmentBrainOutcomeReviewMinimumSamples", 3, 1, 100),
            1,
            100,
        )

    def episode_limit(self) -> int:
        return bounded_int(self.settings, "hypothesisOutcomeReviewEpisodeLimit", 500, 20, 2000)

    def current_records(
        self,
        account_id: str = "",
        symbol: str = "",
        market_id: str = "",
        scope: str = "",
        limit: int = 100,
    ) -> List[Dict[str, object]]:
        store = self.hypothesis_lifecycle_store
        if not store or not hasattr(store, "list_current"):
            return []
        rows = store.list_current(
            account_id=account_id,
            symbol=symbol,
            market_id=market_id,
            scope=scope,
            limit=max(1, min(1000, int(limit or 100))),
        )
        return unique_rows(rows, "lifecycleKey")

    def lifecycle_events(
        self,
        account_id: str = "",
        symbol: str = "",
        market_id: str = "",
        scope: str = "",
        limit: int = 100,
    ) -> List[Dict[str, object]]:
        store = self.hypothesis_lifecycle_store
        if not store or not hasattr(store, "list_events"):
            return []
        rows = store.list_events(
            account_id=account_id,
            symbol=symbol,
            market_id=market_id,
            scope=scope,
            limit=max(1, min(1000, int(limit or 100))),
        )
        return unique_rows(rows, "transitionId")

    def episodes(self, account_id: str = "", symbol: str = "", limit: int = 0) -> List[Dict[str, object]]:
        store = self.decision_episode_store
        if not store or not hasattr(store, "list"):
            return []
        rows = store.list(account_id=account_id, symbol=symbol, limit=limit or self.episode_limit())
        return unique_rows(rows, "episodeId")

    def active_lifecycle_ids(self, relation_context: Mapping[str, object]) -> set:
        hypothesis_set = relation_context.get("hypothesisSet") if isinstance(relation_context.get("hypothesisSet"), Mapping) else {}
        if not hypothesis_set and isinstance(relation_context.get("investmentBrain"), Mapping):
            brain = relation_context.get("investmentBrain") or {}
            hypothesis_set = brain.get("hypothesisSet") if isinstance(brain.get("hypothesisSet"), Mapping) else {}
        ids = set()
        for item in hypothesis_set.get("marketHypotheses") or []:
            if isinstance(item, Mapping) and text(item.get("marketHypothesisId")):
                ids.add(text(item.get("marketHypothesisId")))
        for item in hypothesis_set.get("accountOverlays") or []:
            if isinstance(item, Mapping) and text(item.get("accountOverlayId")):
                ids.add(text(item.get("accountOverlayId")))
        for item in hypothesis_set.get("hypotheses") or []:
            if not isinstance(item, Mapping):
                continue
            if text(item.get("marketHypothesisId")):
                ids.add(text(item.get("marketHypothesisId")))
            if text(item.get("accountHypothesisOverlayId")):
                ids.add(text(item.get("accountHypothesisOverlayId")))
            if not text(item.get("marketHypothesisId")) and not text(item.get("accountHypothesisOverlayId")) and text(item.get("hypothesisId")):
                ids.add("hypothesis:" + text(item.get("hypothesisId")))
        return ids

    def assessment_for_record(
        self,
        record: Mapping[str, object],
        account_id: str = "",
        market_episodes: Iterable[object] = None,
        account_episodes: Iterable[object] = None,
    ) -> Dict[str, object]:
        if text(record.get("scope")) == "market":
            episodes = list(market_episodes or [])
        else:
            episodes = list(account_episodes or [])
            if not episodes and account_id:
                episodes = self.episodes(account_id, upper(record.get("symbol")))
        return outcome_assessment_for_lifecycle(record, episodes, self.minimum_samples())

    def brief(
        self,
        relation_context: Dict[str, object],
        account_id: str = "",
        symbol: str = "",
        research_cycle: Dict[str, object] = None,
    ) -> Dict[str, object]:
        context = dict(relation_context or {})
        subject = context.get("subject") if isinstance(context.get("subject"), Mapping) else {}
        resolved_symbol = upper(symbol or subject.get("symbol") or (context.get("facts") or {}).get("symbol"))
        resolved_account_id = text(account_id or context.get("accountId"))
        records = self.current_records(account_id=resolved_account_id, symbol=resolved_symbol, limit=100)
        active_ids = self.active_lifecycle_ids(context)
        if active_ids:
            records = [item for item in records if text(item.get("lifecycleId")) in active_ids]
        if not records:
            lifecycle = context.get("hypothesisLifecycle") if isinstance(context.get("hypothesisLifecycle"), Mapping) else {}
            records = [dict(item) for item in lifecycle.get("records") or [] if isinstance(item, Mapping)]
        market_episodes = self.episodes(symbol=resolved_symbol)
        account_episodes = self.episodes(account_id=resolved_account_id, symbol=resolved_symbol) if resolved_account_id else []
        items = [
            lifecycle_review_item(
                record,
                self.assessment_for_record(record, resolved_account_id, market_episodes, account_episodes),
            )
            for record in records
        ]
        items.sort(key=lambda item: (str(item.get("scope") or ""), str(item.get("lifecycleKey") or "")))
        material = [item for item in items if item.get("materialChange") or item.get("state") in {"invalidated", "expired"}]
        next_data = []
        freshness_warnings = []
        for item in items:
            for value in (item.get("policy") or {}).get("nextDataRequirements") or []:
                if text(value) and text(value) not in next_data:
                    next_data.append(text(value))
            for profile in item.get("freshness") or []:
                status = text(profile.get("status")).lower()
                is_required = bool(profile.get("required"))
                usable = bool(profile.get("judgementEvidenceUsable", True))
                if status in {"stale", "unavailable"} and (is_required or not usable):
                    label = text(profile.get("domain")) + " 데이터가 " + ("오래됨" if status == "stale" else "없음")
                    if label not in freshness_warnings:
                        freshness_warnings.append(label)
        research = self.research_summary(research_cycle or context.get("researchCycle"))
        if not items:
            summary = "현재 TypeDB 세대와 연결된 가설 수명주기 기록이 아직 없습니다."
            status = "empty"
        elif material:
            summary = "이전 정상 추론 세대와 비교해 " + str(len(material)) + "개 가설의 근거나 유효 상태가 바뀌었습니다."
            status = "ok"
        else:
            summary = "현재 가설의 근거와 유효 상태에 이전 세대 대비 큰 변화가 없습니다."
            status = "ok"
        return {
            "version": HYPOTHESIS_DECISION_BRIEF_VERSION,
            "status": status,
            "source": "typedb-hypothesis-lifecycle+decision-episode-outcome",
            "decisionEligibility": "context-only-not-action-selector",
            "automaticDeployment": False,
            "accountId": resolved_account_id,
            "symbol": resolved_symbol,
            "inferenceGenerationId": text(context.get("inferenceGenerationId")),
            "summary": summary,
            "items": items[:12],
            "materialChanges": material[:8],
            "nextDataRequirements": next_data[:12],
            "freshnessWarnings": freshness_warnings[:8],
            "research": research,
        }

    def research_summary(self, value: object) -> Dict[str, object]:
        cycle = dict(value or {}) if isinstance(value, Mapping) else {}
        if not cycle:
            return {"status": "unavailable", "decisionEligibility": "no-new-research"}
        return {
            "status": text(cycle.get("status")) or "unknown",
            "changedEvidenceCount": bounded_int(cycle, "changedEvidenceCount", 0, 0, 100000),
            "reasoningRefreshed": bool(cycle.get("reasoningRefreshed")),
            "investmentJudgmentEligible": bool(cycle.get("investmentJudgmentEligible")),
            "verifiedClaimCount": len(cycle.get("verifiedClaims") or []),
            "rejectedClaimCount": len(cycle.get("rejectedClaims") or []),
            "reason": text(cycle.get("reason")),
        }

    def rule_policy_map(self) -> Dict[str, Dict[str, object]]:
        repository = self.ontology_repository
        if not repository or not hasattr(repository, "rulebox_snapshot"):
            return {}
        try:
            snapshot = repository.rulebox_snapshot()
        except Exception:  # noqa: BLE001 - workspace remains read-only when TypeDB is unavailable.
            return {}
        rulebox_status = text(snapshot.get("status")).lower()
        editable = bool(
            hasattr(repository, "save_rulebox")
            and bool(snapshot.get("configured", True))
            and rulebox_status not in {"disabled", "error", "unavailable", "not-configured"}
        )
        result = {}
        for raw in snapshot.get("rules") or []:
            if not isinstance(raw, Mapping):
                continue
            try:
                rule = GraphInferenceRule.from_dict(dict(raw))
            except ValueError:
                continue
            result[rule.rule_id] = {
                "ruleId": rule.rule_id,
                "label": rule.label,
                "policy": rule.resolved_hypothesis_lifecycle().to_dict(),
                "editable": editable,
            }
        return result

    def workspace(
        self,
        account_id: str = "",
        symbol: str = "",
        market_id: str = "",
        scope: str = "",
        limit: int = 100,
        event_limit: int = 100,
    ) -> Dict[str, object]:
        records = self.current_records(account_id, symbol, market_id, scope, limit)
        events = self.lifecycle_events(account_id, symbol, market_id, scope, event_limit)
        policy_by_rule = self.rule_policy_map()
        market_cache: Dict[str, List[Dict[str, object]]] = {}
        account_cache: Dict[str, List[Dict[str, object]]] = {}
        items = []
        for record in records:
            item_symbol = upper(record.get("symbol"))
            if item_symbol not in market_cache:
                market_cache[item_symbol] = self.episodes(symbol=item_symbol)
            account_key = text(record.get("accountId") or account_id) + "|" + item_symbol
            if account_key not in account_cache:
                account_cache[account_key] = self.episodes(text(record.get("accountId") or account_id), item_symbol)
            assessment = self.assessment_for_record(
                record,
                text(record.get("accountId") or account_id),
                market_cache[item_symbol],
                account_cache[account_key],
            )
            item = lifecycle_review_item(record, assessment)
            item["transitions"] = [
                event for event in events
                if text(event.get("lifecycleKey")) == text(record.get("lifecycleKey"))
            ][:8]
            item["editablePolicies"] = [
                policy_by_rule[rule_id]
                for rule_id in item.get("sourceRuleIds") or []
                if rule_id in policy_by_rule
            ]
            items.append(item)
        state_counts: Dict[str, int] = {}
        outcome_counts: Dict[str, int] = {}
        for item in items:
            state = text(item.get("state")) or "observed"
            state_counts[state] = int(state_counts.get(state, 0)) + 1
            outcome = item.get("outcomeAssessment") if isinstance(item.get("outcomeAssessment"), Mapping) else {}
            outcome_state = text(outcome.get("outcomeState")) or "insufficient-sample"
            outcome_counts[outcome_state] = int(outcome_counts.get(outcome_state, 0)) + 1
        return {
            "version": HYPOTHESIS_DECISION_BRIEF_VERSION,
            "status": "ok" if self.hypothesis_lifecycle_store else "unavailable",
            "source": "typedb-hypothesis-lifecycle+decision-episode-outcome",
            "decisionEligibility": "review-only-not-action-selector",
            "automaticDeployment": False,
            "accountId": account_id,
            "symbol": upper(symbol),
            "marketId": market_id,
            "scope": scope,
            "count": len(items),
            "eventCount": len(events),
            "summary": {
                "stateCounts": state_counts,
                "outcomeStateCounts": outcome_counts,
                "materialChangeCount": sum(1 for item in items if item.get("materialChange")),
                "activeCount": sum(1 for item in items if item.get("state") not in {"invalidated", "expired"}),
            },
            "items": items,
            "events": events,
        }
