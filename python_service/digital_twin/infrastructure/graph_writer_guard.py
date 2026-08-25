"""Local process ownership for one physical TypeDB graph database."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LocalGraphWriterGuard:
    """Hold one OS-released writer ownership lock per TypeDB database.

    The lock is intentionally process-local infrastructure, not an ontology
    fact.  The operating system releases it when the process exits, so a crash
    cannot leave a durable writer lease behind.  TypeDB transactions and
    generation manifests still remain short-lived and independently audited.
    """

    contract_version = "local-typedb-single-writer-v1"

    def __init__(
        self,
        graph_database: str,
        role: str,
        lock_directory: Path,
        deployment_id: str = "",
    ):
        self.graph_database = str(graph_database or "").strip()
        self.role = str(role or "graph-writer").strip()
        self.deployment_id = str(deployment_id or "").strip()
        digest = hashlib.sha256(self.graph_database.encode("utf-8")).hexdigest()[:20]
        self.path = Path(lock_directory) / ("typedb-writer-" + digest + ".lock")
        self._handle = None
        self._depth = 0
        self._acquired_at = ""

    @staticmethod
    def _owner_payload(handle) -> Dict[str, object]:
        try:
            handle.seek(0)
            value = json.loads(handle.read() or "{}")
        except (OSError, ValueError, TypeError):
            return {}
        return dict(value or {}) if isinstance(value, dict) else {}

    def acquire(self) -> Dict[str, object]:
        if self._handle is not None:
            self._depth += 1
            return {**self.status(), "acquired": True, "status": "adopted-local-writer"}
        if not self.graph_database:
            return {
                "acquired": False,
                "status": "graph-database-required",
                "contractVersion": self.contract_version,
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            owner = self._owner_payload(handle)
            handle.close()
            return {
                "acquired": False,
                "status": "held-by-other-process",
                "contractVersion": self.contract_version,
                "graphDatabase": self.graph_database,
                "requestedRole": self.role,
                "requestedDeploymentId": self.deployment_id,
                "owner": owner,
                "reason": "Another local process owns the TypeDB graph writer boundary.",
            }
        self._handle = handle
        self._depth = 1
        self._acquired_at = _utc_now_iso()
        payload = {
            "contractVersion": self.contract_version,
            "graphDatabase": self.graph_database,
            "role": self.role,
            "deploymentId": self.deployment_id,
            "host": socket.gethostname(),
            "processId": os.getpid(),
            "acquiredAt": self._acquired_at,
        }
        handle.seek(0)
        handle.truncate(0)
        handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
        return {**payload, "acquired": True, "status": "acquired"}

    def release(self) -> Dict[str, object]:
        if self._handle is None:
            return {"status": "not-owner", "released": False}
        self._depth = max(0, self._depth - 1)
        if self._depth:
            return {**self.status(), "status": "retained-by-outer-scope", "released": False}
        handle = self._handle
        self._handle = None
        try:
            payload = {
                "contractVersion": self.contract_version,
                "graphDatabase": self.graph_database,
                "role": self.role,
                "deploymentId": self.deployment_id,
                "processId": os.getpid(),
                "releasedAt": _utc_now_iso(),
            }
            handle.seek(0)
            handle.truncate(0)
            handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True))
            handle.flush()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
        self._acquired_at = ""
        return {**payload, "status": "released", "released": True}

    def status(self) -> Dict[str, object]:
        return {
            "contractVersion": self.contract_version,
            "graphDatabase": self.graph_database,
            "role": self.role,
            "deploymentId": self.deployment_id,
            "processId": os.getpid(),
            "acquired": self._handle is not None,
            "acquiredAt": self._acquired_at,
            "depth": self._depth,
            "lockPath": str(self.path),
        }
