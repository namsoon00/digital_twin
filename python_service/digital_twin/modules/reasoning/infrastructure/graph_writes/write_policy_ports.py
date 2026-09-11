"""Capabilities for write policy; no runtime construction."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class WritePolicyPort(Protocol):
    def abox_incremental_cleanup_batch_size(
        self, settings: Dict[str, object] = None
    ) -> int: ...


@dataclass(frozen=True)
class AboxDeleteBatchSizeBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class AboxIncrementalCleanupBatchSizeBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class AboxIncrementalCleanupMaxBatchesPerSaveBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class AboxInactiveGenerationKeepCountBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class AboxInactiveGenerationMaxPrunePerSaveBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class DeferredMaintenanceAboxMaxManifestsBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class DeferredMaintenanceAboxMaxDeleteBatchesBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class DeferredMaintenanceAboxDeleteBatchSizeBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class AboxWriteTransactionQueryCountBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class AboxNodeBatchSizeBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class AboxRelationBatchSizeBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class GraphWriteTransactionQueryCountBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class StaticNodeInsertBatchSizeBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class StaticWriteTransactionQueryCountBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class InferenceboxWriteTransactionQueryCountBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class InferenceboxRelationBatchSizeBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class InferenceboxGivenRelationWritesEnabledBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class InferenceboxGivenRelationBatchSizeBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class WriteQueryMaxBytesBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class GivenRelationWritesEnabledBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class GivenRelationBatchSizeBindings:
    runtime_settings: Callable[..., Any]
