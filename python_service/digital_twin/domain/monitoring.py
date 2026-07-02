from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, List

from .analytics import DEFAULT_FX_RATES, number, value_in_base
from .model_review import decision_change_review_lines
from .parsing import parse_assignments
from .portfolio import AccountSnapshot, AlertEvent
from .repositories import MonitorStateRepository


DEFAULT_ALERT_RULES = {
    "modelBuy": 1,
    "modelSell": 1,
    "holdingTiming": 1,
    "monitorHeartbeat": 1,
    "monitorConnection": 1,
    "monitorPositionChange": 1,
    "monitorPnlChange": 1,
    "monitorValueChange": 1,
    "monitorTrendChange": 1,
    "monitorCashChange": 1,
    "monitorDecisionChange": 1,
}

DEFAULT_THRESHOLDS = {
    "monitorPnlDelta": 2,
    "monitorValueDelta": 5,
    "monitorMaDistance": 8,
    "monitorCashDelta": 10,
    "monitorExitPressureDelta": 15,
}

DEFAULT_CADENCE = {
    "modelBuy": 10,
    "modelSell": 10,
    "holdingTiming": 10,
    "monitorHeartbeat": 10,
    "monitorConnection": 10,
    "monitorPositionChange": 10,
    "monitorPnlChange": 10,
    "monitorValueChange": 10,
    "monitorTrendChange": 10,
    "monitorCashChange": 10,
    "monitorDecisionChange": 10,
}

MIN_CADENCE_MINUTES = 10


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def money(value: float, currency: str = "KRW") -> str:
    amount = number(value)
    code = str(currency or "KRW").upper()
    if amount <= 0:
        return "-"
    if code == "USD":
        if amount >= 1000:
            return "$" + format(round(amount), ",")
        return "$" + format(round(amount, 2), ",").rstrip("0").rstrip(".")
    if code != "KRW":
        if amount >= 1000:
            return format(round(amount), ",") + " " + code
        return format(round(amount, 2), ",").rstrip("0").rstrip(".") + " " + code
    if amount >= 100000000:
        return str(round(amount / 100000000)) + "억 원"
    if amount >= 10000:
        return format(round(amount / 10000), ",") + "만 원"
    return format(round(amount), ",") + "원"


def signed_pct(value: float, suffix: str = "%") -> str:
    number = round(float(value or 0), 1)
    return ("+" if number > 0 else "") + str(number) + suffix


def pct_delta(current: float, previous: float) -> float:
    base = float(previous or 0)
    if not base:
        return 0.0
    return ((float(current or 0) / base) - 1) * 100


def compact_number(value: float) -> str:
    amount = number(value)
    if not amount:
        return "-"
    rounded = round(amount, 1)
    if rounded == round(rounded):
        return format(round(rounded), ",")
    return format(rounded, ",")


