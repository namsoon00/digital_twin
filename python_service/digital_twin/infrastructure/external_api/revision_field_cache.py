"""Disposable local field cache; MySQL revision membership remains authoritative.

Only bounded selected fields are cached, never provider archives. Atomic replace
avoids partially published files. Losing this cache only requires another read.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import fcntl


class RevisionFieldCache:
    MAX_BYTES = 4 * 1024 * 1024
    MAX_FILES = 5

    def __init__(self, root, namespace, paths):
        self.root = Path(root)
        identity = json.dumps(["immutable-symbol-fields-v1", namespace, paths], sort_keys=True)
        self.path = self.root / (hashlib.sha256(identity.encode()).hexdigest() + ".json")

    @staticmethod
    def key(identity):
        return hashlib.sha256(json.dumps([identity["revision_id"], identity["fetched_at"],
                                         identity["payload_hash"]]).encode()).hexdigest()

    def load(self):
        try:
            if self.path.stat().st_size > self.MAX_BYTES:
                return {}
            envelope = json.loads(self.path.read_text())
            raw = envelope["rows"]
            if (not isinstance(raw, str) or hashlib.sha256(raw.encode()).hexdigest() != envelope["sha256"]):
                return {}
            rows = json.loads(raw)
            return rows if isinstance(rows, dict) else {}
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def save(self, additions):
        if not additions:
            return
        temporary = None
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            # One short write lock bounds the entire cache directory, even
            # when multiple accounts/processes publish different field sets.
            fd = os.open(str(self.root / "write.lock"), os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(fd, "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                for abandoned in self.root.glob(".pending-*"):
                    abandoned.unlink()
                rows = self.load()
                for key, value in additions.items():
                    rows.pop(key, None)
                    rows[key] = value
                # Account for envelope escaping as well as the source bytes.
                while rows:
                    raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
                    encoded = json.dumps({"rows": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()},
                                         ensure_ascii=False).encode()
                    if len(encoded) <= self.MAX_BYTES:
                        break
                    for key in list(rows)[:max(1, len(rows) // 4)]:
                        rows.pop(key)
                if not rows:
                    return
                fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.root)
                with os.fdopen(fd, "wb") as output:
                    output.write(encoded)
                os.replace(temporary, self.path)
                temporary = None
                files = sorted(self.root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
                for path in files[self.MAX_FILES:]:
                    path.unlink()
        except (OSError, ValueError, TypeError):
            # Optional acceleration cannot invalidate a successfully read fact.
            pass
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
