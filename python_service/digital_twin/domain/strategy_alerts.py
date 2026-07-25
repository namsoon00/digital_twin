from typing import Dict, List

from .message_types import WATCHLIST_ONTOLOGY_SIGNAL
from .ontology_inference_context import relation_contexts_from_snapshot
from .ontology_insights import relation_news_event_key_suffix
from .ontology_decision_state import (
    CHANGE_STATE_LABELS,
    DATA_STATE_LABELS,
    REVIEW_LEVEL_LABELS,
    data_state_is_usable,
)
from .portfolio import AccountSnapshot, AlertEvent


class StrategyAlertMixin:
    def watchlist_ontology_signal_type(self, relation_context: Dict[str, object]) -> str:
        decision = relation_context.get("decision") if isinstance(relation_context, dict) else {}
        plan = relation_context.get("executionPlan") if isinstance(relation_context, dict) else {}
        active_rules = relation_context.get("activeRules") if isinstance(relation_context, dict) else []
        category_sources = [
            plan if isinstance(plan, dict) else {},
            decision if isinstance(decision, dict) else {},
            *[item for item in active_rules or [] if isinstance(item, dict)],
        ]
        allowed = {"entryCandidate", "riskWatch", "trendReview", "dataQuality", "relationshipChange"}
        for source in category_sources:
            category = str(
                source.get("notificationCategory")
                or source.get("notification_category")
                or ""
            ).strip()
            if category in allowed:
                return category
        return "relationshipChange"

    def watchlist_ontology_action_line(self, signal_type: str, decision_label: str) -> str:
        del signal_type
        return "권장 액션: " + (str(decision_label or "TypeDB 관계 결과") + "의 다음 확인 기준을 검토")

    def watchlist_ontology_event(
        self,
        snapshot: AccountSnapshot,
        position,
        position_context: Dict[str, object],
        relation_context: Dict[str, object],
    ):
        active_rules = [
            item for item in (relation_context.get("activeRules") if isinstance(relation_context, dict) else []) or []
            if isinstance(item, dict)
        ]
        if not active_rules:
            return None
        decision = relation_context.get("decision") if isinstance(relation_context, dict) else {}
        if not isinstance(decision, dict):
            decision = {}
        review_level = str(relation_context.get("reviewLevel") or decision.get("reviewLevel") or "normal")
        data_state = str(relation_context.get("dataState") or decision.get("dataState") or "partial")
        change_state = str(relation_context.get("changeState") or decision.get("changeState") or "unchanged")
        conflict_state = str(relation_context.get("conflictState") or decision.get("conflictState") or "context-only")
        if not data_state_is_usable(data_state):
            return None
        signal_type = self.watchlist_ontology_signal_type(relation_context)
        decision_label = str(decision.get("label") or "관심종목 관계 신호")
        active_labels = [
            str(item.get("label") or item.get("ruleId") or item.get("rule_id") or "").strip()
            for item in active_rules
            if str(item.get("label") or item.get("ruleId") or item.get("rule_id") or "").strip()
        ]
        active_rule_ids = sorted(
            str(item.get("ruleId") or item.get("rule_id") or "").strip()
            for item in active_rules
            if str(item.get("ruleId") or item.get("rule_id") or "").strip()
        )
        rule_signature = "+".join(active_rule_ids[:4]) or "relationship"
        news_event_suffix = relation_news_event_key_suffix(relation_context)
        if news_event_suffix:
            rule_signature = rule_signature + "+" + news_event_suffix
        plan = relation_context.get("executionPlan") if isinstance(relation_context.get("executionPlan"), dict) else {}
        requested_severity = str(
            plan.get("notificationSeverity")
            or decision.get("notificationSeverity")
            or ""
        ).upper()
        # Delivery eligibility and urgency are authored RuleBox facts.  A
        # relation without this materialized instruction is not actionable.
        if requested_severity not in {"ALERT", "WATCH"}:
            return None
        severity = requested_severity
        symbol = position.symbol.upper()
        lines = [
            "관심종목 온톨로지 관계 신호",
            "상태: " + decision_label + " · " + REVIEW_LEVEL_LABELS.get(review_level, REVIEW_LEVEL_LABELS["observe"]),
            "자료: " + DATA_STATE_LABELS.get(data_state, DATA_STATE_LABELS["partial"]),
            "변화: " + CHANGE_STATE_LABELS.get(change_state, CHANGE_STATE_LABELS["unchanged"]),
            self.current_price_line(position_context),
            self.flow_context_line(position_context),
            self.trend_context_line(position_context),
            self.watchlist_ontology_action_line(signal_type, decision_label),
            "근거 신호: " + " · ".join(active_labels[:4]) if active_labels else "",
        ]
        return AlertEvent(
            snapshot.account_id,
            snapshot.account_label,
            severity,
            WATCHLIST_ONTOLOGY_SIGNAL,
            ":".join([snapshot.account_id, "watchlist-ontology", symbol, signal_type, rule_signature, change_state]),
            position.name,
            [line for line in lines if line],
            symbol,
            criteria=self.criteria(
                "관심종목 온톨로지 관계 그래프에서 진입·회복·리스크 규칙이 성립할 때",
                decision_label
                + " · " + REVIEW_LEVEL_LABELS.get(review_level, REVIEW_LEVEL_LABELS["observe"])
                + " · " + CHANGE_STATE_LABELS.get(change_state, CHANGE_STATE_LABELS["unchanged"]),
            ),
            metadata={
                "watchlistOntologySignalType": signal_type,
                "watchlistActiveRelationRules": active_rule_ids,
                "reviewLevel": review_level,
                "dataState": data_state,
                "changeState": change_state,
                "conflictState": conflict_state,
                "ontologyRelationContext": relation_context,
                "ontologyPromptContext": relation_context.get("promptContext") if isinstance(relation_context, dict) else {},
            },
        )

    def relation_decision(self, relation_context: Dict[str, object]) -> Dict[str, object]:
        decision = relation_context.get("decision") if isinstance(relation_context, dict) else {}
        return decision if isinstance(decision, dict) else {}

    def ontology_signal_events(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        holding_symbols = {item.symbol.upper() for item in snapshot.positions if item.symbol and not item.is_cash()}
        watchlist_items = [item for item in snapshot.watchlist if item.symbol and item.symbol.upper() not in holding_symbols]
        inference_contexts = relation_contexts_from_snapshot(
            snapshot,
            getattr(self.strategy_model, "settings", {}) if self.strategy_model else {},
        )
        for position in watchlist_items:
            if not (position.current_price or position.market_value or position.volume or position.trade_strength):
                continue
            symbol = position.symbol.upper()
            position_context = position.to_dict()
            relation_context = inference_contexts.get(symbol)
            if not relation_context:
                continue
            ontology_event = self.watchlist_ontology_event(snapshot, position, position_context, relation_context)
            if ontology_event:
                events.append(ontology_event)
        return events

    def model_state_events(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        return self.ontology_signal_events(snapshot)
