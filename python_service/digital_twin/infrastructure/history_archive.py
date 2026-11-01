"""Local, lossless content-addressed history. No network and no automatic GC.

Chunks and manifests are fsynced before a MySQL archive receipt is written.
An interrupted transaction can leave unreferenced files, never a dangling
committed receipt. Identical chunks are shared without changing source IDs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import zlib

from digital_twin.platform.domain.storage_preservation import (
    ARCHIVE_FORMAT, ARCHIVE_MAX_CHUNK_BYTES, ARCHIVE_MAX_RECORD_BYTES,
    ARCHIVE_MIN_FREE_BYTES,
)


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def content_chunks(raw):
    """Bounded blocks avoid Python byte-at-a-time work on large source JSON.

    Identical aligned blocks share content. An insertion may reduce reuse,
    but restoration never depends on an older snapshot or a mutable base.
    """
    for start in range(0, len(raw), ARCHIVE_MAX_CHUNK_BYTES):
        yield raw[start:start + ARCHIVE_MAX_CHUNK_BYTES]


def _ensure_directory(path):
    """Persist each new directory entry before its children can be committed."""
    if path.is_symlink():
        raise ValueError("Archive directories must not be symlinks")
    if path.exists():
        if not path.is_dir():
            raise ValueError("Archive directory is not a directory")
        return
    _ensure_directory(path.parent)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Archive directory changed during creation")
    parent_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


class LocalHistoryArchive:
    def __init__(self, root, *, minimum_free_bytes=ARCHIVE_MIN_FREE_BYTES):
        self.root = Path(root)
        self.minimum_free_bytes = max(ARCHIVE_MIN_FREE_BYTES, int(minimum_free_bytes))

    def can_stage(self):
        path = self.root
        while not path.exists():
            path = path.parent
        # Admit the worst allowed row, not its optimistic compression ratio.
        return shutil.disk_usage(path).free >= self.minimum_free_bytes + ARCHIVE_MAX_RECORD_BYTES

    def _path(self, digest):
        if not re.fullmatch(r"[a-f0-9]{64}", str(digest)):
            raise ValueError("Invalid archive content hash")
        return self.root / digest[:2] / (digest + ".z")

    def _read(self, digest, limit):
        path = self._path(digest)
        if self.root.is_symlink() or path.parent.is_symlink() or path.is_symlink():
            raise ValueError("Archive symlinks are not allowed")
        if path.stat().st_size > limit + 65536:
            raise ValueError("Archive object exceeds size limit")
        decoder = zlib.decompressobj()
        raw = decoder.decompress(path.read_bytes(), limit + 1)
        if len(raw) > limit or not decoder.eof or decoder.unused_data or _hash(raw) != digest:
            raise ValueError("Archive object checksum or length mismatch")
        return raw

    def _put(self, raw):
        digest = _hash(raw)
        path = self._path(digest)
        if self.root.is_symlink():
            raise ValueError("Archive root must not be a symlink")
        _ensure_directory(self.root)
        _ensure_directory(path.parent)
        if path.exists():
            if self._read(digest, len(raw)) != raw:
                raise ValueError("Archive content collision")
            return digest, 0
        compressed = zlib.compress(raw, 6)
        if shutil.disk_usage(self.root).free < self.minimum_free_bytes + len(compressed):
            raise OSError("Insufficient archive staging space; keep MySQL row")
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        created = False
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(compressed)
                stream.flush()
                os.fsync(stream.fileno())
            # link is atomic and refuses to replace a concurrent writer's file.
            try:
                os.link(temporary, path)
                created = True
            except FileExistsError:
                self._read(digest, len(raw))
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.unlink(temporary)
        return digest, (path.stat().st_blocks * 512 if created else 0)

    def put(self, row):
        raw = canonical_bytes(row)
        if len(raw) > ARCHIVE_MAX_RECORD_BYTES:
            raise ValueError("Archive row exceeds bounded record size; keep MySQL row")
        chunks, written = [], 0
        for chunk in content_chunks(raw):
            digest, size = self._put(chunk)
            chunks.append({"sha256": digest, "bytes": len(chunk)})
            written += size
        manifest = {"format": ARCHIVE_FORMAT, "sha256": _hash(raw),
                    "bytes": len(raw), "chunks": chunks}
        digest, size = self._put(canonical_bytes(manifest))
        # Read back the complete original before a caller may delete anything.
        if self.get(digest) != row:
            raise ValueError("Archive restore verification failed")
        return {"manifestSha256": digest, "rowSha256": _hash(raw),
                "originalBytes": len(raw), "writtenBytes": written + size,
                "chunks": len(chunks), "restoreVerified": True}

    def get(self, manifest_sha256):
        manifest = json.loads(self._read(manifest_sha256, 2 * 1024 * 1024))
        size = int(manifest.get("bytes", -1))
        chunks = manifest.get("chunks")
        if (manifest.get("format") != ARCHIVE_FORMAT or not 0 <= size <= ARCHIVE_MAX_RECORD_BYTES
                or not isinstance(chunks, list) or len(chunks) > ARCHIVE_MAX_RECORD_BYTES // 4096 + 1):
            raise ValueError("Invalid archive manifest")
        parts, total = [], 0
        for chunk in chunks:
            length = int(chunk["bytes"])
            if not 0 < length <= ARCHIVE_MAX_CHUNK_BYTES:
                raise ValueError("Invalid archive chunk size")
            part = self._read(chunk["sha256"], length)
            if len(part) != length:
                raise ValueError("Archive chunk length mismatch")
            total += length
            if total > size:
                raise ValueError("Archive reconstructed size exceeded")
            parts.append(part)
        raw = b"".join(parts)
        if len(raw) != size or _hash(raw) != manifest["sha256"]:
            raise ValueError("Archive row checksum mismatch")
        return json.loads(raw)
