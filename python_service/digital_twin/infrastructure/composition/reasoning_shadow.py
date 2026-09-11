"""Reasoning Shadow runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.reasoning.public import ReasoningEngineShadowRunner


class FrozenReasoningSnapshotSource:
    """Rehydrate only the immutable observation packet owned by one V2 job."""

    def __init__(self, states):
        self.states = {
            str(account_id or ""): dict(state)
            for account_id, state in dict(states or {}).items()
            if str(account_id or "") and isinstance(state, dict)
        }

    def __call__(self, account, reasoning_context=None):
        from digital_twin.modules.portfolio.domain.portfolio import account_snapshot_from_monitor_state

        del reasoning_context
        account_id = str(getattr(account, "account_id", "") or "")
        snapshot = account_snapshot_from_monitor_state(self.states.get(account_id) or {})
        if snapshot is None:
            raise RuntimeError("Immutable V2 source snapshot is unavailable for account " + account_id)
        metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
        replay = dict(metadata.get("reasoningSnapshotReplay") or {})
        replay.update({
            "status": "ready",
            "mode": "immutable-shadow-input",
            "immutableInput": True,
            "snapshotGeneratedAt": str(snapshot.generated_at or ""),
        })
        metadata["reasoningSnapshotReplay"] = replay
        snapshot.metadata = metadata
        return snapshot


class ShadowWorldProjectionSink:
    """Prevent candidate PortfolioWorld inference from updating shared worlds."""

    def enqueue(self, projection_kind, shared_world, projection_input, **kwargs):
        del projection_input, kwargs
        return {
            "status": "shadow-isolated",
            "saved": False,
            "projectionKind": str(projection_kind or ""),
            "worldId": str(getattr(shared_world, "world_id", "") or ""),
            "preservedActiveGeneration": True,
            "reason": "V2 shadow execution cannot publish shared-world projections.",
        }


class ActiveDeploymentWorldProjectionSink:
    """Allow only the active delivery deployment to advance shared worlds."""

    def __init__(self, outbox, registry, deployment_id: str):
        self.outbox = outbox
        self.registry = registry
        self.deployment_id = str(deployment_id or "").strip()

    def enqueue(self, projection_kind, shared_world, projection_input, **kwargs):
        try:
            control = self.registry.control()
            deployment = self.registry.get(self.deployment_id)
        except Exception as error:  # Shared projection must not invalidate private inference.
            return {
                "status": "deployment-authorization-unavailable",
                "saved": False,
                "projectionKind": str(projection_kind or ""),
                "worldId": str(getattr(shared_world, "world_id", "") or ""),
                "preservedActiveGeneration": True,
                "deploymentId": self.deployment_id,
                "reason": str(error)[:180],
            }
        authorized = bool(
            str(control.active_deployment_id or "") == self.deployment_id
            and str(control.delivery_deployment_id or "") == self.deployment_id
            and str(deployment.get("status") or "") == "active"
        )
        if not authorized:
            return {
                "status": "inactive-deployment-isolated",
                "saved": False,
                "projectionKind": str(projection_kind or ""),
                "worldId": str(getattr(shared_world, "world_id", "") or ""),
                "preservedActiveGeneration": True,
                "deploymentId": self.deployment_id,
                "reason": "Only the active delivery deployment may advance shared worlds.",
            }
        return self.outbox.enqueue(
            projection_kind,
            shared_world,
            projection_input,
            **kwargs,
        )


class ShadowNotificationSink:
    """Block transport while retaining an auditable delivery-attempt count."""

    def __init__(self):
        self.attempt_count = 0
        self.dry_run_count = 0

    def __call__(self, *args, **kwargs):
        from types import SimpleNamespace

        del args
        if bool(kwargs.get("dry_run")):
            self.dry_run_count += 1
        else:
            self.attempt_count += 1
        return SimpleNamespace(
            delivered=False,
            label="shadow-blocked",
            reason="V2 shadow has no delivery transport.",
        )


class V2InferenceDetailReceiptSink:
    """Enable commit-proof fast path without coupling V2 to V1's DB worker."""

    def enqueue(self, **kwargs):
        return {
            "status": "v2-on-demand-detail",
            "saved": False,
            "eventuallyConsistent": False,
            "inferenceGenerationId": str(kwargs.get("inference_generation_id") or ""),
            "sourceAboxSnapshotId": str(kwargs.get("source_abox_snapshot_id") or ""),
            "reason": "V2 keeps native rows in its own TypeDB and reads full detail only on demand.",
        }


