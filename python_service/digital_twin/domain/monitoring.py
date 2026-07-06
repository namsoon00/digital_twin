from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, List

from .alert_formatting import compact_number, money, pct_delta, signed_number, signed_pct
from .market_data import number
from .message_types import (
    DEFAULT_ALERT_RULES,
    DEFAULT_ALERT_THRESHOLDS,
    DEFAULT_CADENCE,
    MIN_CADENCE_MINUTES,
)
from .model_review import decision_change_review_lines
from .parsing import parse_assignments
from .portfolio import AccountSnapshot, AlertEvent, monitor_state_has_live_account_data, status_has_account_data_failure
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

    def events_for_snapshot(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        snapshot = self.snapshot_with_strategy_scores(snapshot)
        has_account_data = snapshot.has_live_account_data()
        previous_has_account_data = monitor_state_has_live_account_data(previous)
        events.extend(self.connection_events(snapshot, previous))
        events.extend(self.heartbeat_events(snapshot))
        if has_account_data:
            events.extend(self.model_score_events(snapshot))
        if has_account_data and previous_has_account_data:
            events.extend(self.position_change_events(snapshot, previous))
            events.extend(self.cash_events(snapshot, previous))
        events.extend(self.watchlist_quote_events(snapshot, previous or {}))
        events.extend(self.external_signal_events(snapshot, previous or {}))
        if has_account_data:
            events.extend(self.holding_timing_events(snapshot))
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
        return self.unique_rules([event for event in self.stamp_events(snapshot, events) if self.enabled(event.rule)])

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
        snapshot.decisions = decisions_for_positions(snapshot.positions, snapshot.portfolio, self.strategy_model)
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

    def position_current_price(self, position: Dict[str, object]) -> float:
        return number(position.get("current_price") if "current_price" in position else position.get("currentPrice"))

    def position_ma(self, position: Dict[str, object], period: int) -> float:
        return number(position.get("ma" + str(period)) or position.get("movingAverage" + str(period)) or position.get("sma" + str(period)))

    def position_volume(self, position: Dict[str, object]) -> float:
        return number(position.get("volume"))

    def position_volume_ratio(self, position: Dict[str, object]) -> float:
        return number(position.get("volume_ratio") if "volume_ratio" in position else position.get("volumeRatio"))

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
        if not parts:
            return ""
        return "수급: " + ", ".join(parts)

    def investor_value(self, position: Dict[str, object], snake_key: str, camel_key: str) -> float:
        return number(position.get(snake_key) if snake_key in position else position.get(camel_key))

    def investor_summary(self, label: str, buy: float, sell: float, net: float) -> str:
        if buy or sell:
            effective_net = net if net else buy - sell
            return label + " " + signed_number(effective_net) + "(매수 " + compact_number(buy) + "/매도 " + compact_number(sell) + ")"
        if net:
            return label + " " + signed_number(net)
        return ""

    def investor_context_line(self, position: Dict[str, object]) -> str:
        foreign_buy = self.investor_value(position, "foreign_buy_volume", "foreignBuyVolume")
        foreign_sell = self.investor_value(position, "foreign_sell_volume", "foreignSellVolume")
        foreign_net = self.investor_value(position, "foreign_net_volume", "foreignNetVolume")
        institution_buy = self.investor_value(position, "institution_buy_volume", "institutionBuyVolume")
        institution_sell = self.investor_value(position, "institution_sell_volume", "institutionSellVolume")
        institution_net = self.investor_value(position, "institution_net_volume", "institutionNetVolume")
        summaries = [
            self.investor_summary("외국인", foreign_buy, foreign_sell, foreign_net),
            self.investor_summary("기관", institution_buy, institution_sell, institution_net),
        ]
        parts = [summary for summary in summaries if summary]
        if not parts:
            return ""
        return "투자자: " + ", ".join(parts)

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
            "추세: 현재 " + money(price, currency)
            + ", 20일선 " + money(ma20, currency) + "(" + signed_pct(self.ma_distance(position, 20)) + ")"
            + ", 60일선 " + money(ma60, currency) + "(" + signed_pct(self.ma_distance(position, 60)) + ")"
        )

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
        current_decisions = snapshot.to_monitor_state()["decisions"]
        previous_decisions = previous.get("decisions") or {}
        symbols = sorted(set(current_positions.keys()) | set(previous_positions.keys()))
        events: List[AlertEvent] = []
        for symbol in symbols:
            item = current_positions.get(symbol)
            before = previous_positions.get(symbol)
            if item and not before:
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "WATCH", "monitorPositionChange", snapshot.account_id + ":new:" + symbol, item["name"], ["새 보유 종목", "수량 " + str(item.get("quantity", 0)), "평가 " + self.position_value_label(item), self.flow_context_line(item), self.investor_context_line(item)], symbol, criteria=self.criteria("직전 스냅샷에 없던 보유 종목이 현재 스냅샷에 생겼을 때", "신규 보유, 수량 " + str(item.get("quantity", 0)))))
                continue
            if before and not item:
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "WATCH", "monitorPositionChange", snapshot.account_id + ":removed:" + symbol, before["name"], ["보유 목록에서 사라졌습니다", "매도/이관/데이터 변경 여부 확인"], symbol, criteria=self.criteria("직전 스냅샷에 있던 보유 종목이 현재 보유 목록에서 사라졌을 때", "보유 제외 감지")))
                continue
            if not item or not before:
                continue
            quantity_delta = float(item.get("quantity") or 0) - float(before.get("quantity") or 0)
            if quantity_delta:
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "WATCH", "monitorPositionChange", snapshot.account_id + ":quantity:" + symbol + ":" + str(item.get("quantity")), item["name"], ["보유 수량 변경", "이전 " + str(before.get("quantity", 0)), "현재 " + str(item.get("quantity", 0)), self.flow_context_line(item), self.investor_context_line(item)], symbol, criteria=self.criteria("직전 스냅샷 대비 보유 수량이 달라졌을 때", "이전 " + str(before.get("quantity", 0)) + ", 현재 " + str(item.get("quantity", 0)))))
            pnl_delta = float(item.get("profit_loss_rate") or 0) - float(before.get("profit_loss_rate") or 0)
            if abs(pnl_delta) >= float(self.thresholds.get("monitorPnlDelta", 0)):
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "ALERT" if pnl_delta < 0 else "WATCH", "monitorPnlChange", snapshot.account_id + ":pnl:" + symbol + ":" + signed_pct(pnl_delta), item["name"], ["손익률 급변", "이전 " + signed_pct(float(before.get("profit_loss_rate") or 0)), "현재 " + signed_pct(float(item.get("profit_loss_rate") or 0)), "변화 " + signed_pct(pnl_delta, "%p"), self.flow_context_line(item), self.investor_context_line(item), self.trend_context_line(item)], symbol, criteria=self.criteria("손익률 변화폭 ±" + self.threshold_text("monitorPnlDelta", "%p") + " 이상", "변화 " + signed_pct(pnl_delta, "%p") + ", 이전 " + signed_pct(float(before.get("profit_loss_rate") or 0)) + ", 현재 " + signed_pct(float(item.get("profit_loss_rate") or 0)))))
            value_delta = pct_delta(self.position_value_base(item), self.position_value_base(before))
            if abs(value_delta) >= float(self.thresholds.get("monitorValueDelta", 0)):
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "ALERT" if value_delta < 0 else "WATCH", "monitorValueChange", snapshot.account_id + ":value:" + symbol + ":" + signed_pct(value_delta), item["name"], ["평가액 급변", "이전 " + self.position_value_label(before), "현재 " + self.position_value_label(item), "변화 " + signed_pct(value_delta) + self.value_delta_basis_label(before, item), self.flow_context_line(item), self.investor_context_line(item), self.trend_context_line(item)], symbol, criteria=self.criteria("평가액 변화율 ±" + self.threshold_text("monitorValueDelta", "%") + " 이상", "변화 " + signed_pct(value_delta) + ", 이전 " + self.position_value_label(before) + ", 현재 " + self.position_value_label(item))))
            trend_signals = self.trend_signals(before, item)
            if trend_signals:
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, self.trend_severity(trend_signals), "monitorTrendChange", snapshot.account_id + ":trend:" + symbol + ":" + ",".join(trend_signals[:2]), item["name"], ["이동평균 변화", "신호 " + " · ".join(trend_signals), self.trend_context_line(item), self.trend_slope_line(item), self.flow_context_line(item), self.investor_context_line(item)], symbol, criteria=self.criteria("20일/60일 이동평균 돌파, 크로스, 또는 괴리 ±" + self.threshold_text("monitorMaDistance", "%") + " 이상", "신호 " + " · ".join(trend_signals))))
            decision = current_decisions.get(symbol) or {}
            previous_decision = previous_decisions.get(symbol) or {}
            pressure_delta = float(decision.get("exit_pressure") or 0) - float(previous_decision.get("exit_pressure") or 0)
            changed = decision.get("decision") and previous_decision.get("decision") and decision.get("decision") != previous_decision.get("decision")
            if changed or abs(pressure_delta) >= float(self.thresholds.get("monitorExitPressureDelta", 0)):
                previous_phrase = self.decision_score_phrase(previous_decision.get("decision") or "-", previous_decision.get("exit_pressure"))
                current_phrase = self.decision_score_phrase(decision.get("decision") or "-", decision.get("exit_pressure"))
                review_lines = decision_change_review_lines(
                    item,
                    before,
                    decision,
                    previous_decision,
                    float(self.thresholds.get("monitorExitPressureDelta", 0)),
                )
                events.append(AlertEvent(snapshot.account_id, snapshot.account_label, "ALERT" if decision.get("tone") == "danger" else "WATCH", "monitorDecisionChange", snapshot.account_id + ":decision:" + symbol + ":" + str(decision.get("decision")), item["name"], ["판단 변화", "이전 " + previous_phrase, "현재 " + current_phrase, self.flow_context_line(item), self.investor_context_line(item), self.trend_context_line(item)] + review_lines, symbol, criteria=self.criteria("판단 이름 변경 또는 위험 점수 변화 " + self.threshold_text("monitorExitPressureDelta", "점") + " 이상", "이전 " + previous_phrase + ", 현재 " + current_phrase)))
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
                "현재 " + money(price, currency),
            ]
            if before_price:
                lines.append("직전 " + money(before_price, currency) + " · 변화 " + signed_pct(price_delta))
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
                    ("변화 " + signed_pct(price_delta) + ", 현재 " + money(price, currency)) if before_price else "현재가 " + money(price, currency) + " 수집",
                ),
            ))
        return events

    def holding_timing_events(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        positions = {item.symbol.upper(): item.to_dict() for item in snapshot.positions if not item.is_cash()}
        for item in snapshot.decisions:
            if item.tone not in {"danger", "caution"} and item.profit_loss_rate > -8:
                continue
            position = positions.get(item.symbol.upper()) or item.to_dict()
            decision_phrase = self.decision_score_phrase(item.decision, item.exit_pressure)
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "ALERT" if item.tone == "danger" else "WATCH",
                "holdingTiming",
                snapshot.account_id + ":timing:" + item.symbol + ":" + item.decision,
                item.name,
                ["상태 " + decision_phrase, "손익 " + signed_pct(item.profit_loss_rate), self.flow_context_line(position), self.investor_context_line(position), self.trend_context_line(position), "매도/매수 기준 재확인"],
                item.symbol,
                criteria=self.criteria(
                    "판단 상태가 위험/주의이거나 손익률이 -8% 이하일 때",
                    "상태 " + decision_phrase + ", 손익 " + signed_pct(item.profit_loss_rate),
                ),
                metadata={
                    "holdingDecision": item.decision,
                    "holdingDecisionBasis": item.decision_basis,
                    "holdingDecisionScore": round(float(item.exit_pressure or 0), 1),
                    "profitLossRate": round(float(item.profit_loss_rate or 0), 2),
                },
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
