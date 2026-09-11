"""Per-repository lease state, shared only by nested calls in that repository."""

from dataclasses import dataclass, field
import threading
from typing import Any, Set


@dataclass
class ProjectionLeaseState:
    local: Any = field(default_factory=threading.local)
    registry_lock: Any = field(default_factory=threading.RLock)
    active_tokens: Set[str] = field(default_factory=set)
