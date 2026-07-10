import os
from typing import Dict

from .mysql_monitoring import MySQLMonitorAccountJobStore, mysql_backend_enabled
from .mysql_operational import (
    MySQLAccountRegistry,
    MySQLAppStore,
    MySQLEventLog,
    MySQLExternalSignalCache,
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
from .sqlite_accounts import AccountRegistry
from .sqlite_model_review import SQLiteModelReviewJobStore
from .sqlite_monitoring import (
    SQLiteEventLog,
    SQLiteExternalSignalCache,
    SQLiteMarketQuoteCache,
    SQLiteMonitorAccountJobStore,
    SQLiteMonitoringCycleRecorder,
    SQLiteMonitorStore,
    SQLiteOntologyQualitySampleStore,
    SQLiteOntologyReasoningCursorStore,
    SQLiteResearchEvidenceStore,
)
from .sqlite_notifications import SQLiteNotificationJobStore, SQLiteNotificationRuleStore, SQLiteNotificationTemplateStore
from .sqlite_runtime import SQLiteAppStore, SQLiteRuntimeSettingsStore
from .sqlite_symbols import SQLiteSymbolUniverseStore


def configured_settings(settings: Dict[str, str] = None) -> Dict[str, str]:
    return settings if settings is not None else runtime_settings()


def use_mysql(settings: Dict[str, str] = None) -> bool:
    return mysql_backend_enabled(configured_settings(settings))


def runtime_settings_store(settings: Dict[str, str] = None):
    if mysql_backend_enabled(settings or {}):
        return MySQLRuntimeSettingsStore(settings)
    return SQLiteRuntimeSettingsStore()


def account_registry(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLAccountRegistry(configured) if mysql_backend_enabled(configured) else AccountRegistry()


def app_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLAppStore(configured) if mysql_backend_enabled(configured) else SQLiteAppStore()


def external_signal_cache(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLExternalSignalCache(configured) if mysql_backend_enabled(configured) else SQLiteExternalSignalCache()


def ontology_reasoning_cursor_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOntologyReasoningCursorStore(configured) if mysql_backend_enabled(configured) else SQLiteOntologyReasoningCursorStore()


def monitor_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLMonitorStore(configured) if mysql_backend_enabled(configured) else SQLiteMonitorStore()


def monitoring_cycle_recorder(settings: Dict[str, str] = None, monitor_store_instance=None):
    configured = configured_settings(settings)
    if mysql_backend_enabled(configured):
        return MySQLMonitoringCycleRecorder(configured, monitor_store=monitor_store_instance)
    return SQLiteMonitoringCycleRecorder(monitor_store=monitor_store_instance)


def event_log(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLEventLog(configured) if mysql_backend_enabled(configured) else SQLiteEventLog()


def model_review_job_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLModelReviewJobStore(configured) if mysql_backend_enabled(configured) else SQLiteModelReviewJobStore()


def notification_job_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLNotificationJobStore(configured) if mysql_backend_enabled(configured) else SQLiteNotificationJobStore()


def notification_template_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLNotificationTemplateStore(configured) if mysql_backend_enabled(configured) else SQLiteNotificationTemplateStore()


def notification_rule_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLNotificationRuleStore(configured) if mysql_backend_enabled(configured) else SQLiteNotificationRuleStore()


def market_quote_cache(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLMarketQuoteCache(configured) if mysql_backend_enabled(configured) else SQLiteMarketQuoteCache()


def symbol_universe_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLSymbolUniverseStore(configured) if mysql_backend_enabled(configured) else SQLiteSymbolUniverseStore()


def research_evidence_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLResearchEvidenceStore(configured) if mysql_backend_enabled(configured) else SQLiteResearchEvidenceStore()


def ontology_quality_sample_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    return MySQLOntologyQualitySampleStore(configured) if mysql_backend_enabled(configured) else SQLiteOntologyQualitySampleStore()


def monitor_account_job_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if mysql_backend_enabled(configured):
        return MySQLMonitorAccountJobStore(configured)
    enabled = str(configured.get("monitorAccountQueueEnabled") or os.environ.get("MONITOR_ACCOUNT_QUEUE_ENABLED") or "").strip().lower()
    if enabled in {"1", "true", "yes", "on"}:
        return SQLiteMonitorAccountJobStore()
    return None
