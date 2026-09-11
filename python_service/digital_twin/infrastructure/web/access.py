"""Web access boundary."""

from digital_twin.infrastructure.share_access import SHARE_ROLE_OWNER
from digital_twin.infrastructure.share_access import SHARE_ROLE_VIEWER
from digital_twin.infrastructure.share_access import ShareAccess
from digital_twin.infrastructure.share_access import anonymous_access
from digital_twin.infrastructure.share_access import authenticate_share_token
from digital_twin.infrastructure.share_access import direct_loopback_request
from digital_twin.infrastructure.share_access import issue_share_session
from digital_twin.infrastructure.share_access import local_owner_access
from digital_twin.infrastructure.share_access import share_access_from_cookie
from digital_twin.infrastructure.share_access import share_mode_enabled
from digital_twin.infrastructure.share_access import share_session_cookie
from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import first_query
import urllib.error
import urllib.parse
import urllib.request


def share_denied_page() -> str:
    return "".join([
        "<!doctype html>",
        '<html lang="ko"><head><meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width, initial-scale=1" />',
        "<title>Orbit Alpha 접근 제한</title>",
        "<style>body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f6f4ee;color:#171717}main{max-width:520px;padding:32px;line-height:1.6}h1{font-size:22px;margin:0 0 10px}p{margin:0;color:#5f5a53}</style>",
        "</head><body><main><h1>공유 접근 토큰이 필요합니다.</h1>",
        "<p>서버를 공유한 사람이 제공한 전체 URL로 다시 접속하세요.</p>",
        "</main></body></html>",
    ])


def authorize_share(self) -> bool:
    if not share_mode_enabled():
        self._share_access = local_owner_access()
        return True
    if direct_loopback_request(self.client_address, self.headers):
        self._share_access = local_owner_access()
        return True
    parsed = self.parsed()
    query = self.parsed_query()
    supplied_owner = first_query(query, "owner_token")
    supplied_viewer = first_query(query, "share_token")
    supplied = supplied_owner or supplied_viewer
    requested_role = SHARE_ROLE_OWNER if supplied_owner else (SHARE_ROLE_VIEWER if supplied_viewer else "")
    access, matched_token = authenticate_share_token(supplied, requested_role)
    if access.authenticated:
        self._share_access = access
        clean_query = {key: values for key, values in query.items() if key not in {"share_token", "owner_token"}}
        encoded = urllib.parse.urlencode(clean_query, doseq=True)
        clean_path = (parsed.path or "/") + (("?" + encoded) if encoded else "")
        forwarded_proto = configured(self.headers.get("X-Forwarded-Proto")).lower()
        secure = forwarded_proto == "https" or configured(self.headers.get("CF-Visitor")).lower().find("https") >= 0
        self.send_redirect(
            clean_path,
            share_session_cookie(issue_share_session(access, matched_token), secure=secure),
        )
        return False
    access = share_access_from_cookie(self.headers.get("Cookie", ""))
    self._share_access = access
    if access.authenticated:
        return True
    if self.path_name().startswith("/api/"):
        self.send_payload(401, {"error": "공유 접근 토큰이 필요합니다."})
    else:
        self.send_payload(401, share_denied_page(), "text/html; charset=utf-8")
    return False


def share_access(self) -> ShareAccess:
    return getattr(self, "_share_access", local_owner_access() if not share_mode_enabled() else anonymous_access())


def ensure_writable(self, message: str) -> bool:
    if not self.share_access().writable:
        self.send_payload(403, {"error": message})
        return False
    return True
