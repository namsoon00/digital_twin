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


DEFAULT_SYMBOL_UNIVERSE_LIMIT = 40


class MySQLSymbolUniverseStore(MySQLOperationalConnection):
    def upsert_many_with_connection(self, connection, symbols: Iterable[ListedSymbol], stamp: str = "") -> int:
        items = [item for item in symbols if item.symbol and item.market]
        if not items:
            return 0
        stamp = stamp or utc_now()
        for item in items:
            existing = connection.execute(
                "SELECT first_seen_at FROM symbol_universe WHERE market = %s AND symbol = %s",
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name),
                    exchange = VALUES(exchange),
                    currency = VALUES(currency),
                    sector = VALUES(sector),
                    asset_type = VALUES(asset_type),
                    source = VALUES(source),
                    source_url = VALUES(source_url),
                    active = VALUES(active),
                    fetched_at = VALUES(fetched_at),
                    last_seen_at = VALUES(last_seen_at),
                    payload_json = VALUES(payload_json),
                    updated_at = VALUES(updated_at)
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
        with self.transaction() as connection:
            return self.upsert_many_with_connection(connection, symbols)

    def row_to_symbol(self, row) -> ListedSymbol:
        payload = _json_loads(row["payload_json"], {})
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
            clauses.append("market = %s")
            params.append(market_value)
        if query_value:
            clauses.append("(symbol LIKE %s OR name LIKE %s)")
            like = "%" + query_value.upper() + "%"
            params.extend([like, "%" + query_value + "%"])
        return query_value, clauses, params

    def search(self, query: str = "", market: str = "", limit: int = DEFAULT_SYMBOL_UNIVERSE_LIMIT, offset: int = 0) -> List[ListedSymbol]:
        query_value, clauses, params = self.symbol_search_clauses(query, market)
        limit_value = max(1, min(500, int(limit or DEFAULT_SYMBOL_UNIVERSE_LIMIT)))
        offset_value = max(0, int(offset or 0))
        exact_symbol = normalize_symbol(query_value)
        sql = """
            SELECT * FROM symbol_universe
            WHERE """ + " AND ".join(clauses) + """
            ORDER BY
                CASE WHEN %s != '' AND symbol = %s THEN 0 WHEN %s != '' AND symbol LIKE %s THEN 1 ELSE 2 END,
                CASE market WHEN 'KOSPI' THEN 1 WHEN 'KOSDAQ' THEN 2 WHEN 'NASDAQ' THEN 3 ELSE 9 END,
                symbol
            LIMIT %s OFFSET %s
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
                    "SELECT * FROM symbol_universe WHERE market = %s AND symbol = %s",
                    (clean_market, clean_symbol),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM symbol_universe
                    WHERE symbol = %s
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
            "SELECT last_success_at FROM symbol_universe_sources WHERE market = %s",
            (normalize_market(market),),
        ).fetchone()
        last_success_at = success_at or (existing["last_success_at"] if existing else "")
        connection.execute(
            """
            INSERT INTO symbol_universe_sources (
                market, source, source_url, status, record_count, last_attempt_at,
                last_success_at, last_error, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                source = VALUES(source),
                source_url = VALUES(source_url),
                status = VALUES(status),
                record_count = VALUES(record_count),
                last_attempt_at = VALUES(last_attempt_at),
                last_success_at = VALUES(last_success_at),
                last_error = VALUES(last_error),
                updated_at = VALUES(updated_at)
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
        with self.transaction() as connection:
            self.mark_source_with_connection(connection, market, source, source_url, status, count, error)

    def refresh_market(self, market: str, source: str, source_url: str, symbols: Iterable[ListedSymbol]) -> int:
        stamp = symbol_utc_now_iso()
        with self.transaction() as connection:
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
