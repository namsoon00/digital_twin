import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from ..domain.accounts import AccountConfig, split_symbols
from ..domain.data_freshness import evaluate_notification_data_freshness
from ..domain.events import (
    DomainEvent,
    alerts_detected_event,
    monitoring_cycle_completed_event,
    snapshot_collected_event,
)
from ..domain.fact_changes import fact_signature, research_evidence_fact_payload
from ..domain.investment_research import ResearchEvidence
from ..domain.model_review import ModelReviewJob
from ..domain.notification_rules import (
    DEFAULT_NOTIFICATION_RULES,
    NotificationRuleConfig,
    apply_market_hours_rule,
    apply_similarity_rule,
    apply_state_cooldown_rule,
    default_notification_rule,
    evaluate_notification_rule,
    notification_fingerprint,
)
from ..domain.notification_templates import DEFAULT_NOTIFICATION_TEMPLATES, NotificationTemplate, alert_context, render_notification
from ..domain.notifications import NotificationJob, notification_debug_number
from ..domain.ontology_quality import OntologyQualitySample, build_ontology_quality_sample
from ..domain.portfolio import AccountSnapshot, AlertEvent
from ..domain.repositories import MonitoringCycleRecordResult
from ..domain.symbol_universe import ListedSymbol, normalize_market, normalize_symbol, utc_now_iso as symbol_utc_now_iso
from .model_review_queue import model_review_payloads_from_event
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
from .mysql_notification_jobs import MySQLNotificationJobStore
from .mysql_operational_connection import MYSQL_SCHEMA, MySQLConnectionProxy, MySQLOperationalConnection
from .mysql_operational_events import insert_domain_event_with_connection
from .mysql_operational_helpers import (
    _is_duplicate_key_error,
    _json_loads,
    _sent_key_hash,
    research_evidence_change_payload,
)


class MySQLOntologyQualitySampleStore(MySQLOperationalConnection):
    def record(self, sample: OntologyQualitySample) -> None:
        stamp = sample.created_at or utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO ontology_ai_opinion_samples (
                    sample_id, portfolio_id, created_at, overall_state, data_state,
                    context_state, reasoning_state, relation_state, validation_state,
                    entity_count, relation_count, evidence_count, belief_count, opinion_count,
                    reasoning_card_count, data_gap_count, bounded_context_count, action_required_count,
                    payload_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    portfolio_id = VALUES(portfolio_id),
                    created_at = VALUES(created_at),
                    overall_state = VALUES(overall_state),
                    data_state = VALUES(data_state),
                    context_state = VALUES(context_state),
                    reasoning_state = VALUES(reasoning_state),
                    relation_state = VALUES(relation_state),
                    validation_state = VALUES(validation_state),
                    entity_count = VALUES(entity_count),
                    relation_count = VALUES(relation_count),
                    evidence_count = VALUES(evidence_count),
                    belief_count = VALUES(belief_count),
                    opinion_count = VALUES(opinion_count),
                    reasoning_card_count = VALUES(reasoning_card_count),
                    data_gap_count = VALUES(data_gap_count),
                    bounded_context_count = VALUES(bounded_context_count),
                    action_required_count = VALUES(action_required_count),
                    payload_json = VALUES(payload_json)
                """,
                (
                    sample.sample_id,
                    sample.portfolio_id,
                    stamp,
                    sample.overall_state,
                    sample.data_state,
                    sample.context_state,
                    sample.reasoning_state,
                    sample.relation_state,
                    sample.validation_state,
                    int(sample.entity_count or 0),
                    int(sample.relation_count or 0),
                    int(sample.evidence_count or 0),
                    int(sample.belief_count or 0),
                    int(sample.opinion_count or 0),
                    int(sample.reasoning_card_count or 0),
                    int(sample.data_gap_count or 0),
                    int(sample.bounded_context_count or 0),
                    int(sample.action_required_count or 0),
                    json_dumps(sample.payload),
                ),
            )

    def record_graph(self, graph, source: str = "monitoring", created_at: str = "") -> OntologyQualitySample:
        sample = build_ontology_quality_sample(graph, source=source, created_at=created_at)
        self.record(sample)
        return sample

    def latest(self, portfolio_id: str = "", limit: int = 20) -> List[Dict[str, object]]:
        clauses = []
        params: List[object] = []
        if portfolio_id:
            clauses.append("portfolio_id = %s")
            params.append(str(portfolio_id))
        sql = """
            SELECT sample_id, portfolio_id, created_at, overall_state, data_state,
                   context_state, reasoning_state, relation_state, validation_state,
                   entity_count, relation_count, evidence_count, belief_count, opinion_count,
                   reasoning_card_count, data_gap_count, bounded_context_count, action_required_count,
                   payload_json
            FROM ontology_ai_opinion_samples
        """
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(max(1, min(200, int(limit or 20))))
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        result = []
        for row in rows:
            result.append({
                "sampleId": row["sample_id"],
                "portfolioId": row["portfolio_id"],
                "createdAt": row["created_at"],
                "overallState": row["overall_state"],
                "dataState": row["data_state"],
                "contextState": row["context_state"],
                "reasoningState": row["reasoning_state"],
                "relationState": row["relation_state"],
                "validationState": row["validation_state"],
                "entityCount": row["entity_count"],
                "relationCount": row["relation_count"],
                "evidenceCount": row["evidence_count"],
                "beliefCount": row["belief_count"],
                "opinionCount": row["opinion_count"],
                "reasoningCardCount": row["reasoning_card_count"],
                "dataGapCount": row["data_gap_count"],
                "boundedContextCount": row["bounded_context_count"],
                "actionRequiredCount": row["action_required_count"],
                "payload": _json_loads(row["payload_json"], {}),
            })
        return result
