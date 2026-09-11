"""Reasoning runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.reasoning.public import IndependentReasoningJobRunner, V2ReasoningEngine


def build_v2_reasoning_engine(
    settings=None,
    deployment_id: str = "",
) -> V2ReasoningEngine:
    """Compose V2 from source ports without constructing MonitorRunner."""
    from digital_twin.domain.investment_reasoning import reasoning_rule_inventory
    from digital_twin.domain.investment_ubiquitous_language import investment_language_registry
    from digital_twin.domain.monitoring import RealtimeMonitor
    from digital_twin.domain.ontology_compiler import compile_ontology_release
    from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
    from digital_twin.domain.ontology_schema import default_tbox_metadata
    from digital_twin.domain.reasoning_engine_versions import reasoning_release_identity
    from digital_twin.domain.reasoning_shadow import payload_hash
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.decisions import build_investment_brain_service
    from digital_twin.infrastructure.composition.outcomes import build_hypothesis_lifecycle_service
    from digital_twin.infrastructure.composition.reasoning_release import prepare_v2_rulebox_release
    from digital_twin.infrastructure.composition.reasoning_shadow import (
        ActiveDeploymentWorldProjectionSink,
        V2InferenceDetailReceiptSink,
    )
    from digital_twin.infrastructure.disclosure_analyzer import disclosure_analyzer_from_settings
    from digital_twin.infrastructure.event_bus import default_event_bus
    from digital_twin.infrastructure.graph_store_lifecycle import ontology_release_seed_artifact
    from digital_twin.infrastructure.graph_store_rulebox import rulebox_rules_from_payload
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
    from digital_twin.infrastructure.reasoning_snapshot_source import LatestMonitorSnapshotReasoningSource
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.infrastructure.statistical_signal_factory import build_statistical_signal_pipeline_service
    from digital_twin.infrastructure.time_series_factory import build_time_series_adapters
    from digital_twin.modules.decisions.public import (
        DecisionContinuityService,
        InvestmentAIInsightHandoffService,
        InvestmentInsightDispatchService,
        NotificationAIDecisionContextEnricher,
        NotificationAIRequestEnqueuer,
    )
    from digital_twin.modules.notifications.public import (
        CompositeNotificationContextEnricher,
        DisclosureAnalysisNotificationEnricher,
        NotificationAIOpinionEnricher,
        NotificationHoldingSnapshotEnricher,
        NotificationHypothesisResearchEnricher,
        NotificationIngressService,
        NotificationInstrumentIdentityEnricher,
    )
    from digital_twin.modules.reasoning.public import (
        IndependentReasoningInputAssembler,
        InvestmentReasoningOrchestrator,
        ScopedTypeDBInferenceExecutor,
        SharedInstrumentInferenceService,
        V2GraphDecisionCandidateBuilder,
        V2ReasoningEngine,
    )

    configured = dict(settings or runtime_settings())
    from digital_twin.infrastructure.reasoning_engine_factory import build_reasoning_engine_platform

    platform = build_reasoning_engine_platform(configured)
    platform.initialize()
    deployment_id = str(
        deployment_id
        or configured.get("reasoningEngineV2DeploymentId")
        or "ontology-v2-shadow"
    ).strip()
    descriptor = platform.deployment_descriptor(deployment_id)
    if str(descriptor.engine_version or "").lower() != "v2":
        raise RuntimeError("The V2 reasoning deployment descriptor is unavailable")

    deployment_row = dict(platform.registry.get(descriptor.deployment_id) or {})
    deployment_health = dict(deployment_row.get("health") or {})
    candidate_settings = dict(configured)
    candidate_settings["typedbDatabase"] = platform.graph_database_for(
        descriptor.deployment_id
    )
    candidate_settings["timeSeriesActiveBackendId"] = descriptor.time_series_backend_id
    candidate_settings["typedbNativeRuleExecutionEnabled"] = "1"
    candidate_settings["ontologyReasoningTypeDbNativeRuleExecutionEnabled"] = "1"
    candidate_settings["ontologySharedMarketWorldAsyncProjectionEnabled"] = "0"
    candidate_settings["ontologyIncrementalCurrentStateReasoningEnabled"] = "1"
    candidate_settings["ontologyCurrentStateAboxStorageEnabled"] = "1"
    if str(
        candidate_settings.get("ontologyTemporalObservationAnchorProjectionEnabled")
        or "auto"
    ).strip().lower() in {"", "auto"}:
        # QuestDB owns historical points. Freeze V2's auto mode to compact
        # temporal summaries before the projection settings are serialized.
        candidate_settings["ontologyTemporalObservationAnchorProjectionEnabled"] = "0"
    candidate_settings["ontologyWorldPartitionedReasoningEnabled"] = "0"
    candidate_settings["ontologyInferenceDetailOutboxEnabled"] = "1"
    candidate_settings["ontologyAsyncQualityRecordEnabled"] = "0"
    provisioning_contract = dict(
        deployment_health.get("graphStoreProvisioning") or {}
    )
    reuses_existing_graph_store = (
        str(provisioning_contract.get("mode") or "").strip().lower()
        == "reuse-existing"
        and str(provisioning_contract.get("database") or "").strip()
        == str(candidate_settings.get("typedbDatabase") or "").strip()
    )
    if (
        str(descriptor.status or "").strip().lower() == "provisioning"
        and not reuses_existing_graph_store
    ):
        candidate_settings["typedbFreshCandidateRebuild"] = "1"
    candidate_settings["_reasoningEngineDeploymentId"] = descriptor.deployment_id
    candidate_settings["_reasoningEngineVersion"] = descriptor.engine_version
    candidate_settings["_reasoningTimeSeriesBackendId"] = descriptor.time_series_backend_id
    candidate_settings["_reasoningFeatureSetVersion"] = descriptor.release_bundle.feature_set_version
    store_settings = dict(configured)
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"
    monitor_store = stores.ontology_reasoning_monitor_store(store_settings)
    subscription_state_store = stores.monitor_store(store_settings)
    account_repository = stores.account_reader(store_settings)
    snapshot_source = LatestMonitorSnapshotReasoningSource(
        monitor_store,
        settings=candidate_settings,
    )
    repository = ontology_repository_from_settings(candidate_settings)
    engine_control = platform.registry.control()
    protected_deployment_ids = {
        str(engine_control.active_deployment_id or "").strip(),
        str(engine_control.delivery_deployment_id or "").strip(),
    }
    protected_deployment_ids.discard("")
    frozen_release_recorded = bool(
        str(deployment_health.get("candidateReleaseId") or "").strip()
        and str(deployment_health.get("ruleboxFingerprint") or "").strip()
    )
    candidate_rulebox, rulebox_release_preflight = prepare_v2_rulebox_release(
        repository,
        candidate_settings,
        release_guard={
            "immutable": (
                descriptor.deployment_id in protected_deployment_ids
                or frozen_release_recorded
            ),
            "ruleboxFingerprint": str(
                deployment_health.get("ruleboxFingerprint") or ""
            ),
            "tboxFingerprint": str(
                deployment_health.get("tboxFingerprint") or ""
            ),
            "tboxVersion": str(
                descriptor.release_bundle.tbox_release_id or ""
            ).split("@", 1)[0],
        },
    )
    rulebox_fingerprint = str(
        candidate_rulebox.get("sourceRulesHash")
        or candidate_rulebox.get("rulesHash")
        or candidate_rulebox.get("ruleboxRulesHash")
        or payload_hash(candidate_rulebox.get("rules") or [])
    )
    runtime_tbox_metadata = repository.active_tbox_metadata()
    release_seed_artifact = ontology_release_seed_artifact(
        default_graph_inference_rules(),
        language_registry=investment_language_registry(configured),
        tbox_metadata=default_tbox_metadata(),
        release_bundle=descriptor.release_bundle.to_dict(),
    )
    save_release_artifact = getattr(platform.registry, "save_release_artifact", None)
    read_release_artifact = getattr(platform.registry, "release_artifact", None)
    stored_release_artifact = (
        dict(read_release_artifact(descriptor.deployment_id) or {})
        if callable(read_release_artifact)
        else {}
    )
    runtime_tbox_fingerprint = str(runtime_tbox_metadata.get("fingerprint") or "")
    read_static_manifest = getattr(repository, "read_seed_static_manifest", None)
    runtime_static_manifest = (
        dict(read_static_manifest() or {})
        if callable(read_static_manifest)
        else {}
    )
    runtime_static_metadata = dict(runtime_static_manifest.get("metadata") or {})
    authored_release_matches_runtime = bool(
        str(runtime_static_manifest.get("status") or "") == "ok"
        and str(runtime_static_metadata.get("ruleboxRulesHash") or "")
        == str(release_seed_artifact.get("ruleboxFingerprint") or "")
        and str(runtime_static_metadata.get("tboxFingerprint") or "")
        == str(release_seed_artifact.get("tboxFingerprint") or "")
        and runtime_tbox_fingerprint
        == str(release_seed_artifact.get("tboxFingerprint") or "")
    )
    if stored_release_artifact:
        stored_payload = dict(stored_release_artifact.get("artifact") or {})
        stored_release_matches_runtime = bool(
            bool(stored_release_artifact.get("valid", True))
            and dict(stored_payload.get("releaseBundle") or {})
            == descriptor.release_bundle.to_dict()
            and str(stored_release_artifact.get("ruleboxFingerprint") or "")
            == str(release_seed_artifact.get("ruleboxFingerprint") or "")
            and str(stored_release_artifact.get("tboxFingerprint") or "")
            == runtime_tbox_fingerprint
            and authored_release_matches_runtime
            and rulebox_fingerprint
        )
        release_artifact_persistence = {
            "status": (
                "unchanged"
                if stored_release_matches_runtime
                else "release-artifact-runtime-mismatch"
            ),
            "artifactFingerprint": str(
                stored_release_artifact.get("artifactFingerprint") or ""
            ),
            "ruleboxFingerprint": str(
                stored_release_artifact.get("ruleboxFingerprint") or ""
            ),
            "artifactRuleboxFingerprint": str(
                stored_release_artifact.get("ruleboxFingerprint") or ""
            ),
            "runtimeRuleboxFingerprint": rulebox_fingerprint,
            "tboxFingerprint": str(
                stored_release_artifact.get("tboxFingerprint") or ""
            ),
        }
    elif callable(save_release_artifact) and authored_release_matches_runtime:
        release_artifact_persistence = dict(
            save_release_artifact(descriptor.deployment_id, release_seed_artifact) or {}
        )
        release_artifact_persistence["artifactRuleboxFingerprint"] = str(
            release_artifact_persistence.get("ruleboxFingerprint") or ""
        )
        release_artifact_persistence["runtimeRuleboxFingerprint"] = rulebox_fingerprint
    elif callable(save_release_artifact):
        # A legacy active release may predate durable artifacts. Never bind its
        # old deployment identity to today's authored TBox/RuleBox.
        release_artifact_persistence = {
            "status": "legacy-release-artifact-missing",
            "artifactFingerprint": "",
            "ruleboxFingerprint": "",
            "artifactRuleboxFingerprint": "",
            "runtimeRuleboxFingerprint": rulebox_fingerprint,
            "tboxFingerprint": runtime_tbox_fingerprint,
        }
    else:
        release_artifact_persistence = {"status": "unsupported"}
    release_identity = reasoning_release_identity(descriptor, rulebox_fingerprint)
    candidate_settings["_reasoningEngineReleaseFingerprint"] = str(
        release_identity.get("releaseFingerprint") or ""
    )
    candidate_settings["_reasoningEngineValidationCohortId"] = str(
        release_identity.get("validationCohortId") or ""
    )
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
        statistical_signal_service=build_statistical_signal_pipeline_service({
            **store_settings,
            "_reasoningFeatureSetVersion": descriptor.release_bundle.feature_set_version,
        }),
        settings=candidate_settings,
        source="reasoning-engine-v2-independent",
        frozen_rulebox_catalog=candidate_rulebox,
        frozen_tbox_metadata={
            **dict(rulebox_release_preflight.get("tboxReleasePreflight") or {}),
            "status": "ok",
        },
    )
    runtime_rulebox_catalog = projection_recorder.ensure_rulebox_ready()
    compiled_ontology_release = compile_ontology_release(
        rulebox_rules_from_payload({
            # ``ensure_rulebox_ready`` may return compact warmed metadata.
            # The immutable candidate release is the authoritative source of
            # executable rule bodies for static compilation.
            "rules": list(candidate_rulebox.get("rules") or [])
        })
    )
    runtime_world_partition = projection_recorder.world_rule_partition(
        runtime_rulebox_catalog
    )
    if (
        str(runtime_rulebox_catalog.get("status") or "") != "ready"
        or not bool(compiled_ontology_release.get("valid"))
        or str(runtime_world_partition.get("status") or "") != "ready"
    ):
        raise RuntimeError(
            "The independent V2 frozen ontology release could not be warmed: "
            + str(
                runtime_rulebox_catalog.get("reason")
                or (compiled_ontology_release.get("failures") or [""])[0]
                or (runtime_world_partition.get("failures") or [{}])[0].get("reason")
                or "unknown"
            )[:220]
        )
    projection_recorder.catalog_for_rules(
        runtime_rulebox_catalog,
        runtime_world_partition.get("sharedRules") or [],
    )
    projection_recorder.catalog_for_rules(
        runtime_rulebox_catalog,
        runtime_world_partition.get("overlayRules") or [],
    )
    shared_inference_store = stores.shared_instrument_inference_store(store_settings)
    shared_inference_service = SharedInstrumentInferenceService(
        shared_inference_store,
        descriptor.deployment_id,
        str(release_identity.get("releaseFingerprint") or ""),
        rule_catalog_provider=projection_recorder.rulebox_rules_for_impact,
    )
    existing_health = dict((registry_store.get(descriptor.deployment_id) or {}).get("health") or {})
    frozen_rulebox_fingerprint = str(existing_health.get("ruleboxFingerprint") or "")
    if (
        frozen_rulebox_fingerprint
        and str(existing_health.get("candidateReleaseId") or "")
        and frozen_rulebox_fingerprint != rulebox_fingerprint
    ):
        raise RuntimeError(
            "The independent V2 RuleBox changed after its release was frozen; "
            "register a new V2 deployment before starting the worker."
        )
    rule_inventory = reasoning_rule_inventory(candidate_rulebox.get("rules") or [])
    existing_health.update({
        "candidateReleaseId": release_identity.get("releaseId"),
        "candidateBaseReleaseId": release_identity.get("baseReleaseId"),
        "candidateRuntimeRevision": release_identity.get("runtimeRevision"),
        "candidateReleaseFingerprint": release_identity.get("releaseFingerprint"),
        "releaseFingerprint": release_identity.get("releaseFingerprint"),
        "validationCohortId": release_identity.get("validationCohortId"),
        "ruleboxFingerprint": release_identity.get("ruleboxFingerprint"),
        "tboxFingerprint": release_identity.get("tboxFingerprint"),
        "tboxReleaseId": release_identity.get("tboxReleaseId"),
        "ruleboxReleaseId": release_identity.get("ruleboxReleaseId"),
        "promptReleaseId": release_identity.get("promptReleaseId"),
        "modelSignalReleaseId": release_identity.get("modelSignalReleaseId"),
        "independentExecution": True,
        "directSourceEvents": True,
        "monitorRunnerUsed": False,
        "ruleboxOwnership": "v2-release-frozen",
        "ruleInventory": rule_inventory,
        "ruleInventoryReleaseReady": bool(rule_inventory.get("releaseReady")),
        "ruleboxReleasePreflight": {
            "status": str(rulebox_release_preflight.get("status") or ""),
            "ruleCount": int(rulebox_release_preflight.get("ruleCount") or 0),
            "ruleboxRulesHash": str(
                rulebox_release_preflight.get("ruleboxRulesHash") or ""
            ),
            "migrationStatus": str(
                (rulebox_release_preflight.get("ruleCatalogMigration") or {}).get("status")
                or ""
            ),
        },
        "releaseSeedArtifact": {
            "status": str(release_artifact_persistence.get("status") or ""),
            "artifactFingerprint": str(
                release_artifact_persistence.get("artifactFingerprint") or ""
            ),
            "ruleboxFingerprint": str(
                release_artifact_persistence.get("ruleboxFingerprint") or ""
            ),
            "artifactRuleboxFingerprint": str(
                release_artifact_persistence.get("artifactRuleboxFingerprint")
                or release_artifact_persistence.get("ruleboxFingerprint")
                or ""
            ),
            "runtimeRuleboxFingerprint": str(
                release_artifact_persistence.get("runtimeRuleboxFingerprint")
                or rulebox_fingerprint
                or ""
            ),
            "tboxFingerprint": str(
                release_artifact_persistence.get("tboxFingerprint") or ""
            ),
            "reconstructable": str(
                release_artifact_persistence.get("status") or ""
            ) in {"saved", "unchanged"},
        },
        "runtimeOntologyRelease": {
            "status": "ready",
            "catalogSource": str(
                runtime_rulebox_catalog.get("runtimeCatalogSource") or ""
            ),
            "ruleCount": int(runtime_rulebox_catalog.get("ruleCount") or 0),
            "sharedRuleCount": int(
                runtime_world_partition.get("sharedRuleCount") or 0
            ),
            "overlayRuleCount": int(
                runtime_world_partition.get("overlayRuleCount") or 0
            ),
            "tboxSource": "frozen-v2-release",
            "warmed": True,
            "compilerVersion": str(compiled_ontology_release.get("version") or ""),
            "compilerIrFingerprint": str(
                compiled_ontology_release.get("irFingerprint") or ""
            ),
            "compilerStatus": str(compiled_ontology_release.get("status") or ""),
            "predictiveRuleCount": int(
                compiled_ontology_release.get("predictiveRuleCount") or 0
            ),
        },
        "ruleExecutionReadiness": {
            "status": "ready",
            "mode": "typedb-direct-typeql",
            "realtimeProjectionMode": "incremental-current-state-one-pass-v1",
            "aboxPersistenceMode": "current-state-copy-on-write-v2",
            "sharedPremiseCriticalPath": False,
        },
    })
    registry_store.update_health(descriptor.deployment_id, existing_health)

    def delivery_authorized():
        control = registry_store.control()
        deployment = registry_store.get(descriptor.deployment_id)
        return bool(
            str(control.delivery_deployment_id or "") == descriptor.deployment_id
            and str(control.active_deployment_id or "") == descriptor.deployment_id
            and str(deployment.get("status") or "") == "active"
        )

    decision_episode_store = stores.investment_decision_episode_store(store_settings)
    subject_decision_orchestrator = InvestmentReasoningOrchestrator(
        stores.investment_reasoning_case_store(store_settings),
        decision_episode_store=decision_episode_store,
        hypothesis_proposal_request_store=stores.investment_research_store(store_settings),
        subject_case_repository=stores.subject_decision_case_store(store_settings),
    )
    decision_continuity = DecisionContinuityService(
        decision_episode_store,
        stores.investment_domain_store(store_settings),
    )
    ai_context_preparer = CompositeNotificationContextEnricher(
        NotificationInstrumentIdentityEnricher(
            stores.symbol_universe_store(store_settings)
        ),
        NotificationHoldingSnapshotEnricher(
            subscription_state_store.load_previous,
            RealtimeMonitor(candidate_settings),
        ),
        DisclosureAnalysisNotificationEnricher(
            disclosure_analyzer_from_settings(candidate_settings),
            candidate_settings,
        ),
        NotificationHypothesisResearchEnricher(
            build_investment_brain_service(store_settings),
            candidate_settings,
        ),
        NotificationAIOpinionEnricher(candidate_settings),
        NotificationAIDecisionContextEnricher(
            delivery_time_series_store,
            candidate_settings,
            investment_domain_store=stores.investment_domain_store(store_settings),
        ),
    )
    detached_ai_enqueuer = NotificationAIRequestEnqueuer(
        stores.ai_inference_queue_store(store_settings),
        ai_context_preparer,
        candidate_settings,
        decision_episode_store=decision_episode_store,
        continuity_service=decision_continuity,
        reasoning_orchestrator=subject_decision_orchestrator,
    )
    insight_notification_ingress = NotificationIngressService(
        template_renderer=stores.notification_template_store(store_settings).render,
        settings=candidate_settings,
    )
    insight_notification_queue = stores.notification_job_store(store_settings)
    ai_insight_handoff_service = InvestmentAIInsightHandoffService(
        insight_notification_ingress,
        detached_ai_enqueuer,
        account_repository=account_repository,
    )
    insight_dispatch_service = InvestmentInsightDispatchService(
        insight_notification_ingress,
        insight_notification_queue,
        ai_insight_handoff_service,
        subject_decision_orchestrator,
        account_repository=account_repository,
    )

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
        return str(
            configured.get("reasoningEngineV2DeploymentId") or "ontology-v2-shadow"
        ).strip()
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
                protected.get("graphStoreBinding")
                or protected.get("graph_store_binding")
                or ""
            ).strip()
            if binding:
                protected_bindings.add(binding)
        if graph_database and graph_database in protected_bindings:
            health = dict(selected_deployment.get("health") or {})
            health.update({
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
            })
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
            graph_address=str(
                configured.get("typedbAddress") or "127.0.0.1:1729"
            ).strip(),
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
