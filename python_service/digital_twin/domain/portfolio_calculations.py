from dataclasses import asdict
from typing import Dict, Iterable, List, Set

from .market_data import number, sector_from_symbol
from .portfolio import PortfolioSummary, Position
from .portfolio_valuation import (
    BROKER_GROSS_BASIS,
    BROKER_NET_BASIS,
    MARK_TO_MARKET_BASIS,
    PortfolioValuationSnapshot,
    PositionValuation,
    normalized_valuation_basis,
    stable_valuation_snapshot_id,
)


DEFAULT_FX_RATES = {"KRW": 1.0, "USD": 1400.0}
BROKER_FX_SOURCE_TYPE = "broker_applied_valuation"
FALLBACK_FX_SOURCE_TYPE = "fallback_setting"
LIVE_MARKET_FX_SOURCE_TYPE = "market_realtime"
DAILY_MARKET_FX_SOURCE_TYPE = "market_daily"


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


def fx_rates_with_external_signals(
    fx_rates: Dict[str, float] = None,
    external_signals: Dict[str, object] = None,
) -> Dict[str, float]:
    rates = normalized_fx_rates(fx_rates)
    external_fx_rates = external_signals.get("fxRates") if isinstance(external_signals, dict) else {}
    if not isinstance(external_fx_rates, dict):
        return rates
    for key, item in external_fx_rates.items():
        if not isinstance(item, dict):
            continue
        normalized_key = str(key or "").upper().replace("/", "").strip()
        base = str(item.get("base") or item.get("baseCurrency") or "").upper().strip()
        quote = str(item.get("quote") or item.get("quoteCurrency") or "").upper().strip()
        if not base and len(normalized_key) >= 6:
            base = normalized_key[:3]
        if not quote and len(normalized_key) >= 6:
            quote = normalized_key[3:6]
        rate = number(item.get("rate") if item.get("rate") not in (None, "") else item.get("value"))
        if not rate:
            continue
        if base and quote == "KRW":
            rates[base] = rate
        elif quote and base == "KRW":
            rates[quote] = 1 / rate
    rates["KRW"] = 1.0
    return rates


def _position_fx_provider(position: Position) -> str:
    source = str(getattr(position, "quote_source", "") or "").strip().lower()
    if "toss" in source:
        return "Toss"
    if "kis" in source or "korea investment" in source or "한국투자" in source:
        return "KIS"
    return "BrokerAccount"


def broker_fx_rates_from_positions(
    positions: Iterable[Position],
    fetched_at: str = "",
) -> Dict[str, Dict[str, object]]:
    accum: Dict[str, Dict[str, object]] = {}
    for position in positions or []:
        currency = str(getattr(position, "currency", "") or "").upper().strip()
        if not currency or currency == "KRW":
            continue
        native_value = number(getattr(position, "broker_market_value", 0.0)) or number(getattr(position, "market_value", 0.0))
        base_value = number(getattr(position, "broker_market_value_krw", 0.0)) or number(getattr(position, "market_value_krw", 0.0))
        explicit_rate = number(getattr(position, "exchange_rate", 0.0))
        implied_rate = base_value / native_value if native_value > 0 and base_value > 0 else 0.0
        rate = implied_rate or explicit_rate
        if rate <= 0:
            continue
        weight = native_value if native_value > 0 else 1.0
        pair = currency + "KRW"
        item = accum.setdefault(
            pair,
            {
                "provider": _position_fx_provider(position),
                "base": currency,
                "quote": "KRW",
                "rateWeight": 0.0,
                "weight": 0.0,
                "sampleCount": 0,
                "lastUpdated": "",
                "fetchedAt": fetched_at,
                "sourceType": BROKER_FX_SOURCE_TYPE,
                "evidenceStrength": "account_applied",
                "valuationPriority": 1,
            },
        )
        item["rateWeight"] = float(item["rateWeight"]) + rate * weight
        item["weight"] = float(item["weight"]) + weight
        item["sampleCount"] = int(item["sampleCount"]) + 1
        if implied_rate:
            item["derivedFrom"] = "marketValueKrw/nativeMarketValue"
        elif not item.get("derivedFrom"):
            item["derivedFrom"] = "exchangeRate"
        updated_at = str(getattr(position, "updated_at", "") or "")
        if updated_at and updated_at > str(item.get("lastUpdated") or ""):
            item["lastUpdated"] = updated_at
        provider = _position_fx_provider(position)
        if provider != "BrokerAccount":
            item["provider"] = provider
    rows: Dict[str, Dict[str, object]] = {}
    for pair, item in accum.items():
        weight = number(item.get("weight"))
        rate = number(item.get("rateWeight")) / weight if weight > 0 else 0.0
        if rate <= 0:
            continue
        row = dict(item)
        row.pop("rateWeight", None)
        row.pop("weight", None)
        row["rate"] = rate
        row["value"] = rate
        rows[pair] = row
    return rows


