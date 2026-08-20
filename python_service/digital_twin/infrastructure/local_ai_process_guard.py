"""Cross-process capacity guard for local Codex invocations."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - the managed local runtime is POSIX.
    fcntl = None


class LocalAICapacityUnavailable(RuntimeError):
    pass


def _try_slot(lock_dir: Path, slot_index: int) -> Tuple[int, Path]:
    if fcntl is None:
        raise LocalAICapacityUnavailable("local AI capacity locking requires POSIX flock")
    path = lock_dir / ("slot-" + str(slot_index) + ".lock")
    descriptor = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return -1, path
    metadata = json.dumps({
        "pid": os.getpid(),
        "slot": slot_index,
        "startedAtEpoch": time.time(),
    }, separators=(",", ":")).encode("utf-8")
    os.ftruncate(descriptor, 0)
    os.write(descriptor, metadata)
    os.fsync(descriptor)
    return descriptor, path


@contextmanager
def local_ai_capacity_lease(
    lock_dir: Path,
    max_concurrent: int = 2,
    wait_seconds: float = 300,
    poll_seconds: float = 0.25,
) -> Iterator[Path]:
    """Lease one host-wide AI slot; flock releases automatically after a crash."""

    lock_dir = Path(lock_dir)
    lock_dir.mkdir(parents=True, exist_ok=True)
    maximum = max(1, min(8, int(max_concurrent or 2)))
    deadline = time.monotonic() + max(0.0, float(wait_seconds or 0))
    descriptor = -1
    slot_path = None
    while descriptor < 0:
        for slot_index in range(1, maximum + 1):
            descriptor, candidate = _try_slot(lock_dir, slot_index)
            if descriptor >= 0:
                slot_path = candidate
                break
        if descriptor >= 0:
            break
        if time.monotonic() >= deadline:
            raise LocalAICapacityUnavailable(
                "local AI capacity remained full for " + str(round(max(0.0, float(wait_seconds or 0)), 1)) + " seconds"
            )
        time.sleep(max(0.05, min(1.0, float(poll_seconds or 0.25))))
    try:
        yield slot_path
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def run_guarded(command: List[str], lock_dir: Path, max_concurrent: int, wait_seconds: float) -> int:
    if not command:
        raise ValueError("guarded AI command is empty")
    with local_ai_capacity_lease(lock_dir, max_concurrent=max_concurrent, wait_seconds=wait_seconds):
        parent_pid = os.getppid()
        process = subprocess.Popen(
            command,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            start_new_session=os.name != "nt",
        )

        def forward(signum, _frame) -> None:
            try:
                os.killpg(process.pid, signum) if os.name != "nt" else process.send_signal(signum)
            except (OSError, ProcessLookupError):
                return

        previous_handlers = {}
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, forward)
        try:
            while True:
                try:
                    return int(process.wait(timeout=0.5))
                except subprocess.TimeoutExpired:
                    # Some shell=True callers only terminate their direct shell
                    # on timeout. Reap the AI process if that shell disappears.
                    if parent_pid > 1 and os.getppid() == 1:
                        forward(signal.SIGTERM, None)
                        try:
                            return int(process.wait(timeout=2))
                        except subprocess.TimeoutExpired:
                            forward(signal.SIGKILL, None)
                            return int(process.wait())
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run one local AI process inside a host-wide capacity slot")
    parser.add_argument("--lock-dir", required=True)
    parser.add_argument("--max-concurrent", type=int, default=2)
    parser.add_argument("--wait-seconds", type=float, default=300)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    command = list(arguments.command or [])
    if command and command[0] == "--":
        command = command[1:]
    try:
        return run_guarded(
            command,
            Path(arguments.lock_dir),
            max_concurrent=arguments.max_concurrent,
            wait_seconds=arguments.wait_seconds,
        )
    except (LocalAICapacityUnavailable, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
