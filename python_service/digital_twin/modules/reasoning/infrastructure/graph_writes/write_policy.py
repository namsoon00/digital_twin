"""Write policy implementation; facade-independent dependencies."""

from __future__ import annotations
from .write_policy_ports import (
    WritePolicyPort,
    AboxDeleteBatchSizeBindings,
    AboxIncrementalCleanupBatchSizeBindings,
    AboxIncrementalCleanupMaxBatchesPerSaveBindings,
    AboxInactiveGenerationKeepCountBindings,
    AboxInactiveGenerationMaxPrunePerSaveBindings,
    DeferredMaintenanceAboxMaxManifestsBindings,
    DeferredMaintenanceAboxMaxDeleteBatchesBindings,
    DeferredMaintenanceAboxDeleteBatchSizeBindings,
    AboxWriteTransactionQueryCountBindings,
    AboxNodeBatchSizeBindings,
    AboxRelationBatchSizeBindings,
    GraphWriteTransactionQueryCountBindings,
    StaticNodeInsertBatchSizeBindings,
    StaticWriteTransactionQueryCountBindings,
    InferenceboxWriteTransactionQueryCountBindings,
    InferenceboxRelationBatchSizeBindings,
    InferenceboxGivenRelationWritesEnabledBindings,
    InferenceboxGivenRelationBatchSizeBindings,
    WriteQueryMaxBytesBindings,
    GivenRelationWritesEnabledBindings,
    GivenRelationBatchSizeBindings,
)
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from typing import Dict


def abox_delete_batch_size(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: AboxDeleteBatchSizeBindings,
) -> int:
    configured_settings = _bindings.runtime_settings() if settings is None else settings
    raw = dict(configured_settings or {}).get("typedbABoxDeleteBatchSize")
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 1000
    # ABox replacement is a bounded operational cleanup, not a per-row
    # workflow. Very small batches turn a few thousand facts into dozens
    # of TypeDB commits and can starve the live reasoning worker before
    # it reaches the first insert batch.
    return max(100, min(5000, int(parsed)))


def abox_incremental_cleanup_batch_size(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: AboxIncrementalCleanupBatchSizeBindings,
) -> int:
    """Keep one live cleanup slice below the TypeDB writer saturation point."""
    configured_settings = _bindings.runtime_settings() if settings is None else settings
    raw = dict(configured_settings or {}).get("typedbABoxIncrementalCleanupBatchSize")
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 50
    # Full deletion remains available to explicit repair commands. Runtime
    # projection drains only a small slice, so a historic generation can
    # never monopolize the writer before the next market inference runs.
    return max(10, min(500, int(parsed)))


def abox_incremental_cleanup_max_batches_per_save(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: AboxIncrementalCleanupMaxBatchesPerSaveBindings,
) -> int:
    configured_settings = _bindings.runtime_settings() if settings is None else settings
    raw = dict(configured_settings or {}).get(
        "typedbABoxIncrementalCleanupMaxBatchesPerSave"
    )
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 1
    return max(0, min(4, int(parsed)))


def abox_inactive_generation_keep_count(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: AboxInactiveGenerationKeepCountBindings,
) -> int:
    raw = (settings or _bindings.runtime_settings()).get(
        "typedbABoxInactiveGenerationKeepCount"
    )
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 0
    # MySQL keeps the source snapshot and activation audit. TypeDB retains
    # only active facts, not a rollback or time-series history.
    return max(0, min(5, int(parsed)))


def abox_inactive_generation_max_prune_per_save(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: AboxInactiveGenerationMaxPrunePerSaveBindings,
) -> int:
    raw = (settings or _bindings.runtime_settings()).get(
        "typedbABoxInactiveGenerationMaxPrunePerSave"
    )
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 2
    # Deletes are deliberately bounded so a live activation cannot spend
    # minutes reclaiming a historic backlog under TypeDB's writer lock.
    return max(0, min(10, int(parsed)))


