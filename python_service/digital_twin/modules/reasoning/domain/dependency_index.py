"""Exact colon-delimited dependency lookup; never evaluates market conditions."""

from bisect import bisect_left
from typing import Iterable, Set


class StructuralDependencyIndex:
    """Compile one immutable dependency boundary for an execution plan.

    A key covers itself, its ancestors and its descendants, but not another
    key that merely shares a text prefix. Retain the original string grammar:
    empty colon segments and punctuation are significant. The index belongs
    to one plan; it never caches observed values or survives a rule change.
    """

    def __init__(self, keys: Iterable[object]):
        self.keys = frozenset(str(key or "").strip().lower() for key in keys or []
                              if str(key or "").strip())
        self._ordered = tuple(sorted(self.keys))

    def covers(self, key: object) -> bool:
        value = str(key or "").strip().lower()
        if not value or not self.keys:
            return False
        if value in self.keys:
            return True
        # Exact ancestor lookup costs only the depth of this key, rather
        # than the size of the complete event's dependency catalogue.
        offset = value.find(":")
        while offset >= 0:
            if value[:offset] in self.keys:
                return True
            offset = value.find(":", offset + 1)
        prefix = value + ":"
        position = bisect_left(self._ordered, prefix)
        return position < len(self._ordered) and self._ordered[position].startswith(prefix)

    def matching(self, keys: Iterable[object]) -> Set[str]:
        return {str(key or "").strip().lower() for key in keys or [] if self.covers(key)}

    def intersects(self, keys: Iterable[object]) -> bool:
        return any(self.covers(key) for key in keys or [])

    def related_keys(self, key: object) -> Set[str]:
        """Find indexed ancestors/descendants for an inverse dependency map."""
        value = str(key or "").strip().lower()
        if not value:
            return set()
        found = {value} if value in self.keys else set()
        offset = value.find(":")
        while offset >= 0:
            if value[:offset] in self.keys:
                found.add(value[:offset])
            offset = value.find(":", offset + 1)
        prefix = value + ":"
        position = bisect_left(self._ordered, prefix)
        while position < len(self._ordered) and self._ordered[position].startswith(prefix):
            found.add(self._ordered[position])
            position += 1
        return found
