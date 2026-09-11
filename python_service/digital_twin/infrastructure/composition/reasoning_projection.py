"""Reasoning Projection runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.reasoning.public import (
        OntologyInferenceDetailRunner,
        OntologyMaintenanceRunner,
        OntologyPortfolioRebuildRunner,
        OntologyReasoningProofService,
        OntologyWorldProjectionRunner,
    )


def build_ontology_world_projection_runner(settings=None) -> OntologyWorldProjectionRunner:
    """Build the independent durable shared-world projection worker."""
    import os
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.reasoning_health import build_ontology_reasoning_queue_probe
    from digital_twin.infrastructure.composition.runtime_support import typedb_capacity_guard
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.reasoning.public import OntologyWorldProjectionRunner

    configured_settings = settings or runtime_settings()
    store_settings = dict(configured_settings)
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"

    storage_guard = typedb_capacity_guard(
        configured_settings,
        "world-projection",
        stores.operational_storage_capacity_state_store(store_settings),
    )
    return OntologyWorldProjectionRunner(
        outbox=stores.ontology_world_projection_outbox_store(store_settings),
        projection_recorder=PortfolioOntologyProjectionRecorder(
            ontology_repository_from_settings(configured_settings),
            graph_assembly_cache_store=stores.ontology_graph_assembly_cache_store(store_settings),
            settings=configured_settings,
            source="ontology-world-projection",
        ),
        settings=configured_settings,
        worker_id=os.environ.get("ONTOLOGY_WORLD_PROJECTION_WORKER_ID") or "",
        reasoning_queue_probe=build_ontology_reasoning_queue_probe(configured_settings),
        storage_guard=storage_guard,
        fairness_state_store=stores.ontology_world_projection_state_store(store_settings),
    )


def build_ontology_portfolio_rebuild_runner(settings=None) -> OntologyPortfolioRebuildRunner:
    """Compose the read-only source replay used before TypeDB cutover."""
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.reasoning.public import OntologyPortfolioRebuildRunner

    configured_settings = dict(settings or runtime_settings())
    store_settings = dict(configured_settings)
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"
    return OntologyPortfolioRebuildRunner(
        snapshot_store=stores.monitor_store(store_settings),
        projection_recorder=PortfolioOntologyProjectionRecorder(
            ontology_repository_from_settings(configured_settings),
            settings=configured_settings,
            source="typedb-blue-green-candidate-rebuild",
        ),
    )


def build_ontology_inference_detail_runner(settings=None) -> OntologyInferenceDetailRunner:
    """Build the idle-only durable InferenceBox detail readback worker."""
    import os
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.reasoning_health import build_ontology_reasoning_queue_probe
    from digital_twin.infrastructure.composition.runtime_support import typedb_capacity_guard
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.reasoning.public import OntologyInferenceDetailRunner

    configured_settings = settings or runtime_settings()
    store_settings = dict(configured_settings)
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"
    storage_guard = typedb_capacity_guard(
        configured_settings,
        "inference-detail",
        stores.operational_storage_capacity_state_store(store_settings),
    )
    return OntologyInferenceDetailRunner(
        outbox=stores.ontology_inference_detail_outbox_store(store_settings),
        ontology_repository=ontology_repository_from_settings(configured_settings),
        settings=configured_settings,
        worker_id=os.environ.get("ONTOLOGY_INFERENCE_DETAIL_WORKER_ID") or "",
        reasoning_queue_probe=build_ontology_reasoning_queue_probe(configured_settings),
        fairness_state_store=stores.ontology_inference_detail_state_store(store_settings),
        storage_guard=storage_guard,
    )


def build_ontology_maintenance_runner(settings=None) -> OntologyMaintenanceRunner:
    """Build the isolated, low-priority scoped ABox retention worker."""
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.reasoning_health import build_ontology_reasoning_queue_probe
    from digital_twin.infrastructure.composition.runtime_support import (
        ontology_graph_store_epoch,
        typedb_capacity_guard,
    )
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.reasoning.public import (
        OntologyMaintenanceRunner,
        OntologyPortfolioScopeRepairRunner,
        OntologyScopeRepairRouter,
    )

    configured_settings = dict(settings or runtime_settings())
    configured_settings["_ontologyGraphStoreEpoch"] = ontology_graph_store_epoch(
        configured_settings
    )
    store_settings = dict(configured_settings)
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"

    capacity_guard = typedb_capacity_guard(
        configured_settings,
        "maintenance",
        stores.operational_storage_capacity_state_store(store_settings),
    )
    shared_scope_repair_outbox = stores.ontology_world_projection_outbox_store(store_settings)
    portfolio_scope_repair = OntologyPortfolioScopeRepairRunner(
        snapshot_store=stores.monitor_store(store_settings),
        projection_recorder=PortfolioOntologyProjectionRecorder(
            ontology_repository_from_settings(configured_settings),
            graph_assembly_cache_store=stores.ontology_graph_assembly_cache_store(store_settings),
            settings=configured_settings,
            source="typedb-scope-integrity-repair",
        ),
        settings=configured_settings,
    )
    return OntologyMaintenanceRunner(
        ontology_repository=ontology_repository_from_settings(configured_settings),
        state_store=stores.ontology_maintenance_state_store(store_settings),
        settings=configured_settings,
        reasoning_queue_probe=build_ontology_reasoning_queue_probe(configured_settings),
        capacity_guard=capacity_guard,
        event_publisher=stores.event_log(store_settings),
        scope_repair_outbox=OntologyScopeRepairRouter(
            shared_scope_repair_outbox,
            portfolio_scope_repair,
        ),
    )


def build_ontology_reasoning_proof_service(settings=None) -> OntologyReasoningProofService:
    """Compose a read-only production-history and TypeDB replay diagnostic."""
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.reasoning.public import OntologyReasoningProofService

    configured_settings = settings or runtime_settings()
    read_only_store_settings = dict(configured_settings)
    read_only_store_settings["_skipOperationalHistoryRetention"] = "1"
    read_only_store_settings["_skipOperationalSchemaBootstrap"] = "1"
    return OntologyReasoningProofService(
        ontology_repository=ontology_repository_from_settings(configured_settings),
        projection_run_store=stores.ontology_projection_run_store(read_only_store_settings),
        account_repository=stores.account_reader(read_only_store_settings),
        reasoning_cursor_store=stores.ontology_reasoning_cursor_store(read_only_store_settings),
        settings=configured_settings,
    )
