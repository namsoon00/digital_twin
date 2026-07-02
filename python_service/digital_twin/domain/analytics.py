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
    latest = closes[-1]
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
        market_value=market_value,
        profit_loss=profit_loss,
        profit_loss_rate=raw_rate,
        trade_strength=trade_strength,
        trading_value=trading_value,
        volume=volume,
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
    allowed_funcs = {"min": min, "max": max, "abs": abs, "round": round}

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
        "50 + ((tradeStrength - 100) * 0.25 + (volumeRatio - 1) * 12 "
        "+ (buyShare - 50) * 0.35 + bidAskImbalance * 0.28 + priceChangeRate * 1.1) * flowWeight"
    )
    default_sell_formula = (
        "50 + ((100 - tradeStrength) * 0.22 + (volumeRatio - 1) * 8 "
        "+ (50 - buyShare) * 0.42 - bidAskImbalance * 0.28 - priceChangeRate * 1.2) * flowWeight"
    )

    def __init__(self, settings: Dict[str, str]):
        self.weights = parse_assignments(settings.get("formulaWeights", ""), {"flowWeight": 1.0})
        self.buy_formula = SafeFormula(settings.get("buyScoreFormula") or self.default_buy_formula)
        self.sell_formula = SafeFormula(settings.get("sellScoreFormula") or self.default_sell_formula)

    def score(self, variables: Dict[str, float]) -> Dict[str, float]:
        merged = dict(self.weights)
        merged.update(variables)
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