def runtime_fx_currencies_from_external_signals(external_signals: Dict[str, object] = None) -> Set[str]:
    external_fx_rates = external_signals.get("fxRates") if isinstance(external_signals, dict) else {}
    currencies: Set[str] = set()
    if not isinstance(external_fx_rates, dict):
        return currencies
    for key, item in external_fx_rates.items():
        if not isinstance(item, dict):
            continue
        normalized_key = str(key or "").upper().replace("/", "").strip()
        base = str(item.get("base") or item.get("baseCurrency") or "").upper().strip()
        quote = str(item.get("quote") or item.get("quoteCurrency") or "").upper().strip()
        if not base and len(normalized_key) >= 6:
            base = normalized_key[:3]
        if not quote and len(normalized_key) >= 6:
            quote = normalized_key[3:6]
        rate = number(item.get("rate") if item.get("rate") not in (None, "") else item.get("value"))
        provider = str(item.get("provider") or "").strip().lower()
        source_type = str(item.get("sourceType") or item.get("source_type") or "").strip().lower()
        if (
            not rate
            or provider == "runtimesettings"
            or source_type in {FALLBACK_FX_SOURCE_TYPE, BROKER_FX_SOURCE_TYPE}
            or (source_type and source_type != LIVE_MARKET_FX_SOURCE_TYPE)
        ):
            continue
        if base and quote == "KRW":
            currencies.add(base)
        elif quote and base == "KRW":
            currencies.add(quote)
    currencies.discard("KRW")
    return currencies


def value_in_base(value: float, currency: str, fx_rates: Dict[str, float] = None) -> float:
    rates = normalized_fx_rates(fx_rates)
    code = str(currency or "KRW").upper()
    return number(value) * rates.get(code, 1.0)


def position_value_in_base(
    position: Position,
    fx_rates: Dict[str, float] = None,
    runtime_fx_currencies: Iterable[str] = None,
) -> float:
    rates = normalized_fx_rates(fx_rates)
    currency = str(position.currency or "KRW").upper()
    runtime_currencies = {str(item or "").upper() for item in (runtime_fx_currencies or [])}
    if currency != "KRW" and position.market_value > 0 and currency in runtime_currencies and rates.get(currency):
        return value_in_base(position.market_value, currency, rates)
    source_base_value = number(getattr(position, "market_value_krw", 0.0))
    if source_base_value > 0:
        return source_base_value
    return value_in_base(position.market_value, currency, rates)


def position_account_value_in_base(
    position: Position,
    fx_rates: Dict[str, float] = None,
    runtime_fx_currencies: Iterable[str] = None,
    valuation_basis: str = "",
) -> float:
    basis = normalized_valuation_basis(
        valuation_basis or getattr(position, "account_value_basis", ""),
        MARK_TO_MARKET_BASIS,
    )
    if not valuation_basis and number(getattr(position, "account_value_krw", 0.0)) > 0:
        return number(position.account_value_krw)
    if basis == BROKER_NET_BASIS:
        value = number(getattr(position, "broker_market_value_after_cost_krw", 0.0))
        if value > 0:
            return value
    if basis in {BROKER_NET_BASIS, BROKER_GROSS_BASIS}:
        value = number(getattr(position, "broker_market_value_krw", 0.0))
        if value > 0:
            return value
    value = number(getattr(position, "mark_to_market_value_krw", 0.0))
    if value > 0:
        return value
    return position_value_in_base(position, fx_rates, runtime_fx_currencies)


