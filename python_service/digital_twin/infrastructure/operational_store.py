from typing import Dict

from .mysql_monitoring import MySQLMonitorAccountJobStore
from .mysql_operational import (
    MySQLAccountRegistry,
    MySQLAIInferenceQueueStore,
    MySQLAppStore,
    MySQLCompanyKnowledgeCache,
    MySQLCryptoMarketSignalCache,
    MySQLDataPipelineHealthStore,
    MySQLNewsDigestReconciliationStateStore,
    MySQLEventLog,
    MySQLExternalSignalCache,
    MySQLOperationalStorageCapacityStateStore,
    MySQLInvestmentCalendarCandidateStore,
    MySQLInvestmentCalendarStore,
    MySQLInvestmentStrategyProposalStore,
    MySQLInvestmentDecisionEpisodeStore,
    MySQLInvestmentDomainStore,
    MySQLHypothesisLifecycleStore,
    MySQLHypothesisDevelopmentStore,
    MySQLOntologyExperimentStore,
    MySQLInvestmentResearchStore,
    MySQLMarketQuoteCache,
    MySQLMarketObservationReasoningAnchorStore,
    MySQLMarketTimeSeriesStore,
    MySQLModelReviewJobStore,
    MySQLMonitorStore,
    MySQLOntologyReasoningMonitorStore,
    MySQLMonitoringCycleRecorder,
    MySQLNotificationJobStore,
    MySQLNotificationRuleStore,
    MySQLNotificationTemplateStore,
    MySQLOntologyProjectionRunStore,
    MySQLOntologyGraphAssemblyCacheStore,
    MySQLOntologyInferenceDetailStateStore,
    MySQLOntologyInferenceDetailOutboxStore,
    MySQLOntologyWorldProjectionOutboxStore,
    MySQLOntologyWorldProjectionStateStore,
    MySQLOntologyQualitySampleStore,
    MySQLOntologyMaintenanceStateStore,
    MySQLOntologyRuleboxPrewarmStateStore,
    MySQLOntologyReasoningCursorStore,
    MySQLOntologyReasoningMailboxStore,
    MySQLResearchEvidenceStore,
    MySQLRuntimeSettingsStore,
    MySQLSymbolUniverseStore,
    MySQLReasoningEngineRegistryStore,
    MySQLReasoningEngineComparisonStore,
    MySQLReasoningEngineJobStore,
    MySQLReasoningShadowJobStore,
    MySQLTemporalFeatureSnapshotStore,
    MySQLTimeSeriesBackendRegistryStore,
    MySQLTimeSeriesProjectionOutboxStore,
    MySQLExternalDataStore,
)
from .settings import runtime_settings


def configured_settings(settings: Dict[str, str] = None) -> Dict[str, str]:
    return settings if settings is not None else runtime_settings()


def use_mysql(settings: Dict[str, str] = None) -> bool:
    return True


def runtime_settings_store(settings: Dict[str, str] = None):
    return MySQLRuntimeSettingsStore(settings)


