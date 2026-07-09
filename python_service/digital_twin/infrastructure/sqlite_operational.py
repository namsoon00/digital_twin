import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..domain.events import (
    DomainEvent,
    alerts_detected_event,
    monitoring_cycle_completed_event,
    snapshot_collected_event,
)
from ..domain.data_freshness import evaluate_notification_data_freshness
from ..domain.fact_changes import fact_signature, research_evidence_fact_payload
from ..domain.investment_research import ResearchEvidence
from ..domain.model_review import ModelReviewJob
from ..domain.notification_rules import DEFAULT_NOTIFICATION_RULES, NotificationRuleConfig, apply_market_hours_rule, apply_similarity_rule, apply_state_cooldown_rule, default_notification_rule, evaluate_notification_rule, notification_fingerprint
from ..domain.notification_templates import DEFAULT_NOTIFICATION_TEMPLATES, LEGACY_DEFAULT_TEMPLATE, PREVIOUS_DEFAULT_TEMPLATE, NotificationTemplate, alert_context, render_notification
from ..domain.notifications import NotificationJob
from ..domain.ontology_quality import OntologyQualitySample, build_ontology_quality_sample
from ..domain.portfolio import AccountSnapshot, AlertEvent
from ..domain.repositories import MonitoringCycleRecordResult
from ..domain.symbol_universe import ListedSymbol, normalize_market, normalize_symbol, utc_now_iso as symbol_utc_now_iso
from .model_review_queue import model_review_payloads_from_event
from .settings import data_dir, read_json, runtime_settings, service_db_path, settings_path, utc_now
from .sqlite_support import connect_sqlite


