"""Bounded, process-local projections of immutable external fact revisions."""

from collections import OrderedDict
import json
import os
from threading import Lock


class RevisionProjectionCache:
    """Cache compact projections, never full provider documents or membership.

    Callers must re-read the retained revision IDs on every request. Serialized
    values prevent a consumer from mutating a later request's evidence and give
    this cache an exact byte budget independent of the number of revisions.
    """

    def __init__(self, max_bytes=4 * 1024 * 1024, max_entries=10000):
        self.max_bytes = max(0, int(max_bytes))
        self.max_entries = max(0, int(max_entries))
        self.byte_count = 0
        self.entries = OrderedDict()
        self.lock = Lock()

    def get(self, key):
        with self.lock:
            encoded = self.entries.get(key)
            if encoded is None:
                return None
            self.entries.move_to_end(key)
        return json.loads(encoded)

    def put(self, key, value):
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with self.lock:
            previous = self.entries.pop(key, None)
            if previous is not None:
                self.byte_count -= len(previous)
            # Oversized projections remain valid results; only caching is skipped.
            if len(encoded) > self.max_bytes or not self.max_entries:
                return
            self.entries[key] = encoded
            self.byte_count += len(encoded)
            while self.byte_count > self.max_bytes or len(self.entries) > self.max_entries:
                _, removed = self.entries.popitem(last=False)
                self.byte_count -= len(removed)


_process_cache = None
_process_cache_pid = None


def process_revision_projection_cache():
    """Survive per-snapshot adapter construction without sharing across forks."""
    global _process_cache, _process_cache_pid
    pid = os.getpid()
    if _process_cache is None or _process_cache_pid != pid:
        _process_cache = RevisionProjectionCache()
        _process_cache_pid = pid
    return _process_cache
