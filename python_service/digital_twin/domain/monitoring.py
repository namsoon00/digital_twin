from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, List

from .alert_formatting import compact_number, money, pct_delta, price_money, signed_number, signed_pct
from .data_freshness import data_freshness_required, freshness_from_position, freshness_record
from .market_data import number
from .message_types import (
    DEFAULT_ALERT_RULES,
    DEFAULT_ALERT_THRESHOLDS,
    DEFAULT_CADENCE,
    INVESTMENT_INSIGHT,
    MIN_CADENCE_MINUTES,
    WATCHLIST_ONTOLOGY_SIGNAL,
)
from .model_review import decision_change_context, decision_change_review_lines
from .ontology_insights import build_investment_insight_events, split_operational_and_investment_events
from .ontology_rules import decision_action_group_for_label, relation_rule_context_summary_lines, relation_thresholds_from_settings
from .parsing import parse_assignments
from .portfolio import AccountSnapshot, AlertEvent, Position, monitor_state_has_live_account_data, status_has_account_data_failure
from .portfolio_calculations import DEFAULT_FX_RATES, value_in_base
from .repositories import MonitorStateRepository
from .strategy import StrategyModel, decisions_for_positions
from .strategy_alerts import StrategyAlertMixin
from .external_signal_alerts import ExternalSignalAlertMixin


