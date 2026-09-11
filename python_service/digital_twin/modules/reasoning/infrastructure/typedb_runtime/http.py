"""Dependency-light TypeDB HTTP transport, imported without opening a connection."""
import json
import urllib.error
import urllib.request

from typing import Dict

from .ports import HttpPort


def typedb_http_json_request(store: HttpPort, path: str, payload: Dict[str, object], timeout_seconds: float, token: str='') -> Dict[str, object]:
    """Call the local TypeDB HTTP API without introducing a new client dependency."""

    base = store.http_address
    if not base:
        raise RuntimeError("TypeDB HTTP address is unavailable.")
    if "://" not in base:
        base = "http://" + base
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        base + "/" + str(path or "").lstrip("/"),
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
            raw = response.read().decode("utf-8").strip()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(
            "TypeDB HTTP " + str(error.code) + ": " + detail
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError("TypeDB HTTP connection failed: " + str(error.reason)) from error
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("TypeDB HTTP returned invalid JSON: " + raw[:300]) from error
    return dict(parsed or {}) if isinstance(parsed, dict) else {"result": parsed}
