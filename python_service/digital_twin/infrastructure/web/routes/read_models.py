"""Read Models HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.service_factory import build_instrument_timeline_query_service
from digital_twin.infrastructure.service_factory import build_instrument_valuation_query_service
from digital_twin.infrastructure.service_factory import investment_analysis_snapshot
from digital_twin.infrastructure.web.adapters.capital_flow import capital_flow_api_payload
from digital_twin.infrastructure.web.adapters.cases import investment_case_api_payload
from digital_twin.infrastructure.web.adapters.cases import investment_flow_api_payload
from digital_twin.infrastructure.web.adapters.console import console_dashboard_api_payload
from digital_twin.infrastructure.web.adapters.console import console_decisions_api_payload
from digital_twin.infrastructure.web.adapters.console import console_market_evidence_api_payload
from digital_twin.infrastructure.web.adapters.console import console_market_instruments_api_payload
from digital_twin.infrastructure.web.adapters.console import console_portfolio_api_payload
from digital_twin.infrastructure.web.adapters.flow_lens import flow_lens_read_payload
from digital_twin.infrastructure.web.adapters.flow_lens import persisted_flow_lens_snapshot
from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from digital_twin.modules.portfolio.domain.instrument_valuation import InstrumentValuationQuery
from digital_twin.modules.read_models.contracts import InstrumentTimelineQuery
from typing import Callable
import re
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class ReadModelsRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    build_instrument_timeline_query_service: Callable[..., object] = build_instrument_timeline_query_service
    build_instrument_valuation_query_service: Callable[..., object] = build_instrument_valuation_query_service
    capital_flow_api_payload: Callable[..., object] = capital_flow_api_payload
    console_dashboard_api_payload: Callable[..., object] = console_dashboard_api_payload
    console_decisions_api_payload: Callable[..., object] = console_decisions_api_payload
    console_market_evidence_api_payload: Callable[..., object] = console_market_evidence_api_payload
    console_market_instruments_api_payload: Callable[..., object] = console_market_instruments_api_payload
    console_portfolio_api_payload: Callable[..., object] = console_portfolio_api_payload
    flow_lens_read_payload: Callable[..., object] = flow_lens_read_payload
    investment_analysis_snapshot: Callable[..., object] = investment_analysis_snapshot
    investment_case_api_payload: Callable[..., object] = investment_case_api_payload
    investment_flow_api_payload: Callable[..., object] = investment_flow_api_payload
    operational_read_settings: Callable[..., object] = operational_read_settings
    persisted_flow_lens_snapshot: Callable[..., object] = persisted_flow_lens_snapshot

    def route_flow_lens(self, request, path: str, query: Query):
        if path == "/api/flow-lens" and request.command == "GET":
            return request.send_payload(200, self.flow_lens_read_payload(query), cache_control="no-store")

        if path == "/api/capital-flow/summary" and request.command == "GET":
            return request.send_payload(200, self.capital_flow_api_payload(query), cache_control="no-store")

        if path == "/api/capital-flow/quality" and request.command == "GET":
            return request.send_payload(200, self.capital_flow_api_payload(query, quality_only=True), cache_control="no-store")

        if path == "/api/capital-flow/portfolio" and request.command == "GET":
            return request.send_payload(
                200,
                self.capital_flow_api_payload(query, snapshot=self.persisted_flow_lens_snapshot()),
                cache_control="no-store",
            )

        capital_flow_subject_match = re.match(r"^/api/capital-flow/subjects/([^/]+)$", path)
        if capital_flow_subject_match and request.command == "GET":
            subject_id = urllib.parse.unquote(capital_flow_subject_match.group(1))
            return request.send_payload(
                200,
                self.capital_flow_api_payload(query, subject_id=subject_id),
                cache_control="no-store",
            )

        if path == "/api/dashboard/summary" and request.command == "GET":
            return request.send_payload(200, self.console_dashboard_api_payload(query), cache_control="no-store")

        portfolio_console_views = {
            "/api/portfolio/summary": "summary",
            "/api/portfolio/positions": "positions",
            "/api/portfolio/rebalance": "rebalance",
            "/api/portfolio/activity": "activity",
            "/api/portfolio/interpretation": "interpretation",
        }
        if path in portfolio_console_views and request.command == "GET":
            return request.send_payload(
                200,
                self.console_portfolio_api_payload(query, portfolio_console_views[path]),
                cache_control="no-store",
            )

        if path == "/api/market/instruments" and request.command == "GET":
            return request.send_payload(200, self.console_market_instruments_api_payload(query), cache_control="no-store")

        if path == "/api/market/evidence" and request.command == "GET":
            return request.send_payload(200, self.console_market_evidence_api_payload(query), cache_control="no-store")

        if path == "/api/decisions" and request.command == "GET":
            return request.send_payload(200, self.console_decisions_api_payload(query), cache_control="no-store")

        console_decision_match = re.match(r"^/api/decisions/([^/]+)$", path)
        if console_decision_match and request.command == "GET":
            case_id = urllib.parse.unquote(console_decision_match.group(1))
            payload = self.investment_case_api_payload(query, case_id=case_id)
            return request.send_payload(200 if payload.get("status") == "ok" else 404, payload, cache_control="no-store")
        return NOT_HANDLED

    def route_investment_cases(self, request, path: str, query: Query):
        if path == "/api/investment-cases" and request.command == "GET":
            return request.send_payload(200, self.investment_case_api_payload(query), cache_control="no-store")

        investment_case_section_match = re.match(
            r"^/api/investment-cases/([^/]+)/(history|trace)$",
            path,
        )
        if investment_case_section_match and request.command == "GET":
            case_id = urllib.parse.unquote(investment_case_section_match.group(1))
            payload = self.investment_case_api_payload(
                query,
                case_id=case_id,
                section=investment_case_section_match.group(2),
            )
            return request.send_payload(200 if payload.get("status") == "ok" else 404, payload, cache_control="no-store")

        investment_case_match = re.match(r"^/api/investment-cases/([^/]+)$", path)
        if investment_case_match and request.command == "GET":
            case_id = urllib.parse.unquote(investment_case_match.group(1))
            payload = self.investment_case_api_payload(query, case_id=case_id)
            return request.send_payload(200 if payload.get("status") == "ok" else 404, payload, cache_control="no-store")

        if path in {"/api/investment-flow", "/api/investment-validation"} and request.command == "GET":
            payload = self.investment_flow_api_payload(query)
            payload["view"] = "validation" if path.endswith("validation") else "flow"
            return request.send_payload(200, payload, cache_control="no-store")

        investment_flow_match = re.match(r"^/api/investment-flow/([^/]+)$", path)
        if investment_flow_match and request.command == "GET":
            episode_id = urllib.parse.unquote(investment_flow_match.group(1))
            payload = self.investment_flow_api_payload(query, episode_id=episode_id)
            return request.send_payload(200 if payload.get("status") == "ok" else 404, payload, cache_control="no-store")

        if path == "/api/investment-analysis" and request.command == "GET":
            mock_value = configured(first_query(query, "mock") or first_query(query, "mode")).lower()
            return request.send_payload(200, self.investment_analysis_snapshot(
                mock=mock_value in {"1", "true", "mock"},
                watchlist_symbols=first_query(query, "watchlistSymbols"),
            ))
        return NOT_HANDLED

    def route_instruments_valuation(self, request, path: str, query: Query):
        instrument_valuation_match = re.match(r"^/api/instruments/([^/]+)/valuation$", path)
        if instrument_valuation_match and request.command == "GET":
            try:
                payload = self.build_instrument_valuation_query_service(self.operational_read_settings()).query(
                    InstrumentValuationQuery(
                        symbol=urllib.parse.unquote(instrument_valuation_match.group(1)),
                        account_id=first_query(query, "accountId"),
                    )
                )
            except ValueError as error:
                return request.send_payload(400, {"error": str(error)})
            status = 200 if payload.get("status") == "ok" else 404
            return request.send_payload(status, payload, cache_control="no-store")

        instrument_timeline_match = re.match(r"^/api/instruments/([^/]+)/timeline$", path)
        if instrument_timeline_match and request.command == "GET":
            try:
                payload = self.build_instrument_timeline_query_service(self.operational_read_settings()).query(
                    InstrumentTimelineQuery(
                        symbol=urllib.parse.unquote(instrument_timeline_match.group(1)),
                        account_id=first_query(query, "accountId"),
                        range_key=first_query(query, "range") or "3m",
                        interval=first_query(query, "interval"),
                    )
                )
            except ValueError as error:
                return request.send_payload(400, {"error": str(error)})
            return request.send_payload(200, payload, cache_control="no-store")
        return NOT_HANDLED
