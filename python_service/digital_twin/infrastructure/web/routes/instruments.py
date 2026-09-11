"""Instruments HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.web.adapters.instruments import account_watchlist_payload
from digital_twin.infrastructure.web.adapters.instruments import add_account_watchlist_payload
from digital_twin.infrastructure.web.adapters.instruments import remove_account_watchlist_payload
from digital_twin.infrastructure.web.adapters.instruments import replace_account_watchlist_payload
from digital_twin.infrastructure.web.adapters.instruments import request_symbol_universe_refresh
from digital_twin.infrastructure.web.adapters.instruments import symbol_universe_payload
from digital_twin.infrastructure.web.adapters.instruments import symbol_universe_refresh_status
from digital_twin.infrastructure.web.adapters.instruments import symbol_universe_suggest_payload
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from typing import Callable
import re
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class InstrumentsRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    account_watchlist_payload: Callable[..., object] = account_watchlist_payload
    add_account_watchlist_payload: Callable[..., object] = add_account_watchlist_payload
    remove_account_watchlist_payload: Callable[..., object] = remove_account_watchlist_payload
    replace_account_watchlist_payload: Callable[..., object] = replace_account_watchlist_payload
    request_symbol_universe_refresh: Callable[..., object] = request_symbol_universe_refresh
    symbol_universe_payload: Callable[..., object] = symbol_universe_payload
    symbol_universe_refresh_status: Callable[..., object] = symbol_universe_refresh_status
    symbol_universe_suggest_payload: Callable[..., object] = symbol_universe_suggest_payload

    def route_service_accounts_watchlist(self, request, path: str, query: Query):
        account_watchlist_match = re.match(r"^/api/service-accounts/([^/]+)/watchlist(?:/([^/]+))?$", path)
        if account_watchlist_match:
            account_id = urllib.parse.unquote(account_watchlist_match.group(1))
            symbol = urllib.parse.unquote(account_watchlist_match.group(2) or "")
            if request.command == "GET" and not symbol:
                return request.send_payload(200, self.account_watchlist_payload(account_id))
            if request.command == "POST" and not symbol:
                if not request.ensure_writable("공유 모드에서는 관심 종목을 변경할 수 없습니다."):
                    return
                return request.send_payload(200, self.add_account_watchlist_payload(account_id, request.read_json_body()))
            if request.command == "PUT" and not symbol:
                if not request.ensure_writable("공유 모드에서는 관심 종목을 변경할 수 없습니다."):
                    return
                return request.send_payload(200, self.replace_account_watchlist_payload(account_id, request.read_json_body()))
            if request.command == "DELETE" and symbol:
                if not request.ensure_writable("공유 모드에서는 관심 종목을 변경할 수 없습니다."):
                    return
                return request.send_payload(200, self.remove_account_watchlist_payload(account_id, symbol))
        return NOT_HANDLED

    def route_symbol_universe(self, request, path: str, query: Query):
        if path == "/api/symbol-universe":
            if request.command == "GET":
                return request.send_payload(200, self.symbol_universe_payload(query))

        if path == "/api/symbol-universe/suggest":
            if request.command == "GET":
                return request.send_payload(200, self.symbol_universe_suggest_payload(query))

        if path == "/api/symbol-universe/refresh/status" and request.command == "GET":
            return request.send_payload(200, self.symbol_universe_refresh_status(first_query(query, "jobId")))

        if path == "/api/symbol-universe/refresh" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 종목 유니버스를 갱신할 수 없습니다."):
                return
            return request.send_payload(202, self.request_symbol_universe_refresh(request.read_json_body()))
        return NOT_HANDLED
