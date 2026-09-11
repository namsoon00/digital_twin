"""Capabilities for detail outbox; no runtime construction."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class DetailOutboxPort(Protocol):
    inference_detail_outbox: Any

    def inference_detail_outbox_enabled(self) -> bool: ...

    def inference_snapshot_limit(self) -> int: ...

    settings: Any
