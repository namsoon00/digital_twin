from dataclasses import asdict
from typing import Dict, Iterable, List

from .market_data import number, sector_from_symbol
from .portfolio import PortfolioSummary, Position


DEFAULT_FX_RATES = {"KRW": 1.0, "USD": 1400.0}


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


def serialize_dataclass(value) -> Dict[str, object]:
    return asdict(value)
