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


def investor_net_volume(reported_net, buy, sell) -> float:
    buy_value = number(buy)
    sell_value = number(sell)
    if buy_value or sell_value:
        return buy_value - sell_value
    return number(reported_net)


def optional_investor_net_volume(reported_net, buy, sell):
    if buy is not None or sell is not None:
        return number(buy) - number(sell)
    if reported_net is not None:
        return number(reported_net)
    return None


BASE_MARKET_VALUE_KEYS = [
    "marketValueKrw",
    "marketValueKRW",
    "market_value_krw",
    "krwMarketValue",
    "krw_market_value",
    "wonMarketValue",
    "evaluationAmountKrw",
    "evaluationAmountKRW",
    "evaluation_amount_krw",
    "krwEvaluationAmount",
    "convertedMarketValue",
    "converted_market_value",
    "convertedEvaluationAmount",
    "converted_evaluation_amount",
    "baseMarketValue",
    "base_market_value",
    "baseCurrencyMarketValue",
    "localCurrencyMarketValue",
    "원화평가금액",
    "원화평가액",
    "평가금액원화",
    "평가액원화",
]


BASE_PROFIT_LOSS_KEYS = [
    "profitLossKrw",
    "profitLossKRW",
    "profit_loss_krw",
    "krwProfitLoss",
    "krw_profit_loss",
    "wonProfitLoss",
    "unrealizedProfitLossKrw",
    "unrealizedProfitLossKRW",
    "unrealized_profit_loss_krw",
    "convertedProfitLoss",
    "converted_profit_loss",
    "baseProfitLoss",
    "base_profit_loss",
    "baseCurrencyProfitLoss",
    "localCurrencyProfitLoss",
    "원화평가손익",
    "원화손익",
    "평가손익원화",
]


EXCHANGE_RATE_KEYS = [
    "exchangeRate",
    "exchange_rate",
    "fxRate",
    "fx_rate",
    "appliedExchangeRate",
    "applied_exchange_rate",
    "baseExchangeRate",
    "base_exchange_rate",
    "환율",
    "적용환율",
]


