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


class MySQLResearchEvidenceStore(MySQLOperationalConnection):
    def upsert_many(self, items: Iterable[ResearchEvidence]) -> int:
        stamp = utc_now()
        written = 0
        changed_symbols: List[str] = []
        changed_items: List[ResearchEvidence] = []
        with self.transaction() as connection:
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
                dedupe_key = "|".join([symbol, kind, source, title, str(item.url or "").strip()])[:191]
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
                    WHERE evidence_id = %s
                    """,
                    (evidence_id,),
                ).fetchone()
                previous_signature = ""
                if previous_row:
                    previous_payload = _json_loads(previous_row["payload_json"], {})
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
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        symbol = VALUES(symbol),
                        kind = VALUES(kind),
                        source = VALUES(source),
                        title = VALUES(title),
                        summary = VALUES(summary),
                        url = VALUES(url),
                        published_at = VALUES(published_at),
                        observed_at = VALUES(observed_at),
                        last_seen_at = VALUES(last_seen_at),
                        polarity = VALUES(polarity),
                        impact_score = VALUES(impact_score),
                        confidence = VALUES(confidence),
                        dedupe_key = VALUES(dedupe_key),
                        payload_json = VALUES(payload_json)
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
                    changed_items.append(item)
        self.last_changed_symbols = changed_symbols
        self.last_changed_items = changed_items
        return written

    def latest(self, symbol: str = "", kind: str = "", limit: int = 50) -> List[ResearchEvidence]:
        conditions = []
        params: List[object] = []
        normalized_symbol = str(symbol or "").upper().strip()
        normalized_kind = str(kind or "").strip()
        if normalized_symbol:
            conditions.append("symbol = %s")
            params.append(normalized_symbol)
        if normalized_kind:
            conditions.append("kind = %s")
            params.append(normalized_kind)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(max(1, min(500, int(limit or 50))))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_evidence" + where + " ORDER BY last_seen_at DESC, published_at DESC, evidence_id DESC LIMIT %s",
                params,
            ).fetchall()
        return [research_evidence_from_row(row) for row in rows]

    def delete(self, evidence_id: str) -> bool:
        normalized_id = str(evidence_id or "").strip()
        if not normalized_id:
            return False
        with self.transaction() as connection:
            cursor = connection.execute("DELETE FROM research_evidence WHERE evidence_id = %s", (normalized_id,))
        return int(cursor.rowcount or 0) > 0

    def delete_stale_news(self, cutoff_iso: str, limit: int = 500) -> int:
        cutoff = str(cutoff_iso or "").strip()
        if not cutoff:
            return 0
        row_limit = max(1, min(5000, int(limit or 500)))
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM research_evidence
                WHERE kind = 'news'
                  AND COALESCE(NULLIF(published_at, ''), NULLIF(observed_at, ''), NULLIF(last_seen_at, '')) < %s
                ORDER BY last_seen_at ASC, evidence_id ASC
                LIMIT %s
                """,
                (cutoff, row_limit),
            )
        return int(cursor.rowcount or 0)

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
                LIMIT %s
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
