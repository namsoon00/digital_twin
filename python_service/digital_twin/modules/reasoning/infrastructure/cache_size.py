"""Bound memory retained by graph caches, without serializing large graphs."""
from dataclasses import fields, is_dataclass
from sys import getsizeof


def retained_size(value, limit):
    seen, pending, total = set(), [value], 0
    while pending:
        item = pending.pop()
        identity = id(item)
        if identity in seen:
            continue
        seen.add(identity)
        total += getsizeof(item)
        if total > limit:
            return total
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)):
            pending.extend(item)
        elif is_dataclass(item) and not isinstance(item, type):
            total += getsizeof(getattr(item, "__dict__", {}))
            pending.extend(getattr(item, field.name) for field in fields(item))
    return total


def trim_entries(entries, max_entries, max_bytes):
    total = sum(entry["retainedBytes"] for entry in entries.values())
    while entries and (len(entries) > max_entries or total > max_bytes):
        _, entry = entries.popitem(last=False)
        total -= entry["retainedBytes"]
