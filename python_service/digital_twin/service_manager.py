import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List

from .infrastructure.settings import ROOT_DIR, data_dir


PID_PATH = data_dir() / "python-monitor.pid"
LOG_PATH = data_dir() / "python-monitor.log"


def read_pid() -> int:
    try:
        return int(PID_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def command_for_pid(pid: int) -> str:
    if not pid:
        return ""
    try:
        output = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True, stderr=subprocess.DEVNULL)
        return output.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def is_worker_command(command: str) -> bool:
    return "python_service/service.py monitor watch" in command


def is_running(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    if os.name != "nt":
        return is_worker_command(command_for_pid(pid))
    return True


def remove_pid() -> None:
    try:
        PID_PATH.unlink()
    except FileNotFoundError:
        return


def append_log(label: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write("\n[" + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "] manager " + label + "\n")


def tail(path: Path, count: int = 8) -> List[str]:
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        return lines[-count:]
    except OSError:
        return []


def status() -> int:
    pid = read_pid()
    running = is_running(pid)
    print("Python realtime monitor: " + ("running" if running else "stopped"))
    if pid:
        print("PID: " + str(pid))
    if running:
        print("Command: " + command_for_pid(pid))
    if LOG_PATH.exists():
        print("Log: " + str(LOG_PATH))
        print("Log updated: " + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(LOG_PATH.stat().st_mtime)))
        recent = tail(LOG_PATH)
        if recent:
            print("Recent log:")
            for line in recent:
                print(line)
    else:
        print("Log: " + str(LOG_PATH) + " (not created)")
    if pid and not running:
        remove_pid()
    return 0


def start() -> int:
    existing = read_pid()
    if is_running(existing):
        print("Python realtime monitor already running.")
        return status()
    if existing:
        remove_pid()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    append_log("start")
    out = LOG_PATH.open("a", encoding="utf-8")
    command = [sys.executable, "-u", "python_service/service.py", "monitor", "watch"]
    process = subprocess.Popen(
        command,
        cwd=str(ROOT_DIR),
        env=dict(os.environ, PYTHONUNBUFFERED="1"),
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=out,
        start_new_session=True,
    )
    PID_PATH.write_text(str(process.pid) + "\n", encoding="utf-8")
    os.chmod(PID_PATH, 0o600)
    print("Python realtime monitor started. pid=" + str(process.pid))
    print("Log: " + str(LOG_PATH))
    return 0


def stop() -> int:
    pid = read_pid()
    if not pid:
        print("Python realtime monitor is not running.")
        return 0
    if not is_running(pid):
        remove_pid()
        print("Python realtime monitor was not running. Removed stale pid file.")
        return 0
    os.kill(pid, signal.SIGTERM)
    for _index in range(25):
        time.sleep(0.2)
        if not is_running(pid):
            remove_pid()
            append_log("stop")
            print("Python realtime monitor stopped. pid=" + str(pid))
            return 0
    os.kill(pid, signal.SIGKILL)
    remove_pid()
    append_log("kill")
    print("Python realtime monitor killed. pid=" + str(pid))
    return 0


def restart() -> int:
    stop()
    return start()


def main(argv: List[str] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    command = args[0] if args else "status"
    if command == "start":
        return start()
    if command == "stop":
        return stop()
    if command == "restart":
        return restart()
    if command == "status":
        return status()
    print("Usage: python3 python_service/monitor_service.py start|stop|restart|status")
    return 1
