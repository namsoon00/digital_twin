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
    attach_previous_profit_loss_context,
    apply_market_hours_rule,
    apply_similarity_rule,
    apply_state_cooldown_rule,
    default_notification_rule,
    evaluate_notification_rule,
    notification_fingerprint,
)
from ..domain.notification_templates import (
    DEFAULT_NOTIFICATION_TEMPLATES,
    PREVIOUS_DEFAULT_TEMPLATE,
    NotificationTemplate,
    alert_context,
    render_notification,
)
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


class MySQLNotificationTemplateStore(MySQLOperationalConnection):
    def __init__(self, settings: Dict[str, str] = None):
        super().__init__(settings)
        self.seed_defaults()

    def seed_defaults(self) -> None:
        stamp = utc_now()
        with self.transaction() as connection:
            for message_type, payload in DEFAULT_NOTIFICATION_TEMPLATES.items():
                default_template = str(payload.get("template") or "")
                default_description = str(payload.get("description") or "")
                connection.execute(
                    """
                    INSERT IGNORE INTO notification_templates (message_type, template, description, enabled, updated_at)
                    VALUES (%s, %s, %s, 1, %s)
                    """,
                    (message_type, default_template, default_description, stamp),
                )
                row = connection.execute(
                    "SELECT template FROM notification_templates WHERE message_type = %s",
                    (message_type,),
                ).fetchone()
                if row and str(row.get("template") or "") == PREVIOUS_DEFAULT_TEMPLATE and default_template != PREVIOUS_DEFAULT_TEMPLATE:
                    connection.execute(
                        """
                        UPDATE notification_templates
                        SET template = %s, description = %s, enabled = 1, updated_at = %s
                        WHERE message_type = %s
                        """,
                        (default_template, default_description, stamp, message_type),
                    )

    def list(self) -> List[NotificationTemplate]:
        with self.connect() as connection:
            rows = connection.execute("SELECT message_type, template, description, enabled, updated_at FROM notification_templates ORDER BY message_type").fetchall()
        return [template_from_row(row) for row in rows]

    def get(self, message_type: str) -> NotificationTemplate:
        key = str(message_type or "notification").strip() or "notification"
        with self.connect() as connection:
            row = connection.execute(
                "SELECT message_type, template, description, enabled, updated_at FROM notification_templates WHERE message_type = %s",
                (key,),
            ).fetchone()
            if not row:
                row = connection.execute(
                    "SELECT message_type, template, description, enabled, updated_at FROM notification_templates WHERE message_type = 'default'"
                ).fetchone()
        return template_from_row(row) if row else NotificationTemplate.default("default")

    def upsert(self, message_type: str, template: str, description: str = "", enabled: bool = True) -> NotificationTemplate:
        key = str(message_type or "").strip()
        if not key:
            raise ValueError("message_type is required")
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO notification_templates (message_type, template, description, enabled, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE template = VALUES(template), description = VALUES(description),
                    enabled = VALUES(enabled), updated_at = VALUES(updated_at)
                """,
                (key, str(template or ""), str(description or ""), 1 if enabled else 0, stamp),
            )
        return self.get(key)

    def reset(self, message_type: str) -> NotificationTemplate:
        key = str(message_type or "").strip() or "default"
        configured = DEFAULT_NOTIFICATION_TEMPLATES.get(key) or DEFAULT_NOTIFICATION_TEMPLATES["default"]
        return self.upsert(key, configured["template"], configured.get("description", ""), True)

    def render(self, message_type: str, context: Dict[str, object]) -> str:
        return render_notification(self.get(message_type), context)

    def render_job(self, job: NotificationJob) -> str:
        context = dict(job.context or {})
        context.setdefault("body", job.text)
        context.setdefault("messageType", job.message_type)
        context.setdefault("accountId", job.account_id)
        context.setdefault("accountLabel", job.account_label)
        context.setdefault("jobId", job.job_id)
        context.setdefault("notificationNumber", notification_debug_number(job.job_id))
        return self.render(job.message_type, context)

class MySQLNotificationRuleStore(MySQLOperationalConnection):
    def __init__(self, settings: Dict[str, str] = None, seed_defaults: bool = True):
        super().__init__(settings)
        if seed_defaults:
            self.seed_defaults()

    def seed_defaults(self) -> None:
        stamp = utc_now()
        with self.transaction() as connection:
            for message_type, rule in DEFAULT_NOTIFICATION_RULES.items():
                connection.execute(
                    """
                    INSERT IGNORE INTO notification_rules (
                        message_type, enabled, threshold, base_score, low_score_action, conditions_json,
                        similarity_enabled, similarity_window_minutes, similarity_penalty, similarity_bypass_score_delta,
                        similarity_bypass_conditions_json, similarity_fields_json, state_cooldown_enabled,
                        state_cooldown_minutes, market_hours_enabled, market_hours_markets_json, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        message_type,
                        1 if rule.enabled else 0,
                        int(rule.threshold),
                        int(rule.base_score),
                        rule.low_score_action,
                        json_dumps([condition.to_dict() for condition in rule.conditions]),
                        1 if rule.similarity_enabled else 0,
                        int(rule.similarity_window_minutes),
                        int(rule.similarity_penalty),
                        int(rule.similarity_bypass_score_delta),
                        json_dumps([condition.to_dict() for condition in rule.similarity_bypass_conditions]),
                        json_dumps(rule.similarity_fields),
                        1 if rule.state_cooldown_enabled else 0,
                        int(rule.state_cooldown_minutes),
                        1 if rule.market_hours_enabled else 0,
                        json_dumps(rule.market_hours_markets),
                        stamp,
                    ),
                )
                row = connection.execute("SELECT * FROM notification_rules WHERE message_type = %s", (message_type,)).fetchone()
                if row:
                    current = rule_from_row(row)
                    migrated_conditions = self._migrate_default_conditions(current, rule)
                    migrated_similarity = self._migrate_default_similarity(current, rule)
                    set_clauses = []
                    params = []
                    if migrated_conditions:
                        set_clauses.append("conditions_json = %s")
                        params.append(json_dumps([condition.to_dict() for condition in current.conditions]))
                    if migrated_similarity:
                        set_clauses.append("similarity_fields_json = %s")
                        params.append(json_dumps(current.similarity_fields))
                        set_clauses.append("similarity_bypass_conditions_json = %s")
                        params.append(json_dumps([condition.to_dict() for condition in current.similarity_bypass_conditions]))
                    if current.market_hours_enabled != rule.market_hours_enabled:
                        set_clauses.append("market_hours_enabled = %s")
                        params.append(1 if rule.market_hours_enabled else 0)
                    if set_clauses:
                        set_clauses.append("updated_at = %s")
                        params.append(stamp)
                        params.append(message_type)
                        connection.execute(
                            "UPDATE notification_rules SET " + ", ".join(set_clauses) + " WHERE message_type = %s",
                            params,
                        )

    def _migrate_default_conditions(self, current: NotificationRuleConfig, default_rule: NotificationRuleConfig) -> bool:
        defaults = {condition.condition_id: condition for condition in default_rule.conditions}
        changed = False
        for condition in current.conditions:
            default = defaults.get(condition.condition_id)
            if not default:
                continue
            if default.condition_type != "context_contains_any" or default.field != "notificationSignals":
                continue
            if condition.condition_type == default.condition_type and condition.field == default.field and condition.terms == default.terms:
                continue
            condition.condition_type = default.condition_type
            condition.field = default.field
            condition.terms = list(default.terms or [])
            changed = True
        return changed

    def _migrate_default_similarity(self, current: NotificationRuleConfig, default_rule: NotificationRuleConfig) -> bool:
        changed = False
        legacy_fields = [
            "ontologyInsight.insightType" if field == "ontologyInsight.dispatchInsightType" else field
            for field in default_rule.similarity_fields
        ]
        legacy_dispatch_fields = [
            "messageType",
            "accountId",
            "ontologyInsight.subject",
            "ontologyInsight.dispatchInsightType",
        ]
        legacy_insight_fields = [
            "messageType",
            "accountId",
            "ontologyInsight.subject",
            "ontologyInsight.insightType",
        ]
        known_legacy_fields = [legacy_fields, legacy_dispatch_fields, legacy_insight_fields]
        if current.similarity_fields in known_legacy_fields and current.similarity_fields != default_rule.similarity_fields:
            current.similarity_fields = list(default_rule.similarity_fields)
            changed = True
        if str(current.message_type or "") == "investmentInsight":
            filtered_conditions = [
                condition
                for condition in current.similarity_bypass_conditions
                if condition.condition_id != "semantic_signature_changed"
            ]
            if len(filtered_conditions) != len(current.similarity_bypass_conditions):
                current.similarity_bypass_conditions = filtered_conditions
                changed = True
        defaults = {condition.condition_id: condition for condition in default_rule.similarity_bypass_conditions}
        current_ids = {condition.condition_id for condition in current.similarity_bypass_conditions}
        for condition in default_rule.similarity_bypass_conditions:
            if condition.condition_id not in current_ids:
                current.similarity_bypass_conditions.append(condition)
                changed = True
        for condition in current.similarity_bypass_conditions:
            default = defaults.get(condition.condition_id)
            if not default:
                continue
            legacy_field = "ontologyInsight.insightType" if default.field == "ontologyInsight.dispatchInsightType" else default.field
            if condition.condition_id == "insight_action_changed":
                legacy_field = "activeInvestmentOpinion.actionLabel,activeInvestmentOpinion.action,actionLabel,action,ontologyInsight.actionLabel,ontologyInsight.action"
            if condition.field == legacy_field and condition.field != default.field:
                condition.field = default.field
                changed = True
            if condition.condition_id in {
                "insight_profit_loss_worsened",
                "insight_profit_loss_improved",
                "loss_rate_worsened",
                "loss_rate_improved",
            }:
                if condition.value != default.value:
                    condition.value = default.value
                    changed = True
                if condition.label != default.label:
                    condition.label = default.label
                    changed = True
                if condition.description != default.description:
                    condition.description = default.description
                    changed = True
        return changed

    def list(self) -> List[NotificationRuleConfig]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM notification_rules ORDER BY message_type").fetchall()
        return [rule_from_row(row) for row in rows]

    def get(self, message_type: str) -> NotificationRuleConfig:
        key = str(message_type or "notification").strip() or "notification"
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM notification_rules WHERE message_type = %s", (key,)).fetchone()
        return rule_from_row(row) if row else default_notification_rule(key)

    def upsert(self, rule: NotificationRuleConfig) -> NotificationRuleConfig:
        normalized = NotificationRuleConfig.from_dict(rule.to_dict() if isinstance(rule, NotificationRuleConfig) else dict(rule or {}))
        normalized.message_type = str(normalized.message_type or "").strip()
        if not normalized.message_type:
            raise ValueError("message_type is required")
        normalized.updated_at = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO notification_rules (
                    message_type, enabled, threshold, base_score, low_score_action, conditions_json,
                    similarity_enabled, similarity_window_minutes, similarity_penalty, similarity_bypass_score_delta,
                    similarity_bypass_conditions_json, similarity_fields_json, state_cooldown_enabled,
                    state_cooldown_minutes, market_hours_enabled, market_hours_markets_json, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE enabled = VALUES(enabled), threshold = VALUES(threshold),
                    base_score = VALUES(base_score), low_score_action = VALUES(low_score_action),
                    conditions_json = VALUES(conditions_json), similarity_enabled = VALUES(similarity_enabled),
                    similarity_window_minutes = VALUES(similarity_window_minutes),
                    similarity_penalty = VALUES(similarity_penalty),
                    similarity_bypass_score_delta = VALUES(similarity_bypass_score_delta),
                    similarity_bypass_conditions_json = VALUES(similarity_bypass_conditions_json),
                    similarity_fields_json = VALUES(similarity_fields_json),
                    state_cooldown_enabled = VALUES(state_cooldown_enabled),
                    state_cooldown_minutes = VALUES(state_cooldown_minutes),
                    market_hours_enabled = VALUES(market_hours_enabled),
                    market_hours_markets_json = VALUES(market_hours_markets_json),
                    updated_at = VALUES(updated_at)
                """,
                (
                    normalized.message_type,
                    1 if normalized.enabled else 0,
                    int(normalized.threshold),
                    int(normalized.base_score),
                    normalized.low_score_action,
                    json_dumps([condition.to_dict() for condition in normalized.conditions]),
                    1 if normalized.similarity_enabled else 0,
                    int(normalized.similarity_window_minutes),
                    int(normalized.similarity_penalty),
                    int(normalized.similarity_bypass_score_delta),
                    json_dumps([condition.to_dict() for condition in normalized.similarity_bypass_conditions]),
                    json_dumps(normalized.similarity_fields),
                    1 if normalized.state_cooldown_enabled else 0,
                    int(normalized.state_cooldown_minutes),
                    1 if normalized.market_hours_enabled else 0,
                    json_dumps(normalized.market_hours_markets),
                    normalized.updated_at,
                ),
            )
        return self.get(normalized.message_type)

    def reset(self, message_type: str) -> NotificationRuleConfig:
        return self.upsert(default_notification_rule(str(message_type or "notification").strip() or "notification"))

    def similar_history(self, job: NotificationJob, rule: NotificationRuleConfig, fingerprint: str):
        return MySQLNotificationJobStore(self.runtime_settings).similar_history_for_rule(job, rule, fingerprint)

    def evaluate_job(self, job: NotificationJob):
        rule = self.get(job.message_type)
        decision = evaluate_notification_rule(job, rule)
        recent_count, previous_score, previous_context, last_sent_at = self.similar_history(job, rule, decision.fingerprint)
        decision = apply_state_cooldown_rule(decision, rule, recent_count, previous_score, previous_context, last_sent_at, age_minutes_since(last_sent_at), job)
        decision = apply_similarity_rule(decision, rule, recent_count, previous_score, previous_context, job)
        decision = attach_previous_profit_loss_context(decision, job, previous_context)
        return apply_market_hours_rule(decision, rule, job)
