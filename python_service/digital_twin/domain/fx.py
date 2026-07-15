from dataclasses import asdict, dataclass
from typing import Dict

from .market_data import number, optional_number
from .portfolio import PortfolioSummary, Position


@dataclass(frozen=True)
class FxRateSignal:
    pair: str = ""
    base: str = ""
    quote: str = "KRW"
    rate: float = 0.0
    previous_rate: float = 0.0
    delta_krw: float = 0.0
    delta_pct: float = 0.0
    delta_7d_krw: float = 0.0
    delta_7d_pct: float = 0.0
    provider: str = ""
    source_type: str = ""
    evidence_strength: str = ""
    market_rate: float = 0.0
    valuation_rate: float = 0.0
    has_delta_signal: bool = False

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FxExposureContext:
    pair: str = ""
    base_currency: str = ""
    quote_currency: str = "KRW"
    rate_to_krw: float = 0.0
    usd_krw_rate: float = 0.0
    usd_krw_previous_rate: float = 0.0
    usd_krw_delta_krw: float = 0.0
    usd_krw_delta_pct: float = 0.0
    usd_krw_7d_delta_krw: float = 0.0
    usd_krw_7d_delta_pct: float = 0.0
    provider: str = ""
    source_type: str = ""
    evidence_strength: str = ""
    market_rate: float = 0.0
    valuation_rate: float = 0.0
    exposure_ratio: float = 0.0
    regime: str = "base_currency_or_unknown"
    has_fx_rate_signal: bool = False
    has_fx_delta_signal: bool = False

    def to_facts(self) -> Dict[str, object]:
        return {
            "fxRatePair": self.pair,
            "fxBaseCurrency": self.base_currency,
            "fxQuoteCurrency": self.quote_currency,
            "fxRateToKrw": self.rate_to_krw,
            "usdKrwRate": self.usd_krw_rate,
            "usdKrwPreviousRate": self.usd_krw_previous_rate,
            "usdKrwDeltaKrw": self.usd_krw_delta_krw,
            "usdKrwDeltaPct": self.usd_krw_delta_pct,
            "usdKrw7dDeltaKrw": self.usd_krw_7d_delta_krw,
            "usdKrw7dDeltaPct": self.usd_krw_7d_delta_pct,
            "fxProvider": self.provider,
            "fxSourceType": self.source_type,
            "fxEvidenceStrength": self.evidence_strength,
            "fxMarketRate": self.market_rate,
            "fxValuationRate": self.valuation_rate,
            "fxExposureRatio": self.exposure_ratio,
            "fxRegime": self.regime,
            "hasFxRateSignal": self.has_fx_rate_signal,
            "hasFxDeltaSignal": self.has_fx_delta_signal,
        }

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def position_weight_pct(position: Position, portfolio: PortfolioSummary, rate_to_krw: float = 0.0) -> float:
    invested = number(portfolio.invested)
    if invested <= 0:
        return 0.0
    currency = str(position.currency or "").upper().strip()
    rate = number(rate_to_krw)
    value = number(position.market_value)
    if currency and currency != "KRW" and rate > 0:
        value *= rate
    return (value / invested) * 100.0


def _fx_delta_fields(item: Dict[str, object], rate: float, inverted: bool = False) -> Dict[str, object]:
    if not isinstance(item, dict):
        return {
            "previous_rate": 0.0,
            "delta_krw": 0.0,
            "delta_pct": 0.0,
            "delta_7d_krw": 0.0,
            "delta_7d_pct": 0.0,
            "has_delta_signal": False,
        }
    previous_rate = optional_number(item, ["previousRate", "previousValue", "previous"])
    if previous_rate is not None and inverted:
        previous_rate = 1 / previous_rate if previous_rate else None
    delta_krw = optional_number(item, ["deltaKrw", "changeKrw", "deltaValue", "changeValue"])
    delta_pct = optional_number(item, ["deltaPct", "changePct", "changePercent", "deltaPercent"])
    delta_7d_krw = optional_number(item, ["delta7dKrw", "change7dKrw", "sevenDayDeltaKrw"])
    delta_7d_pct = optional_number(item, ["delta7dPct", "change7dPct", "sevenDayDeltaPct"])
    if previous_rate is not None and rate:
        computed_delta = rate - previous_rate
        if delta_krw is None:
            delta_krw = computed_delta
        if delta_pct is None and previous_rate:
            delta_pct = (computed_delta / previous_rate) * 100
    has_delta = any(value is not None for value in [previous_rate, delta_krw, delta_pct, delta_7d_krw, delta_7d_pct])
    return {
        "previous_rate": previous_rate or 0.0,
        "delta_krw": delta_krw or 0.0,
        "delta_pct": delta_pct or 0.0,
        "delta_7d_krw": delta_7d_krw or 0.0,
        "delta_7d_pct": delta_7d_pct or 0.0,
        "has_delta_signal": has_delta,
    }


