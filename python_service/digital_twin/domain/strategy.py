import ast
import math
from typing import Dict, Iterable, List

from .market_data import clamp, number
from .parsing import parse_assignments
from .portfolio import DecisionItem, PortfolioSummary, Position


class SafeFormula:
    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Num,
        ast.Constant,
        ast.Name,
        ast.Load,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Call,
    )
    allowed_funcs = {
        "min": min,
        "max": max,
        "abs": abs,
        "round": round,
        "sqrt": math.sqrt,
        "pow": pow,
        "clamp": clamp,
    }

    def __init__(self, expression: str):
        self.expression = expression or "0"
        self.tree = ast.parse(self.expression, mode="eval")
        for node in ast.walk(self.tree):
            if not isinstance(node, self.allowed_nodes):
                raise ValueError("unsupported formula syntax: " + node.__class__.__name__)
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in self.allowed_funcs:
                    raise ValueError("unsupported formula function")
        self.code = compile(self.tree, "<strategy-formula>", "eval")

    def evaluate(self, variables: Dict[str, float]) -> float:
        scope = dict(self.allowed_funcs)
        scope.update({key: number(value) for key, value in variables.items()})
        return number(eval(self.code, {"__builtins__": {}}, scope))


class StrategyModel:
    default_buy_formula = (
        "50 + (executionScore * 0.42 + directionalVolumePressure * 0.9 + buyShareScore * 0.55 "
        "+ orderbookScore * 0.32 + momentumScore * 0.35 + trendScore * 0.45 "
        "+ investorFlowScore * 0.35) * flowWeight + undervalueBonus * valuationWeight - expensivePenalty * valuationWeight"
    )
    default_sell_formula = (
        "50 + (-executionScore * 0.38 - directionalVolumePressure * 0.85 - buyShareScore * 0.55 "
        "- orderbookScore * 0.3 - momentumScore * 0.4 - trendScore * 0.35 "
        "- investorFlowScore * 0.3) * flowWeight + expensiveBonus * valuationWeight"
    )
    default_profit_take_formula = (
        "baseScore + profitTakePnlScore + sectorConcentrationScore + sellableScore + holdingSignalScore"
    )
    default_loss_cut_formula = (
        "baseScore + lossCutPnlScore + sectorConcentrationScore + sellableScore + holdingSignalScore"
    )

    def __init__(self, settings: Dict[str, str]):
        self.weights = parse_assignments(settings.get("formulaWeights", ""), {"flowWeight": 1.0, "valuationWeight": 1.0})
        self.buy_formula = SafeFormula(settings.get("buyScoreFormula") or self.default_buy_formula)
        self.sell_formula = SafeFormula(settings.get("sellScoreFormula") or self.default_sell_formula)
        self.profit_take_formula = SafeFormula(settings.get("profitTakeScoreFormula") or self.default_profit_take_formula)
        self.loss_cut_formula = SafeFormula(settings.get("lossCutScoreFormula") or self.default_loss_cut_formula)

    def feature_variables(self, variables: Dict[str, float]) -> Dict[str, float]:
        enriched = {key: number(value) for key, value in variables.items()}
        trade_strength = number(enriched.get("tradeStrength")) or 100.0
        volume_ratio = number(enriched.get("volumeRatio")) or 1.0
        buy_volume = number(enriched.get("buyVolume"))
        sell_volume = number(enriched.get("sellVolume"))
        total_volume = buy_volume + sell_volume
        buy_share = number(enriched.get("buyShare")) or ((buy_volume / total_volume) * 100 if total_volume else 50.0)
        current_price = number(enriched.get("currentPrice") or enriched.get("price") or enriched.get("closePrice"))
        ma20 = number(enriched.get("ma20") or enriched.get("movingAverage20"))
        ma60 = number(enriched.get("ma60") or enriched.get("movingAverage60"))
        trend_distance20 = number(enriched.get("trendDistance20")) or (((current_price / ma20) - 1) * 100 if current_price and ma20 else 0.0)
        trend_distance60 = number(enriched.get("trendDistance60")) or (((current_price / ma60) - 1) * 100 if current_price and ma60 else 0.0)
        ma_spread = number(enriched.get("maSpread")) or (((ma20 / ma60) - 1) * 100 if ma20 and ma60 else 0.0)
        trend_score = clamp(trend_distance20 * 0.35 + trend_distance60 * 0.2 + ma_spread * 0.4, -15.0, 15.0)
        foreign_net = number(enriched.get("foreignNet") or enriched.get("foreignNetVolume") or enriched.get("foreignInvestorNet"))
        institution_net = number(enriched.get("institutionNet") or enriched.get("institutionNetVolume") or enriched.get("institutionInvestorNet"))
        individual_net = number(enriched.get("individualNet") or enriched.get("individualNetVolume") or enriched.get("retailNet"))
        investor_base = abs(foreign_net) + abs(institution_net) + abs(individual_net)
        investor_balance = foreign_net + institution_net - individual_net * 0.35
        investor_flow_score = clamp((investor_balance / investor_base) * 100, -30.0, 30.0) if investor_base else 0.0
        bid_ask_imbalance = number(enriched.get("bidAskImbalance"))
        price_change_rate = number(enriched.get("priceChangeRate"))
        volume_pressure = clamp((volume_ratio - 1) * 10, -10.0, 25.0)
        execution_score = clamp((trade_strength - 100) * 0.5, -25.0, 25.0)
        buy_share_score = clamp((buy_share - 50) * 0.7, -25.0, 25.0)
        orderbook_score = clamp(bid_ask_imbalance * 0.5, -20.0, 20.0)
        momentum_score = clamp(price_change_rate * 4, -20.0, 20.0)
        flow_direction_score = clamp(
            execution_score * 0.35
            + buy_share_score * 0.35
            + orderbook_score * 0.2
            + momentum_score * 0.25
            + trend_score * 0.25
            + investor_flow_score * 0.2,
            -25.0,
            25.0,
        )
        volume_confirmation = clamp(flow_direction_score / 12, -1.0, 1.0)
        enriched.update({
            "tradeStrength": trade_strength,
            "volumeRatio": volume_ratio,
            "buyShare": buy_share,
            "sellShare": max(0.0, 100.0 - buy_share),
            "bidAskImbalance": bid_ask_imbalance,
            "priceChangeRate": price_change_rate,
            "volumePressure": volume_pressure,
            "directionalVolumePressure": volume_pressure * volume_confirmation,
            "volumeConfirmation": volume_confirmation,
            "volumeDryness": clamp((1 - volume_ratio) * 10, 0.0, 10.0) if volume_ratio < 1 else 0.0,
            "executionScore": execution_score,
            "buyShareScore": buy_share_score,
            "orderbookScore": orderbook_score,
            "momentumScore": momentum_score,
            "flowDirectionScore": flow_direction_score,
            "ma20": ma20,
            "ma60": ma60,
            "trendDistance20": trend_distance20,
            "trendDistance60": trend_distance60,
            "maSpread": ma_spread,
            "trendScore": trend_score,
            "foreignNet": foreign_net,
            "institutionNet": institution_net,
            "individualNet": individual_net,
            "smartMoneyNet": foreign_net + institution_net,
            "investorFlowBalance": investor_balance,
            "investorFlowScore": investor_flow_score,
            "valuationWeight": number(enriched.get("valuationWeight")) or number(self.weights.get("valuationWeight")) or 1.0,
            "undervalueBonus": number(enriched.get("undervalueBonus")),
            "expensivePenalty": number(enriched.get("expensivePenalty")),
            "expensiveBonus": number(enriched.get("expensiveBonus") or enriched.get("expensivePenalty")),
        })
        return enriched

    def score(self, variables: Dict[str, float]) -> Dict[str, float]:
        merged = dict(self.weights)
        merged.update(self.feature_variables(variables))
        buy_score = clamp(self.buy_formula.evaluate(merged), 0.0, 100.0)
        sell_score = clamp(self.sell_formula.evaluate(merged), 0.0, 100.0)
        return {"buyScore": round(buy_score, 1), "sellScore": round(sell_score, 1), "scoreGap": round(buy_score - sell_score, 1)}

    def holding_variables(self, position: Position, sector_ratio: float = 0.0) -> Dict[str, float]:
        pnl = float(position.profit_loss_rate or 0.0)
        buy_volume = number(position.buy_volume)
        sell_volume = number(position.sell_volume)
        total_volume = buy_volume + sell_volume
        buy_share = (buy_volume / total_volume) * 100.0 if total_volume else 50.0
        sector_concentration_score = 12.0 if sector_ratio >= 50 else 6.0 if sector_ratio >= 35 else 0.0
        sellable_score = 4.0 if position.sellable_quantity > 0 else 0.0
        holding_signal_score = holding_signal_adjustment(position, pnl)
        variables = self.feature_variables({
            "tradeStrength": position.trade_strength,
            "volumeRatio": position.volume_ratio,
            "buyVolume": buy_volume,
            "sellVolume": sell_volume,
            "buyShare": buy_share,
            "currentPrice": position.current_price,
            "ma20": position.ma20,
            "ma60": position.ma60,
            "trendDistance20": position.ma20_distance,
            "trendDistance60": position.ma60_distance,
            "foreignNet": number(position.foreign_net_volume) or number(position.foreign_buy_volume) - number(position.foreign_sell_volume),
            "institutionNet": number(position.institution_net_volume) or number(position.institution_buy_volume) - number(position.institution_sell_volume),
        })
        variables.update({
            "baseScore": 24.0,
            "profitTakePnlScore": profit_take_pnl_component(pnl),
            "lossCutPnlScore": loss_cut_pnl_component(pnl),
            "sectorRatio": float(sector_ratio or 0.0),
            "sectorConcentrationScore": sector_concentration_score,
            "sellableScore": sellable_score,
            "holdingSignalScore": holding_signal_score,
            "commonScore": 24.0 + sector_concentration_score + sellable_score + holding_signal_score,
            "profitLossRate": pnl,
            "pnl": pnl,
            "quantity": number(position.quantity),
            "sellableQuantity": number(position.sellable_quantity),
            "marketValue": number(position.market_value),
            "profitLoss": number(position.profit_loss),
            "ma20Slope": number(position.ma20_slope),
            "ma60Slope": number(position.ma60_slope),
            "ma20Distance": number(position.ma20_distance),
            "ma60Distance": number(position.ma60_distance),
        })
        merged = dict(self.weights)
        merged.update(variables)
        return merged

    def holding_pressure_scores(self, position: Position, sector_ratio: float = 0.0) -> Dict[str, object]:
        pnl = float(position.profit_loss_rate or 0.0)
        variables = self.holding_variables(position, sector_ratio)
        profit_take_pressure = self.profit_take_formula.evaluate(variables)
        loss_cut_pressure = self.loss_cut_formula.evaluate(variables)
        decision_basis = "lossCut" if pnl < 0 else "profitTake"
        selected = loss_cut_pressure if decision_basis == "lossCut" else profit_take_pressure
        return {
            "profitTakePressure": clamp(profit_take_pressure, 0.0, 100.0),
            "lossCutPressure": clamp(loss_cut_pressure, 0.0, 100.0),
            "exitPressure": clamp(selected, 0.0, 100.0),
            "decisionBasis": decision_basis,
        }


