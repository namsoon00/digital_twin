"""Market Data HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.mock_market import mock_market_payload
from digital_twin.infrastructure.mock_market import mock_market_scenario_list
from digital_twin.infrastructure.web.adapters.market_proxy import fetch_json_url
from digital_twin.infrastructure.web.adapters.market_proxy import normalize_fred_observations_url
from digital_twin.infrastructure.web.adapters.market_proxy import normalize_opendart_company_url
from digital_twin.infrastructure.web.adapters.market_proxy import stock_snapshot
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import now
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from typing import Callable


@dataclass(frozen=True)
class MarketDataRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    fetch_json_url: Callable[..., object] = fetch_json_url
    mock_market_payload: Callable[..., object] = mock_market_payload
    mock_market_scenario_list: Callable[..., object] = mock_market_scenario_list
    normalize_fred_observations_url: Callable[..., object] = normalize_fred_observations_url
    normalize_opendart_company_url: Callable[..., object] = normalize_opendart_company_url
    stock_snapshot: Callable[..., object] = stock_snapshot

    def route_data_api_fred_observations(self, request, path: str, query: Query):
        if path == "/api/data-api/fred/observations":
            if request.command == "OPTIONS":
                return request.send_payload(204, {}, cors=True)
            return request.send_payload(200, self.fetch_json_url(self.normalize_fred_observations_url(query)), cors=True)

        if path == "/api/data-api/opendart/company":
            if request.command == "OPTIONS":
                return request.send_payload(204, {}, cors=True)
            return request.send_payload(200, self.fetch_json_url(self.normalize_opendart_company_url(query)), cors=True)

        if path == "/api/mock-market/scenarios":
            if request.command == "OPTIONS":
                return request.send_payload(204, {}, cors=True)
            return request.send_payload(200, self.mock_market_scenario_list(), cors=True)

        if path == "/api/mock-market/candles":
            if request.command == "OPTIONS":
                return request.send_payload(204, {}, cors=True)
            flat_query = {key: first_query(query, key) for key in query}
            return request.send_payload(200, self.mock_market_payload(flat_query), cors=True)
        return NOT_HANDLED

    def route_stocks(self, request, path: str, query: Query):
        if path == "/api/stocks" and request.command == "GET":
            symbols = []
            for symbol in str(first_query(query, "symbols") or "").split(","):
                cleaned = symbol.strip()
                if cleaned and cleaned not in symbols:
                    symbols.append(cleaned)
            return request.send_payload(200, {
                "stocks": [self.stock_snapshot(symbol) for symbol in symbols[:12]],
                "source": "Quotes: Stooq/Naver Finance, News: multi-channel RSS/GDELT",
                "fetchedAt": now(),
            })
        return NOT_HANDLED
