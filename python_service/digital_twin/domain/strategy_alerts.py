from typing import List

from .portfolio import AccountSnapshot, AlertEvent


class StrategyAlertMixin:
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
            scores = self.strategy_model.score(position.to_dict())
            formula_audits = self.strategy_model.score_formula_audits(position.to_dict(), scores)
            symbol = position.symbol.upper()
            position_context = position.to_dict()
            current_price = self.current_price_line(position_context)
            price_lines = self.holding_price_lines(position_context) if source == "보유" else [current_price] if current_price else []
            common_lines = [
                source + " 종목",
                *price_lines,
                self.flow_context_line(position_context),
                self.trend_context_line(position_context),
            ]
            buy_score = float(scores.get("buyScore") or 0)
            sell_score = float(scores.get("sellScore") or 0)
            if source == "관심" and watchlist_buy_threshold and buy_score >= watchlist_buy_threshold:
                buy_phrase = self.model_score_phrase("buy", buy_score)
                events.append(AlertEvent(
                    snapshot.account_id,
                    snapshot.account_label,
                    "WATCH",
                    "watchlistBuyCandidate",
                    ":".join([snapshot.account_id, "watchlist-buy", symbol, str(round(buy_score, 1))]),
                    position.name,
                    ["관심종목 매수 후보 " + buy_phrase, *common_lines],
                    symbol,
                    criteria=self.criteria(
                        "관심종목 매수 기준 이상 (" + self.threshold_text("watchlistBuyScore", "점") + ")",
                        buy_phrase,
                    ),
                    metadata={
                        "modelBuyScore": round(buy_score, 1),
                        "modelSellScore": round(sell_score, 1),
                        "watchlistBuyScore": round(buy_score, 1),
                        "formulaAudits": formula_audits,
                    },
                ))
            if source != "관심" and buy_threshold and buy_score >= buy_threshold:
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
                        "내 매수 기준에서 매수 후보 이상 (" + self.threshold_text("modelBuyScore", "점") + ")",
                        buy_phrase,
                    ),
                    metadata={
                        "modelBuyScore": round(buy_score, 1),
                        "modelSellScore": round(sell_score, 1),
                        "formulaAudits": formula_audits,
                    },
                ))
            if source != "관심" and sell_threshold and sell_score >= sell_threshold:
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
                        *common_lines,
                    ],
                    symbol,
                    criteria=self.criteria(
                        "내 매도 기준에서 분할매도 점검 이상 (" + self.threshold_text("modelSellScore", "점") + ")",
                        sell_phrase,
                    ),
                    metadata={
                        "modelBuyScore": round(buy_score, 1),
                        "modelSellScore": round(sell_score, 1),
                        "formulaAudits": formula_audits,
                    },
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
        scores = self.strategy_model.score(position.to_dict())
        formula_audits = self.strategy_model.score_formula_audits(position.to_dict(), scores)
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
                    *price_lines,
                    self.flow_context_line(position_context),
                    self.trend_context_line(position_context),
                ],
                symbol,
                criteria=self.criteria(
                    "내 매도 기준에서 분할매도 점검 이상 (" + self.threshold_text("modelSellScore", "점") + ")",
                    sell_phrase,
                ),
                metadata={
                    "modelBuyScore": round(float(scores.get("buyScore") or 0), 1),
                    "modelSellScore": round(float(scores.get("sellScore") or 0), 1),
                    "formulaAudits": formula_audits,
                },
            )]
        buy_phrase = self.model_score_phrase("buy", float(scores.get("buyScore") or 0))
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
                    "관심종목 매수 기준 이상 (" + self.threshold_text("watchlistBuyScore", "점") + ")",
                    buy_phrase,
                ),
                metadata={
                    "modelBuyScore": round(float(scores.get("buyScore") or 0), 1),
                    "watchlistBuyScore": round(float(scores.get("buyScore") or 0), 1),
                    "modelSellScore": round(float(scores.get("sellScore") or 0), 1),
                    "formulaAudits": formula_audits,
                },
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
                "내 매수 기준에서 매수 후보 이상 (" + self.threshold_text("modelBuyScore", "점") + ")",
                buy_phrase,
            ),
            metadata={
                "modelBuyScore": round(float(scores.get("buyScore") or 0), 1),
                "modelSellScore": round(float(scores.get("sellScore") or 0), 1),
                "formulaAudits": formula_audits,
            },
        )]