def deferred_maintenance_abox_max_manifests(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: DeferredMaintenanceAboxMaxManifestsBindings,
) -> int:
    """Allow idle maintenance to drain faster than a live ABox save.

    The realtime activation path intentionally removes at most a couple of
    manifests. Once the queue is idle, a larger bounded slice prevents a
    sustained market session from leaving hundreds of immutable manifests
    behind indefinitely.
    """
    raw = (settings or _bindings.runtime_settings()).get(
        "typedbDeferredMaintenanceMaxManifests"
    )
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 10
    return max(1, min(10, int(parsed)))


def deferred_maintenance_abox_max_delete_batches(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: DeferredMaintenanceAboxMaxDeleteBatchesBindings,
) -> int:
    """Bound physical TypeDB deletes independently from Manifest count.

    One immutable Manifest can own several scope generations and each
    generation can require many TypeDB delete transactions. This budget
    is the real latency guard for a low-priority retention pass.
    """
    raw = (settings or _bindings.runtime_settings()).get(
        "ontologyAboxMaintenanceMaxDeleteBatchesPerRun"
    )
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 2
    return max(1, min(50, int(parsed)))


def deferred_maintenance_abox_delete_batch_size(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: DeferredMaintenanceAboxDeleteBatchSizeBindings,
) -> int:
    """Use short deletes for deferred retention, independent of ABox replacement."""
    raw = (settings or _bindings.runtime_settings()).get(
        "ontologyAboxMaintenanceDeleteBatchSize"
    )
    parsed = number_or_none(raw)
    if parsed is None:
        return _store.abox_incremental_cleanup_batch_size(settings)
    return max(10, min(500, int(parsed)))


def abox_write_transaction_query_count(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: AboxWriteTransactionQueryCountBindings,
) -> int:
    raw = (settings or _bindings.runtime_settings()).get(
        "typedbABoxWriteTransactionQueryCount"
    )
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 16
    # A single ABox refresh can produce dozens of insert queries. Keeping
    # fifty of them in one transaction made the TypeDB writer hold its lock
    # for several minutes under live market load, which starved the next
    # reasoning and notification cycle. Sixteen keeps commit overhead
    # bounded without bringing back the long writer lock; larger explicit
    # settings are capped at twenty-four for the same reason.
    return max(1, min(24, int(parsed)))


def abox_node_batch_size(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: AboxNodeBatchSizeBindings,
) -> int:
    """Keep one native TypeQL insert plan below the transport idle edge."""

    raw = dict(settings or _bindings.runtime_settings()).get("typedbABoxNodeBatchSize")
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 10
    # Independent inserts in one TypeQL query still share one planner
    # graph. Ten keeps large shared-world replays responsive while the
    # transaction grouping above amortises commit overhead.
    return max(1, min(10, int(parsed)))


def abox_relation_batch_size(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: AboxRelationBatchSizeBindings,
) -> int:
    configured_settings = _bindings.runtime_settings() if settings is None else settings
    raw = dict(configured_settings or {}).get("typedbABoxRelationBatchSize")
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 1
    # A live replay on TypeDB 3.12 showed the planner spending minutes in
    # a beam-search plan for even a small group of independent endpoint
    # matches. A relation write is therefore one edge per TypeQL query.
    # Queries remain grouped into short write transactions, so this avoids
    # the planner cross product without one commit per edge.
    return max(1, min(1, int(parsed)))


def graph_write_transaction_query_count(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: GraphWriteTransactionQueryCountBindings,
) -> int:
    raw = (settings or _bindings.runtime_settings()).get(
        "typedbGraphWriteTransactionQueryCount"
    )
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 8
    # TBox and RuleBox seeding can also contain
    # thousands of queries. Seed in short commits so startup does not hold
    # the TypeDB writer for minutes before the live ABox worker can run.
    # A subsequent seed deletes and rebuilds those boxes, so retrying a
    # partial seed is deterministic.
    return max(1, min(50, int(parsed)))


