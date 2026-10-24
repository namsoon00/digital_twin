"""Best-effort local wake hint; the committed MySQL outbox stays authoritative."""

from hashlib import sha256
import os
from pathlib import Path
import tempfile
import time


class NotificationWakeSignal:
    def __init__(self, settings=None, path=None):
        settings = settings or {}
        namespace = "|".join(str(value) for value in (
            Path(__file__).resolve().parents[6], os.getuid(),
            settings.get("mysqlHost"), settings.get("mysqlPort"),
            settings.get("mysqlDatabase"), settings.get("mysqlUnixSocket"),
        ))
        self.path = Path(path) if path else Path(tempfile.gettempdir()) / (
            "orbit-notify-wake-" + sha256(namespace.encode()).hexdigest()[:24]
        )

    def version(self):
        try:
            value = self.path.stat()
            return value.st_ino, value.st_mtime_ns, value.st_ctime_ns
        except OSError:
            return None

    def notify(self):
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                os.write(fd, b"1")
            finally:
                os.close(fd)
        except OSError:
            pass  # Polling recovers missed hints; never fail a committed enqueue.

    def wait(self, previous, running, end_at, *, clock=time.monotonic, sleep=time.sleep):
        while running():
            remaining = end_at - clock()
            if remaining <= 0 or self.version() != previous:
                return
            sleep(min(1.0, remaining))