def looks_like_krw_value(native_value: float, candidate_value: float, currency: str) -> bool:
    if str(currency or "").upper() == "KRW":
        return True
    native = abs(number(native_value))
    candidate = abs(number(candidate_value))
    if not candidate:
        return False
    if not native:
        return candidate >= 1000
    return candidate >= native * 10


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
    if normalized in {"SPY", "QQQ", "IWM", "IPO", "IPOS", "VIXY"}:
        return "시장프록시"
    if normalized in {"TLT", "IEF"}:
        return "채권/금리"
    if normalized in {"HYG", "LQD"}:
        return "크레딧"
    if normalized in {"GLD", "USO", "UUP"}:
        return "원자재/통화"
    if normalized in {"069500", "229200", "122630", "360750"}:
        return "한국시장"
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
        "035420": {"name": "NAVER", "market": "KR", "currency": "KRW", "sector": "AI/플랫폼"},
        "069500": {"name": "KODEX 200", "market": "KR", "currency": "KRW", "sector": "한국시장", "assetType": "ETF"},
        "229200": {"name": "KODEX 코스닥150", "market": "KR", "currency": "KRW", "sector": "한국시장", "assetType": "ETF"},
        "091160": {"name": "KODEX 반도체", "market": "KR", "currency": "KRW", "sector": "반도체", "assetType": "ETF"},
        "122630": {"name": "KODEX 레버리지", "market": "KR", "currency": "KRW", "sector": "한국시장", "assetType": "ETF"},
        "360750": {"name": "TIGER 미국S&P500", "market": "KR", "currency": "KRW", "sector": "한국시장", "assetType": "ETF"},
        "AAPL": {"name": "Apple", "market": "US", "currency": "USD", "sector": "AI/플랫폼"},
        "MSFT": {"name": "Microsoft", "market": "US", "currency": "USD", "sector": "AI/플랫폼"},
        "NVDA": {"name": "NVIDIA", "market": "US", "currency": "USD", "sector": "반도체"},
        "AMD": {"name": "AMD", "market": "US", "currency": "USD", "sector": "반도체"},
        "TSLA": {"name": "Tesla", "market": "US", "currency": "USD", "sector": "모빌리티"},
        "MSTR": {"name": "Strategy", "market": "US", "currency": "USD", "sector": "디지털자산"},
        "STRC": {"name": "Strategy Preferred", "market": "US", "currency": "USD", "sector": "디지털자산"},
        "COIN": {"name": "Coinbase", "market": "US", "currency": "USD", "sector": "디지털자산"},
        "SPY": {"name": "SPDR S&P 500 ETF Trust", "market": "US", "currency": "USD", "sector": "시장프록시", "assetType": "ETF", "exchange": "NYSEARCA"},
        "QQQ": {"name": "Invesco QQQ Trust", "market": "US", "currency": "USD", "sector": "시장프록시", "assetType": "ETF", "exchange": "NASDAQ"},
        "IWM": {"name": "iShares Russell 2000 ETF", "market": "US", "currency": "USD", "sector": "시장프록시", "assetType": "ETF", "exchange": "NYSEARCA"},
        "IPO": {"name": "Renaissance IPO ETF", "market": "US", "currency": "USD", "sector": "시장프록시", "assetType": "ETF", "exchange": "NYSEARCA"},
        "IPOS": {"name": "Renaissance International IPO ETF", "market": "US", "currency": "USD", "sector": "시장프록시", "assetType": "ETF", "exchange": "NYSEARCA"},
        "VIXY": {"name": "ProShares VIX Short-Term Futures ETF", "market": "US", "currency": "USD", "sector": "시장프록시", "assetType": "ETF", "exchange": "NYSEARCA"},
        "TLT": {"name": "iShares 20+ Year Treasury Bond ETF", "market": "US", "currency": "USD", "sector": "채권/금리", "assetType": "ETF", "exchange": "NASDAQ"},
        "IEF": {"name": "iShares 7-10 Year Treasury Bond ETF", "market": "US", "currency": "USD", "sector": "채권/금리", "assetType": "ETF", "exchange": "NASDAQ"},
        "HYG": {"name": "iShares iBoxx High Yield Corporate Bond ETF", "market": "US", "currency": "USD", "sector": "크레딧", "assetType": "ETF", "exchange": "NYSEARCA"},
        "LQD": {"name": "iShares iBoxx Investment Grade Corporate Bond ETF", "market": "US", "currency": "USD", "sector": "크레딧", "assetType": "ETF", "exchange": "NYSEARCA"},
        "SOXX": {"name": "iShares Semiconductor ETF", "market": "US", "currency": "USD", "sector": "반도체", "assetType": "ETF", "exchange": "NASDAQ"},
        "SMH": {"name": "VanEck Semiconductor ETF", "market": "US", "currency": "USD", "sector": "반도체", "assetType": "ETF", "exchange": "NASDAQ"},
        "GLD": {"name": "SPDR Gold Shares", "market": "US", "currency": "USD", "sector": "원자재/통화", "assetType": "ETF", "exchange": "NYSEARCA"},
        "USO": {"name": "United States Oil Fund", "market": "US", "currency": "USD", "sector": "원자재/통화", "assetType": "ETF", "exchange": "NYSEARCA"},
        "UUP": {"name": "Invesco DB US Dollar Index Bullish Fund", "market": "US", "currency": "USD", "sector": "원자재/통화", "assetType": "ETF", "exchange": "NYSEARCA"},
        "BTC": {"name": "Bitcoin", "market": "CRYPTO", "currency": "USD", "sector": "디지털자산", "assetType": "CRYPTO"},
        "ETH": {"name": "Ethereum", "market": "CRYPTO", "currency": "USD", "sector": "디지털자산", "assetType": "CRYPTO"},
    }
    fallback = {"name": normalized or "관심 종목", "market": "", "currency": "", "sector": sector_from_symbol(normalized)}
    fallback.update(known.get(normalized, {}))
    fallback["symbol"] = normalized
    return fallback


