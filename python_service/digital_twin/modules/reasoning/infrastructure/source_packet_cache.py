"""Bounded, repository-local cache of immutable serialized source packets.

The repository must check the durable row's existence and fingerprint before
using an entry. No TTL can establish source authority. Strings let each reader
decode its own object without copying an already decoded provider archive.
"""

from collections import OrderedDict
from sys import getsizeof
from threading import Lock


class SourcePacketCache:
    def __init__(self, max_entries=4, max_bytes=64 * 1024 * 1024):
        self.max_entries = max(0, int(max_entries))
        self.max_bytes = max(0, int(max_bytes))
        self._entries = OrderedDict()
        self._bytes = 0
        self._lock = Lock()

    def get(self, snapshot_id):
        with self._lock:
            entry = self._entries.get(snapshot_id)
            if entry is None:
                return None
            self._entries.move_to_end(snapshot_id)
            return entry[0], entry[1]

    def discard(self, snapshot_id):
        with self._lock:
            entry = self._entries.pop(snapshot_id, None)
            if entry is not None:
                self._bytes -= entry[2]

    def put(self, snapshot_id, fingerprint, payload_json):
        if not snapshot_id or not fingerprint or not isinstance(payload_json, str):
            return
        size = sum(getsizeof(value) for value in (snapshot_id, fingerprint, payload_json))
        with self._lock:
            previous = self._entries.pop(snapshot_id, None)
            if previous is not None:
                self._bytes -= previous[2]
            if self.max_entries == 0 or size > self.max_bytes:
                return
            self._entries[snapshot_id] = (fingerprint, payload_json, size)
            self._bytes += size
            while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                _, entry = self._entries.popitem(last=False)
                self._bytes -= entry[2]
