"""Input-stage capabilities, without graph-write or notification authority.

Optional legacy query methods are checked at their call sites. Outcome
observation is intentionally a separate effectful port, not a read method.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Protocol

from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.portfolio.contracts import AccountSnapshot


class DecisionMemoryReader(Protocol):
    def list(self, account_id: str, symbol: str = "", limit: int = 40) -> list: ...
    def list_for_symbols(
        self,
        symbols: Iterable[str],
        *,
        account_id: str,
        limit_per_symbol: int,
        as_of: str,
    ) -> list: ...
    def outcome_history_for_symbols(
        self,
        symbols: Iterable[str],
        *,
        account_id: str,
        as_of: str,
        limit_per_symbol: int,
        maximum_episode_count: int,
    ) -> list: ...
    def performance_episodes(
        self, *, account_id: str, symbol: str, limit: int, as_of: str
    ) -> list: ...
    def performance(self, *, account_id: str, limit: int, as_of: str = "") -> dict: ...


class OutcomeObserver(Protocol):
    def observe_snapshot(self, snapshot: AccountSnapshot) -> dict: ...


class PortfolioMemoryReader(Protocol):
    def lifecycle_feedback_for_decisions(self, episode_ids: Iterable[str]) -> dict: ...
    def ontology_portfolio_lifecycle_context(self, portfolio_id: str) -> dict: ...


class HypothesisProposalReader(Protocol):
    def list_hypothesis_proposals(
        self, status: str, symbol: str, limit: int
    ) -> list: ...


class HypothesisLifecycleReader(Protocol):
    def current_summary_for_subjects(
        self, account_id: str, symbols: Iterable[str], lifecycle_key_prefix: str = ""
    ) -> dict: ...
    def current_for_subjects(
        self, account_id: str, symbols: Iterable[str], lifecycle_key_prefix: str = ""
    ) -> dict: ...


class TemporalWindowReader(Protocol):
    def load_temporal_windows(
        self, account_id: str, symbols: Iterable[str], definitions, *, as_of: str
    ) -> dict: ...


class StatisticalSignalRunner(Protocol):
    def run(
        self,
        *,
        account_id: str,
        backend_id: str,
        windows: dict,
        as_of: str,
        source_event_id: str,
        graph: PortfolioOntology,
        rules,
    ) -> dict: ...


class RuntimeContextCache(Protocol):
    def get(self, key: str, ttl_seconds: float) -> dict: ...
    def put(self, key: str, context: dict, max_entries: int) -> None: ...


class GraphAssemblyCache(Protocol):
    def get(self, key: str, ttl_seconds: float) -> dict: ...
    def put(
        self,
        key: str,
        graph: PortfolioOntology,
        persistence_graph: PortfolioOntology,
        max_entries: int,
        runtime_context_packet: dict,
    ) -> None: ...


class PersistentAssemblyCache(Protocol):
    def get(self, key: str, ttl_seconds: float) -> dict: ...
    def put(
        self,
        key: str,
        graph: PortfolioOntology,
        persistence_graph: PortfolioOntology,
        ttl_seconds: float,
        max_entries: int,
        max_payload_bytes: int,
        runtime_context_packet: dict,
    ) -> dict: ...


@dataclass(frozen=True)
class DecisionMemoryInputs:
    decision_episode_context_hypothesis_limit: Callable[[], int]
    decision_episode_context_maximum_episodes: Callable[[], int]
    decision_episode_context_outcome_limit: Callable[[], int]
    decision_episode_context_per_symbol_limit: Callable[[], int]
    decision_episode_store: Optional[DecisionMemoryReader]
    decision_outcome_history_maximum_episodes: Callable[[], int]
    decision_outcome_history_per_symbol_limit: Callable[[], int]
    investment_domain_store: Optional[PortfolioMemoryReader]
    outcome_observation_service: OutcomeObserver


@dataclass(frozen=True)
class HypothesisInputs:
    hypothesis_lifecycle_store: Optional[HypothesisLifecycleReader]
    hypothesis_proposal_store: Optional[HypothesisProposalReader]


@dataclass(frozen=True)
class TemporalInputs:
    market_time_series_store: Optional[TemporalWindowReader]
    settings: Dict[str, object]


@dataclass(frozen=True)
class RuntimeContextInputs:
    active_tbox_context: Callable[[], dict]
    data_pipeline_health_context: Callable[[AccountSnapshot], dict]
    decision_episode_projection_context: Callable[..., dict]
    decision_episode_store: Optional[DecisionMemoryReader]
    factual_runtime_metadata: Callable[..., dict]
    hypothesis_lifecycle_abox_projection_enabled: Callable[[], bool]
    hypothesis_lifecycle_context: Callable[..., list]
    hypothesis_proposal_context: Callable[..., list]
    investment_domain_store: Optional[PortfolioMemoryReader]
    last_runtime_context_cache_status: Dict[str, Dict[str, object]]
    last_runtime_contexts: Dict[str, Dict[str, object]]
    performance_setting: Callable[[str, float], float]
    runtime_cache: RuntimeContextCache
    runtime_context_cache_enabled: Callable[[], bool]
    runtime_context_cache_key: Callable[..., str]
    runtime_context_cache_max_entries: Callable[[], int]
    runtime_context_cache_ttl_seconds: Callable[[], float]
    runtime_context_overrides: Dict[str, Dict[str, object]]
    settings: Dict[str, object]
    statistical_signal_service: Optional[StatisticalSignalRunner]
    temporal_observation_windows: Callable[..., dict]


@dataclass(frozen=True)
class ProjectionIdentityInputs:
    build_graph_assembly: Callable[..., tuple]
    catalog_for_rules: Callable[..., dict]
    incremental_current_state_reasoning_enabled: Callable[[], bool]
    settings: Dict[str, object]
    world_partitioned_reasoning_enabled: Callable[[], bool]
    world_rule_partition: Callable[[dict], dict]


@dataclass(frozen=True)
class PersistentCacheInputs:
    graph_assembly_cache_store: Optional[PersistentAssemblyCache]
    graph_assembly_persistent_cache_enabled: Callable[[], bool]
    graph_assembly_persistent_cache_max_entries: Callable[[], int]
    graph_assembly_persistent_cache_max_payload_bytes: Callable[[], int]
    graph_assembly_persistent_cache_ttl_seconds: Callable[[], float]


@dataclass(frozen=True)
class CaptureInputs:
    active_tbox_context: Callable[[], dict]
    last_runtime_context_cache_status: Dict[str, Dict[str, object]]
    last_runtime_contexts: Dict[str, Dict[str, object]]
    runtime_context: Callable[..., dict]
    settings: Dict[str, object]


@dataclass(frozen=True)
class CacheFlowInputs:
    graph_assembly_cache_max_entries: Callable[[], int]
    graph_assembly_cache_ttl_seconds: Callable[[], float]
    graph_cache: GraphAssemblyCache
    last_runtime_contexts: Dict[str, Dict[str, object]]
    persistent_graph_assembly_cache_get: Callable[[str], dict]
    persistent_graph_assembly_cache_put: Callable[..., dict]


@dataclass(frozen=True)
class ModelEvidenceInputs:
    last_runtime_contexts: Dict[str, Dict[str, object]]
    settings: Dict[str, object]
    statistical_signal_service: Optional[StatisticalSignalRunner]


@dataclass(frozen=True)
class AssemblyInputs:
    cache: CacheFlowInputs
    capture: CaptureInputs
    graph_assembly_cache_enabled: Callable[[], bool]
    graph_assembly_cache_key: Callable[..., str]
    graph_for_graph_store_persistence: Callable[
        [PortfolioOntology, dict], PortfolioOntology
    ]
    model: ModelEvidenceInputs


@dataclass(frozen=True)
class PreparedGraphInput:
    observation_input: Dict[str, object]
    input_mode: str
    input_symbols: List[str]
    projection_external_signals: Dict[str, object]
    input_projection: Dict[str, object]
    graph_input_snapshot: AccountSnapshot
    active_tbox: Dict[str, object]
    runtime_context: Dict[str, object]
    runtime_context_packet: Dict[str, object]
