from typing import List

from .alert_formatting import money, signed_pct
from .portfolio import AccountSnapshot, AlertEvent


class StrategyAlertMixin:
    def model_score_events(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        buy_threshold = float(self.thresholds.get("modelBuyScore", 0))
        sell_threshold = float(self.thresholds.get("modelSellScore", 0))
        watch_symbols = {item.symbol.upper() for item in snapshot.watchlist if item.symbol}
        items = [item for item in snapshot.positions + snapshot.watchlist if not item.is_cash() and item.symbol]
        for position in items:
            if not (position.current_price or position.market_value or position.volume or position.trade_strength):
                continue
            scores = self.strategy_model.score(position.to_dict())
            symbol = position.symbol.upper()
            source = "관심" if symbol in watch_symbols else "보유"
            common_lines = [
                source + " 종목",
                "현재 " + money(position.current_price, position.currency),
                self.flow_context_line(position.to_dict()),
                self.trend_context_line(position.to_dict()),
            ]
            buy_score = float(scores.get("buyScore") or 0)
            if buy_threshold and buy_score >= buy_threshold:
                buy_phrase = self.model_score_phrase("buy", buy_score)
                events.append(AlertEvent(
                    snapshot.account_id,
                    snapshot.account_label,
                    "WATCH",
                    "modelBuy",
                    ":".join([snapshot.account_id, "model-buy", symbol, str(round(buy_score, 1))]),
                    position.name,
                    ["매수 판단 " + buy_phrase, *common_lines],
                    symbol,
                    criteria=self.criteria(
                        "모델 매수 기준 매수 후보 이상 (" + self.threshold_text("modelBuyScore", "점") + ")",
                        buy_phrase,
                    ),
                ))
            sell_score = float(scores.get("sellScore") or 0)
            if symbol not in watch_symbols and sell_threshold and sell_score >= sell_threshold:
                sell_phrase = self.model_score_phrase("sell", sell_score)
                events.append(AlertEvent(
                    snapshot.account_id,
                    snapshot.account_label,
                    "ALERT",
                    "modelSell",
                    ":".join([snapshot.account_id, "model-sell", symbol, str(round(sell_score, 1))]),
                    position.name,
                    [
                        "매도 판단 " + sell_phrase,
                        "손익률 " + signed_pct(position.profit_loss_rate),
                        *common_lines,
                    ],
                    symbol,
                    criteria=self.criteria(
                        "모델 매도 기준 분할매도 압력 이상 (" + self.threshold_text("modelSellScore", "점") + ")",
                        sell_phrase,
                    ),
                ))
        return events

    def model_sample_events(self, snapshot: AccountSnapshot, rule: str) -> List[AlertEvent]:
        candidates = [item for item in snapshot.watchlist + snapshot.positions if not item.is_cash() and item.symbol]
        if not candidates:
            return []
        position = candidates[0] if rule == "modelBuy" else next((item for item in snapshot.positions if not item.is_cash() and item.symbol), candidates[0])
        scores = self.strategy_model.score(position.to_dict())
        symbol = position.symbol.upper()
        if rule == "modelSell":
            sell_phrase = self.model_score_phrase("sell", float(scores.get("sellScore") or 0))
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
                    "손익률 " + signed_pct(position.profit_loss_rate),
                    self.flow_context_line(position.to_dict()),
                    self.trend_context_line(position.to_dict()),
                ],
                symbol,
                criteria=self.criteria(
                    "모델 매도 기준 분할매도 압력 이상 (" + self.threshold_text("modelSellScore", "점") + ")",
                    sell_phrase,
                ),
            )]
        buy_phrase = self.model_score_phrase("buy", float(scores.get("buyScore") or 0))
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
                "현재 " + money(position.current_price, position.currency),
                self.flow_context_line(position.to_dict()),
                self.trend_context_line(position.to_dict()),
            ],
            symbol,
            criteria=self.criteria(
                "모델 매수 기준 매수 후보 이상 (" + self.threshold_text("modelBuyScore", "점") + ")",
                buy_phrase,
            ),
        )]

