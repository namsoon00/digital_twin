"""Web responses boundary."""

from digital_twin.infrastructure.web.common import safe_int
from digital_twin.infrastructure.web.telemetry import API_PERFORMANCE
from typing import Dict
import gzip
import json
import time


MAX_BODY_BYTES = 1024 * 1024


def read_json_body(self) -> Dict[str, object]:
    length = int(self.headers.get("Content-Length") or "0")
    if length > MAX_BODY_BYTES:
        raise ValueError("요청이 너무 큽니다.")
    if not length:
        return {}
    raw = self.rfile.read(length).decode("utf-8")
    return json.loads(raw) if raw else {}


def accepts_gzip(self) -> bool:
    return "gzip" in str(self.headers.get("Accept-Encoding") or "").lower()


def send_payload(
    self,
    status: int,
    payload,
    content_type: str = "application/json; charset=utf-8",
    cors: bool = False,
    cache_control: str = "no-store",
):
    no_body = status in {204, 304} or self.command == "HEAD"
    body = b"" if no_body else (
        json.dumps(payload, ensure_ascii=False).encode("utf-8") if content_type.startswith("application/json") else (
            payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        )
    )
    raw_length = len(body)
    compressed = False
    if not no_body and len(body) >= 1024 and self.accepts_gzip() and (content_type.startswith("application/json") or content_type.startswith("text/")):
        body = gzip.compress(body, compresslevel=6)
        compressed = True
    try:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
        self.send_header("Vary", "Accept-Encoding")
        request_id = str(getattr(self, "_request_id", "") or "")
        if request_id:
            self.send_header("X-Request-ID", request_id)
        started_at = getattr(self, "_request_started_at", None)
        if started_at is not None:
            self.send_header("Server-Timing", "app;dur=" + ("%.1f" % ((time.monotonic() - started_at) * 1000.0)))
        if status in {429, 502, 503, 504}:
            retry_after = 5
            if isinstance(payload, dict):
                retry_after = max(1, safe_int(payload.get("retryAfterSeconds"), 5, 1, 3600))
            self.send_header("Retry-After", str(retry_after))
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("X-Response-Raw-Bytes", str(raw_length))
        self.send_header("X-Response-Wire-Bytes", str(len(body)))
        if cors:
            self.add_cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not no_body:
            self.wfile.write(body)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
        return
    finally:
        started_at = getattr(self, "_request_started_at", None)
        duration_ms = (time.monotonic() - started_at) * 1000.0 if started_at is not None else 0.0
        API_PERFORMANCE.record(
            self.command,
            self.path_name(),
            status,
            duration_ms,
            raw_length,
            len(body),
            compressed,
        )


def add_cors_headers(self):
    self.send_header("Access-Control-Allow-Origin", "*")
    self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
    self.send_header("Access-Control-Allow-Headers", "Accept, Authorization, Content-Type, Cache-Control, Pragma, X-Requested-With")
    self.send_header("Access-Control-Allow-Private-Network", "true")
    self.send_header("Access-Control-Max-Age", "600")
    self.send_header("Vary", "Origin, Access-Control-Request-Headers, Access-Control-Request-Private-Network")


def send_redirect(self, location: str, cookie: str = ""):
    self.send_response(302)
    if cookie:
        self.send_header("Set-Cookie", cookie)
    self.send_header("Location", location)
    self.send_header("Cache-Control", "no-store")
    self.send_header("Referrer-Policy", "no-referrer")
    self.send_header("Content-Length", "0")
    self.end_headers()
