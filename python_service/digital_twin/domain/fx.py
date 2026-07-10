from dataclasses import asdict, dataclass
from typing import Dict

from .market_data import number
from .portfolio import PortfolioSummary, Position


@dataclass(frozen=True)
class FxRateSignal:
    pair: str = ""
    base: str = ""
    quote: str = "KRW"
    rate: float = 0.0
    provider: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FxExposureContext:
    pair: str = ""
    base_currency: str = ""
    quote_currency: str = "KRW"
    rate_to_krw: float = 0.0
    usd_krw_rate: float = 0.0
    provider: str = ""
    exposure_ratio: float = 0.0
    regime: str = "base_currency_or_unknown"
    has_fx_rate_signal: bool = False

    def to_facts(self) -> Dict[str, object]:
        return {
            "fxRatePair": self.pair,
            "fxBaseCurrency": self.base_currency,
            "fxQuoteCurrency": self.quote_currency,
            "fxRateToKrw": self.rate_to_krw,
            "usdKrwRate": self.usd_krw_rate,
            "fxProvider": self.provider,
            "fxExposureRatio": self.exposure_ratio,
            "fxRegime": self.regime,
            "hasFxRateSignal": self.has_fx_rate_signal,
        }

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def position_weight_pct(position: Position, portfolio: PortfolioSummary) -> float:
    invested = number(portfolio.invested)
    if invested <= 0:
        return 0.0
    return (number(position.market_value) / invested) * 100.0


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
                return FxRateSignal(item_base + item_quote, item_base, item_quote, rate, provider)
            if item_base == quote_currency and item_quote == base and rate:
                return FxRateSignal(base + quote_currency, base, quote_currency, 1 / rate if rate else 0.0, provider)
        elif item not in (None, ""):
            rate = number(item)
            if rate:
                return FxRateSignal(direct_key, base, quote_currency, rate, "externalSignals")
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
    exposure_ratio = position_weight_pct(position, portfolio) if currency and currency != "KRW" else 0.0
    return FxExposureContext(
        pair=str(signal.pair or ""),
        base_currency=str(signal.base or currency),
        quote_currency=str(signal.quote or "KRW"),
        rate_to_krw=rate_value,
        usd_krw_rate=rate_value if currency == "USD" else 0.0,
        provider=str(signal.provider or ""),
        exposure_ratio=exposure_ratio,
        regime=fx_regime(currency, rate_value),
        has_fx_rate_signal=bool(rate_value),
    )


def fx_exposure_facts(position: Position, portfolio: PortfolioSummary, external_signals: Dict[str, object]) -> Dict[str, object]:
    return fx_exposure_context(position, portfolio, external_signals).to_facts()
