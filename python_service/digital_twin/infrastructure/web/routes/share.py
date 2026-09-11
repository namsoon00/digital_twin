"""Share HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.share_runtime import request_share_tunnel_rotation
from digital_twin.infrastructure.web.adapters.share import share_runtime_status_payload
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from typing import Callable


@dataclass(frozen=True)
class ShareRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    request_share_tunnel_rotation: Callable[..., object] = request_share_tunnel_rotation
    share_runtime_status_payload: Callable[..., object] = share_runtime_status_payload

    def route_share_access(self, request, path: str, query: Query):
        if path == "/api/share/access" and request.command == "GET":
            return request.send_payload(200, request.share_access().to_public_dict())
        if path == "/api/share/status" and request.command == "GET":
            return request.send_payload(200, self.share_runtime_status_payload(request.share_access()))
        if path == "/api/share/rotate" and request.command == "POST":
            if not request.ensure_writable("공유 보기 모드에서는 터널 주소를 갱신할 수 없습니다."):
                return
            body = request.read_json_body()
            payload = self.request_share_tunnel_rotation(
                reason=str(body.get("reason") or "manual"),
                requested_by=str(request.share_access().role or "local-owner"),
            )
            return request.send_payload(202, payload)
        return NOT_HANDLED
