from datetime import datetime, timezone
from typing import Dict, List

from .parsing import parse_assignments
from .portfolio import AccountSnapshot, AlertEvent
from .repositories import MonitorStateRepository


DEFAULT_ALERT_RULES = {
    "holdingTiming": 1,
    "monitorHeartbeat": 1,
    "monitorConnection": 1,
    "monitorPositionChange": 1,
    "monitorPnlChange": 1,
    "monitorValueChange": 1,
    "monitorCashChange": 1,
    "monitorDecisionChange": 1,
}

DEFAULT_THRESHOLDS = {
    "monitorPnlDelta": 2,
    "monitorValueDelta": 5,
    "monitorCashDelta": 10,
    "monitorExitPressureDelta": 15,
}

DEFAULT_CADENCE = {
    "holdingTiming": 10,
    "monitorHeartbeat": 60,
    "monitorConnection": 10,
    "monitorPositionChange": 10,
    "monitorPnlChange": 30,
    "monitorValueChange": 30,
    "monitorCashChange": 60,
    "monitorDecisionChange": 30,
}

MIN_CADENCE_MINUTES = 10


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def money(value: float) -> str:
    amount = float(value or 0)
    if amount <= 0:
        return "-"
    if amount >= 100000000:
        return str(round(amount / 100000000)) + "억"
    if amount >= 10000:
        return format(round(amount / 10000), ",") + "만"
    return format(round(amount), ",")


def signed_pct(value: float, suffix: str = "%") -> str:
    number = round(float(value or 0), 1)
    return ("+" if number > 0 else "") + str(number) + suffix


def pct_delta(current: float, previous: float) -> float:
    base = float(previous or 0)
    if not base:
        return 0.0
    return ((float(current or 0) / base) - 1) * 100


class RealtimeMonitor:
    def __init__(self, settings: Dict[str, str] = None):
        settings = settings or {}
        self.rules = parse_assignments(settings.get("alertRules", ""), DEFAULT_ALERT_RULES)
        self.thresholds = parse_assignments(settings.get("alertThresholds", ""), DEFAULT_THRESHOLDS)
        self.cadence = parse_assignments(settings.get("alertCadenceMinutes", ""), DEFAULT_CADENCE)

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
                "평가 " + money(snapshot.portfolio.invested),
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
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "WATCH", "monitorPositionChange", snapshot.account_id + ":new:" + symbol, item["name"], ["새 보유 종목", "수량 " + str(item.get("quantity", 0)), "평가 " + money(float(item.get("market_value", 0)))], symbol))
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
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "ALERT" if pnl_delta < 0 else "WATCH", "monitorPnlChange", snapshot.account_id + ":pnl:" + symbol + ":" + signed_pct(pnl_delta), item["name"], ["손익률 급변", "이전 " + signed_pct(float(before.get("profit_loss_rate") or 0)), "현재 " + signed_pct(float(item.get("profit_loss_rate") or 0)), "변화 " + signed_pct(pnl_delta, "%p")], symbol))
            value_delta = pct_delta(float(item.get("market_value") or 0), float(before.get("market_value") or 0))
            if abs(value_delta) >= float(self.thresholds.get("monitorValueDelta", 0)):
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "ALERT" if value_delta < 0 else "WATCH", "monitorValueChange", snapshot.account_id + ":value:" + symbol + ":" + signed_pct(value_delta), item["name"], ["평가액 급변", "이전 " + money(float(before.get("market_value") or 0)), "현재 " + money(float(item.get("market_value") or 0)), "변화 " + signed_pct(value_delta)], symbol))
            decision = current_decisions.get(symbol) or {}
            previous_decision = previous_decisions.get(symbol) or {}
            pressure_delta = float(decision.get("exit_pressure") or 0) - float(previous_decision.get("exit_pressure") or 0)
            changed = decision.get("decision") and previous_decision.get("decision") and decision.get("decision") != previous_decision.get("decision")
            if changed or abs(pressure_delta) >= float(self.thresholds.get("monitorExitPressureDelta", 0)):
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "ALERT" if decision.get("tone") == "danger" else "WATCH", "monitorDecisionChange", snapshot.account_id + ":decision:" + symbol + ":" + str(decision.get("decision")), item["name"], ["판단 변화", "이전 " + str(previous_decision.get("decision") or "-") + " " + str(previous_decision.get("exit_pressure") or 0) + "점", "현재 " + str(decision.get("decision") or "-") + " " + str(decision.get("exit_pressure") or 0) + "점"], symbol))
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

