from dataclasses import replace
from typing import Dict, List

from .message_types import WATCHLIST_ONTOLOGY_SIGNAL
from .ontology_relation_rules import evaluate_position_relation_rules
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
        ]) or action_group in {"lossControl", "entryRisk", "disclosure", "cryptoSensitivity"}:
            return "riskWatch"
        if any(rule_id in rule_ids for rule_id in [
            "trend.support_retest.v1",
            "trend.recovery_attempt.v1",
            "holding.trend_flow.confirmation.v1",
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

    def relation_score_phrase(self, relation_context: Dict[str, object], fallback: str = "관계 규칙 신호") -> str:
        decision = self.relation_decision(relation_context)
        label = str(decision.get("label") or fallback).strip()
        return label + " (" + ("%.1f" % self.relation_score(relation_context)) + "점)"

    def relation_metadata(self, relation_context: Dict[str, object], score_key: str = "") -> Dict[str, object]:
        score = round(self.relation_score(relation_context), 1)
        metadata = {
            "relationRuleScore": score,
            "ontologyRelationContext": relation_context,
            "ontologyPromptContext": relation_context.get("promptContext") if isinstance(relation_context, dict) else {},
        }
        if score_key:
            metadata[score_key] = score
        return metadata

    def relation_sell_action_group(self, action_group: str) -> bool:
        return action_group in {
            "profitTake",
            "lossControl",
            "rebalance",
            "distributionRisk",
            "executionRisk",
            "eventRisk",
            "cryptoSensitivity",
            "factorRisk",
            "entryRisk",
        }

    def model_score_events(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        buy_threshold = float(self.thresholds.get("modelBuyScore", 0))
        watchlist_buy_threshold = float(self.thresholds.get("watchlistBuyScore", buy_threshold) or buy_threshold)
        sell_threshold = float(self.thresholds.get("modelSellScore", 0))
        holding_symbols = {item.symbol.upper() for item in snapshot.positions if item.symbol and not item.is_cash()}
        watchlist_items = [item for item in snapshot.watchlist if item.symbol and item.symbol.upper() not in holding_symbols]
        items = [(item, "보유") for item in snapshot.positions if item.symbol and not item.is_cash()] + [(item, "관심") for item in watchlist_items]
        for position, source in items:
            if not (position.current_price or position.market_value or position.volume or position.trade_strength):
                continue
            symbol = position.symbol.upper()
            position_context = position.to_dict()
            relation_position = replace(position, source="watchlist" if source == "관심" else "holding")
            relation_context = evaluate_position_relation_rules(
                relation_position,
                snapshot.portfolio,
                external_signals=snapshot.external_signals,
                settings=getattr(self.strategy_model, "settings", {}) if self.strategy_model else {},
            )
            current_price = self.current_price_line(position_context)
            price_lines = self.holding_price_lines(position_context) if source == "보유" else [current_price] if current_price else []
            common_lines = [
                source + " 종목",
                *price_lines,
                self.flow_context_line(position_context),
                self.trend_context_line(position_context),
            ]
            relation_decision = self.relation_decision(relation_context)
            relation_score = self.relation_score(relation_context)
            relation_action_group = str(relation_decision.get("actionGroup") or "")
            relation_entry_candidate = (
                source == "관심"
                and watchlist_buy_threshold
                and relation_action_group == "entry"
                and relation_score >= watchlist_buy_threshold
            )
            if source == "관심":
                ontology_event = self.watchlist_ontology_event(snapshot, position, position_context, relation_context)
                if ontology_event:
                    events.append(ontology_event)
            if source == "관심" and relation_entry_candidate:
                buy_phrase = self.relation_score_phrase(relation_context, "분할매수 후보")
                detected = buy_phrase
                events.append(AlertEvent(
                    snapshot.account_id,
                    snapshot.account_label,
                    "WATCH",
                    "watchlistBuyCandidate",
                    ":".join([snapshot.account_id, "watchlist-buy", symbol, str(round(relation_score, 1))]),
                    position.name,
                    ["관심종목 매수 후보 " + buy_phrase, *common_lines],
                    symbol,
                    criteria=self.criteria(
                        "관심종목 관계 규칙 점수 " + self.threshold_text("watchlistBuyScore", "점") + " 이상이며 진입 관계가 성립할 때",
                        detected,
                    ),
                    metadata=self.relation_metadata(relation_context, "watchlistBuyScore"),
                ))
            if source != "관심" and buy_threshold and relation_action_group == "entry" and relation_score >= buy_threshold:
                buy_phrase = self.relation_score_phrase(relation_context, "매수 후보")
                events.append(AlertEvent(
                    snapshot.account_id,
                    snapshot.account_label,
                    "WATCH",
                    "modelBuy",
                    ":".join([snapshot.account_id, "model-buy", symbol, str(round(relation_score, 1))]),
                    position.name,
                    ["매수 판단 " + buy_phrase, *common_lines],
                    symbol,
                    criteria=self.criteria(
                        "보유 종목 관계 규칙 점수 " + self.threshold_text("modelBuyScore", "점") + " 이상이며 진입 관계가 성립할 때",
                        buy_phrase,
                    ),
                    metadata=self.relation_metadata(relation_context, "modelBuyScore"),
                ))
            if source != "관심" and sell_threshold and self.relation_sell_action_group(relation_action_group) and relation_score >= sell_threshold:
                sell_phrase = self.relation_score_phrase(relation_context, "매도/축소 점검")
                events.append(AlertEvent(
                    snapshot.account_id,
                    snapshot.account_label,
                    "ALERT",
                    "modelSell",
                    ":".join([snapshot.account_id, "model-sell", symbol, str(round(relation_score, 1))]),
                    position.name,
                    [
                        "매도 판단 " + sell_phrase,
                        *common_lines,
                    ],
                    symbol,
                    criteria=self.criteria(
                        "보유 종목 관계 규칙 점수 " + self.threshold_text("modelSellScore", "점") + " 이상이며 매도·축소·리스크 관계가 성립할 때",
                        sell_phrase,
                    ),
                    metadata=self.relation_metadata(relation_context, "modelSellScore"),
                ))
        return events

    def model_sample_events(self, snapshot: AccountSnapshot, rule: str) -> List[AlertEvent]:
        candidates = [item for item in snapshot.watchlist + snapshot.positions if not item.is_cash() and item.symbol]
        if not candidates:
            return []
        holding_symbols = {item.symbol.upper() for item in snapshot.positions if item.symbol and not item.is_cash()}
        holding_candidates = [item for item in snapshot.positions if item.symbol and not item.is_cash()]
        watchlist_candidates = [item for item in snapshot.watchlist if item.symbol and item.symbol.upper() not in holding_symbols]
        if rule == "watchlistBuyCandidate":
            position = watchlist_candidates[0] if watchlist_candidates else candidates[0]
        else:
            position = holding_candidates[0] if holding_candidates else candidates[0]
        position_context = position.to_dict()
        current_price = self.current_price_line(position_context)
        price_lines = self.holding_price_lines(position_context) if position.symbol.upper() in holding_symbols else [current_price] if current_price else []
        relation_position = replace(position, source="watchlist" if position.symbol.upper() not in holding_symbols else "holding")
        relation_context = evaluate_position_relation_rules(
            relation_position,
            snapshot.portfolio,
            external_signals=snapshot.external_signals,
            settings=getattr(self.strategy_model, "settings", {}) if self.strategy_model else {},
        )
        symbol = position.symbol.upper()
        if rule == "modelSell":
            sell_phrase = self.relation_score_phrase(relation_context, "매도/축소 점검")
            return [AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "ALERT",
                "modelSell",
                ":".join([snapshot.account_id, "model-sell-test", symbol]),
                position.name,
                [
                    "매도 판단 " + sell_phrase,
                    "현재 데이터 기준 템플릿 테스트",
                    *price_lines,
                    self.flow_context_line(position_context),
                    self.trend_context_line(position_context),
                ],
                symbol,
                criteria=self.criteria(
                    "관계 규칙에서 매도·축소·리스크 관계가 성립할 때",
                    sell_phrase,
                ),
                metadata=self.relation_metadata(relation_context, "modelSellScore"),
            )]
        buy_phrase = self.relation_score_phrase(relation_context, "매수 후보")
        if rule == "watchlistBuyCandidate":
            return [AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "WATCH",
                "watchlistBuyCandidate",
                ":".join([snapshot.account_id, "watchlist-buy-test", symbol]),
                position.name,
                [
                    "관심종목 매수 후보 " + buy_phrase,
                    "현재 데이터 기준 템플릿 테스트",
                    *price_lines,
                    self.flow_context_line(position_context),
                    self.trend_context_line(position_context),
                ],
                symbol,
                criteria=self.criteria(
                    "관심종목 관계 규칙에서 진입 관계가 성립할 때",
                    buy_phrase,
                ),
                metadata=self.relation_metadata(relation_context, "watchlistBuyScore"),
            )]
        return [AlertEvent(
            snapshot.account_id,
            snapshot.account_label,
            "WATCH",
            "modelBuy",
            ":".join([snapshot.account_id, "model-buy-test", symbol]),
            position.name,
            [
                "매수 판단 " + buy_phrase,
                "현재 데이터 기준 템플릿 테스트",
                *price_lines,
                self.flow_context_line(position_context),
                self.trend_context_line(position_context),
            ],
            symbol,
            criteria=self.criteria(
                "관계 규칙에서 진입 관계가 성립할 때",
                buy_phrase,
            ),
            metadata=self.relation_metadata(relation_context, "modelBuyScore"),
        )]
