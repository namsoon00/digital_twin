"""V2 candidate construction from verified TypeDB relation contexts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, Mapping

from ...domain.context_observation_notifications import typedb_context_observation_contract
from ...domain.data_freshness import freshness_from_snapshot_subject
from ...domain.independent_reasoning import IndependentReasoningRequest
from ...domain.investment_reasoning import decision_synthesis_from_relation_context
from ...domain.message_types import (
    CRYPTO_ONTOLOGY_SIGNAL,
    DEFAULT_CADENCE,
    HOLDING_TIMING,
    INVESTMENT_INSIGHT,
    MIN_CADENCE_MINUTES,
    PORTFOLIO_ONTOLOGY_SIGNAL,
    WATCHLIST_ONTOLOGY_SIGNAL,
)
from ...domain.ontology_inference_context import (
    portfolio_relation_context_from_snapshot,
    relation_contexts_from_snapshot,
)
from ...domain.ontology_insights import build_investment_insight_events
from ...domain.parsing import parse_assignments
from ...domain.portfolio import AlertEvent


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _float_text(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return ("%.2f" % number).rstrip("0").rstrip(".")


class V2NotificationCadence:
    """Delivery-only cooldown; it never scores or changes an investment action."""

    def __init__(
        self,
        settings: Mapping[str, object],
        monitor_store,
        delivery_history_store=None,
    ):
        self.settings = dict(settings or {})
        self.monitor_store = monitor_store
        self.delivery_history_store = delivery_history_store
        self.cadence = parse_assignments(
            _text(self.settings.get("alertCadenceMinutes")),
            DEFAULT_CADENCE,
        )

    def minutes(self, event: AlertEvent) -> int:
        if event.rule == INVESTMENT_INSIGHT and _text(self.settings.get("notificationCooldownMinutes")):
            raw = self.settings.get("notificationCooldownMinutes")
        else:
            raw = self.cadence.get(event.rule, DEFAULT_CADENCE.get(event.rule, MIN_CADENCE_MINUTES))
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            value = MIN_CADENCE_MINUTES
        return max(MIN_CADENCE_MINUTES, value)

    def delivered_at_by_cadence_key(self, events: Iterable[AlertEvent]):
        candidates = list(events or [])
        provider = getattr(
            self.delivery_history_store,
            "delivered_cadence_timestamps",
            None,
        )
        if callable(provider):
            try:
                return dict(provider([event.cadence_key() for event in candidates]) or {})
            except Exception:  # noqa: BLE001 - retain the conservative legacy gate on storage failure.
                pass
        return dict(getattr(self.monitor_store, "sent", {}) or {})

    def ready(self, events: Iterable[AlertEvent], force: bool = False):
        candidates = list(events or [])
        if force:
            return candidates
        sent = self.delivered_at_by_cadence_key(candidates)
        now = datetime.now(timezone.utc).timestamp()
        ready = []
        for event in candidates:
            sent_at = sent.get(event.cadence_key())
            if not sent_at:
                ready.append(event)
                continue
            try:
                previous = datetime.fromisoformat(_text(sent_at).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                ready.append(event)
                continue
            if now - previous >= self.minutes(event) * 60:
                ready.append(event)
        return ready


class V2GraphDecisionCandidateBuilder:
    """Package TypeDB output for AI without invoking V1 monitoring logic."""

    def __init__(
        self,
        settings: Mapping[str, object],
        monitor_store,
        delivery_history_store=None,
    ):
        self.settings = dict(settings or {})
        self.cadence = V2NotificationCadence(
            self.settings,
            monitor_store,
            delivery_history_store=delivery_history_store,
        )

    @staticmethod
    def _requested(request: IndependentReasoningRequest, symbol: str) -> bool:
        return not request.symbols or _text(symbol).upper() in set(request.symbols)

    @staticmethod
    def _signal_rule(relation: Mapping[str, object]) -> str:
        subject = _mapping(relation.get("subject"))
        facts = _mapping(relation.get("facts"))
        market = _text(subject.get("market") or facts.get("market")).upper()
        source_kind = _text(
            next((
                item.get("ruleSourceKind")
                for item in relation.get("activeRules") or relation.get("matchedRules") or []
                if isinstance(item, Mapping) and item.get("ruleSourceKind")
            ), "")
        ).lower()
        if market in {"CRYPTO", "COIN"} or source_kind == "crypto-asset":
            return CRYPTO_ONTOLOGY_SIGNAL
        source = _text(facts.get("source") or relation.get("targetRole")).lower()
        return WATCHLIST_ONTOLOGY_SIGNAL if source == "watchlist" or facts.get("isWatchlist") else HOLDING_TIMING

    @staticmethod
    def _notification_severity(relation: Mapping[str, object]) -> str:
        decision = _mapping(relation.get("decision"))
        plan = _mapping(relation.get("executionPlan"))
        severity = _text(
            plan.get("notificationSeverity")
            or decision.get("notificationSeverity")
        ).upper()
        return severity if severity in {"ALERT", "WATCH"} else ""

    def _base_event(self, snapshot, relation, synthesis):
        subject = _mapping(relation.get("subject"))
        facts = _mapping(relation.get("facts"))
        decision = _mapping(relation.get("decision"))
        rule = V2GraphDecisionCandidateBuilder._signal_rule(relation)
        severity = V2GraphDecisionCandidateBuilder._notification_severity(relation)
        if not severity:
            return None
        context_observation = typedb_context_observation_contract(relation)
        if not context_observation and not synthesis.eligible_hypothesis_ids:
            return None
        symbol = synthesis.symbol
        name = _text(subject.get("name")) or symbol
        action = "NO_ACTION" if context_observation else synthesis.graph_candidate_action or "NO_ACTION"
        label = _text(decision.get("label")) or action
        if context_observation:
            lines = [
                "TypeDB 시장 관찰: " + label,
                "투자 행동: 매수·매도 판단이 아닌 참고 정보",
            ]
        else:
            lines = [
                "TypeDB 판단 후보: " + label,
                "행동 대안: " + ", ".join(
                    alternative.action
                    for alternative in synthesis.alternatives
                    if alternative.decision_eligible
                ),
            ]
        current_price = _float_text(facts.get("currentPrice"))
        if current_price:
            lines.append("현재가: " + current_price)
        price_change = _float_text(facts.get("priceChangeRate"))
        if context_observation and price_change:
            lines.append("24시간 변동: " + price_change + "%")
        provider = _text(facts.get("quoteSource"))
        if context_observation and provider:
            lines.append("데이터 출처: " + provider)
        profit_loss = _float_text(facts.get("profitLossRate"))
        if profit_loss and not context_observation:
            lines.append("수익률: " + profit_loss + "%")
        if synthesis.missing_data:
            lines.append("판단 한계: " + ", ".join(synthesis.missing_data[:4]))
        metadata = {
            "reviewLevel": synthesis.review_level,
            "dataState": synthesis.data_state,
            "changeState": synthesis.change_state,
            "conflictState": synthesis.conflict_state,
            "validationState": (
                "reference-only"
                if context_observation
                else "blocked" if synthesis.judgement_blocked else "conditional"
            ),
            "sourceAboxSnapshotId": synthesis.source_abox_snapshot_id,
            "inferenceGenerationId": synthesis.inference_generation_id,
            "selectedRuleId": synthesis.selected_rule_id,
            "reasoningEngineVersion": _text(self.settings.get("_reasoningEngineVersion")) or "v2",
            "reasoningEngineDeploymentId": _text(self.settings.get("_reasoningEngineDeploymentId")),
            "v2DecisionSynthesis": synthesis.to_dict(),
            "ontologyRelationContext": dict(relation),
            "ontologyPromptContext": _mapping(relation.get("promptContext")),
            "dataFreshness": freshness_from_snapshot_subject(
                snapshot,
                symbol,
                rule,
                settings=self.settings,
            ),
            "dataFreshnessRequired": True,
            "reasoningSourceObservedAt": _text(getattr(snapshot, "generated_at", "")),
        }
        if context_observation:
            metadata.update({
                "contextObservationDecision": context_observation,
                "notificationDecisionMode": context_observation["decisionMode"],
                "requiresAiJudgement": False,
            })
        return AlertEvent(
            snapshot.account_id,
            snapshot.account_label,
            severity,
            rule,
            ":".join([
                snapshot.account_id,
                "v2-typedb-decision",
                symbol,
                synthesis.inference_generation_id or synthesis.synthesis_id,
            ]),
            name,
            [line for line in lines if line and not line.endswith(": ")],
            symbol,
            criteria=(
                [
                    "설정: TypeDB 참고 관찰 규칙의 시장 상태가 변경될 때",
                    "감지: " + label + " · 투자 행동 판단 없음",
                ]
                if context_observation
                else [
                    "설정: TypeDB 규칙이 행동 대안과 근거 가설을 생성할 때",
                    "감지: " + label + " · 검증 가능 가설 " + str(len(synthesis.eligible_hypothesis_ids)) + "개",
                ]
            ),
            metadata=metadata,
        )

    def build(self, request, snapshots, previous_by_account, projection_results, force=False):
        del previous_by_account
        base_events = []
        syntheses = []
        hypothesis_candidates = []
        for snapshot in snapshots or []:
            account_id = _text(getattr(snapshot, "account_id", ""))
            projection = _mapping(projection_results.get(account_id))
            inference = _mapping(projection.get("inferenceBox"))
            if not (
                inference.get("generationAligned")
                and inference.get("sourceAboxSnapshotId")
                and (
                    inference.get("nativeTypeDbReasoningCompleted")
                    or inference.get("typedbNativeRuleEvaluationCompleted")
                )
            ):
                continue
            contexts = relation_contexts_from_snapshot(
                snapshot,
                self.settings,
                include_crypto_market_subjects=True,
            )
            for symbol, relation in contexts.items():
                if not self._requested(request, symbol):
                    continue
                synthesis = decision_synthesis_from_relation_context(account_id, relation)
                syntheses.append(synthesis)
                hypothesis_candidates.append({
                    "context": {"ontologyRelationContext": dict(relation)},
                })
                event = self._base_event(snapshot, relation, synthesis)
                if event is not None:
                    base_events.append(event)

            portfolio_scope = bool(
                "PORTFOLIO" in set(request.context.get("subjectKinds") or [])
                and (not request.account_ids or account_id in request.account_ids)
            )
            if portfolio_scope:
                relation = portfolio_relation_context_from_snapshot(snapshot)
                if relation:
                    synthesis = decision_synthesis_from_relation_context(account_id, relation)
                    syntheses.append(synthesis)
                    hypothesis_candidates.append({
                        "context": {"ontologyRelationContext": dict(relation)},
                    })
                    event = self._base_event(snapshot, relation, synthesis)
                    if event is not None:
                        event.rule = PORTFOLIO_ONTOLOGY_SIGNAL
                        base_events.append(event)

        detected = build_investment_insight_events_by_snapshot(snapshots, base_events)
        delivery_ready = self.cadence.ready(detected, force=force)
        delivery_ready_keys = {event.key for event in delivery_ready}
        for event in detected:
            metadata = dict(getattr(event, "metadata", {}) or {})
            metadata["preDecisionDeliveryCadence"] = {
                "version": "pre-decision-delivery-cadence-v1",
                "eligible": event.key in delivery_ready_keys,
                "minutes": self.cadence.minutes(event),
                "decisionBoundary": "delivery-only",
            }
            event.metadata = metadata
        return {
            "detected": detected,
            # Every material TypeDB candidate is judgment-ready. Delivery
            # cadence is evaluated after the immutable decision is persisted.
            "judgmentReady": detected,
            "deliveryReady": delivery_ready,
            "ready": detected,
            "syntheses": syntheses,
            # Hypothesis capture is an audit boundary, not a notification
            # boundary. A quiet relation context must still become a durable
            # subject decision case even when no user alert is warranted.
            "hypothesisCandidates": hypothesis_candidates,
        }


def build_investment_insight_events_by_snapshot(snapshots, events):
    """Preserve account ownership when packaging graph signals as insights."""

    by_account = {}
    for event in events or []:
        by_account.setdefault(event.account_id, []).append(event)
    insights = []
    for snapshot in snapshots or []:
        account_insights = build_investment_insight_events(
            snapshot,
            by_account.get(_text(getattr(snapshot, "account_id", ""))) or [],
        )
        generated_at = _text(getattr(snapshot, "generated_at", ""))
        for insight in account_insights:
            if generated_at:
                insight.generated_at = generated_at
            insight.metadata = dict(insight.metadata or {})
            insight.metadata.setdefault("reasoningSourceObservedAt", generated_at)
        insights.extend(account_insights)
    return insights
