import urllib.parse
from typing import Dict, List

from ..domain.company_knowledge import optional_number
from ..domain.market_data import number
from ..domain.portfolio import Position, utc_now_iso
from ..domain.security_lines import related_market_symbols_for_positions
from .external_signal_utils import DISABLED_SETTING_VALUES, percent_text


class ExternalSignalAlphaMixin:
    def add_alpha_vantage(self, signals: Dict[str, object], positions: List[Position]) -> None:
        if not self.external_api_enabled("externalAlphaEnabled"):
            return
        api_key = str(self.settings.get("alphaVantageApiKey") or "").strip()
        if not api_key:
            return
        for symbol in self.alpha_symbols(positions):
            try:
                def fetch_quote():
                    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode({
                        "function": "GLOBAL_QUOTE",
                        "symbol": symbol,
                        "apikey": api_key,
                    })
                    payload = self.fetch_json(url, {"Accept": "application/json"})
                    if payload.get("Information") or payload.get("Note"):
                        raise RuntimeError(str(payload.get("Information") or payload.get("Note")))
                    quote = payload.get("Global Quote") if isinstance(payload.get("Global Quote"), dict) else {}
                    if not quote:
                        raise RuntimeError("empty GLOBAL_QUOTE")
                    return {
                        "provider": "Alpha Vantage",
                        "price": number(quote.get("05. price")),
                        "change": number(quote.get("09. change")),
                        "changePercent": percent_text(quote.get("10. change percent")),
                        "volume": number(quote.get("06. volume")),
                        "latestTradingDay": str(quote.get("07. latest trading day") or ""),
                    }

                signals["equityQuotes"][symbol] = self.guarded_call("Alpha Vantage", "GLOBAL_QUOTE:" + symbol, fetch_quote)
            except Exception as error:  # noqa: BLE001 - provider failure should not stop Toss monitoring.
                self.status_for_error(signals, "Alpha Vantage", symbol + " ", error)

    def alpha_symbols(self, positions: List[Position]) -> List[str]:
        if not self.external_api_enabled("externalAlphaEnabled"):
            return []
        max_symbols = int(number(self.settings.get("externalAlphaMaxSymbols")) or 3)
        related_symbols = related_market_symbols_for_positions(positions, self.settings)
        max_total_symbols = max(max_symbols, max_symbols + int(number(self.settings.get("externalAlphaRelatedMaxSymbols")) or 0))
        symbols = []
        seen = set()
        for position in positions:
            if position.is_cash():
                continue
            symbol = str(position.symbol or "").upper()
            if not symbol or symbol in seen:
                continue
            if position.market.upper() == "US" or position.currency.upper() == "USD":
                seen.add(symbol)
                symbols.append(symbol)
        for symbol in related_symbols:
            if symbol and symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)
        return symbols[:max(1, max_total_symbols)]

    def alpha_fundamentals_enabled(self) -> bool:
        raw_enabled = str(self.settings.get("externalAlphaFundamentalsEnabled") or "0").strip().lower()
        return (
            self.external_api_enabled("externalAlphaEnabled")
            and raw_enabled not in DISABLED_SETTING_VALUES
            and bool(str(self.settings.get("alphaVantageApiKey") or "").strip())
        )

    def alpha_fundamental_symbols(self, positions: List[Position]) -> List[str]:
        if not self.alpha_fundamentals_enabled():
            return []
        max_symbols = int(number(self.settings.get("externalAlphaFundamentalsMaxSymbols")) or 1)
        return self.alpha_symbols(positions)[:max(1, max_symbols)]

    def add_alpha_fundamentals(self, signals: Dict[str, object], positions: List[Position]) -> None:
        if not self.alpha_fundamentals_enabled():
            return
        api_key = str(self.settings.get("alphaVantageApiKey") or "").strip()
        for symbol in self.alpha_fundamental_symbols(positions):
            try:
                def fetch_overview():
                    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode({
                        "function": "OVERVIEW",
                        "symbol": symbol,
                        "apikey": api_key,
                    })
                    payload = self.fetch_json(url, {"Accept": "application/json"})
                    if payload.get("Information") or payload.get("Note") or payload.get("Error Message"):
                        raise RuntimeError(str(payload.get("Information") or payload.get("Note") or payload.get("Error Message")))
                    if not payload.get("Symbol") and not payload.get("Name"):
                        raise RuntimeError("empty OVERVIEW")
                    return self.alpha_company_overview(symbol, payload)

                signals.setdefault("companyOverviews", {})[symbol] = self.guarded_call("Alpha Vantage", "OVERVIEW:" + symbol, fetch_overview)
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "Alpha Vantage", "OVERVIEW:" + symbol + " ", error)

            try:
                def fetch_earnings():
                    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode({
                        "function": "EARNINGS",
                        "symbol": symbol,
                        "apikey": api_key,
                    })
                    payload = self.fetch_json(url, {"Accept": "application/json"})
                    if payload.get("Information") or payload.get("Note") or payload.get("Error Message"):
                        raise RuntimeError(str(payload.get("Information") or payload.get("Note") or payload.get("Error Message")))
                    quarterly = payload.get("quarterlyEarnings") if isinstance(payload.get("quarterlyEarnings"), list) else []
                    annual = payload.get("annualEarnings") if isinstance(payload.get("annualEarnings"), list) else []
                    latest = quarterly[0] if quarterly and isinstance(quarterly[0], dict) else {}
                    if not latest:
                        raise RuntimeError("empty EARNINGS")
                    latest_annual = annual[0] if annual and isinstance(annual[0], dict) else {}
                    return {
                        "provider": "Alpha Vantage",
                        "symbol": symbol,
                        "fetchedAt": utc_now_iso(),
                        "latestQuarter": {
                            "fiscalDateEnding": str(latest.get("fiscalDateEnding") or ""),
                            "reportedDate": str(latest.get("reportedDate") or ""),
                            "reportedEPS": number(latest.get("reportedEPS")),
                            "estimatedEPS": number(latest.get("estimatedEPS")),
                            "epsPeriod": "quarterly",
                            "surprise": number(latest.get("surprise")),
                            "surprisePercentage": number(latest.get("surprisePercentage")),
                        },
                        "latestAnnual": {
                            "fiscalDateEnding": str(latest_annual.get("fiscalDateEnding") or ""),
                            "reportedEPS": number(latest_annual.get("reportedEPS")),
                            "epsPeriod": "annual",
                        } if latest_annual else {},
                    }

                signals.setdefault("earningsReports", {})[symbol] = self.guarded_call("Alpha Vantage", "EARNINGS:" + symbol, fetch_earnings)
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "Alpha Vantage", "EARNINGS:" + symbol + " ", error)

    def alpha_company_overview(self, symbol: str, payload: Dict[str, object]) -> Dict[str, object]:
        return {
            "provider": "Alpha Vantage",
            "symbol": str(payload.get("Symbol") or symbol).upper(),
            "name": str(payload.get("Name") or symbol),
            "assetType": str(payload.get("AssetType") or ""),
            "exchange": str(payload.get("Exchange") or ""),
            "currency": str(payload.get("Currency") or ""),
            "country": str(payload.get("Country") or ""),
            "sector": str(payload.get("Sector") or ""),
            "industry": str(payload.get("Industry") or ""),
            "latestQuarter": str(payload.get("LatestQuarter") or ""),
            "fetchedAt": utc_now_iso(),
            "marketCapitalization": optional_number(payload.get("MarketCapitalization")),
            "revenueTTM": optional_number(payload.get("RevenueTTM")),
            "grossProfitTTM": optional_number(payload.get("GrossProfitTTM")),
            "ebitda": optional_number(payload.get("EBITDA")),
            "profitMargin": optional_number(payload.get("ProfitMargin")),
            "operatingMarginTTM": optional_number(payload.get("OperatingMarginTTM")),
            "trailingEPS": optional_number(payload.get("DilutedEPSTTM")),
            "epsPeriod": "ttm" if number(payload.get("DilutedEPSTTM")) else "",
            "peRatio": optional_number(payload.get("PERatio")),
            "pegRatio": optional_number(payload.get("PEGRatio")),
            "forwardPE": optional_number(payload.get("ForwardPE")),
            "beta": optional_number(payload.get("Beta")),
            "dividendYield": optional_number(payload.get("DividendYield")),
            "dividendYieldUnit": "ratio" if optional_number(payload.get("DividendYield")) is not None else "",
            "analystTargetPrice": optional_number(payload.get("AnalystTargetPrice")),
            "analystRatingStrongBuy": optional_number(payload.get("AnalystRatingStrongBuy")),
            "analystRatingBuy": optional_number(payload.get("AnalystRatingBuy")),
            "analystRatingHold": optional_number(payload.get("AnalystRatingHold")),
            "analystRatingSell": optional_number(payload.get("AnalystRatingSell")),
            "analystRatingStrongSell": optional_number(payload.get("AnalystRatingStrongSell")),
        }