def build_reasoning_engine_shadow_runner(settings=None, worker_id: str = "") -> ReasoningEngineShadowRunner:
    """Compose the isolated V2 TypeDB + QuestDB candidate worker."""
    from digital_twin.modules.market_data.domain.monitoring import RealtimeMonitor
    from digital_twin.modules.reasoning.domain.portfolio_ontology_temporal_concepts import parse_temporal_windows
    from digital_twin.modules.reasoning.domain.reasoning_engine_versions import reasoning_release_identity
    from digital_twin.modules.reasoning.domain.reasoning_shadow import payload_hash, unpack_projection_runtime_contexts
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.reasoning_health import build_ontology_reasoning_queue_probe
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
    from digital_twin.infrastructure.questdb_time_series import QuestDBTimeSeriesAdapter
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.infrastructure.time_series_factory import build_temporal_feature_snapshot_service
    from digital_twin.modules.market_data.public import MonitorRunner
    from digital_twin.modules.reasoning.public import ReasoningEngineShadowRunner

    configured = dict(settings or runtime_settings())
    store_settings = dict(configured)
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"
    registry_store = stores.reasoning_engine_registry_store(store_settings)
    candidate_base_settings = dict(configured)
    candidate_base_settings["typedbDatabase"] = str(
        configured.get("reasoningEngineV2TypeDbDatabase")
        or configured.get("reasoningEngineShadowTypeDbDatabase")
        or "orbit_alpha_ontology_shadow_v2"
    )
    candidate_base_settings["timeSeriesActiveBackendId"] = str(
        configured.get("timeSeriesShadowBackendId") or "questdb-shadow"
    )
    candidate_base_settings["ontologySharedMarketWorldAsyncProjectionEnabled"] = "1"
    candidate_base_settings["ontologyInferenceDetailOutboxEnabled"] = "0"
    candidate_base_settings["ontologyAsyncQualityRecordEnabled"] = "0"
    active_repository = ontology_repository_from_settings(configured)
    candidate_repository = ontology_repository_from_settings(candidate_base_settings)

    def synchronize_candidate_release(candidate_deployment_id):
        active_rulebox = dict(active_repository.rulebox_snapshot() or {})
        if str(active_rulebox.get("status") or "") != "ok" or not active_rulebox.get("rules"):
            raise RuntimeError("Active TypeDB RuleBox cannot be read for V2 release synchronization")
        try:
            candidate_rulebox = dict(candidate_repository.rulebox_snapshot() or {})
        except Exception:  # noqa: BLE001 - an empty candidate database has no base TBox yet.
            candidate_rulebox = {"status": "schema-unavailable", "rules": []}
        if str(candidate_rulebox.get("status") or "") != "ok":
            seed_result = dict(candidate_repository.seed_ontology({
                "rules": list(active_rulebox.get("rules") or []),
                "replaceRuleBox": True,
                "changeReason": "Bootstrap isolated V2 shadow release",
                "author": "reasoning-shadow-worker",
            }) or {})
            if not seed_result.get("saved"):
                raise RuntimeError(
                    "V2 TBox/RuleBox bootstrap failed: "
                    + str(seed_result.get("reason") or seed_result.get("status") or "unknown")
                )
            candidate_rulebox = dict(candidate_repository.rulebox_snapshot() or {})
            if str(candidate_rulebox.get("status") or "") != "ok":
                raise RuntimeError("V2 RuleBox cannot be read after candidate TBox bootstrap")
        active_hash = str(
            active_rulebox.get("sourceRulesHash")
            or active_rulebox.get("rulesHash")
            or active_rulebox.get("ruleboxRulesHash")
            or payload_hash(active_rulebox.get("rules") or [])
        )
        candidate_hash = str(
            candidate_rulebox.get("sourceRulesHash")
            or candidate_rulebox.get("rulesHash")
            or candidate_rulebox.get("ruleboxRulesHash")
            or payload_hash(candidate_rulebox.get("rules") or [])
        )
        candidate_deployment = registry_store.get(candidate_deployment_id)
        candidate_health = dict(candidate_deployment.get("health") or {})
        frozen_hash = str(candidate_health.get("ruleboxFingerprint") or "")
        if not frozen_hash and not str(candidate_health.get("candidateReleaseId") or ""):
            # Compatibility with the pre-v2 health contract, where the field
            # named releaseFingerprint contained only the RuleBox hash.
            frozen_hash = str(candidate_health.get("releaseFingerprint") or "")
        if frozen_hash and active_hash != frozen_hash:
            raise RuntimeError(
                "The active RuleBox changed after the V2 release was frozen; "
                "register a new candidate deployment before comparing it."
            )
        if frozen_hash and candidate_hash != frozen_hash:
            raise RuntimeError(
                "The V2 TypeDB RuleBox no longer matches its frozen release fingerprint."
            )
        if active_hash and active_hash == candidate_hash:
            return {"status": "unchanged", "rulesHash": active_hash}
        result = dict(candidate_repository.save_rulebox({
            "rules": list(active_rulebox.get("rules") or []),
            "changeReason": "Synchronize immutable V2 shadow release from active V1",
            "author": "reasoning-shadow-worker",
            "status": "shadow-release-sync",
        }) or {})
        if not result.get("saved"):
            raise RuntimeError(
                "V2 RuleBox synchronization failed: " + str(result.get("reason") or result.get("status") or "unknown")
            )
        return {"status": "synchronized", "rulesHash": active_hash}

    def candidate_monitor_runner(payload):
        candidate_deployment_id = str(
            payload.get("candidateDeploymentId") or "ontology-v2-shadow"
        )
        release_sync = synchronize_candidate_release(candidate_deployment_id)
        release_identity = reasoning_release_identity(
            registry_store.get(candidate_deployment_id),
            release_sync.get("rulesHash") or "",
        )
        expected_release_identity = dict(payload.get("candidateReleaseIdentity") or {})
        if expected_release_identity and str(
            expected_release_identity.get("releaseFingerprint") or ""
        ) != str(release_identity.get("releaseFingerprint") or ""):
            raise RuntimeError("Candidate release identity changed after the shadow input was captured")
        # Storage bindings are isolated, but the investment-world input must
        # be identical to V1. Passing the candidate DB/backend controls into
        # ``PortfolioOntologyProjectionRecorder.settings`` would turn
        # infrastructure differences into different ABox facts and make a
        # shadow comparison invalid by construction.
        candidate_storage_settings = dict(candidate_base_settings)
        reasoning_input_settings = dict(configured)
        account_ids = {
            str(value or "")
            for value in payload.get("accountIds") or []
            if str(value or "")
        }
        context_packet = payload.get("projectionRuntimeContextPacket")
        if not isinstance(context_packet, dict):
            raise RuntimeError(
                "V2 shadow job is missing the immutable V1 projection runtime context packet"
            )
        runtime_context_overrides = unpack_projection_runtime_contexts(context_packet)
        if account_ids - set(runtime_context_overrides):
            raise RuntimeError(
                "V2 shadow job is missing the immutable V1 projection runtime context"
            )
        projection_symbol_filters = {
            str(account_id or ""): list(symbols or [])
            for account_id, symbols in dict(
                payload.get("projectionTargetSymbolsByAccount") or {}
            ).items()
            if str(account_id or "")
        }
        if account_ids - set(projection_symbol_filters):
            raise RuntimeError(
                "V2 shadow job is missing the immutable V1 projection target set"
            )
        accounts = [
            account
            for account in stores.account_reader(store_settings).load() or []
            if not account_ids or str(getattr(account, "account_id", "") or "") in account_ids
        ]
        if account_ids and len(accounts) != len(account_ids):
            raise RuntimeError("V2 shadow account registry no longer matches the immutable input")
        monitor_store = stores.ontology_reasoning_monitor_store(store_settings)
        questdb_store = QuestDBTimeSeriesAdapter(
            candidate_storage_settings,
            str(configured.get("timeSeriesShadowBackendId") or "questdb-shadow"),
        )
        projection_recorder = PortfolioOntologyProjectionRecorder(
            candidate_repository,
            decision_episode_store=stores.investment_decision_episode_store(store_settings),
            hypothesis_proposal_store=stores.investment_research_store(store_settings),
            hypothesis_lifecycle_store=stores.hypothesis_lifecycle_store(store_settings),
            market_time_series_store=questdb_store,
            investment_domain_store=stores.investment_domain_store(store_settings),
            world_projection_outbox=ShadowWorldProjectionSink(),
            runtime_context_overrides=runtime_context_overrides,
            settings=reasoning_input_settings,
            source="reasoning-engine-v2-shadow",
        )
        notification_sink = ShadowNotificationSink()
        runner = MonitorRunner(
            accounts,
            store=monitor_store,
            monitor=RealtimeMonitor(reasoning_input_settings),
            snapshot_builder=FrozenReasoningSnapshotSource(payload.get("sourceStates") or {}),
            event_sender=notification_sink,
            event_publisher=None,
            cycle_recorder=None,
            ontology_projection_recorder=projection_recorder,
            hypothesis_lifecycle_service=None,
            ontology_projection_enabled=True,
            account_job_store=None,
            source_snapshot_replay=True,
            projection_symbol_filters_by_account=projection_symbol_filters,
            portfolio_lifecycle_observer=None,
        )
        runner.shadow_delivery_count = 0
        runner.shadow_notification_sink = notification_sink
        runner.shadow_rulebox_fingerprint = str(release_sync.get("rulesHash") or "")
        runner.shadow_release_identity = release_identity
        runner.shadow_release_fingerprint = str(release_identity.get("releaseFingerprint") or "")
        return runner

    return ReasoningEngineShadowRunner(
        queue=stores.reasoning_shadow_job_store(store_settings),
        comparison_store=stores.reasoning_engine_comparison_store(store_settings),
        registry=registry_store,
        candidate_runner_factory=candidate_monitor_runner,
        temporal_snapshot_service=build_temporal_feature_snapshot_service(configured),
        temporal_definitions=parse_temporal_windows(configured.get("temporalWindowPeriods")),
        settings=configured,
        active_queue_probe=build_ontology_reasoning_queue_probe(configured),
        worker_id=worker_id,
    )