def _fx_signal(
    pair: str,
    base: str,
    quote: str,
    rate: float,
    provider: str,
    item: Dict[str, object] = None,
    inverted: bool = False,
) -> FxRateSignal:
    deltas = _fx_delta_fields(item or {}, rate, inverted=inverted)
    return FxRateSignal(
        pair=pair,
        base=base,
        quote=quote,
        rate=rate,
        previous_rate=deltas["previous_rate"],
        delta_krw=deltas["delta_krw"],
        delta_pct=deltas["delta_pct"],
        delta_7d_krw=deltas["delta_7d_krw"],
        delta_7d_pct=deltas["delta_7d_pct"],
        provider=provider,
        source_type=str((item or {}).get("sourceType") or (item or {}).get("source_type") or ""),
        evidence_strength=str((item or {}).get("evidenceStrength") or (item or {}).get("evidence_strength") or ""),
        market_rate=number((item or {}).get("marketRate")),
        valuation_rate=number((item or {}).get("valuationRate")),
        has_delta_signal=deltas["has_delta_signal"],
    )


def fx_rate_for_currency(external_signals: Dict[str, object], currency: str, quote: str = "KRW") -> FxRateSignal:
    rates = external_signals.get("fxRates") if isinstance(external_signals, dict) else {}
    if not isinstance(rates, dict):
        return FxRateSignal()
    base = str(currency or "").upper().strip()
    quote_currency = str(quote or "KRW").upper().strip()
    if not base or base == quote_currency:
        return FxRateSignal(base=base, quote=quote_currency)
    direct_key = base + quote_currency
    reverse_key = quote_currency + base
    for key in [direct_key, base, reverse_key]:
        item = rates.get(key)
        if isinstance(item, dict):
            item_base = str(item.get("base") or item.get("baseCurrency") or base).upper().strip()
            item_quote = str(item.get("quote") or item.get("quoteCurrency") or quote_currency).upper().strip()
            rate = number(item.get("rate") if item.get("rate") not in (None, "") else item.get("value"))
            provider = str(item.get("provider") or "")
            if item_base == base and item_quote == quote_currency and rate:
                return _fx_signal(item_base + item_quote, item_base, item_quote, rate, provider, item)
            if item_base == quote_currency and item_quote == base and rate:
                return _fx_signal(base + quote_currency, base, quote_currency, 1 / rate if rate else 0.0, provider, item, inverted=True)
        elif item not in (None, ""):
            rate = number(item)
            if rate:
                return FxRateSignal(pair=direct_key, base=base, quote=quote_currency, rate=rate, provider="externalSignals")
    return FxRateSignal(base=base, quote=quote_currency)


def fx_regime(currency: str, rate_to_krw: float) -> str:
    code = str(currency or "").upper().strip()
    rate = number(rate_to_krw)
    if code == "USD" and rate >= 1450:
        return "krw_weakening"
    if code == "USD" and rate and rate <= 1300:
        return "krw_strengthening"
    if code and code != "KRW" and rate:
        return "fx_observed"
    return "base_currency_or_unknown"


def fx_exposure_context(position: Position, portfolio: PortfolioSummary, external_signals: Dict[str, object]) -> FxExposureContext:
    currency = str(position.currency or "").upper().strip()
    signal = fx_rate_for_currency(external_signals, currency)
    rate_value = number(signal.rate)
    exposure_ratio = position_weight_pct(position, portfolio, rate_value) if currency and currency != "KRW" else 0.0
    return FxExposureContext(
        pair=str(signal.pair or ""),
        base_currency=str(signal.base or currency),
        quote_currency=str(signal.quote or "KRW"),
        rate_to_krw=rate_value,
        usd_krw_rate=rate_value if currency == "USD" else 0.0,
        usd_krw_previous_rate=number(signal.previous_rate) if currency == "USD" else 0.0,
        usd_krw_delta_krw=number(signal.delta_krw) if currency == "USD" else 0.0,
        usd_krw_delta_pct=number(signal.delta_pct) if currency == "USD" else 0.0,
        usd_krw_7d_delta_krw=number(signal.delta_7d_krw) if currency == "USD" else 0.0,
        usd_krw_7d_delta_pct=number(signal.delta_7d_pct) if currency == "USD" else 0.0,
        provider=str(signal.provider or ""),
        source_type=str(signal.source_type or ""),
        evidence_strength=str(signal.evidence_strength or ""),
        market_rate=number(signal.market_rate),
        valuation_rate=number(signal.valuation_rate),
        exposure_ratio=exposure_ratio,
        regime=fx_regime(currency, rate_value),
        has_fx_rate_signal=bool(rate_value),
        has_fx_delta_signal=bool(signal.has_delta_signal) if currency == "USD" else False,
    )


def fx_exposure_facts(position: Position, portfolio: PortfolioSummary, external_signals: Dict[str, object]) -> Dict[str, object]:
    return fx_exposure_context(position, portfolio, external_signals).to_facts()
