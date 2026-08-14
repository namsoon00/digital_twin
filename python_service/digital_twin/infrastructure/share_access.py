"""Role-based access for an intentionally exposed local web server."""

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Dict, Iterable, Tuple


SHARE_SESSION_COOKIE = "dt_share_session"
SHARE_ROLE_ANONYMOUS = "anonymous"
SHARE_ROLE_VIEWER = "viewer"
SHARE_ROLE_OWNER = "owner"
SHARE_ROLE_LOCAL_OWNER = "local-owner"
SHARE_CAPABILITY_READ = "read"
SHARE_CAPABILITY_WRITE = "write"
DEFAULT_SESSION_DAYS = 30


def configured(value: object) -> str:
    return str(value or "").strip()


def _unique(values: Iterable[str]) -> Tuple[str, ...]:
    result = []
    for value in values:
        clean = configured(value)
        if clean and clean not in result:
            result.append(clean)
    return tuple(result)


def viewer_tokens(environment: Dict[str, str] = None) -> Tuple[str, ...]:
    source = environment if environment is not None else os.environ
    return _unique((source.get("SHARE_VIEW_TOKEN"), source.get("SHARE_TOKEN")))


def owner_tokens(environment: Dict[str, str] = None) -> Tuple[str, ...]:
    source = environment if environment is not None else os.environ
    return _unique((source.get("SHARE_OWNER_TOKEN"),))


def share_mode_enabled(environment: Dict[str, str] = None) -> bool:
    return bool(viewer_tokens(environment) or owner_tokens(environment))


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(configured(token).encode("utf-8")).hexdigest()[:24]


def _session_secret(environment: Dict[str, str] = None) -> bytes:
    source = environment if environment is not None else os.environ
    explicit = configured(source.get("SHARE_SESSION_SECRET"))
    if explicit:
        return explicit.encode("utf-8")
    material = "|".join(viewer_tokens(source) + owner_tokens(source))
    return hashlib.sha256(("orbit-alpha-share-session|" + material).encode("utf-8")).digest()


def _session_days(environment: Dict[str, str] = None) -> int:
    source = environment if environment is not None else os.environ
    try:
        return max(1, min(90, int(source.get("SHARE_SESSION_DAYS") or DEFAULT_SESSION_DAYS)))
    except (TypeError, ValueError):
        return DEFAULT_SESSION_DAYS


def _base64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _base64url_decode(payload: str) -> bytes:
    clean = configured(payload)
    return base64.urlsafe_b64decode(clean + ("=" * ((4 - len(clean) % 4) % 4)))


def _role_tokens(role: str, environment: Dict[str, str] = None) -> Tuple[str, ...]:
    if role == SHARE_ROLE_OWNER:
        return owner_tokens(environment)
    if role == SHARE_ROLE_VIEWER:
        return viewer_tokens(environment)
    return ()


def _role_capabilities(role: str) -> Tuple[str, ...]:
    if role in {SHARE_ROLE_OWNER, SHARE_ROLE_LOCAL_OWNER}:
        return (SHARE_CAPABILITY_READ, SHARE_CAPABILITY_WRITE)
    if role == SHARE_ROLE_VIEWER:
        return (SHARE_CAPABILITY_READ,)
    return ()


@dataclass(frozen=True)
class ShareAccess:
    role: str
    expires_at: int = 0

    @property
    def authenticated(self) -> bool:
        return self.role != SHARE_ROLE_ANONYMOUS

    @property
    def shared(self) -> bool:
        return self.role in {SHARE_ROLE_VIEWER, SHARE_ROLE_OWNER}

    @property
    def writable(self) -> bool:
        return SHARE_CAPABILITY_WRITE in self.capabilities

    @property
    def capabilities(self) -> Tuple[str, ...]:
        return _role_capabilities(self.role)

    def to_public_dict(self) -> Dict[str, object]:
        return {
            "role": self.role,
            "authenticated": self.authenticated,
            "shared": self.shared,
            "writable": self.writable,
            "capabilities": list(self.capabilities),
            "expiresAt": int(self.expires_at or 0),
        }


def local_owner_access() -> ShareAccess:
    return ShareAccess(SHARE_ROLE_LOCAL_OWNER)


def anonymous_access() -> ShareAccess:
    return ShareAccess(SHARE_ROLE_ANONYMOUS)


def authenticate_share_token(
    supplied: str,
    requested_role: str = "",
    environment: Dict[str, str] = None,
) -> Tuple[ShareAccess, str]:
    clean = configured(supplied)
    if not clean:
        return anonymous_access(), ""
    roles = (requested_role,) if requested_role in {SHARE_ROLE_OWNER, SHARE_ROLE_VIEWER} else (
        SHARE_ROLE_OWNER,
        SHARE_ROLE_VIEWER,
    )
    for role in roles:
        for expected in _role_tokens(role, environment):
            if hmac.compare_digest(clean, expected):
                expires_at = int(time.time()) + (_session_days(environment) * 86400)
                return ShareAccess(role, expires_at), expected
    return anonymous_access(), ""


def issue_share_session(access: ShareAccess, matched_token: str, environment: Dict[str, str] = None) -> str:
    if access.role not in {SHARE_ROLE_OWNER, SHARE_ROLE_VIEWER} or not matched_token:
        return ""
    payload = {
        "v": 1,
        "r": access.role,
        "e": int(access.expires_at),
        "f": _token_fingerprint(matched_token),
    }
    encoded = _base64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(_session_secret(environment), encoded.encode("ascii"), hashlib.sha256).digest()
    return encoded + "." + _base64url_encode(signature)


def verify_share_session(session: str, environment: Dict[str, str] = None) -> ShareAccess:
    try:
        encoded, supplied_signature = configured(session).split(".", 1)
        expected_signature = hmac.new(_session_secret(environment), encoded.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_base64url_decode(supplied_signature), expected_signature):
            return anonymous_access()
        payload = json.loads(_base64url_decode(encoded).decode("utf-8"))
        role = configured(payload.get("r"))
        expires_at = int(payload.get("e") or 0)
        fingerprint = configured(payload.get("f"))
        if int(payload.get("v") or 0) != 1 or expires_at <= int(time.time()):
            return anonymous_access()
        if not any(hmac.compare_digest(fingerprint, _token_fingerprint(token)) for token in _role_tokens(role, environment)):
            return anonymous_access()
        return ShareAccess(role, expires_at)
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return anonymous_access()


def share_access_from_cookie(cookie_header: str, environment: Dict[str, str] = None) -> ShareAccess:
    try:
        cookies = SimpleCookie()
        cookies.load(str(cookie_header or ""))
        morsel = cookies.get(SHARE_SESSION_COOKIE)
        return verify_share_session(morsel.value if morsel else "", environment)
    except (KeyError, TypeError):
        return anonymous_access()


def share_session_cookie(
    session: str,
    secure: bool = False,
    environment: Dict[str, str] = None,
) -> str:
    parts = [
        SHARE_SESSION_COOKIE + "=" + configured(session),
        "Path=/",
        "HttpOnly",
        "SameSite=Strict",
        "Max-Age=" + str(_session_days(environment) * 86400),
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)
