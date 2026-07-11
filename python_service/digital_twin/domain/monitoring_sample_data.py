from copy import deepcopy
from dataclasses import replace
from typing import Dict

from .alert_formatting import pct_delta
from .market_data import number
from .portfolio import AccountSnapshot


class MonitoringSampleDataMixin:
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
