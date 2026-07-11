from typing import Dict, List

from .message_types import WATCHLIST_ONTOLOGY_SIGNAL
from .ontology_inference_context import relation_contexts_from_snapshot
from .ontology_insights import relation_news_event_key_suffix
from .portfolio import AccountSnapshot, AlertEvent


class StrategyAlertMixin:
    def watchlist_ontology_signal_type(self, relation_context: Dict[str, object]) -> str:
        decision = relation_context.get("decision") if isinstance(relation_context, dict) else {}
        active_rules = relation_context.get("activeRules") if isinstance(relation_context, dict) else []
        action_group = str(decision.get("actionGroup") or "") if isinstance(decision, dict) else ""
        rule_ids = {
            str(item.get("ruleId") or item.get("rule_id") or "")
            for item in active_rules or []
            if isinstance(item, dict)
        }
        if "entry.pullback.supported.v1" in rule_ids or action_group == "entry":
            return "entryCandidate"
        if any(rule_id in rule_ids for rule_id in [
            "trend.breakdown_acceleration.v1",
            "entry.add_buy.blocked.v1",
            "disclosure.material_event.v1",
            "news.direct_risk.new_material.v1",
            "news.direct_risk.price_confirmed.v1",
        ]) or action_group in {"lossControl", "entryRisk", "disclosure", "cryptoSensitivity"}:
            return "riskWatch"
        if any(rule_id in rule_ids for rule_id in [
            "trend.support_retest.v1",
            "trend.recovery_attempt.v1",
            "holding.trend_flow.confirmation.v1",
            "news.direct_support.new_material.v1",
            "news.direct_support.price_confirmed.v1",
            "news.direct_material.new.v1",
        ]) or action_group in {"trendReview", "recovery", "flowTrend"}:
            return "trendReview"
        if "data.conflict.v1" in rule_ids or relation_context.get("missingData"):
            return "dataQuality"
        return "relationshipChange"

    def watchlist_ontology_action_line(self, signal_type: str, decision_label: str) -> str:
        if signal_type == "entryCandidate":
            return "권장 액션: 첫 진입은 작게 검토하고 손절 기준, 거래량, 20일선 유지 여부를 먼저 확인"
        if signal_type == "riskWatch":
            return "권장 액션: 신규 진입 보류, 하락 가속·공시·외부 리스크가 완화되는지 우선 확인"
        if signal_type == "trendReview":
            return "권장 액션: 추격 진입보다 다음 조회에서도 회복·지지 관계가 유지되는지 확인"
        if signal_type == "dataQuality":
            return "권장 액션: 투자 판단 전 현재가, 이동평균, 수급 데이터 연결 상태를 먼저 복구"
        return "권장 액션: 새 관계가 다음 데이터 업데이트에서도 유지되는지 확인"

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
        relation_score = float(decision.get("score") or relation_context.get("signalStrength") or 0)
        if relation_score < 55:
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
        severity = "ALERT" if signal_type == "riskWatch" and (relation_score >= 75 or decision.get("tone") == "danger") else "WATCH"
        symbol = position.symbol.upper()
        lines = [
            "관심종목 온톨로지 관계 신호",
            "상태: " + decision_label + " (" + ("%.1f" % relation_score) + "점)",
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
            ":".join([snapshot.account_id, "watchlist-ontology", symbol, signal_type, rule_signature, str(round(relation_score, 1))]),
            position.name,
            [line for line in lines if line],
            symbol,
            criteria=self.criteria(
                "관심종목 온톨로지 관계 그래프에서 진입·회복·리스크 규칙이 성립할 때",
                decision_label + " · 관계 강도 " + ("%.1f" % relation_score) + "점",
            ),
            metadata={
                "watchlistOntologySignalType": signal_type,
                "watchlistSignalScore": round(relation_score, 1),
                "watchlistActiveRelationRules": active_rule_ids,
                "relationRuleScore": round(relation_score, 1),
                "ontologyRelationContext": relation_context,
                "ontologyPromptContext": relation_context.get("promptContext") if isinstance(relation_context, dict) else {},
            },
        )

    def relation_decision(self, relation_context: Dict[str, object]) -> Dict[str, object]:
        decision = relation_context.get("decision") if isinstance(relation_context, dict) else {}
        return decision if isinstance(decision, dict) else {}

    def relation_score(self, relation_context: Dict[str, object]) -> float:
        decision = self.relation_decision(relation_context)
        return float(decision.get("score") or relation_context.get("signalStrength") or 0)

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

    def model_score_events(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        return self.ontology_signal_events(snapshot)
