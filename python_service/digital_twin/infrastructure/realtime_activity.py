"""Cheap host-local admission signal, independent of an overloaded database."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import time


class RealtimeActivity:
    def __init__(self, path, quiet_seconds=20, clock=time.time):
        self.path = Path(path)
        self.quiet_seconds = max(0, float(quiet_seconds))
        self.clock = clock

    @contextmanager
    def turn(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            # Realtime workers may overlap each other. Only background probes
            # use an exclusive lock, and release it before doing any work.
            fcntl.flock(fd, fcntl.LOCK_SH)
            os.utime(fd, (self.clock(), self.clock()))
            try:
                yield
            finally:
                os.utime(fd, (self.clock(), self.clock()))
        finally:
            os.close(fd)

    def active(self):
        try:
            fd = os.open(str(self.path), os.O_RDWR)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return self.clock() - os.fstat(fd).st_mtime < self.quiet_seconds
        finally:
            os.close(fd)