DEFAULT_THRESHOLDS = DEFAULT_ALERT_THRESHOLDS


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class RealtimeMonitor(StrategyAlertMixin, ExternalSignalAlertMixin):
    def __init__(self, settings: Dict[str, str] = None):
        settings = settings or {}
        self.settings = dict(settings)
        self.rules = parse_assignments(settings.get("alertRules", ""), DEFAULT_ALERT_RULES)
        self.thresholds = parse_assignments(settings.get("alertThresholds", ""), DEFAULT_THRESHOLDS)
        self.relation_thresholds = relation_thresholds_from_settings(settings)
        self.cadence = parse_assignments(settings.get("alertCadenceMinutes", ""), DEFAULT_CADENCE)
        self.fx_rates = {
            str(key).upper(): float(value or 0)
            for key, value in parse_assignments(settings.get("fxRates", ""), DEFAULT_FX_RATES).items()
        }
        self.strategy_model = StrategyModel(settings)

    def enabled(self, rule: str) -> bool:
        return self.rules.get(rule, 1) != 0

    def rule_cadence_minutes(self, rule: str) -> int:
        value = int(self.cadence.get(rule, DEFAULT_CADENCE.get(rule, MIN_CADENCE_MINUTES)) or 0)
        return max(MIN_CADENCE_MINUTES, value)

    def dispatch_cadence_minutes(self, event: AlertEvent) -> int:
        if event.rule == INVESTMENT_INSIGHT:
            raw = self.settings.get("notificationCooldownMinutes")
            if str(raw or "").strip():
                try:
                    value = int(float(str(raw).strip()))
                except ValueError:
                    value = self.rule_cadence_minutes(event.rule)
                return max(MIN_CADENCE_MINUTES, value)
        return self.rule_cadence_minutes(event.rule)

    def dispatch_cadence_key(self, event: AlertEvent) -> str:
        if event.rule == INVESTMENT_INSIGHT:
            insight = event.metadata.get("ontologyInsight") if isinstance(event.metadata, dict) else {}
            if isinstance(insight, dict) and str(insight.get("cadenceKey") or "").strip():
                return str(insight.get("cadenceKey"))
            if str(event.key or "").strip():
                return ":".join(["cadence", "python", event.account_id, event.rule, event.key])
        return event.cadence_key()

    def criteria(self, setting: str, detected: str = "") -> List[str]:
        lines = []
        if str(setting or "").strip():
            lines.append("설정: " + str(setting).strip())
        if str(detected or "").strip():
            lines.append("감지: " + str(detected).strip())
        return lines

    def threshold_text(self, key: str, suffix: str = "") -> str:
        return compact_number(float(self.thresholds.get(key, DEFAULT_THRESHOLDS.get(key, 0)) or 0)) + suffix

    def model_score_phrase(self, side: str, score: float) -> str:
        value = round(float(score or 0), 1)
        if side == "buy":
            if value >= 85:
                label = "강한 매수 후보"
            elif value >= 74:
                label = "매수 후보"
            elif value >= 60:
                label = "관찰 후보"
            else:
                label = "매수 기준 미달"
        else:
            if value >= 85:
                label = "강한 매도 압력"
            elif value >= 72:
                label = "분할매도 압력"
            elif value >= 60:
                label = "리스크 관찰"
            else:
                label = "매도 기준 미달"
        return label + " (" + compact_number(value) + "점)"

    def decision_score_phrase(self, label: object, score: object) -> str:
        text = str(label or "-").strip() or "-"
        return text + " (" + compact_number(float(score or 0)) + "점)"

    def decision_action_group(self, label: object) -> str:
        return decision_action_group_for_label(label)

    def enabled_signal_events(self, events: List[AlertEvent]) -> List[AlertEvent]:
        return [event for event in events or [] if self.enabled(event.rule)]

    def meaningful_decision_change(self, current_decision: Dict[str, object], previous_decision: Dict[str, object], pressure_delta: float) -> bool:
        current_label = str(current_decision.get("decision") or "").strip()
        previous_label = str(previous_decision.get("decision") or "").strip()
        if not current_label or not previous_label or current_label == previous_label:
            return False
        if self.decision_action_group(current_label) != self.decision_action_group(previous_label):
            return True
        label_buffer = float(self.thresholds.get("monitorDecisionLabelBuffer", 5) or 0)
        return abs(float(pressure_delta or 0)) >= label_buffer

    def events_for_snapshot(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        raw_events: List[AlertEvent] = []
        snapshot = self.snapshot_with_strategy_scores(snapshot)
        has_account_data = snapshot.has_live_account_data()
        previous_has_account_data = monitor_state_has_live_account_data(previous)
        raw_events.extend(self.connection_events(snapshot, previous))
        raw_events.extend(self.heartbeat_events(snapshot))
        if has_account_data:
            raw_events.extend(self.model_score_events(snapshot))
        if has_account_data and previous_has_account_data:
            raw_events.extend(self.position_change_events(snapshot, previous))
            raw_events.extend(self.cash_events(snapshot, previous))
        raw_events.extend(self.watchlist_quote_events(snapshot, previous or {}))
        raw_events.extend(self.external_signal_events(snapshot, previous or {}))
        if has_account_data:
            raw_events.extend(self.holding_timing_events(snapshot))
        raw_events = self.attach_data_freshness(snapshot, raw_events)
        system_events, signal_events = split_operational_and_investment_events(raw_events)
        signal_events = self.enabled_signal_events(signal_events)
        events = [*system_events, *build_investment_insight_events(snapshot, signal_events)]
        return [event for event in self.stamp_events(snapshot, events) if self.enabled(event.rule)]

    def type_check_events_for_snapshot(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        snapshot = self.snapshot_with_strategy_scores(snapshot)
        events.extend(self.connection_events(snapshot, {"status": "이전 연결 상태"}))
        events.extend(self.heartbeat_events(snapshot))
        model_events = self.model_score_events(snapshot)
        events.extend(self.only_rule("modelBuy", model_events) or self.model_sample_events(snapshot, "modelBuy"))
        events.extend(self.only_rule("modelSell", model_events) or self.model_sample_events(snapshot, "modelSell"))
        watchlist_snapshot = self.snapshot_with_sample_watchlist(snapshot)
        events.extend(self.only_rule("watchlistBuyCandidate", model_events) or self.model_sample_events(watchlist_snapshot, "watchlistBuyCandidate"))
        ontology_watchlist_snapshot = self.snapshot_with_sample_watchlist_ontology_signal(watchlist_snapshot)
        events.extend(self.only_rule(WATCHLIST_ONTOLOGY_SIGNAL, self.model_score_events(ontology_watchlist_snapshot)))

        state = snapshot.to_monitor_state()
        symbols = sorted(state.get("positions", {}).keys())
        if symbols:
            symbol = symbols[0]
            events.extend(self.only_rule(
                "monitorPositionChange",
                self.position_change_events(snapshot, self.previous_with_position_quantity(state, symbol)),
            ))
            events.extend(self.only_rule(
                "monitorPnlChange",
                self.position_change_events(snapshot, self.previous_with_pnl_delta(state, symbol)),
            ))
            events.extend(self.only_rule(
                "monitorValueChange",
                self.position_change_events(snapshot, self.previous_with_value_delta(state, symbol)),
            ))
            trend_snapshot = self.snapshot_with_trend_metrics(snapshot, symbol)
            events.extend(self.only_rule(
                "monitorTrendChange",
                self.position_change_events(
                    trend_snapshot,
                    self.previous_with_trend_delta(trend_snapshot.to_monitor_state(), symbol),
                ),
            ))
            events.extend(self.only_rule(
                "monitorDecisionChange",
                self.position_change_events(snapshot, self.previous_with_decision_delta(state, symbol)),
            ))

        events.extend(self.only_rule("monitorCashChange", self.cash_events(snapshot, self.previous_with_cash_delta(state))))
        events.extend(self.only_rule("watchlistQuote", self.watchlist_quote_events(
            watchlist_snapshot,
            self.previous_with_watchlist_delta(watchlist_snapshot.to_monitor_state()),
        )))
        events.extend(self.only_rule("watchlistQuotePending", self.watchlist_quote_events(
            self.snapshot_with_pending_watchlist(watchlist_snapshot),
            {},
        )))
        external_snapshot = self.snapshot_with_sample_external_signals(snapshot)
        external_state = external_snapshot.to_monitor_state()
        events.extend(self.external_signal_events(external_snapshot, self.previous_with_external_delta(external_state)))
        timing_events = self.holding_timing_events(snapshot)
        if not timing_events and snapshot.decisions:
            timing_snapshot = replace(snapshot, decisions=[
                replace(snapshot.decisions[0], tone="caution", decision=snapshot.decisions[0].decision or "조건부 보유"),
                *snapshot.decisions[1:],
            ])
            timing_events = self.holding_timing_events(timing_snapshot)
        events.extend(self.only_rule("holdingTiming", timing_events))
        events = self.attach_data_freshness(snapshot, events)
        investment_insights = build_investment_insight_events(snapshot, self.enabled_signal_events(events))
        events.extend(self.only_rule(INVESTMENT_INSIGHT, investment_insights))
        return self.unique_rules([event for event in self.stamp_events(snapshot, events) if self.enabled(event.rule)])

    def attach_data_freshness(self, snapshot: AccountSnapshot, events: List[AlertEvent]) -> List[AlertEvent]:
        state = snapshot.to_monitor_state()
        positions: Dict[str, Dict[str, object]] = {}
        for group_key in ["positions", "watchlist"]:
            group = state.get(group_key) if isinstance(state.get(group_key), dict) else {}
            for symbol, item in group.items():
                if isinstance(item, dict):
                    positions[str(symbol or "").upper()] = item
        for event in events:
            event.metadata = dict(event.metadata or {})
            event.metadata.setdefault("dataFreshnessRequired", data_freshness_required(event.rule))
            if event.metadata.get("dataFreshness"):
                continue
            symbol = str(event.symbol or "").upper()
            position = positions.get(symbol)
            if position:
                event.metadata["dataFreshness"] = freshness_from_position(position, event.rule, self.settings)
            elif data_freshness_required(event.rule):
                event.metadata["dataFreshness"] = freshness_record(
                    "accountSnapshot",
                    event.rule,
                    settings=self.settings,
                    source_fetched_at=snapshot.generated_at,
                    data_quality=snapshot.mode,
                )
        return events

    def stamp_events(self, snapshot: AccountSnapshot, events: List[AlertEvent]) -> List[AlertEvent]:
        generated_at = str(snapshot.generated_at or "").strip()
        formula_metadata = self.notification_formula_metadata()
        for event in events:
            if generated_at:
                event.generated_at = generated_at
            if formula_metadata:
                event.metadata.update({key: value for key, value in formula_metadata.items() if key not in event.metadata})
        return events

    def snapshot_with_strategy_scores(self, snapshot: AccountSnapshot) -> AccountSnapshot:
        if not snapshot.has_live_account_data():
            return snapshot
        snapshot.decisions = decisions_for_positions(
            snapshot.positions,
            snapshot.portfolio,
            self.strategy_model,
            external_signals=snapshot.external_signals,
        )
        return snapshot

    def notification_formula_metadata(self) -> Dict[str, object]:
        keys = [
            "buyScoreFormula",
            "sellScoreFormula",
            "profitTakeScoreFormula",
            "lossCutScoreFormula",
            "notificationScoreFormula",
        ]
        return {
            key: str(self.settings.get(key) or "").strip()
            for key in keys
            if str(self.settings.get(key) or "").strip()
        }

    def position_from_state(self, item: Dict[str, object]) -> Position:
        market_signal_coverage = item.get("market_signal_coverage") if "market_signal_coverage" in item else item.get("marketSignalCoverage")
        change_rate_value = item.get("change_rate") if "change_rate" in item else item.get("changeRate")
        quote_source_value = item.get("quote_source") if "quote_source" in item else item.get("quoteSource")
        quote_status_value = item.get("quote_status") if "quote_status" in item else item.get("quoteStatus")
        quote_message_value = item.get("quote_message") if "quote_message" in item else item.get("quoteMessage")
        data_quality_value = item.get("data_quality") if "data_quality" in item else item.get("dataQuality")
        updated_at_value = item.get("updated_at") if "updated_at" in item else item.get("updatedAt")
        return Position(
            symbol=str(item.get("symbol") or ""),
            name=str(item.get("name") or ""),
            market=str(item.get("market") or ""),
            currency=str(item.get("currency") or ""),
            quantity=number(item.get("quantity")),
            sellable_quantity=number(item.get("sellable_quantity") if "sellable_quantity" in item else item.get("sellableQuantity")),
            average_price=number(item.get("average_price") if "average_price" in item else item.get("averagePrice")),
            current_price=number(item.get("current_price") if "current_price" in item else item.get("currentPrice")),
            change_rate=number(change_rate_value) if change_rate_value is not None else None,
            quote_source=str(quote_source_value or ""),
            quote_status=str(quote_status_value or ""),
            quote_message=str(quote_message_value or ""),
            data_quality=str(data_quality_value or ""),
            market_signal_coverage=dict(market_signal_coverage or {}) if isinstance(market_signal_coverage, dict) else {},
            updated_at=str(updated_at_value or ""),
            market_value=number(item.get("market_value") if "market_value" in item else item.get("marketValue")),
            profit_loss=number(item.get("profit_loss") if "profit_loss" in item else item.get("profitLoss")),
            profit_loss_rate=number(item.get("profit_loss_rate") if "profit_loss_rate" in item else item.get("profitLossRate")),
            trade_strength=number(item.get("trade_strength") if "trade_strength" in item else item.get("tradeStrength")),
            trading_value=number(item.get("trading_value") if "trading_value" in item else item.get("tradingValue")),
            volume=number(item.get("volume")),
            volume_ratio=number(item.get("volume_ratio") if "volume_ratio" in item else item.get("volumeRatio")),
            buy_volume=number(item.get("buy_volume") if "buy_volume" in item else item.get("buyVolume")),
            sell_volume=number(item.get("sell_volume") if "sell_volume" in item else item.get("sellVolume")),
            orderbook_bid_volume=number(item.get("orderbook_bid_volume") if "orderbook_bid_volume" in item else item.get("orderbookBidVolume")),
            orderbook_ask_volume=number(item.get("orderbook_ask_volume") if "orderbook_ask_volume" in item else item.get("orderbookAskVolume")),
            bid_ask_imbalance=number(item.get("bid_ask_imbalance") if "bid_ask_imbalance" in item else item.get("bidAskImbalance")),
            foreign_buy_volume=number(item.get("foreign_buy_volume") if "foreign_buy_volume" in item else item.get("foreignBuyVolume")),
            foreign_sell_volume=number(item.get("foreign_sell_volume") if "foreign_sell_volume" in item else item.get("foreignSellVolume")),
            foreign_net_volume=number(item.get("foreign_net_volume") if "foreign_net_volume" in item else item.get("foreignNetVolume")),
            foreign_net_amount=number(item.get("foreign_net_amount")) or number(item.get("foreignNetAmount")),
            institution_buy_volume=number(item.get("institution_buy_volume") if "institution_buy_volume" in item else item.get("institutionBuyVolume")),
            institution_sell_volume=number(item.get("institution_sell_volume") if "institution_sell_volume" in item else item.get("institutionSellVolume")),
            institution_net_volume=number(item.get("institution_net_volume") if "institution_net_volume" in item else item.get("institutionNetVolume")),
            institution_net_amount=number(item.get("institution_net_amount")) or number(item.get("institutionNetAmount")),
            individual_buy_volume=number(item.get("individual_buy_volume") if "individual_buy_volume" in item else item.get("individualBuyVolume")),
            individual_sell_volume=number(item.get("individual_sell_volume") if "individual_sell_volume" in item else item.get("individualSellVolume")),
            individual_net_volume=number(item.get("individual_net_volume") if "individual_net_volume" in item else item.get("individualNetVolume")),
            individual_net_amount=number(item.get("individual_net_amount")) or number(item.get("individualNetAmount")),
            ma20=number(item.get("ma20")),
            ma60=number(item.get("ma60")),
            ma20_slope=number(item.get("ma20_slope") if "ma20_slope" in item else item.get("ma20Slope")),
            ma60_slope=number(item.get("ma60_slope") if "ma60_slope" in item else item.get("ma60Slope")),
            ma20_distance=number(item.get("ma20_distance") if "ma20_distance" in item else item.get("ma20Distance")),
            ma60_distance=number(item.get("ma60_distance") if "ma60_distance" in item else item.get("ma60Distance")),
            sector=str(item.get("sector") or "기타"),
            source=str(item.get("source") or item.get("positionSource") or "holding"),
        )

    def sector_ratio_for_position(self, snapshot: AccountSnapshot, sector: str) -> float:
        for item in snapshot.portfolio.sectors:
            if item.get("sector") == sector:
                return number(item.get("ratio"))
        return 0.0

    def holding_formula_audits(self, snapshot: AccountSnapshot, position_state: Dict[str, object], decision_state: Dict[str, object] = None) -> List[Dict[str, object]]:
        position = self.position_from_state(position_state)
        sector_ratio = self.sector_ratio_for_position(snapshot, position.sector)
        scores = None
        if isinstance(decision_state, dict):
            scores = {
                "profitTakePressure": number(decision_state.get("profit_take_pressure") if "profit_take_pressure" in decision_state else decision_state.get("profitTakePressure")),
                "lossCutPressure": number(decision_state.get("loss_cut_pressure") if "loss_cut_pressure" in decision_state else decision_state.get("lossCutPressure")),
            }
        return self.strategy_model.holding_formula_audits(position, sector_ratio, scores)

    def ontology_opinion_from_decision(self, decision_state: Dict[str, object]) -> Dict[str, object]:
        if not isinstance(decision_state, dict):
            return {}
        opinion = decision_state.get("ontology_opinion") if "ontology_opinion" in decision_state else decision_state.get("ontologyOpinion")
        return dict(opinion or {}) if isinstance(opinion, dict) else {}

    def ontology_worldview_from_decision(self, decision_state: Dict[str, object]) -> Dict[str, object]:
        if not isinstance(decision_state, dict):
            return {}
        worldview = decision_state.get("ontology_worldview") if "ontology_worldview" in decision_state else decision_state.get("ontologyWorldview")
        return dict(worldview or {}) if isinstance(worldview, dict) else {}

    def ai_context_from_decision(self, decision_state: Dict[str, object]) -> Dict[str, object]:
        if not isinstance(decision_state, dict):
            return {}
        context = decision_state.get("ai_context") if "ai_context" in decision_state else decision_state.get("aiContext")
        return dict(context or {}) if isinstance(context, dict) else {}

    def active_investment_opinion_from_decision(self, decision_state: Dict[str, object]) -> Dict[str, object]:
        if not isinstance(decision_state, dict):
            return {}
        opinion = (
            decision_state.get("active_investment_opinion")
            if "active_investment_opinion" in decision_state
            else decision_state.get("activeInvestmentOpinion")
        )
        if isinstance(opinion, dict) and opinion:
            return dict(opinion)
        ai_context = self.ai_context_from_decision(decision_state)
        nested = ai_context.get("activeInvestmentOpinion") if isinstance(ai_context, dict) else {}
        return dict(nested or {}) if isinstance(nested, dict) else {}

    def relation_context_from_decision(self, decision_state: Dict[str, object]) -> Dict[str, object]:
        if not isinstance(decision_state, dict):
            return {}
        context = (
            decision_state.get("relation_rule_context")
            if "relation_rule_context" in decision_state
            else decision_state.get("relationRuleContext")
        )
        if isinstance(context, dict) and context:
            return dict(context)
        ai_context = self.ai_context_from_decision(decision_state)
        nested = ai_context.get("relationRuleContext") if isinstance(ai_context, dict) else {}
        return dict(nested or {}) if isinstance(nested, dict) else {}

    def prompt_context_from_decision(self, decision_state: Dict[str, object]) -> Dict[str, object]:
        if not isinstance(decision_state, dict):
            return {}
        context = (
            decision_state.get("ai_prompt_context")
            if "ai_prompt_context" in decision_state
            else decision_state.get("aiPromptContext")
        )
        if isinstance(context, dict) and context:
            return dict(context)
        relation_context = self.relation_context_from_decision(decision_state)
        nested = relation_context.get("promptContext") if isinstance(relation_context, dict) else {}
        if isinstance(nested, dict) and nested:
            return dict(nested)
        ai_context = self.ai_context_from_decision(decision_state)
        nested = ai_context.get("promptContext") if isinstance(ai_context, dict) else {}
        return dict(nested or {}) if isinstance(nested, dict) else {}

    def relation_context_lines(self, decision_state: Dict[str, object]) -> List[str]:
        return relation_rule_context_summary_lines(self.relation_context_from_decision(decision_state))

    def ontology_context_lines(self, decision_state: Dict[str, object]) -> List[str]:
        opinion = self.ontology_opinion_from_decision(decision_state)
        if not opinion:
            return []
        lines = [
            "관계 판단: " + str(opinion.get("action") or "-")
            + " · 관계 신호 " + compact_number(float(opinion.get("ontology_pressure") or opinion.get("ontologyPressure") or 0)) + "점"
            + " · 확신 " + compact_number(float(opinion.get("conviction") or 0)) + "점",
        ]
        thesis = str(opinion.get("thesis") or "").strip()
        if thesis:
            lines.append("판단 근거: " + thesis)
        contradictions = opinion.get("contradictions") if isinstance(opinion.get("contradictions"), list) else []
        if contradictions:
            lines.append("관계 충돌: " + " · ".join(str(item) for item in contradictions[:2]))
        risks = opinion.get("dominant_risks") if isinstance(opinion.get("dominant_risks"), list) else opinion.get("dominantRisks")
        if isinstance(risks, list) and risks:
            lines.append("주요 리스크: " + " · ".join(str(item) for item in risks[:2]))
        return lines

    def active_investment_opinion_lines(self, decision_state: Dict[str, object]) -> List[str]:
        opinion = self.active_investment_opinion_from_decision(decision_state)
        if not opinion:
            return []
        action = str(opinion.get("actionLabel") or opinion.get("action") or "").strip()
        conviction = opinion.get("conviction")
        thesis = str(opinion.get("thesis") or "").strip()
        lines = []
        if action:
            lines.append("AI 적극 의견: " + action + (" · 확신 " + compact_number(float(conviction or 0)) + "%" if conviction not in (None, "") else ""))
        if thesis:
            lines.append("의견 근거: " + thesis)
        return lines

    def only_rule(self, rule: str, events: List[AlertEvent]) -> List[AlertEvent]:
        return [event for event in events if event.rule == rule]

    def unique_rules(self, events: List[AlertEvent]) -> List[AlertEvent]:
        seen = set()
        unique: List[AlertEvent] = []
        for event in events:
            if event.rule in seen:
                continue
            seen.add(event.rule)
            unique.append(event)
        return unique

    def previous_with_position_quantity(self, state: Dict[str, object], symbol: str) -> Dict[str, object]:
        previous = deepcopy(state)
        position = previous.get("positions", {}).get(symbol, {})
        position["quantity"] = max(0, float(position.get("quantity") or 0) - 1)
        return previous

    def previous_with_pnl_delta(self, state: Dict[str, object], symbol: str) -> Dict[str, object]:
        previous = deepcopy(state)
        position = previous.get("positions", {}).get(symbol, {})
        current = float(position.get("profit_loss_rate") or 0)
        position["profit_loss_rate"] = current - float(self.thresholds.get("monitorPnlDelta", 0)) - 1
        return previous

    def previous_with_value_delta(self, state: Dict[str, object], symbol: str) -> Dict[str, object]:
        previous = deepcopy(state)
        position = previous.get("positions", {}).get(symbol, {})
        current = float(position.get("market_value") or 0)
        threshold = max(1.0, float(self.thresholds.get("monitorValueDelta", 0)) + 1)
        position["market_value"] = current / (1 + threshold / 100) if current else 1
        return previous

    def previous_with_decision_delta(self, state: Dict[str, object], symbol: str) -> Dict[str, object]:
        previous = deepcopy(state)
        decision = previous.get("decisions", {}).get(symbol, {})
        current = float(decision.get("exit_pressure") or 0)
        decision["decision"] = "이전 판단"
        decision["exit_pressure"] = max(0, current - float(self.thresholds.get("monitorExitPressureDelta", 0)) - 1)
        return previous

    def previous_with_cash_delta(self, state: Dict[str, object]) -> Dict[str, object]:
        previous = deepcopy(state)
        markets = previous.get("portfolio", {}).get("markets") or []
        if not markets:
            previous.setdefault("portfolio", {})["markets"] = [{"key": "KR", "label": "한국장", "cashRatio": 100}]
            return previous
        first = markets[0]
        current = float(first.get("cashRatio") or 0)
        first["cashRatio"] = current + float(self.thresholds.get("monitorCashDelta", 0)) + 1
        return previous

    def snapshot_with_sample_external_signals(self, snapshot: AccountSnapshot) -> AccountSnapshot:
        signals = snapshot.external_signals if snapshot.external_signals else {
            "equityQuotes": {
                "AAPL": {
                    "provider": "Alpha Vantage",
                    "price": 125.5,
                    "changePercent": 4.2,
                    "volume": 58000000,
                    "latestTradingDay": "2026-07-01",
                }
            },
            "cryptoMarkets": {
                "bitcoin": {
                    "provider": "CoinGecko",
                    "symbol": "BTC",
                    "name": "Bitcoin",
                    "price": 108000,
                    "volume24h": 42000000000,
                    "change24h": -5.4,
                    "change7d": -11.2,
                }
            },
            "macro": {
                "series": {
                    "DGS10": {"provider": "FRED", "date": "2026-07-01", "value": 4.35},
                    "DGS2": {"provider": "FRED", "date": "2026-07-01", "value": 3.95},
                },
                "yieldSpread10y2y": 0.4,
            },
            "dartDisclosures": {
                "005930": {
                    "provider": "OpenDART",
                    "corpName": "삼성전자",
                    "reportName": "주요사항보고서",
                    "receiptNo": "20260701000001",
                    "receiptDate": "20260701",
                    "count": 1,
                }
            },
            "statuses": [{"source": "FRED", "ok": False, "message": "샘플 연결 오류"}],
        }
        return replace(snapshot, external_signals=signals)

    def previous_with_external_delta(self, state: Dict[str, object]) -> Dict[str, object]:
        previous = deepcopy(state)
        signals = previous.setdefault("externalSignals", {})
        series = (signals.setdefault("macro", {}).setdefault("series", {}))
        if "DGS10" in series:
            series["DGS10"]["value"] = number(series["DGS10"].get("value")) - 0.25
        if "DGS2" in series:
            series["DGS2"]["value"] = number(series["DGS2"].get("value")) + 0.05
        if "yieldSpread10y2y" in signals.get("macro", {}):
            signals["macro"]["yieldSpread10y2y"] = number(signals["macro"].get("yieldSpread10y2y")) - 0.3
        disclosures = signals.setdefault("dartDisclosures", {})
        for disclosure in disclosures.values():
            if isinstance(disclosure, dict):
                disclosure["receiptNo"] = "previous-" + str(disclosure.get("receiptNo") or "")
                break
        return previous

    def snapshot_with_trend_metrics(self, snapshot: AccountSnapshot, symbol: str) -> AccountSnapshot:
        for position in snapshot.positions:
            if position.symbol.upper() != symbol:
                continue
            if position.current_price and position.ma20 and position.ma60:
                return snapshot
            price = position.current_price or (position.market_value / max(1.0, position.quantity or 1)) or 100.0
            replacement = replace(
                position,
                current_price=price,
                ma20=price * 0.98,
                ma60=price * 1.02,
                ma20_distance=pct_delta(price, price * 0.98),
                ma60_distance=pct_delta(price, price * 1.02),
            )
            return replace(snapshot, positions=[
                replacement if item.symbol.upper() == symbol else item
                for item in snapshot.positions
            ])
        return snapshot

    def snapshot_with_sample_watchlist(self, snapshot: AccountSnapshot) -> AccountSnapshot:
        if snapshot.watchlist:
            return snapshot
        sample = replace(
            snapshot.positions[0],
            quantity=0,
            sellable_quantity=0,
            average_price=0,
            market_value=0,
            profit_loss=0,
            profit_loss_rate=0,
        ) if snapshot.positions else None
        if sample:
            return replace(snapshot, watchlist=[sample])
        return snapshot

    def snapshot_with_sample_watchlist_ontology_signal(self, snapshot: AccountSnapshot) -> AccountSnapshot:
        candidates = list(snapshot.watchlist or snapshot.positions or [])
        if not candidates:
            return snapshot
        base = candidates[0]
        current = number(base.current_price) or number(base.average_price) or 100.0
        sample = replace(
            base,
            source="watchlist",
            quantity=0,
            sellable_quantity=0,
            average_price=0,
            market_value=0,
            profit_loss=0,
            profit_loss_rate=0,
            current_price=current,
            ma20=current / 0.962,
            ma60=current / 1.01,
            ma20_distance=-3.8,
            ma60_distance=1.0,
            volume_ratio=1.1,
            trade_strength=118,
            bid_ask_imbalance=12,
            foreign_net_volume=180000,
            institution_net_volume=90000,
            individual_net_volume=-210000,
        )
        return replace(snapshot, positions=[], decisions=[], watchlist=[sample])

    def snapshot_with_pending_watchlist(self, snapshot: AccountSnapshot) -> AccountSnapshot:
        if snapshot.watchlist:
            return replace(snapshot, watchlist=[
                replace(
                    item,
                    current_price=0,
                    volume=0,
                    volume_ratio=0,
                    ma20=0,
                    ma60=0,
                    ma20_distance=0,
                    ma60_distance=0,
                )
                for item in snapshot.watchlist
            ])
        return snapshot

    def previous_with_watchlist_delta(self, state: Dict[str, object]) -> Dict[str, object]:
        previous = deepcopy(state)
        watchlist = previous.get("watchlist") or {}
        for item in watchlist.values():
            if not isinstance(item, dict):
                continue
            current = self.position_current_price(item)
            if current:
                item["current_price"] = current / 1.05
                break
        return previous

    def previous_with_trend_delta(self, state: Dict[str, object], symbol: str) -> Dict[str, object]:
        previous = deepcopy(state)
        position = previous.get("positions", {}).get(symbol, {})
        ma20 = number(position.get("ma20"))
        ma60 = number(position.get("ma60"))
        if ma20:
            position["current_price"] = ma20 * 0.98
        if ma20 and ma60:
            position["ma20"] = min(ma20, ma60 * 0.98)
            position["ma60"] = max(ma60, ma20 * 1.02)
        return previous

    def position_currency(self, position: Dict[str, object]) -> str:
        currency = str(position.get("currency") or "").upper()
        if currency:
            return currency
        market = str(position.get("market") or "").upper()
        symbol = str(position.get("symbol") or "").upper()
        if market == "US":
            return "USD"
        if market in {"KR", "KOSPI", "KOSDAQ"} or symbol.isdigit():
            return "KRW"
        return "KRW"

    def position_market_value(self, position: Dict[str, object]) -> float:
        return number(position.get("market_value") if "market_value" in position else position.get("marketValue"))

    def position_quantity(self, position: Dict[str, object]) -> float:
        return number(position.get("quantity"))

    def position_sellable_quantity(self, position: Dict[str, object]) -> float:
        return number(position.get("sellable_quantity") if "sellable_quantity" in position else position.get("sellableQuantity"))

    def position_current_price(self, position: Dict[str, object]) -> float:
        return number(position.get("current_price") if "current_price" in position else position.get("currentPrice"))

    def position_average_price(self, position: Dict[str, object]) -> float:
        return number(position.get("average_price") if "average_price" in position else position.get("averagePrice"))

    def position_profit_loss_rate(self, position: Dict[str, object]) -> float:
        return number(position.get("profit_loss_rate") if "profit_loss_rate" in position else position.get("profitLossRate"))

    def has_position_field(self, position: Dict[str, object], snake_key: str, camel_key: str) -> bool:
        return snake_key in position or camel_key in position

    def current_price_line(self, position: Dict[str, object]) -> str:
        price = self.position_current_price(position)
        if not price:
            return ""
        return "현재가: " + price_money(price, self.position_currency(position))

    def average_price_line(self, position: Dict[str, object]) -> str:
        price = self.position_average_price(position)
        if not price:
            return ""
        return "평균매입가: " + price_money(price, self.position_currency(position))

    def profit_rate_line(self, position: Dict[str, object]) -> str:
        if not self.has_position_field(position, "profit_loss_rate", "profitLossRate"):
            return ""
        return "수익률: " + signed_pct(self.position_profit_loss_rate(position))

    def holding_balance_line(self, position: Dict[str, object]) -> str:
        parts: List[str] = []
        quantity = self.position_quantity(position)
        sellable = self.position_sellable_quantity(position)
        market_value = self.position_market_value(position)
        currency = self.position_currency(position)
        if quantity:
            parts.append("수량 " + compact_number(quantity) + "주")
        if sellable:
            parts.append("매도가능 " + compact_number(sellable) + "주")
        if market_value:
            parts.append("평가금액 " + money(market_value, currency))
        if not parts:
            return ""
        return "보유: " + ", ".join(parts)

    def holding_quantity_line(self, position: Dict[str, object]) -> str:
        quantity = self.position_quantity(position)
        if not quantity:
            return ""
        return "보유 수량: " + compact_number(quantity) + "주"

    def sellable_quantity_line(self, position: Dict[str, object]) -> str:
        sellable = self.position_sellable_quantity(position)
        if not sellable:
            return ""
        return "매도가능 수량: " + compact_number(sellable) + "주"

    def position_market_value_line(self, position: Dict[str, object]) -> str:
        market_value = self.position_market_value(position)
        if not market_value:
            return ""
        return "종목 평가금액: " + money(market_value, self.position_currency(position))

    def portfolio_total_value(self, portfolio) -> float:
        if isinstance(portfolio, dict):
            return number(portfolio.get("total"))
        return number(getattr(portfolio, "total", 0))

    def account_market_value_line(self, portfolio) -> str:
        total = self.portfolio_total_value(portfolio)
        if not total:
            return ""
        return "계좌 평가금액: " + money(total, "KRW")

    def holding_price_lines(self, position: Dict[str, object], portfolio=None) -> List[str]:
        return [
            line
            for line in [
                self.current_price_line(position),
                self.average_price_line(position),
                self.profit_rate_line(position),
                self.holding_quantity_line(position),
                self.sellable_quantity_line(position),
                self.position_market_value_line(position),
                self.account_market_value_line(portfolio),
            ]
            if line
        ]

    def position_ma(self, position: Dict[str, object], period: int) -> float:
        return number(position.get("ma" + str(period)) or position.get("movingAverage" + str(period)) or position.get("sma" + str(period)))

    def position_volume(self, position: Dict[str, object]) -> float:
        return number(position.get("volume"))

    def position_volume_ratio(self, position: Dict[str, object]) -> float:
        return number(position.get("volume_ratio") if "volume_ratio" in position else position.get("volumeRatio"))

    def position_trade_strength(self, position: Dict[str, object]) -> float:
        return number(position.get("trade_strength")) or number(position.get("tradeStrength"))

    def position_trading_value(self, position: Dict[str, object]) -> float:
        value = number(position.get("trading_value")) or number(position.get("tradingValue"))
        if value:
            return value
        volume = self.position_volume(position)
        price = self.position_current_price(position)
        return volume * price if volume and price else 0.0

    def position_value_base(self, position: Dict[str, object]) -> float:
        return value_in_base(self.position_market_value(position), self.position_currency(position), self.fx_rates)

    def position_value_label(self, position: Dict[str, object]) -> str:
        currency = self.position_currency(position)
        native_value = self.position_market_value(position)
        native_label = money(native_value, currency)
        if currency == "KRW":
            return native_label
        base_value = self.position_value_base(position)
        return native_label + " (약 " + money(base_value, "KRW") + ")"

    def value_delta_basis_label(self, before: Dict[str, object], item: Dict[str, object]) -> str:
        currencies = {self.position_currency(before), self.position_currency(item)}
        return " (KRW 환산 기준)" if currencies != {"KRW"} else ""

    def flow_context_line(self, position: Dict[str, object]) -> str:
        parts: List[str] = []
        volume = self.position_volume(position)
        ratio = self.position_volume_ratio(position)
        if volume > 0:
            volume_label = compact_number(volume)
            if ratio > 0:
                volume_label += "(" + compact_number(ratio) + "x)"
            parts.append("거래량 " + volume_label)
        elif ratio > 0:
            parts.append("거래량 배율 " + compact_number(ratio) + "x")

        trading_value = self.position_trading_value(position)
        if trading_value > 0:
            parts.append("거래액 " + money(trading_value, self.position_currency(position)))
        trade_strength = self.position_trade_strength(position)
        if trade_strength > 0:
            parts.append("체결강도 " + compact_number(trade_strength))
        orderbook_bid = number(position.get("orderbook_bid_volume")) or number(position.get("orderbookBidVolume"))
        orderbook_ask = number(position.get("orderbook_ask_volume")) or number(position.get("orderbookAskVolume"))
        if orderbook_bid or orderbook_ask:
            parts.append("호가잔량 매수 " + compact_number(orderbook_bid) + "/매도 " + compact_number(orderbook_ask))
        bid_ask_imbalance = number(position.get("bid_ask_imbalance")) or number(position.get("bidAskImbalance"))
        if bid_ask_imbalance:
            parts.append("호가불균형 " + signed_pct(bid_ask_imbalance))
        if not parts:
            return ""
        return "수급: " + ", ".join(parts)

    def investor_value(self, position: Dict[str, object], snake_key: str, camel_key: str) -> float:
        return number(position.get(snake_key)) or number(position.get(camel_key))

    def investor_amount_text(self, value: float, currency: str) -> str:
        amount = number(value)
        if not amount:
            return ""
        return ("+" if amount > 0 else "-") + money(abs(amount), currency)

    def normalized_investor_net_amount(self, position: Dict[str, object], net: float, net_amount: float) -> float:
        amount = number(net_amount)
        if not amount:
            return 0.0
        currency = self.position_currency(position)
        expected = abs(number(net) * self.position_current_price(position))
        if currency == "KRW" and expected:
            ratio = expected / max(1.0, abs(amount))
            if 500000 <= ratio <= 1500000:
                return amount * 1000000
        return amount

    def investor_summary(self, label: str, buy: float, sell: float, net: float, net_amount: float, currency: str) -> str:
        amount_text = (", 금액 " + self.investor_amount_text(net_amount, currency)) if net_amount else ""
        if buy or sell:
            effective_net = net if net else buy - sell
            direction = "순매수" if effective_net > 0 else "순매도" if effective_net < 0 else "순매수/순매도 0"
            net_text = direction + " " + compact_number(abs(effective_net)) + "주" if effective_net else direction
            return label + ": " + net_text + ", 매수 " + compact_number(buy) + "주, 매도 " + compact_number(sell) + "주" + amount_text
        if net:
            direction = "순매수" if net > 0 else "순매도"
            return label + ": " + direction + " " + compact_number(abs(net)) + "주" + amount_text
        if net_amount:
            return label + ": 금액 " + self.investor_amount_text(net_amount, currency)
        return ""

    def investor_context_line(self, position: Dict[str, object]) -> str:
        currency = self.position_currency(position)
        foreign_buy = self.investor_value(position, "foreign_buy_volume", "foreignBuyVolume")
        foreign_sell = self.investor_value(position, "foreign_sell_volume", "foreignSellVolume")
        foreign_net = self.investor_value(position, "foreign_net_volume", "foreignNetVolume")
        foreign_net_amount = self.normalized_investor_net_amount(position, foreign_net, self.investor_value(position, "foreign_net_amount", "foreignNetAmount"))
        institution_buy = self.investor_value(position, "institution_buy_volume", "institutionBuyVolume")
        institution_sell = self.investor_value(position, "institution_sell_volume", "institutionSellVolume")
        institution_net = self.investor_value(position, "institution_net_volume", "institutionNetVolume")
        institution_net_amount = self.normalized_investor_net_amount(position, institution_net, self.investor_value(position, "institution_net_amount", "institutionNetAmount"))
        individual_buy = self.investor_value(position, "individual_buy_volume", "individualBuyVolume")
        individual_sell = self.investor_value(position, "individual_sell_volume", "individualSellVolume")
        individual_net = self.investor_value(position, "individual_net_volume", "individualNetVolume")
        individual_net_amount = self.normalized_investor_net_amount(position, individual_net, self.investor_value(position, "individual_net_amount", "individualNetAmount"))
        summaries = [
            self.investor_summary("외국인", foreign_buy, foreign_sell, foreign_net, foreign_net_amount, currency),
            self.investor_summary("기관", institution_buy, institution_sell, institution_net, institution_net_amount, currency),
            self.investor_summary("개인", individual_buy, individual_sell, individual_net, individual_net_amount, currency),
        ]
        parts = [summary for summary in summaries if summary]
        if not parts:
            return ""
        return "투자자:\n" + "\n".join(parts)

    def holding_action_text(self, decision_text: str, pnl_rate: float) -> str:
        blob = str(decision_text or "")
        if "손절" in blob or "손실" in blob or pnl_rate <= -8:
            return "손절·분할축소 우선, 20일선 회복 전 추가매수 보류"
        if "축소" in blob or (pnl_rate < 0 and ("방어" in blob or "관망" in blob)):
            return "손실 축소 우선, 회복 조건 확인 전 비중 확대 보류"
        if "분할매도" in blob or "익절" in blob or "수익" in blob:
            return "분할매도 우선, 목표 수익률과 20일선 이탈 조건을 함께 적용"
        if "리밸런싱" in blob:
            return "초과 비중 축소 우선, 같은 섹터 추가매수 보류"
        if "비트코인" in blob:
            return "비트코인 민감 비중 축소 검토, 크립토 변동 안정 전 추가매수 보류"
        if "공시" in blob:
            return "공시 원문 확인 전 추가매수 보류, 변동성 확대 시 비중 축소 검토"
        if "매수" in blob:
            return "분할매수 후보, 첫 매수는 작게 시작하고 손절 기준을 먼저 설정"
        if pnl_rate > 0:
            return "보유 유지, 수익 보호용 분할매도 기준만 대기"
        return "보유 유지, 추가매수는 새 매수 신호가 뜰 때까지 보류"

    def holding_action_line(self, decision_text: str, pnl_rate: float) -> str:
        return "권장 액션: " + self.holding_action_text(decision_text, pnl_rate)

    def ma_distance(self, position: Dict[str, object], period: int) -> float:
        return pct_delta(self.position_current_price(position), self.position_ma(position, period))

    def ma_comparison_text(self, position: Dict[str, object], period: int) -> str:
        ma_value = self.position_ma(position, period)
        if not ma_value:
            return ""
        label = str(period) + "일선 " + price_money(ma_value, self.position_currency(position))
        distance = self.ma_distance(position, period)
        value = compact_number(abs(distance))
        if distance > 0:
            return label + "보다 " + value + "% 높음"
        if distance < 0:
            return label + "보다 " + value + "% 낮음"
        return label + "과 같음"

    def trend_context_line(self, position: Dict[str, object]) -> str:
        price = self.position_current_price(position)
        ma20 = self.position_ma(position, 20)
        ma60 = self.position_ma(position, 60)
        if not price or not ma20 or not ma60:
            return ""
        return "추세: " + self.ma_comparison_text(position, 20) + ", " + self.ma_comparison_text(position, 60)

    def trend_slope_line(self, position: Dict[str, object]) -> str:
        slope20 = number(position.get("ma20_slope") if "ma20_slope" in position else position.get("ma20Slope"))
        slope60 = number(position.get("ma60_slope") if "ma60_slope" in position else position.get("ma60Slope"))
        parts = []
        if slope20:
            parts.append("20일선 " + signed_pct(slope20))
        if slope60:
            parts.append("60일선 " + signed_pct(slope60))
        if not parts:
            return ""
        return "기울기: " + ", ".join(parts)

    def trend_signals(self, before: Dict[str, object], item: Dict[str, object]) -> List[str]:
        signals: List[str] = []
        threshold = float(self.thresholds.get("monitorMaDistance", 0))
        for period in [20, 60]:
            if not self.position_ma(item, period) or not self.position_current_price(item):
                continue
            label = str(period) + "일선"
            before_has_ma = bool(self.position_ma(before, period) and self.position_current_price(before))
            before_distance = self.ma_distance(before, period) if before_has_ma else 0.0
            current_distance = self.ma_distance(item, period)
            if before_has_ma and before_distance <= 0 < current_distance:
                signals.append(label + " 상향 돌파")
            elif before_has_ma and before_distance >= 0 > current_distance:
                signals.append(label + " 하향 이탈")
            elif threshold and abs(current_distance) >= threshold and (not before_has_ma or abs(before_distance) < threshold):
                signals.append(self.ma_comparison_text(item, period))
        if self.position_ma(item, 20) and self.position_ma(item, 60):
            current_spread = pct_delta(self.position_ma(item, 20), self.position_ma(item, 60))
            before_has_cross = bool(self.position_ma(before, 20) and self.position_ma(before, 60))
            before_spread = pct_delta(self.position_ma(before, 20), self.position_ma(before, 60)) if before_has_cross else 0.0
            if before_has_cross and before_spread <= 0 < current_spread:
                signals.append("20/60일선 골든크로스")
            elif before_has_cross and before_spread >= 0 > current_spread:
                signals.append("20/60일선 데드크로스")
        return signals

    def trend_severity(self, signals: List[str]) -> str:
        joined = " ".join(signals)
        if "하향" in joined or "데드" in joined or "낮음" in joined:
            return "ALERT"
        return "WATCH"

    def toss_diagnostics(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        metadata = dict(getattr(snapshot, "metadata", {}) or {})
        toss = metadata.get("toss") if isinstance(metadata.get("toss"), dict) else {}
        return dict(toss or {})

    def toss_failure_stage(self, snapshot: AccountSnapshot) -> str:
        toss = self.toss_diagnostics(snapshot)
        stage_failures = toss.get("stageFailures") if isinstance(toss.get("stageFailures"), dict) else {}
        if stage_failures:
            return str(next(reversed(stage_failures.keys())) or "")
        status = str(snapshot.status or "")
        marker = "Toss "
        suffix = " 단계 실패"
        if marker in status and suffix in status:
            return status.split(marker, 1)[1].split(suffix, 1)[0].strip()
        return ""

    def connection_failure_streak(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> int:
        if snapshot.mode == "live" and not status_has_account_data_failure(snapshot.status):
            return 0
        previous_metadata = dict((previous or {}).get("metadata") or {})
        previous_streak = int(float(previous_metadata.get("connectionFailureStreak") or 0))
        previous_failed = (
            str((previous or {}).get("mode") or "").strip().lower() != "live"
            or status_has_account_data_failure((previous or {}).get("status"))
        )
        return (previous_streak if previous_failed else 0) + 1

    def set_connection_failure_streak(self, snapshot: AccountSnapshot, streak: int) -> None:
        metadata = dict(getattr(snapshot, "metadata", {}) or {})
        metadata["connectionFailureStreak"] = int(streak or 0)
        snapshot.metadata = metadata

    def connection_events(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        failure_streak = self.connection_failure_streak(snapshot, previous)
        self.set_connection_failure_streak(snapshot, failure_streak)
        if snapshot.mode != "live":
            repeated = failure_streak >= 2
            severity = "ALERT" if repeated else "WATCH"
            stage = self.toss_failure_stage(snapshot) or "-"
            toss = self.toss_diagnostics(snapshot)
            auth_refreshes = int(float(toss.get("authRefreshes") or 0))
            status_line = "연속 인증 실패" if repeated else "일시 인증 실패"
            retry_line = "재시도 access token 재발급 " + str(auth_refreshes) + "회" if auth_refreshes else ""
            lines = [
                "상태 " + status_line,
                "연속 실패 " + str(failure_streak) + "회",
                "실패 단계 " + stage,
            ]
            if retry_line:
                lines.append(retry_line)
            lines.append(snapshot.status or snapshot.mode)
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                severity,
                "monitorConnection",
                ":".join([snapshot.account_id, "connection", snapshot.mode, "repeated" if repeated else "single", snapshot.status]),
                "연결 상태",
                lines,
                criteria=self.criteria(
                    "토스 연결 모드가 live가 아니며 " + ("2회 이상 연속 실패할 때 주의로 보냅니다" if repeated else "1회성 실패는 관찰로 보냅니다"),
                    "연속 실패 " + str(failure_streak) + "회, stage=" + stage + ", mode=" + str(snapshot.mode or "-") + ", status=" + str(snapshot.status or "-"),
                ),
                metadata={
                    "connectionFailureStreak": failure_streak,
                    "tossFailureStage": stage,
                    "tossAuthRefreshes": auth_refreshes,
                },
            ))
        previous_status = previous.get("status") if previous else ""
        if previous_status and previous_status != snapshot.status:
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "WATCH",
                "monitorConnection",
                ":".join([snapshot.account_id, "connection-change", snapshot.status]),
                "연결 상태 변화",
                ["이전 " + str(previous_status), "현재 " + snapshot.status],
                criteria=self.criteria(
                    "직전 스냅샷의 토스 연결 상태와 현재 상태가 다를 때",
                    "이전 " + str(previous_status) + ", 현재 " + snapshot.status,
                ),
            ))
        return events

    def heartbeat_events(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        return [AlertEvent(
            snapshot.account_id,
            snapshot.account_label,
            "INFO",
            "monitorHeartbeat",
            ":".join([snapshot.account_id, "heartbeat", snapshot.generated_at]),
            "실시간 모니터링",
            [
                "모니터링 정상 작동",
                "상태 " + (snapshot.status or snapshot.mode),
                "보유 " + str(len([item for item in snapshot.positions if not item.is_cash()])) + "개",
                "평가 " + money(snapshot.portfolio.invested, "KRW"),
            ],
            criteria=self.criteria(
                "실시간 모니터링 워커 생존 확인 주기",
                "상태 " + (snapshot.status or snapshot.mode) + ", 보유 " + str(len([item for item in snapshot.positions if not item.is_cash()])) + "개",
            ),
        )]

    def position_change_events(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        current_positions = snapshot.to_monitor_state()["positions"]
        previous_positions = previous.get("positions") or {}
        previous_portfolio = previous.get("portfolio") or {}
        current_decisions = snapshot.to_monitor_state()["decisions"]
        previous_decisions = previous.get("decisions") or {}
        symbols = sorted(set(current_positions.keys()) | set(previous_positions.keys()))
        events: List[AlertEvent] = []
        for symbol in symbols:
            item = current_positions.get(symbol)
            before = previous_positions.get(symbol)
            if item and not before:
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "WATCH", "monitorPositionChange", snapshot.account_id + ":new:" + symbol, item["name"], ["새 보유 종목", *self.holding_price_lines(item, snapshot.portfolio), self.flow_context_line(item), self.investor_context_line(item)], symbol, criteria=self.criteria("직전 스냅샷에 없던 보유 종목이 현재 스냅샷에 생겼을 때", "신규 보유, 수량 " + str(item.get("quantity", 0)))))
                continue
            if before and not item:
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "WATCH", "monitorPositionChange", snapshot.account_id + ":removed:" + symbol, before["name"], ["보유 목록에서 사라졌습니다", *self.holding_price_lines(before, previous_portfolio), "매도/이관/데이터 변경 여부 확인"], symbol, criteria=self.criteria("직전 스냅샷에 있던 보유 종목이 현재 보유 목록에서 사라졌을 때", "보유 제외 감지")))
                continue
            if not item or not before:
                continue
            quantity_delta = float(item.get("quantity") or 0) - float(before.get("quantity") or 0)
            if quantity_delta:
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "WATCH", "monitorPositionChange", snapshot.account_id + ":quantity:" + symbol + ":" + str(item.get("quantity")), item["name"], ["보유 수량 변경", "이전 " + str(before.get("quantity", 0)), "현재 " + str(item.get("quantity", 0)), *self.holding_price_lines(item, snapshot.portfolio), self.flow_context_line(item), self.investor_context_line(item)], symbol, criteria=self.criteria("직전 스냅샷 대비 보유 수량이 달라졌을 때", "이전 " + str(before.get("quantity", 0)) + ", 현재 " + str(item.get("quantity", 0)))))
            pnl_delta = float(item.get("profit_loss_rate") or 0) - float(before.get("profit_loss_rate") or 0)
            if abs(pnl_delta) >= float(self.thresholds.get("monitorPnlDelta", 0)):
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "ALERT" if pnl_delta < 0 else "WATCH", "monitorPnlChange", snapshot.account_id + ":pnl:" + symbol + ":" + signed_pct(pnl_delta), item["name"], ["손익률 급변", "이전 " + signed_pct(float(before.get("profit_loss_rate") or 0)), "현재 " + signed_pct(float(item.get("profit_loss_rate") or 0)), "변화 " + signed_pct(pnl_delta, "%p"), *self.holding_price_lines(item, snapshot.portfolio), self.flow_context_line(item), self.investor_context_line(item), self.trend_context_line(item)], symbol, criteria=self.criteria("손익률 변화폭 ±" + self.threshold_text("monitorPnlDelta", "%p") + " 이상", "변화 " + signed_pct(pnl_delta, "%p") + ", 이전 " + signed_pct(float(before.get("profit_loss_rate") or 0)) + ", 현재 " + signed_pct(float(item.get("profit_loss_rate") or 0)))))
            value_delta = pct_delta(self.position_value_base(item), self.position_value_base(before))
            if abs(value_delta) >= float(self.thresholds.get("monitorValueDelta", 0)):
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "ALERT" if value_delta < 0 else "WATCH", "monitorValueChange", snapshot.account_id + ":value:" + symbol + ":" + signed_pct(value_delta), item["name"], ["평가액 급변", "이전 " + self.position_value_label(before), "현재 " + self.position_value_label(item), "변화 " + signed_pct(value_delta) + self.value_delta_basis_label(before, item), *self.holding_price_lines(item, snapshot.portfolio), self.flow_context_line(item), self.investor_context_line(item), self.trend_context_line(item)], symbol, criteria=self.criteria("평가액 변화율 ±" + self.threshold_text("monitorValueDelta", "%") + " 이상", "변화 " + signed_pct(value_delta) + ", 이전 " + self.position_value_label(before) + ", 현재 " + self.position_value_label(item))))
            trend_signals = self.trend_signals(before, item)
            if trend_signals:
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, self.trend_severity(trend_signals), "monitorTrendChange", snapshot.account_id + ":trend:" + symbol + ":" + ",".join(trend_signals[:2]), item["name"], ["이동평균 변화", *self.holding_price_lines(item, snapshot.portfolio), "신호 " + " · ".join(trend_signals), self.trend_context_line(item), self.trend_slope_line(item), self.flow_context_line(item), self.investor_context_line(item)], symbol, criteria=self.criteria("20일/60일 이동평균 돌파, 크로스, 또는 현재가가 이동평균보다 " + self.threshold_text("monitorMaDistance", "%") + " 이상 높거나 낮을 때", "신호 " + " · ".join(trend_signals))))
            decision = current_decisions.get(symbol) or {}
            previous_decision = previous_decisions.get(symbol) or {}
            pressure_delta = float(decision.get("exit_pressure") or 0) - float(previous_decision.get("exit_pressure") or 0)
            changed = self.meaningful_decision_change(decision, previous_decision, pressure_delta)
            if changed or abs(pressure_delta) >= float(self.thresholds.get("monitorExitPressureDelta", 0)):
                previous_phrase = self.decision_score_phrase(previous_decision.get("decision") or "-", previous_decision.get("exit_pressure"))
                current_phrase = self.decision_score_phrase(decision.get("decision") or "-", decision.get("exit_pressure"))
                legacy_formula_audits = self.holding_formula_audits(snapshot, item, decision)
                review_lines = decision_change_review_lines(
                    item,
                    before,
                    decision,
                    previous_decision,
                    float(self.thresholds.get("monitorExitPressureDelta", 0)),
                )
                decision_context = decision_change_context(
                    decision,
                    previous_decision,
                    float(self.thresholds.get("monitorExitPressureDelta", 0)),
                )
                relation_context = self.relation_context_from_decision(decision)
                prompt_context = self.prompt_context_from_decision(decision)
                relation_lines = self.relation_context_lines(decision)
                ontology_lines = self.ontology_context_lines(decision)
                active_lines = self.active_investment_opinion_lines(decision)
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "ALERT" if decision.get("tone") == "danger" else "WATCH", "monitorDecisionChange", snapshot.account_id + ":decision:" + symbol + ":" + str(decision.get("decision")), item["name"], ["보유 종목 판단 변화", "이전 " + previous_phrase, "현재 " + current_phrase, *self.holding_price_lines(item, snapshot.portfolio), self.holding_action_line(decision.get("decision") or "", float(item.get("profit_loss_rate") or 0)), self.flow_context_line(item), self.investor_context_line(item), self.trend_context_line(item)] + relation_lines + ontology_lines + active_lines + review_lines, symbol, criteria=self.criteria("보유 종목의 대응 액션 그룹이 바뀌거나 같은 그룹 내 판단 점수 변화가 " + self.threshold_text("monitorDecisionLabelBuffer", "점") + " 이상, 또는 관계 신호 강도 변화가 " + self.threshold_text("monitorExitPressureDelta", "점") + " 이상일 때", "이전 " + previous_phrase + ", 현재 " + current_phrase + (", " + " · ".join(relation_lines[:2]) if relation_lines else "")), metadata={
                    "holdingDecision": decision.get("decision") or "",
                    "holdingDecisionBasis": decision.get("decision_basis") or "",
                    "holdingDecisionScore": round(float(decision.get("exit_pressure") or 0), 1),
                    "profitLossRate": round(float(item.get("profit_loss_rate") or 0), 2),
                    "legacyFormulaAudits": legacy_formula_audits,
                    "decisionChangeContext": decision_context,
                    "ontologyRelationContext": relation_context,
                    "ontologyPromptContext": prompt_context,
                    "ontologyOpinion": self.ontology_opinion_from_decision(decision),
                    "ontologyWorldview": self.ontology_worldview_from_decision(decision),
                    "activeInvestmentOpinion": self.active_investment_opinion_from_decision(decision),
                    "ontologyReviewContext": self.ai_context_from_decision(decision),
                }))
        return events

    def cash_events(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        previous_markets = {item.get("key") or item.get("label"): item for item in (previous.get("portfolio", {}).get("markets") or [])}
        events: List[AlertEvent] = []
        for market in snapshot.portfolio.markets:
            key = str(market.get("key") or market.get("label"))
            before = previous_markets.get(key)
            if not before:
                continue
            ratio_delta = float(market.get("cashRatio") or 0) - float(before.get("cashRatio") or 0)
            if abs(ratio_delta) >= float(self.thresholds.get("monitorCashDelta", 0)):
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "ALERT" if ratio_delta < 0 else "WATCH", "monitorCashChange", snapshot.account_id + ":cash:" + key + ":" + signed_pct(ratio_delta, "p"), "현금비중", [str(market.get("label") or key), "이전 " + signed_pct(float(before.get("cashRatio") or 0)), "현재 " + signed_pct(float(market.get("cashRatio") or 0)), "변화 " + signed_pct(ratio_delta, "%p")], criteria=self.criteria("시장별 현금 비중 변화폭 ±" + self.threshold_text("monitorCashDelta", "%p") + " 이상", "변화 " + signed_pct(ratio_delta, "%p") + ", 이전 " + signed_pct(float(before.get("cashRatio") or 0)) + ", 현재 " + signed_pct(float(market.get("cashRatio") or 0)))))
        return events

    def watchlist_quote_events(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        current_watchlist = snapshot.to_monitor_state().get("watchlist") or {}
        previous_watchlist = previous.get("watchlist") or {}
        events: List[AlertEvent] = []
        threshold = float(self.thresholds.get("watchlistPriceDelta", 0))
        for symbol in sorted(current_watchlist.keys()):
            item = current_watchlist.get(symbol) or {}
            before = previous_watchlist.get(symbol) or {}
            price = self.position_current_price(item)
            before_price = self.position_current_price(before) if isinstance(before, dict) else 0.0
            name = str(item.get("name") or symbol)
            currency = self.position_currency(item)
            if not price:
                if before and not before_price:
                    continue
                events.append(AlertEvent(
                    snapshot.account_id,
                    snapshot.account_label,
                    "INFO",
                    "watchlistQuotePending",
                    ":".join([snapshot.account_id, "watchlist-pending", symbol]),
                    name,
                    [
                        "관심종목 시세 대기",
                        "현재가를 아직 받지 못했습니다.",
                        "토스 candles 응답, 종목 코드, 허용 IP를 확인하세요.",
                    ],
                    symbol,
                    criteria=self.criteria(
                        "관심종목 현재가가 아직 수집되지 않았을 때",
                        "현재가 없음, Toss candles 응답 확인 필요",
                    ),
                ))
                continue
            price_delta = pct_delta(price, before_price)
            if before_price and threshold and abs(price_delta) < threshold:
                continue
            lines = [
                "관심종목 시세 수집",
                self.current_price_line(item),
            ]
            if before_price:
                lines.append("직전 " + price_money(before_price, currency) + " · 변화 " + signed_pct(price_delta))
            lines.extend([
                self.flow_context_line(item),
                self.trend_context_line(item),
                "관심종목 알림 기준과 매수 후보를 확인하세요.",
            ])
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "ALERT" if before_price and price_delta < 0 else "WATCH",
                "watchlistQuote",
                ":".join([snapshot.account_id, "watchlist-quote", symbol, signed_pct(price_delta)]),
                name,
                [line for line in lines if line],
                symbol,
                criteria=self.criteria(
                    "관심종목 가격 변화율 ±" + self.threshold_text("watchlistPriceDelta", "%") + " 이상",
                    ("변화 " + signed_pct(price_delta) + ", 현재가 " + price_money(price, currency)) if before_price else "현재가 " + price_money(price, currency) + " 수집",
                ),
            ))
        return events

    def holding_timing_events(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        positions = {item.symbol.upper(): item.to_dict() for item in snapshot.positions if not item.is_cash()}
        loss_threshold = float(self.relation_thresholds.get("lossRateLow", -8.0) or -8.0)
        loss_buffer = abs(float(self.relation_thresholds.get("lossRateBufferPct", 1.0) or 0.0))
        forced_loss_threshold = loss_threshold - loss_buffer
        for item in snapshot.decisions:
            if item.tone not in {"danger", "caution"} and item.profit_loss_rate > forced_loss_threshold:
                continue
            position = positions.get(item.symbol.upper()) or item.to_dict()
            decision_phrase = self.decision_score_phrase(item.decision, item.exit_pressure)
            decision_state = item.to_dict()
            legacy_formula_audits = self.holding_formula_audits(snapshot, position, decision_state)
            relation_context = self.relation_context_from_decision(decision_state)
            prompt_context = self.prompt_context_from_decision(decision_state)
            relation_lines = self.relation_context_lines(decision_state)
            ontology_lines = self.ontology_context_lines(decision_state)
            active_lines = self.active_investment_opinion_lines(decision_state)
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "ALERT" if item.tone == "danger" else "WATCH",
                "holdingTiming",
                snapshot.account_id + ":timing:" + item.symbol + ":" + item.decision,
                item.name,
                ["상태 " + decision_phrase, *self.holding_price_lines(position, snapshot.portfolio), self.flow_context_line(position), self.investor_context_line(position), self.trend_context_line(position), self.holding_action_line(item.decision, item.profit_loss_rate)] + relation_lines + ontology_lines + active_lines,
                item.symbol,
                criteria=self.criteria(
                    "관계 규칙이 위험/주의 상태로 성립하거나 손익률이 손실 기준 "
                    + compact_number(loss_threshold)
                    + "%에서 완충 "
                    + compact_number(loss_buffer)
                    + "%p 이상 더 악화될 때",
                    "상태 " + decision_phrase + ", 수익률 " + signed_pct(item.profit_loss_rate) + (", " + " · ".join(relation_lines[:2]) if relation_lines else ""),
                ),
                metadata={
                    "holdingDecision": item.decision,
                    "holdingDecisionBasis": item.decision_basis,
                    "holdingDecisionScore": round(float(item.exit_pressure or 0), 1),
                    "profitLossRate": round(float(item.profit_loss_rate or 0), 2),
                    "legacyFormulaAudits": legacy_formula_audits,
                    "ontologyRelationContext": relation_context,
                    "ontologyPromptContext": prompt_context,
                    "ontologyOpinion": dict(item.ontology_opinion or {}),
                    "ontologyWorldview": dict(item.ontology_worldview or {}),
                    "activeInvestmentOpinion": dict(item.active_investment_opinion or {}),
                    "ontologyReviewContext": dict(item.ai_context or {}),
                },
            ))
        return events

    def apply_cadence(self, events: List[AlertEvent], store: MonitorStateRepository, force: bool = False) -> List[AlertEvent]:
        if force:
            return events
        filtered: List[AlertEvent] = []
        now = now_ms()
        for event in events:
            minutes = self.dispatch_cadence_minutes(event)
            sent_at = store.sent.get(self.dispatch_cadence_key(event))
            if not sent_at:
                filtered.append(event)
                continue
            try:
                previous = datetime.fromisoformat(str(sent_at).replace("Z", "+00:00")).timestamp() * 1000
            except ValueError:
                filtered.append(event)
                continue
            if now - previous >= minutes * 60 * 1000:
                filtered.append(event)
        return filtered
