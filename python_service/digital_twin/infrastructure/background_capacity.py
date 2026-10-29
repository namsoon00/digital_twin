"""Cooperative admission for managed research work, never live data delivery.

Check before claiming a job. Already-running turns keep their lease until
settlement; no transaction, child process or unrelated application is killed.
Kernel file locks release capacity after a crash without a stale lease timer.
"""

from contextlib import contextmanager
import ctypes
import fcntl
import json
import os
from pathlib import Path
import sys
import time


BACKGROUND_WORKERS = frozenset({
    "investment-research", "model-review", "historical-replay", "ontology-lab",
    "news-analysis", "ai-control",
})
REALTIME_WORKERS = frozenset({
    "monitor", "market-data", "kis-realtime", "reasoning-engine-delivery",
    "notifications", "typedb", "questdb", "time-series-projection",
})


def host_memory_pressure():
    """Read current pressure, not cumulative swap usage or process RSS."""
    try:
        if sys.platform == "darwin":
            level, size = ctypes.c_uint32(), ctypes.c_size_t(4)
            libc = ctypes.CDLL(None, use_errno=True)
            read = libc.sysctlbyname
            read.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                             ctypes.c_void_p, ctypes.c_size_t]
            read.restype = ctypes.c_int
            if read(b"kern.memorystatus_vm_pressure_level", ctypes.byref(level),
                    ctypes.byref(size), None, 0) != 0:
                return "unknown"
            # This sysctl returns dispatch flags, NOT the kernel internal enum.
            return {1: "normal", 2: "warning", 4: "critical"}.get(level.value, "unknown")
        if sys.platform.startswith("linux"):
            values = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, value = line.split(":", 1)
                values[key] = int(value.strip().split()[0])
            total, available = values.get("MemTotal", 0), values.get("MemAvailable")
            if total <= 0 or available is None:
                return "unknown"
            return "critical" if available / total < .05 else (
                "warning" if available / total < .15 else "normal"
            )
    except (OSError, AttributeError, ValueError, IndexError):
        pass
    return "unknown"


class BackgroundCapacity:
    def __init__(self, directory, slots=1, pressure_reader=host_memory_pressure,
                 clock=time.monotonic, emit=print):
        self.directory = Path(directory)
        self.slots = max(1, min(4, int(slots)))
        self.pressure_reader, self.clock, self.emit = pressure_reader, clock, emit
        self.last_pressure_check = None
        self.pressure = "unknown"
        self.resume_after = 0.0
        self.last_report = None
        self.last_status = ""

    def report(self, status, now):
        if status != self.last_status or self.last_report is None or now - self.last_report >= 60:
            self.emit("background-capacity " + json.dumps({
                "status": status, "memoryPressure": self.pressure,
                "slots": self.slots, "worker": os.environ.get("ORBIT_BACKGROUND_WORKER", ""),
            }), flush=True)
            self.last_report, self.last_status = now, status

    @contextmanager
    def acquire(self):
        now = self.clock()
        if self.last_pressure_check is None or now - self.last_pressure_check >= 5:
            self.pressure = self.pressure_reader()
            self.last_pressure_check = now
            if self.pressure == "critical":
                self.resume_after = now + 30
        if self.pressure == "critical" or now < self.resume_after:
            self.report("deferred-memory-pressure", now)
            yield False
            return
        handle = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            for index in range(self.slots):
                candidate = (self.directory / ("slot-" + str(index) + ".lock")).open("a+")
                try:
                    fcntl.flock(candidate.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    handle = candidate
                    break
                except BlockingIOError:
                    candidate.close()
                except OSError:
                    candidate.close()
                    raise
            self.report("admitted" if handle is not None else "deferred-background-capacity", now)
        except OSError:
            self.report("deferred-capacity-unavailable", now)
        try:
            yield handle is not None
        finally:
            if handle is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()


_managed_capacity = None
_managed_lifetime = None


def managed_background_call(callback, deferred_result=None):
    global _managed_capacity
    if os.environ.get("ORBIT_BACKGROUND_ADMISSION") != "1":
        return callback()
    if _managed_capacity is None:
        from .settings import data_dir
        _managed_capacity = BackgroundCapacity(data_dir() / "background-capacity")
    with _managed_capacity.acquire() as admitted:
        return callback() if admitted else deferred_result


def managed_background_should_retire():
    """Only the managed caller checks this after a turn has settled."""
    global _managed_lifetime
    if os.environ.get("ORBIT_BACKGROUND_ADMISSION") != "1":
        return False
    if _managed_lifetime is None:
        from .worker_lifetime import managed_worker_lifetime
        _managed_lifetime = managed_worker_lifetime()
    return _managed_lifetime is not None and _managed_lifetime.should_retire()
