"""Cooperative process retirement at completed durable-work boundaries.

RSS alone misses memory already compressed/swapped out on macOS. This guard
reads the current physical footprint, collects unreachable Python objects on
pressure, then requests a clean exit. The service supervisor starts a fresh
process; rebuilding a runner in the same process would retain native memory.
"""

import ctypes
from functools import lru_cache
import gc
import json
import os
from pathlib import Path
import sys
import time


class _RUsageInfoV0(ctypes.Structure):
    # Public macOS sys/resource.h RUSAGE_INFO_V0 (96 bytes).
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "user_time", "system_time", "pkg_idle_wkups", "interrupt_wkups",
            "pageins", "wired_size", "resident_size", "phys_footprint",
            "proc_start_abstime", "proc_exit_abstime",
        )
    ]


@lru_cache(maxsize=1)
def _mac_rusage():
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    read = library.proc_pid_rusage
    read.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    read.restype = ctypes.c_int
    return read


def process_memory_bytes():
    """Return current accounted memory including swap, or None if unavailable."""
    try:
        if sys.platform == "darwin":
            usage = _RUsageInfoV0()
            if _mac_rusage()(os.getpid(), 0, ctypes.byref(usage)) == 0:
                return int(usage.phys_footprint)
        elif sys.platform.startswith("linux"):
            fields = {}
            for line in Path("/proc/self/status").read_text().splitlines():
                name, _, value = line.partition(":")
                if name in {"VmRSS", "VmSwap"}:
                    fields[name] = int(value.split()[0]) * 1024
            if "VmRSS" in fields and "VmSwap" in fields:
                return fields["VmRSS"] + fields["VmSwap"]
    except (OSError, AttributeError, ValueError, IndexError):
        pass
    return None


def _positive_env(name, default):
    try:
        value = int(os.environ.get(name, default))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


class WorkerLifetime:
    def __init__(self, *, max_bytes=2 * 1024 ** 3, max_seconds=3600,
                 check_seconds=60, memory_reader=process_memory_bytes,
                 clock=time.monotonic, collect=gc.collect, emit=print):
        self.max_bytes = max_bytes
        self.max_seconds = max_seconds
        self.check_seconds = check_seconds
        self.memory_reader = memory_reader
        self.clock = clock
        self.collect = collect
        self.emit = emit
        self.started = clock()
        self.next_check = self.started
        self.retired = False

    def should_retire(self):
        """Call only after the current job/transaction/receipt has settled."""
        if self.retired:
            return True
        now = self.clock()
        age = max(0, now - self.started)
        expired = age >= self.max_seconds
        if not expired and now < self.next_check:
            return False
        self.next_check = now + self.check_seconds
        before = self.memory_reader()
        after = before
        pressure = before is not None and before >= self.max_bytes
        if pressure:
            self.collect()
            after = self.memory_reader()
        # A failed second read cannot prove that pressure was relieved.
        self.retired = expired or (pressure and (after is None or after >= self.max_bytes))
        if pressure or self.retired:
            self.emit("worker-lifetime " + json.dumps({
                "pid": os.getpid(), "ageSeconds": round(age, 1),
                "memoryBeforeBytes": before, "memoryAfterBytes": after,
                "memoryLimitBytes": self.max_bytes,
                "action": "retire" if self.retired else "collected",
                "reason": "maximum-age" if expired else "memory-pressure",
                "boundary": "completed-turn",
            }, sort_keys=True))
        return self.retired


def managed_worker_lifetime():
    """Manual watches stay under their caller's lifecycle ownership."""
    if os.environ.get("ORBIT_MANAGED_WORKER_LIFETIME") != "1":
        return None
    return WorkerLifetime(
        max_bytes=_positive_env("ORBIT_WORKER_MAX_MEMORY_MB", 2048) * 1024 ** 2,
        max_seconds=_positive_env("ORBIT_WORKER_MAX_AGE_SECONDS", 3600),
    )
