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


def sample_slot(started_at, now, interval, next_slot):
    """Skip missed wall-clock slots after sleep; never run a catch-up burst."""
    slot = max(next_slot, int(max(0, now - started_at) // interval))
    return slot, max(0, slot - next_slot), started_at + slot * interval


def _save_state(path, state):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as output:
        json.dump(state, output)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def supervise_samples(command, directory, *, ends_at, interval=300, timeout=90):
    """Persist passive samples independently of an interactive reporting turn.

    This starts no application work. Every child is disposable and owns its
    read-only sample. A lifecycle receipt is not proof that the child collected
    healthy data; query errors and evidence coverage remain in that sample.
    """
    if interval < 60 or timeout <= 0 or timeout >= interval:
        raise ValueError('observer interval/timeout must be bounded and non-overlapping')
    end = datetime.fromisoformat(ends_at.replace('Z', '+00:00'))
    if end.tzinfo is None:
        raise ValueError('observer cutoff requires a timezone')
    root = Path(directory).resolve()
    os.umask(0o077)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stopping = [False]
    def stop(_signal, _frame):
        stopping[0] = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with (root / 'observer-supervisor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pid_path, state_path = root / 'observer.pid', root / 'sample-schedule.json'
        if state_path.exists():
            state = json.loads(state_path.read_text())
            if state['endsAt'] != ends_at or state['intervalSeconds'] != interval:
                raise ValueError('existing observer schedule must not be silently replaced')
        else:
            state = {'startedAtEpoch': time.time(), 'endsAt': ends_at,
                     'intervalSeconds': interval, 'nextSlot': 0, 'skippedSlots': 0}
        pid_path.write_text(str(os.getpid()) + '\n')
        try:
            while not stopping[0] and not (root / 'STOP').exists():
                now = time.time()
                if now > end.timestamp():
                    break
                slot, missed, scheduled = sample_slot(state['startedAtEpoch'], now, interval, state['nextSlot'])
                if scheduled > end.timestamp():
                    break
                if scheduled > now:
                    time.sleep(min(0.5, scheduled - now))
                    continue
                # Advance before launching: a killed observer cannot silently
                # overwrite an interrupted sample as successful on restart.
                state.update(nextSlot=slot + 1, skippedSlots=state['skippedSlots'] + missed)
                _save_state(state_path, state)
                result = run_cycle(command, timeout=timeout, stop_requested=lambda: stopping[0])
                result.update(at=datetime.now(timezone.utc).isoformat(),
                              scheduledAt=datetime.fromtimestamp(scheduled, timezone.utc).isoformat(),
                              skippedSlots=missed, readOnly=True)
                with (root / 'observer-cycle-lifecycle.jsonl').open('a') as output:
                    output.write(json.dumps(result) + '\n')
                    output.flush()
                    os.fsync(output.fileno())
        finally:
            if pid_path.exists() and pid_path.read_text().strip() == str(os.getpid()):
                pid_path.unlink()


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