def decision_for_position(position: Position, portfolio: PortfolioSummary, strategy_model: StrategyModel = None) -> DecisionItem:
    sector_ratio = 0.0
    for item in portfolio.sectors:
        if item.get("sector") == position.sector:
            sector_ratio = float(item.get("ratio") or 0)
            break
    pnl = position.profit_loss_rate
    scores = holding_pressure_scores(position, sector_ratio, strategy_model)
    pressure = scores["exitPressure"]
    label, tone = holding_decision_label(pressure, pnl)
    return DecisionItem(
        symbol=position.symbol,
        name=position.name,
        sector=position.sector,
        market=position.market,
        currency=position.currency,
        market_value=position.market_value,
        profit_loss=position.profit_loss,
        profit_loss_rate=round(pnl, 2),
        exit_pressure=round(pressure, 1),
        decision=label,
        tone=tone,
        profit_take_pressure=round(scores["profitTakePressure"], 1),
        loss_cut_pressure=round(scores["lossCutPressure"], 1),
        decision_basis=scores["decisionBasis"],
    )


def holding_pressure_scores(position: Position, sector_ratio: float = 0.0, strategy_model: StrategyModel = None) -> Dict[str, object]:
    model = strategy_model or StrategyModel({})
    return model.holding_pressure_scores(position, sector_ratio)


