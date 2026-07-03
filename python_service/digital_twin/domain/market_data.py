import math
from typing import Dict, Iterable, List

from .portfolio import Position


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
