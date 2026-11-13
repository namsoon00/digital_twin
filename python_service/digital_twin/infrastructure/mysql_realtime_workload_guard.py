"""Host-local serialization between realtime monitoring and MySQL cleanup."""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field
from pathlib import Path
import fcntl
import os
import time
from typing import Callable, Iterator, Optional
from .realtime_activity import RealtimeActivity


@dataclass(frozen=True)
class MySQLWorkloadLease:
    acquired: bool
    role: str
    waited_seconds: float = 0.0
    checkpoint: Optional[Callable[[], None]] = field(default=None, compare=False, repr=False)


class MySQLRealtimeWorkloadGuard:
    """Use ``flock`` so maintenance cannot overlap a monitor cycle.

    This lock is intentionally host-local. Orbit Alpha's MySQL and workers are
    project-managed local processes, and the OS releases the descriptor after
    a crash without requiring a stale-row recovery transaction.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.turnstile = self.path.with_name(self.path.name + ".admission")
        self.monitor_demand = self.path.with_name(self.path.name + ".monitor-demand")

    @contextmanager
    def monitor_cycle(self) -> Iterator[MySQLWorkloadLease]:
        """Give the monitor a durable turn, waiting rather than timing out."""

        with self._acquire(role="monitor-demand", blocking=True, path=self.monitor_demand), ExitStack() as held:
            with self._acquire(role="admission", blocking=True, path=self.turnstile):
                lease = held.enter_context(self._acquire(role="monitor", blocking=True, shared=True))
            with RealtimeActivity(self.path.parent / "realtime-activity.lock").turn():
                yield lease

    @contextmanager
    def market_source_turn(self):
        """Overlap collection, but never cleanup. Skip a busy turn without waiting.

        Admission's nonblocking probe respects an overdue maintenance waiter.
        The monitor/account row lock serializes only the final atomic write.
        """
        with ExitStack() as held:
            with self._acquire(role="admission", blocking=False, path=self.turnstile) as gate:
                if not gate.acquired:
                    yield MySQLWorkloadLease(False, "market-source")
                    return
                lease = held.enter_context(self._acquire(role="market-source", blocking=False, shared=True))
            with RealtimeActivity(self.path.parent / "realtime-activity.lock").turn():
                yield lease

    @contextmanager
    def maintenance_turn(self, wait_seconds: float = 0) -> Iterator[MySQLWorkloadLease]:
        """Overdue cleanup reserves the next gap, with a bounded lock wait."""

        deadline = time.monotonic() + max(0, min(60, float(wait_seconds)))
        with ExitStack() as held:
            gate = held.enter_context(self._acquire(role="admission", blocking=False, path=self.turnstile,
                           wait_seconds=max(0, deadline - time.monotonic())))
            if not gate.acquired:
                yield MySQLWorkloadLease(acquired=False, role="maintenance", waited_seconds=gate.waited_seconds)
                return
            lease = held.enter_context(self._acquire(role="maintenance", blocking=False,
                               wait_seconds=max(0, deadline - time.monotonic())))
            first_batch = True

            def checkpoint():
                nonlocal first_batch
                # An overdue turn earns one batch even under continuous demand.
                # Subsequent boundaries yield, preserving both disk cleanup and
                # monitor progress instead of starving either owner.
                if first_batch:
                    first_batch = False
                    return
                # Call only between committed batches. A waiting monitor owns
                # demand before joining admission, so it cannot lose this gap.
                with self._acquire(role="demand-probe", blocking=False, path=self.monitor_demand) as demand:
                    waiting = not demand.acquired
                if not waiting:
                    return
                held.close()
                with self._acquire(role="maintenance-resume", blocking=True, path=self.monitor_demand):
                    held.enter_context(self._acquire(role="admission", blocking=True, path=self.turnstile))
                    held.enter_context(self._acquire(role="maintenance", blocking=True))

            yield MySQLWorkloadLease(lease.acquired, lease.role, lease.waited_seconds,
                                    checkpoint if lease.acquired else None)

    @contextmanager
    def _acquire(self, *, role: str, blocking: bool, path=None, wait_seconds=0, shared=False) -> Iterator[MySQLWorkloadLease]:
        target = Path(path or self.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(target), os.O_CREAT | os.O_RDWR, 0o600)
        started = time.monotonic()
        acquired = False
        try:
            flags = (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | (0 if blocking else fcntl.LOCK_NB)
            while True:
                try:
                    fcntl.flock(descriptor, flags)
                    acquired = True
                    break
                except BlockingIOError:
                    remaining = wait_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        break
                    time.sleep(min(0.05, remaining))
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