def profit_take_pnl_component(pnl: float) -> float:
    if pnl >= 20:
        return 40.0
    if pnl >= 10:
        return 28.0
    if pnl >= 5:
        return 15.0
    return 0.0


def loss_cut_pnl_component(pnl: float) -> float:
    if pnl <= -15:
        return 42.0
    if pnl <= -8:
        return 28.0
    if pnl <= -3:
        return 10.0
    return 0.0


def holding_decision_label(pressure: float, pnl: float):
    if pressure >= 72:
        if pnl <= -8:
            return "손절 기준 확인", "danger"
        if pnl < 0:
            return "손실 축소 기준 확인", "danger"
        return "분할 매도 기준 확인", "danger"
    if pressure >= 55:
        if pnl <= -8:
            return "손절 기준 확인", "danger"
        if pnl < 0:
            return "손실 관리 기준 확인", "caution"
        return "일부 익절 기준 확인", "caution"
    if pressure >= 38:
        if pnl <= -8:
            return "손실 관리 조건부 보유", "hold"
        return "조건부 보유", "hold"
    return "보유 유지", "watch"


def holding_signal_adjustment(position: Position, pnl: float = None) -> float:
    pnl_rate = position.profit_loss_rate if pnl is None else float(pnl or 0.0)
    adjustment = 0.0
    buy_volume = number(position.buy_volume)
    sell_volume = number(position.sell_volume)
    total_volume = buy_volume + sell_volume
    volume_ratio = number(position.volume_ratio)
    if total_volume:
        buy_share = (buy_volume / total_volume) * 100.0
        sell_share = 100.0 - buy_share
        if sell_share >= 62 and volume_ratio >= 1.2:
            adjustment += 6
        elif sell_share >= 56:
            adjustment += 3
        if buy_share >= 62 and volume_ratio >= 1.2:
            adjustment -= 5
        elif buy_share >= 56:
            adjustment -= 2
    foreign_net = number(position.foreign_net_volume) or number(position.foreign_buy_volume) - number(position.foreign_sell_volume)
    institution_net = number(position.institution_net_volume) or number(position.institution_buy_volume) - number(position.institution_sell_volume)
    investor_base = abs(foreign_net) + abs(institution_net)
    if investor_base:
        smart_money_ratio = (foreign_net + institution_net) / investor_base
        if smart_money_ratio <= -0.35:
            adjustment += 5
        elif smart_money_ratio <= -0.15:
            adjustment += 2
        elif smart_money_ratio >= 0.35:
            adjustment -= 4
        elif smart_money_ratio >= 0.15:
            adjustment -= 2
    trade_strength = number(position.trade_strength)
    if trade_strength:
        if trade_strength <= 85:
            adjustment += 3
        elif trade_strength >= 120:
            adjustment -= 3
    ma20_distance = number(position.ma20_distance)
    ma60_distance = number(position.ma60_distance)
    ma20_slope = number(position.ma20_slope)
    ma60_slope = number(position.ma60_slope)
    if ma20_distance:
        if ma20_distance <= -5:
            adjustment += 7
        elif ma20_distance <= -2:
            adjustment += 4
        elif ma20_distance >= 8 and pnl_rate >= 10:
            adjustment += 3
        elif ma20_distance > 0 and pnl_rate > -8:
            adjustment -= 2
    if ma60_distance:
        if ma60_distance <= -4:
            adjustment += 4
        elif ma60_distance > 0 and pnl_rate > -8:
            adjustment -= 1
    if ma20_slope:
        if ma20_slope <= -1:
            adjustment += 4
        elif ma20_slope >= 0.5 and pnl_rate > -8:
            adjustment -= 2
    if ma60_slope:
        if ma60_slope <= -0.5:
            adjustment += 2
        elif ma60_slope > 0 and pnl_rate > -8:
            adjustment -= 1
    return clamp(adjustment, -12.0, 18.0)


def decisions_for_positions(positions: Iterable[Position], portfolio: PortfolioSummary, strategy_model: StrategyModel = None) -> List[DecisionItem]:
    decisions = [
        decision_for_position(item, portfolio, strategy_model)
        for item in positions
        if not item.is_cash() and item.market_value > 0
    ]
    return sorted(decisions, key=lambda item: (-item.exit_pressure, item.symbol))
