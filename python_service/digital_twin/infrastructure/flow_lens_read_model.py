"""Fast, read-only delivery of the latest Flow Lens snapshot.

The dashboard must never make a browser request wait on vendor collection or
ontology materialisation.  This small infrastructure read model serves the
last verified snapshot immediately and permits at most one background refresh
for each view key.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Tuple


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class FlowLensReadResult:
    snapshot: Optional[Dict[str, object]]
    status: str
    refreshing: bool = False
    source: str = ""
    refreshed_at: str = ""
    error: str = ""

    def metadata(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "ready": bool(self.snapshot),
            "refreshing": self.refreshing,
            "source": self.source,
            "refreshedAt": self.refreshed_at,
            "error": self.error,
        }


class FlowLensReadModel:
    """In-memory view cache backed by persisted monitoring state.

    ``snapshot_provider`` is intentionally called only by a background thread.
    ``persisted_provider`` must be a quick read from the monitor projection and
    may return ``None`` before the first monitor cycle.
    """

    def __init__(
        self,
        snapshot_provider: Callable[[bool, str], Dict[str, object]],
        persisted_provider: Callable[[str], Optional[Dict[str, object]]],
        on_refresh: Callable[[Dict[str, object]], None] = None,
    ):
        self.snapshot_provider = snapshot_provider
        self.persisted_provider = persisted_provider
        self.on_refresh = on_refresh
        self._cache: Dict[str, Tuple[Dict[str, object], str, str]] = {}
        self._refreshing = set()
        self._errors: Dict[str, str] = {}
        self._lock = threading.Lock()

    @staticmethod
    def view_key(mock: bool, watchlist_symbols: str) -> str:
        return ("mock" if mock else "live") + ":" + str(watchlist_symbols or "").strip().upper()

    def read(self, mock: bool = False, watchlist_symbols: str = "", refresh: bool = False) -> FlowLensReadResult:
        key = self.view_key(mock, watchlist_symbols)
        with self._lock:
            cached = self._cache.get(key)
            refreshing = key in self._refreshing
            error = self._errors.get(key, "")
        if cached:
            snapshot, source, refreshed_at = cached
            if refresh and not refreshing:
                self._start_refresh(key, mock, watchlist_symbols, prefer_persisted=False)
                refreshing = True
            return FlowLensReadResult(snapshot, "ready", refreshing, source, refreshed_at, error)

        # The monitor projection is also I/O.  A browser request must not wait
        # for an unavailable MySQL connection, so even this fallback read is
        # performed by the single-flight background worker.
        self._start_refresh(key, mock, watchlist_symbols, prefer_persisted=not mock)
        return FlowLensReadResult(None, "pending", True, "", "", error)

    def _remember(self, key: str, snapshot: Dict[str, object], source: str, refreshed_at: str = "") -> None:
        with self._lock:
            self._cache[key] = (dict(snapshot), source, refreshed_at or utc_now_iso())
            self._errors.pop(key, None)

    def _start_refresh(self, key: str, mock: bool, watchlist_symbols: str, prefer_persisted: bool) -> None:
        with self._lock:
            if key in self._refreshing:
                return
            self._refreshing.add(key)
        thread = threading.Thread(
            target=self._refresh,
            args=(key, mock, watchlist_symbols, prefer_persisted),
            name="flow-lens-refresh-" + key[:24],
            daemon=True,
        )
        thread.start()

    def _refresh(self, key: str, mock: bool, watchlist_symbols: str, prefer_persisted: bool) -> None:
        try:
            snapshot = None
            source = "live-refresh"
            if prefer_persisted:
                snapshot = self.persisted_provider(watchlist_symbols)
                source = "monitor-snapshot"
            if not snapshot:
                snapshot = self.snapshot_provider(mock, watchlist_symbols)
                source = "live-refresh"
            if not isinstance(snapshot, dict):
                raise ValueError("Flow Lens 스냅샷 형식이 올바르지 않습니다.")
            refreshed_at = str(snapshot.get("generatedAt") or utc_now_iso())
            self._remember(key, snapshot, source, refreshed_at)
            if self.on_refresh:
                self.on_refresh(snapshot)
        except Exception as error:  # noqa: BLE001 - read model preserves the previous verified view.
            with self._lock:
                self._errors[key] = str(error)[:240]
        finally:
            with self._lock:
                self._refreshing.discard(key)
