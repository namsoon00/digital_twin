"""Cross-process capacity guard for local Codex invocations."""

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple, Union

try:
    import fcntl
except ImportError:  # pragma: no cover - the managed local runtime is POSIX.
    fcntl = None


class LocalAICapacityUnavailable(RuntimeError):
    pass


def terminate_process_group(process: subprocess.Popen, force: bool = False) -> None:
    """Terminate a managed AI process and every child in its session."""

    if process is None or process.poll() is not None:
        return
    signum = signal.SIGKILL if force else signal.SIGTERM
    if os.name != "nt":
        try:
            os.killpg(process.pid, signum)
            return
        except (OSError, ProcessLookupError):
            pass
    try:
        process.kill() if force else process.terminate()
    except (OSError, ProcessLookupError):
        return


@contextmanager
def forward_termination_signals(process: subprocess.Popen):
    """Forward worker termination to its isolated AI process group."""

    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous_handlers = {}

    def forward(signum, _frame) -> None:
        terminate_process_group(process, force=False)
        raise SystemExit(128 + int(signum))

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, forward)
    try:
        yield
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


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
    wait_seconds: Optional[float] = 300,
    poll_seconds: float = 0.25,
    lane: str = "background",
    reserved_priority_slots: int = 1,
    cancel_event=None,
) -> Iterator[Path]:
    """Lease one host-wide AI slot; flock releases automatically after a crash."""

    lock_dir = Path(lock_dir)
    lock_dir.mkdir(parents=True, exist_ok=True)
    maximum = max(1, min(8, int(max_concurrent or 2)))
    wait_value = None if wait_seconds is None else float(wait_seconds or 0)
    deadline = None if wait_value is None or wait_value <= 0 else time.monotonic() + wait_value
    normalized_lane = str(lane or "background").strip().lower()
    reserved = max(0, min(maximum - 1, int(reserved_priority_slots or 0)))
    if normalized_lane in {"investment", "investment-judgement", "priority"}:
        slot_indexes = list(range(1, maximum + 1))
    else:
        slot_indexes = list(range(reserved + 1, maximum + 1)) or [maximum]
    descriptor = -1
    slot_path = None
    while descriptor < 0:
        if cancel_event is not None and cancel_event.is_set():
            raise LocalAICapacityUnavailable("local AI capacity wait was cancelled")
        for slot_index in slot_indexes:
            descriptor, candidate = _try_slot(lock_dir, slot_index)
            if descriptor >= 0:
                slot_path = candidate
                break
        if descriptor >= 0:
            break
        if deadline is not None and time.monotonic() >= deadline:
            raise LocalAICapacityUnavailable(
                "local AI capacity remained full for " + str(round(max(0.0, wait_value or 0), 1)) + " seconds"
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


def run_ai_prompt_command(
    command: Union[List[str], str],
    prompt: str,
    *,
    lock_dir: Path,
    max_concurrent: int = 2,
    wait_seconds: Optional[float] = 300,
    lane: str = "background",
    reserved_priority_slots: int = 1,
    timeout_seconds: Optional[float] = None,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Run one prompt with capacity control and process-group cleanup.

    Application adapters use direct argv. Supporting a string keeps tests and
    compatibility callers working, but production Codex calls never need a
    shell wrapper whose timeout could orphan the actual model process.
    """

    if not command:
        raise ValueError("guarded AI command is empty")
    timeout_value = None
    if timeout_seconds not in (None, ""):
        timeout_value = float(timeout_seconds)
        if timeout_value <= 0:
            timeout_value = None
    with local_ai_capacity_lease(
        lock_dir,
        max_concurrent=max_concurrent,
        wait_seconds=wait_seconds,
        lane=lane,
        reserved_priority_slots=reserved_priority_slots,
    ):
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=isinstance(command, str),
            cwd=str(cwd) if cwd else None,
            env=env,
            start_new_session=os.name != "nt",
        )
        try:
            with forward_termination_signals(process):
                stdout, stderr = process.communicate(input=prompt, timeout=timeout_value)
        except subprocess.TimeoutExpired as error:
            terminate_process_group(process, force=False)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                terminate_process_group(process, force=True)
                process.wait(timeout=2)
            process.communicate()
            raise error
        except BaseException:
            terminate_process_group(process, force=False)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                terminate_process_group(process, force=True)
                process.wait(timeout=2)
            process.communicate()
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def run_guarded(
    command: List[str],
    lock_dir: Path,
    max_concurrent: int,
    wait_seconds: float,
    lane: str = "background",
    reserved_priority_slots: int = 1,
) -> int:
    if not command:
        raise ValueError("guarded AI command is empty")
    with local_ai_capacity_lease(
        lock_dir,
        max_concurrent=max_concurrent,
        wait_seconds=wait_seconds,
        lane=lane,
        reserved_priority_slots=reserved_priority_slots,
    ):
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
    parser.add_argument("--lane", default="background")
    parser.add_argument("--reserved-priority-slots", type=int, default=1)
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
            lane=arguments.lane,
            reserved_priority_slots=arguments.reserved_priority_slots,
        )
    except (LocalAICapacityUnavailable, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
