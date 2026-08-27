import json
import os
import time
import uuid
from types import SimpleNamespace
from typing import Callable, Dict, Iterable

from ..application.flow_lens_service import FlowLensService
from ..application.ai_inference_queue_service import (
    AIInferenceQueueRunner,
    NotificationAIRequestEnqueuer,
)
from ..application.data_pipeline_health_service import DataPipelineHealthNotificationEnqueuer, DataPipelineHealthService
from ..application.decision_continuity_service import DecisionContinuityService
from ..application.decision_episode_reconciliation_service import DecisionEpisodeReconciliationService
from ..application.ontology_reasoning_queue_health_service import (
    OntologyReasoningQueueHealthNotificationEnqueuer,
    OntologyReasoningQueueHealthService,
)
from ..application.operational_storage_capacity_service import (
    OperationalStorageCapacityNotificationEnqueuer,
    OperationalStorageCapacityService,
)
from ..application.investment_analysis_service import InvestmentAnalysisService
from ..application.independent_reasoning_engine import (
    IndependentReasoningInputAssembler,
    IndependentReasoningJobRunner,
    ScopedTypeDBInferenceExecutor,
    V2ReasoningEngine,
)
from ..application.investment_reasoning import (
    InvestmentReasoningOrchestrator,
    V2GraphDecisionCandidateBuilder,
)
from ..application.shared_instrument_inference_service import SharedInstrumentInferenceService
from ..application.investment_brain_service import InvestmentBrainService
from ..application.investment_domain_service import InvestmentDomainService
from ..application.investment_research_orchestration_service import InvestmentResearchOrchestrationService, InvestmentResearchQueueRunner
from ..application.hypothesis_proposal_service import (
    HypothesisProposalQueueRunner,
    HypothesisProposalService,
)
from ..application.hypothesis_lifecycle_service import HypothesisLifecycleService
from ..application.hypothesis_lifecycle_policy_service import HypothesisLifecyclePolicyService
from ..application.hypothesis_policy_governance_service import HypothesisPolicyGovernanceService
from ..application.hypothesis_research_planner_service import HypothesisResearchPlanningService
from ..application.hypothesis_review_service import HypothesisReviewService
from ..application.hypothesis_quality_review_service import HypothesisQualityReviewService
from ..application.hypothesis_outcome_replay_service import HypothesisOutcomeReplayService
from ..application.historical_decision_replay_service import HistoricalDecisionReplayService
from ..application.hypothesis_development_service import HypothesisDevelopmentService
from ..application.investment_strategy_proposal_service import InvestmentStrategyProposalService
from ..application.investment_calendar_candidate_service import InvestmentCalendarCandidateService
from ..application.investment_calendar_discovery_service import InvestmentCalendarDiscoveryService
from ..application.investment_calendar_extraction_service import InvestmentCalendarExtractionService
from ..application.investment_calendar_research_service import InvestmentCalendarResearchRecommendationService
from ..application.investment_calendar_service import InvestmentCalendarRunner, InvestmentCalendarService
from ..application.instrument_timeline_query_service import InstrumentTimelineQueryService
from ..application.kis_realtime_service import KISRealtimeWebSocketRunner
from ..application.market_data_collection_service import MarketDataCollectionRunner
from ..application.external_data.collection_service import ExternalDataCollectionService
from ..application.external_data.research_evidence_projection_service import (
    ExternalFactResearchEvidenceReconciler,
    ExternalOfficialEvidenceProjectionService,
)
from ..application.model_review_service import ModelReviewRunner
from ..application.news_collection_service import NewsCollectionRunner
from ..application.news_ai_analysis_service import NewsAiAnalysisService
from ..application.news_analysis_enrichment_service import NewsAnalysisEnrichmentRunner
from ..application.news_digest_service import NewsDigestEnqueuer, NewsDigestEventReconciler
from ..application.notification_ai_decision_context import NotificationAIDecisionContextEnricher
from ..application.monitoring_service import MonitorRunner
from ..application.portfolio_lifecycle_service import (
    DecisionActionPlanningService,
    PortfolioAccountingService,
    TradeExecutionService,
)
from ..application.notification.workflow import (
    CompositeNotificationContextEnricher,
    DisclosureAnalysisNotificationEnricher,
    NotificationAIValidatedGateEnricher,
    NotificationAIOpinionEnricher,
    NotificationHoldingSnapshotEnricher,
    NotificationHypothesisResearchEnricher,
    NotificationInstrumentIdentityEnricher,
    NotificationQueueRunner,
)
from ..application.official_calendar_sync_service import OfficialCalendarSyncService
from ..application.ontology_reasoning_service import (
    OntologyReasoningRunner,
    lightweight_ontology_reasoning_queue_state,
)
from ..application.reasoning_shadow_service import (
    ReasoningEngineShadowRunner,
    ReasoningShadowScheduler,
)
from ..application.ontology_reasoning_proof_service import OntologyReasoningProofService
from ..application.ontology_maintenance_service import OntologyMaintenanceRunner
from ..application.ontology_inference_detail_service import OntologyInferenceDetailRunner
from ..application.ontology_world_projection_service import OntologyWorldProjectionRunner
from ..application.ontology_portfolio_rebuild_service import (
    OntologyPortfolioRebuildRunner,
    OntologyPortfolioScopeRepairRunner,
    OntologyScopeRepairRouter,
)
from ..application.ontology_lab_service import OntologyLabService
from ..application.ontology_rule_candidate_service import RuleChangeCandidateProposalService
from ..application.symbol_universe_service import SymbolUniverseService
from ..domain.accounts import AccountConfig
from ..domain.events import (
    DATA_PIPELINE_HEALTH_CHANGED,
    ONTOLOGY_REASONING_QUEUE_HEALTH_CHANGED,
    OPERATIONAL_STORAGE_CAPACITY_CHANGED,
    RESEARCH_EVIDENCE_COLLECTED,
)
from ..domain.market_data import number
from ..domain.monitoring import RealtimeMonitor
from ..domain.portfolio import account_snapshot_from_monitor_state
from ..domain.portfolio_ontology_temporal_concepts import parse_temporal_windows
from ..domain.reasoning_shadow import payload_hash, unpack_projection_runtime_contexts
from ..domain.ontology_rulebox_governance import rulebox_rules_hash
from ..domain.reasoning_engine_versions import reasoning_release_identity
from ..domain.investment_reasoning import reasoning_rule_inventory
from ..domain.ontology_worlds import portfolio_world_id
from .event_bus import EventBus, default_event_bus
from .share_notification_links import ActiveShareNotificationLinkResolver
from .bok_calendar_source import BokPolicyDecisionCalendarSource
from .opendart_calendar_source import OpenDartEarningsCalendarSource
from .samsung_ir_calendar_source import SamsungIrEarningsCalendarSource
from .disclosure_analyzer import disclosure_analyzer_from_settings
from .model_review_queue import ModelReviewEnqueuer
from .model_reviewer import reviewer_from_settings
from .notification_ai_reviewer import notification_ai_reviewer_from_settings
from .hypothesis_proposal_ai import hypothesis_proposal_advisor_from_settings
from .hypothesis_research_planner_ai import hypothesis_research_planning_advisor_from_settings
from .investment_research_gateway import CompositeInvestmentResearchGateway, ExistingApiResearchGateway
from .ontology_graph_store import ontology_repository_from_settings
from . import operational_store as stores
from .ontology_projection import PortfolioOntologyProjectionRecorder
from .typedb_storage_guard import TypeDBCapacityGuard
from .operational_storage_guard import operational_storage_inventory
from .kis_realtime_ws import KISRealtimeSymbolSelector, KISRealtimeWebSocketClient
from .rule_change_candidate_ai import rule_change_candidate_advisor_from_settings
from .notification.ingress import queued_notifier_for_account, send_events
from .notification.transport import notifier_for_account, notifier_for_operations
from .news_sources import NewsSourceGateway
from .news_ai_analyzer import news_ai_analyzer_from_settings
from .external_signals import ExternalSignalProvider
from .external_api.adapters import default_external_dataset_registry
from .external_api.legacy_import import LegacyExternalSignalImporter
from .settings import currency_rates, data_dir, runtime_settings, utc_now
from .graph_writer_guard import LocalGraphWriterGuard
from .symbol_sources import RemoteSymbolSourceGateway
from .toss_snapshots import TossProvider, build_snapshot, demo_positions
from .reasoning_snapshot_source import LatestMonitorSnapshotReasoningSource
from .questdb_time_series import QuestDBTimeSeriesAdapter
from .time_series_factory import build_temporal_feature_snapshot_service, build_time_series_adapters
from .statistical_signal_factory import build_statistical_signal_pipeline_service
from ..domain.ontology_schema import default_tbox_metadata
from ..domain.statistical_signals import (
    DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID,
    DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID,
    DEFAULT_EVENT_SIGNAL_RELEASE_ID,
    DEFAULT_FLOW_SIGNAL_RELEASE_ID,
    DEFAULT_PRICE_SIGNAL_RELEASE_ID,
    DEFAULT_VALUATION_SIGNAL_RELEASE_ID,
)


