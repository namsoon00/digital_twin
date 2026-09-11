"""Reasoning runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.reasoning.public import (
        IndependentReasoningJobRunner,
        V2ReasoningEngine,
    )


def build_v2_reasoning_engine(
    settings=None,
    deployment_id: str = "",
) -> V2ReasoningEngine:
    """Compose V2 from source ports without constructing MonitorRunner."""
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.outcomes import build_hypothesis_lifecycle_service
    from digital_twin.infrastructure.composition.reasoning_shadow import (
        ActiveDeploymentWorldProjectionSink,
        V2InferenceDetailReceiptSink,
    )
    from digital_twin.infrastructure.event_bus import default_event_bus
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
    from digital_twin.infrastructure.reasoning_snapshot_source import (
        LatestMonitorSnapshotReasoningSource,
    )
    from digital_twin.infrastructure.statistical_signal_factory import (
        build_statistical_signal_pipeline_service,
    )
    from digital_twin.infrastructure.time_series_factory import build_time_series_adapters
    from digital_twin.modules.reasoning.public import (
        IndependentReasoningInputAssembler,
        ScopedTypeDBInferenceExecutor,
        SharedInstrumentInferenceService,
        V2GraphDecisionCandidateBuilder,
        V2ReasoningEngine,
    )

    from .reasoning_launch import prepare_v2_launch

    reasoning_launch = prepare_v2_launch(settings, deployment_id)
    configured = reasoning_launch.configured
    platform = reasoning_launch.platform
    descriptor = reasoning_launch.descriptor
    deployment_health = reasoning_launch.deployment_health
    candidate_settings = reasoning_launch.candidate_settings
    store_settings = reasoning_launch.store_settings

    monitor_store = stores.ontology_reasoning_monitor_store(store_settings)
    subscription_state_store = stores.monitor_store(store_settings)
    account_repository = stores.account_reader(store_settings)
    snapshot_source = LatestMonitorSnapshotReasoningSource(
        monitor_store,
        settings=candidate_settings,
    )
    repository = ontology_repository_from_settings(candidate_settings)
    from .reasoning_binding import bind_v2_release

    reasoning_binding = bind_v2_release(
        repository, platform, descriptor, candidate_settings, configured, deployment_health
    )
    candidate_rulebox = reasoning_binding.candidate_rulebox
    rulebox_release_preflight = reasoning_binding.rulebox_release_preflight
    rulebox_fingerprint = reasoning_binding.rulebox_fingerprint
    release_identity = reasoning_binding.release_identity
    release_artifact_persistence = reasoning_binding.release_artifact_persistence

    reasoning_time_series_adapters = build_time_series_adapters(candidate_settings)
    reasoning_time_series_store = reasoning_time_series_adapters.get(
        descriptor.time_series_backend_id
    )
    if reasoning_time_series_store is None:
        raise RuntimeError(
            "The V2 reasoning time-series backend is unavailable: "
            + str(descriptor.time_series_backend_id or "unknown")
        )
    # V2 reads through the backend frozen into its deployment descriptor.
    # Notification bookkeeping owns a MySQL transaction and therefore receives
    # the versioned write boundary independently of the reasoning read adapter.
    delivery_time_series_store = stores.market_time_series_store(store_settings)
    registry_store = stores.reasoning_engine_registry_store(store_settings)
    shared_world_projection_outbox = ActiveDeploymentWorldProjectionSink(
        stores.ontology_world_projection_outbox_store(store_settings),
        registry_store,
        descriptor.deployment_id,
    )
    projection_recorder = PortfolioOntologyProjectionRecorder(
        repository,
        quality_store=stores.ontology_quality_sample_store(store_settings),
        projection_run_store=stores.ontology_projection_run_store(store_settings),
        decision_episode_store=stores.investment_decision_episode_store(store_settings),
        hypothesis_proposal_store=stores.investment_research_store(store_settings),
        hypothesis_lifecycle_store=stores.hypothesis_lifecycle_store(store_settings),
        data_pipeline_health_store=stores.data_pipeline_health_store(store_settings),
        market_time_series_store=reasoning_time_series_store,
        investment_domain_store=stores.investment_domain_store(store_settings),
        world_projection_outbox=shared_world_projection_outbox,
        inference_detail_outbox=V2InferenceDetailReceiptSink(),
        # V2 workers are isolated processes. Reuse is still exact-source only:
        # the cache key includes the source snapshot, TBox, RuleBox, model
        # settings, target scope, and runtime context fingerprints.
        graph_assembly_cache_store=stores.ontology_graph_assembly_cache_store(store_settings),
        statistical_signal_service=build_statistical_signal_pipeline_service(
            {
                **store_settings,
                "_reasoningFeatureSetVersion": descriptor.release_bundle.feature_set_version,
            }
        ),
        settings=candidate_settings,
        source="reasoning-engine-v2-independent",
        frozen_rulebox_catalog=candidate_rulebox,
        frozen_tbox_metadata={
            **dict(rulebox_release_preflight.get("tboxReleasePreflight") or {}),
            "status": "ok",
        },
    )
    from .reasoning_warmup import warm_v2_release

    reasoning_warmup = warm_v2_release(projection_recorder, candidate_rulebox)
    runtime_rulebox_catalog = reasoning_warmup.runtime_rulebox_catalog
    compiled_ontology_release = reasoning_warmup.compiled_ontology_release
    runtime_world_partition = reasoning_warmup.runtime_world_partition

    shared_inference_store = stores.shared_instrument_inference_store(store_settings)
    shared_inference_service = SharedInstrumentInferenceService(
        shared_inference_store,
        descriptor.deployment_id,
        str(release_identity.get("releaseFingerprint") or ""),
        rule_catalog_provider=projection_recorder.rulebox_rules_for_impact,
    )
    from .reasoning_release_health import record_v2_release_health

    record_v2_release_health(
        registry_store,
        descriptor,
        candidate_rulebox,
        rulebox_fingerprint,
        release_identity,
        rulebox_release_preflight,
        release_artifact_persistence,
        runtime_rulebox_catalog,
        compiled_ontology_release,
        runtime_world_partition,
    )

    from .reasoning_delivery import wire_v2_decision_services

    reasoning_delivery = wire_v2_decision_services(
        registry_store,
        descriptor,
        store_settings,
        candidate_settings,
        subscription_state_store,
        delivery_time_series_store,
        account_repository,
    )
    delivery_authorized = reasoning_delivery.delivery_authorized
    subject_decision_orchestrator = reasoning_delivery.subject_decision_orchestrator
    ai_insight_handoff_service = reasoning_delivery.ai_insight_handoff_service
    insight_dispatch_service = reasoning_delivery.insight_dispatch_service

    return V2ReasoningEngine(
        descriptor=descriptor,
        input_assembler=IndependentReasoningInputAssembler(
            account_repository,
            snapshot_source,
            monitor_store,
            candidate_settings,
            instrument_subscription_index=shared_inference_service,
            instrument_subscription_state_source=subscription_state_store,
        ),
        inference_executor=ScopedTypeDBInferenceExecutor(
            projection_recorder,
            shared_inference_service=shared_inference_service,
            post_inference_observer=build_hypothesis_lifecycle_service(
                store_settings,
                event_publisher=default_event_bus(),
            ),
            settings=candidate_settings,
        ),
        candidate_builder=V2GraphDecisionCandidateBuilder(
            candidate_settings,
            monitor_store,
            delivery_history_store=stores.notification_job_store(store_settings),
        ),
        cycle_recorder=stores.monitoring_cycle_recorder(
            store_settings,
            monitor_store,
            delivery_time_series_store,
        ),
        delivery_authorized_provider=delivery_authorized,
        settings=candidate_settings,
        release_identity=release_identity,
        reasoning_orchestrator=subject_decision_orchestrator,
        shared_inference_service=shared_inference_service,
        ai_insight_handoff_service=ai_insight_handoff_service,
        insight_dispatch_service=insight_dispatch_service,
    )


def resolve_v2_reasoning_worker_deployment(
    settings=None,
    worker_role: str = "configured",
    deployment_id: str = "",
) -> str:
    """Resolve one worker to an authoritative control-plane deployment."""
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings

    configured = dict(settings or runtime_settings())
    explicit = str(deployment_id or "").strip()
    if explicit:
        return explicit
    role = str(worker_role or "configured").strip().lower()
    if role == "configured":
        return str(configured.get("reasoningEngineV2DeploymentId") or "ontology-v2-shadow").strip()
    registry = stores.reasoning_engine_registry_store(configured)
    control = registry.control()
    selected = {
        "delivery": str(control.delivery_deployment_id or control.active_deployment_id or ""),
        "active": str(control.active_deployment_id or ""),
        "candidate": str(control.candidate_deployment_id or ""),
    }.get(role, "")
    if not selected:
        raise RuntimeError("No V2 reasoning deployment is assigned to worker role: " + role)
    row = dict(registry.get(selected) or {})
    if str(row.get("engineVersion") or row.get("engine_version") or "").lower() != "v2":
        raise RuntimeError(
            "The deployment assigned to worker role " + role + " is not a V2 engine: " + selected
        )
    return selected


def build_v2_reasoning_job_runner(
    settings=None,
    worker_id: str = "",
    worker_role: str = "configured",
    deployment_id: str = "",
) -> IndependentReasoningJobRunner:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.reasoning_projection import (
        build_ontology_maintenance_runner,
        build_ontology_world_projection_runner,
    )
    from digital_twin.infrastructure.composition.runtime_support import (
        ontology_graph_single_writer_enabled,
        typedb_capacity_guard,
    )
    from digital_twin.infrastructure.graph_writer_guard import LocalGraphWriterGuard
    from digital_twin.infrastructure.settings import data_dir, runtime_settings
    from digital_twin.modules.reasoning.public import (
        IndependentReasoningComparisonService,
        IndependentReasoningJobRunner,
    )

    configured = dict(settings or runtime_settings())
    selected_deployment_id = resolve_v2_reasoning_worker_deployment(
        configured,
        worker_role=worker_role,
        deployment_id=deployment_id,
    )
    configured["reasoningEngineV2DeploymentId"] = selected_deployment_id
    store_settings = dict(configured)
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"
    from digital_twin.infrastructure.mysql_reasoning_ingress import MySQLReasoningIngressRouter

    registry = stores.reasoning_engine_registry_store(configured)
    selected_deployment = dict(registry.get(selected_deployment_id) or {})
    graph_database = str(
        selected_deployment.get("graphStoreBinding")
        or selected_deployment.get("graph_store_binding")
        or ""
    ).strip()
    role = str(worker_role or "configured").strip().lower()
    control = registry.control()
    if role == "candidate":
        protected_bindings = set()
        for protected_id in {
            str(control.active_deployment_id or "").strip(),
            str(control.delivery_deployment_id or "").strip(),
        }:
            if not protected_id:
                continue
            protected = dict(registry.get(protected_id) or {})
            binding = str(
                protected.get("graphStoreBinding") or protected.get("graph_store_binding") or ""
            ).strip()
            if binding:
                protected_bindings.add(binding)
        if graph_database and graph_database in protected_bindings:
            health = dict(selected_deployment.get("health") or {})
            health.update(
                {
                    "status": "blocked",
                    "candidateGraphIsolation": {
                        "status": "blocked",
                        "isolated": False,
                        "graphStoreBinding": graph_database,
                        "protectedGraphStoreBindings": sorted(protected_bindings),
                        "reasonCode": "candidate-graph-store-not-isolated",
                    },
                    "lastError": (
                        "Candidate TypeDB graphStoreBinding must be isolated from active delivery."
                    ),
                }
            )
            registry.update_health(selected_deployment_id, health)
            raise RuntimeError(
                "Candidate TypeDB graphStoreBinding is shared with active delivery: "
                + graph_database
            )

    single_writer = ontology_graph_single_writer_enabled(configured)
    graph_writer_guard = (
        LocalGraphWriterGuard(
            graph_database,
            role=role,
            deployment_id=selected_deployment_id,
            lock_directory=data_dir() / "graph-writer-locks",
            graph_address=str(configured.get("typedbAddress") or "127.0.0.1:1729").strip(),
        )
        if single_writer
        else None
    )
    background_graph_tasks = []
    delivery_id = str(control.delivery_deployment_id or control.active_deployment_id or "").strip()
    if single_writer and role in {"delivery", "active"} and selected_deployment_id == delivery_id:
        world_projection_runner = build_ontology_world_projection_runner(configured)
        maintenance_runner = build_ontology_maintenance_runner(configured)
        background_graph_tasks = [
            {
                "name": "shared-world-projection",
                "runner": world_projection_runner,
                "intervalSeconds": int(
                    configured.get("ontologyWorldProjectionIntervalSeconds") or 10
                ),
            },
            {
                "name": "abox-maintenance",
                "runner": maintenance_runner,
                "intervalSeconds": int(
                    configured.get("ontologyAboxMaintenanceIntervalSeconds") or 60
                ),
            },
        ]

    market_observation_anchor_store = stores.market_observation_reasoning_anchor_store(
        store_settings
    )
    reasoning_job_store = stores.reasoning_engine_job_store(configured)
    comparison_service = IndependentReasoningComparisonService(
        job_store=reasoning_job_store,
        comparison_store=stores.reasoning_engine_comparison_store(store_settings),
        registry=registry,
    )
    return IndependentReasoningJobRunner(
        queue=reasoning_job_store,
        engine=build_v2_reasoning_engine(
            configured,
            deployment_id=selected_deployment_id,
        ),
        registry=registry,
        settings=configured,
        worker_id=worker_id,
        event_reader=stores.event_log(configured),
        execution_guard=typedb_capacity_guard(
            configured,
            "reasoning-v2",
            stores.operational_storage_capacity_state_store(store_settings),
        ),
        route_reconciler=MySQLReasoningIngressRouter(store_settings).reconcile,
        deployment_role=worker_role,
        graph_writer_guard=graph_writer_guard,
        background_graph_tasks=background_graph_tasks,
        market_observation_completion_recorder=market_observation_anchor_store.complete,
        market_observation_completion_reconciler=(
            lambda: market_observation_anchor_store.reconcile_completed_reasoning_jobs(
                selected_deployment_id
            )
        ),
        comparison_reconciler=comparison_service.reconcile,
    )
