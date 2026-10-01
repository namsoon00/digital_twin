"""Filesystem inventory and bounded-scope removal of retired TypeDB stores."""

import shutil
from pathlib import Path

from .operational_storage_guard import storage_directory_physical_size_bytes


def retired_stores(active_path, retention_minutes, now):
    active = Path(active_path)
    rows = []
    for path in sorted(active.parent.glob(active.name + "-retired-*")):
        # Never follow aliases or treat an unrecognised sibling as disposable.
        if path.is_symlink() or not path.is_dir():
            continue
        suffix = path.name.rsplit("-retired-", 1)[-1]
        if not suffix.isdigit():
            continue
        boundary = max(path.stat().st_mtime, float(int(suffix)))
        rows.append({"path": str(path), "eligible": boundary + retention_minutes * 60 <= now})
    return rows


def storage_inventory(active_path):
    active = Path(active_path)
    groups = {"active": [active]}
    for kind in ("retired", "failed", "candidate"):
        groups[kind] = list(active.parent.glob(active.name + "-" + kind + "*"))
    return {
        kind: sum(storage_directory_physical_size_bytes(p) for p in paths
                  if p.is_dir() and not p.is_symlink())
        for kind, paths in groups.items()
    }


def prune_retired_stores(active_path, retention_minutes, now):
    """Preserve active/staged/failed stores and report errors without hiding them."""
    active = Path(active_path)
    rows = retired_stores(active, retention_minutes, now)
    eligible = [r for r in rows if r["eligible"]]
    if not eligible or not active.is_dir() or active.is_symlink():
        return {"removedPaths": [], "errors": [], "retiredStoreCount": len(rows)}
    before = storage_inventory(active)
    free_before = shutil.disk_usage(active).free
    removed, errors = [], []
    for row in eligible:
        try:
            shutil.rmtree(row["path"])
            removed.append(row["path"])
        except OSError as error:
            errors.append({"path": row["path"], "errorType": type(error).__name__})
    after = storage_inventory(active)
    free_after = shutil.disk_usage(active).free
    return {
        "removedPaths": removed, "errors": errors,
        "retiredStoreCount": len(rows) - len(removed),
        "storageBytesBefore": before, "storageBytesAfter": after,
        "totalStorageBytesBefore": sum(before.values()),
        "totalStorageBytesAfter": sum(after.values()),
        "removedAllocatedBytes": max(0, before["retired"] - after["retired"]),
        # Other processes can write/free blocks concurrently. Keep these two
        # observations separate from the bytes removed in the retired tree.
        "diskFreeBytesBefore": free_before, "diskFreeBytesAfter": free_after,
        "diskFreeDeltaBytes": free_after - free_before,
        "spaceReclaimed": bool(removed and after["retired"] < before["retired"]),
    }
