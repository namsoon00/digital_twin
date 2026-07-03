import ast
import math
from dataclasses import asdict
from typing import Dict, Iterable, List

from .parsing import parse_assignments
from .portfolio import DecisionItem, PortfolioSummary, Position


DEFAULT_FX_RATES = {"KRW": 1.0, "USD": 1400.0}


def number(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else 0.0
    if isinstance(value, dict):
        return number(value.get("amount") or value.get("value") or value.get("krw") or value.get("usd"))
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return 0.0


def first_number(item: Dict[str, object], keys: List[str]) -> float:
    for key in keys:
        if key in item and item.get(key) not in (None, ""):
            return number(item.get(key))
    return 0.0


def optional_number(item: Dict[str, object], keys: List[str]):
    for key in keys:
        if key in item and item.get(key) not in (None, ""):
            return number(item.get(key))
    return None


def moving_average(values: List[float], period: int) -> float:
    usable = [number(value) for value in values if number(value) > 0]
    if not usable:
        return 0.0
    window = usable[-period:] if len(usable) >= period else usable
    return sum(window) / len(window)


def previous_moving_average(values: List[float], period: int) -> float:
    usable = [number(value) for value in values if number(value) > 0]
    if len(usable) < 2:
        return 0.0
    return moving_average(usable[:-1], period)


def pct_distance(value: float, reference: float) -> float:
    base = number(reference)
    if not base:
        return 0.0
    return ((number(value) / base) - 1) * 100


def sorted_candles(candles: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    items = [item for item in candles if isinstance(item, dict)]
    if not items:
        return []
    if any(item.get("timestamp") or item.get("date") for item in items):
        return sorted(items, key=lambda item: str(item.get("timestamp") or item.get("date") or ""))
    return items


def candle_close(candle: Dict[str, object]) -> float:
    return number(candle.get("closePrice") or candle.get("close") or candle.get("price"))


def technical_indicators_from_candles(candles: Iterable[Dict[str, object]]) -> Dict[str, float]:
    ordered = sorted_candles(candles)
    closes = [candle_close(item) for item in ordered if candle_close(item) > 0]
    if not closes:
        return {}
    volumes = [number(item.get("volume")) for item in ordered if number(item.get("volume")) > 0]
    latest = closes[-1]
    latest_volume = volumes[-1] if volumes else 0.0
    volume_ma20 = moving_average(volumes, 20) if volumes else 0.0
    ma5 = moving_average(closes, 5)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma120 = moving_average(closes, 120)
    ma200 = moving_average(closes, 200)
    prev_ma20 = previous_moving_average(closes, 20)
    prev_ma60 = previous_moving_average(closes, 60)
    return {
        "currentPrice": latest,
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "ma120": ma120,
        "ma200": ma200,
        "ma20Slope": pct_distance(ma20, prev_ma20),
        "ma60Slope": pct_distance(ma60, prev_ma60),
        "ma20Distance": pct_distance(latest, ma20),
        "ma60Distance": pct_distance(latest, ma60),
        "volume": latest_volume,
        "volumeMa20": volume_ma20,
        "volumeRatio": latest_volume / volume_ma20 if volume_ma20 else 0.0,
    }


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def sector_from_symbol(value: str) -> str:
    normalized = str(value or "").upper()
    if any(token in normalized for token in ["005930", "000660", "NVDA", "AMD", "TSM", "CHIP", "SEMICONDUCTOR"]):
        return "반도체"
    if any(token in normalized for token in ["AAPL", "MSFT", "GOOGL", "META", "AMZN", "AI", "SOFTWARE"]):
        return "AI/플랫폼"
    if normalized in {"TSLA", "RIVN", "GM", "F"} or any(token in normalized for token in ["EV", "BATTERY", "AUTO"]):
        return "모빌리티"
    if any(token in normalized for token in ["CASH", "USD", "KRW", "현금"]):
        return "현금"
    if any(token in normalized for token in ["BTC", "ETH", "COIN", "CRYPTO"]):
        return "디지털자산"
    return "기타"


def known_stock(symbol: str) -> Dict[str, str]:
    normalized = str(symbol or "").upper()
    known = {
        "005930": {"name": "삼성전자", "market": "KR", "currency": "KRW", "sector": "반도체"},
        "000660": {"name": "SK하이닉스", "market": "KR", "currency": "KRW", "sector": "반도체"},
        "AAPL": {"name": "Apple", "market": "US", "currency": "USD", "sector": "AI/플랫폼"},
        "MSFT": {"name": "Microsoft", "market": "US", "currency": "USD", "sector": "AI/플랫폼"},
        "NVDA": {"name": "NVIDIA", "market": "US", "currency": "USD", "sector": "반도체"},
        "AMD": {"name": "AMD", "market": "US", "currency": "USD", "sector": "반도체"},
        "TSLA": {"name": "Tesla", "market": "US", "currency": "USD", "sector": "모빌리티"},
        "MSTR": {"name": "Strategy", "market": "US", "currency": "USD", "sector": "디지털자산"},
        "STRC": {"name": "Strategy Preferred", "market": "US", "currency": "USD", "sector": "디지털자산"},
    }
    fallback = {"name": normalized or "관심 종목", "market": "", "currency": "", "sector": sector_from_symbol(normalized)}
    fallback.update(known.get(normalized, {}))
    fallback["symbol"] = normalized
    return fallback


def normalize_position(item: Dict[str, object]) -> Position:
    market_value = number(item.get("marketValue") or item.get("evaluationAmount"))
    profit_loss = number(item.get("profitLoss") or item.get("unrealizedProfitLoss"))
    average_price = number(
        item.get("averagePrice")
        or item.get("avgPrice")
        or item.get("purchasePrice")
        or item.get("averagePurchasePrice")
    )
    current_price = number(item.get("currentPrice") or item.get("price") or item.get("closePrice") or item.get("lastPrice"))
    raw_rate = (
        number(item.get("profitLossRate"))
        if item.get("profitLossRate") is not None
        else number(item.get("unrealizedProfitLossRate"))
    )
    if not raw_rate and market_value and profit_loss:
        raw_rate = profit_loss / max(1.0, market_value - profit_loss) * 100
    trade_strength = first_number(item, [
        "tradeStrength",
        "trade_strength",
        "executionStrength",
        "contractStrength",
        "tradePower",
        "체결강도",
    ])
    volume = first_number(item, [
        "volume",
        "tradingVolume",
        "tradeVolume",
        "accumulatedVolume",
        "accTradeVolume",
        "거래량",
    ])
    volume_ratio = first_number(item, [
        "volumeRatio",
        "volume_ratio",
        "relativeVolume",
        "volumeMultiple",
        "거래량배율",
    ])
    buy_volume = first_number(item, [
        "buyVolume",
        "buyTradeVolume",
        "bidVolume",
        "buyExecutionVolume",
        "매수량",
        "매수체결량",
    ])
    sell_volume = first_number(item, [
        "sellVolume",
        "sellTradeVolume",
        "askVolume",
        "sellExecutionVolume",
        "매도량",
        "매도체결량",
    ])
    foreign_buy_volume = first_number(item, [
        "foreignBuyVolume",
        "foreignerBuyVolume",
        "foreignInvestorBuyVolume",
        "foreignBuy",
        "외국인매수량",
        "외국인매수",
    ])
    foreign_sell_volume = first_number(item, [
        "foreignSellVolume",
        "foreignerSellVolume",
        "foreignInvestorSellVolume",
        "foreignSell",
        "외국인매도량",
        "외국인매도",
    ])
    foreign_net_volume = (
        foreign_buy_volume - foreign_sell_volume
        if foreign_buy_volume or foreign_sell_volume
        else first_number(item, [
            "foreignNet",
            "foreignNetBuy",
            "foreignerNetBuy",
            "foreignInvestorNet",
            "외국인순매수",
        ])
    )
    institution_buy_volume = first_number(item, [
        "institutionBuyVolume",
        "institutionalBuyVolume",
        "institutionInvestorBuyVolume",
        "institutionBuy",
        "기관매수량",
        "기관매수",
    ])
    institution_sell_volume = first_number(item, [
        "institutionSellVolume",
        "institutionalSellVolume",
        "institutionInvestorSellVolume",
        "institutionSell",
        "기관매도량",
        "기관매도",
    ])
    institution_net_volume = (
        institution_buy_volume - institution_sell_volume
        if institution_buy_volume or institution_sell_volume
        else first_number(item, [
            "institutionNet",
            "institutionNetBuy",
            "institutionalNet",
            "institutionInvestorNet",
            "기관순매수",
        ])
    )
    trading_value = first_number(item, [
        "tradingValue",
        "trading_value",
        "tradeValue",
        "tradingAmount",
        "tradeAmount",
        "turnover",
        "accumulatedTradeAmount",
        "accTradeAmount",
        "거래대금",
        "거래액",
    ])
    if not trading_value and volume and current_price:
        trading_value = volume * current_price
    symbol = str(item.get("symbol") or item.get("stockCode") or item.get("code") or "").upper()
    info = known_stock(symbol)
    market = str(item.get("marketCountry") or item.get("market") or info["market"])
    currency = str(item.get("currency") or info["currency"])
    if not currency:
        market_code = market.upper()
        if market_code == "US":
            currency = "USD"
        elif market_code in {"KR", "KOSPI", "KOSDAQ"} or symbol.isdigit():
            currency = "KRW"
    return Position(
        symbol=symbol or info["symbol"],
        name=str(item.get("name") or item.get("stockName") or info["name"]),
        market=market,
        currency=currency,
        quantity=number(item.get("quantity") or item.get("qty")),
        sellable_quantity=number(item.get("sellableQuantity") or item.get("availableQuantity") or item.get("sellableQty") or item.get("quantity") or item.get("qty")),
        average_price=average_price,
        current_price=current_price,
        change_rate=optional_number(item, ["changeRate", "priceChangeRate", "changePercent", "changePct", "rate"]),
        quote_source=str(item.get("quoteSource") or item.get("quote_source") or item.get("provider") or ""),
        quote_status=str(item.get("quoteStatus") or item.get("quote_status") or ""),
        quote_message=str(item.get("quoteMessage") or item.get("quote_message") or ""),
        data_quality=str(item.get("dataQuality") or item.get("data_quality") or ""),
        updated_at=str(item.get("updatedAt") or item.get("updated_at") or item.get("timestamp") or ""),
        market_value=market_value,
        profit_loss=profit_loss,
        profit_loss_rate=raw_rate,
        trade_strength=trade_strength,
        trading_value=trading_value,
        volume=volume,
        volume_ratio=volume_ratio,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        foreign_buy_volume=foreign_buy_volume,
        foreign_sell_volume=foreign_sell_volume,
        foreign_net_volume=foreign_net_volume,
        institution_buy_volume=institution_buy_volume,
        institution_sell_volume=institution_sell_volume,
        institution_net_volume=institution_net_volume,
        ma5=first_number(item, ["ma5", "movingAverage5", "sma5"]),
        ma20=first_number(item, ["ma20", "movingAverage20", "sma20"]),
        ma60=first_number(item, ["ma60", "movingAverage60", "sma60"]),
        ma120=first_number(item, ["ma120", "movingAverage120", "sma120"]),
        ma200=first_number(item, ["ma200", "movingAverage200", "sma200"]),
        ma20_slope=first_number(item, ["ma20Slope", "ma20_slope", "movingAverage20Slope"]),
        ma60_slope=first_number(item, ["ma60Slope", "ma60_slope", "movingAverage60Slope"]),
        ma20_distance=first_number(item, ["ma20Distance", "ma20_distance"]),
        ma60_distance=first_number(item, ["ma60Distance", "ma60_distance"]),
        sector=str(item.get("sector") or info["sector"]),
    )


def market_key(position: Position) -> str:
    market = position.market.upper()
    currency = position.currency.upper()
    if market in {"KR", "KOSPI", "KOSDAQ"} or currency == "KRW" or position.symbol.isdigit():
        return "KR"
    if market == "CASH":
        return "KR" if currency == "KRW" else "US" if currency == "USD" else "OTHER"
    return "US" if currency == "USD" or market == "US" else "OTHER"


def empty_market(key: str) -> Dict[str, object]:
    labels = {"KR": "한국장", "US": "미국장", "OTHER": "기타"}
    return {"key": key, "label": labels.get(key, key), "invested": 0.0, "cash": 0.0, "total": 0.0, "cashRatio": 0.0}


def normalized_fx_rates(fx_rates: Dict[str, float] = None) -> Dict[str, float]:
    rates = dict(DEFAULT_FX_RATES)
    for key, value in (fx_rates or {}).items():
        currency = str(key or "").upper()
        if currency:
            rates[currency] = max(0.0, number(value))
    return rates


def value_in_base(value: float, currency: str, fx_rates: Dict[str, float] = None) -> float:
    rates = normalized_fx_rates(fx_rates)
    code = str(currency or "KRW").upper()
    return number(value) * rates.get(code, 1.0)


def portfolio_summary(
    positions: Iterable[Position],
    account_cash: float = 0.0,
    account_currency: str = "KRW",
    fx_rates: Dict[str, float] = None,
) -> PortfolioSummary:
    market_map: Dict[str, Dict[str, object]] = {}
    rates = normalized_fx_rates(fx_rates)

    def exposure(key: str) -> Dict[str, object]:
        if key not in market_map:
            market_map[key] = empty_market(key)
        return market_map[key]

    position_list = list(positions)
    cash = sum(max(0.0, value_in_base(item.market_value, item.currency, rates)) for item in position_list if item.is_cash())
    if cash:
        for item in position_list:
            if item.is_cash():
                exposure(market_key(item))["cash"] = float(exposure(market_key(item))["cash"]) + max(0.0, value_in_base(item.market_value, item.currency, rates))
    elif account_cash:
        cash = max(0.0, value_in_base(account_cash, account_currency, rates))
        exposure("KR" if account_currency.upper() == "KRW" else "US" if account_currency.upper() == "USD" else "OTHER")["cash"] = cash

    invested = 0.0
    sector_map: Dict[str, float] = {}
    if cash:
        sector_map["현금"] = cash
    for item in position_list:
        if item.is_cash():
            continue
        value = max(0.0, value_in_base(item.market_value, item.currency, rates))
        invested += value
        exposure(market_key(item))["invested"] = float(exposure(market_key(item))["invested"]) + value
        sector_map[item.sector or sector_from_symbol(item.symbol)] = sector_map.get(item.sector or "기타", 0.0) + value

    total = invested + cash
    sectors = sorted(
        [{"sector": sector, "value": value, "ratio": round((value / total) * 100) if total else 0} for sector, value in sector_map.items()],
        key=lambda item: float(item["value"]),
        reverse=True,
    )
    markets: List[Dict[str, object]] = []
    for key in ["KR", "US", "OTHER"]:
        item = exposure(key)
        item["total"] = float(item["invested"]) + float(item["cash"])
        item["cashRatio"] = round((float(item["cash"]) / float(item["total"])) * 100) if item["total"] else 0
        if item["total"]:
            markets.append(item)
    concentration = next((float(item["ratio"]) for item in sectors if item["sector"] != "현금"), 0.0)
    return PortfolioSummary(total=total, invested=invested, cash=cash, markets=markets, sectors=sectors, concentration=concentration)


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

    def __init__(self, settings: Dict[str, str]):
        self.weights = parse_assignments(settings.get("formulaWeights", ""), {"flowWeight": 1.0, "valuationWeight": 1.0})
        self.buy_formula = SafeFormula(settings.get("buyScoreFormula") or self.default_buy_formula)
        self.sell_formula = SafeFormula(settings.get("sellScoreFormula") or self.default_sell_formula)

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


def decision_for_position(position: Position, portfolio: PortfolioSummary) -> DecisionItem:
    sector_ratio = 0.0
    for item in portfolio.sectors:
        if item.get("sector") == position.sector:
            sector_ratio = float(item.get("ratio") or 0)
            break
    score = 24.0
    pnl = position.profit_loss_rate
    if pnl >= 20:
        score += 40
    elif pnl >= 10:
        score += 28
    elif pnl >= 5:
        score += 15
    elif pnl <= -15:
        score += 38
    elif pnl <= -8:
        score += 24
    if sector_ratio >= 50:
        score += 12
    elif sector_ratio >= 35:
        score += 6
    if position.sellable_quantity > 0:
        score += 4
    pressure = clamp(score, 0.0, 100.0)
    if pressure >= 72:
        label, tone = ("손절 기준 확인", "danger") if pnl <= -8 else ("분할 매도 기준 확인", "danger")
    elif pressure >= 55:
        label, tone = "일부 익절 기준 확인", "caution"
    elif pressure >= 38:
        label, tone = "조건부 보유", "hold"
    else:
        label, tone = "보유 유지", "watch"
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
    )


def decisions_for_positions(positions: Iterable[Position], portfolio: PortfolioSummary) -> List[DecisionItem]:
    decisions = [decision_for_position(item, portfolio) for item in positions if not item.is_cash() and item.market_value > 0]
    return sorted(decisions, key=lambda item: (-item.exit_pressure, item.symbol))


def serialize_dataclass(value) -> Dict[str, object]:
    return asdict(value)