class RealtimeMonitor:
    def __init__(self, settings: Dict[str, str] = None):
        settings = settings or {}
        self.rules = parse_assignments(settings.get("alertRules", ""), DEFAULT_ALERT_RULES)
        self.thresholds = parse_assignments(settings.get("alertThresholds", ""), DEFAULT_THRESHOLDS)
        self.cadence = parse_assignments(settings.get("alertCadenceMinutes", ""), DEFAULT_CADENCE)
        self.fx_rates = {
            str(key).upper(): float(value or 0)
            for key, value in parse_assignments(settings.get("fxRates", ""), DEFAULT_FX_RATES).items()
        }

    def enabled(self, rule: str) -> bool:
        return self.rules.get(rule, 1) != 0

    def rule_cadence_minutes(self, rule: str) -> int:
        value = int(self.cadence.get(rule, DEFAULT_CADENCE.get(rule, MIN_CADENCE_MINUTES)) or 0)
        return max(MIN_CADENCE_MINUTES, value)

    def events_for_snapshot(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        events.extend(self.connection_events(snapshot, previous))
        events.extend(self.heartbeat_events(snapshot))
        if previous:
            events.extend(self.position_change_events(snapshot, previous))
            events.extend(self.cash_events(snapshot, previous))
        events.extend(self.holding_timing_events(snapshot))
        return [event for event in events if self.enabled(event.rule)]

    def type_check_events_for_snapshot(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        events.extend(self.connection_events(snapshot, {"status": "이전 연결 상태"}))
        events.extend(self.heartbeat_events(snapshot))

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
        timing_events = self.holding_timing_events(snapshot)
        if not timing_events and snapshot.decisions:
            timing_snapshot = replace(snapshot, decisions=[
                replace(snapshot.decisions[0], tone="caution", decision=snapshot.decisions[0].decision or "조건부 보유"),
                *snapshot.decisions[1:],
            ])
            timing_events = self.holding_timing_events(timing_snapshot)
        events.extend(self.only_rule("holdingTiming", timing_events))
        return self.unique_rules([event for event in events if self.enabled(event.rule)])

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

    def position_current_price(self, position: Dict[str, object]) -> float:
        return number(position.get("current_price") if "current_price" in position else position.get("currentPrice"))

    def position_ma(self, position: Dict[str, object], period: int) -> float:
        return number(position.get("ma" + str(period)) or position.get("movingAverage" + str(period)) or position.get("sma" + str(period)))

    def position_volume(self, position: Dict[str, object]) -> float:
        return number(position.get("volume"))

    def position_trade_strength(self, position: Dict[str, object]) -> float:
        return number(position.get("trade_strength") if "trade_strength" in position else position.get("tradeStrength"))

    def position_trading_value(self, position: Dict[str, object]) -> float:
        value = number(position.get("trading_value") if "trading_value" in position else position.get("tradingValue"))
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
        trading_value = self.position_trading_value(position)
        trading_value_label = money(trading_value, self.position_currency(position)) if trading_value else "-"
        return "수급 체결강도 " + compact_number(self.position_trade_strength(position)) + " · 거래액 " + trading_value_label

    def ma_distance(self, position: Dict[str, object], period: int) -> float:
        return pct_delta(self.position_current_price(position), self.position_ma(position, period))

    def trend_context_line(self, position: Dict[str, object]) -> str:
        price = self.position_current_price(position)
        ma20 = self.position_ma(position, 20)
        ma60 = self.position_ma(position, 60)
        if not price or not ma20 or not ma60:
            return ""
        currency = self.position_currency(position)
        return (
            "추세 현재 " + money(price, currency)
            + " · 20일선 " + money(ma20, currency) + "(" + signed_pct(self.ma_distance(position, 20)) + ")"
            + " · 60일선 " + money(ma60, currency) + "(" + signed_pct(self.ma_distance(position, 60)) + ")"
        )

    def trend_slope_line(self, position: Dict[str, object]) -> str:
        slope20 = number(position.get("ma20_slope") if "ma20_slope" in position else position.get("ma20Slope"))
        slope60 = number(position.get("ma60_slope") if "ma60_slope" in position else position.get("ma60Slope"))
        if not slope20 and not slope60:
            return ""
        return "기울기 20일선 " + signed_pct(slope20) + " · 60일선 " + signed_pct(slope60)

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
                signals.append(label + " 괴리 " + signed_pct(current_distance))
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
        if "하향" in joined or "데드" in joined or "괴리 -" in joined:
            return "ALERT"
        return "WATCH"

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

    def connection_events(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        if snapshot.mode != "live":
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "ALERT",
                "monitorConnection",
                ":".join([snapshot.account_id, "connection", snapshot.mode, snapshot.status]),
                "연결 상태",
                ["토스 연결 상태가 live가 아닙니다", snapshot.status or snapshot.mode],
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
        )]

    def position_change_events(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        current_positions = snapshot.to_monitor_state()["positions"]
        previous_positions = previous.get("positions") or {}
        current_decisions = snapshot.to_monitor_state()["decisions"]
        previous_decisions = previous.get("decisions") or {}
        symbols = sorted(set(current_positions.keys()) | set(previous_positions.keys()))
        events: List[AlertEvent] = []
        for symbol in symbols:
            item = current_positions.get(symbol)
            before = previous_positions.get(symbol)
            if item and not before:
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "WATCH", "monitorPositionChange", snapshot.account_id + ":new:" + symbol, item["name"], ["새 보유 종목", "수량 " + str(item.get("quantity", 0)), "평가 " + self.position_value_label(item)], symbol))
                continue
            if before and not item:
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "WATCH", "monitorPositionChange", snapshot.account_id + ":removed:" + symbol, before["name"], ["보유 목록에서 사라졌습니다", "매도/이관/데이터 변경 여부 확인"], symbol))
                continue
            if not item or not before:
                continue
            quantity_delta = float(item.get("quantity") or 0) - float(before.get("quantity") or 0)
            if quantity_delta:
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "WATCH", "monitorPositionChange", snapshot.account_id + ":quantity:" + symbol + ":" + str(item.get("quantity")), item["name"], ["보유 수량 변경", "이전 " + str(before.get("quantity", 0)), "현재 " + str(item.get("quantity", 0))], symbol))
            pnl_delta = float(item.get("profit_loss_rate") or 0) - float(before.get("profit_loss_rate") or 0)
            if abs(pnl_delta) >= float(self.thresholds.get("monitorPnlDelta", 0)):
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "ALERT" if pnl_delta < 0 else "WATCH", "monitorPnlChange", snapshot.account_id + ":pnl:" + symbol + ":" + signed_pct(pnl_delta), item["name"], ["손익률 급변", "이전 " + signed_pct(float(before.get("profit_loss_rate") or 0)), "현재 " + signed_pct(float(item.get("profit_loss_rate") or 0)), "변화 " + signed_pct(pnl_delta, "%p"), self.flow_context_line(item), self.trend_context_line(item)], symbol))
            value_delta = pct_delta(self.position_value_base(item), self.position_value_base(before))
            if abs(value_delta) >= float(self.thresholds.get("monitorValueDelta", 0)):
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "ALERT" if value_delta < 0 else "WATCH", "monitorValueChange", snapshot.account_id + ":value:" + symbol + ":" + signed_pct(value_delta), item["name"], ["평가액 급변", "이전 " + self.position_value_label(before), "현재 " + self.position_value_label(item), "변화 " + signed_pct(value_delta) + self.value_delta_basis_label(before, item), self.flow_context_line(item), self.trend_context_line(item)], symbol))
            trend_signals = self.trend_signals(before, item)
            if trend_signals:
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, self.trend_severity(trend_signals), "monitorTrendChange", snapshot.account_id + ":trend:" + symbol + ":" + ",".join(trend_signals[:2]), item["name"], ["이동평균 변화", "신호 " + " · ".join(trend_signals), self.trend_context_line(item), self.trend_slope_line(item), self.flow_context_line(item)], symbol))
            decision = current_decisions.get(symbol) or {}
            previous_decision = previous_decisions.get(symbol) or {}
            pressure_delta = float(decision.get("exit_pressure") or 0) - float(previous_decision.get("exit_pressure") or 0)
            changed = decision.get("decision") and previous_decision.get("decision") and decision.get("decision") != previous_decision.get("decision")
            if changed or abs(pressure_delta) >= float(self.thresholds.get("monitorExitPressureDelta", 0)):
                review_lines = decision_change_review_lines(
                    item,
                    before,
                    decision,
                    previous_decision,
                    float(self.thresholds.get("monitorExitPressureDelta", 0)),
                )
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "ALERT" if decision.get("tone") == "danger" else "WATCH", "monitorDecisionChange", snapshot.account_id + ":decision:" + symbol + ":" + str(decision.get("decision")), item["name"], ["판단 변화", "이전 " + str(previous_decision.get("decision") or "-") + " " + str(previous_decision.get("exit_pressure") or 0) + "점", "현재 " + str(decision.get("decision") or "-") + " " + str(decision.get("exit_pressure") or 0) + "점"] + review_lines, symbol))
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
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "ALERT" if ratio_delta < 0 else "WATCH", "monitorCashChange", snapshot.account_id + ":cash:" + key + ":" + signed_pct(ratio_delta, "p"), "현금비중", [str(market.get("label") or key), "이전 " + signed_pct(float(before.get("cashRatio") or 0)), "현재 " + signed_pct(float(market.get("cashRatio") or 0)), "변화 " + signed_pct(ratio_delta, "%p")]))
        return events

    def holding_timing_events(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        for item in snapshot.decisions:
            if item.tone not in {"danger", "caution"} and item.profit_loss_rate > -8:
                continue
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "ALERT" if item.tone == "danger" else "WATCH",
                "holdingTiming",
                snapshot.account_id + ":timing:" + item.symbol + ":" + item.decision,
                item.name,
                ["상태 " + item.decision, "손익 " + signed_pct(item.profit_loss_rate), "매도/매수 기준 재확인"],
                item.symbol,
            ))
        return events

    def apply_cadence(self, events: List[AlertEvent], store: MonitorStateRepository, force: bool = False) -> List[AlertEvent]:
        if force:
            return events
        filtered: List[AlertEvent] = []
        now = now_ms()
        for event in events:
            minutes = self.rule_cadence_minutes(event.rule)
            sent_at = store.sent.get(event.cadence_key())
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
