"""Persistent stale-while-revalidate snapshots for optional dependencies."""

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict

from .settings import data_dir


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class StaleReadModelCache:
    """Keep the last usable payload while one background refresh is in flight."""

    def __init__(
        self,
        namespace: str,
        root: Path = None,
        ttl_seconds: int = 60,
        retry_cooldown_seconds: int = 15,
    ):
        self.namespace = str(namespace or "read-model").strip() or "read-model"
        self.root = Path(root or (data_dir() / "api-read-models"))
        self.ttl_seconds = max(1, int(ttl_seconds or 60))
        self.retry_cooldown_seconds = max(1, int(retry_cooldown_seconds or 15))
        self._lock = threading.Lock()
        self._inflight = set()

    def path_for(self, key: str) -> Path:
        digest = hashlib.sha256(str(key or "default").encode("utf-8")).hexdigest()[:32]
        return self.root / (self.namespace + "-" + digest + ".json")

    def read_entry(self, key: str) -> Dict[str, object]:
        try:
            payload = json.loads(self.path_for(key).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def write_entry(self, key: str, entry: Dict[str, object]) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + str(os.getpid()) + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(
            json.dumps(entry, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    @staticmethod
    def elapsed_seconds(epoch_value: object) -> int:
        try:
            return max(0, int(time.time() - float(epoch_value or 0)))
        except (TypeError, ValueError):
            return 0

    def snapshot(self, key: str) -> Dict[str, object]:
        entry = self.read_entry(key)
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        age_seconds = self.elapsed_seconds(entry.get("lastSuccessEpoch")) if payload else 0
        last_attempt_age = self.elapsed_seconds(entry.get("lastAttemptEpoch")) if entry.get("lastAttemptEpoch") else 0
        with self._lock:
            refreshing = key in self._inflight
        retry_after = 0
        if entry.get("lastError") and last_attempt_age < self.retry_cooldown_seconds:
            retry_after = self.retry_cooldown_seconds - last_attempt_age
        return {
            "payload": payload,
            "hasData": bool(payload),
            "stale": bool(payload) and age_seconds > self.ttl_seconds,
            "ageSeconds": age_seconds,
            "lastSuccessAt": str(entry.get("lastSuccessAt") or ""),
            "lastAttemptAt": str(entry.get("lastAttemptAt") or ""),
            "lastError": str(entry.get("lastError") or ""),
            "refreshing": refreshing,
            "retryAfterSeconds": retry_after,
        }

    def store_success(self, key: str, payload: Dict[str, object]) -> Dict[str, object]:
        stamp = utc_now_iso()
        entry = {
            "version": "stale-read-model-v1",
            "key": str(key or ""),
            "lastAttemptAt": stamp,
            "lastAttemptEpoch": time.time(),
            "lastSuccessAt": stamp,
            "lastSuccessEpoch": time.time(),
            "lastError": "",
            "payload": dict(payload or {}),
        }
        self.write_entry(key, entry)
        return self.snapshot(key)

    def store_failure(self, key: str, error: object) -> Dict[str, object]:
        entry = self.read_entry(key)
        entry.update({
            "version": "stale-read-model-v1",
            "key": str(key or ""),
            "lastAttemptAt": utc_now_iso(),
            "lastAttemptEpoch": time.time(),
            "lastError": str(error or "dependency refresh failed")[:500],
        })
        self.write_entry(key, entry)
        return self.snapshot(key)

    def refresh(self, key: str, loader: Callable[[], Dict[str, object]]) -> Dict[str, object]:
        try:
            payload = loader()
            if not isinstance(payload, dict) or not payload:
                raise ValueError("dependency returned an empty read model")
            return self.store_success(key, payload)
        except Exception as error:  # noqa: BLE001 - the last good payload remains authoritative.
            return self.store_failure(key, error)

    def refresh_async(self, key: str, loader: Callable[[], Dict[str, object]]) -> bool:
        current = self.snapshot(key)
        if current.get("retryAfterSeconds"):
            return False
        with self._lock:
            if key in self._inflight:
                return False
            self._inflight.add(key)

        def run() -> None:
            try:
                self.refresh(key, loader)
            finally:
                with self._lock:
                    self._inflight.discard(key)

        threading.Thread(
            target=run,
            name=self.namespace + "-refresh",
            daemon=True,
        ).start()
        return True
