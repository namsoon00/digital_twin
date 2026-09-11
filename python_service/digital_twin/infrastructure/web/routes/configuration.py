"""Configuration HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.web.adapters.configuration import save_settings_payload
from digital_twin.infrastructure.web.adapters.configuration import settings_status_payload
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from typing import Callable


@dataclass(frozen=True)
class ConfigurationRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    save_settings_payload: Callable[..., object] = save_settings_payload
    settings_status_payload: Callable[..., object] = settings_status_payload

    def route_settings(self, request, path: str, query: Query):
        if path == "/api/settings":
            if request.command == "GET":
                return request.send_payload(200, self.settings_status_payload(request.share_access()))
            if request.command == "PUT":
                if not request.ensure_writable("공유 모드에서는 서버 설정을 변경할 수 없습니다."):
                    return
                return request.send_payload(200, self.save_settings_payload(request.read_json_body(), request.share_access()))
        return NOT_HANDLED