def json_dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def parse_utc_datetime(value: str):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def age_minutes_since(value: str, now=None) -> int:
    parsed = parse_utc_datetime(value)
    if not parsed:
        return 0
    current = now or datetime.now(timezone.utc)
    return max(0, int((current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() // 60))


def insert_domain_event_with_connection(connection, event: DomainEvent) -> None:
    payload = event.to_dict()
    connection.execute(
        """
        INSERT OR IGNORE INTO domain_events (
            event_id, name, aggregate_id, occurred_at, correlation_id, payload_json, event_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_id,
            event.name,
            event.aggregate_id,
            event.occurred_at,
            event.correlation_id,
            json_dumps(event.payload),
            json_dumps(payload),
        ),
    )


def rule_from_row(row) -> NotificationRuleConfig:
    row_keys = set(row.keys())
    try:
        conditions = json.loads(row["conditions_json"] or "[]")
    except json.JSONDecodeError:
        conditions = []
    try:
        similarity_fields = json.loads(row["similarity_fields_json"] or "[]")
    except json.JSONDecodeError:
        similarity_fields = []
    if "similarity_bypass_conditions_json" in row_keys:
        try:
            similarity_bypass_conditions = json.loads(row["similarity_bypass_conditions_json"] or "[]")
        except json.JSONDecodeError:
            similarity_bypass_conditions = []
    else:
        similarity_bypass_conditions = []
    if "market_hours_markets_json" in row_keys:
        try:
            market_hours_markets = json.loads(row["market_hours_markets_json"] or "[]")
        except json.JSONDecodeError:
            market_hours_markets = []
    else:
        market_hours_markets = []
    return NotificationRuleConfig.from_dict({
        "messageType": row["message_type"],
        "enabled": bool(row["enabled"]),
        "threshold": row["threshold"],
        "baseScore": row["base_score"],
        "lowScoreAction": row["low_score_action"],
        "conditions": conditions if isinstance(conditions, list) else [],
        "similarityEnabled": bool(row["similarity_enabled"]),
        "similarityWindowMinutes": row["similarity_window_minutes"],
        "similarityPenalty": row["similarity_penalty"],
        "similarityBypassScoreDelta": row["similarity_bypass_score_delta"],
        "similarityBypassConditions": similarity_bypass_conditions if isinstance(similarity_bypass_conditions, list) else [],
        "similarityFields": similarity_fields if isinstance(similarity_fields, list) else [],
        "stateCooldownEnabled": bool(row["state_cooldown_enabled"]) if "state_cooldown_enabled" in row_keys else None,
        "stateCooldownMinutes": row["state_cooldown_minutes"] if "state_cooldown_minutes" in row_keys else None,
        "marketHoursEnabled": bool(row["market_hours_enabled"]) if "market_hours_enabled" in row_keys else None,
        "marketHoursMarkets": market_hours_markets if isinstance(market_hours_markets, list) else [],
        "updatedAt": row["updated_at"],
    })


def template_from_row(row) -> NotificationTemplate:
    return NotificationTemplate(
        message_type=row["message_type"],
        template=row["template"],
        description=row["description"],
        enabled=bool(row["enabled"]),
        updated_at=row["updated_at"],
    )


def research_evidence_from_row(row) -> ResearchEvidence:
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    return ResearchEvidence(
        evidence_id=row["evidence_id"],
        symbol=row["symbol"],
        kind=row["kind"],
        source=row["source"],
        title=row["title"],
        summary=row["summary"],
        url=row["url"],
        observed_at=row["observed_at"],
        polarity=row["polarity"],
        impact_score=row["impact_score"],
        confidence=row["confidence"],
        published_at=row["published_at"],
        raw_payload=payload if isinstance(payload, dict) else {},
    )


def research_evidence_change_payload(
    symbol: str,
    kind: str,
    source: str,
    title: str,
    summary: str,
    url: str,
    published_at: str,
    polarity: str,
    impact_score: float,
    confidence: float,
    payload: Dict[str, object],
) -> Dict[str, object]:
    return research_evidence_fact_payload({
        "symbol": symbol,
        "kind": kind,
        "source": source,
        "title": title,
        "summary": summary,
        "url": url,
        "publishedAt": published_at,
        "polarity": polarity,
        "impactScore": round(float(impact_score or 0), 6),
        "confidence": round(float(confidence or 0), 6),
        "payload": payload if isinstance(payload, dict) else {},
    })


class OperationalConnection:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or service_db_path()).resolve()
        self.ensure_schema()

    def connect(self):
        return connect_sqlite(self.path)

    def ensure_schema(self) -> None:
        with self.connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS monitor_snapshots (
                    account_id TEXT PRIMARY KEY,
                    account_label TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    generated_at TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS monitor_sent (
                    key TEXT PRIMARY KEY,
                    sent_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS domain_events (
                    event_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL DEFAULT '',
                    occurred_at TEXT NOT NULL,
                    correlation_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    event_json TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_domain_events_name_time ON domain_events(name, occurred_at)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS model_review_jobs (
                    job_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT '',
                    account_label TEXT NOT NULL DEFAULT '',
                    symbol TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    alert_key TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    alert_lines_json TEXT NOT NULL DEFAULT '[]',
                    payload_json TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_model_review_jobs_status ON model_review_jobs(status, created_at)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS ontology_ai_opinion_samples (
                    sample_id TEXT PRIMARY KEY,
                    portfolio_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    overall_score REAL NOT NULL DEFAULT 0,
                    data_coverage_score REAL NOT NULL DEFAULT 0,
                    context_coverage_score REAL NOT NULL DEFAULT 0,
                    reasoning_readiness_score REAL NOT NULL DEFAULT 0,
                    relation_density_score REAL NOT NULL DEFAULT 0,
                    entity_count INTEGER NOT NULL DEFAULT 0,
                    relation_count INTEGER NOT NULL DEFAULT 0,
                    evidence_count INTEGER NOT NULL DEFAULT 0,
                    belief_count INTEGER NOT NULL DEFAULT 0,
                    opinion_count INTEGER NOT NULL DEFAULT 0,
                    reasoning_card_count INTEGER NOT NULL DEFAULT 0,
                    data_gap_count INTEGER NOT NULL DEFAULT 0,
                    bounded_context_count INTEGER NOT NULL DEFAULT 0,
                    high_pressure_count INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_ontology_quality_portfolio_time ON ontology_ai_opinion_samples(portfolio_id, created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_ontology_quality_score ON ontology_ai_opinion_samples(overall_score, created_at)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS notification_jobs (
                    job_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT '',
                    account_label TEXT NOT NULL DEFAULT '',
                    message_type TEXT NOT NULL DEFAULT 'notification',
                    source_event_id TEXT NOT NULL DEFAULT '',
                    source_event_name TEXT NOT NULL DEFAULT '',
                    dedupe_key TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL
                )
            """)
            self.ensure_columns(
                connection,
                "notification_jobs",
                {
                    "source_event_id": "TEXT NOT NULL DEFAULT ''",
                    "source_event_name": "TEXT NOT NULL DEFAULT ''",
                    "dedupe_key": "TEXT NOT NULL DEFAULT ''",
                },
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_notification_jobs_status ON notification_jobs(status, created_at)")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_jobs_dedupe ON notification_jobs(dedupe_key) WHERE dedupe_key != ''")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS runtime_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS app_store (
                    store_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    published_at TEXT NOT NULL DEFAULT '',
                    observed_at TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    polarity TEXT NOT NULL DEFAULT 'context',
                    impact_score REAL NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0,
                    dedupe_key TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}'
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_research_evidence_symbol_time ON research_evidence(symbol, last_seen_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_research_evidence_kind_time ON research_evidence(kind, last_seen_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_research_evidence_source_time ON research_evidence(source, last_seen_at)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS market_quote_cache (
                    provider TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT '',
                    symbol TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (provider, account_id, symbol)
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_market_quote_cache_account ON market_quote_cache(account_id, symbol)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS notification_templates (
                    message_type TEXT PRIMARY KEY,
                    template TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS notification_rules (
                    message_type TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    threshold INTEGER NOT NULL DEFAULT 45,
                    base_score INTEGER NOT NULL DEFAULT 25,
                    low_score_action TEXT NOT NULL DEFAULT 'suppress',
                    conditions_json TEXT NOT NULL DEFAULT '[]',
                    similarity_enabled INTEGER NOT NULL DEFAULT 1,
                    similarity_window_minutes INTEGER NOT NULL DEFAULT 120,
                    similarity_penalty INTEGER NOT NULL DEFAULT -20,
                    similarity_bypass_score_delta INTEGER NOT NULL DEFAULT 20,
                    similarity_bypass_conditions_json TEXT NOT NULL DEFAULT '[]',
                    similarity_fields_json TEXT NOT NULL DEFAULT '["messageType","accountId","symbol","severity","title"]',
                    state_cooldown_enabled INTEGER NOT NULL DEFAULT 0,
                    state_cooldown_minutes INTEGER NOT NULL DEFAULT 0,
                    market_hours_enabled INTEGER NOT NULL DEFAULT 0,
                    market_hours_markets_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                )
            """)
            added_rule_columns = self.ensure_columns(
                connection,
                "notification_rules",
                {
                    "similarity_enabled": "INTEGER NOT NULL DEFAULT 1",
                    "similarity_window_minutes": "INTEGER NOT NULL DEFAULT 120",
                    "similarity_penalty": "INTEGER NOT NULL DEFAULT -20",
                    "similarity_bypass_score_delta": "INTEGER NOT NULL DEFAULT 20",
                    "similarity_bypass_conditions_json": "TEXT NOT NULL DEFAULT '[]'",
                    "similarity_fields_json": "TEXT NOT NULL DEFAULT '[\"messageType\",\"accountId\",\"symbol\",\"severity\",\"title\"]'",
                    "state_cooldown_enabled": "INTEGER NOT NULL DEFAULT 0",
                    "state_cooldown_minutes": "INTEGER NOT NULL DEFAULT 0",
                    "market_hours_enabled": "INTEGER NOT NULL DEFAULT 0",
                    "market_hours_markets_json": "TEXT NOT NULL DEFAULT '[]'",
                },
            )
            if {"state_cooldown_enabled", "state_cooldown_minutes"} & set(added_rule_columns):
                stamp = utc_now()
                for message_type, rule in DEFAULT_NOTIFICATION_RULES.items():
                    connection.execute(
                        """
                        UPDATE notification_rules
                        SET state_cooldown_enabled = ?, state_cooldown_minutes = ?, updated_at = ?
                        WHERE message_type = ?
                        """,
                        (
                            1 if rule.state_cooldown_enabled else 0,
                            int(rule.state_cooldown_minutes),
                            stamp,
                            message_type,
                        ),
                    )
            if {"market_hours_enabled", "market_hours_markets_json"} & set(added_rule_columns):
                stamp = utc_now()
                for message_type, rule in DEFAULT_NOTIFICATION_RULES.items():
                    connection.execute(
                        """
                        UPDATE notification_rules
                        SET market_hours_enabled = ?, market_hours_markets_json = ?, updated_at = ?
                        WHERE message_type = ?
                        """,
                        (
                            1 if rule.market_hours_enabled else 0,
                            json_dumps(rule.market_hours_markets),
                            stamp,
                            message_type,
                        ),
                    )
            if "similarity_bypass_conditions_json" in added_rule_columns:
                stamp = utc_now()
                for message_type, rule in DEFAULT_NOTIFICATION_RULES.items():
                    connection.execute(
                        """
                        UPDATE notification_rules
                        SET similarity_bypass_conditions_json = ?, updated_at = ?
                        WHERE message_type = ?
                        """,
                        (
                            json_dumps([condition.to_dict() for condition in rule.similarity_bypass_conditions]),
                            stamp,
                            message_type,
                        ),
                    )
            connection.execute("""
                CREATE TABLE IF NOT EXISTS symbol_universe (
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    exchange TEXT NOT NULL DEFAULT '',
                    currency TEXT NOT NULL DEFAULT '',
                    sector TEXT NOT NULL DEFAULT '',
                    asset_type TEXT NOT NULL DEFAULT 'STOCK',
                    source TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    fetched_at TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL DEFAULT '',
                    last_seen_at TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (market, symbol)
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_symbol_universe_symbol ON symbol_universe(symbol)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_symbol_universe_name ON symbol_universe(name)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_symbol_universe_market_seen ON symbol_universe(market, last_seen_at)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS symbol_universe_sources (
                    market TEXT PRIMARY KEY,
                    source TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    record_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT NOT NULL DEFAULT '',
                    last_success_at TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
            """)

    def ensure_columns(self, connection, table: str, columns: Dict[str, str]) -> List[str]:
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(" + table + ")").fetchall()
        }
        added = []
        for name, definition in columns.items():
            if name not in existing:
                try:
                    connection.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + definition)
                    added.append(name)
                except sqlite3.OperationalError as error:
                    if "duplicate column name" not in str(error).lower():
                        raise
        return added


class SQLiteNotificationTemplateStore(OperationalConnection):
    def __init__(self, path: Optional[Path] = None):
        super().__init__(path)
        self.seed_defaults()

    def seed_defaults(self) -> None:
        stamp = utc_now()
        with self.connect() as connection:
            for message_type, payload in DEFAULT_NOTIFICATION_TEMPLATES.items():
                template = str(payload.get("template") or "")
                description = str(payload.get("description") or "")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO notification_templates (
                        message_type, template, description, enabled, updated_at
                    )
                    VALUES (?, ?, ?, 1, ?)
                    """,
                    (
                        message_type,
                        template,
                        description,
                        stamp,
                    ),
                )
                if template != LEGACY_DEFAULT_TEMPLATE:
                    connection.execute(
                        """
                        UPDATE notification_templates
                        SET template = ?, description = ?, updated_at = ?
                        WHERE message_type = ? AND template = ?
                        """,
                        (template, description, stamp, message_type, LEGACY_DEFAULT_TEMPLATE),
                    )
                if template != PREVIOUS_DEFAULT_TEMPLATE:
                    connection.execute(
                        """
                        UPDATE notification_templates
                        SET template = ?, description = ?, updated_at = ?
                        WHERE message_type = ? AND template = ?
                        """,
                        (template, description, stamp, message_type, PREVIOUS_DEFAULT_TEMPLATE),
                    )

    def row_to_template(self, row) -> NotificationTemplate:
        return template_from_row(row)

    def list(self) -> List[NotificationTemplate]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT message_type, template, description, enabled, updated_at FROM notification_templates ORDER BY message_type"
            ).fetchall()
        return [self.row_to_template(row) for row in rows]

    def get(self, message_type: str) -> NotificationTemplate:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT message_type, template, description, enabled, updated_at
                FROM notification_templates
                WHERE message_type = ?
                """,
                (str(message_type or "notification"),),
            ).fetchone()
            if not row:
                row = connection.execute(
                    """
                    SELECT message_type, template, description, enabled, updated_at
                    FROM notification_templates
                    WHERE message_type = 'default'
                    """
                ).fetchone()
        return self.row_to_template(row) if row else NotificationTemplate.default("default")

    def upsert(self, message_type: str, template: str, description: str = "", enabled: bool = True) -> NotificationTemplate:
        stamp = utc_now()
        key = str(message_type or "").strip()
        if not key:
            raise ValueError("message_type is required")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO notification_templates (message_type, template, description, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(message_type) DO UPDATE SET
                    template = excluded.template,
                    description = excluded.description,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
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
        return self.render(job.message_type, context)


class SQLiteNotificationRuleStore(OperationalConnection):
    def __init__(self, path: Optional[Path] = None, seed_defaults: bool = True):
        super().__init__(path)
        if seed_defaults:
            self.seed_defaults()

    def seed_defaults(self) -> None:
        stamp = utc_now()
        with self.connect() as connection:
            for message_type, rule in DEFAULT_NOTIFICATION_RULES.items():
                connection.execute(
                    """
                    INSERT OR IGNORE INTO notification_rules (
                        message_type, enabled, threshold, base_score, low_score_action, conditions_json,
                        similarity_enabled, similarity_window_minutes, similarity_penalty, similarity_bypass_score_delta,
                        similarity_bypass_conditions_json, similarity_fields_json, state_cooldown_enabled, state_cooldown_minutes,
                        market_hours_enabled, market_hours_markets_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            for message_type, rule in DEFAULT_NOTIFICATION_RULES.items():
                row = connection.execute(
                    """
                    SELECT threshold, conditions_json, similarity_bypass_conditions_json,
                        similarity_fields_json, state_cooldown_enabled, state_cooldown_minutes,
                        market_hours_enabled, market_hours_markets_json, updated_at
                    FROM notification_rules
                    WHERE message_type = ?
                    """,
                    (message_type,),
                ).fetchone()
                if not row:
                    continue
                try:
                    configured_conditions = json.loads(row["similarity_bypass_conditions_json"] or "[]")
                except json.JSONDecodeError:
                    configured_conditions = []
                if not isinstance(configured_conditions, list):
                    configured_conditions = []
                try:
                    configured_rule_conditions = json.loads(row["conditions_json"] or "[]")
                except json.JSONDecodeError:
                    configured_rule_conditions = []
                if not isinstance(configured_rule_conditions, list):
                    configured_rule_conditions = []
                default_conditions_by_id = {
                    condition.condition_id: condition.to_dict()
                    for condition in rule.conditions
                    if condition.condition_id
                }
                migrated_condition_ids = {"important_terms", "confirming_data", "actionable_terms", "status_noise"}
                rule_conditions_changed = False
                for item in configured_rule_conditions:
                    if not isinstance(item, dict):
                        continue
                    condition_id = str(item.get("id") or "")
                    if condition_id not in migrated_condition_ids:
                        continue
                    default_condition = default_conditions_by_id.get(condition_id)
                    if not default_condition:
                        continue
                    for field_name in ["type", "field", "terms"]:
                        if item.get(field_name) != default_condition.get(field_name):
                            item[field_name] = default_condition.get(field_name)
                            rule_conditions_changed = True
                existing_ids = {
                    str(item.get("id") or "")
                    for item in configured_conditions
                    if isinstance(item, dict)
                }
                existing_rule_condition_ids = {
                    str(item.get("id") or "")
                    for item in configured_rule_conditions
                    if isinstance(item, dict)
                }
                missing_conditions = [
                    condition.to_dict()
                    for condition in rule.similarity_bypass_conditions
                    if condition.condition_id and condition.condition_id not in existing_ids
                ]
                configured_conditions.extend(missing_conditions)
                missing_rule_conditions = [
                    condition.to_dict()
                    for condition in rule.conditions
                    if condition.condition_id and condition.condition_id not in existing_rule_condition_ids
                ]
                if missing_rule_conditions:
                    configured_rule_conditions.extend(missing_rule_conditions)
                    rule_conditions_changed = True
                try:
                    configured_similarity_fields = json.loads(row["similarity_fields_json"] or "[]")
                except json.JSONDecodeError:
                    configured_similarity_fields = []
                if not isinstance(configured_similarity_fields, list):
                    configured_similarity_fields = []
                similarity_fields_changed = False
                if (
                    message_type == "investmentInsight"
                    and configured_similarity_fields == ["messageType", "accountId", "symbol", "severity", "title"]
                    and list(rule.similarity_fields or []) != configured_similarity_fields
                ):
                    configured_similarity_fields = list(rule.similarity_fields or configured_similarity_fields)
                    similarity_fields_changed = True
                threshold = int(row["threshold"] or 0)
                if message_type == "externalCryptoMove" and threshold == 45:
                    threshold = int(rule.threshold)
                state_cooldown_enabled = int(row["state_cooldown_enabled"] or 0)
                state_cooldown_minutes = int(row["state_cooldown_minutes"] or 0)
                state_cooldown_changed = False
                if missing_conditions and rule.state_cooldown_enabled and not state_cooldown_enabled and not state_cooldown_minutes:
                    state_cooldown_enabled = 1
                    state_cooldown_minutes = int(rule.state_cooldown_minutes)
                    state_cooldown_changed = True
                market_hours_enabled = int(row["market_hours_enabled"] or 0)
                try:
                    configured_market_hours_markets = json.loads(row["market_hours_markets_json"] or "[]")
                except json.JSONDecodeError:
                    configured_market_hours_markets = []
                if not isinstance(configured_market_hours_markets, list):
                    configured_market_hours_markets = []
                market_hours_changed = False
                if (
                    rule.market_hours_enabled
                    and not market_hours_enabled
                    and list(configured_market_hours_markets or []) == list(rule.market_hours_markets or [])
                    and age_minutes_since(row["updated_at"]) > 5
                ):
                    market_hours_enabled = 1
                    market_hours_changed = True
                if (
                    not missing_conditions
                    and not rule_conditions_changed
                    and not similarity_fields_changed
                    and not state_cooldown_changed
                    and not market_hours_changed
                    and int(row["threshold"] or 0) == threshold
                ):
                    continue
                connection.execute(
                    """
                    UPDATE notification_rules
                    SET threshold = ?, conditions_json = ?, similarity_bypass_conditions_json = ?, similarity_fields_json = ?,
                        state_cooldown_enabled = ?, state_cooldown_minutes = ?, market_hours_enabled = ?, updated_at = ?
                    WHERE message_type = ?
                    """,
                    (
                        threshold,
                        json_dumps(configured_rule_conditions),
                        json_dumps(configured_conditions),
                        json_dumps(configured_similarity_fields),
                        state_cooldown_enabled,
                        state_cooldown_minutes,
                        market_hours_enabled,
                        stamp,
                        message_type,
                    ),
                )

    def row_to_rule(self, row) -> NotificationRuleConfig:
        return rule_from_row(row)

    def list(self) -> List[NotificationRuleConfig]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT message_type, enabled, threshold, base_score, low_score_action, conditions_json,
                    similarity_enabled, similarity_window_minutes, similarity_penalty, similarity_bypass_score_delta,
                    similarity_bypass_conditions_json, similarity_fields_json, state_cooldown_enabled, state_cooldown_minutes,
                    market_hours_enabled, market_hours_markets_json, updated_at
                FROM notification_rules
                ORDER BY message_type
                """
            ).fetchall()
        return [self.row_to_rule(row) for row in rows]

    def get(self, message_type: str) -> NotificationRuleConfig:
        key = str(message_type or "notification").strip() or "notification"
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT message_type, enabled, threshold, base_score, low_score_action, conditions_json,
                    similarity_enabled, similarity_window_minutes, similarity_penalty, similarity_bypass_score_delta,
                    similarity_bypass_conditions_json, similarity_fields_json, state_cooldown_enabled, state_cooldown_minutes,
                    market_hours_enabled, market_hours_markets_json, updated_at
                FROM notification_rules
                WHERE message_type = ?
                """,
                (key,),
            ).fetchone()
        return self.row_to_rule(row) if row else default_notification_rule(key)

    def upsert(self, rule: NotificationRuleConfig) -> NotificationRuleConfig:
        normalized = NotificationRuleConfig.from_dict(rule.to_dict() if isinstance(rule, NotificationRuleConfig) else dict(rule or {}))
        key = str(normalized.message_type or "").strip()
        if not key:
            raise ValueError("message_type is required")
        normalized.message_type = key
        normalized.updated_at = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO notification_rules (
                    message_type, enabled, threshold, base_score, low_score_action, conditions_json,
                    similarity_enabled, similarity_window_minutes, similarity_penalty, similarity_bypass_score_delta,
                    similarity_bypass_conditions_json, similarity_fields_json, state_cooldown_enabled, state_cooldown_minutes,
                    market_hours_enabled, market_hours_markets_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_type) DO UPDATE SET
                    enabled = excluded.enabled,
                    threshold = excluded.threshold,
                    base_score = excluded.base_score,
                    low_score_action = excluded.low_score_action,
                    conditions_json = excluded.conditions_json,
                    similarity_enabled = excluded.similarity_enabled,
                    similarity_window_minutes = excluded.similarity_window_minutes,
                    similarity_penalty = excluded.similarity_penalty,
                    similarity_bypass_score_delta = excluded.similarity_bypass_score_delta,
                    similarity_bypass_conditions_json = excluded.similarity_bypass_conditions_json,
                    similarity_fields_json = excluded.similarity_fields_json,
                    state_cooldown_enabled = excluded.state_cooldown_enabled,
                    state_cooldown_minutes = excluded.state_cooldown_minutes,
                    market_hours_enabled = excluded.market_hours_enabled,
                    market_hours_markets_json = excluded.market_hours_markets_json,
                    updated_at = excluded.updated_at
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
        return self.get(key)

    def reset(self, message_type: str) -> NotificationRuleConfig:
        return self.upsert(default_notification_rule(str(message_type or "notification").strip() or "notification"))

    def evaluate_job(self, job: NotificationJob):
        rule = self.get(job.message_type)
        decision = evaluate_notification_rule(job, rule)
        recent_count, previous_score, previous_context, last_sent_at = self.similar_history(job, rule, decision.fingerprint)
        decision = apply_state_cooldown_rule(
            decision,
            rule,
            recent_count,
            previous_score,
            previous_context,
            last_sent_at,
            age_minutes_since(last_sent_at),
            job,
        )
        decision = apply_similarity_rule(decision, rule, recent_count, previous_score, previous_context, job)
        return apply_market_hours_rule(decision, rule, job)

    def similar_history(self, job: NotificationJob, rule: NotificationRuleConfig, fingerprint: str):
        similarity_minutes = int(rule.similarity_window_minutes or 0) if rule.similarity_enabled else 0
        state_minutes = int(rule.state_cooldown_minutes or 0) + 60 if rule.state_cooldown_enabled and int(rule.state_cooldown_minutes or 0) else 0
        history_minutes = max(similarity_minutes, state_minutes)
        if not history_minutes or not fingerprint:
            return 0, 0, {}, ""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=history_minutes)
        cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json, created_at FROM notification_jobs
                WHERE message_type = ? AND created_at >= ? AND status IN ('pending', 'processing', 'done')
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (job.message_type, cutoff_text),
            ).fetchall()
        count = 0
        previous_score = 0
        most_recent_context: Dict[str, object] = {}
        most_recent_at = ""
        for row in rows:
            try:
                previous = NotificationJob.from_dict(json.loads(row["payload_json"]))
            except json.JSONDecodeError:
                continue
            if previous.job_id == job.job_id:
                continue
            previous_context = previous.context or {}
            previous_fingerprint = str(previous_context.get("honeyFingerprint") or notification_fingerprint(previous, rule))
            if previous_fingerprint != fingerprint:
                continue
            count += 1
            if not most_recent_context:
                most_recent_context = dict(previous_context)
                most_recent_at = row["created_at"] or previous.created_at
            previous_score = max(previous_score, int(previous_context.get("honeyScore") or 0))
        return count, previous_score, most_recent_context, most_recent_at


class SQLiteAppStore(OperationalConnection):
    def __init__(self, path: Optional[Path] = None, legacy_path: Optional[Path] = None):
        self.legacy_path = legacy_path or data_dir() / "store.json"
        super().__init__(path)
        self.migrate_legacy_if_needed()

    def migrate_legacy_if_needed(self) -> None:
        if not self.legacy_path.exists():
            return
        with self.connect() as connection:
            existing = connection.execute("SELECT store_id FROM app_store WHERE store_id = 'default'").fetchone()
        if existing:
            return
        payload = read_json(self.legacy_path, {})
        if isinstance(payload, dict) and payload:
            self.replace(payload)

    def load(self) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute("SELECT payload_json FROM app_store WHERE store_id = 'default'").fetchone()
        if not row:
            return {}
        try:
            payload = json.loads(row["payload_json"])
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}

    def replace(self, payload: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_store (store_id, payload_json, updated_at)
                VALUES ('default', ?, ?)
                ON CONFLICT(store_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (json_dumps(payload), stamp),
            )


class SQLiteExternalSignalCache(OperationalConnection):
    store_id = "external_signals"

    def load(self) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM app_store WHERE store_id = ?",
                (self.store_id,),
            ).fetchone()
        if not row:
            return {}
        try:
            payload = json.loads(row["payload_json"])
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}

    def replace(self, payload: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_store (store_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(store_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (self.store_id, json_dumps(payload), stamp),
            )


class SQLiteResearchEvidenceStore(OperationalConnection):
    def upsert_many(self, items: Iterable[ResearchEvidence]) -> int:
        stamp = utc_now()
        written = 0
        changed_symbols: List[str] = []
        with self.connect() as connection:
            for item in items or []:
                evidence_id = str(item.evidence_id or "").strip()
                if not evidence_id:
                    continue
                symbol = str(item.symbol or "").upper().strip()
                kind = str(item.kind or "").strip()
                source = str(item.source or "").strip()
                title = str(item.title or "").strip()
                observed_at = str(item.observed_at or item.published_at or stamp).strip()
                published_at = str(item.published_at or item.observed_at or "").strip()
                dedupe_key = "|".join([symbol, kind, source, title, str(item.url or "").strip()])[:500]
                payload = dict(item.raw_payload or {})
                current_signature = fact_signature(research_evidence_change_payload(
                    symbol,
                    kind,
                    source,
                    title,
                    str(item.summary or ""),
                    str(item.url or ""),
                    published_at,
                    str(item.polarity or "context"),
                    float(item.impact_score or 0),
                    float(item.confidence or 0),
                    payload,
                ))
                previous_row = connection.execute(
                    """
                    SELECT symbol, kind, source, title, summary, url, published_at,
                           polarity, impact_score, confidence, payload_json
                    FROM research_evidence
                    WHERE evidence_id = ?
                    """,
                    (evidence_id,),
                ).fetchone()
                previous_signature = ""
                if previous_row:
                    try:
                        previous_payload = json.loads(previous_row["payload_json"] or "{}")
                    except json.JSONDecodeError:
                        previous_payload = {}
                    previous_signature = fact_signature(research_evidence_change_payload(
                        str(previous_row["symbol"] or ""),
                        str(previous_row["kind"] or ""),
                        str(previous_row["source"] or ""),
                        str(previous_row["title"] or ""),
                        str(previous_row["summary"] or ""),
                        str(previous_row["url"] or ""),
                        str(previous_row["published_at"] or ""),
                        str(previous_row["polarity"] or "context"),
                        float(previous_row["impact_score"] or 0),
                        float(previous_row["confidence"] or 0),
                        previous_payload,
                    ))
                connection.execute(
                    """
                    INSERT INTO research_evidence (
                        evidence_id, symbol, kind, source, title, summary, url, published_at,
                        observed_at, first_seen_at, last_seen_at, polarity, impact_score,
                        confidence, dedupe_key, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(evidence_id) DO UPDATE SET
                        symbol = excluded.symbol,
                        kind = excluded.kind,
                        source = excluded.source,
                        title = excluded.title,
                        summary = excluded.summary,
                        url = excluded.url,
                        published_at = excluded.published_at,
                        observed_at = excluded.observed_at,
                        last_seen_at = excluded.last_seen_at,
                        polarity = excluded.polarity,
                        impact_score = excluded.impact_score,
                        confidence = excluded.confidence,
                        dedupe_key = excluded.dedupe_key,
                        payload_json = excluded.payload_json
                    """,
                    (
                        evidence_id,
                        symbol,
                        kind,
                        source,
                        title,
                        str(item.summary or ""),
                        str(item.url or ""),
                        published_at,
                        observed_at,
                        stamp,
                        stamp,
                        str(item.polarity or "context"),
                        float(item.impact_score or 0),
                        float(item.confidence or 0),
                        dedupe_key,
                        json_dumps(payload),
                    ),
                )
                if not previous_row or current_signature != previous_signature:
                    written += 1
                    if symbol and symbol not in changed_symbols:
                        changed_symbols.append(symbol)
        self.last_changed_symbols = changed_symbols
        return written

    def latest(self, symbol: str = "", kind: str = "", limit: int = 50) -> List[ResearchEvidence]:
        conditions = []
        params: List[object] = []
        normalized_symbol = str(symbol or "").upper().strip()
        normalized_kind = str(kind or "").strip()
        if normalized_symbol:
            conditions.append("symbol = ?")
            params.append(normalized_symbol)
        if normalized_kind:
            conditions.append("kind = ?")
            params.append(normalized_kind)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(max(1, min(500, int(limit or 50))))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM research_evidence
                """ + where + """
                ORDER BY
                    COALESCE(NULLIF(published_at, ''), NULLIF(observed_at, ''), last_seen_at) DESC,
                    last_seen_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [research_evidence_from_row(row) for row in rows]

    def delete(self, evidence_id: str) -> bool:
        normalized_id = str(evidence_id or "").strip()
        if not normalized_id:
            return False
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM research_evidence WHERE evidence_id = ?",
                (normalized_id,),
            )
        return int(cursor.rowcount or 0) > 0

    def summary_counts(self, column: str, limit: int = 20) -> List[Dict[str, object]]:
        if column not in {"symbol", "kind", "source", "polarity"}:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT """ + column + """ AS name, COUNT(*) AS count, MAX(last_seen_at) AS latest_seen_at
                FROM research_evidence
                WHERE """ + column + """ != ''
                GROUP BY """ + column + """
                ORDER BY count DESC, latest_seen_at DESC
                LIMIT ?
                """,
                (max(1, min(100, int(limit or 20))),),
            ).fetchall()
        return [
            {
                "name": row["name"],
                "count": int(row["count"] or 0),
                "latestSeenAt": row["latest_seen_at"],
            }
            for row in rows
        ]

    def summary(self) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count, MAX(last_seen_at) AS latest_seen_at FROM research_evidence"
            ).fetchone()
        return {
            "total": int(row["count"] or 0) if row else 0,
            "latestSeenAt": row["latest_seen_at"] if row else "",
            "bySymbol": self.summary_counts("symbol"),
            "byKind": self.summary_counts("kind"),
            "bySource": self.summary_counts("source"),
            "byPolarity": self.summary_counts("polarity"),
        }


class SQLiteOntologyQualitySampleStore(OperationalConnection):
    def record(self, sample: OntologyQualitySample) -> None:
        stamp = sample.created_at or utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO ontology_ai_opinion_samples (
                    sample_id, portfolio_id, created_at, overall_score, data_coverage_score,
                    context_coverage_score, reasoning_readiness_score, relation_density_score,
                    entity_count, relation_count, evidence_count, belief_count, opinion_count,
                    reasoning_card_count, data_gap_count, bounded_context_count, high_pressure_count,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sample.sample_id,
                    sample.portfolio_id,
                    stamp,
                    float(sample.overall_score or 0),
                    float(sample.data_coverage_score or 0),
                    float(sample.context_coverage_score or 0),
                    float(sample.reasoning_readiness_score or 0),
                    float(sample.relation_density_score or 0),
                    int(sample.entity_count or 0),
                    int(sample.relation_count or 0),
                    int(sample.evidence_count or 0),
                    int(sample.belief_count or 0),
                    int(sample.opinion_count or 0),
                    int(sample.reasoning_card_count or 0),
                    int(sample.data_gap_count or 0),
                    int(sample.bounded_context_count or 0),
                    int(sample.high_pressure_count or 0),
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
            clauses.append("portfolio_id = ?")
            params.append(str(portfolio_id))
        sql = """
            SELECT sample_id, portfolio_id, created_at, overall_score, data_coverage_score,
                   context_coverage_score, reasoning_readiness_score, relation_density_score,
                   entity_count, relation_count, evidence_count, belief_count, opinion_count,
                   reasoning_card_count, data_gap_count, bounded_context_count, high_pressure_count,
                   payload_json
            FROM ontology_ai_opinion_samples
        """
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(200, int(limit or 20))))
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        result = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            result.append({
                "sampleId": row["sample_id"],
                "portfolioId": row["portfolio_id"],
                "createdAt": row["created_at"],
                "overallScore": row["overall_score"],
                "dataCoverageScore": row["data_coverage_score"],
                "contextCoverageScore": row["context_coverage_score"],
                "reasoningReadinessScore": row["reasoning_readiness_score"],
                "relationDensityScore": row["relation_density_score"],
                "entityCount": row["entity_count"],
                "relationCount": row["relation_count"],
                "evidenceCount": row["evidence_count"],
                "beliefCount": row["belief_count"],
                "opinionCount": row["opinion_count"],
                "reasoningCardCount": row["reasoning_card_count"],
                "dataGapCount": row["data_gap_count"],
                "boundedContextCount": row["bounded_context_count"],
                "highPressureCount": row["high_pressure_count"],
                "payload": payload if isinstance(payload, dict) else {},
            })
        return result


class SQLiteMarketQuoteCache(OperationalConnection):
    def save(self, provider: str, account_id: str, symbol: str, payload: Dict[str, object]) -> None:
        clean_symbol = str(symbol or "").upper().strip()
        if not clean_symbol or not isinstance(payload, dict):
            return
        stamp = utc_now()
        cached = dict(payload)
        cached.setdefault("updatedAt", stamp)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO market_quote_cache (provider, account_id, symbol, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider, account_id, symbol) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    str(provider or "").strip().lower() or "unknown",
                    str(account_id or "").strip(),
                    clean_symbol,
                    json_dumps(cached),
                    stamp,
                ),
            )

    def load(self, provider: str, account_id: str, symbol: str) -> Dict[str, object]:
        clean_symbol = str(symbol or "").upper().strip()
        if not clean_symbol:
            return {}
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json, updated_at
                FROM market_quote_cache
                WHERE provider = ? AND account_id = ? AND symbol = ?
                """,
                (
                    str(provider or "").strip().lower() or "unknown",
                    str(account_id or "").strip(),
                    clean_symbol,
                ),
            ).fetchone()
        if not row:
            return {}
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        payload.setdefault("updatedAt", row["updated_at"])
        payload.setdefault("symbol", clean_symbol)
        return payload

    def load_many(self, provider: str, account_id: str, symbols: Iterable[str]) -> Dict[str, Dict[str, object]]:
        return {
            str(symbol or "").upper(): payload
            for symbol in symbols
            for payload in [self.load(provider, account_id, str(symbol or ""))]
            if payload
        }

    def stale_universe_symbols(
        self,
        provider: str,
        account_id: str,
        markets: Iterable[str] = None,
        limit: int = 200,
        max_age_minutes: int = 240,
    ) -> List[Dict[str, object]]:
        clean_markets = [normalize_market(market) for market in (markets or []) if normalize_market(market)]
        limit_value = max(1, min(1000, int(limit or 200)))
        age_minutes = max(0, int(max_age_minutes or 0))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
        clauses = ["su.active = 1"]
        params: List[object] = [
            str(provider or "").strip().lower() or "unknown",
            str(account_id or "").strip(),
        ]
        if clean_markets:
            clauses.append("su.market IN (" + ",".join(["?"] * len(clean_markets)) + ")")
            params.extend(clean_markets)
        clauses.append("(mq.updated_at IS NULL OR mq.updated_at <= ?)")
        params.append(cutoff_text)
        sql = """
            SELECT su.symbol, su.name, su.market, su.exchange, su.currency, su.sector,
                   su.asset_type, mq.updated_at AS quote_updated_at
            FROM symbol_universe su
            LEFT JOIN market_quote_cache mq
              ON mq.provider = ? AND mq.account_id = ? AND mq.symbol = su.symbol
            WHERE """ + " AND ".join(clauses) + """
            ORDER BY
                CASE WHEN mq.updated_at IS NULL THEN 0 ELSE 1 END,
                COALESCE(mq.updated_at, ''),
                CASE su.market WHEN 'KOSPI' THEN 1 WHEN 'KOSDAQ' THEN 2 WHEN 'NASDAQ' THEN 3 ELSE 9 END,
                su.symbol
            LIMIT ?
        """
        with self.connect() as connection:
            rows = connection.execute(sql, params + [limit_value]).fetchall()
        return [
            {
                "symbol": row["symbol"],
                "name": row["name"],
                "market": row["market"],
                "exchange": row["exchange"],
                "currency": row["currency"],
                "sector": row["sector"],
                "assetType": row["asset_type"],
                "quoteUpdatedAt": row["quote_updated_at"] or "",
            }
            for row in rows
        ]

    def summary(self, provider: str, account_id: str) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT symbol, payload_json, updated_at
                FROM market_quote_cache
                WHERE provider = ? AND account_id = ?
                ORDER BY updated_at DESC
                """,
                (
                    str(provider or "").strip().lower() or "unknown",
                    str(account_id or "").strip(),
                ),
            ).fetchall()
        markets: Dict[str, int] = {}
        qualities: Dict[str, int] = {}
        latest = ""
        for row in rows:
            latest = latest or row["updated_at"]
            try:
                payload = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                payload = {}
            market = str(payload.get("market") or "UNKNOWN")
            quality = str(payload.get("dataQuality") or "unknown")
            markets[market] = markets.get(market, 0) + 1
            qualities[quality] = qualities.get(quality, 0) + 1
        return {
            "provider": str(provider or "").strip().lower() or "unknown",
            "accountId": str(account_id or "").strip(),
            "count": len(rows),
            "latestUpdatedAt": latest,
            "markets": markets,
            "dataQuality": qualities,
        }


class SQLiteSymbolUniverseStore(OperationalConnection):
    def upsert_many_with_connection(self, connection, symbols: Iterable[ListedSymbol], stamp: str = "") -> int:
        items = [item for item in symbols if item.symbol and item.market]
        if not items:
            return 0
        stamp = stamp or utc_now()
        for item in items:
            existing = connection.execute(
                "SELECT first_seen_at FROM symbol_universe WHERE market = ? AND symbol = ?",
                (item.market, item.symbol),
            ).fetchone()
            if existing and existing["first_seen_at"]:
                item.first_seen_at = existing["first_seen_at"]
            payload = item.to_dict(max_age_hours=24)
            connection.execute(
                """
                INSERT INTO symbol_universe (
                    market, symbol, name, exchange, currency, sector, asset_type,
                    source, source_url, active, fetched_at, first_seen_at, last_seen_at,
                    payload_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market, symbol) DO UPDATE SET
                    name = excluded.name,
                    exchange = excluded.exchange,
                    currency = excluded.currency,
                    sector = excluded.sector,
                    asset_type = excluded.asset_type,
                    source = excluded.source,
                    source_url = excluded.source_url,
                    active = excluded.active,
                    fetched_at = excluded.fetched_at,
                    last_seen_at = excluded.last_seen_at,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    item.market,
                    item.symbol,
                    item.name,
                    item.exchange,
                    item.currency,
                    item.sector,
                    item.asset_type,
                    item.source,
                    item.source_url,
                    1 if item.active else 0,
                    item.fetched_at,
                    item.first_seen_at,
                    item.last_seen_at,
                    json_dumps(payload),
                    stamp,
                ),
            )
        return len(items)

    def upsert_many(self, symbols: Iterable[ListedSymbol]) -> int:
        with self.connect() as connection:
            return self.upsert_many_with_connection(connection, symbols)

    def row_to_symbol(self, row) -> ListedSymbol:
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            payload = {}
        payload.update({
            "symbol": row["symbol"],
            "name": row["name"],
            "market": row["market"],
            "exchange": row["exchange"],
            "currency": row["currency"],
            "sector": row["sector"],
            "assetType": row["asset_type"],
            "source": row["source"],
            "sourceUrl": row["source_url"],
            "active": bool(row["active"]),
            "fetchedAt": row["fetched_at"],
            "firstSeenAt": row["first_seen_at"],
            "lastSeenAt": row["last_seen_at"],
        })
        return ListedSymbol.from_dict(payload)

    def symbol_search_clauses(self, query: str = "", market: str = ""):
        query_value = str(query or "").strip()
        market_value = normalize_market(market)
        clauses = ["active = 1"]
        params: List[object] = []
        if market_value:
            clauses.append("market = ?")
            params.append(market_value)
        if query_value:
            clauses.append("(symbol LIKE ? OR name LIKE ?)")
            like = "%" + query_value.upper() + "%"
            params.extend([like, "%" + query_value + "%"])
        return query_value, clauses, params

    def search(self, query: str = "", market: str = "", limit: int = 80, offset: int = 0) -> List[ListedSymbol]:
        query_value, clauses, params = self.symbol_search_clauses(query, market)
        limit_value = max(1, min(500, int(limit or 80)))
        offset_value = max(0, int(offset or 0))
        exact_symbol = normalize_symbol(query_value)
        sql = """
            SELECT * FROM symbol_universe
            WHERE """ + " AND ".join(clauses) + """
            ORDER BY
                CASE WHEN ? != '' AND symbol = ? THEN 0 WHEN ? != '' AND symbol LIKE ? THEN 1 ELSE 2 END,
                CASE market WHEN 'KOSPI' THEN 1 WHEN 'KOSDAQ' THEN 2 WHEN 'NASDAQ' THEN 3 ELSE 9 END,
                symbol
            LIMIT ? OFFSET ?
        """
        with self.connect() as connection:
            rows = connection.execute(
                sql,
                params + [exact_symbol, exact_symbol, exact_symbol, exact_symbol + "%", limit_value, offset_value],
            ).fetchall()
        return [self.row_to_symbol(row) for row in rows]

    def search_count(self, query: str = "", market: str = "") -> int:
        _, clauses, params = self.symbol_search_clauses(query, market)
        sql = "SELECT COUNT(*) AS count FROM symbol_universe WHERE " + " AND ".join(clauses)
        with self.connect() as connection:
            row = connection.execute(sql, params).fetchone()
        return int(row["count"] if row else 0)

    def get(self, symbol: str, market: str = "") -> Optional[ListedSymbol]:
        clean_symbol = normalize_symbol(symbol)
        clean_market = normalize_market(market)
        if not clean_symbol:
            return None
        with self.connect() as connection:
            if clean_market:
                row = connection.execute(
                    "SELECT * FROM symbol_universe WHERE market = ? AND symbol = ?",
                    (clean_market, clean_symbol),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM symbol_universe
                    WHERE symbol = ?
                    ORDER BY CASE market WHEN 'KOSPI' THEN 1 WHEN 'KOSDAQ' THEN 2 WHEN 'NASDAQ' THEN 3 ELSE 9 END
                    LIMIT 1
                    """,
                    (clean_symbol,),
                ).fetchone()
        return self.row_to_symbol(row) if row else None

    def counts_by_market(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT market, COUNT(*) AS count FROM symbol_universe WHERE active = 1 GROUP BY market"
            ).fetchall()
        return {row["market"]: int(row["count"]) for row in rows}

    def latest_seen_by_market(self) -> Dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT market, MAX(last_seen_at) AS last_seen_at FROM symbol_universe WHERE active = 1 GROUP BY market"
            ).fetchall()
        return {row["market"]: row["last_seen_at"] or "" for row in rows}

    def mark_source_with_connection(
        self,
        connection,
        market: str,
        source: str,
        source_url: str,
        status: str,
        count: int = 0,
        error: str = "",
        stamp: str = "",
    ) -> None:
        stamp = stamp or symbol_utc_now_iso()
        success_at = stamp if status == "ok" else ""
        existing = connection.execute(
            "SELECT last_success_at FROM symbol_universe_sources WHERE market = ?",
            (normalize_market(market),),
        ).fetchone()
        last_success_at = success_at or (existing["last_success_at"] if existing else "")
        connection.execute(
            """
            INSERT INTO symbol_universe_sources (
                market, source, source_url, status, record_count, last_attempt_at,
                last_success_at, last_error, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market) DO UPDATE SET
                source = excluded.source,
                source_url = excluded.source_url,
                status = excluded.status,
                record_count = excluded.record_count,
                last_attempt_at = excluded.last_attempt_at,
                last_success_at = excluded.last_success_at,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (
                normalize_market(market),
                str(source or ""),
                str(source_url or ""),
                str(status or ""),
                int(count or 0),
                stamp,
                last_success_at,
                str(error or ""),
                stamp,
            ),
        )

    def mark_source(self, market: str, source: str, source_url: str, status: str, count: int = 0, error: str = "") -> None:
        with self.connect() as connection:
            self.mark_source_with_connection(connection, market, source, source_url, status, count, error)

    def refresh_market(self, market: str, source: str, source_url: str, symbols: Iterable[ListedSymbol]) -> int:
        stamp = symbol_utc_now_iso()
        with self.connect() as connection:
            count = self.upsert_many_with_connection(connection, symbols, stamp)
            self.mark_source_with_connection(connection, market, source, source_url, "ok", count=count, stamp=stamp)
        return count

    def source_states(self) -> List[Dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT market, source, source_url, status, record_count, last_attempt_at,
                       last_success_at, last_error, updated_at
                FROM symbol_universe_sources
                ORDER BY CASE market WHEN 'KOSPI' THEN 1 WHEN 'KOSDAQ' THEN 2 WHEN 'NASDAQ' THEN 3 ELSE 9 END
                """
            ).fetchall()
        return [
            {
                "market": row["market"],
                "source": row["source"],
                "sourceUrl": row["source_url"],
                "status": row["status"],
                "recordCount": int(row["record_count"]),
                "lastAttemptAt": row["last_attempt_at"],
                "lastSuccessAt": row["last_success_at"],
                "lastError": row["last_error"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]


class SQLiteRuntimeSettingsStore(OperationalConnection):
    def __init__(self, path: Optional[Path] = None, legacy_path: Optional[Path] = None):
        self.legacy_path = legacy_path or settings_path()
        super().__init__(path)
        self.migrate_legacy_if_needed()

    def migrate_legacy_if_needed(self) -> None:
        if not self.legacy_path.exists():
            return
        with self.connect() as connection:
            existing = int(connection.execute("SELECT COUNT(*) AS count FROM runtime_settings").fetchone()["count"])
        if existing:
            return
        payload = read_json(self.legacy_path, {})
        if isinstance(payload, dict):
            self.replace({str(key): str(value or "") for key, value in payload.items()})

    def load(self) -> Dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT key, value FROM runtime_settings ORDER BY key").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def replace(self, settings: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.connect() as connection:
            connection.execute("DELETE FROM runtime_settings")
            for key, value in settings.items():
                connection.execute(
                    "INSERT INTO runtime_settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (str(key), str(value or ""), stamp),
                )

    def save(self, settings: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.connect() as connection:
            for key, value in settings.items():
                connection.execute(
                    """
                    INSERT INTO runtime_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (str(key), str(value or ""), stamp),
                )


class SQLiteMonitorStore(OperationalConnection):
    def __init__(self, path: Optional[Path] = None, legacy_path: Optional[Path] = None):
        self.legacy_path = legacy_path or data_dir() / "python-monitor-state.json"
        super().__init__(path)
        self.migrate_legacy_if_needed()
        self.payload = {"previous": self.load_previous(), "sent": self.load_sent()}

    def migrate_legacy_if_needed(self) -> None:
        if not self.legacy_path.exists():
            return
        with self.connect() as connection:
            snapshot_count = int(connection.execute("SELECT COUNT(*) AS count FROM monitor_snapshots").fetchone()["count"])
            sent_count = int(connection.execute("SELECT COUNT(*) AS count FROM monitor_sent").fetchone()["count"])
        if snapshot_count or sent_count:
            return
        payload = read_json(self.legacy_path, {"previous": {}, "sent": {}})
        if not isinstance(payload, dict):
            return
        for account_id, state in (payload.get("previous") or {}).items():
            if isinstance(state, dict):
                self.upsert_snapshot_state(str(account_id), state)
        stamp = utc_now()
        with self.connect() as connection:
            for key, sent_at in (payload.get("sent") or {}).items():
                connection.execute(
                    "INSERT OR REPLACE INTO monitor_sent (key, sent_at) VALUES (?, ?)",
                    (str(key), str(sent_at or stamp)),
                )

    def load_previous(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute("SELECT account_id, payload_json FROM monitor_snapshots").fetchall()
        previous = {}
        for row in rows:
            try:
                previous[row["account_id"]] = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                previous[row["account_id"]] = {}
        return previous

    def load_sent(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute("SELECT key, sent_at FROM monitor_sent").fetchall()
        return {row["key"]: row["sent_at"] for row in rows}

    @property
    def previous(self) -> Dict[str, object]:
        return self.payload["previous"]

    @property
    def sent(self) -> Dict[str, object]:
        return self.payload["sent"]

    def upsert_snapshot_state_with_connection(self, connection, account_id: str, state: Dict[str, object], stamp: str = "") -> None:
        connection.execute(
            """
            INSERT INTO monitor_snapshots (
                account_id, account_label, provider, mode, status, generated_at, payload_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                account_label = excluded.account_label,
                provider = excluded.provider,
                mode = excluded.mode,
                status = excluded.status,
                generated_at = excluded.generated_at,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                account_id,
                str(state.get("accountLabel") or ""),
                str(state.get("provider") or ""),
                str(state.get("mode") or ""),
                str(state.get("status") or ""),
                str(state.get("generatedAt") or ""),
                json_dumps(state),
                stamp or utc_now(),
            ),
        )

    def upsert_snapshot_state(self, account_id: str, state: Dict[str, object]) -> None:
        with self.connect() as connection:
            self.upsert_snapshot_state_with_connection(connection, account_id, state)

    def save_snapshot(self, snapshot: AccountSnapshot) -> None:
        state = snapshot.to_monitor_state()
        self.upsert_snapshot_state(snapshot.account_id, state)
        self.previous[snapshot.account_id] = state

    def sent_entries(self, events: Iterable[AlertEvent], stamp: str) -> Dict[str, str]:
        entries: Dict[str, str] = {}
        for event in events:
            entries[event.key] = stamp
            entries[event.cadence_key()] = stamp
        return entries

    def mark_sent_with_connection(self, connection, events: Iterable[AlertEvent], stamp: str) -> Dict[str, str]:
        entries = self.sent_entries(events, stamp)
        for key, sent_at in entries.items():
            connection.execute(
                "INSERT OR REPLACE INTO monitor_sent (key, sent_at) VALUES (?, ?)",
                (key, sent_at),
            )
        return entries

    def mark_sent(self, events: Iterable[AlertEvent]) -> None:
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self.connect() as connection:
            entries = self.mark_sent_with_connection(connection, events, stamp)
        self.sent.update(entries)

    def record_cycle(
        self,
        account_ids: List[str],
        snapshots: List[AccountSnapshot],
        alert_events: List[AlertEvent],
        dry_run: bool = False,
    ) -> MonitoringCycleRecordResult:
        return SQLiteMonitoringCycleRecorder(self.path, monitor_store=self).record_cycle(
            account_ids,
            snapshots,
            alert_events,
            dry_run=dry_run,
        )

    def write(self) -> None:
        pass


class SQLiteMonitoringCycleRecorder(OperationalConnection):
    def __init__(self, path: Optional[Path] = None, monitor_store: SQLiteMonitorStore = None):
        self.monitor_store = monitor_store
        super().__init__(path or (monitor_store.path if monitor_store else None))
        if self.monitor_store is None:
            self.monitor_store = SQLiteMonitorStore(self.path)

    def record_cycle(
        self,
        account_ids: List[str],
        snapshots: List[AccountSnapshot],
        alert_events: List[AlertEvent],
        dry_run: bool = False,
    ) -> MonitoringCycleRecordResult:
        if dry_run:
            return MonitoringCycleRecordResult(False, 0, "dry-run")
        SQLiteNotificationTemplateStore(self.path)
        notification_store = SQLiteNotificationJobStore(self.path)
        model_review_store = SQLiteModelReviewJobStore(self.path)
        snapshot_states = {
            snapshot.account_id: snapshot.to_monitor_state()
            for snapshot in snapshots
        }
        delivered = bool(alert_events)
        alert_source_event = alerts_detected_event(alert_events) if alert_events else None
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        queued = 0
        sent_entries: Dict[str, str] = {}
        with self.connect() as connection:
            for snapshot in snapshots:
                insert_domain_event_with_connection(connection, snapshot_collected_event(snapshot))
            if alert_source_event:
                insert_domain_event_with_connection(connection, alert_source_event)
                queued = self.enqueue_alert_notifications_with_connection(
                    connection,
                    notification_store,
                    alert_events,
                    alert_source_event,
                )
                model_review_store.enqueue_from_event_with_connection(connection, alert_source_event)
                sent_entries = self.monitor_store.mark_sent_with_connection(connection, alert_events, stamp)
            insert_domain_event_with_connection(
                connection,
                monitoring_cycle_completed_event(
                    list(account_ids or []),
                    len(snapshots),
                    len(alert_events),
                    False,
                    delivered,
                ),
            )
            for account_id, state in snapshot_states.items():
                self.monitor_store.upsert_snapshot_state_with_connection(connection, account_id, state, stamp)
        self.monitor_store.previous.update(snapshot_states)
        self.monitor_store.sent.update(sent_entries)
        return MonitoringCycleRecordResult(delivered, queued, "queued=" + str(queued))

    def notification_template_for_connection(self, connection, message_type: str) -> NotificationTemplate:
        row = connection.execute(
            """
            SELECT message_type, template, description, enabled, updated_at
            FROM notification_templates
            WHERE message_type = ?
            """,
            (str(message_type or "notification"),),
        ).fetchone()
        if not row:
            row = connection.execute(
                """
                SELECT message_type, template, description, enabled, updated_at
                FROM notification_templates
                WHERE message_type = 'default'
                """
            ).fetchone()
        return template_from_row(row) if row else NotificationTemplate.default("default")

    def enqueue_alert_notifications_with_connection(
        self,
        connection,
        notification_store,
        events: Iterable[AlertEvent],
        source_event: DomainEvent,
    ) -> int:
        queued = 0
        for event in events:
            context = alert_context(event)
            template = self.notification_template_for_connection(connection, event.rule)
            message = render_notification(template, context)
            job = NotificationJob.create(
                message,
                account_id=event.account_id,
                account_label=event.account_label,
                message_type=event.rule or "alert",
                source_event_id=source_event.event_id,
                source_event_name=source_event.name,
                dedupe_key=":".join(["outbox", source_event.event_id, event.key]),
                context=context,
            )
            if notification_store.enqueue_with_connection(connection, job):
                queued += 1
        return queued


class SQLiteEventLog(OperationalConnection):
    def __init__(self, path: Optional[Path] = None, legacy_path: Optional[Path] = None):
        self.legacy_path = legacy_path or data_dir() / "domain-events.jsonl"
        super().__init__(path)
        self.migrate_legacy_if_needed()

    def migrate_legacy_if_needed(self) -> None:
        if not self.legacy_path.exists():
            return
        with self.connect() as connection:
            existing = int(connection.execute("SELECT COUNT(*) AS count FROM domain_events").fetchone()["count"])
        if existing:
            return
        with self.legacy_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self.insert_event_dict(payload)

    def insert_event_dict(self, event: Dict[str, object]) -> None:
        self.handle(DomainEvent.from_dict(event))

    def handle(self, event: DomainEvent) -> None:
        with self.connect() as connection:
            insert_domain_event_with_connection(connection, event)

    def events(self, name: str = "", aggregate_id: str = "", limit: int = 0) -> List[DomainEvent]:
        clauses = []
        params = []
        if name:
            clauses.append("name = ?")
            params.append(name)
        if aggregate_id:
            clauses.append("aggregate_id = ?")
            params.append(aggregate_id)
        sql = "SELECT event_json FROM domain_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_at, event_id"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        events = []
        for row in rows:
            try:
                events.append(DomainEvent.from_dict(json.loads(row["event_json"])))
            except json.JSONDecodeError:
                continue
        return events

    def event_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for event in self.events():
            counts[event.name] = counts.get(event.name, 0) + 1
        return counts


class SQLiteOntologyReasoningCursorStore(OperationalConnection):
    store_id = "ontology_reasoning_cursor"

    def load(self) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM app_store WHERE store_id = ?",
                (self.store_id,),
            ).fetchone()
        if not row:
            return {"processedEventIds": []}
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            return {"processedEventIds": []}
        return payload if isinstance(payload, dict) else {"processedEventIds": []}

    def processed_event_ids(self) -> List[str]:
        return [
            str(item or "").strip()
            for item in (self.load().get("processedEventIds") or [])
            if str(item or "").strip()
        ]

    def mark_processed(self, event_ids: Iterable[str]) -> None:
        existing = self.processed_event_ids()
        seen = set(existing)
        merged = list(existing)
        for event_id in event_ids or []:
            clean = str(event_id or "").strip()
            if clean and clean not in seen:
                seen.add(clean)
                merged.append(clean)
        payload = {
            "processedEventIds": merged[-1000:],
            "updatedAt": utc_now(),
        }
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_store (store_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(store_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (self.store_id, json_dumps(payload), payload["updatedAt"]),
            )


class SQLiteModelReviewJobStore(OperationalConnection):
    def __init__(self, path: Optional[Path] = None, legacy_path: Optional[Path] = None):
        self.legacy_path = legacy_path or data_dir() / "model-review-queue.json"
        super().__init__(path)
        self.migrate_legacy_if_needed()

    def migrate_legacy_if_needed(self) -> None:
        if not self.legacy_path.exists():
            return
        with self.connect() as connection:
            existing = int(connection.execute("SELECT COUNT(*) AS count FROM model_review_jobs").fetchone()["count"])
        if existing:
            return
        payload = read_json(self.legacy_path, {"jobs": []})
        for item in payload.get("jobs") or []:
            if isinstance(item, dict):
                self.upsert_job(ModelReviewJob.from_dict(item))

    def jobs(self) -> List[ModelReviewJob]:
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM model_review_jobs ORDER BY created_at, job_id").fetchall()
        jobs = []
        for row in rows:
            try:
                jobs.append(ModelReviewJob.from_dict(json.loads(row["payload_json"])))
            except json.JSONDecodeError:
                continue
        return jobs

    def write_jobs(self, jobs: Iterable[ModelReviewJob]) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM model_review_jobs")
            for job in jobs:
                self.upsert_job_with_connection(connection, job)

    def upsert_job_with_connection(self, connection, job: ModelReviewJob) -> None:
        payload = job.to_dict()
        connection.execute(
            """
            INSERT INTO model_review_jobs (
                job_id, account_id, account_label, symbol, title, alert_key, status, attempts,
                created_at, updated_at, result, last_error, alert_lines_json, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                account_id = excluded.account_id,
                account_label = excluded.account_label,
                symbol = excluded.symbol,
                title = excluded.title,
                alert_key = excluded.alert_key,
                status = excluded.status,
                attempts = excluded.attempts,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                result = excluded.result,
                last_error = excluded.last_error,
                alert_lines_json = excluded.alert_lines_json,
                payload_json = excluded.payload_json
            """,
            (
                job.job_id,
                job.account_id,
                job.account_label,
                job.symbol,
                job.title,
                job.alert_key,
                job.status,
                job.attempts,
                job.created_at,
                job.updated_at,
                job.result,
                job.last_error,
                json_dumps(job.alert_lines),
                json_dumps(payload),
            ),
        )

    def upsert_job(self, job: ModelReviewJob) -> None:
        with self.connect() as connection:
            self.upsert_job_with_connection(connection, job)

    def enqueue_with_connection(self, connection, job: ModelReviewJob) -> bool:
        existing = connection.execute("SELECT job_id FROM model_review_jobs WHERE job_id = ?", (job.job_id,)).fetchone()
        if existing:
            return False
        self.upsert_job_with_connection(connection, job)
        return True

    def enqueue(self, job: ModelReviewJob) -> bool:
        with self.connect() as connection:
            return self.enqueue_with_connection(connection, job)

    def enqueue_from_event_with_connection(self, connection, event: DomainEvent) -> int:
        count = 0
        for item in model_review_payloads_from_event(event):
            if self.enqueue_with_connection(connection, ModelReviewJob.create(item)):
                count += 1
        return count

    def enqueue_from_event(self, event: DomainEvent) -> int:
        with self.connect() as connection:
            return self.enqueue_from_event_with_connection(connection, event)

    def pending(self, limit: int = 1) -> List[ModelReviewJob]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM model_review_jobs
                WHERE status IN ('pending', 'failed')
                ORDER BY created_at, job_id
                LIMIT ?
                """,
                (int(limit or 1),),
            ).fetchall()
        jobs = []
        for row in rows:
            try:
                jobs.append(ModelReviewJob.from_dict(json.loads(row["payload_json"])))
            except json.JSONDecodeError:
                continue
        return jobs

    def update(self, updated: ModelReviewJob) -> None:
        self.upsert_job(updated)

    def mark_processing(self, job: ModelReviewJob) -> ModelReviewJob:
        job.status = "processing"
        job.attempts += 1
        job.updated_at = utc_now()
        self.update(job)
        return job

    def mark_done(self, job: ModelReviewJob, result: str) -> None:
        job.status = "done"
        job.result = result
        job.last_error = ""
        job.updated_at = utc_now()
        self.update(job)

    def mark_failed(self, job: ModelReviewJob, error: str) -> None:
        job.status = "failed"
        job.last_error = error
        job.updated_at = utc_now()
        self.update(job)

    def summary(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM model_review_jobs GROUP BY status").fetchall()
        return {row["status"]: int(row["count"]) for row in rows}


class SQLiteNotificationJobStore(OperationalConnection):
    def __init__(self, path: Optional[Path] = None):
        super().__init__(path)
        if not self.notification_rule_defaults_exist():
            SQLiteNotificationRuleStore(self.path)

    def notification_rule_defaults_exist(self) -> bool:
        message_types = list(DEFAULT_NOTIFICATION_RULES.keys())
        if not message_types:
            return True
        placeholders = ",".join(["?"] * len(message_types))
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM notification_rules WHERE message_type IN (" + placeholders + ")",
                message_types,
            ).fetchone()
        return int(row["count"] if row else 0) >= len(message_types)

    def jobs(self) -> List[NotificationJob]:
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM notification_jobs ORDER BY created_at, job_id").fetchall()
        jobs = []
        for row in rows:
            try:
                jobs.append(NotificationJob.from_dict(json.loads(row["payload_json"])))
            except json.JSONDecodeError:
                continue
        return jobs

    def recent(self, limit: int = 40, message_type: str = "", status: str = "") -> List[NotificationJob]:
        clauses = []
        params = []
        if str(message_type or "").strip():
            clauses.append("message_type = ?")
            params.append(str(message_type or "").strip())
        if str(status or "").strip():
            clauses.append("status = ?")
            params.append(str(status or "").strip())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(200, int(limit or 40))))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM notification_jobs
                """ + where + """
                ORDER BY created_at DESC, job_id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        jobs = []
        for row in rows:
            try:
                jobs.append(NotificationJob.from_dict(json.loads(row["payload_json"])))
            except json.JSONDecodeError:
                continue
        return jobs

    def upsert_job_with_connection(self, connection, job: NotificationJob) -> None:
        payload = job.to_dict()
        connection.execute(
            """
            INSERT INTO notification_jobs (
                job_id, account_id, account_label, message_type, source_event_id, source_event_name, dedupe_key, status, attempts,
                created_at, updated_at, last_error, text, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                account_id = excluded.account_id,
                account_label = excluded.account_label,
                message_type = excluded.message_type,
                source_event_id = excluded.source_event_id,
                source_event_name = excluded.source_event_name,
                dedupe_key = excluded.dedupe_key,
                status = excluded.status,
                attempts = excluded.attempts,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                last_error = excluded.last_error,
                text = excluded.text,
                payload_json = excluded.payload_json
            """,
            (
                job.job_id,
                job.account_id,
                job.account_label,
                job.message_type,
                job.source_event_id,
                job.source_event_name,
                job.dedupe_key,
                job.status,
                job.attempts,
                job.created_at,
                job.updated_at,
                job.last_error,
                job.text,
                json_dumps(payload),
            ),
        )

    def upsert_job(self, job: NotificationJob) -> None:
        with self.connect() as connection:
            self.upsert_job_with_connection(connection, job)

    def rule_for_connection(self, connection, message_type: str) -> NotificationRuleConfig:
        key = str(message_type or "notification").strip() or "notification"
        row = connection.execute(
            """
            SELECT message_type, enabled, threshold, base_score, low_score_action, conditions_json,
                similarity_enabled, similarity_window_minutes, similarity_penalty, similarity_bypass_score_delta,
                similarity_bypass_conditions_json, similarity_fields_json, state_cooldown_enabled, state_cooldown_minutes,
                market_hours_enabled, market_hours_markets_json, updated_at
            FROM notification_rules
            WHERE message_type = ?
            """,
            (key,),
        ).fetchone()
        return rule_from_row(row) if row else default_notification_rule(key)

    def similar_history_with_connection(
        self,
        connection,
        job: NotificationJob,
        rule: NotificationRuleConfig,
        fingerprint: str,
    ):
        if not rule.similarity_enabled or not int(rule.similarity_window_minutes or 0) or not fingerprint:
            similarity_minutes = 0
        else:
            similarity_minutes = int(rule.similarity_window_minutes or 0)
        state_minutes = int(rule.state_cooldown_minutes or 0) + 60 if rule.state_cooldown_enabled and int(rule.state_cooldown_minutes or 0) else 0
        history_minutes = max(similarity_minutes, state_minutes)
        if not history_minutes or not fingerprint:
            return 0, 0, {}, ""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=history_minutes)
        cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
        rows = connection.execute(
            """
            SELECT payload_json, created_at FROM notification_jobs
            WHERE message_type = ? AND created_at >= ? AND status IN ('pending', 'processing', 'done')
            ORDER BY created_at DESC
            LIMIT 100
            """,
            (job.message_type, cutoff_text),
        ).fetchall()
        count = 0
        previous_score = 0
        most_recent_context: Dict[str, object] = {}
        most_recent_at = ""
        for row in rows:
            try:
                previous = NotificationJob.from_dict(json.loads(row["payload_json"]))
            except json.JSONDecodeError:
                continue
            if previous.job_id == job.job_id:
                continue
            previous_context = previous.context or {}
            previous_fingerprint = str(previous_context.get("honeyFingerprint") or notification_fingerprint(previous, rule))
            if previous_fingerprint != fingerprint:
                continue
            count += 1
            if not most_recent_context:
                most_recent_context = dict(previous_context)
                most_recent_at = row["created_at"] or previous.created_at
            previous_score = max(previous_score, int(previous_context.get("honeyScore") or 0))
        return count, previous_score, most_recent_context, most_recent_at

    def evaluate_job_with_connection(self, connection, job: NotificationJob):
        rule = self.rule_for_connection(connection, job.message_type)
        decision = evaluate_notification_rule(job, rule)
        recent_count, previous_score, previous_context, last_sent_at = self.similar_history_with_connection(connection, job, rule, decision.fingerprint)
        decision = apply_state_cooldown_rule(
            decision,
            rule,
            recent_count,
            previous_score,
            previous_context,
            last_sent_at,
            age_minutes_since(last_sent_at),
            job,
        )
        decision = apply_similarity_rule(decision, rule, recent_count, previous_score, previous_context, job)
        return apply_market_hours_rule(decision, rule, job)

    def enqueue_with_connection(self, connection, job: NotificationJob) -> bool:
        if not job.text.strip():
            return False
        existing = connection.execute("SELECT job_id FROM notification_jobs WHERE job_id = ?", (job.job_id,)).fetchone()
        if existing:
            return False
        if job.dedupe_key:
            existing = connection.execute(
                "SELECT job_id FROM notification_jobs WHERE dedupe_key = ?",
                (job.dedupe_key,),
            ).fetchone()
            if existing:
                return False

        decision = self.evaluate_job_with_connection(connection, job)
        context = dict(job.context or {})
        context.update(decision.to_context())
        freshness_decision = evaluate_notification_data_freshness(context, runtime_settings())
        context.update(freshness_decision.to_context())
        job.context = context
        if decision.should_send and not freshness_decision.should_send:
            job.status = "suppressed"
            job.updated_at = utc_now()
            job.last_error = "데이터 신선도 기준 미통과로 발송하지 않았습니다. " + str(freshness_decision.reason or "")
            job.context["honeySuppressionReason"] = "stale_data"
            try:
                self.upsert_job_with_connection(connection, job)
            except sqlite3.IntegrityError:
                return False
            return False
        if not decision.should_send:
            job.status = "suppressed"
            job.updated_at = utc_now()
            if decision.suppression_reason == "market_closed":
                job.last_error = "장 시간 외라 발송하지 않았습니다. " + str(decision.market_hours_reason or "")
            elif decision.suppression_reason == "state_cooldown":
                job.last_error = decision.state_reason or "같은 임계값 상태가 지속되어 발송하지 않았습니다."
            else:
                job.last_error = "발송 우선도 " + str(decision.score) + "이 기준 " + str(decision.threshold) + "보다 낮아 발송하지 않았습니다."
            try:
                self.upsert_job_with_connection(connection, job)
            except sqlite3.IntegrityError:
                return False
            return False

        try:
            self.upsert_job_with_connection(connection, job)
        except sqlite3.IntegrityError:
            return False
        return True

    def enqueue(self, job: NotificationJob) -> bool:
        with self.connect() as connection:
            return self.enqueue_with_connection(connection, job)

    def pending(self, limit: int = 10) -> List[NotificationJob]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM notification_jobs
                WHERE status IN ('pending', 'failed')
                ORDER BY created_at, job_id
                LIMIT ?
                """,
                (int(limit or 10),),
            ).fetchall()
        jobs = []
        for row in rows:
            try:
                jobs.append(NotificationJob.from_dict(json.loads(row["payload_json"])))
            except json.JSONDecodeError:
                continue
        return jobs

    def update(self, updated: NotificationJob) -> None:
        self.upsert_job(updated)

    def mark_processing(self, job: NotificationJob) -> NotificationJob:
        job.status = "processing"
        job.attempts += 1
        job.updated_at = utc_now()
        self.update(job)
        return job

    def mark_done(self, job: NotificationJob) -> None:
        job.status = "done"
        job.last_error = ""
        job.updated_at = utc_now()
        self.update(job)

    def mark_failed(self, job: NotificationJob, error: str) -> None:
        job.status = "failed"
        job.last_error = error
        job.updated_at = utc_now()
        self.update(job)

    def mark_suppressed(self, job: NotificationJob, reason: str) -> None:
        job.status = "suppressed"
        job.last_error = str(reason or "알림 정책으로 발송하지 않았습니다.")
        job.updated_at = utc_now()
        self.update(job)

    def summary(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM notification_jobs GROUP BY status").fetchall()
        return {row["status"]: int(row["count"]) for row in rows}
