"""Local process ownership for one physical TypeDB server."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import random
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalized_graph_address(value: str) -> str:
    address = str(value or "127.0.0.1:1729").strip().lower()
    if address.startswith("http://"):
        address = address[len("http://"):]
    elif address.startswith("https://"):
        address = address[len("https://"):]
    address = address.rstrip("/")
    if address.startswith("localhost:"):
        address = "127.0.0.1:" + address.split(":", 1)[1]
    return address or "127.0.0.1:1729"


class LocalGraphWriterGuard:
    """Hold one OS-released writer lock per TypeDB instance, across its databases.

    The lock is intentionally process-local infrastructure, not an ontology
    fact.  The operating system releases it when the process exits, so a crash
    cannot leave a durable writer lease behind.  TypeDB transactions and
    generation manifests still remain short-lived and independently audited.
    """

    contract_version = "local-typedb-single-writer-v3"

    def __init__(
        self,
        graph_database: str,
        role: str,
        lock_directory: Path,
        deployment_id: str = "",
        graph_address: str = "127.0.0.1:1729",
    ):
        self.graph_database = str(graph_database or "").strip()
        self.graph_address = _normalized_graph_address(graph_address)
        self.role = str(role or "graph-writer").strip()
        self.deployment_id = str(deployment_id or "").strip()
        scope = self.graph_address
        digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:20]
        self.path = Path(lock_directory) / ("typedb-writer-" + digest + ".lock")
        self._priority_path = self.path.with_suffix(".priority")
        self._cooldown_path = self.path.with_suffix(".cooldown")
        self._priority_handle = None
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
        # Delivery keeps a shared intent lock while waiting. Background writers
        # only probe the exclusive side, so a waiting delivery gets the next turn.
        priority = self._priority_handle or self._priority_path.open("a+")
        try:
            fcntl.flock(priority.fileno(),
                        (fcntl.LOCK_SH if self.role in {"delivery", "active"}
                         else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            priority.close()
            with self.path.open("a+", encoding="utf-8") as current:
                owner = self._owner_payload(current)
            return {"acquired": False, "status": "delivery-waiting",
                    "owner": owner, "contractVersion": self.contract_version}
        if self.role in {"delivery", "active"}:
            self._priority_handle = priority
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            owner = self._owner_payload(handle)
            handle.close()
            if self._priority_handle is None:
                priority.close()
            return {
                "acquired": False,
                "status": "held-by-other-process",
                "contractVersion": self.contract_version,
                "graphAddress": self.graph_address,
                "graphDatabase": self.graph_database,
                "requestedRole": self.role,
                "requestedDeploymentId": self.deployment_id,
                "owner": owner,
                "reason": "Another local process owns the TypeDB graph writer boundary.",
            }
        if self._priority_handle is None:
            priority.close()
        cooldown = self._read_cooldown()
        remaining = min(120, max(0, float(cooldown.get("until") or 0) - time.time()))
        if remaining:
            handle.close()
            # Keep delivery's reservation across cooldown polls, so a faster
            # candidate poll cannot steal the first admissible recovery turn.
            return {"acquired": False, "status": "server-cooling-down",
                    "retryAfterSeconds": math.ceil(remaining),
                    "contractVersion": self.contract_version}
        self._handle = handle
        self._depth = 1
        self._acquired_at = _utc_now_iso()
        payload = {
            "contractVersion": self.contract_version,
            "graphAddress": self.graph_address,
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
            self._release_priority()
            return {"status": "not-owner", "released": False}
        self._depth = max(0, self._depth - 1)
        if self._depth:
            return {**self.status(), "status": "retained-by-outer-scope", "released": False}
        handle = self._handle
        self._handle = None
        try:
            payload = {
                "contractVersion": self.contract_version,
                "graphAddress": self.graph_address,
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
            self._release_priority()
        self._acquired_at = ""
        return {**payload, "status": "released", "released": True}

    def status(self) -> Dict[str, object]:
        return {
            "contractVersion": self.contract_version,
            "graphAddress": self.graph_address,
            "graphDatabase": self.graph_database,
            "role": self.role,
            "deploymentId": self.deployment_id,
            "processId": os.getpid(),
            "acquired": self._handle is not None,
            "acquiredAt": self._acquired_at,
            "depth": self._depth,
            "lockPath": str(self.path),
            "lockScope": "server",
            "deliveryIntent": self._priority_handle is not None,
            "serverBackoff": self._read_cooldown(),
        }

    def _release_priority(self):
        if self._priority_handle is not None:
            self._priority_handle.close()
            self._priority_handle = None

    def _read_cooldown(self):
        try:
            value = json.loads(self._cooldown_path.read_text())
            if isinstance(value, dict):
                float(value.get("until") or 0)
                int(value.get("failures") or 0)
                return value
        except (OSError, ValueError, TypeError):
            pass
        return {}

    def record_result(self, turn):
        """Share overload backoff across databases, while still owning the lock.

        No queue or graph state is changed here. A crash retains the cooldown;
        idle turns cannot falsely reset the consecutive failure count.
        """
        if self._handle is None:
            raise RuntimeError("A writer must own the server before recording a result")
        result = dict(turn.get("result") or turn)
        code = result.get("reason_code") or result.get("reasonCode")
        transient = result.get("retryable") and code in {
            "typedbRequestError", "typedbTimeout", "typedbConnectionError",
            "typedbTransactionClosed", "typedbServerUnavailable",
        }
        state = self._read_cooldown()
        if transient:
            failures = min(6, max(0, int(state.get("failures") or 0)) + 1)
            delay = min(120, 5 * (2 ** (failures - 1)) + random.uniform(0, 5))
            state = {"failures": failures, "until": time.time() + delay,
                     "reasonCode": code}
        elif result.get("status") in {"completed", "ok"}:
            delay = 0
            state = {"failures": 0, "until": 0}
        else:
            return {}
        temp = self._cooldown_path.with_suffix(".writing")
        temp.write_text(json.dumps(state))
        os.replace(temp, self._cooldown_path)
        return {"retryAfterSeconds": math.ceil(delay), "consecutiveFailures": state["failures"]}
