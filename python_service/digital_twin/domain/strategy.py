import ast
import math
from typing import Dict, Iterable, List

from .market_data import clamp, number
from .ontology import ONTOLOGY_PROMPT_VERSION, build_portfolio_ontology, build_position_opinion
from .ontology_rules import DEFAULT_RELATION_THRESHOLDS, evaluate_position_relation_rules
from .parsing import parse_assignments
from .portfolio import DecisionItem, PortfolioSummary, Position, expects_kr_microstructure_signals


DERIVED_FORMULA_DEPENDENCIES = {
    "holdingSignalScore": [
        "tradeStrength",
        "volumeRatio",
        "buyShare",
        "sellShare",
        "investorFlowScore",
        "trendScore",
        "trendDistance20",
        "trendDistance60",
        "maSpread",
    ],
    "lossGuardConfirmationScore": [
        "profitLossRate",
        "volumeRatio",
        "trendDistance20",
        "trendDistance60",
        "ma20Slope",
        "ma60Slope",
        "investorFlowScore",
    ],
    "lossGuardWeakEvidencePenalty": [
        "profitLossRate",
        "volumeRatio",
        "trendDistance20",
        "trendDistance60",
        "investorFlowScore",
    ],
}


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

    def variable_names(self) -> List[str]:
        names = {
            node.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Name) and node.id not in self.allowed_funcs
        }
        return sorted(names)


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
        "baseScore + lossCutPnlScore + sectorConcentrationScore + sellableScore + holdingSignalScore "
        "+ lossGuardConfirmationScore - lossGuardWeakEvidencePenalty"
    )

    def __init__(self, settings: Dict[str, str]):
        settings = settings or {}
        self.settings = dict(settings or {})
        self.weights = parse_assignments(settings.get("formulaWeights", ""), {"flowWeight": 1.0, "valuationWeight": 1.0})
        self.thresholds = parse_assignments(settings.get("alertThresholds", ""), DEFAULT_RELATION_THRESHOLDS)
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

    def score_formula_audits(self, variables: Dict[str, float], scores: Dict[str, float] = None) -> List[Dict[str, object]]:
        merged = dict(self.weights)
        merged.update(self.feature_variables(variables))
        scores = scores or self.score(variables)
        missing = self.market_formula_missing_inputs(variables)
        return [
            self.formula_audit(
                "buyScoreFormula",
                "매수 공식",
                self.buy_formula,
                merged,
                scores.get("buyScore"),
                missing,
            ),
            self.formula_audit(
                "sellScoreFormula",
                "매도 공식",
                self.sell_formula,
                merged,
                scores.get("sellScore"),
                missing,
            ),
        ]

    def holding_formula_audits(self, position: Position, sector_ratio: float = 0.0, scores: Dict[str, object] = None) -> List[Dict[str, object]]:
        variables = self.holding_variables(position, sector_ratio)
        scores = scores or self.holding_pressure_scores(position, sector_ratio)
        missing = self.holding_formula_missing_inputs(position, sector_ratio)
        audits = [
            self.formula_audit(
                "profitTakeScoreFormula",
                "익절 점검 공식",
                self.profit_take_formula,
                variables,
                scores.get("profitTakePressure"),
                missing,
            ),
            self.formula_audit(
                "lossCutScoreFormula",
                "손실 관리 공식",
                self.loss_cut_formula,
                variables,
                scores.get("lossCutPressure"),
                missing,
            ),
        ]
        selected_key = "lossCutScoreFormula" if (position.profit_loss_rate or 0) < 0 else "profitTakeScoreFormula"
        for audit in audits:
            audit["selected"] = audit.get("key") == selected_key
        return audits

    def formula_audit(
        self,
        key: str,
        label: str,
        formula: SafeFormula,
        variables: Dict[str, float],
        result,
        missing_candidates: Dict[str, str] = None,
    ) -> Dict[str, object]:
        names = formula.variable_names()
        missing_candidates = missing_candidates or {}
        missing = [
            missing_candidates[name]
            for name in names
            if name in missing_candidates
        ]
        for name in names:
            for dependency in DERIVED_FORMULA_DEPENDENCIES.get(name, []):
                if dependency in missing_candidates:
                    missing.append(missing_candidates[dependency])
        missing.extend([name for name in names if name not in variables])
        return {
            "key": key,
            "label": label,
            "expression": formula.expression,
            "result": round(number(result), 1),
            "variables": {
                name: round(number(variables.get(name)), 4)
                for name in names
                if name in variables
            },
            "missing": sorted(set(item for item in missing if str(item or "").strip())),
        }

    def market_formula_missing_inputs(self, raw: Dict[str, float]) -> Dict[str, str]:
        raw = raw or {}
        missing: Dict[str, str] = {}
        trade_strength = number(raw.get("tradeStrength") or raw.get("trade_strength"))
        volume_ratio = number(raw.get("volumeRatio") or raw.get("volume_ratio"))
        buy_volume = number(raw.get("buyVolume") or raw.get("buy_volume"))
        sell_volume = number(raw.get("sellVolume") or raw.get("sell_volume"))
        current_price = number(raw.get("currentPrice") or raw.get("current_price") or raw.get("price") or raw.get("closePrice"))
        ma20 = number(raw.get("ma20") or raw.get("movingAverage20"))
        ma60 = number(raw.get("ma60") or raw.get("movingAverage60"))
        bid_ask = number(raw.get("bidAskImbalance") or raw.get("bid_ask_imbalance"))
        orderbook_bid = number(raw.get("orderbookBidVolume") or raw.get("orderbook_bid_volume"))
        orderbook_ask = number(raw.get("orderbookAskVolume") or raw.get("orderbook_ask_volume"))
        price_change = number(raw.get("priceChangeRate") or raw.get("price_change_rate"))
        foreign_net = number(raw.get("foreignNet") or raw.get("foreignNetVolume") or raw.get("foreign_net_volume"))
        institution_net = number(raw.get("institutionNet") or raw.get("institutionNetVolume") or raw.get("institution_net_volume"))
        individual_net = number(raw.get("individualNet") or raw.get("individualNetVolume") or raw.get("individual_net_volume"))
        expects_kr_signals = expects_kr_microstructure_signals(
            raw.get("market") or raw.get("marketCode") or raw.get("exchange"),
            raw.get("currency"),
            raw.get("symbol") or raw.get("ticker") or raw.get("code"),
        )
        if expects_kr_signals and not trade_strength:
            missing["tradeStrength"] = "체결강도 없음 -> 100 기준값"
            missing["executionScore"] = "체결강도 없음 -> 0점"
        if not volume_ratio:
            missing["volumeRatio"] = "거래량 배율 없음 -> 1배"
            missing["volumePressure"] = "거래량 배율 없음 -> 0점"
            missing["directionalVolumePressure"] = "거래량 방향 확인값 없음 -> 0점"
        if expects_kr_signals and not (buy_volume + sell_volume) and not (trade_strength or bid_ask or orderbook_bid or orderbook_ask):
            missing["buyShare"] = "매수/매도 체결량 없음 -> 50%"
            missing["buyShareScore"] = "매수 체결 비중 없음 -> 0점"
            missing["sellShare"] = "매수/매도 체결량 없음 -> 50%"
            missing["directionalVolumePressure"] = "매수/매도 방향 없음 -> 0점"
        if not bid_ask:
            missing["bidAskImbalance"] = "호가 불균형 없음 -> 0"
            missing["orderbookScore"] = "호가 불균형 없음 -> 0점"
        if not price_change:
            missing["priceChangeRate"] = "가격 변화율 없음 -> 0%"
            missing["momentumScore"] = "가격 움직임 없음 -> 0점"
        if not current_price or not ma20 or not ma60:
            missing["trendScore"] = "현재가 또는 이동평균 없음 -> 0점"
            missing["trendDistance20"] = "20일선 괴리 없음 -> 0%"
            missing["trendDistance60"] = "60일선 괴리 없음 -> 0%"
            missing["maSpread"] = "20/60일선 간격 없음 -> 0%"
        if expects_kr_signals and not (foreign_net or institution_net or individual_net):
            missing["investorFlowScore"] = "투자자별 수급 없음 -> 0점"
        if not number(raw.get("undervalueBonus")):
            missing["undervalueBonus"] = "저평가 보너스 없음 -> 0점"
        if not number(raw.get("expensivePenalty") or raw.get("expensiveBonus")):
            missing["expensivePenalty"] = "고평가 패널티 없음 -> 0점"
            missing["expensiveBonus"] = "고평가 보너스 없음 -> 0점"
        return missing

    def holding_formula_missing_inputs(self, position: Position, sector_ratio: float = 0.0) -> Dict[str, str]:
        missing = self.market_formula_missing_inputs(position.to_dict())
        if not position.profit_loss_rate:
            missing["profitLossRate"] = "손익률 없음 또는 0% -> 손익 구간 점수 0점"
            missing["profitTakePnlScore"] = "수익 구간 아님 -> 0점"
            missing["lossCutPnlScore"] = "손실 구간 아님 -> 0점"
        if not sector_ratio:
            missing["sectorConcentrationScore"] = "업종 비중 없음 또는 낮음 -> 0점"
        if not position.sellable_quantity:
            missing["sellableScore"] = "매도 가능 수량 없음 -> 0점"
        if not number(position.buy_volume + position.sell_volume) and not number(position.trade_strength) and not number(position.ma20_distance):
            missing["holdingSignalScore"] = "수급·추세 보유 신호 부족 -> 0점 기준"
        return missing

    def holding_variables(self, position: Position, sector_ratio: float = 0.0) -> Dict[str, float]:
        pnl = float(position.profit_loss_rate or 0.0)
        buy_volume = number(position.buy_volume)
        sell_volume = number(position.sell_volume)
        total_volume = buy_volume + sell_volume
        buy_share = (buy_volume / total_volume) * 100.0 if total_volume else 50.0
        sector_concentration_score = 12.0 if sector_ratio >= 50 else 6.0 if sector_ratio >= 35 else 0.0
        sellable_score = 4.0 if position.sellable_quantity > 0 else 0.0
        holding_signal_score = holding_signal_adjustment(position, pnl)
        loss_guard = loss_guard_confirmation_components(position, pnl, self.thresholds)
        variables = self.feature_variables({
            "tradeStrength": position.trade_strength,
            "volumeRatio": position.volume_ratio,
            "buyVolume": buy_volume,
            "sellVolume": sell_volume,
            "buyShare": buy_share,
            "bidAskImbalance": position.bid_ask_imbalance,
            "currentPrice": position.current_price,
            "ma20": position.ma20,
            "ma60": position.ma60,
            "trendDistance20": position.ma20_distance,
            "trendDistance60": position.ma60_distance,
            "foreignNet": number(position.foreign_net_volume) or number(position.foreign_buy_volume) - number(position.foreign_sell_volume),
            "institutionNet": number(position.institution_net_volume) or number(position.institution_buy_volume) - number(position.institution_sell_volume),
            "individualNet": number(position.individual_net_volume) or number(position.individual_buy_volume) - number(position.individual_sell_volume),
        })
        variables.update({
            "baseScore": 24.0,
            "profitTakePnlScore": profit_take_pnl_component(pnl),
            "lossCutPnlScore": loss_cut_pnl_component(pnl),
            "sectorRatio": float(sector_ratio or 0.0),
            "sectorConcentrationScore": sector_concentration_score,
            "sellableScore": sellable_score,
            "holdingSignalScore": holding_signal_score,
            **loss_guard,
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


def sector_ratio_for_holding(position: Position, portfolio: PortfolioSummary) -> float:
    sector_ratio = 0.0
    for item in portfolio.sectors:
        if item.get("sector") == position.sector:
            sector_ratio = float(item.get("ratio") or 0)
            break
    return sector_ratio


def legacy_decision_payload(position: Position, portfolio: PortfolioSummary, strategy_model: StrategyModel = None) -> Dict[str, object]:
    sector_ratio = sector_ratio_for_holding(position, portfolio)
    pnl = position.profit_loss_rate
    scores = holding_pressure_scores(position, sector_ratio, strategy_model)
    pressure = scores["exitPressure"]
    label, tone = holding_decision_label(pressure, pnl)
    return {
        "symbol": position.symbol,
        "name": position.name,
        "sector": position.sector,
        "market": position.market,
        "currency": position.currency,
        "marketValue": position.market_value,
        "profitLoss": position.profit_loss,
        "profitLossRate": round(pnl, 2),
        "exitPressure": round(pressure, 1),
        "decision": label,
        "tone": tone,
        "profitTakePressure": round(scores["profitTakePressure"], 1),
        "lossCutPressure": round(scores["lossCutPressure"], 1),
        "decisionBasis": scores["decisionBasis"],
    }


def decision_for_position(
    position: Position,
    portfolio: PortfolioSummary,
    strategy_model: StrategyModel = None,
    legacy_payload: Dict[str, object] = None,
    ontology_opinion=None,
    ontology_worldview: Dict[str, object] = None,
    ontology_prompt: str = "",
    relation_context: Dict[str, object] = None,
) -> DecisionItem:
    payload = legacy_payload or legacy_decision_payload(position, portfolio, strategy_model)
    relation_context = relation_context or evaluate_position_relation_rules(
        position,
        portfolio,
        settings=getattr(strategy_model, "settings", {}) if strategy_model else {},
        legacy_model=payload,
    )
    relation_decision = relation_context.get("decision") if isinstance(relation_context, dict) else {}
    if not isinstance(relation_decision, dict):
        relation_decision = {}
    prompt_context = relation_context.get("promptContext") if isinstance(relation_context, dict) else {}
    if not isinstance(prompt_context, dict):
        prompt_context = {}
    exit_pressure = float(relation_decision.get("score") or relation_context.get("signalStrength") or payload.get("exitPressure") or 0)
    decision_label = str(relation_decision.get("label") or payload.get("decision") or "")
    decision_tone = str(relation_decision.get("tone") or payload.get("tone") or "")
    decision_basis = str(relation_decision.get("basis") or "ontologyRelationRules")
    opinion = ontology_opinion or build_position_opinion(position, portfolio, payload)
    opinion_payload = opinion.to_dict()
    worldview = dict(ontology_worldview or {})
    return DecisionItem(
        symbol=position.symbol,
        name=position.name,
        sector=position.sector,
        market=position.market,
        currency=position.currency,
        market_value=position.market_value,
        profit_loss=position.profit_loss,
        profit_loss_rate=float(payload.get("profitLossRate") or 0),
        exit_pressure=exit_pressure,
        decision=decision_label,
        tone=decision_tone,
        profit_take_pressure=float(payload.get("profitTakePressure") or 0),
        loss_cut_pressure=float(payload.get("lossCutPressure") or 0),
        decision_basis=decision_basis,
        ontology_opinion=opinion_payload,
        ontology_worldview=worldview,
        relation_rule_context=relation_context,
        ai_prompt_context=prompt_context,
        ai_context={
            "promptVersion": prompt_context.get("promptVersion") or ONTOLOGY_PROMPT_VERSION,
            "role": "ontology-relation-rule-ai-review",
            "legacyModelRole": "supporting-evidence",
            "worldview": worldview,
            "opinion": opinion_payload,
            "prompt": ontology_prompt,
            "relationRuleContext": relation_context,
            "promptContext": prompt_context,
            "promptTemplate": prompt_context.get("promptTemplate") if isinstance(prompt_context, dict) else {},
        },
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


def loss_guard_confirmation_components(position: Position, pnl: float, thresholds: Dict[str, float] = None) -> Dict[str, float]:
    thresholds = thresholds or DEFAULT_RELATION_THRESHOLDS
    loss_threshold = float(thresholds.get("lossRateLow", DEFAULT_RELATION_THRESHOLDS["lossRateLow"]) or DEFAULT_RELATION_THRESHOLDS["lossRateLow"])
    loss_buffer = abs(float(thresholds.get("lossRateBufferPct", DEFAULT_RELATION_THRESHOLDS["lossRateBufferPct"]) or 0.0))
    volume_confirm_ratio = float(thresholds.get("lossGuardVolumeConfirmRatio", DEFAULT_RELATION_THRESHOLDS["lossGuardVolumeConfirmRatio"]) or 0.0)
    ma60_support_threshold = float(thresholds.get("lossGuardMa60SupportPct", DEFAULT_RELATION_THRESHOLDS["lossGuardMa60SupportPct"]) or 0.0)
    weak_penalty_max = float(thresholds.get("lossGuardWeakEvidencePenalty", DEFAULT_RELATION_THRESHOLDS["lossGuardWeakEvidencePenalty"]) or 0.0)
    ma20_distance = number(position.ma20_distance)
    ma60_distance = number(position.ma60_distance)
    ma20_slope = number(position.ma20_slope)
    ma60_slope = number(position.ma60_slope)
    volume_ratio = number(position.volume_ratio)
    buy_volume = number(position.buy_volume)
    sell_volume = number(position.sell_volume)
    total_volume = buy_volume + sell_volume
    sell_share = (sell_volume / total_volume) * 100.0 if total_volume else 0.0
    foreign_net = number(position.foreign_net_volume) or number(position.foreign_buy_volume) - number(position.foreign_sell_volume)
    institution_net = number(position.institution_net_volume) or number(position.institution_buy_volume) - number(position.institution_sell_volume)
    individual_net = number(position.individual_net_volume) or number(position.individual_buy_volume) - number(position.individual_sell_volume)
    investor_base = abs(foreign_net) + abs(institution_net) + abs(individual_net)
    investor_flow_score = clamp(((foreign_net + institution_net) - individual_net * 0.35) / investor_base * 100.0, -100.0, 100.0) if investor_base else 0.0
    pnl_value = float(pnl or 0.0)
    loss_depth = max(0.0, loss_threshold - pnl_value) if pnl_value <= loss_threshold else 0.0
    near_threshold = pnl_value <= loss_threshold and loss_depth <= loss_buffer
    ma20_break = ma20_distance <= -5
    has_ma60 = bool(number(position.ma60) or ma60_distance)
    ma60_break = has_ma60 and ma60_distance <= ma60_support_threshold
    ma60_support = has_ma60 and ma60_distance > ma60_support_threshold
    volume_confirm = volume_ratio >= volume_confirm_ratio
    sell_flow_confirm = bool(total_volume) and sell_share >= 56.0
    investor_flow_confirm = investor_flow_score <= -15.0
    slope_confirm = ma20_slope <= -1.0 or ma60_slope <= -0.5
    confirmation_count = sum(
        1
        for value in [ma60_break, volume_confirm, sell_flow_confirm, investor_flow_confirm, slope_confirm]
        if value
    )
    confirmation_score = min(18.0, confirmation_count * 4.0)
    weak_penalty = weak_penalty_max if (
        near_threshold
        and ma20_break
        and ma60_support
        and not volume_confirm
        and not investor_flow_confirm
    ) else 0.0
    return {
        "lossThreshold": loss_threshold,
        "lossRateBufferPct": loss_buffer,
        "lossRateDepth": round(loss_depth, 4),
        "lossRateNearThreshold": 1.0 if near_threshold else 0.0,
        "lossGuardMa20Break": 1.0 if ma20_break else 0.0,
        "lossGuardMa60Break": 1.0 if ma60_break else 0.0,
        "lossGuardMa60Support": 1.0 if ma60_support else 0.0,
        "lossGuardVolumeConfirm": 1.0 if volume_confirm else 0.0,
        "lossGuardSellFlowConfirm": 1.0 if sell_flow_confirm else 0.0,
        "lossGuardInvestorFlowConfirm": 1.0 if investor_flow_confirm else 0.0,
        "lossGuardSlopeConfirm": 1.0 if slope_confirm else 0.0,
        "lossGuardConfirmationCount": float(confirmation_count),
        "lossGuardConfirmationScore": confirmation_score,
        "lossGuardWeakEvidencePenalty": weak_penalty,
    }


def holding_decision_label(pressure: float, pnl: float):
    if pressure >= 72:
        if pnl <= -8:
            return "손절·분할축소 권장", "danger"
        if pnl < 0:
            return "손실 축소 권장", "danger"
        return "분할매도 권장", "danger"
    if pressure >= 55:
        if pnl <= -8:
            return "손절·분할축소 권장", "danger"
        if pnl < 0:
            return "손실 축소 권장", "caution"
        return "일부 익절 권장", "caution"
    if pressure >= 38:
        if pnl <= -8:
            return "손실 방어 관망", "hold"
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


def decisions_for_positions(
    positions: Iterable[Position],
    portfolio: PortfolioSummary,
    strategy_model: StrategyModel = None,
    external_signals: Dict[str, object] = None,
) -> List[DecisionItem]:
    active_positions = [item for item in positions if not item.is_cash() and item.market_value > 0]
    legacy_by_symbol = {
        item.symbol.upper(): legacy_decision_payload(item, portfolio, strategy_model)
        for item in active_positions
    }
    ontology = build_portfolio_ontology(
        active_positions,
        portfolio,
        legacy_by_symbol=legacy_by_symbol,
        external_signals=external_signals or {},
    )
    decisions = []
    for item in active_positions:
        legacy_payload = legacy_by_symbol.get(item.symbol.upper())
        relation_context = evaluate_position_relation_rules(
            item,
            portfolio,
            external_signals=external_signals or {},
            settings=getattr(strategy_model, "settings", {}) if strategy_model else {},
            legacy_model=legacy_payload,
        )
        decisions.append(decision_for_position(
            item,
            portfolio,
            strategy_model,
            legacy_payload=legacy_payload,
            ontology_opinion=ontology.opinion_for_symbol(item.symbol),
            ontology_worldview=ontology.worldview,
            ontology_prompt=ontology.prompt,
            relation_context=relation_context,
        ))
    return sorted(decisions, key=lambda item: (-item.exit_pressure, item.symbol))