def static_node_insert_batch_size(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: StaticNodeInsertBatchSizeBindings,
) -> int:
    """Keep immutable ontology seed inserts planner-safe on a live ABox.

    TypeDB 3 plans independent node inserts together.  A RuleBox component
    contains a large JSON contract and many promoted attributes, so a
    conventional 100-node batch can consume minutes of CPU before any
    rows commit when a multi-gigabyte ABox is present.  Static seed writes
    are rare and correctness-critical, therefore their safe default is
    one node per TypeQL query.  Operators can raise the bounded setting
    after benchmarking their own TypeDB deployment.
    """
    raw = dict(settings or _bindings.runtime_settings()).get(
        "typedbStaticNodeBatchSize"
    )
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 1
    return max(1, min(8, int(parsed)))


def static_write_transaction_query_count(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: StaticWriteTransactionQueryCountBindings,
) -> int:
    """Bound commits for static seed writes without combining TypeQL plans."""
    raw = dict(settings or _bindings.runtime_settings()).get(
        "typedbStaticWriteTransactionQueryCount"
    )
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 16
    return max(1, min(32, int(parsed)))


def inferencebox_write_transaction_query_count(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: InferenceboxWriteTransactionQueryCountBindings,
) -> int:
    configured_settings = _bindings.runtime_settings() if settings is None else settings
    raw = dict(configured_settings or {}).get(
        "typedbInferenceBoxWriteTransactionQueryCount"
    )
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 24
    # One candidate normally fits in one transaction. The cap keeps a
    # pathological trace set bounded while avoiding separate commits for
    # candidate cleanup, rows, and the candidate marker.
    return max(1, min(50, int(parsed)))


def inferencebox_relation_batch_size(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: InferenceboxRelationBatchSizeBindings,
) -> int:
    raw = (settings or _bindings.runtime_settings()).get(
        "typedbInferenceBoxRelationBatchSize"
    )
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 1
    # Inference traces are denser than ABox facts, so grouped endpoint
    # matches are particularly costly to compile. Keep the same safe
    # single-edge plan used by the live ABox writer.
    return max(1, min(1, int(parsed)))


def inferencebox_given_relation_writes_enabled(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: InferenceboxGivenRelationWritesEnabledBindings,
) -> bool:
    values = dict(_bindings.runtime_settings() if settings is None else settings or {})
    raw = values.get("typedbInferenceBoxGivenRelationWritesEnabled")
    if raw is None:
        return True
    return str(raw).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }


def inferencebox_given_relation_batch_size(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: InferenceboxGivenRelationBatchSizeBindings,
) -> int:
    values = dict(_bindings.runtime_settings() if settings is None else settings or {})
    configured = number_or_none(values.get("typedbInferenceBoxGivenRelationBatchSize"))
    if configured is None:
        configured = 50
    return max(1, min(250, int(configured)))


def write_query_max_bytes(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: WriteQueryMaxBytesBindings,
) -> int:
    configured_settings = _bindings.runtime_settings() if settings is None else settings
    raw = dict(configured_settings or {}).get("typedbWriteMaxQueryBytes")
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 192000
    return max(4096, min(256000, int(parsed)))


def given_relation_writes_enabled(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: GivenRelationWritesEnabledBindings,
) -> bool:
    values = dict(_bindings.runtime_settings() if settings is None else settings or {})
    raw = values.get("typedbABoxGivenRelationWritesEnabled")
    if raw is None:
        # TypeDB 3.12 accepts ``given`` input rows. The legacy query path
        # remains an automatic per-batch fallback for mixed deployments.
        return True
    return str(raw).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def given_relation_batch_size(
    _store: WritePolicyPort,
    settings: Dict[str, object] = None,
    *,
    _bindings: GivenRelationBatchSizeBindings,
) -> int:
    values = dict(_bindings.runtime_settings() if settings is None else settings or {})
    configured = number_or_none(values.get("typedbABoxGivenRelationBatchSize"))
    if configured is None:
        configured = 50
    # The old multi-edge query created independent endpoint matches in a
    # single TypeQL plan. ``given`` keeps one stable plan and streams row
    # values, but a bounded size still limits transaction validation work.
    return max(1, min(250, int(configured)))
