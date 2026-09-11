"""Common access, error and transport boundary for the business HTTP routers."""

import os
import re
import time
import urllib.error
import urllib.parse
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Callable, Dict, List

from digital_twin.infrastructure.external_signal_utils import ExternalCircuitOpen, ExternalRateLimited
from digital_twin.infrastructure.operational_error_reporting import operational_error_reporter, report_runtime_error
from digital_twin.infrastructure.share_access import ShareAccess

from .access import authorize_share, ensure_writable, share_access
from .responses import accepts_gzip, add_cors_headers, read_json_body, send_payload, send_redirect
from .router import ApiRouter, NOT_HANDLED
from .static import serve_static
from .websocket import handle_websocket


@dataclass(frozen=True)
class AccessPolicy:
    authorize: Callable[[object], bool] = authorize_share
    resolve: Callable[[object], ShareAccess] = share_access


class WebRequestHandler(BaseHTTPRequestHandler):
    server_version = "DigitalTwinPython/0.1"
    api_router: ApiRouter
    access_policy = AccessPolicy()

    def log_message(self, format, *args):
        if os.environ.get("WEB_SERVER_LOG_REQUESTS") == "1":
            super().log_message(format, *args)

    def do_OPTIONS(self):
        self.handle_request()

    def do_GET(self):
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self.handle_websocket()
            return
        self.handle_request()

    def do_HEAD(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_PUT(self):
        self.handle_request()

    def do_PATCH(self):
        self.handle_request()

    def do_DELETE(self):
        self.handle_request()

    def parsed(self):
        return urllib.parse.urlsplit(self.path)

    def parsed_query(self) -> Dict[str, List[str]]:
        return urllib.parse.parse_qs(self.parsed().query, keep_blank_values=True)

    def path_name(self) -> str:
        return urllib.parse.unquote(self.parsed().path or "/")

    def handle_websocket(self):
        return handle_websocket(self)

    def read_json_body(self) -> Dict[str, object]:
        return read_json_body(self)

    def accepts_gzip(self) -> bool:
        return accepts_gzip(self)

    def send_payload(self, status, payload, content_type="application/json; charset=utf-8", cors=False, cache_control="no-store"):
        return send_payload(self, status, payload, content_type, cors, cache_control)

    def add_cors_headers(self):
        return add_cors_headers(self)

    def send_redirect(self, location: str, cookie: str = ""):
        return send_redirect(self, location, cookie)

    def authorize_share(self) -> bool:
        return self.access_policy.authorize(self)

    def share_access(self) -> ShareAccess:
        return self.access_policy.resolve(self)

    def ensure_writable(self, message: str) -> bool:
        return ensure_writable(self, message)

    def serve_static(self, path: str):
        return serve_static(self, path)

    def handle_api(self, path: str):
        result = self.api_router.dispatch(self, path, self.parsed_query())
        if result is NOT_HANDLED:
            return self.send_payload(404, {"error": "API를 찾지 못했습니다."})
        return result

    def handle_request(self):
        supplied_request_id = str(self.headers.get("X-Request-ID") or "").strip()
        self._request_id = supplied_request_id[:80] if re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", supplied_request_id) else uuid.uuid4().hex
        self._request_started_at = time.monotonic()
        if not self.authorize_share():
            return
        path = self.path_name()
        try:
            if (
                path.startswith("/api/")
                and self.command in {"POST", "PUT", "PATCH", "DELETE"}
                and not self.share_access().writable
            ):
                return self.send_payload(403, {"error": "조회 전용 링크에서는 데이터를 변경할 수 없습니다."})
            if path.startswith("/api/"):
                self.handle_api(path)
            else:
                self.serve_static(path)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
        except ValueError as error:
            self.send_payload(400, {
                "status": "error",
                "error": str(error) or "잘못된 요청입니다.",
                "retryable": False,
                "requestId": self._request_id,
            })
        except (urllib.error.URLError, TimeoutError, ExternalCircuitOpen, ExternalRateLimited) as error:
            report_runtime_error(operational_error_reporter(), "Python web server", error, "HTTP 502 " + path)
            self.send_payload(502, {
                "status": "unavailable",
                "error": str(error) or "외부 데이터 요청 실패",
                "retryable": True,
                "retryAfterSeconds": 5,
                "requestId": self._request_id,
                "dependencyStatus": {"externalApi": "unavailable"},
            })
        except Exception as error:
            report_runtime_error(operational_error_reporter(), "Python web server", error, "HTTP 500 " + path)
            self.send_payload(500, {
                "status": "error",
                "error": str(error) or "서버 오류",
                "retryable": False,
                "requestId": self._request_id,
            })


def make_handler(router: ApiRouter, access_policy: AccessPolicy = None):
    """Bind dependencies once per server, never by mutating a module namespace."""
    class DigitalTwinHandler(WebRequestHandler):
        api_router = router

    if access_policy is not None:
        DigitalTwinHandler.access_policy = access_policy
    return DigitalTwinHandler
