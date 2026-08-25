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
from ..domain.message_types import MARKET_OBSERVATION
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


MARKET_OBSERVATION_SIMILARITY_MIGRATION_KEY = "notification-rule-migration:market-observation-no-similarity-v1"
INVESTMENT_INSIGHT_SIMILARITY_MIGRATION_KEY = "notification-rule-migration:investment-insight-state-cooldown-only-v1"
NOTIFICATION_RULE_DEFAULTS_STATE_KEY = "notification-rule-defaults:active"
NOTIFICATION_RULE_DEFAULTS_LOCK_NAME = "orbit-alpha-notification-rule-defaults"


def notification_rule_defaults_fingerprint() -> str:
    payload = {
        message_type: rule.to_dict()
        for message_type, rule in sorted(DEFAULT_NOTIFICATION_RULES.items())
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def notification_rule_defaults_marker_matches(row, fingerprint: str) -> bool:
    if not row:
        return False
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except (AttributeError, json.JSONDecodeError, TypeError):
        return False
    return str(payload.get("fingerprint") or "") == str(fingerprint or "")


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
        fingerprint = notification_rule_defaults_fingerprint()
        with self.connect() as connection:
            current_marker = connection.execute(
                "SELECT payload_json FROM app_store WHERE store_id = %s",
                (NOTIFICATION_RULE_DEFAULTS_STATE_KEY,),
            ).fetchone()
        if notification_rule_defaults_marker_matches(current_marker, fingerprint):
            return

        with self.connect() as lock_connection:
            lock_row = lock_connection.execute(
                "SELECT GET_LOCK(%s, 10) AS acquired",
                (NOTIFICATION_RULE_DEFAULTS_LOCK_NAME,),
            ).fetchone()
            if int((lock_row or {}).get("acquired") or 0) != 1:
                raise RuntimeError("Notification rule defaults lock was not acquired.")
            try:
                current = lock_connection.execute(
                    "SELECT payload_json FROM app_store WHERE store_id = %s",
                    (NOTIFICATION_RULE_DEFAULTS_STATE_KEY,),
                ).fetchone()
                if notification_rule_defaults_marker_matches(current, fingerprint):
                    return

                def seed(connection):
                    self._seed_defaults_with_connection(connection, stamp)
                    connection.execute(
                        """
                        INSERT INTO app_store (store_id, payload_json, updated_at)
                        VALUES (%s, %s, %s)
                        ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                        """,
                        (
                            NOTIFICATION_RULE_DEFAULTS_STATE_KEY,
                            json_dumps({"fingerprint": fingerprint, "version": "notification-rule-defaults-v1"}),
                            stamp,
                        ),
                    )

                self.transaction_with_deadlock_retry("notification-rule-default-seed", seed)
            finally:
                lock_connection.execute(
                    "SELECT RELEASE_LOCK(%s)",
                    (NOTIFICATION_RULE_DEFAULTS_LOCK_NAME,),
                )

    def _seed_defaults_with_connection(self, connection, stamp: str) -> None:
        migration_row = connection.execute(
            "SELECT store_id FROM app_store WHERE store_id = %s",
            (MARKET_OBSERVATION_SIMILARITY_MIGRATION_KEY,),
        ).fetchone()
        migrate_market_observation_similarity = not bool(migration_row)
        investment_migration_row = connection.execute(
            "SELECT store_id FROM app_store WHERE store_id = %s",
            (INVESTMENT_INSIGHT_SIMILARITY_MIGRATION_KEY,),
        ).fetchone()
        migrate_investment_insight_similarity = not bool(investment_migration_row)
        for message_type, rule in DEFAULT_NOTIFICATION_RULES.items():
            connection.execute(
                """
                INSERT IGNORE INTO notification_rules (
                    message_type, enabled, conditions_json,
                    similarity_enabled, similarity_window_minutes,
                    similarity_bypass_conditions_json, similarity_fields_json, state_cooldown_enabled,
                    state_cooldown_minutes, market_hours_enabled, market_hours_markets_json,
                    off_hours_delivery_mode, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    message_type,
                    1 if rule.enabled else 0,
                    json_dumps([condition.to_dict() for condition in rule.conditions]),
                    1 if rule.similarity_enabled else 0,
                    int(rule.similarity_window_minutes),
                    json_dumps([condition.to_dict() for condition in rule.similarity_bypass_conditions]),
                    json_dumps(rule.similarity_fields),
                    1 if rule.state_cooldown_enabled else 0,
                    int(rule.state_cooldown_minutes),
                    1 if rule.market_hours_enabled else 0,
                    json_dumps(rule.market_hours_markets),
                    rule.off_hours_delivery_mode,
                    stamp,
                ),
            )
            row = connection.execute("SELECT * FROM notification_rules WHERE message_type = %s", (message_type,)).fetchone()
            if row:
                current = rule_from_row(row)
                migrated_conditions = self._migrate_default_conditions(current, rule)
                migrated_similarity = self._migrate_default_similarity(current, rule)
                migrated_market_observation_similarity = (
                    self._migrate_legacy_market_observation_similarity(current, rule)
                    if migrate_market_observation_similarity
                    else False
                )
                migrated_investment_insight_similarity = (
                    self._migrate_legacy_investment_insight_similarity(current, rule)
                    if migrate_investment_insight_similarity
                    else False
                )
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
                if migrated_market_observation_similarity or migrated_investment_insight_similarity:
                    set_clauses.append("similarity_enabled = %s")
                    params.append(1 if current.similarity_enabled else 0)
                    set_clauses.append("similarity_window_minutes = %s")
                    params.append(int(current.similarity_window_minutes))
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
        if migrate_market_observation_similarity:
            connection.execute(
                """
                INSERT INTO app_store (store_id, payload_json, updated_at)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
                (
                    MARKET_OBSERVATION_SIMILARITY_MIGRATION_KEY,
                    json_dumps({"policy": "marketObservation uses monitor cadence instead of text similarity"}),
                    stamp,
                ),
            )
        if migrate_investment_insight_similarity:
            connection.execute(
                """
                INSERT INTO app_store (store_id, payload_json, updated_at)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
                (
                    INVESTMENT_INSIGHT_SIMILARITY_MIGRATION_KEY,
                    json_dumps({"policy": "investmentInsight uses semantic state cooldown instead of duplicate text similarity"}),
                    stamp,
                ),
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
        legacy_dispatch_signature_fields = [
            "messageType",
            "accountId",
            "ontologyInsight.subject",
            "ontologyInsight.dispatchInsightType",
            "ontologyInsight.semanticSignature",
        ]
        legacy_insight_fields = [
            "messageType",
            "accountId",
            "ontologyInsight.subject",
            "ontologyInsight.insightType",
        ]
        generic_legacy_fields = ["messageType", "accountId", "symbol", "severity", "title"]
        known_legacy_fields = [
            legacy_fields,
            legacy_dispatch_fields,
            legacy_dispatch_signature_fields,
            legacy_insight_fields,
            generic_legacy_fields,
        ]
        if current.similarity_fields in known_legacy_fields and current.similarity_fields != default_rule.similarity_fields:
            current.similarity_fields = list(default_rule.similarity_fields)
            changed = True
        if str(current.message_type or "") == "investmentInsight":
            default_ids = {condition.condition_id for condition in default_rule.similarity_bypass_conditions}
            filtered_conditions = [
                condition
                for condition in current.similarity_bypass_conditions
                if condition.condition_id in default_ids and condition.condition_id != "semantic_signature_changed"
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

    @staticmethod
    def _migrate_legacy_market_observation_similarity(
        current: NotificationRuleConfig,
        default_rule: NotificationRuleConfig,
    ) -> bool:
        """Disable only the old untouched two-hour price-observation policy.

        The marker written by ``seed_defaults`` makes this a one-time migration
        and preserves any later user change to the notification rule.
        """

        if str(current.message_type or "") != MARKET_OBSERVATION:
            return False
        if not current.similarity_enabled or int(current.similarity_window_minutes or 0) != 120:
            return False
        if list(current.similarity_fields or []) != list(default_rule.similarity_fields or []):
            return False
        if current.similarity_bypass_conditions:
            return False
        current.similarity_enabled = False
        current.similarity_window_minutes = 0
        return True

    @staticmethod
    def _migrate_legacy_investment_insight_similarity(
        current: NotificationRuleConfig,
        default_rule: NotificationRuleConfig,
    ) -> bool:
        """Remove only the untouched generic similarity layer.

        Semantic state cooldown remains enabled and keeps its material-change
        bypass conditions, so changed actions or relations can still publish.
        """

        if str(current.message_type or "") != "investmentInsight":
            return False
        if not current.similarity_enabled or int(current.similarity_window_minutes or 0) != 360:
            return False
        if list(current.similarity_fields or []) != list(default_rule.similarity_fields or []):
            return False
        default_ids = {
            condition.condition_id
            for condition in default_rule.similarity_bypass_conditions
        }
        current_ids = {
            condition.condition_id
            for condition in current.similarity_bypass_conditions
        }
        if current_ids != default_ids:
            return False
        current.similarity_enabled = False
        return True

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
                    message_type, enabled, conditions_json,
                    similarity_enabled, similarity_window_minutes,
                    similarity_bypass_conditions_json, similarity_fields_json, state_cooldown_enabled,
                    state_cooldown_minutes, market_hours_enabled, market_hours_markets_json,
                    off_hours_delivery_mode, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE enabled = VALUES(enabled), conditions_json = VALUES(conditions_json), similarity_enabled = VALUES(similarity_enabled),
                    similarity_window_minutes = VALUES(similarity_window_minutes),
                    similarity_bypass_conditions_json = VALUES(similarity_bypass_conditions_json),
                    similarity_fields_json = VALUES(similarity_fields_json),
                    state_cooldown_enabled = VALUES(state_cooldown_enabled),
                    state_cooldown_minutes = VALUES(state_cooldown_minutes),
                    market_hours_enabled = VALUES(market_hours_enabled),
                    market_hours_markets_json = VALUES(market_hours_markets_json),
                    off_hours_delivery_mode = VALUES(off_hours_delivery_mode),
                    updated_at = VALUES(updated_at)
                """,
                (
                    normalized.message_type,
                    1 if normalized.enabled else 0,
                    json_dumps([condition.to_dict() for condition in normalized.conditions]),
                    1 if normalized.similarity_enabled else 0,
                    int(normalized.similarity_window_minutes),
                    json_dumps([condition.to_dict() for condition in normalized.similarity_bypass_conditions]),
                    json_dumps(normalized.similarity_fields),
                    1 if normalized.state_cooldown_enabled else 0,
                    int(normalized.state_cooldown_minutes),
                    1 if normalized.market_hours_enabled else 0,
                    json_dumps(normalized.market_hours_markets),
                    normalized.off_hours_delivery_mode,
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
        recent_count, previous_context, last_sent_at = self.similar_history(job, rule, decision.fingerprint)
        decision = apply_state_cooldown_rule(
            decision,
            rule,
            recent_count,
            previous_context,
            last_sent_at,
            age_minutes_since(last_sent_at),
            job,
        )
        decision = apply_similarity_rule(decision, rule, recent_count, previous_context, job)
        decision = attach_previous_profit_loss_context(decision, job, previous_context)
        return apply_market_hours_rule(decision, rule, job)
