import os
from typing import Dict

from .mysql_monitoring import MySQLMonitorAccountJobStore
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


def configured_settings(settings: Dict[str, str] = None) -> Dict[str, str]:
    return settings if settings is not None else runtime_settings()


def legacy_sqlite_backend_enabled(settings: Dict[str, str] = None) -> bool:
    configured = configured_settings(settings)
    backend = str(configured.get("operationalDbBackend") or os.environ.get("OPERATIONAL_DB_BACKEND") or "").strip().lower()
    return backend == "sqlite"


def use_mysql(settings: Dict[str, str] = None) -> bool:
    return not legacy_sqlite_backend_enabled(settings)


def _legacy_sqlite_store(module_name: str, class_name: str):
    module = __import__("digital_twin.infrastructure." + module_name, fromlist=[class_name])
    return getattr(module, class_name)


def runtime_settings_store(settings: Dict[str, str] = None):
    if legacy_sqlite_backend_enabled(settings or {}):
        return _legacy_sqlite_store("sqlite_runtime", "SQLiteRuntimeSettingsStore")()
    return MySQLRuntimeSettingsStore(settings)


def account_registry(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_accounts", "AccountRegistry")()
    return MySQLAccountRegistry(configured)


def app_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_runtime", "SQLiteAppStore")()
    return MySQLAppStore(configured)


def external_signal_cache(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_monitoring", "SQLiteExternalSignalCache")()
    return MySQLExternalSignalCache(configured)


def ontology_reasoning_cursor_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_monitoring", "SQLiteOntologyReasoningCursorStore")()
    return MySQLOntologyReasoningCursorStore(configured)


def monitor_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_monitoring", "SQLiteMonitorStore")()
    return MySQLMonitorStore(configured)


def monitoring_cycle_recorder(settings: Dict[str, str] = None, monitor_store_instance=None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_monitoring", "SQLiteMonitoringCycleRecorder")(monitor_store=monitor_store_instance)
    return MySQLMonitoringCycleRecorder(configured, monitor_store=monitor_store_instance)


def event_log(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_monitoring", "SQLiteEventLog")()
    return MySQLEventLog(configured)


def model_review_job_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_model_review", "SQLiteModelReviewJobStore")()
    return MySQLModelReviewJobStore(configured)


def notification_job_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_notifications", "SQLiteNotificationJobStore")()
    return MySQLNotificationJobStore(configured)


def notification_template_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_notifications", "SQLiteNotificationTemplateStore")()
    return MySQLNotificationTemplateStore(configured)


def notification_rule_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_notifications", "SQLiteNotificationRuleStore")()
    return MySQLNotificationRuleStore(configured)


def market_quote_cache(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_monitoring", "SQLiteMarketQuoteCache")()
    return MySQLMarketQuoteCache(configured)


def symbol_universe_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_symbols", "SQLiteSymbolUniverseStore")()
    return MySQLSymbolUniverseStore(configured)


def research_evidence_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_monitoring", "SQLiteResearchEvidenceStore")()
    return MySQLResearchEvidenceStore(configured)


def ontology_quality_sample_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        return _legacy_sqlite_store("sqlite_monitoring", "SQLiteOntologyQualitySampleStore")()
    return MySQLOntologyQualitySampleStore(configured)


def monitor_account_job_store(settings: Dict[str, str] = None):
    configured = configured_settings(settings)
    if legacy_sqlite_backend_enabled(configured):
        enabled = str(configured.get("monitorAccountQueueEnabled") or "").strip().lower()
        if enabled in {"1", "true", "yes", "on"}:
            return _legacy_sqlite_store("sqlite_monitoring", "SQLiteMonitorAccountJobStore")()
        return None
    return MySQLMonitorAccountJobStore(configured)
