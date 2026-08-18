"""Resolve an active Cloudflare share URL for outbound notification links."""

import json
import os
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlsplit


def default_share_runtime_state_path() -> Path:
    configured = str(os.environ.get("SHARE_RUNTIME_STATE_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "data" / "share-runtime.json"


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


class ActiveShareNotificationLinkResolver:
    """Prefer the live Cloudflare viewer URL without coupling application code to files."""

    def __init__(
        self,
        state_path: Path = None,
        process_alive: Callable[[int], bool] = None,
    ):
        self.state_path = Path(state_path) if state_path else default_share_runtime_state_path()
        self.process_alive = process_alive or process_is_alive

    def __call__(self, configured_url: str) -> str:
        active_url = self.active_viewer_url()
        return active_url or str(configured_url or "").strip()

    def active_viewer_url(self) -> str:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return ""
        if not isinstance(state, dict) or str(state.get("provider") or "").strip() != "cloudflared":
            return ""
        owner_pid = self._positive_pid(state.get("ownerPid"))
        tunnel_pid = self._positive_pid(state.get("tunnelPid"))
        if not self.process_alive(owner_pid) or not self.process_alive(tunnel_pid):
            return ""
        base_url = str(state.get("baseUrl") or "").strip()
        viewer_url = str(state.get("viewerUrl") or "").strip()
        if not self._valid_cloudflare_url(base_url, viewer_url):
            return ""
        return viewer_url

    @staticmethod
    def _positive_pid(value: object) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _valid_cloudflare_url(base_url: str, viewer_url: str) -> bool:
        base = urlsplit(base_url)
        viewer = urlsplit(viewer_url)
        if base.scheme != "https" or viewer.scheme != "https":
            return False
        if not base.hostname or not viewer.hostname or base.hostname != viewer.hostname:
            return False
        if base.port != viewer.port:
            return False
        query = dict(parse_qsl(viewer.query, keep_blank_values=True))
        return bool(str(query.get("share_token") or "").strip())