def _external_fx_row(external_signals: Dict[str, object], currency: str) -> Dict[str, object]:
    rows = external_signals.get("fxRates") if isinstance(external_signals, dict) else {}
    if not isinstance(rows, dict):
        return {}
    code = str(currency or "").upper().strip()
    for key in [code + "KRW", code + "/KRW", code]:
        row = rows.get(key)
        if isinstance(row, dict):
            return row
    return {}


def _position_fx_context(
    position: Position,
    rates: Dict[str, float],
    external_signals: Dict[str, object],
) -> Dict[str, object]:
    currency = str(position.currency or "KRW").upper().strip() or "KRW"
    rate = number(rates.get(currency)) or (1.0 if currency == "KRW" else 0.0)
    if currency == "KRW":
        return {"rate": 1.0, "source": "account-base-currency", "state": "native", "asOf": position.broker_source_as_of or position.source_as_of}
    row = _external_fx_row(external_signals, currency)
    source_type = str(row.get("sourceType") or "").strip().lower()
    provider = str(row.get("provider") or "").strip()
    if source_type == BROKER_FX_SOURCE_TYPE:
        state = "broker-applied"
    elif source_type == LIVE_MARKET_FX_SOURCE_TYPE:
        state = "live"
    elif source_type == DAILY_MARKET_FX_SOURCE_TYPE:
        state = "daily"
    else:
        state = "fallback"
        provider = provider or "RuntimeSettings"
    return {
        "rate": rate,
        "source": provider or "RuntimeSettings",
        "state": state,
        "asOf": str(row.get("lastUpdated") or row.get("fetchedAt") or ""),
    }


def apply_position_base_currency_values(
    positions: Iterable[Position],
    fx_rates: Dict[str, float] = None,
    runtime_fx_currencies: Iterable[str] = None,
    external_signals: Dict[str, object] = None,
    valuation_basis: str = MARK_TO_MARKET_BASIS,
) -> List[Position]:
    rates = normalized_fx_rates(fx_rates)
    runtime_currencies = {str(item or "").upper() for item in (runtime_fx_currencies or [])}
    basis = normalized_valuation_basis(valuation_basis)
    position_list = list(positions or [])
    for position in position_list:
        currency = str(position.currency or "KRW").upper()
        rate = rates.get(currency) or 0.0
        mark_native = number(getattr(position, "mark_to_market_value", 0.0)) or number(position.market_value)
        broker_gross_native = number(getattr(position, "broker_market_value", 0.0)) or number(position.market_value)
        broker_net_native = number(getattr(position, "broker_market_value_after_cost", 0.0))
        if currency == "KRW":
            position.exchange_rate = 1.0
            if number(getattr(position, "market_value_krw", 0.0)) <= 0 and number(position.market_value) > 0:
                position.market_value_krw = number(position.market_value)
            if number(getattr(position, "profit_loss_krw", 0.0)) == 0 and number(position.profit_loss) != 0:
                position.profit_loss_krw = number(position.profit_loss)
        elif rate > 0:
            position.exchange_rate = rate
            should_refresh_base_value = currency in runtime_currencies or number(getattr(position, "market_value_krw", 0.0)) <= 0
            if should_refresh_base_value and number(position.market_value) > 0:
                position.market_value_krw = value_in_base(position.market_value, currency, rates)
            should_refresh_profit_loss = currency in runtime_currencies or number(getattr(position, "profit_loss_krw", 0.0)) == 0
            if should_refresh_profit_loss and number(position.profit_loss) != 0:
                position.profit_loss_krw = value_in_base(position.profit_loss, currency, rates)
        position.mark_to_market_value = mark_native
        position.mark_to_market_value_krw = value_in_base(mark_native, currency, rates) if mark_native else number(position.market_value_krw)
        if currency == "KRW":
            position.mark_to_market_value_krw = mark_native
        position.broker_market_value = broker_gross_native
        if broker_gross_native and (currency == "KRW" or number(position.broker_market_value_krw) <= 0):
            position.broker_market_value_krw = value_in_base(broker_gross_native, currency, rates)
        if broker_net_native and (currency == "KRW" or number(position.broker_market_value_after_cost_krw) <= 0):
            position.broker_market_value_after_cost_krw = value_in_base(broker_net_native, currency, rates)
        if basis == BROKER_NET_BASIS:
            account_value = number(position.broker_market_value_after_cost_krw) or number(position.broker_market_value_krw)
        elif basis == BROKER_GROSS_BASIS:
            account_value = number(position.broker_market_value_krw)
        else:
            account_value = number(position.mark_to_market_value_krw) or number(position.market_value_krw)
        fx_context = _position_fx_context(position, rates, external_signals or {})
        position.account_value_krw = account_value
        position.account_value_basis = basis
        position.valuation_fx_source = str(fx_context.get("source") or "")
        position.valuation_fx_state = str(fx_context.get("state") or "")
        position.valuation_fx_as_of = str(fx_context.get("asOf") or "")
    return position_list


