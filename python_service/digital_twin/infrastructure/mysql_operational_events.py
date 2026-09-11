import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from digital_twin.modules.accounts.domain.accounts import AccountConfig, split_symbols
from digital_twin.modules.market_data.domain.data_freshness import evaluate_notification_data_freshness
from digital_twin.shared_kernel.events import DomainEvent
from digital_twin.modules.market_data.domain.events import alerts_detected_event, monitoring_cycle_completed_event, snapshot_collected_event
from digital_twin.platform.domain.events import domain_event_storage_payload
from digital_twin.modules.reasoning.domain.fact_changes import fact_signature, research_evidence_fact_payload
from digital_twin.modules.news_intelligence.domain.investment_research import ResearchEvidence
from digital_twin.modules.model_registry.contracts import ModelReviewJob
from digital_twin.modules.notifications.domain.notification_rules import DEFAULT_NOTIFICATION_RULES, NotificationRuleConfig, apply_market_hours_rule, apply_similarity_rule, apply_state_cooldown_rule, default_notification_rule, evaluate_notification_rule, notification_fingerprint
from digital_twin.modules.notifications.domain.notification_templates import DEFAULT_NOTIFICATION_TEMPLATES, NotificationTemplate, alert_context, render_notification
from digital_twin.modules.notifications.domain.notifications import NotificationJob, notification_debug_number
from digital_twin.modules.reasoning.domain.ontology_quality import OntologyQualitySample, build_ontology_quality_sample
from digital_twin.modules.portfolio.domain.portfolio import AccountSnapshot, AlertEvent
from digital_twin.modules.portfolio.contracts import MonitoringCycleRecordResult
from digital_twin.modules.instruments.contracts import ListedSymbol, normalize_market, normalize_symbol, utc_now_iso as symbol_utc_now_iso
from digital_twin.modules.model_registry.infrastructure.model_review_queue import model_review_payloads_from_event
from .mysql_monitoring import MySQLDependencyError, MySQLMonitorAccountJobStore, ensure_mysql_database_exists, mysql_settings
from .operational_common import (
    MAX_NOTIFICATION_DELIVERY_ATTEMPTS,
    NOTIFICATION_HISTORY_LOOKBACK_LIMIT,
    age_minutes_since,
    json_dumps,
    notification_history_is_recent_in_flight,
    research_evidence_from_row,
    rule_from_row,
    template_from_row,
)
from .settings import read_json, settings_path, utc_now
from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore
from .mysql_operational_connection import MYSQL_SCHEMA, MySQLConnectionProxy, MySQLOperationalConnection
from .mysql_operational_helpers import (
    _is_duplicate_key_error,
    _json_loads,
    _sent_key_hash,
    research_evidence_change_payload,
)


def insert_domain_event_with_connection(connection, event: DomainEvent) -> None:
    # ``payload_json`` is the canonical payload. Keeping it again inside
    # ``event_json`` doubled the largest monitoring rows and amplified every
    # ABox cycle. Readers merge the two columns for backward compatibility.
    event_metadata = event.to_dict()
    event_metadata.pop("payload", None)
    payload = domain_event_storage_payload(event.name, event.payload)
    connection.execute(
        """
        INSERT IGNORE INTO domain_events (
            event_id, name, aggregate_id, occurred_at, correlation_id, payload_json, event_json
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event.event_id,
            event.name,
            str(event.aggregate_id or "")[:191],
            event.occurred_at,
            event.correlation_id,
            json_dumps(payload),
            json_dumps(event_metadata),
        ),
    )


def domain_event_from_row(row) -> DomainEvent:
    event_payload = _json_loads(row.get("event_json"), {})
    if not event_payload.get("payload"):
        event_payload["payload"] = _json_loads(row.get("payload_json"), {})
    event_payload.setdefault("eventId", row.get("event_id"))
    event_payload.setdefault("name", row.get("name"))
    event_payload.setdefault("aggregateId", row.get("aggregate_id"))
    event_payload.setdefault("occurredAt", row.get("occurred_at"))
    event_payload.setdefault("correlationId", row.get("correlation_id"))
    return DomainEvent.from_dict(event_payload)