def account_registry(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLAccountRegistry(configured)


def app_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLAppStore(configured)


def external_signal_cache(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLExternalSignalCache(configured)


def external_data_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLExternalDataStore(configured)


def company_knowledge_cache(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLCompanyKnowledgeCache(configured)


def crypto_market_signal_cache(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLCryptoMarketSignalCache(configured)


def data_pipeline_health_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLDataPipelineHealthStore(configured)


def news_digest_reconciliation_state_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLNewsDigestReconciliationStateStore(configured)


def operational_storage_capacity_state_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOperationalStorageCapacityStateStore(configured)


def ontology_reasoning_cursor_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOntologyReasoningCursorStore(configured)


def ontology_maintenance_state_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOntologyMaintenanceStateStore(configured)


def ontology_world_projection_state_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOntologyWorldProjectionStateStore(configured)


def ontology_inference_detail_state_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOntologyInferenceDetailStateStore(configured)


def ontology_rulebox_prewarm_state_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOntologyRuleboxPrewarmStateStore(configured)


def ontology_reasoning_mailbox_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOntologyReasoningMailboxStore(configured)


def monitor_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLMonitorStore(configured)


def ontology_reasoning_monitor_store(settings: Dict[str, str] = None):
    """Return the read-only, target-scoped monitor source for TypeDB replay."""
    configured = configured_settings(settings)
    return MySQLOntologyReasoningMonitorStore(configured)


def monitoring_cycle_recorder(
    settings: Dict[str, str] = None,
    monitor_store_instance=None,
    market_time_series_store_instance=None,
):
    configured = configured_settings(settings)
    return MySQLMonitoringCycleRecorder(
        configured,
        monitor_store=monitor_store_instance,
        market_time_series_store=market_time_series_store_instance,
    )


def event_log(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLEventLog(configured)


def model_review_job_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLModelReviewJobStore(configured)


def notification_job_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLNotificationJobStore(configured)


def ai_inference_queue_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLAIInferenceQueueStore(configured)


def notification_template_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLNotificationTemplateStore(configured)


def notification_rule_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLNotificationRuleStore(configured)


def market_quote_cache(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLMarketQuoteCache(configured)


def market_time_series_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    from .time_series_factory import build_versioned_time_series_store
    return build_versioned_time_series_store(configured)


def raw_mysql_market_time_series_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLMarketTimeSeriesStore(configured)


def time_series_backend_registry_store(settings: Dict[str, str] = None):
    return MySQLTimeSeriesBackendRegistryStore(configured_settings(settings))


def time_series_projection_outbox_store(settings: Dict[str, str] = None):
    return MySQLTimeSeriesProjectionOutboxStore(configured_settings(settings))


def temporal_feature_snapshot_store(settings: Dict[str, str] = None):
    return MySQLTemporalFeatureSnapshotStore(configured_settings(settings))


def reasoning_engine_registry_store(settings: Dict[str, str] = None):
    return MySQLReasoningEngineRegistryStore(configured_settings(settings))


def reasoning_engine_comparison_store(settings: Dict[str, str] = None):
    return MySQLReasoningEngineComparisonStore(configured_settings(settings))


def reasoning_engine_job_store(settings: Dict[str, str] = None):
    return MySQLReasoningEngineJobStore(configured_settings(settings))


def reasoning_shadow_job_store(settings: Dict[str, str] = None):
    return MySQLReasoningShadowJobStore(configured_settings(settings))


def market_observation_reasoning_anchor_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLMarketObservationReasoningAnchorStore(configured)


def symbol_universe_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLSymbolUniverseStore(configured)


def research_evidence_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLResearchEvidenceStore(configured)


def investment_calendar_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLInvestmentCalendarStore(configured)


def investment_calendar_candidate_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLInvestmentCalendarCandidateStore(configured)


def ontology_quality_sample_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOntologyQualitySampleStore(configured)


def ontology_projection_run_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOntologyProjectionRunStore(configured)


def ontology_graph_assembly_cache_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOntologyGraphAssemblyCacheStore(configured)


def ontology_world_projection_outbox_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOntologyWorldProjectionOutboxStore(configured)


def ontology_inference_detail_outbox_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOntologyInferenceDetailOutboxStore(configured)


def ontology_experiment_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    from .settings import data_dir
    return MySQLOntologyExperimentStore(configured, legacy_path=data_dir() / "ontology-lab.json")


def hypothesis_development_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLHypothesisDevelopmentStore(configured)


def investment_strategy_proposal_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLInvestmentStrategyProposalStore(configured)


def investment_decision_episode_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLInvestmentDecisionEpisodeStore(configured)


def investment_domain_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLInvestmentDomainStore(configured)


def hypothesis_lifecycle_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLHypothesisLifecycleStore(configured)


def investment_research_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLInvestmentResearchStore(configured)


def monitor_account_job_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLMonitorAccountJobStore(configured)
