"""Host-local serialization between realtime monitoring and MySQL cleanup."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import fcntl
import os
import time
from typing import Iterator


@dataclass(frozen=True)
class MySQLWorkloadLease:
    acquired: bool
    role: str
    waited_seconds: float = 0.0


class MySQLRealtimeWorkloadGuard:
    """Use ``flock`` so maintenance cannot overlap a monitor cycle.

    This lock is intentionally host-local. Orbit Alpha's MySQL and workers are
    project-managed local processes, and the OS releases the descriptor after
    a crash without requiring a stale-row recovery transaction.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def monitor_cycle(self) -> Iterator[MySQLWorkloadLease]:
        """Give the monitor a durable turn, waiting rather than timing out."""

        with self._acquire(role="monitor", blocking=True) as lease:
            yield lease

    @contextmanager
    def maintenance_turn(self) -> Iterator[MySQLWorkloadLease]:
        """Let low-priority maintenance yield immediately to the monitor."""

        with self._acquire(role="maintenance", blocking=False) as lease:
            yield lease

    @contextmanager
    def _acquire(self, *, role: str, blocking: bool) -> Iterator[MySQLWorkloadLease]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
        started = time.monotonic()
        acquired = False
        try:
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(descriptor, flags)
                acquired = True
            except BlockingIOError:
                acquired = False
            yield MySQLWorkloadLease(
                acquired=acquired,
                role=str(role or ""),
                waited_seconds=round(time.monotonic() - started, 4),
            )
        finally:
            if acquired:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(descriptor)
