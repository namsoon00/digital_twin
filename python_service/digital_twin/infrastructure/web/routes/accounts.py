"""Accounts HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.web.adapters.accounts import remove_account_payload
from digital_twin.infrastructure.web.adapters.accounts import save_account_payload
from digital_twin.infrastructure.web.adapters.accounts import service_accounts_payload
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from typing import Callable
import re
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class AccountsRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    remove_account_payload: Callable[..., object] = remove_account_payload
    save_account_payload: Callable[..., object] = save_account_payload
    service_accounts_payload: Callable[..., object] = service_accounts_payload

    def route_service_accounts(self, request, path: str, query: Query):
        if path == "/api/service-accounts":
            if request.command == "GET":
                return request.send_payload(200, self.service_accounts_payload())
            if request.command in {"POST", "PUT"}:
                if not request.ensure_writable("공유 모드에서는 계정 DB를 변경할 수 없습니다."):
                    return
                return request.send_payload(200, self.save_account_payload(request.read_json_body()))
        return NOT_HANDLED

    def route_remove_service_account(self, request, path: str, query: Query):
        account_match = re.match(r"^/api/service-accounts/([^/]+)$", path)
        if account_match and request.command == "DELETE":
            if not request.ensure_writable("공유 모드에서는 계정 DB를 변경할 수 없습니다."):
                return
            return request.send_payload(200, self.remove_account_payload(urllib.parse.unquote(account_match.group(1))))
        return NOT_HANDLED
