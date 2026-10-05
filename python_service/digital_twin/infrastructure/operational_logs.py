"""Bound manager-owned diagnostic logs; never touch DB/audit/observer records.

copytruncate preserves the append descriptors held by running workers. As with
standard copytruncate, diagnostic bytes racing the final truncate can be lost;
durable domain events and delivery receipts must never use this mechanism.
"""
import fcntl
import gzip
import os
from pathlib import Path
import stat
import time
import uuid


def maintain_operational_logs(root, paths, budget_bytes, *, now=None):
    root = Path(root).resolve()
    now = time.time() if now is None else now
    budget = max(1024 * 1024, int(budget_bytes))
    archive = root / "operational-log-archives"
    if archive.is_symlink():
        raise ValueError("Log archive must not be a symlink")
    archive.mkdir(mode=0o700, exist_ok=True)
    lock_fd = os.open(str(archive / ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"busy": True, "rotated": 0, "reclaimedBytes": 0}
        selected = sorted({Path(p) for p in paths if Path(p).parent.resolve() == root
                           and Path(p).suffix == ".log" and not Path(p).is_symlink()})
        threshold = max(1024 * 1024, min(32 * 1024 * 1024, budget // (2 * max(1, len(selected)))))
        rotated = reclaimed = 0
        failures = []
        deadline = time.monotonic() + 5
        # Bound compression work per supervisor pass; largest files first.
        candidates = sorted((p for p in selected if p.is_file()), key=lambda p: p.stat().st_size, reverse=True)
        for path in candidates[:4]:
            if time.monotonic() >= deadline:
                break
            target = archive / (path.name + "." + str(int(now)) + "." + uuid.uuid4().hex + ".gz")
            pending = target.with_suffix(".pending")
            try:
                fd = os.open(str(path), os.O_RDWR | os.O_NOFOLLOW)
                with os.fdopen(fd, "r+b", buffering=0) as source:
                    before = os.fstat(source.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size < threshold:
                        continue
                    out_fd = os.open(str(pending), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with os.fdopen(out_fd, "wb") as output:
                        with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=1, mtime=0) as compressed:
                            remaining = before.st_size
                            while remaining:
                                if time.monotonic() >= deadline:
                                    raise TimeoutError("Diagnostic archive time budget exhausted")
                                block = source.read(min(1024 * 1024, remaining))
                                if not block:
                                    raise OSError("Log changed during archival")
                                compressed.write(block)
                                remaining -= len(block)
                        output.flush()
                        os.fsync(output.fileno())
                    # Preserve concurrent growth; retry a busy file next pass.
                    after = os.fstat(source.fileno())
                    if after.st_size != before.st_size or path.stat().st_ino != before.st_ino:
                        pending.unlink()
                        continue
                    os.replace(pending, target)
                    source.truncate(0)
                    os.fsync(source.fileno())
                    rotated += 1
                    reclaimed += max(0, before.st_size - target.stat().st_size)
            except OSError as error:
                pending.unlink(missing_ok=True)
                failures.append({"file": path.name, "errno": error.errno})
        archives = sorted((p for p in archive.glob("*.log.*.gz") if not p.is_symlink() and p.is_file()),
                          key=lambda p: p.stat().st_mtime)
        retained = sum(p.stat().st_size for p in archives)
        for path in archives:
            info = path.stat()
            if retained <= budget // 4 and now - info.st_mtime <= 7 * 86400:
                continue
            path.unlink()
            retained -= info.st_size
            reclaimed += info.st_size
        return {"rotated": rotated, "reclaimedBytes": reclaimed, "archiveBytes": retained,
                "perFileLimitBytes": threshold, "failures": failures}
