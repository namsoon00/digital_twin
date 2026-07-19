from typing import Dict

from .mysql_monitoring import MySQLMonitorAccountJobStore
from .ontology_lab_store import JsonOntologyExperimentStore
from .mysql_operational import (
    MySQLAccountRegistry,
    MySQLAppStore,
    MySQLEventLog,
    MySQLExternalSignalCache,
    MySQLInvestmentCalendarCandidateStore,
    MySQLInvestmentCalendarStore,
    MySQLInvestmentStrategyProposalStore,
    MySQLInvestmentDecisionEpisodeStore,
    MySQLInvestmentResearchStore,
    MySQLMarketQuoteCache,
    MySQLModelReviewJobStore,
    MySQLMonitorStore,
    MySQLMonitoringCycleRecorder,
    MySQLNotificationJobStore,
    MySQLNotificationRuleStore,
    MySQLNotificationTemplateStore,
    MySQLOntologyQualitySampleStore,
    MySQLOntologyReasoningCursorStore,
    MySQLResearchEvidenceStore,
    MySQLRuntimeSettingsStore,
    MySQLSymbolUniverseStore,
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


def ontology_reasoning_cursor_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOntologyReasoningCursorStore(configured)


def monitor_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLMonitorStore(configured)


def monitoring_cycle_recorder(settings: Dict[str, str] = None, monitor_store_instance=None):
    configured = configured_settings(settings)
    return MySQLMonitoringCycleRecorder(configured, monitor_store=monitor_store_instance)


def event_log(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLEventLog(configured)


def model_review_job_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLModelReviewJobStore(configured)


def notification_job_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLNotificationJobStore(configured)


def notification_template_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLNotificationTemplateStore(configured)


def notification_rule_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLNotificationRuleStore(configured)


def market_quote_cache(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLMarketQuoteCache(configured)


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


def ontology_experiment_store(settings: Dict[str, str] = None):
    return JsonOntologyExperimentStore()


def investment_strategy_proposal_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLInvestmentStrategyProposalStore(configured)


def investment_decision_episode_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLInvestmentDecisionEpisodeStore(configured)


def investment_research_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLInvestmentResearchStore(configured)


def monitor_account_job_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLMonitorAccountJobStore(configured)
