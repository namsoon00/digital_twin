from typing import Dict, Iterable, List

from .alert_formatting import compact_number, money, pct_delta, price_money, signed_pct
from .market_data import investor_net_volume, number
from .ontology_relation_reasoning import relation_rule_context_summary_lines
from .portfolio import AccountSnapshot, Position
from .portfolio_calculations import value_in_base
from .volume_time_adjustment import trading_value_snapshot, volume_pace_snapshot


class MonitoringPositionContextMixin:
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
            ma5=number(item.get("ma5") or item.get("movingAverage5") or item.get("sma5")),
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
        if quantity:
            parts.append("수량 " + compact_number(quantity) + "주")
        if sellable:
            parts.append("매도가능 " + compact_number(sellable) + "주")
        if market_value:
            parts.append("평가금액 " + self.position_value_label(position))
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
        return "종목 평가금액: " + self.position_value_label(position)

    def portfolio_total_value(self, portfolio) -> float:
        if isinstance(portfolio, dict):
            return number(portfolio.get("total"))
        return number(getattr(portfolio, "total", 0))

    def portfolio_cash_value(self, portfolio) -> float:
        if isinstance(portfolio, dict):
            return number(portfolio.get("cash"))
        return number(getattr(portfolio, "cash", 0))

    def position_is_cash_state(self, position: Dict[str, object]) -> bool:
        return str(position.get("symbol") or "").upper() == "CASH" or str(position.get("sector") or "") == "현금"

    def position_states(self, positions) -> Iterable[Dict[str, object]]:
        if isinstance(positions, dict):
            values = positions.values()
        else:
            values = positions or []
        for item in values:
            if isinstance(item, Position):
                yield item.to_dict()
            elif isinstance(item, dict):
                yield item

    def recalculated_positions_value_base(self, positions) -> float:
        total = 0.0
        for item in self.position_states(positions):
            if self.position_is_cash_state(item):
                continue
            total += max(0.0, self.position_value_base(item))
        return total

    def account_market_value(self, portfolio, positions=None) -> float:
        recalculated = self.recalculated_positions_value_base(positions)
        if recalculated:
            return recalculated + max(0.0, self.portfolio_cash_value(portfolio))
        return self.portfolio_total_value(portfolio)

    def account_market_value_line(self, portfolio, positions=None) -> str:
        total = self.account_market_value(portfolio, positions)
        if not total:
            return ""
        return "계좌 평가금액: " + money(total, "KRW")

    def holding_price_lines(self, position: Dict[str, object], portfolio=None, positions=None) -> List[str]:
        return [
            line
            for line in [
                self.current_price_line(position),
                self.average_price_line(position),
                self.profit_rate_line(position),
                self.holding_quantity_line(position),
                self.sellable_quantity_line(position),
                self.position_market_value_line(position),
                self.account_market_value_line(portfolio, positions),
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
        return number(self.position_trading_value_snapshot(position).get("tradingValue"))

    def position_trading_value_snapshot(self, position: Dict[str, object]) -> Dict[str, object]:
        value = number(position.get("trading_value")) or number(position.get("tradingValue"))
        return trading_value_snapshot(
            self.position_current_price(position),
            self.position_volume(position),
            value,
        )

    def volume_ratio_label(self, value: object) -> str:
        amount = number(value)
        if amount <= 0:
            return ""
        if amount < 0.01:
            return ("%.4f" % amount).rstrip("0").rstrip(".")
        if amount < 0.1:
            return ("%.2f" % amount).rstrip("0").rstrip(".")
        if amount < 10:
            return ("%.1f" % amount).rstrip("0").rstrip(".")
        return compact_number(amount)

    def trading_value_detail_label(self, snapshot: Dict[str, object], currency: str) -> str:
        quality = str(snapshot.get("tradingValueQuality") or "")
        if quality == "reported":
            return "제공값, 가격×거래량 교차확인"
        if quality == "reported_without_cross_check":
            return "제공값, 가격·거래량 교차검증 불가"
        if quality == "estimated_from_price_volume":
            reported = number(snapshot.get("reportedTradingValue"))
            mismatch_pct = number(snapshot.get("tradingValueMismatchPct"))
            if reported > 0 and mismatch_pct > 0:
                return "가격×거래량 추정, 제공값 " + money(reported, currency) + "와 " + self.volume_ratio_label(mismatch_pct) + "% 차이"
            return "가격×거래량 추정"
        return ""

    def position_value_base(self, position: Dict[str, object]) -> float:
        currency = self.position_currency(position)
        native_value = self.position_market_value(position)
        runtime_fx_currencies = {str(item or "").upper() for item in getattr(self, "runtime_fx_currencies", set())}
        if currency != "KRW" and native_value > 0 and currency in runtime_fx_currencies and self.fx_rates.get(currency):
            return value_in_base(native_value, currency, self.fx_rates)
        source_base_value = (
            number(position.get("market_value_krw"))
            or number(position.get("marketValueKrw"))
            or number(position.get("marketValueKRW"))
            or number(position.get("baseMarketValue"))
            or number(position.get("convertedMarketValue"))
        )
        if source_base_value > 0:
            return source_base_value
        return value_in_base(native_value, currency, self.fx_rates)

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
        trading_value_snapshot_value = self.position_trading_value_snapshot(position)
        trading_value = number(trading_value_snapshot_value.get("tradingValue"))
        pace = volume_pace_snapshot(
            position.get("market"),
            ratio,
            volume=volume,
            trading_value=trading_value,
            observed_at=position.get("updated_at") if "updated_at" in position else position.get("updatedAt"),
        )
        adjusted_ratio = number(pace.get("timeAdjustedVolumeRatio"))
        expected_ratio = number(pace.get("expectedVolumeRatioNow"))
        elapsed_pct = number(pace.get("volumePaceElapsedPct"))
        session_label = str(pace.get("volumePaceSessionLabel") or "").strip()
        pace_label = str(pace.get("volumePaceLabel") or "").strip()
        if volume > 0:
            volume_label = compact_number(volume)
            if ratio > 0:
                ratio_bits = ["평균 대비 원본 " + self.volume_ratio_label(ratio) + "x"]
                if adjusted_ratio > 0:
                    ratio_bits.append("시간보정 " + self.volume_ratio_label(adjusted_ratio) + "x")
                if session_label and elapsed_pct > 0:
                    ratio_bits.append(session_label + " " + self.volume_ratio_label(elapsed_pct) + "% 경과")
                if expected_ratio > 0:
                    ratio_bits.append("현시점 기대 누적 " + self.volume_ratio_label(expected_ratio) + "x")
                if pace_label:
                    ratio_bits.append(pace_label)
                volume_label += "(" + " · ".join(ratio_bits) + ")"
            parts.append("거래량 " + volume_label)
        elif ratio > 0:
            ratio_label = "거래량 배율 평균 대비 원본 " + self.volume_ratio_label(ratio) + "x"
            if adjusted_ratio > 0:
                ratio_label += " · 시간보정 " + self.volume_ratio_label(adjusted_ratio) + "x"
            parts.append(ratio_label)

        if trading_value > 0:
            currency = self.position_currency(position)
            detail = self.trading_value_detail_label(trading_value_snapshot_value, currency)
            label = "거래액 " + money(trading_value, currency)
            if detail:
                label += " (" + detail + ")"
            parts.append(label)
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
        if snake_key in position and position.get(snake_key) not in (None, ""):
            return number(position.get(snake_key))
        return number(position.get(camel_key))

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
            effective_net = investor_net_volume(net, buy, sell)
            direction = "순매수" if effective_net > 0 else "순매도" if effective_net < 0 else "매수·매도 균형"
            net_text = ("+" if effective_net > 0 else "-" if effective_net < 0 else "") + compact_number(abs(effective_net)) + "주"
            return (
                label
                + ": 상태 "
                + direction
                + ", 차이 "
                + net_text
                + "(매수-매도), 매수 "
                + compact_number(buy)
                + "주, 매도 "
                + compact_number(sell)
                + "주"
                + amount_text
            )
        if net:
            direction = "순매수" if net > 0 else "순매도"
            return label + ": " + direction + " " + compact_number(abs(net)) + "주" + amount_text
        if net_amount:
            return label + ": 금액 " + self.investor_amount_text(net_amount, currency)
        return ""

    def investor_coverage_note(self, position: Dict[str, object]) -> str:
        coverage = position.get("market_signal_coverage")
        if not isinstance(coverage, dict) or not coverage:
            coverage = position.get("marketSignalCoverage")
        if not isinstance(coverage, dict):
            return ""
        investor = coverage.get("investor") if isinstance(coverage.get("investor"), dict) else {}
        if not investor:
            return ""
        status = str(investor.get("status") or "").strip()
        if status == "stale":
            reason = str(investor.get("staleReason") or investor.get("reason") or "").strip()
            return "신선도 주의" + (" - " + reason if reason else "")
        if investor.get("unchangedCount") not in (None, "", 0):
            return "이전 조회와 같은 투자자 수급 값 " + str(investor.get("unchangedCount")) + "회 연속"
        latency_label = str(investor.get("latencyLabel") or "").strip()
        if investor.get("aiUsableAsStrongEvidence") is False:
            reason = str(investor.get("latencyReason") or investor.get("reason") or "").strip()
            reference = "판단 참고 근거" if investor.get("judgementEvidenceUsable") is not False else "수치 제외"
            return (latency_label or "KIS 투자자 수급 참고용") + " · 실시간 강근거 제외 · " + reference + ((" · " + reason) if reason else "")
        if investor.get("realTime") is True and str(investor.get("cadence") or "") == "live-poll":
            return (latency_label or "KIS 장중 누적 수급 실시간 조회") + " · 매수-매도 차이를 판단 근거로 반영"
        if investor.get("realTime") is False or latency_label:
            return (latency_label or "장중 누적·지연 가능") + " · 현재가·호가와 같은 실시간 체결 데이터 아님"
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
        note = self.investor_coverage_note(position)
        if note:
            coverage = position.get("market_signal_coverage")
            if not isinstance(coverage, dict) or not coverage:
                coverage = position.get("marketSignalCoverage")
            investor = coverage.get("investor") if isinstance(coverage, dict) and isinstance(coverage.get("investor"), dict) else {}
            if investor.get("judgementEvidenceUsable") is not False and str(investor.get("status") or "") == "available":
                return "투자자:\n" + note + "\n" + "\n".join(parts)
            return "투자자:\n" + note + "\n수치 제외: KIS 투자자별 수급이 최신 판단 참고값으로 확인되지 않았습니다."
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
        if not price:
            return ""
        parts = [
            self.ma_comparison_text(position, 5),
            self.ma_comparison_text(position, 20),
            self.ma_comparison_text(position, 60),
        ]
        parts = [part for part in parts if part]
        if not parts:
            return ""
        return "추세: " + ", ".join(parts)

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
