"""Bounded query diagnostics with per-repository lock ownership."""

from dataclasses import dataclass, field
import threading
from typing import Any, Dict, List


@dataclass
class QueryMetricState:
    rows: List[Dict[str, object]] = field(default_factory=list)
    lock: Any = field(default_factory=threading.Lock)
