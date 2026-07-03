import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..domain.events import DomainEvent, MONITORING_ALERTS_DETECTED
from ..domain.model_review import ModelReviewJob
from ..domain.notification_rules import DEFAULT_NOTIFICATION_RULES, NotificationRuleConfig, apply_similarity_rule, default_notification_rule, evaluate_notification_rule, notification_fingerprint
from ..domain.notification_templates import DEFAULT_NOTIFICATION_TEMPLATES, LEGACY_DEFAULT_TEMPLATE, PREVIOUS_DEFAULT_TEMPLATE, NotificationTemplate, render_notification
from ..domain.notifications import NotificationJob
from ..domain.portfolio import AccountSnapshot, AlertEvent
from ..domain.symbol_universe import ListedSymbol, normalize_market, normalize_symbol, utc_now_iso as symbol_utc_now_iso
from .settings import data_dir, read_json, service_db_path, settings_path, utc_now


def json_dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class OperationalConnection:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or service_db_path()).resolve()
        self.ensure_schema()

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return connection

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
                    similarity_fields_json TEXT NOT NULL DEFAULT '["messageType","accountId","symbol","severity","title"]',
                    updated_at TEXT NOT NULL
                )
            """)
            self.ensure_columns(
                connection,
                "notification_rules",
                {
                    "similarity_enabled": "INTEGER NOT NULL DEFAULT 1",
                    "similarity_window_minutes": "INTEGER NOT NULL DEFAULT 120",
                    "similarity_penalty": "INTEGER NOT NULL DEFAULT -20",
                    "similarity_bypass_score_delta": "INTEGER NOT NULL DEFAULT 20",
                    "similarity_fields_json": "TEXT NOT NULL DEFAULT '[\"messageType\",\"accountId\",\"symbol\",\"severity\",\"title\"]'",
                },
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

    def ensure_columns(self, connection, table: str, columns: Dict[str, str]) -> None:
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(" + table + ")").fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                try:
                    connection.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + definition)
                except sqlite3.OperationalError as error:
                    if "duplicate column name" not in str(error).lower():
                        raise


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
        return NotificationTemplate(
            message_type=row["message_type"],
            template=row["template"],
            description=row["description"],
            enabled=bool(row["enabled"]),
            updated_at=row["updated_at"],
        )

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
    def __init__(self, path: Optional[Path] = None):
        super().__init__(path)
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
                        similarity_fields_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        json_dumps(rule.similarity_fields),
                        stamp,
                    ),
                )

    def row_to_rule(self, row) -> NotificationRuleConfig:
        try:
            conditions = json.loads(row["conditions_json"] or "[]")
        except json.JSONDecodeError:
            conditions = []
        try:
            similarity_fields = json.loads(row["similarity_fields_json"] or "[]")
        except json.JSONDecodeError:
            similarity_fields = []
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
            "similarityFields": similarity_fields if isinstance(similarity_fields, list) else [],
            "updatedAt": row["updated_at"],
        })

    def list(self) -> List[NotificationRuleConfig]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT message_type, enabled, threshold, base_score, low_score_action, conditions_json,
                    similarity_enabled, similarity_window_minutes, similarity_penalty, similarity_bypass_score_delta,
                    similarity_fields_json, updated_at
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
                    similarity_fields_json, updated_at
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
                    similarity_fields_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    similarity_fields_json = excluded.similarity_fields_json,
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
                    json_dumps(normalized.similarity_fields),
                    normalized.updated_at,
                ),
            )
        return self.get(key)

    def reset(self, message_type: str) -> NotificationRuleConfig:
        return self.upsert(default_notification_rule(str(message_type or "notification").strip() or "notification"))

    def evaluate_job(self, job: NotificationJob):
        rule = self.get(job.message_type)
        decision = evaluate_notification_rule(job, rule)
        recent_count, previous_score = self.similar_history(job, rule, decision.fingerprint)
        return apply_similarity_rule(decision, rule, recent_count, previous_score)

    def similar_history(self, job: NotificationJob, rule: NotificationRuleConfig, fingerprint: str):
        if not rule.similarity_enabled or not int(rule.similarity_window_minutes or 0) or not fingerprint:
            return 0, 0
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=int(rule.similarity_window_minutes or 0))
        cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM notification_jobs
                WHERE message_type = ? AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (job.message_type, cutoff_text),
            ).fetchall()
        count = 0
        previous_score = 0
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
            previous_score = max(previous_score, int(previous_context.get("honeyScore") or 0))
        return count, previous_score


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


class SQLiteSymbolUniverseStore(OperationalConnection):
    def upsert_many(self, symbols: Iterable[ListedSymbol]) -> int:
        items = [item for item in symbols if item.symbol and item.market]
        if not items:
            return 0
        stamp = utc_now()
        with self.connect() as connection:
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

    def mark_source(self, market: str, source: str, source_url: str, status: str, count: int = 0, error: str = "") -> None:
        stamp = symbol_utc_now_iso()
        success_at = stamp if status == "ok" else ""
        with self.connect() as connection:
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

    def upsert_snapshot_state(self, account_id: str, state: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.connect() as connection:
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
                    stamp,
                ),
            )

    def save_snapshot(self, snapshot: AccountSnapshot) -> None:
        state = snapshot.to_monitor_state()
        self.previous[snapshot.account_id] = state
        self.upsert_snapshot_state(snapshot.account_id, state)

    def mark_sent(self, events: Iterable[AlertEvent]) -> None:
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        keys = []
        for event in events:
            keys.extend([event.key, event.cadence_key()])
            self.sent[event.key] = stamp
            self.sent[event.cadence_key()] = stamp
        with self.connect() as connection:
            for key in keys:
                connection.execute(
                    "INSERT OR REPLACE INTO monitor_sent (key, sent_at) VALUES (?, ?)",
                    (key, stamp),
                )

    def write(self) -> None:
        pass


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
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO domain_events (
                    event_id, name, aggregate_id, occurred_at, correlation_id, payload_json, event_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.get("event_id") or event.get("eventId") or ""),
                    str(event.get("name") or ""),
                    str(event.get("aggregate_id") or event.get("aggregateId") or ""),
                    str(event.get("occurred_at") or event.get("occurredAt") or ""),
                    str(event.get("correlation_id") or event.get("correlationId") or ""),
                    json_dumps(event.get("payload") or {}),
                    json_dumps(event),
                ),
            )

    def handle(self, event: DomainEvent) -> None:
        self.insert_event_dict(event.to_dict())

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

    def enqueue(self, job: ModelReviewJob) -> bool:
        with self.connect() as connection:
            existing = connection.execute("SELECT job_id FROM model_review_jobs WHERE job_id = ?", (job.job_id,)).fetchone()
            if existing:
                return False
            self.upsert_job_with_connection(connection, job)
        return True

    def enqueue_from_event(self, event: DomainEvent) -> int:
        if event.name != MONITORING_ALERTS_DETECTED:
            return 0
        count = 0
        for item in event.payload.get("events") or []:
            if not isinstance(item, dict) or item.get("rule") != "monitorDecisionChange":
                continue
            if self.enqueue(ModelReviewJob.create(item)):
                count += 1
        return count

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

    def enqueue(self, job: NotificationJob) -> bool:
        if not job.text.strip():
            return False
        with self.connect() as connection:
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

        decision = SQLiteNotificationRuleStore(self.path).evaluate_job(job)
        context = dict(job.context or {})
        context.update(decision.to_context())
        job.context = context
        if not decision.should_send:
            job.status = "suppressed"
            job.updated_at = utc_now()
            job.last_error = "꿀점수 " + str(decision.score) + "점이 기준 " + str(decision.threshold) + "점보다 낮아 발송하지 않았습니다."
            with self.connect() as connection:
                self.upsert_job_with_connection(connection, job)
            return False

        with self.connect() as connection:
            self.upsert_job_with_connection(connection, job)
        return True

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

    def summary(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM notification_jobs GROUP BY status").fetchall()
        return {row["status"]: int(row["count"]) for row in rows}
