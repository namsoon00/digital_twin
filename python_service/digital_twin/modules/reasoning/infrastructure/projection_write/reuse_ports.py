"""Capabilities for reuse; no runtime construction."""

from __future__ import annotations
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class ReusePort(Protocol):
    _frozen_tbox_metadata: Any

    def inference_result_is_reusable(
        self,
        inferencebox: Dict[str, object],
        active_abox: Dict[str, object],
        required_symbols: List[str] = None,
    ) -> bool: ...

    def inference_snapshot_limit(self) -> int: ...

    def inference_symbols(
        self, snapshot: AccountSnapshot, target_symbols: List[str] = None
    ) -> List[str]: ...

    def matched_rule_ids_from_inference_payload(
        self, payload: Dict[str, object]
    ) -> List[str]: ...

    repository: Any

    def repository_world_call(
        self, method_name: str, *args, world_id: str = "", **kwargs
    ): ...

    settings: Any


@dataclass(frozen=True)
class ExecutionNamespaceBindings:
    RULE_EVALUATION_NAMESPACE_VERSION: Any


@dataclass(frozen=True)
class CompactSharedInferenceReuseBindings:
    shared_inference_from_result_slot_proof: Callable[..., Any]
