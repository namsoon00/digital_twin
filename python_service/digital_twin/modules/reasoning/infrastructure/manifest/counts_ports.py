"""Explicit capabilities for manifest/counts; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol




class ManifestCountsStore(Protocol):
    pass
