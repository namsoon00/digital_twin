"""Private runtime state for the account-free external preview tunnel."""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping
from urllib.parse import urlencode, urlsplit, urlunsplit


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_FIXED_ENTRY_URL = "https://namsoon00.github.io/digital_twin/live/"


def _configured(value: object) -> str:
    return str(value or "").strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def share_credentials_path(environment: Mapping[str, object] = None) -> Path:
    source = environment if environment is not None else os.environ
    configured = _configured(source.get("SHARE_CREDENTIALS_PATH"))
    return Path(configured).expanduser().resolve() if configured else ROOT_DIR / "data" / "share-access.json"


def share_runtime_state_path(environment: Mapping[str, object] = None) -> Path:
    source = environment if environment is not None else os.environ
    configured = _configured(source.get("SHARE_RUNTIME_STATE_PATH"))
    return Path(configured).expanduser().resolve() if configured else ROOT_DIR / "data" / "share-runtime.json"


def _read_object(path: Path) -> Dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_private_object(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _session_days(value: object) -> int:
    try:
        return max(1, min(90, int(value or 30)))
    except (TypeError, ValueError):
        return 30


def load_or_create_share_credentials(
    environment: Mapping[str, object] = None,
    path: Path = None,
) -> Dict[str, object]:
    """Return persistent credentials without exposing them through settings storage."""

    source = environment if environment is not None else os.environ
    target = Path(path) if path else share_credentials_path(source)
    saved = _read_object(target)
    credentials = {
        "viewToken": _configured(source.get("SHARE_VIEW_TOKEN") or source.get("SHARE_TOKEN") or saved.get("viewToken"))
        or secrets.token_urlsafe(24),
        "ownerToken": _configured(source.get("SHARE_OWNER_TOKEN") or saved.get("ownerToken"))
        or secrets.token_urlsafe(24),
        "sessionSecret": _configured(source.get("SHARE_SESSION_SECRET") or saved.get("sessionSecret"))
        or secrets.token_urlsafe(32),
        "sessionDays": _session_days(source.get("SHARE_SESSION_DAYS") or saved.get("sessionDays")),
        "createdAt": _configured(saved.get("createdAt")) or _utc_now(),
        "updatedAt": _configured(saved.get("updatedAt")) or _utc_now(),
    }
    stable_keys = ("viewToken", "ownerToken", "sessionSecret", "sessionDays", "createdAt")
    if not saved or any(saved.get(key) != credentials.get(key) for key in stable_keys):
        credentials["updatedAt"] = _utc_now()
        _write_private_object(target, credentials)
    return credentials


def share_credentials_environment(
    environment: Mapping[str, object] = None,
    path: Path = None,
) -> Dict[str, str]:
    credentials = load_or_create_share_credentials(environment=environment, path=path)
    return {
        "SHARE_TOKEN": str(credentials["viewToken"]),
        "SHARE_VIEW_TOKEN": str(credentials["viewToken"]),
        "SHARE_OWNER_TOKEN": str(credentials["ownerToken"]),
        "SHARE_SESSION_SECRET": str(credentials["sessionSecret"]),
        "SHARE_SESSION_DAYS": str(credentials["sessionDays"]),
    }


def fixed_entry_url(settings: Mapping[str, object] = None, environment: Mapping[str, object] = None) -> str:
    source = environment if environment is not None else os.environ
    configured = _configured(
        source.get("SHARE_FIXED_ENTRY_URL")
        or dict(settings or {}).get("cloudflareShareFixedEntryUrl")
        or DEFAULT_FIXED_ENTRY_URL
    )
    parsed = urlsplit(configured)
    if parsed.scheme != "https" or not parsed.hostname:
        return DEFAULT_FIXED_ENTRY_URL
    path = parsed.path or "/"
    if not path.endswith("/"):
        path += "/"
    return urlunsplit(("https", parsed.netloc, path, parsed.query, ""))


def fixed_access_url(entry_url: str, token_name: str, token: str) -> str:
    parsed = urlsplit(_configured(entry_url))
    if parsed.scheme != "https" or not parsed.hostname or token_name not in {"share_token", "owner_token"}:
        return ""
    fragment = urlencode({token_name: _configured(token)})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, fragment)) if fragment else ""


def read_share_runtime_state(path: Path = None) -> Dict[str, object]:
    return _read_object(Path(path) if path else share_runtime_state_path())


def process_is_alive(pid: object) -> bool:
    try:
        resolved = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if resolved <= 0:
        return False
    try:
        os.kill(resolved, 0)
        return True
    except (OSError, ValueError):
        return False


def active_share_runtime_state(path: Path = None) -> Dict[str, object]:
    state = read_share_runtime_state(path)
    if not state:
        return {}
    if not process_is_alive(state.get("ownerPid")) or not process_is_alive(state.get("tunnelPid")):
        return {}
    return state
