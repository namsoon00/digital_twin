"""Capabilities for inference; no runtime construction."""

from __future__ import annotations
from digital_twin.domain.portfolio import AccountSnapshot
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class InferencePort(Protocol):
    def acquire_inference_write_lease(
        self, result: Dict[str, object], world_id: str = ""
    ) -> Dict[str, object]: ...

    def active_graph_store_key(self, result: Dict[str, object] = None) -> str: ...

    def adaptive_native_rule_target_sharding_profile(
        self,
        snapshot: AccountSnapshot,
        world_id: str = "",
        rulebox_rules_hash: str = "",
    ) -> Dict[str, object]: ...

    def audited_prior_rule_selection_context(
        self,
        snapshot: AccountSnapshot,
        inference_symbols: List[str],
        candidate_scope_plan: List[Dict[str, object]] = None,
        rulebox_rules_hash: str = "",
        tbox_fingerprint: str = "",
        world_id: str = "",
        requested_fact_families: List[str] = None,
        requested_fact_families_by_symbol: Dict[str, List[str]] = None,
    ) -> Dict[str, object]: ...

    def enqueue_inference_detail_readback(
        self,
        result: Dict[str, object],
        snapshot: AccountSnapshot,
        inference_symbols: List[str],
        world_id: str = "",
    ) -> Dict[str, object]: ...

    def impact_plan_with_audited_candidates(
        self, impact_plan: Dict[str, object], selection_context: Dict[str, object]
    ) -> Dict[str, object]: ...

    def incremental_equivalence_audit_sample_pct(self) -> int: ...

    def incremental_equivalence_audit_selected(
        self,
        snapshot: AccountSnapshot,
        symbols: List[str],
        impact_plan: Dict[str, object],
        selection_context: Dict[str, object],
    ) -> bool: ...

    def inference_detail_outbox_enabled(self) -> bool: ...

    def inference_detail_outbox_summary(
        self, payload: Dict[str, object]
    ) -> Dict[str, object]: ...

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

    def reconcile_abox_activation_after_inference(
        self,
        result: Dict[str, object],
        inference_symbols: List[str],
        world_id: str = "",
    ) -> None: ...

    def release_inference_write_lease(
        self, lease: Dict[str, object]
    ) -> Dict[str, object]: ...

    repository: Any

    def repository_world_call(
        self, method_name: str, *args, world_id: str = "", **kwargs
    ): ...

    def rulebox_rules_for_impact(self) -> List[Dict[str, object]]: ...

    settings: Any

    def shared_inference_selection_context(
        self,
        impact_plan: Dict[str, object],
        reasoning_context: Dict[str, object],
        inference_symbols: List[str],
        account_selection_context: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def world_partitioned_reasoning_enabled(self) -> bool: ...

    def world_rule_partition(
        self, rule_catalog: Dict[str, object]
    ) -> Dict[str, object]: ...