def normalize_position(item: Dict[str, object]) -> Position:
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

    quantity = number(item.get("quantity") or item.get("qty"))
    average_price = number(
        item.get("averagePrice")
        or item.get("avgPrice")
        or item.get("purchasePrice")
        or item.get("averagePurchasePrice")
    )
    current_price = number(
        item.get("currentPrice")
        or item.get("price")
        or item.get("closePrice")
        or item.get("lastPrice")
        or item.get("stck_prpr")
    )
    evaluation_amount = optional_number(item, ["evaluationAmount", "evaluation_amount", "evalAmount", "평가금액", "평가액"])
    native_market_value = first_number(item, [
        "marketValue",
        "market_value",
        "foreignMarketValue",
        "foreign_market_value",
        "currencyMarketValue",
        "nativeMarketValue",
        "native_market_value",
        "assetValue",
        "asset_value",
    ])
    estimated_native_market_value = quantity * current_price if quantity and current_price else 0.0
    if not native_market_value:
        if currency.upper() == "KRW" and evaluation_amount is not None:
            native_market_value = evaluation_amount
        elif estimated_native_market_value:
            native_market_value = estimated_native_market_value
        elif evaluation_amount is not None:
            native_market_value = evaluation_amount
    market_value_krw = first_number(item, BASE_MARKET_VALUE_KEYS)
    if not market_value_krw and evaluation_amount is not None and looks_like_krw_value(native_market_value, evaluation_amount, currency):
        market_value_krw = evaluation_amount
    if currency.upper() == "KRW" and not market_value_krw:
        market_value_krw = native_market_value

    profit_loss = first_number(item, [
        "profitLoss",
        "profit_loss",
        "unrealizedProfitLoss",
        "unrealized_profit_loss",
        "nativeProfitLoss",
        "native_profit_loss",
    ])
    profit_loss_krw = first_number(item, BASE_PROFIT_LOSS_KEYS)
    if currency.upper() == "KRW" and not profit_loss_krw:
        profit_loss_krw = profit_loss
    exchange_rate = first_number(item, EXCHANGE_RATE_KEYS)
    raw_rate = (
        number(item.get("profitLossRate"))
        if item.get("profitLossRate") is not None
        else number(item.get("unrealizedProfitLossRate"))
    )
    if not raw_rate and native_market_value and profit_loss:
        raw_rate = profit_loss / max(1.0, native_market_value - profit_loss) * 100
    trade_strength = first_number(item, [
        "tradeStrength",
        "trade_strength",
        "executionStrength",
        "contractStrength",
        "tradePower",
        "tday_rltv",
        "체결강도",
    ])
    volume = first_number(item, [
        "volume",
        "tradingVolume",
        "tradeVolume",
        "accumulatedVolume",
        "accTradeVolume",
        "acml_vol",
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
    orderbook_bid_volume = first_number(item, [
        "orderbookBidVolume",
        "orderbook_bid_volume",
        "bidOrderbookVolume",
        "totalBidVolume",
        "totalBidResidualQuantity",
        "total_bidp_rsqn",
        "총매수호가잔량",
        "매수호가잔량",
    ])
    orderbook_ask_volume = first_number(item, [
        "orderbookAskVolume",
        "orderbook_ask_volume",
        "askOrderbookVolume",
        "totalAskVolume",
        "totalAskResidualQuantity",
        "total_askp_rsqn",
        "총매도호가잔량",
        "매도호가잔량",
    ])
    bid_ask_imbalance = first_number(item, [
        "bidAskImbalance",
        "bid_ask_imbalance",
        "orderbookImbalance",
        "호가불균형",
    ])
    if not bid_ask_imbalance and (orderbook_bid_volume or orderbook_ask_volume):
        base = orderbook_bid_volume + orderbook_ask_volume
        bid_ask_imbalance = ((orderbook_bid_volume - orderbook_ask_volume) / base) * 100 if base else 0.0
    foreign_buy_volume = optional_number(item, [
        "foreignBuyVolume",
        "foreignerBuyVolume",
        "foreignInvestorBuyVolume",
        "foreignBuy",
        "frgn_shnu_vol",
        "frgn_shnu_qty",
        "외국인매수량",
        "외국인매수",
    ])
    foreign_sell_volume = optional_number(item, [
        "foreignSellVolume",
        "foreignerSellVolume",
        "foreignInvestorSellVolume",
        "foreignSell",
        "frgn_seln_vol",
        "frgn_seln_qty",
        "외국인매도량",
        "외국인매도",
    ])
    foreign_net_reported = optional_number(item, [
        "foreignNet",
        "foreignNetBuy",
        "foreignerNetBuy",
        "foreignInvestorNet",
        "foreignNetVolume",
        "frgn_ntby_qty",
        "외국인순매수",
    ])
    foreign_net_volume = optional_investor_net_volume(foreign_net_reported, foreign_buy_volume, foreign_sell_volume) or 0.0
    foreign_buy_volume = number(foreign_buy_volume)
    foreign_sell_volume = number(foreign_sell_volume)
    foreign_net_amount = first_number(item, [
        "foreignNetAmount",
        "foreign_net_amount",
        "foreignNetTradeAmount",
        "foreignInvestorNetAmount",
        "foreignNetTradingValue",
        "frgn_ntby_tr_pbmn",
        "외국인순매수금액",
        "외국인순매수거래대금",
    ])
    institution_buy_volume = optional_number(item, [
        "institutionBuyVolume",
        "institutionalBuyVolume",
        "institutionInvestorBuyVolume",
        "institutionBuy",
        "orgn_shnu_vol",
        "orgn_shnu_qty",
        "기관매수량",
        "기관매수",
    ])
    institution_sell_volume = optional_number(item, [
        "institutionSellVolume",
        "institutionalSellVolume",
        "institutionInvestorSellVolume",
        "institutionSell",
        "orgn_seln_vol",
        "orgn_seln_qty",
        "기관매도량",
        "기관매도",
    ])
    institution_net_reported = optional_number(item, [
        "institutionNet",
        "institutionNetBuy",
        "institutionalNet",
        "institutionInvestorNet",
        "institutionNetVolume",
        "orgn_ntby_qty",
        "기관순매수",
    ])
    institution_net_volume = optional_investor_net_volume(institution_net_reported, institution_buy_volume, institution_sell_volume) or 0.0
    institution_buy_volume = number(institution_buy_volume)
    institution_sell_volume = number(institution_sell_volume)
    institution_net_amount = first_number(item, [
        "institutionNetAmount",
        "institution_net_amount",
        "institutionNetTradeAmount",
        "institutionInvestorNetAmount",
        "institutionNetTradingValue",
        "orgn_ntby_tr_pbmn",
        "기관순매수금액",
        "기관순매수거래대금",
    ])
    individual_buy_volume = optional_number(item, [
        "individualBuyVolume",
        "retailBuyVolume",
        "personalBuyVolume",
        "individualBuy",
        "prsn_shnu_vol",
        "prsn_shnu_qty",
        "개인매수량",
        "개인매수",
    ])
    individual_sell_volume = optional_number(item, [
        "individualSellVolume",
        "retailSellVolume",
        "personalSellVolume",
        "individualSell",
        "prsn_seln_vol",
        "prsn_seln_qty",
        "개인매도량",
        "개인매도",
    ])
    individual_net_reported = optional_number(item, [
        "individualNet",
        "individualNetBuy",
        "individualNetVolume",
        "retailNet",
        "personalNetBuy",
        "prsn_ntby_qty",
        "개인순매수",
    ])
    individual_net_volume = optional_investor_net_volume(individual_net_reported, individual_buy_volume, individual_sell_volume) or 0.0
    individual_buy_volume = number(individual_buy_volume)
    individual_sell_volume = number(individual_sell_volume)
    individual_net_amount = first_number(item, [
        "individualNetAmount",
        "individual_net_amount",
        "individualNetTradeAmount",
        "retailNetAmount",
        "personalNetAmount",
        "individualNetTradingValue",
        "prsn_ntby_tr_pbmn",
        "개인순매수금액",
        "개인순매수거래대금",
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
        "acml_tr_pbmn",
        "거래대금",
        "거래액",
    ])
    if not trading_value and volume and current_price:
        trading_value = volume * current_price
    market_signal_coverage = item.get("marketSignalCoverage")
    if not isinstance(market_signal_coverage, dict):
        market_signal_coverage = item.get("market_signal_coverage")
    return Position(
        symbol=symbol or info["symbol"],
        name=str(item.get("name") or item.get("stockName") or info["name"]),
        market=market,
        currency=currency,
        quantity=number(item.get("quantity") or item.get("qty")),
        sellable_quantity=number(item.get("sellableQuantity") or item.get("availableQuantity") or item.get("sellableQty") or item.get("quantity") or item.get("qty")),
        average_price=average_price,
        current_price=current_price,
        change_rate=optional_number(item, ["changeRate", "priceChangeRate", "changePercent", "changePct", "rate", "prdy_ctrt"]),
        quote_source=str(item.get("quoteSource") or item.get("quote_source") or item.get("provider") or ""),
        quote_status=str(item.get("quoteStatus") or item.get("quote_status") or ""),
        quote_message=str(item.get("quoteMessage") or item.get("quote_message") or ""),
        data_quality=str(item.get("dataQuality") or item.get("data_quality") or ""),
        market_signal_coverage=dict(market_signal_coverage or {}) if isinstance(market_signal_coverage, dict) else {},
        updated_at=str(item.get("updatedAt") or item.get("updated_at") or item.get("timestamp") or ""),
        market_value=native_market_value,
        market_value_krw=market_value_krw,
        profit_loss=profit_loss,
        profit_loss_krw=profit_loss_krw,
        profit_loss_rate=raw_rate,
        exchange_rate=exchange_rate,
        trade_strength=trade_strength,
        trading_value=trading_value,
        volume=volume,
        volume_ratio=volume_ratio,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        orderbook_bid_volume=orderbook_bid_volume,
        orderbook_ask_volume=orderbook_ask_volume,
        bid_ask_imbalance=bid_ask_imbalance,
        foreign_buy_volume=foreign_buy_volume,
        foreign_sell_volume=foreign_sell_volume,
        foreign_net_volume=foreign_net_volume,
        foreign_net_amount=foreign_net_amount,
        institution_buy_volume=institution_buy_volume,
        institution_sell_volume=institution_sell_volume,
        institution_net_volume=institution_net_volume,
        institution_net_amount=institution_net_amount,
        individual_buy_volume=individual_buy_volume,
        individual_sell_volume=individual_sell_volume,
        individual_net_volume=individual_net_volume,
        individual_net_amount=individual_net_amount,
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
        source=str(item.get("source") or item.get("positionSource") or "holding"),
    )
