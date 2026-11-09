#!/usr/bin/env python3
"""Independent read-only observer. Writes only its specified observation folder."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import argparse
import fcntl
import os
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python_service"))
from digital_twin.infrastructure.settings import load_env_file
from digital_twin.infrastructure.storage_observation import (
    collect_sample, disk_inventory, parse_time, read_json, utc_now, write_json, summarize_observations,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--disk-scan", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    directory = args.directory.resolve()
    if directory == args.root.resolve() or args.root.resolve() in directory.parents:
        parser.error("Observation records must be outside the application repository")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (directory / ("disk-scan.lock" if args.disk_scan else "storage-watch.lock")).open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('{"collectorStatus":"busy"}')
        return 0
    if args.disk_scan:
        os.nice(10)
        source = read_json(directory / "disk-paths.json")
        directory_age = (datetime.now(timezone.utc)-parse_time(source["observedAt"])).total_seconds() if source.get("observedAt") else 1e9
        metrics = disk_inventory(args.root, read_json(directory / "component-metrics.json"),
            mysql_directory=source.get("mysqlDirectory") if 0 <= directory_age <= 1800 else None)
        write_json(directory / "component-metrics.json", metrics)
        return 0
    load_env_file(args.root / ".env.local")
    window_path = directory / "observation-window.json"
    window = read_json(window_path)
    if not window:
        window = {"startedAt": utc_now(), "minimumHours": 72,
                  "maximumDays": 10, "intervalSeconds": 300, "continuousUptimeClaim": False}
        write_json(window_path, window)
    end = parse_time(window["startedAt"]) + timedelta(days=float(window.get("maximumDays", 10)))
    stopped = False

    def request_stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    disk_process = None
    last_disk_attempt = 0.0
    if not args.sample:
        (directory / "storage-watch.pid").write_text(str(os.getpid()))
    try:
        while not stopped and not (directory / "STOP").exists() and datetime.now(timezone.utc) < end:
            import json
            with (directory / "observer-cycle-lifecycle.jsonl").open("a") as stream:
                stream.write(json.dumps({"at": utc_now(), "action": "started"}) + "\n")
            sample = collect_sample(args.root, directory)
            previous = read_json(directory / "storage-latest.json")
            if previous.get("observedAt"):
                sample["previousSampleAt"] = previous["observedAt"]
                sample["gapSeconds"] = (parse_time(sample["observedAt"]) - parse_time(previous["observedAt"])).total_seconds()
            write_json(directory / "storage-latest.json", sample)
            with (directory / "storage-samples.jsonl").open("a") as stream:
                stream.write(json.dumps(sample, ensure_ascii=False, default=str) + "\n")
            with (directory / "observer-cycle-lifecycle.jsonl").open("a") as stream:
                stream.write(json.dumps({"at": utc_now(), "action": "collected",
                    "observedAt": sample["observedAt"], "collectorStatus": sample["collectorStatus"],
                    "coreCollectorStatus": sample["coreCollectorStatus"]}) + "\n")
            write_json(directory / "storage-summary.json", summarize_observations(directory))
            # Inventory processes cannot delay the core sample or overlap one
            # another, and old observations keep their original expiration.
            if (disk_process is None or disk_process.poll() is not None) and time.monotonic() - last_disk_attempt > 1800:
                disk_process = subprocess.Popen([
                    sys.executable, str(Path(__file__).resolve()), "--root", str(args.root),
                    "--directory", str(directory), "--disk-scan"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                last_disk_attempt = time.monotonic()
            if args.sample:
                print(json.dumps({key: sample.get(key) for key in
                    ("observedAt", "collectorStatus", "coreCollectorStatus", "queryCount", "durationSeconds")}))
                break
            target = time.monotonic() + 300
            while not stopped and not (directory / "STOP").exists() and time.monotonic() < target:
                time.sleep(1)
    finally:
        if not args.sample:
            write_json(directory / "storage-watch-stopped.json", {
                "stoppedAt": utc_now(), "stopFile": (directory / "STOP").exists(),
                "deadlineReached": datetime.now(timezone.utc) >= end, "signalReceived": stopped})
            (directory / "storage-watch.pid").unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