DISABLED_SETTING_VALUES = {"0", "false", "no", "off", "disabled"}


def ontology_graph_store_epoch(settings=None) -> str:
    """Return the durable identity of the currently mounted TypeDB store."""

    configured = dict(settings or {})
    explicit = str(configured.get("ontologyGraphStoreEpoch") or "").strip()
    if explicit:
        return explicit
    marker = {}
    try:
        marker = json.loads(
            (data_dir() / "typedb-retention.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, json.JSONDecodeError):
        marker = {}
    store_epoch = str(marker.get("graphStoreEpoch") or "").strip()
    if not store_epoch:
        timestamps = [
            str(marker.get(key) or "").strip()
            for key in [
                "blueGreenCutoverActivatedAt",
                "blueGreenCutoverAt",
                "blueGreenRollbackAt",
            ]
            if str(marker.get(key) or "").strip()
        ]
        store_epoch = max(timestamps, default="initial-store")
    return "|".join([
        str(configured.get("typedbAddress") or "127.0.0.1:1729").strip(),
        str(configured.get("typedbDatabase") or "orbit_alpha_ontology").strip(),
        store_epoch,
    ])


def ontology_graph_single_writer_enabled(settings=None) -> bool:
    value = str(
        dict(settings or {}).get("ontologyGraphSingleWriterEnabled", "1")
        or ""
    ).strip().lower()
    return value not in DISABLED_SETTING_VALUES


def setting_truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_SETTING_VALUES


def typedb_capacity_guard(settings, role: str, state_store=None) -> TypeDBCapacityGuard:
    """Build a role-aware guard that reuses the maintenance worker sample."""
    reader = getattr(state_store, "load", None)
    return TypeDBCapacityGuard(
        settings,
        role=role,
        capacity_state_loader=reader if callable(reader) else None,
    )


def v2_model_signal_release_contract(rulebox_snapshot, settings=None):
    """Resolve model releases required by enabled rules and runtime scorers."""

    required = set()
    for rule in (rulebox_snapshot or {}).get("rules") or []:
        if not bool(rule.get("enabled", True)):
            continue
        for condition in rule.get("conditions") or []:
            relation_type = str(
                condition.get("relation_type")
                or condition.get("relationType")
                or ""
            ).strip()
            if relation_type != "HAS_MODEL_SIGNAL":
                continue
            filters = (
                condition.get("target_property_filters")
                or condition.get("targetPropertyFilters")
                or {}
            )
            release_id = str(
                filters.get("releaseId") or filters.get("release_id") or ""
            ).strip()
            if release_id:
                required.add(release_id)
    configured = dict(settings or {})
    available = {
        str(
            configured.get("statisticalPriceSignalReleaseId")
            or DEFAULT_PRICE_SIGNAL_RELEASE_ID
        ).strip(),
        str(
            configured.get("statisticalFlowSignalReleaseId")
            or DEFAULT_FLOW_SIGNAL_RELEASE_ID
        ).strip(),
        DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID,
        DEFAULT_VALUATION_SIGNAL_RELEASE_ID,
        DEFAULT_EVENT_SIGNAL_RELEASE_ID,
        DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID,
    }
    available.discard("")
    missing = sorted(required - available)
    return {
        "status": "matched" if not missing else "mismatch",
        "requiredReleaseIds": sorted(required),
        "availableReleaseIds": sorted(available),
        "missingReleaseIds": missing,
    }


def prepare_v2_rulebox_release(repository, settings=None, release_guard=None):
    """Read or migrate one V2 ontology release before freezing it in memory.

    A provisioning deployment may repair its isolated RuleBox before its first
    release fingerprint is recorded. An active, delivery-authorized, or
    already-frozen candidate is immutable: startup verifies its persisted
    fingerprints and must never rewrite that graph store in place.
    """

    guard = dict(release_guard or {})
    immutable_release = bool(guard.get("immutable"))
    expected_rulebox_fingerprint = str(
        guard.get("ruleboxFingerprint")
        or guard.get("rulebox_fingerprint")
        or ""
    ).strip()
    expected_tbox_fingerprint = str(
        guard.get("tboxFingerprint")
        or guard.get("tbox_fingerprint")
        or ""
    ).strip()
    expected_tbox_version = str(
        guard.get("tboxVersion")
        or guard.get("tbox_version")
        or ""
    ).strip()

    if immutable_release:
        try:
            snapshot = dict(repository.rulebox_snapshot() or {})
        except Exception as error:
            raise RuntimeError(
                "The immutable V2 RuleBox release is unavailable: "
                + str(error)[:220]
            ) from error
        if str(snapshot.get("status") or "") != "ok" or not snapshot.get("rules"):
            raise RuntimeError("The immutable V2 RuleBox release is unavailable or empty")
        actual_rulebox_fingerprint = str(
            snapshot.get("sourceRulesHash")
            or snapshot.get("rulesHash")
            or snapshot.get("ruleboxRulesHash")
            or rulebox_rules_hash(snapshot.get("rules") or [])
        ).strip()
        if (
            not expected_rulebox_fingerprint
            or actual_rulebox_fingerprint != expected_rulebox_fingerprint
        ):
            raise RuntimeError(
                "The immutable V2 RuleBox release fingerprint does not match its "
                "deployment record: "
                + (actual_rulebox_fingerprint or "unknown")
                + " != "
                + (expected_rulebox_fingerprint or "missing")
                + ". Register a new V2 deployment or restore the frozen release."
            )
        try:
            deployed_tbox = dict(repository.active_tbox_metadata() or {})
        except Exception as error:
            raise RuntimeError(
                "The immutable V2 TBox release cannot be verified: "
                + str(error)[:220]
            ) from error
        actual_tbox_fingerprint = str(deployed_tbox.get("fingerprint") or "").strip()
        actual_tbox_version = str(deployed_tbox.get("version") or "").strip()
        if (
            str(deployed_tbox.get("status") or "") != "ok"
            or not expected_tbox_fingerprint
            or actual_tbox_fingerprint != expected_tbox_fingerprint
            or (expected_tbox_version and actual_tbox_version != expected_tbox_version)
        ):
            raise RuntimeError(
                "The immutable V2 TBox release fingerprint does not match its "
                "deployment record: "
                + (actual_tbox_version or "unknown")
                + "/"
                + (actual_tbox_fingerprint or "unknown")
                + " != "
                + (expected_tbox_version or actual_tbox_version or "unknown")
                + "/"
                + (expected_tbox_fingerprint or "missing")
                + ". Register a new V2 deployment or restore the frozen release."
            )
        model_signal_contract = v2_model_signal_release_contract(snapshot, settings)
        if model_signal_contract["status"] != "matched":
            raise RuntimeError(
                "The immutable V2 RuleBox requires model-signal releases that the "
                "runtime scorer does not produce: "
                + ", ".join(model_signal_contract["missingReleaseIds"])
            )
        snapshot["frozenReleaseVerified"] = True
        return snapshot, {
            "status": "ready",
            "ruleCount": int(snapshot.get("ruleCount") or len(snapshot.get("rules") or [])),
            "ruleboxRulesHash": actual_rulebox_fingerprint,
            "sourceOfTruth": "typedb-immutable-v2-release",
            "ruleCatalogStore": "typedb",
            "modelSignalReleasePreflight": model_signal_contract,
            "tboxReleasePreflight": {
                "status": "matched",
                "version": actual_tbox_version,
                "fingerprint": actual_tbox_fingerprint,
                "source": str(deployed_tbox.get("source") or ""),
            },
            "ruleCatalogMigration": {
                "status": "immutable-release-reused",
                "required": False,
                "saved": False,
            },
        }

    schema_release_preflight = {}
    synchronize_schema = getattr(repository, "sync_base_schema_contract", None)
    if callable(synchronize_schema):
        try:
            schema_release_preflight = dict(synchronize_schema() or {})
        except Exception as error:
            raise RuntimeError(
                "The independent V2 TypeDB storage schema preflight failed: "
                + str(error)[:220]
            ) from error
        if not bool(schema_release_preflight.get("saved")):
            raise RuntimeError(
                "The independent V2 TypeDB storage schema preflight failed: "
                + str(
                    schema_release_preflight.get("reason")
                    or schema_release_preflight.get("status")
                    or "unknown"
                )[:220]
            )

    projection_recorder = PortfolioOntologyProjectionRecorder(
        repository,
        settings=dict(settings or {}),
        source="reasoning-engine-v2-release-preflight",
    )
    readiness = projection_recorder.ensure_rulebox_ready()
    readiness_reason = str(readiness.get("reason") or "")
    missing_base_schema = (
        str(readiness.get("status") or "") == "error"
        and "type label 'ontology-node' not found" in readiness_reason.lower()
    )
    if missing_base_schema and hasattr(repository, "seed_ontology"):
        try:
            seed_result = dict(repository.seed_ontology({
                "replaceRuleBox": True,
                "clearInference": False,
                "changeReason": "Bootstrap isolated V2 reasoning release",
                "author": "reasoning-engine-v2-release-preflight",
            }) or {})
        except Exception as error:
            raise RuntimeError(
                "The independent V2 TypeDB release bootstrap failed: "
                + str(error)[:220]
            ) from error
        if not bool(seed_result.get("saved") or seed_result.get("seeded")):
            raise RuntimeError(
                "The independent V2 TypeDB release bootstrap failed: "
                + str(
                    seed_result.get("reason")
                    or seed_result.get("status")
                    or "unknown"
                )[:220]
            )
        readiness = projection_recorder.ensure_rulebox_ready()
        readiness = {
            **dict(readiness or {}),
            "releaseBootstrap": {
                "status": str(seed_result.get("status") or "seeded"),
                "saved": True,
                "seeded": bool(seed_result.get("seeded", True)),
            },
        }
    if str(readiness.get("status") or "") not in {"ready", "seeded"}:
        raise RuntimeError(
            "The independent V2 RuleBox release preflight failed: "
            + str(readiness.get("reason") or readiness.get("status") or "unknown")
        )
    try:
        snapshot = dict(repository.rulebox_snapshot() or {})
    except Exception as error:
        raise RuntimeError(
            "The independent V2 RuleBox release is unavailable after preflight: "
            + str(error)[:220]
        ) from error
    if str(snapshot.get("status") or "") != "ok" or not snapshot.get("rules"):
        raise RuntimeError("The independent V2 RuleBox release is unavailable or empty")
    model_signal_contract = v2_model_signal_release_contract(snapshot, settings)
    if model_signal_contract["status"] != "matched":
        raise RuntimeError(
            "The independent V2 RuleBox requires model-signal releases that the "
            "runtime scorer does not produce: "
            + ", ".join(model_signal_contract["missingReleaseIds"])
        )
    expected_tbox = default_tbox_metadata()
    try:
        deployed_tbox = dict(repository.active_tbox_metadata() or {})
    except Exception as error:
        raise RuntimeError(
            "The independent V2 TBox release cannot be verified: " + str(error)[:220]
        ) from error
    if (
        str(deployed_tbox.get("status") or "") != "ok"
        or str(deployed_tbox.get("version") or "") != str(expected_tbox.get("version") or "")
        or str(deployed_tbox.get("fingerprint") or "") != str(expected_tbox.get("fingerprint") or "")
    ):
        raise RuntimeError(
            "The independent V2 TypeDB TBox does not match the source release: "
            + str(deployed_tbox.get("version") or "unknown")
            + "/"
            + str(deployed_tbox.get("fingerprint") or "unknown")
            + " != "
            + str(expected_tbox.get("version") or "unknown")
            + "/"
            + str(expected_tbox.get("fingerprint") or "unknown")
        )
    readiness = {
        **dict(readiness or {}),
        "storageSchemaPreflight": schema_release_preflight,
        "modelSignalReleasePreflight": model_signal_contract,
        "tboxReleasePreflight": {
            "status": "matched",
            "version": str(expected_tbox.get("version") or ""),
            "fingerprint": str(expected_tbox.get("fingerprint") or ""),
            "source": str(deployed_tbox.get("source") or ""),
        },
    }
    return snapshot, readiness


def monitor_event_bus(settings=None) -> EventBus:
    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe_all(ModelReviewEnqueuer(stores.model_review_job_store(configured_settings)).handle)
    return bus


def news_event_bus(settings=None) -> EventBus:
    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe(
        RESEARCH_EVIDENCE_COLLECTED,
        NewsDigestEnqueuer(
            account_repository=stores.account_registry(configured_settings),
            monitor_store=stores.monitor_store(configured_settings),
            queue=stores.notification_job_store(configured_settings),
            settings=configured_settings,
            max_items=int(number(configured_settings.get("newsDigestMaxItems")) or 3),
            evidence_repository=stores.research_evidence_store(configured_settings),
        ).handle,
    )
    bus.subscribe(
        DATA_PIPELINE_HEALTH_CHANGED,
        DataPipelineHealthNotificationEnqueuer(
            account_repository=stores.account_registry(configured_settings),
            queue=stores.notification_job_store(configured_settings),
            settings=configured_settings,
        ).handle,
    )
    calendar_service = build_investment_calendar_service(configured_settings)
    bus.subscribe(
        RESEARCH_EVIDENCE_COLLECTED,
        InvestmentCalendarExtractionService(
            calendar_service=calendar_service,
            account_repository=stores.account_registry(configured_settings),
            candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
            settings=configured_settings,
        ).handle,
    )
    return bus


def data_pipeline_health_event_bus(settings=None) -> EventBus:
    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe(
        DATA_PIPELINE_HEALTH_CHANGED,
        DataPipelineHealthNotificationEnqueuer(
            account_repository=stores.account_registry(configured_settings),
            queue=stores.notification_job_store(configured_settings),
            settings=configured_settings,
        ).handle,
    )
    return bus


def operational_storage_event_bus(settings=None) -> EventBus:
    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe(
        OPERATIONAL_STORAGE_CAPACITY_CHANGED,
        OperationalStorageCapacityNotificationEnqueuer(
            queue=stores.notification_job_store(configured_settings),
            fallback_notifier_factory=notifier_for_operations,
        ).handle,
    )
    return bus


def build_operational_storage_capacity_service(settings=None) -> OperationalStorageCapacityService:
    configured_settings = settings or runtime_settings()
    return OperationalStorageCapacityService(
        store=stores.operational_storage_capacity_state_store(configured_settings),
        settings=configured_settings,
    )


def observe_operational_storage_capacity(
    settings=None,
    snapshot=None,
    force_alert: bool = False,
    force_alert_kind: str = "runtime-write-failure",
):
    """Record one bounded capacity observation and dispatch any state alert.

    The helper is intentionally usable from error paths.  When MySQL cannot
    accept the event or notification job, the subscribed enqueuer sends the
    operations notifier directly instead.
    """

    configured_settings = settings or runtime_settings()
    observed = dict(snapshot or operational_storage_inventory(configured_settings))
    health, event = build_operational_storage_capacity_service(configured_settings).record(
        observed,
        force_alert=force_alert,
        force_alert_kind=force_alert_kind,
    )
    if event:
        operational_storage_event_bus(configured_settings).publish(event)
    return health


def ontology_reasoning_event_bus(settings=None) -> EventBus:
    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe(
        ONTOLOGY_REASONING_QUEUE_HEALTH_CHANGED,
        OntologyReasoningQueueHealthNotificationEnqueuer(
            queue=stores.notification_job_store(configured_settings),
            settings=configured_settings,
        ).handle,
    )
    return bus


def monitor_account_job_store_from_settings(settings):
    """Enable durable account scheduling only when it is explicitly configured.

    The monitor runner treats the presence of this store as the queue-mode
    switch. Constructing it unconditionally made ``monitorAccountQueueEnabled``
    ineffective and delayed normal monitor refreshes behind the job cadence.
    """
    configured_settings = dict(settings or {})
    if not setting_truthy(configured_settings.get("monitorAccountQueueEnabled"), default=False):
        return None
    return stores.monitor_account_job_store(configured_settings)


def build_hypothesis_lifecycle_service(settings=None, event_publisher=None) -> HypothesisLifecycleService:
    configured_settings = settings or runtime_settings()
    return HypothesisLifecycleService(
        store=stores.hypothesis_lifecycle_store(configured_settings),
        event_publisher=event_publisher,
        settings=configured_settings,
    )


def build_monitor_runner(
    accounts: Iterable[AccountConfig],
    event_publisher=None,
    progress_callback: Callable[[str, Dict[str, object]], None] = None,
    settings=None,
    typedb_native_rule_execution_enabled: bool = False,
    snapshot_builder: Callable = None,
    ontology_projection_enabled: bool = None,
    ontology_repository=None,
    monitor_store=None,
    source_snapshot_replay: bool = False,
) -> MonitorRunner:
    configured_settings = dict(settings or runtime_settings())
    configured_settings["typedbNativeRuleExecutionEnabled"] = "1" if typedb_native_rule_execution_enabled else "0"
    if ontology_projection_enabled is None:
        # Normal monitoring commits source data and publishes one verified
        # snapshot request.  The durable reasoning worker is then the only
        # TypeDB ABox/InferenceBox writer.  Operators can re-enable the old
        # synchronous path explicitly for a diagnostic run.
        ontology_projection_enabled = setting_truthy(
            configured_settings.get("ontologyMonitorInlineProjectionEnabled"),
            False,
        )
    monitor_snapshot_settings = dict(configured_settings)
    # The dedicated market-data worker owns slow external refreshes. The
    # realtime monitor consumes its cache so a vendor timeout cannot hold a
    # price/technical alert behind yfinance, news, or disclosure collection.
    monitor_snapshot_settings["_externalSignalsCacheOnly"] = "1"
    # The normal monitor owns the full research archive.  Isolated TypeDB
    # replay injects a read-only, target-scoped source store so selecting one
    # mailbox symbol does not deserialize every provider document first.
    store = monitor_store or stores.monitor_store(configured_settings)
    market_time_series_store = stores.market_time_series_store(configured_settings)
    ontology_quality_store = stores.ontology_quality_sample_store(configured_settings)
    investment_domain_store = stores.investment_domain_store(configured_settings)
    projection_repository = ontology_repository or ontology_repository_from_settings(configured_settings)
    interval_seconds = int(os.environ.get("PYTHON_REALTIME_INTERVAL_SECONDS") or os.environ.get("REALTIME_NOTIFY_INTERVAL_SECONDS") or configured_settings.get("monitorAccountIntervalSeconds") or 120)
    publisher = event_publisher or monitor_event_bus(configured_settings)
    return MonitorRunner(
        accounts,
        store=store,
        monitor=RealtimeMonitor(configured_settings),
        snapshot_builder=snapshot_builder or (
            lambda account: build_snapshot(account, external_settings=monitor_snapshot_settings)
        ),
        event_sender=send_events,
        event_publisher=publisher,
        cycle_recorder=stores.monitoring_cycle_recorder(
            configured_settings,
            store,
            market_time_series_store,
        ),
        ontology_projection_recorder=PortfolioOntologyProjectionRecorder(
            projection_repository,
            quality_store=ontology_quality_store,
            projection_run_store=stores.ontology_projection_run_store(configured_settings),
            decision_episode_store=stores.investment_decision_episode_store(configured_settings),
            hypothesis_proposal_store=stores.investment_research_store(configured_settings),
            hypothesis_lifecycle_store=stores.hypothesis_lifecycle_store(configured_settings),
            data_pipeline_health_store=stores.data_pipeline_health_store(configured_settings),
            market_time_series_store=market_time_series_store,
            investment_domain_store=investment_domain_store,
            world_projection_outbox=stores.ontology_world_projection_outbox_store(configured_settings),
            inference_detail_outbox=stores.ontology_inference_detail_outbox_store(configured_settings),
            graph_assembly_cache_store=stores.ontology_graph_assembly_cache_store(configured_settings),
            statistical_signal_service=build_statistical_signal_pipeline_service(configured_settings),
            settings=configured_settings,
        ),
        hypothesis_lifecycle_service=build_hypothesis_lifecycle_service(configured_settings, publisher),
        ontology_projection_enabled=bool(ontology_projection_enabled),
        account_job_store=monitor_account_job_store_from_settings(configured_settings),
        account_job_batch_size=int(configured_settings.get("monitorAccountBatchSize") or os.environ.get("MONITOR_ACCOUNT_BATCH_SIZE") or 10),
        account_job_interval_seconds=interval_seconds,
        account_job_lock_seconds=int(configured_settings.get("monitorAccountLockSeconds") or os.environ.get("MONITOR_ACCOUNT_LOCK_SECONDS") or max(600, interval_seconds * 4)),
        worker_id=os.environ.get("MONITOR_WORKER_ID") or ("monitor-" + uuid.uuid4().hex[:12]),
        progress_callback=progress_callback,
        source_snapshot_replay=source_snapshot_replay,
        portfolio_lifecycle_observer=(
            None
            if source_snapshot_replay
            else PortfolioAccountingService(
                investment_domain_store,
                stores.account_registry(configured_settings),
                InvestmentDomainService(investment_domain_store, publisher),
                market_time_series_store,
                configured_settings,
            )
        ),
    )


def build_model_review_runner(dry_run: bool = False) -> ModelReviewRunner:
    settings = runtime_settings()
    return ModelReviewRunner(
        queue=stores.model_review_job_store(settings),
        reviewer=reviewer_from_settings(settings),
        account_repository=stores.account_registry(settings),
        notifier_factory=lambda account: queued_notifier_for_account(account, message_type="modelReview"),
        dry_run=dry_run,
        settings=settings,
    )


def build_notification_queue_runner(dry_run: bool = False, lane: str = "all") -> NotificationQueueRunner:
    settings = runtime_settings()
    del lane  # Compatibility argument; dedicated AI workers replaced notification lanes.
    include_message_types = []
    exclude_message_types = []
    monitor_store = stores.monitor_store(settings)
    decision_episode_store = stores.investment_decision_episode_store(settings)
    investment_domain_store = stores.investment_domain_store(settings)
    continuity_service = DecisionContinuityService(decision_episode_store, investment_domain_store)
    investment_brain_service = build_investment_brain_service(settings)
    reasoning_queue_probe = build_ontology_reasoning_queue_probe(settings)
    queue_health_service = OntologyReasoningQueueHealthService(
        store=stores.ontology_reasoning_cursor_store(settings),
        settings=settings,
    )
    refresh_job_store = monitor_account_job_store_from_settings(settings) if not dry_run else None

    def request_fresh_data_recheck(account_id: str, symbol: str, source_job_id: str):
        if not refresh_job_store:
            return {
                "requested": True,
                "scheduledAt": utc_now(),
                "scheduleMode": "next-monitor-cycle",
                "maxDelaySeconds": max(30, int(settings.get("monitorAccountIntervalSeconds") or 30)),
                "pipeline": "monitor-snapshot-typedb-ai",
                "reason": "다음 실시간 모니터 주기에서 새 스냅샷부터 다시 판단합니다.",
            }
        result = dict(refresh_job_store.request_refresh(account_id, priority=5) or {})
        result.update({
            "symbol": str(symbol or "").upper(),
            "sourceJobId": str(source_job_id or ""),
        })
        return result

    def queue_health_at_dispatch():
        # Do not trust the event payload alone: an operations job can wait
        # behind outbound delivery work while the reasoning queue already
        # recovered. This deliberately avoids the full runner.status() path:
        # delivery must not contend with account-priority or TypeDB diagnostics
        # while a live projection is writing.
        return queue_health_service.observe(reasoning_queue_probe())

    identity_enricher = NotificationInstrumentIdentityEnricher(
        stores.symbol_universe_store(settings),
    )
    holding_enricher = NotificationHoldingSnapshotEnricher(
        monitor_store.load_previous,
        RealtimeMonitor(settings),
    )
    disclosure_enricher = DisclosureAnalysisNotificationEnricher(
        disclosure_analyzer_from_settings(settings),
        settings,
    )
    research_enricher = NotificationHypothesisResearchEnricher(
        investment_brain_service,
        settings,
    )
    opinion_enricher = NotificationAIOpinionEnricher(settings)
    ai_decision_context_enricher = NotificationAIDecisionContextEnricher(
        stores.market_time_series_store(settings),
        settings,
        investment_domain_store=investment_domain_store,
    )
    ai_request_enqueuer = None
    reasoning_orchestrator = None
    news_digest_reconciler = None
    if not dry_run:
        reasoning_orchestrator = InvestmentReasoningOrchestrator(
            stores.investment_reasoning_case_store(settings),
            decision_episode_store=decision_episode_store,
            subject_case_repository=stores.subject_decision_case_store(settings),
        )
        ai_request_enqueuer = NotificationAIRequestEnqueuer(
            stores.ai_inference_queue_store(settings),
            CompositeNotificationContextEnricher(
                identity_enricher,
                holding_enricher,
                disclosure_enricher,
                research_enricher,
                opinion_enricher,
                ai_decision_context_enricher,
            ),
            settings,
            decision_episode_store=decision_episode_store,
            continuity_service=continuity_service,
            reasoning_orchestrator=reasoning_orchestrator,
        )
        news_digest_reconciler = NewsDigestEventReconciler(
            event_reader=stores.event_log(settings),
            enqueuer=NewsDigestEnqueuer(
                account_repository=stores.account_registry(settings),
                monitor_store=monitor_store,
                queue=stores.notification_job_store(settings),
                settings=settings,
                max_items=int(number(settings.get("newsDigestMaxItems")) or 3),
                evidence_repository=stores.research_evidence_store(settings),
            ),
            cursor_store=stores.news_digest_reconciliation_state_store(settings),
        )

    return NotificationQueueRunner(
        queue=stores.notification_job_store(settings),
        account_repository=stores.account_registry(settings),
        notifier_factory=notifier_for_account,
        operations_notifier_factory=notifier_for_operations,
        dry_run=dry_run,
        send_gap_seconds=float(settings.get("notificationSendGapSeconds") or 0),
        stale_after_minutes=int(settings.get("notificationProcessingStaleMinutes") or 2),
        template_renderer=stores.notification_template_store(settings).render_job,
        context_enricher=CompositeNotificationContextEnricher(
            identity_enricher,
            disclosure_enricher,
            ai_decision_context_enricher,
            NotificationAIValidatedGateEnricher(
                notification_ai_reviewer_from_settings(settings) if dry_run else None,
                settings,
                stores.investment_decision_episode_store(settings),
            ),
            opinion_enricher,
        ),
        operator_reports_enabled=str(settings.get("operatorReasoningReportEnabled", "1")).strip().lower() not in {"0", "false", "no", "off"},
        settings=settings,
        operational_state_resolver=queue_health_at_dispatch,
        operational_delivery_recorder=queue_health_service.record_notification_delivery,
        include_message_types=include_message_types,
        exclude_message_types=exclude_message_types,
        ai_request_enqueuer=ai_request_enqueuer,
        reasoning_orchestrator=reasoning_orchestrator,
        news_digest_reconciler=news_digest_reconciler,
        fresh_data_recheck_requester=request_fresh_data_recheck,
        link_base_resolver=ActiveShareNotificationLinkResolver(),
    )


def build_ai_inference_queue_runner(worker_id: str = "") -> AIInferenceQueueRunner:
    settings = runtime_settings()
    decision_episode_store = stores.investment_decision_episode_store(settings)
    continuity_service = DecisionContinuityService(
        decision_episode_store,
        stores.investment_domain_store(settings),
    )
    return AIInferenceQueueRunner(
        queue=stores.ai_inference_queue_store(settings),
        reviewer=notification_ai_reviewer_from_settings(settings, allow_local_fallback=False),
        settings=settings,
        decision_episode_store=decision_episode_store,
        continuity_service=continuity_service,
        action_planning_service=build_decision_action_planning_service(settings),
        reasoning_orchestrator=InvestmentReasoningOrchestrator(
            stores.investment_reasoning_case_store(settings),
            decision_episode_store=decision_episode_store,
            hypothesis_proposal_request_store=stores.investment_research_store(settings),
            subject_case_repository=stores.subject_decision_case_store(settings),
        ),
        worker_id=worker_id,
    )


def build_decision_episode_reconciliation_service(settings=None) -> DecisionEpisodeReconciliationService:
    configured_settings = settings or runtime_settings()
    return DecisionEpisodeReconciliationService(
        stores.investment_decision_episode_store(configured_settings),
        stores.notification_job_store(configured_settings),
    )


def build_investment_brain_service(settings=None) -> InvestmentBrainService:
    configured_settings = settings or runtime_settings()
    research_store = stores.investment_research_store(configured_settings)
    ontology_repository = ontology_repository_from_settings(configured_settings)
    decision_episode_store = stores.investment_decision_episode_store(configured_settings)
    lifecycle_store = stores.hypothesis_lifecycle_store(configured_settings)
    hypothesis_review_service = HypothesisReviewService(
        hypothesis_lifecycle_store=lifecycle_store,
        decision_episode_store=decision_episode_store,
        ontology_repository=ontology_repository,
        settings=configured_settings,
    )
    hypothesis_lifecycle_policy_service = HypothesisLifecyclePolicyService(ontology_repository)
    hypothesis_quality_review_service = HypothesisQualityReviewService(decision_episode_store=decision_episode_store)
    return InvestmentBrainService(
        monitor_store=stores.monitor_store(configured_settings),
        ontology_repository=ontology_repository,
        reviewer=notification_ai_reviewer_from_settings(configured_settings),
        decision_episode_store=decision_episode_store,
        research_orchestrator=build_investment_research_orchestrator(configured_settings, research_store),
        hypothesis_proposal_service=build_hypothesis_proposal_service(configured_settings, research_store),
        research_store=research_store,
        settings=configured_settings,
        hypothesis_lifecycle_store=lifecycle_store,
        hypothesis_review_service=hypothesis_review_service,
        hypothesis_lifecycle_policy_service=hypothesis_lifecycle_policy_service,
        hypothesis_quality_review_service=hypothesis_quality_review_service,
        hypothesis_policy_governance_service=HypothesisPolicyGovernanceService(
            ontology_repository=ontology_repository,
            lifecycle_policy_service=hypothesis_lifecycle_policy_service,
        ),
        hypothesis_outcome_replay_service=HypothesisOutcomeReplayService(
            decision_episode_store=decision_episode_store,
            hypothesis_review_service=hypothesis_review_service,
            quality_review_service=hypothesis_quality_review_service,
        ),
    )


def build_historical_decision_replay_service(settings=None) -> HistoricalDecisionReplayService:
    """Build the read-only point-in-time replay audit without heavy brain dependencies."""

    configured_settings = settings or runtime_settings()
    return HistoricalDecisionReplayService(
        decision_episode_store=stores.investment_decision_episode_store(configured_settings),
    )


def build_investment_domain_service(settings=None) -> InvestmentDomainService:
    configured_settings = settings or runtime_settings()
    return InvestmentDomainService(
        repository=stores.investment_domain_store(configured_settings),
        event_publisher=ontology_reasoning_event_bus(configured_settings),
    )


def build_decision_action_planning_service(settings=None) -> DecisionActionPlanningService:
    configured_settings = settings or runtime_settings()
    return DecisionActionPlanningService(
        repository=stores.investment_domain_store(configured_settings),
        monitor_store=stores.monitor_store(configured_settings),
        settings=configured_settings,
    )


def build_trade_execution_service(settings=None) -> TradeExecutionService:
    configured_settings = settings or runtime_settings()
    return TradeExecutionService(
        stores.investment_domain_store(configured_settings),
        monitor_store=stores.monitor_store(configured_settings),
        settings=configured_settings,
        investment_domain_service=InvestmentDomainService(
            stores.investment_domain_store(configured_settings),
            ontology_reasoning_event_bus(configured_settings),
        ),
    )


def build_investment_research_orchestrator(settings=None, research_store=None) -> InvestmentResearchOrchestrationService:
    configured_settings = settings or runtime_settings()
    evidence_store = stores.research_evidence_store(configured_settings)
    return InvestmentResearchOrchestrationService(
        evidence_repository=evidence_store,
        research_gateway=CompositeInvestmentResearchGateway([
            ExistingApiResearchGateway(configured_settings),
            NewsSourceGateway(configured_settings),
        ]),
        research_store=research_store or stores.investment_research_store(configured_settings),
        event_publisher=default_event_bus(),
        article_analysis_service=NewsAiAnalysisService(
            news_ai_analyzer_from_settings(configured_settings),
            configured_settings,
        ),
        hypothesis_research_planner=build_hypothesis_research_planning_service(configured_settings),
        settings=configured_settings,
    )


def build_investment_research_queue_runner(settings=None) -> InvestmentResearchQueueRunner:
    configured_settings = settings or runtime_settings()
    research_store = stores.investment_research_store(configured_settings)
    return InvestmentResearchQueueRunner(
        store=research_store,
        orchestrator=build_investment_research_orchestrator(configured_settings, research_store),
        hypothesis_proposal_runner=HypothesisProposalQueueRunner(
            research_store,
            build_hypothesis_proposal_service(configured_settings, research_store),
        ),
    )


def build_hypothesis_proposal_service(settings=None, research_store=None) -> HypothesisProposalService:
    configured_settings = settings or runtime_settings()
    return HypothesisProposalService(
        store=research_store or stores.investment_research_store(configured_settings),
        advisor=hypothesis_proposal_advisor_from_settings(configured_settings),
        event_publisher=default_event_bus(),
        settings=configured_settings,
        development_service=build_hypothesis_development_service(configured_settings, research_store),
    )


def build_hypothesis_development_service(settings=None, research_store=None) -> HypothesisDevelopmentService:
    configured_settings = settings or runtime_settings()
    return HypothesisDevelopmentService(
        case_store=stores.hypothesis_development_store(configured_settings),
        proposal_store=research_store or stores.investment_research_store(configured_settings),
        experiment_store=stores.ontology_experiment_store(configured_settings),
        rule_candidate_service=build_rule_change_candidate_service(configured_settings),
        ontology_repository=ontology_repository_from_settings(configured_settings),
        monitor_store=stores.monitor_store(configured_settings),
        event_publisher=default_event_bus(),
        settings=configured_settings,
    )


def build_hypothesis_research_planning_service(settings=None) -> HypothesisResearchPlanningService:
    configured_settings = settings or runtime_settings()
    return HypothesisResearchPlanningService(
        advisor=hypothesis_research_planning_advisor_from_settings(configured_settings),
        settings=configured_settings,
    )


def build_symbol_universe_service(settings=None) -> SymbolUniverseService:
    configured_settings = settings or runtime_settings()
    return SymbolUniverseService(
        store=stores.symbol_universe_store(configured_settings),
        source_gateway=RemoteSymbolSourceGateway(configured_settings),
        settings=configured_settings,
        quote_cache=stores.market_quote_cache(configured_settings),
    )


def build_market_data_collection_runner(settings=None, event_publisher=None) -> MarketDataCollectionRunner:
    configured_settings = settings or runtime_settings()
    return MarketDataCollectionRunner(
        account_repository=stores.account_registry(configured_settings),
        symbol_service=build_symbol_universe_service(configured_settings),
        quote_cache=stores.market_quote_cache(configured_settings),
        settings=configured_settings,
        provider_factory=lambda account, quote_cache: TossProvider(account, quote_cache=quote_cache, settings=configured_settings),
        event_publisher=event_publisher or data_pipeline_health_event_bus(configured_settings),
        time_series_store=stores.market_time_series_store(configured_settings),
        health_service=DataPipelineHealthService(
            stores.data_pipeline_health_store(configured_settings),
            configured_settings,
        ),
        decision_episode_store=stores.investment_decision_episode_store(configured_settings),
        external_signal_refresher=lambda positions: ExternalSignalProvider(
            settings=configured_settings,
        ).signals_for_positions(positions, cache_scope="account-snapshot"),
    )


def build_external_data_collection_runner(settings=None) -> ExternalDataCollectionService:
    configured_settings = dict(settings or runtime_settings())
    registry = default_external_dataset_registry(configured_settings)
    store = stores.external_data_store(configured_settings)
    evidence_projector = ExternalOfficialEvidenceProjectionService(
        fact_store=store,
        evidence_store=stores.research_evidence_store(configured_settings),
        event_publisher=news_event_bus(configured_settings),
        settings=configured_settings,
        disclosure_analyzer=disclosure_analyzer_from_settings(configured_settings),
    )
    return ExternalDataCollectionService(
        settings=configured_settings,
        registry=registry,
        store=store,
        legacy_importer=LegacyExternalSignalImporter(
            stores.external_signal_cache(configured_settings),
            store,
            registry,
            configured_settings,
        ),
        evidence_reconciler=ExternalFactResearchEvidenceReconciler(
            event_reader=stores.event_log(configured_settings),
            projector=evidence_projector,
            cursor_store=stores.external_evidence_projection_state_store(configured_settings),
            batch_size=int(number(configured_settings.get("externalEvidenceProjectionBatchSize")) or 100),
            initial_lookback_minutes=int(number(configured_settings.get("externalEvidenceProjectionInitialLookbackMinutes")) or 10),
            max_replay_age_minutes=int(number(configured_settings.get("externalEvidenceProjectionMaxReplayAgeMinutes")) or 180),
        ),
        worker_id="external-data-" + str(os.getpid()),
    )


def build_kis_realtime_websocket_runner(settings=None, event_publisher=None) -> KISRealtimeWebSocketRunner:
    configured_settings = settings or runtime_settings()
    quote_cache = stores.market_quote_cache(configured_settings)
    monitor_store = stores.monitor_store(configured_settings)
    return KISRealtimeWebSocketRunner(
        client=KISRealtimeWebSocketClient(configured_settings, quote_cache=quote_cache),
        symbol_selector=KISRealtimeSymbolSelector(
            stores.account_registry(configured_settings),
            monitor_store,
            quote_cache,
            configured_settings,
        ),
        quote_cache=quote_cache,
        settings=configured_settings,
        event_publisher=event_publisher or default_event_bus(),
    )


def build_news_collection_runner(settings=None, event_publisher=None) -> NewsCollectionRunner:
    configured_settings = settings or runtime_settings()
    return NewsCollectionRunner(
        account_repository=stores.account_registry(configured_settings),
        monitor_store=stores.monitor_store(configured_settings),
        symbol_store=stores.symbol_universe_store(configured_settings),
        evidence_store=stores.research_evidence_store(configured_settings),
        gateway=NewsSourceGateway(configured_settings),
        settings=configured_settings,
        event_publisher=event_publisher or news_event_bus(configured_settings),
        article_analysis_service=NewsAiAnalysisService(
            news_ai_analyzer_from_settings(configured_settings),
            configured_settings,
        ),
        health_service=DataPipelineHealthService(
            stores.data_pipeline_health_store(configured_settings),
            configured_settings,
        ),
    )


def build_news_analysis_enrichment_runner(settings=None, event_publisher=None) -> NewsAnalysisEnrichmentRunner:
    configured_settings = settings or runtime_settings()
    return NewsAnalysisEnrichmentRunner(
        evidence_store=stores.research_evidence_store(configured_settings),
        analysis_service=NewsAiAnalysisService(
            news_ai_analyzer_from_settings(configured_settings),
            configured_settings,
        ),
        settings=configured_settings,
        event_publisher=event_publisher or news_event_bus(configured_settings),
        storage_guard=lambda: operational_storage_inventory(configured_settings),
    )


def build_investment_calendar_service(settings=None, event_publisher=None) -> InvestmentCalendarService:
    configured_settings = settings or runtime_settings()
    return InvestmentCalendarService(
        repository=stores.investment_calendar_store(configured_settings),
        account_repository=stores.account_registry(configured_settings),
        notification_queue=stores.notification_job_store(configured_settings),
        settings=configured_settings,
        event_publisher=event_publisher or default_event_bus(),
        symbol_repository=stores.symbol_universe_store(configured_settings),
    )


def build_instrument_timeline_query_service(settings=None) -> InstrumentTimelineQueryService:
    configured_settings = settings or runtime_settings()
    return InstrumentTimelineQueryService(
        time_series_store=stores.market_time_series_store(configured_settings),
        evidence_store=stores.research_evidence_store(configured_settings),
        calendar_store=stores.investment_calendar_store(configured_settings),
        decision_episode_store=stores.investment_decision_episode_store(configured_settings),
        hypothesis_lifecycle_store=stores.hypothesis_lifecycle_store(configured_settings),
        notification_job_store=stores.notification_job_store(configured_settings),
        symbol_store=stores.symbol_universe_store(configured_settings),
    )


def build_official_calendar_sync_service(settings=None, event_publisher=None) -> OfficialCalendarSyncService:
    configured_settings = settings or runtime_settings()
    calendar_service = build_investment_calendar_service(configured_settings, event_publisher)
    account_repository = stores.account_registry(configured_settings)
    priority_symbols = ontology_reasoning_priority_symbols(account_repository, configured_settings)
    calendar_symbols = list(priority_symbols.get("holdingSymbols") or []) + list(priority_symbols.get("watchlistSymbols") or [])
    candidate_service = InvestmentCalendarCandidateService(
        candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
        calendar_service=calendar_service,
        settings=configured_settings,
        symbol_repository=stores.symbol_universe_store(configured_settings),
    )
    return OfficialCalendarSyncService(
        calendar_service=calendar_service,
        sources=[
            BokPolicyDecisionCalendarSource(configured_settings),
            OpenDartEarningsCalendarSource(configured_settings, target_symbols=calendar_symbols),
            SamsungIrEarningsCalendarSource(configured_settings),
        ],
        candidate_service=candidate_service,
        settings=configured_settings,
    )


def build_investment_calendar_candidate_service(settings=None, event_publisher=None) -> InvestmentCalendarCandidateService:
    configured_settings = settings or runtime_settings()
    return InvestmentCalendarCandidateService(
        candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
        calendar_service=build_investment_calendar_service(configured_settings, event_publisher),
        settings=configured_settings,
        symbol_repository=stores.symbol_universe_store(configured_settings),
    )


def build_investment_calendar_research_service(settings=None) -> InvestmentCalendarResearchRecommendationService:
    configured_settings = settings or runtime_settings()
    return InvestmentCalendarResearchRecommendationService(
        candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
        evidence_repository=stores.research_evidence_store(configured_settings),
        account_repository=stores.account_registry(configured_settings),
        news_collection_runner_factory=lambda: build_news_collection_runner(
            configured_settings,
            event_publisher=default_event_bus(),
        ),
        settings=configured_settings,
    )


def build_investment_calendar_discovery_service(settings=None, event_publisher=None) -> InvestmentCalendarDiscoveryService:
    configured_settings = settings or runtime_settings()
    return InvestmentCalendarDiscoveryService(
        calendar_service=build_investment_calendar_service(configured_settings, event_publisher),
        candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
        evidence_repository=stores.research_evidence_store(configured_settings),
        account_repository=stores.account_registry(configured_settings),
        research_gateway=ExistingApiResearchGateway(configured_settings),
        settings=configured_settings,
    )


def build_investment_calendar_runner(settings=None, event_publisher=None) -> InvestmentCalendarRunner:
    configured_settings = settings or runtime_settings()
    return InvestmentCalendarRunner(
        build_investment_calendar_service(configured_settings, event_publisher),
        official_sync_service=build_official_calendar_sync_service(configured_settings, event_publisher),
        discovery_service=build_investment_calendar_discovery_service(configured_settings, event_publisher),
    )


def ontology_reasoning_priority_symbols(account_repository, settings=None) -> Dict[str, list]:
    """Return the latest account focus set for worker scheduling only.

    Holdings are handled before watchlist names and both are handled before
    background universe ticks. The snapshot remains the source of truth for
    positions; this helper does not create investment facts or decisions.
    """
    configured_settings = settings or runtime_settings()
    roles = {"holdingSymbols": [], "watchlistSymbols": []}

    def add(role: str, value: object) -> None:
        symbol = str(value or "").upper().strip()
        if symbol and symbol not in roles[role]:
            roles[role].append(symbol)

    try:
        previous = stores.monitor_store(configured_settings).previous
    except Exception:  # noqa: BLE001 - a missing snapshot simply leaves account config priorities.
        previous = {}
    for state in (previous or {}).values():
        if not isinstance(state, dict):
            continue
        for container, role in (("positions", "holdingSymbols"), ("watchlist", "watchlistSymbols")):
            items = state.get(container)
            if isinstance(items, dict):
                for key, item in items.items():
                    add(role, item.get("symbol") if isinstance(item, dict) else key)
            elif isinstance(items, list):
                for item in items:
                    add(role, item.get("symbol") if isinstance(item, dict) else item)
    try:
        accounts = account_repository.load()
    except Exception:  # noqa: BLE001 - the live snapshot above is still sufficient when available.
        accounts = []
    for account in accounts:
        for symbol in getattr(account, "watchlist_symbols", []) or []:
            add("watchlistSymbols", symbol)
    return roles


def typedb_projection_recovery_health(ontology_repository, world_id: str) -> Dict[str, object]:
    """Check durable TypeDB availability before retrying a failed projection.

    The circuit guards the worker after an infrastructure failure. It must not
    require the pending event's future InferenceBox generation to already
    exist, because the normal projection path is responsible for producing it
    once this health probe succeeds.
    """
    clean_world_id = str(world_id or "").strip()
    active = ontology_repository.active_abox_metadata(world_id=clean_world_id)
    active_status = str((active or {}).get("status") or "").strip().lower()
    active_abox = str((active or {}).get("aboxSnapshotId") or "").strip()
    marker_reader = getattr(ontology_repository, "inferencebox_recovery_metadata", None)
    if callable(marker_reader):
        try:
            inference = marker_reader(world_id=clean_world_id)
        except Exception as error:  # noqa: BLE001 - active ABox availability is the recovery gate.
            inference = {
                "status": "error",
                "reason": "TypeDB active InferenceBox marker 조회 실패: " + str(error)[:180],
            }
    else:
        inference = {
            "status": "not-supported",
            "reason": "현재 저장소는 경량 InferenceBox 복구 표식을 제공하지 않습니다.",
        }
    inference = dict(inference or {}) if isinstance(inference, dict) else {"status": "invalid"}
    source_abox = str(inference.get("sourceAboxSnapshotId") or "").strip()
    aligned = bool(source_abox and source_abox == active_abox)
    ready = bool(active_status == "ok" and active_abox)
    return {
        "ready": ready,
        "recoveryMode": "active-abox-health-probe",
        "worldId": clean_world_id,
        "activeAboxSnapshotId": active_abox,
        "activeAboxStatus": active_status,
        "inferenceStatus": str(inference.get("status") or ""),
        "inferenceGenerationId": str(inference.get("inferenceGenerationId") or ""),
        "sourceAboxSnapshotId": source_abox,
        "inferenceGenerationAligned": aligned,
        "inferenceTargetSymbols": list(inference.get("targetSymbols") or []),
        "inferenceDiagnostic": str(inference.get("reason") or "")[:180],
        "requiresFreshProjection": not aligned,
        "reason": (
            "현재 활성 ABox를 읽을 수 있어 보류된 이벤트의 새 TypeDB 추론을 다시 시도합니다."
            if ready
            else str((active or {}).get("reason") or "현재 활성 ABox를 검증하지 못했습니다.")[:180]
        ),
    }


def active_versioned_reasoning_queue_state(
    registry,
    job_store,
    configured_v2_deployment_id: str = "",
) -> Dict[str, object]:
    """Read every protected V2 deployment's live TypeDB-writer backlog.

    Active and candidate releases can briefly coexist during an immutable
    release switch. Low-priority TypeDB writers must yield to either queue;
    observing only the active release lets maintenance or schema compilation
    race a candidate projection that is already processing.
    """

    try:
        control = registry.control()
        active_deployment_id = str(
            getattr(control, "active_deployment_id", "") or ""
        ).strip()
        requested_ids = list(dict.fromkeys(
            str(getattr(control, field, "") or "").strip()
            for field in ("active_deployment_id", "delivery_deployment_id")
            if str(getattr(control, field, "") or "").strip()
        ))
        candidate_id = str(
            getattr(control, "candidate_deployment_id", "") or ""
        ).strip()
        if candidate_id and candidate_id == str(configured_v2_deployment_id or "").strip():
            requested_ids.append(candidate_id)
        v2_ids = []
        for deployment_id in requested_ids:
            deployment = dict(registry.get(deployment_id) or {})
            engine_version = str(
                deployment.get("engineVersion")
                or deployment.get("engine_version")
                or ""
            ).strip().lower()
            if engine_version == "v2":
                v2_ids.append(deployment_id)
        if not v2_ids:
            return {
                "status": "not-active-v2",
                "deploymentId": active_deployment_id,
                "deploymentIds": [],
                "effectivePendingCount": 0,
            }
        states = {
            deployment_id: dict(job_store.live_queue_state(deployment_id) or {})
            for deployment_id in v2_ids
        }
        effective_pending = sum(
            max(0, int(state.get("effectivePendingCount") or 0))
            for state in states.values()
        )
        processing = sum(
            max(0, int(state.get("processingCount") or 0))
            for state in states.values()
        )
        queued = sum(
            max(0, int(state.get("queuedCount") or 0))
            for state in states.values()
        )
        primary = dict(states.get(active_deployment_id) or states[v2_ids[0]])
        return {
            **primary,
            "status": "active" if effective_pending else "idle",
            "deploymentId": active_deployment_id or v2_ids[0],
            "deploymentIds": v2_ids,
            "effectivePendingCount": effective_pending,
            "processingCount": processing,
            "queuedCount": queued,
            "queuesByDeployment": states,
        }
    except Exception as error:  # Fail closed so background writes cannot race an unknown V2 state.
        return {
            "status": "error",
            "effectivePendingCount": 1,
            "pendingCount": 1,
            "reason": str(error)[:180],
        }


def build_ontology_reasoning_queue_probe(settings=None):
    """Build a low-cost read-only signal for lower-priority workers.

    The full reasoning status is an operator diagnostic; it evaluates account
    priority and TypeDB health.  That is intentionally not a dependency of
    shared-world projection, ABox retention, or notification dispatch.
    """
    configured_settings = settings or runtime_settings()
    store_settings = dict(configured_settings)
    # This probe is called by isolated low-priority workers.  Do not let its
    # construction run schema DDL or operational-history retention before it
    # can yield.
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"
    event_reader = stores.event_log(store_settings)
    cursor_store = stores.ontology_reasoning_cursor_store(store_settings)
    mailbox_store = stores.ontology_reasoning_mailbox_store(store_settings)
    reasoning_registry = stores.reasoning_engine_registry_store(store_settings)
    versioned_job_store = stores.reasoning_engine_job_store(store_settings)
    try:
        cache_seconds = float(str(configured_settings.get("ontologyReasoningQueueProbeCacheSeconds") or "5").strip())
    except ValueError:
        cache_seconds = 5.0
    cache_seconds = max(0.0, min(60.0, cache_seconds))
    cache = {"at": 0.0, "value": None}

    def probe():
        now = time.monotonic()
        cached = cache.get("value")
        age = now - float(cache.get("at") or 0.0)
        if cached is not None and age >= 0 and age < cache_seconds:
            value = dict(cached or {})
            value["probeCached"] = True
            value["probeCacheAgeMs"] = int(age * 1000)
            return value
        try:
            legacy_value = lightweight_ontology_reasoning_queue_state(
                event_reader,
                cursor_store,
                mailbox_store=mailbox_store,
                settings=configured_settings,
            )
            versioned_value = active_versioned_reasoning_queue_state(
                reasoning_registry,
                versioned_job_store,
                configured_v2_deployment_id=str(
                    configured_settings.get("reasoningEngineV2DeploymentId") or ""
                ),
            )
            if str(versioned_value.get("status") or "") != "not-active-v2":
                value = {
                    **dict(versioned_value or {}),
                    "probeMode": "active-v2-authoritative-queue",
                    "queueMode": str(versioned_value.get("status") or "unknown"),
                    "activeReasoningEngineQueue": versioned_value,
                    "compatibilityQueueIncluded": True,
                    "legacyReasoningQueue": legacy_value,
                }
            else:
                value = dict(legacy_value or {})
            cache["at"] = time.monotonic()
            cache["value"] = dict(value or {})
            return value
        except Exception as error:  # noqa: BLE001 - the global TypeDB lease remains the final safety boundary.
            return {
                "status": "error",
                "effectivePendingCount": 0,
                "probeHealth": {"status": "degraded", "reason": str(error)[:180]},
                "queueHealth": {"status": "degraded", "reason": str(error)[:180], "scope": "probe-connectivity"},
            }

    return probe


def build_ontology_world_projection_runner(settings=None) -> OntologyWorldProjectionRunner:
    """Build the independent durable shared-world projection worker."""
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
    configured_settings = settings or runtime_settings()
    read_only_store_settings = dict(configured_settings)
    read_only_store_settings["_skipOperationalHistoryRetention"] = "1"
    read_only_store_settings["_skipOperationalSchemaBootstrap"] = "1"
    return OntologyReasoningProofService(
        ontology_repository=ontology_repository_from_settings(configured_settings),
        projection_run_store=stores.ontology_projection_run_store(read_only_store_settings),
        account_repository=stores.account_registry(read_only_store_settings),
        reasoning_cursor_store=stores.ontology_reasoning_cursor_store(read_only_store_settings),
        settings=configured_settings,
    )


def build_ontology_reasoning_runner(settings=None, event_publisher=None) -> OntologyReasoningRunner:
    configured_settings = settings or runtime_settings()
    from .reasoning_engine_factory import build_reasoning_engine_platform

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
    registry = stores.account_registry(reasoning_store_settings)
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


class FrozenReasoningSnapshotSource:
    """Rehydrate only the immutable observation packet owned by one V2 job."""

    def __init__(self, states):
        self.states = {
            str(account_id or ""): dict(state)
            for account_id, state in dict(states or {}).items()
            if str(account_id or "") and isinstance(state, dict)
        }

    def __call__(self, account, reasoning_context=None):
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
            for account in stores.account_registry(store_settings).load() or []
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


def build_v2_reasoning_engine(
    settings=None,
    deployment_id: str = "",
) -> V2ReasoningEngine:
    """Compose V2 from source ports without constructing MonitorRunner."""

    configured = dict(settings or runtime_settings())
    from .reasoning_engine_factory import build_reasoning_engine_platform

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
    account_repository = stores.account_registry(store_settings)
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
    runtime_world_partition = projection_recorder.world_rule_partition(
        runtime_rulebox_catalog
    )
    if (
        str(runtime_rulebox_catalog.get("status") or "") != "ready"
        or str(runtime_world_partition.get("status") or "") != "ready"
    ):
        raise RuntimeError(
            "The independent V2 frozen ontology release could not be warmed: "
            + str(
                runtime_rulebox_catalog.get("reason")
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
        },
        "ruleExecutionReadiness": {
            "status": "ready",
            "mode": "typedb-direct-typeql",
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
            post_inference_observer=build_hypothesis_lifecycle_service(store_settings),
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
        reasoning_orchestrator=InvestmentReasoningOrchestrator(
            stores.investment_reasoning_case_store(store_settings),
            decision_episode_store=stores.investment_decision_episode_store(store_settings),
            hypothesis_proposal_request_store=stores.investment_research_store(store_settings),
            subject_case_repository=stores.subject_decision_case_store(store_settings),
        ),
        shared_inference_service=shared_inference_service,
    )


def resolve_v2_reasoning_worker_deployment(
    settings=None,
    worker_role: str = "configured",
    deployment_id: str = "",
) -> str:
    """Resolve one worker to an authoritative control-plane deployment."""

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
    from .mysql_reasoning_ingress import MySQLReasoningIngressRouter

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
    return IndependentReasoningJobRunner(
        queue=stores.reasoning_engine_job_store(configured),
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
    )


def build_rule_change_candidate_service(settings=None) -> RuleChangeCandidateProposalService:
    configured_settings = settings or runtime_settings()
    return RuleChangeCandidateProposalService(
        ontology_repository=ontology_repository_from_settings(configured_settings),
        advisor=rule_change_candidate_advisor_from_settings(configured_settings),
        event_reader=stores.event_log(configured_settings),
        settings=configured_settings,
        strategy_proposal_service=build_investment_strategy_proposal_service(configured_settings),
    )


def build_investment_strategy_proposal_service(settings=None, event_publisher=None) -> InvestmentStrategyProposalService:
    configured_settings = settings or runtime_settings()
    return InvestmentStrategyProposalService(
        proposal_store=stores.investment_strategy_proposal_store(configured_settings),
        ontology_repository=ontology_repository_from_settings(configured_settings),
        event_publisher=event_publisher or default_event_bus(),
        settings=configured_settings,
    )


def build_ontology_lab_service(settings=None) -> OntologyLabService:
    configured_settings = settings or runtime_settings()
    return OntologyLabService(
        ontology_repository=ontology_repository_from_settings(configured_settings),
        experiment_store=stores.ontology_experiment_store(configured_settings),
        monitor_store=stores.monitor_store(configured_settings),
        rule_candidate_service=build_rule_change_candidate_service(configured_settings),
        strategy_proposal_service=build_investment_strategy_proposal_service(configured_settings),
        notification_queue=stores.notification_job_store(configured_settings),
        reasoning_queue_probe=build_ontology_reasoning_queue_probe(configured_settings),
        hypothesis_development_service=build_hypothesis_development_service(configured_settings),
        settings=configured_settings,
    )


def build_flow_lens_service(settings=None) -> FlowLensService:
    configured_settings = settings or runtime_settings()
    flow_lens_external_settings = dict(configured_settings)
    def capped_int(key: str, fallback: int, cap: int) -> str:
        return str(min(cap, int(number(flow_lens_external_settings.get(key)) or fallback)))

    flow_lens_external_settings["externalApiRetryAttempts"] = "1"
    flow_lens_external_settings["externalApiTimeoutSeconds"] = str(min(2.0, number(flow_lens_external_settings.get("externalApiTimeoutSeconds")) or 2.0))
    flow_lens_external_settings["externalFredTimeoutSeconds"] = str(min(2.0, number(flow_lens_external_settings.get("externalFredTimeoutSeconds")) or 2.0))
    flow_lens_external_settings["externalAlphaMaxSymbols"] = capped_int("externalAlphaMaxSymbols", 1, 1)
    flow_lens_external_settings["externalSecMaxSymbols"] = capped_int("externalSecMaxSymbols", 1, 1)
    flow_lens_external_settings["externalDartMaxSymbols"] = capped_int("externalDartMaxSymbols", 1, 1)
    flow_lens_external_settings["externalNewsMaxSymbols"] = capped_int("externalNewsMaxSymbols", 1, 1)
    flow_lens_external_settings["externalCryptoMaxIds"] = capped_int("externalCryptoMaxIds", 2, 2)
    flow_lens_external_settings["externalFredMaxSeries"] = capped_int("externalFredMaxSeries", 2, 2)
    symbol_service = build_symbol_universe_service(configured_settings)
    return FlowLensService(
        account_repository=stores.account_registry(configured_settings),
        snapshot_builder=lambda account: build_snapshot(account, external_settings=flow_lens_external_settings),
        demo_positions_provider=demo_positions,
        settings_provider=lambda: configured_settings,
        fx_rates_provider=currency_rates,
        symbol_enricher=symbol_service.enrich,
        market_quote_cache=stores.market_quote_cache(configured_settings),
    )


def flow_lens_snapshot(mock: bool = False, watchlist_symbols: str = ""):
    return build_flow_lens_service().snapshot(mock=mock, watchlist_symbols=watchlist_symbols)


def build_investment_analysis_service(settings=None) -> InvestmentAnalysisService:
    flow_service = build_flow_lens_service(settings)
    return InvestmentAnalysisService(
        snapshot_provider=lambda mock=False, watchlist_symbols="": flow_service.snapshot(
            mock=mock,
            watchlist_symbols=watchlist_symbols,
        ),
    )


def investment_analysis_snapshot(mock: bool = False, watchlist_symbols: str = ""):
    return build_investment_analysis_service().snapshot(mock=mock, watchlist_symbols=watchlist_symbols)