def portfolio_summary(
    positions: Iterable[Position],
    account_cash: float = 0.0,
    account_currency: str = "KRW",
    fx_rates: Dict[str, float] = None,
    runtime_fx_currencies: Iterable[str] = None,
    valuation_basis: str = MARK_TO_MARKET_BASIS,
    account_id: str = "",
    observed_at: str = "",
    external_signals: Dict[str, object] = None,
) -> PortfolioSummary:
    market_map: Dict[str, Dict[str, object]] = {}
    rates = normalized_fx_rates(fx_rates)
    runtime_currencies = {str(item or "").upper() for item in (runtime_fx_currencies or [])}
    basis = normalized_valuation_basis(valuation_basis)

    def exposure(key: str) -> Dict[str, object]:
        if key not in market_map:
            market_map[key] = empty_market(key)
        return market_map[key]

    position_list = list(positions)
    cash = sum(max(0.0, position_account_value_in_base(item, rates, runtime_currencies, basis)) for item in position_list if item.is_cash())
    if cash:
        for item in position_list:
            if item.is_cash():
                exposure(market_key(item))["cash"] = float(exposure(market_key(item))["cash"]) + max(0.0, position_account_value_in_base(item, rates, runtime_currencies, basis))
    elif account_cash:
        cash = max(0.0, value_in_base(account_cash, account_currency, rates))
        exposure("KR" if account_currency.upper() == "KRW" else "US" if account_currency.upper() == "USD" else "OTHER")["cash"] = cash

    invested = 0.0
    broker_gross_invested = 0.0
    broker_net_invested = 0.0
    broker_net_position_count = 0
    mark_to_market_invested = 0.0
    valuation_rows: List[Dict[str, object]] = []
    sector_map: Dict[str, float] = {}
    if cash:
        sector_map["현금"] = cash
    for item in position_list:
        if item.is_cash():
            continue
        value = max(0.0, position_account_value_in_base(item, rates, runtime_currencies, basis))
        broker_gross = max(0.0, number(item.broker_market_value_krw) or position_value_in_base(item, rates, runtime_currencies))
        broker_net = max(0.0, number(item.broker_market_value_after_cost_krw))
        mark_value = max(0.0, number(item.mark_to_market_value_krw) or position_value_in_base(item, rates, runtime_currencies))
        invested += value
        broker_gross_invested += broker_gross
        broker_net_invested += broker_net
        broker_net_position_count += int(broker_net > 0)
        mark_to_market_invested += mark_value
        exposure(market_key(item))["invested"] = float(exposure(market_key(item))["invested"]) + value
        sector = item.sector or sector_from_symbol(item.symbol)
        sector_map[sector] = sector_map.get(sector, 0.0) + value
        valuation_rows.append(PositionValuation(
            symbol=item.symbol,
            currency=item.currency,
            quantity=item.quantity,
            broker_price=(item.broker_market_value / item.quantity if item.quantity and item.broker_market_value else item.current_price),
            broker_gross_native=item.broker_market_value,
            broker_net_native=item.broker_market_value_after_cost,
            broker_purchase_native=item.broker_purchase_amount,
            broker_profit_loss_native=item.broker_profit_loss,
            broker_profit_loss_net_native=item.broker_profit_loss_after_cost,
            broker_gross_base=broker_gross,
            broker_net_base=broker_net,
            mark_to_market_native=item.mark_to_market_value or item.market_value,
            mark_to_market_base=mark_value,
            account_value_base=value,
            account_value_basis=basis,
            fx_rate=item.exchange_rate,
            fx_source=item.valuation_fx_source,
            fx_state=item.valuation_fx_state,
            fx_as_of=item.valuation_fx_as_of,
            holdings_as_of=item.broker_source_as_of,
            price_as_of=item.source_as_of or item.updated_at,
        ).to_dict())

    total = invested + cash
    broker_gross_total = broker_gross_invested + cash
    broker_net_complete = bool(valuation_rows) and broker_net_position_count == len(valuation_rows)
    broker_net_total = broker_net_invested + cash if broker_net_complete else 0.0
    mark_to_market_total = mark_to_market_invested + cash
    broker_comparable_total = broker_net_total if broker_net_complete else broker_gross_total
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
    snapshot_id = stable_valuation_snapshot_id(account_id, observed_at, basis, valuation_rows, cash)
    for item in position_list:
        if not item.is_cash():
            item.valuation_snapshot_id = snapshot_id
    fx_context = {
        currency: _position_fx_context(item, rates, external_signals or {})
        for currency in sorted({str(item.currency or "KRW").upper() for item in position_list if not item.is_cash()})
        for item in [next(row for row in position_list if str(row.currency or "KRW").upper() == currency)]
    }
    component_as_of = {
        "holdings": max([str(item.broker_source_as_of or "") for item in position_list] or [""]),
        "prices": max([str(item.source_as_of or item.updated_at or "") for item in position_list] or [""]),
        "fx": max([str(item.valuation_fx_as_of or "") for item in position_list] or [""]),
        "cash": str(observed_at or ""),
    }
    reconciliation = {
        "status": "matched" if abs(total - (invested + cash)) < 1 else "mismatch",
        "positionSum": invested,
        "cash": cash,
        "total": total,
        "difference": total - (invested + cash),
        "brokerNetCoverage": "complete" if broker_net_complete else "unavailable",
        "brokerComparableDifference": total - broker_comparable_total,
    }
    valuation = PortfolioValuationSnapshot(
        valuation_snapshot_id=snapshot_id,
        account_id=str(account_id or ""),
        observed_at=str(observed_at or ""),
        display_basis=basis,
        base_currency="KRW",
        broker_comparable_total=broker_comparable_total,
        broker_gross_total=broker_gross_total,
        broker_net_total=broker_net_total,
        mark_to_market_total=mark_to_market_total,
        invested_total=invested,
        cash_total=cash,
        account_equity_total=total,
        position_count=len(valuation_rows),
        fx_context=fx_context,
        component_as_of=component_as_of,
        reconciliation=reconciliation,
    ).to_dict()
    valuation["positions"] = valuation_rows
    return PortfolioSummary(
        total=total,
        invested=invested,
        cash=cash,
        markets=markets,
        sectors=sectors,
        concentration=concentration,
        valuation_snapshot_id=snapshot_id,
        valuation_basis=basis,
        broker_comparable_total=broker_comparable_total,
        broker_gross_total=broker_gross_total,
        broker_net_total=broker_net_total,
        mark_to_market_total=mark_to_market_total,
        account_equity_total=total,
        valuation=valuation,
    )


def serialize_dataclass(value) -> Dict[str, object]:
    return asdict(value)
