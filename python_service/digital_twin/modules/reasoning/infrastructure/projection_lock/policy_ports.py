"""Explicit capabilities for projection_lock/policy; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class ProjectionLockPolicyStore(Protocol):
    pass


@dataclass(frozen=True)
class ProjectionLockPolicyRuntime:
    runtime_settings: Callable[..., Any]
