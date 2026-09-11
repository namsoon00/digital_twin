"""Reasoning Monitor runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.reasoning.public import OntologyReasoningRunner


def build_ontology_reasoning_runner(settings=None, event_publisher=None) -> OntologyReasoningRunner:
    from digital_twin.domain.ontology_worlds import portfolio_world_id
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.events import ontology_reasoning_event_bus
    from digital_twin.infrastructure.composition.instruments import ontology_reasoning_priority_symbols
    from digital_twin.infrastructure.composition.market_data import build_monitor_runner
    from digital_twin.infrastructure.composition.model_registry import build_investment_strategy_proposal_service
    from digital_twin.infrastructure.composition.reasoning_health import typedb_projection_recovery_health
    from digital_twin.infrastructure.composition.runtime_support import setting_truthy, typedb_capacity_guard
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.reasoning_snapshot_source import LatestMonitorSnapshotReasoningSource
    from digital_twin.infrastructure.rule_change_candidate_ai import rule_change_candidate_advisor_from_settings
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.model_registry.public import RuleChangeCandidateProposalService
    from digital_twin.modules.reasoning.public import (
        OntologyReasoningQueueHealthService,
        OntologyReasoningRunner,
        ReasoningShadowScheduler,
        SharedInstrumentInferenceService,
    )

    configured_settings = settings or runtime_settings()
    from digital_twin.infrastructure.reasoning_engine_factory import build_reasoning_engine_platform

    engine_platform = build_reasoning_engine_platform(configured_settings)
    engine_state = engine_platform.initialize()
    configured_settings = dict(configured_settings)
    v1_deployment_id = str(
        configured_settings.get("reasoningEngineV1DeploymentId")
        or "ontology-v1-active"
    )
    configured_settings["_reasoningEngineDeploymentId"] = v1_deployment_id
    active_deployment = next(
        (
            row for row in engine_state.get("deployments") or []
            if str(row.get("deploymentId") or "") == v1_deployment_id
        ),
        {},
    )
    release_bundle = dict(active_deployment.get("releaseBundle") or {})
    active_release_identity = engine_platform.release_identity(v1_deployment_id)
    configured_settings["_reasoningEngineVersion"] = str(active_deployment.get("engineVersion") or "v1")
    configured_settings["_reasoningEngineReleaseFingerprint"] = str(
        active_release_identity.get("releaseFingerprint") or ""
    )
    configured_settings["_reasoningEngineValidationCohortId"] = str(
        active_release_identity.get("validationCohortId") or ""
    )
    configured_settings["_reasoningTimeSeriesBackendId"] = str(
        active_deployment.get("timeSeriesBackendId") or "mysql-primary"
    )
    configured_settings["_reasoningFeatureSetVersion"] = str(
        release_bundle.get("feature_set_version")
        or release_bundle.get("featureSetVersion")
        or "temporal-features-v1"
    )
    reasoning_store_settings = dict(configured_settings)
    reasoning_store_settings["_skipOperationalHistoryRetention"] = "1"
    reasoning_store_settings["_skipOperationalSchemaBootstrap"] = "1"
    reasoning_monitor_settings = dict(configured_settings)
    reasoning_monitor_settings["_skipOperationalHistoryRetention"] = "1"
    reasoning_monitor_settings["_skipOperationalSchemaBootstrap"] = "1"
    # External collection is owned by dedicated workers. Re-running it while
    # materializing an ABox can block TypeDB reasoning on a vendor response.
    reasoning_monitor_settings["_externalSignalsCacheOnly"] = "1"
    reasoning_native_rule_execution_enabled = setting_truthy(
        configured_settings.get("ontologyReasoningTypeDbNativeRuleExecutionEnabled"),
        True,
    )
    reasoning_monitor_settings["typedbNativeRuleExecutionEnabled"] = "1" if reasoning_native_rule_execution_enabled else "0"
    registry = stores.account_reader(reasoning_store_settings)
    event_log = stores.event_log(reasoning_store_settings)
    ontology_repository = ontology_repository_from_settings(configured_settings)
    cursor_store = stores.ontology_reasoning_cursor_store(reasoning_store_settings)
    maintenance_state_store = stores.ontology_maintenance_state_store(
        reasoning_store_settings,
    )
    snapshot_readiness_source = LatestMonitorSnapshotReasoningSource(
        stores.ontology_reasoning_monitor_store(reasoning_store_settings),
        settings=reasoning_monitor_settings,
    )

    def source_snapshot_preflight(reasoning_context):
        context = dict(reasoning_context or {})
        requested_accounts = {
            str(account_id or "").strip()
            for account_id in context.get("accountIds") or []
            if str(account_id or "").strip()
        }
        accounts = [
            account for account in (registry.load() or [])
            if not requested_accounts or str(getattr(account, "account_id", "") or "").strip() in requested_accounts
        ]
        if requested_accounts and len(accounts) != len(requested_accounts):
            registered = {
                str(getattr(account, "account_id", "") or "").strip()
                for account in accounts
            }
            invalid_accounts = sorted(requested_accounts - registered)
            return {
                "ready": False,
                "status": "rejected-source-account",
                "reason": "The requested account has no registered monitor snapshot source.",
                "reasonCode": "unregistered-source-account",
                "permanent": True,
                "invalidAccountIds": invalid_accounts,
                "validAccountIds": sorted(registered),
                "retryAfterSeconds": 0,
                "accounts": [
                    {"accountId": account_id, "status": "rejected", "permanent": True}
                    for account_id in invalid_accounts
                ],
            }
        return snapshot_readiness_source.preflight(accounts, context)

    def projection_recovery_probe(account_ids, symbols):
        requested_accounts = {
            str(account_id or "").strip()
            for account_id in account_ids or []
            if str(account_id or "").strip()
        }
        accounts = list(registry.load() or [])
        selected_accounts = [
            account for account in accounts
            if not requested_accounts or str(getattr(account, "account_id", "") or "") in requested_accounts
        ]
        if requested_accounts and len(selected_accounts) != len(requested_accounts):
            return {
                "ready": False,
                "status": "account-not-found",
                "reason": "The interrupted projection account is no longer registered.",
                "accounts": [{"accountId": account_id, "ready": False} for account_id in sorted(requested_accounts)],
            }
        requested_symbols = {
            str(symbol or "").upper().strip()
            for symbol in symbols or []
            if str(symbol or "").strip()
        }
        rows = []
        tenant_id = str(configured_settings.get("ontologyTenantId") or configured_settings.get("tenantId") or "")
        for account in selected_accounts:
            account_id = str(getattr(account, "account_id", "") or "").strip()
            world_id = portfolio_world_id(account_id, tenant_id)
            try:
                health = typedb_projection_recovery_health(ontology_repository, world_id)
            except Exception as error:  # noqa: BLE001 - keep the circuit open until the normal retry can read TypeDB.
                rows.append({"accountId": account_id, "worldId": world_id, "ready": False, "reason": str(error)[:180]})
                continue
            rows.append({
                "accountId": account_id,
                "requestedSymbols": sorted(requested_symbols),
                **health,
            })
        return {
            "ready": bool(rows) and all(bool(row.get("ready")) for row in rows),
            "status": "ready" if rows and all(bool(row.get("ready")) for row in rows) else "not-ready",
            "recoveryMode": "active-abox-health-probe",
            "accounts": rows,
        }

    def projection_lease_recovery():
        """Recover only verified-dead local TypeDB writers after a timeout.

        The reasoning parent invokes this only when the coordinator is already
        held or a killable child has exceeded its hard timeout. The repository
        validates hostname and PID before it removes any durable lease.
        """
        recover = getattr(ontology_repository, "recover_all_dead_local_scoped_abox_write_leases", None)
        if not callable(recover):
            return {"status": "unsupported", "clearedCount": 0, "worldCount": 0}
        return dict(recover() or {})

    def projection_coordinator_lease_recovery():
        """Probe only the global writer lease while a live writer may run."""
        recover = getattr(ontology_repository, "recover_dead_projection_coordinator_lease", None)
        if not callable(recover):
            return {"status": "unsupported"}
        return dict(recover() or {})

    def reasoning_worker_maintenance():
        """Keep legacy reasoning cadence free of TypeDB physical deletes.

        The dedicated ``ontology-maintenance`` worker owns all immutable ABox
        retention across portfolio, market, and knowledge worlds.  The
        reasoning runner still invokes this compatibility hook so it can prune
        its durable mailbox on the existing cadence, but it must never take
        the TypeDB writer lease after a live investment projection.
        """
        return {
            "status": "delegated",
            "maintenanceMode": "dedicated-abox-worker",
            "reason": "Scoped ABox retention is owned by the ontology-maintenance worker.",
        }

    def reasoning_monitor_runner():
        reasoning_snapshot_store = stores.ontology_reasoning_monitor_store(
            reasoning_store_settings,
        )
        runner = build_monitor_runner(
            registry.load(),
            settings=reasoning_monitor_settings,
            typedb_native_rule_execution_enabled=reasoning_native_rule_execution_enabled,
            ontology_projection_enabled=True,
            ontology_repository=ontology_repository,
            monitor_store=reasoning_snapshot_store,
            source_snapshot_replay=True,
        )
        runner.snapshot_builder = LatestMonitorSnapshotReasoningSource(
            reasoning_snapshot_store,
            settings=reasoning_monitor_settings,
        )
        return runner

    def refresh_reasoning_monitor_settings(updated_settings, changed_keys, removed_keys):
        """Keep a warm sidecar's short-lived monitor runners in sync.

        The monitor runner is composed per durable turn, but its settings map
        is intentionally retained so it can reuse the TypeDB repository and
        source snapshot boundaries.  Only the application-owned operational
        keys are forwarded by ``OntologyReasoningRunner``.
        """
        updated = dict(updated_settings or {})
        for key in changed_keys or []:
            if key in updated:
                reasoning_monitor_settings[key] = updated[key]
        for key in removed_keys or []:
            reasoning_monitor_settings.pop(key, None)

    storage_guard = typedb_capacity_guard(
        configured_settings,
        "reasoning",
        stores.operational_storage_capacity_state_store(reasoning_store_settings),
    )
    reasoning_shadow_scheduler = (
        None
        if setting_truthy(configured_settings.get("reasoningEngineV2IndependentEnabled"), True)
        else ReasoningShadowScheduler(
            stores.reasoning_shadow_job_store(reasoning_store_settings),
            stores.reasoning_engine_registry_store(reasoning_store_settings),
            configured_settings,
        )
    )
    reasoning_engine_registry = stores.reasoning_engine_registry_store(reasoning_store_settings)

    def v1_rule_catalog():
        try:
            snapshot = dict(ontology_repository.rulebox_snapshot() or {})
        except Exception:
            return []
        return [
            dict(rule)
            for rule in snapshot.get("rules") or []
            if isinstance(rule, dict)
        ]

    shared_inference_service = SharedInstrumentInferenceService(
        stores.shared_instrument_inference_store(reasoning_store_settings),
        v1_deployment_id,
        str(active_release_identity.get("releaseFingerprint") or ""),
        rule_catalog_provider=v1_rule_catalog,
    )

    def publish_shared_inference(projection_results, symbols, runner):
        return shared_inference_service.publish_verified_results(
            projection_results,
            symbols,
            states=getattr(runner, "last_reasoning_source_states", {}) or {},
        )

    def execution_authorized():
        control = reasoning_engine_registry.control()
        deployment = reasoning_engine_registry.get(v1_deployment_id)
        return bool(
            str(control.active_deployment_id or "") == v1_deployment_id
            and str(control.delivery_deployment_id or "") == v1_deployment_id
            and str(deployment.get("status") or "") == "active"
        )

    return OntologyReasoningRunner(
        event_reader=event_log,
        cursor_store=cursor_store,
        monitor_runner_factory=reasoning_monitor_runner,
        event_publisher=event_publisher or ontology_reasoning_event_bus(reasoning_store_settings),
        settings=configured_settings,
        rule_candidate_service=RuleChangeCandidateProposalService(
            ontology_repository=ontology_repository,
            advisor=rule_change_candidate_advisor_from_settings(configured_settings),
            event_reader=event_log,
            settings=reasoning_store_settings,
            strategy_proposal_service=build_investment_strategy_proposal_service(reasoning_store_settings, event_publisher=event_publisher),
        ),
        research_store=stores.investment_research_store(reasoning_store_settings),
        priority_symbols_provider=lambda: ontology_reasoning_priority_symbols(registry, reasoning_store_settings),
        projection_recovery_probe=projection_recovery_probe,
        maintenance_runner=reasoning_worker_maintenance,
        storage_guard=storage_guard,
        mailbox_store=stores.ontology_reasoning_mailbox_store(reasoning_store_settings),
        queue_health_service=OntologyReasoningQueueHealthService(
            store=cursor_store,
            settings=configured_settings,
        ),
        projection_coordinator_probe=(
            getattr(ontology_repository, "projection_coordinator_lease_status")
            if callable(getattr(ontology_repository, "projection_coordinator_lease_status", None))
            else None
        ),
        projection_lease_recovery=projection_lease_recovery,
        projection_coordinator_lease_recovery=projection_coordinator_lease_recovery,
        snapshot_readiness_probe=source_snapshot_preflight,
        operational_settings_refresher=refresh_reasoning_monitor_settings,
        maintenance_yield_state_probe=(
            getattr(maintenance_state_store, "load")
            if callable(getattr(maintenance_state_store, "load", None))
            else None
        ),
        market_observation_completion_recorder=(
            stores.market_observation_reasoning_anchor_store(reasoning_store_settings).complete
        ),
        reasoning_shadow_scheduler=reasoning_shadow_scheduler,
        execution_authorized_provider=execution_authorized,
        shared_inference_publisher=publish_shared_inference,
    )
