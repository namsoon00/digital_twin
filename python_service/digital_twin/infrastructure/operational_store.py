from typing import Dict


def configured_settings(settings: Dict[str, str] = None) -> Dict[str, str]:
    if settings is not None:
        return settings
    from .settings import runtime_settings

    return runtime_settings()


def use_mysql(settings: Dict[str, str] = None) -> bool:
    return True


def runtime_settings_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_operational_core_stores import MySQLRuntimeSettingsStore

    return MySQLRuntimeSettingsStore(settings)


def runtime_checkpoint_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_runtime_checkpoints import MySQLRuntimeCheckpointStore

    return MySQLRuntimeCheckpointStore(configured_settings(settings))


def account_registry(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.account_transactions import MySQLAccountRegistry

    configured = configured_settings(settings)
    return MySQLAccountRegistry(configured)


def account_reader(settings: Dict[str, str] = None):
    from digital_twin.modules.accounts.infrastructure.mysql_account_reader import MySQLAccountReader

    return MySQLAccountReader(configured_settings(settings))


def account_watchlist_repository(settings: Dict[str, str] = None):
    from digital_twin.modules.instruments.infrastructure.mysql_account_watchlist import MySQLAccountWatchlistRepository

    configured = configured_settings(settings)
    return MySQLAccountWatchlistRepository(configured, account_reader(configured))


def app_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_operational_core_stores import MySQLAppStore

    configured = configured_settings(settings)
    return MySQLAppStore(configured)


def external_signal_cache(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_operational_core_stores import MySQLExternalSignalCache

    configured = configured_settings(settings)
    return MySQLExternalSignalCache(configured)


def external_data_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.external_api.mysql_stores import MySQLExternalDataStore

    configured = configured_settings(settings)
    return MySQLExternalDataStore(configured)


def external_evidence_projection_state_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_operational_core_stores import MySQLExternalEvidenceProjectionStateStore

    configured = configured_settings(settings)
    return MySQLExternalEvidenceProjectionStateStore(configured)


def company_knowledge_cache(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_operational_core_stores import MySQLCompanyKnowledgeCache

    configured = configured_settings(settings)
    return MySQLCompanyKnowledgeCache(configured)


def crypto_market_signal_cache(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_operational_core_stores import MySQLCryptoMarketSignalCache

    configured = configured_settings(settings)
    return MySQLCryptoMarketSignalCache(configured)


def data_pipeline_health_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_operational_core_stores import MySQLDataPipelineHealthStore

    configured = configured_settings(settings)
    return MySQLDataPipelineHealthStore(configured)


def news_digest_reconciliation_state_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_operational_core_stores import MySQLNewsDigestReconciliationStateStore

    configured = configured_settings(settings)
    return MySQLNewsDigestReconciliationStateStore(configured)


def operational_storage_capacity_state_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_operational_core_stores import MySQLOperationalStorageCapacityStateStore

    configured = configured_settings(settings)
    return MySQLOperationalStorageCapacityStateStore(configured)


def ontology_reasoning_cursor_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_operational_core_stores import MySQLOntologyReasoningCursorStore

    configured = configured_settings(settings)
    return MySQLOntologyReasoningCursorStore(configured)


def ontology_maintenance_state_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_operational_core_stores import MySQLOntologyMaintenanceStateStore

    configured = configured_settings(settings)
    return MySQLOntologyMaintenanceStateStore(configured)


def ontology_world_projection_state_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_operational_core_stores import MySQLOntologyWorldProjectionStateStore

    configured = configured_settings(settings)
    return MySQLOntologyWorldProjectionStateStore(configured)


def ontology_inference_detail_state_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_operational_core_stores import MySQLOntologyInferenceDetailStateStore

    configured = configured_settings(settings)
    return MySQLOntologyInferenceDetailStateStore(configured)


def ontology_reasoning_mailbox_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_reasoning_mailbox import MySQLOntologyReasoningMailboxStore

    configured = configured_settings(settings)
    return MySQLOntologyReasoningMailboxStore(configured)


def monitor_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_monitoring_stores import MySQLMonitorStore

    configured = configured_settings(settings)
    return MySQLMonitorStore(configured)


def ontology_reasoning_monitor_store(settings: Dict[str, str] = None):
    """Return the read-only, target-scoped monitor source for TypeDB replay."""
    from digital_twin.infrastructure.mysql_monitoring_stores import MySQLOntologyReasoningMonitorStore

    configured = configured_settings(settings)
    return MySQLOntologyReasoningMonitorStore(configured)


def monitoring_cycle_recorder(
    settings: Dict[str, str] = None,
    monitor_store_instance=None,
    market_time_series_store_instance=None,
):
    from digital_twin.infrastructure.mysql_monitoring_stores import MySQLMonitoringCycleRecorder

    configured = configured_settings(settings)
    return MySQLMonitoringCycleRecorder(
        configured,
        monitor_store=monitor_store_instance,
        market_time_series_store=market_time_series_store_instance,
    )


def event_log(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_monitoring_stores import MySQLEventLog

    configured = configured_settings(settings)
    return MySQLEventLog(configured)


def model_review_job_store(settings: Dict[str, str] = None):
    from digital_twin.modules.model_registry.infrastructure.mysql_model_review_jobs import MySQLModelReviewJobStore

    configured = configured_settings(settings)
    return MySQLModelReviewJobStore(configured)


def historical_replay_job_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_historical_replay_jobs import MySQLHistoricalReplayJobStore

    configured = configured_settings(settings)
    return MySQLHistoricalReplayJobStore(configured)


def notification_job_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore

    configured = configured_settings(settings)
    return MySQLNotificationJobStore(configured)


def ai_inference_queue_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_ai_inference_queue import MySQLAIInferenceQueueStore

    configured = configured_settings(settings)
    return MySQLAIInferenceQueueStore(configured)


def notification_template_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_notification_config import MySQLNotificationTemplateStore

    configured = configured_settings(settings)
    return MySQLNotificationTemplateStore(configured)


def notification_rule_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_notification_config import MySQLNotificationRuleStore

    configured = configured_settings(settings)
    return MySQLNotificationRuleStore(configured)


def market_quote_cache(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_market_quotes import MySQLMarketQuoteCache

    configured = configured_settings(settings)
    return MySQLMarketQuoteCache(configured)


def market_time_series_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    from .time_series_factory import build_versioned_time_series_store
    return build_versioned_time_series_store(configured)


def raw_mysql_market_time_series_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_market_time_series import MySQLMarketTimeSeriesStore

    configured = configured_settings(settings)
    return MySQLMarketTimeSeriesStore(configured)


def time_series_backend_registry_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_versioned_runtime import MySQLTimeSeriesBackendRegistryStore

    return MySQLTimeSeriesBackendRegistryStore(configured_settings(settings))


def time_series_projection_outbox_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_versioned_runtime import MySQLTimeSeriesProjectionOutboxStore

    return MySQLTimeSeriesProjectionOutboxStore(configured_settings(settings))


def temporal_feature_snapshot_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_versioned_runtime import MySQLTemporalFeatureSnapshotStore

    return MySQLTemporalFeatureSnapshotStore(configured_settings(settings))


def statistical_model_signal_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_statistical_signals import MySQLStatisticalModelSignalStore

    return MySQLStatisticalModelSignalStore(configured_settings(settings))


def reasoning_engine_registry_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_versioned_runtime import MySQLReasoningEngineRegistryStore

    return MySQLReasoningEngineRegistryStore(configured_settings(settings))


def reasoning_engine_comparison_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_versioned_runtime import MySQLReasoningEngineComparisonStore

    return MySQLReasoningEngineComparisonStore(configured_settings(settings))


def reasoning_engine_job_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_versioned_runtime import MySQLReasoningEngineJobStore

    return MySQLReasoningEngineJobStore(configured_settings(settings))


def shared_instrument_inference_store(settings: Dict[str, str] = None):
    from digital_twin.modules.reasoning.infrastructure.mysql_shared_instrument_inference import MySQLSharedInstrumentInferenceStore

    return MySQLSharedInstrumentInferenceStore(configured_settings(settings))


def investment_reasoning_case_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_investment_reasoning_cases import MySQLInvestmentReasoningCaseStore

    return MySQLInvestmentReasoningCaseStore(configured_settings(settings))


def subject_decision_case_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_subject_decision_cases import MySQLSubjectDecisionCaseStore

    return MySQLSubjectDecisionCaseStore(configured_settings(settings))


def reasoning_shadow_job_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_versioned_runtime import MySQLReasoningShadowJobStore

    return MySQLReasoningShadowJobStore(configured_settings(settings))


def market_observation_reasoning_anchor_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_monitoring_stores import MySQLMarketObservationReasoningAnchorStore

    configured = configured_settings(settings)
    return MySQLMarketObservationReasoningAnchorStore(configured)


def investment_alert_coverage_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_investment_alert_coverage import MySQLInvestmentAlertCoverageStore

    return MySQLInvestmentAlertCoverageStore(configured_settings(settings))


def symbol_universe_store(settings: Dict[str, str] = None):
    from digital_twin.modules.instruments.infrastructure.mysql_symbol_universe import MySQLSymbolUniverseStore

    configured = configured_settings(settings)
    return MySQLSymbolUniverseStore(configured)


def research_evidence_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_research_evidence import MySQLResearchEvidenceStore

    configured = configured_settings(settings)
    return MySQLResearchEvidenceStore(configured)


def investment_calendar_store(settings: Dict[str, str] = None):
    from digital_twin.modules.investment_calendar.infrastructure.mysql_investment_calendar import MySQLInvestmentCalendarStore

    configured = configured_settings(settings)
    return MySQLInvestmentCalendarStore(configured)


def reasoning_source_fact_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_reasoning_source_facts import MySQLReasoningSourceFactStore

    return MySQLReasoningSourceFactStore(configured_settings(settings))


def investment_calendar_candidate_store(settings: Dict[str, str] = None):
    from digital_twin.modules.investment_calendar.infrastructure.mysql_investment_calendar_candidates import MySQLInvestmentCalendarCandidateStore

    configured = configured_settings(settings)
    return MySQLInvestmentCalendarCandidateStore(configured)


def ontology_quality_sample_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_ontology_quality import MySQLOntologyQualitySampleStore

    configured = configured_settings(settings)
    return MySQLOntologyQualitySampleStore(configured)


def ontology_projection_run_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_ontology_projection_runs import MySQLOntologyProjectionRunStore

    configured = configured_settings(settings)
    return MySQLOntologyProjectionRunStore(configured)


def ontology_graph_assembly_cache_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_ontology_graph_assembly_cache import MySQLOntologyGraphAssemblyCacheStore

    configured = configured_settings(settings)
    return MySQLOntologyGraphAssemblyCacheStore(configured)


def ontology_world_projection_outbox_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_ontology_world_projection_outbox import MySQLOntologyWorldProjectionOutboxStore

    configured = configured_settings(settings)
    return MySQLOntologyWorldProjectionOutboxStore(configured)


def ontology_inference_detail_outbox_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_ontology_inference_detail_outbox import MySQLOntologyInferenceDetailOutboxStore

    configured = configured_settings(settings)
    return MySQLOntologyInferenceDetailOutboxStore(configured)


def ontology_experiment_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_hypothesis_development import MySQLOntologyExperimentStore

    configured = configured_settings(settings)
    from .settings import data_dir
    return MySQLOntologyExperimentStore(configured, legacy_path=data_dir() / "ontology-lab.json")


def hypothesis_development_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_hypothesis_development import MySQLHypothesisDevelopmentStore

    configured = configured_settings(settings)
    return MySQLHypothesisDevelopmentStore(configured)


def investment_strategy_proposal_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_investment_strategy_proposals import MySQLInvestmentStrategyProposalStore

    configured = configured_settings(settings)
    return MySQLInvestmentStrategyProposalStore(configured)


def investment_decision_episode_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_investment_decision_episodes import MySQLInvestmentDecisionEpisodeStore

    configured = configured_settings(settings)
    return MySQLInvestmentDecisionEpisodeStore(configured)


def investment_domain_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_investment_domain import MySQLInvestmentDomainStore

    configured = configured_settings(settings)
    return MySQLInvestmentDomainStore(configured)


def hypothesis_lifecycle_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_hypothesis_lifecycle import MySQLHypothesisLifecycleStore

    configured = configured_settings(settings)
    return MySQLHypothesisLifecycleStore(configured)


def investment_research_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_investment_research import MySQLInvestmentResearchStore

    configured = configured_settings(settings)
    return MySQLInvestmentResearchStore(configured)


def monitor_account_job_store(settings: Dict[str, str] = None):
    from digital_twin.infrastructure.mysql_monitoring import MySQLMonitorAccountJobStore

    configured = configured_settings(settings)
    return MySQLMonitorAccountJobStore(configured)
