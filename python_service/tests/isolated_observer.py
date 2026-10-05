"""Run passive observation cycles in disposable processes, preserving checkpoints.

No application imports, store construction, jobs, notifications or DB writes.
The child owns sample/checkpoint semantics; this supervisor only bounds lifetime.
"""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from datetime import datetime, timezone


def run_cycle(command, *, timeout=125, stop_requested=lambda: False):
    child = subprocess.Popen(command, start_new_session=True)
    started = time.monotonic()
    reason = "completed"
    try:
        while child.poll() is None:
            if stop_requested() or time.monotonic() - started >= timeout:
                reason = "stopped" if stop_requested() else "timeout"
                break
            time.sleep(0.1)
    finally:
        # Kill the private process group even if the leader exited while a native
        # helper survived. Never leave one cycle overlapping the next cycle.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
    return {"pid": child.pid, "returnCode": child.returncode, "reason": reason,
            "durationSeconds": round(time.monotonic() - started, 3)}


def supervise(command, directory, *, timeout=125):
    root = Path(directory)
    os.umask(0o077)
    stopping = [False]
    def stop(_signal, _frame):
        stopping[0] = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with (root / "observer-supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (root / "observer.pid").write_text(str(os.getpid()) + "\n")
        while not stopping[0] and not (root / "STOP").exists():
            state = json.loads((root / "observer-state-private.json").read_text())
            parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            final_tick = int((parse(state["endsAt"]) - parse(state["startedAt"])) // 60)
            if state["nextTick"] > final_tick:
                break
            # Do not retain the accumulating state in the supervisor heap.
            del state
            result = run_cycle(command, timeout=timeout, stop_requested=lambda: stopping[0])
            result["at"] = datetime.now(timezone.utc).isoformat()
            with (root / "observer-cycle-lifecycle.jsonl").open("a") as output:
                output.write(json.dumps(result) + "\n")
                output.flush()
                os.fsync(output.fileno())
            if result["returnCode"] != 0:
                # Failure is explicit in lifecycle evidence; the child's existing
                # nextTick accounting records the missing observation on recovery.
                end = time.monotonic() + 60
                while not stopping[0] and time.monotonic() < end:
                    time.sleep(0.2)
