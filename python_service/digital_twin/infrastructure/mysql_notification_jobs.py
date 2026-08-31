from datetime import datetime, timedelta, timezone
import hashlib
from threading import Lock
from typing import Dict, Iterable, List, Optional, Tuple
import uuid

from ..application.notification.admission import NotificationAdmissionPolicy
from ..domain.investment_analysis import investment_decision_key
from ..domain.message_types import (
    HOLDING_TIMING,
    INVESTMENT_CALENDAR_REMINDER,
    INVESTMENT_INSIGHT,
    MODEL_BUY,
    MODEL_SELL,
    NEWS_DIGEST,
    OPERATOR_REASONING_REPORT,
    WATCHLIST_BUY_CANDIDATE,
    WATCHLIST_ONTOLOGY_SIGNAL,
)
from ..domain.notification_rules import (
    DEFAULT_NOTIFICATION_RULES,
    NotificationRuleConfig,
    attach_previous_profit_loss_context,
    default_notification_rule,
    notification_fingerprint,
    ontology_relation_delivery_metadata,
    notification_subject_group_key,
    notification_state_group_key,
)
from ..domain.notifications import NotificationJob
from ..domain.notification.lifecycle import NotificationLifecycleEvent
from ..domain.ontology_relation_delivery import suppressed_relation_context_is_comparable
from ..domain.sent_article_filter import (
    article_event_family_keys,
    article_filter_context_summary,
    collect_article_identity_keys_from_context,
    filter_sent_articles_from_context,
    news_story_changes_decision,
    news_story_is_decision_driver,
    news_story_impact_from_context,
    suppressible_identity_key,
)
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_retention import sent_article_delivery_ledger_cutoff
from .mysql_operational_helpers import _is_duplicate_key_error, _json_loads
from .operational_common import (
    MAX_NOTIFICATION_DELIVERY_ATTEMPTS,
    NOTIFICATION_HISTORY_LOOKBACK_LIMIT,
    json_dumps,
    notification_history_is_recent_in_flight,
    rule_from_row,
)
from .settings import utc_now


class MySQLNotificationJobStore(MySQLOperationalConnection):
    _article_delivery_ledger_backfill_lock = Lock()
    _article_delivery_ledger_backfill_ready = set()

    def __init__(self, settings: Dict[str, str] = None, admission_policy=None):
        super().__init__(settings)
        self.admission_policy = admission_policy or NotificationAdmissionPolicy()
        skip_rule_seed = str(
            self.runtime_settings.get("_skipNotificationRuleDefaultsSeed") or ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        if not skip_rule_seed:
            from .mysql_operational import MySQLNotificationRuleStore

            MySQLNotificationRuleStore(self.runtime_settings)
        # Read-side stores are constructed by several HTTP projections. Do not
        # scan and rewrite historical article delivery rows on construction;
        # duplicate checks already merge the durable ledger with bounded live
        # history and opportunistically repair it inside their write boundary.

    def sent_article_delivery_ledger_backfill_key(self) -> Tuple[str, str, str, str]:
        return (
            str(self.mysql_config.get("database") or ""),
            str(self.mysql_config.get("unix_socket") or ""),
            str(self.mysql_config.get("host") or ""),
            str(self.mysql_config.get("port") or ""),
        )

    def ensure_sent_article_delivery_ledger_backfill(self) -> int:
        """Seed historical keys once per process without adding hot-path writes."""
        key = self.sent_article_delivery_ledger_backfill_key()
        with self._article_delivery_ledger_backfill_lock:
            if key in self._article_delivery_ledger_backfill_ready:
                return 0
            written = self.backfill_sent_article_delivery_ledger()
            self._article_delivery_ledger_backfill_ready.add(key)
            return written

    def notification_rule_defaults_exist(self) -> bool:
        message_types = list(DEFAULT_NOTIFICATION_RULES.keys())
        if not message_types:
            return True
        placeholders = ",".join(["%s"] * len(message_types))
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM notification_rules WHERE message_type IN (" + placeholders + ")",
                message_types,
            ).fetchone()
        return int(row["count"] if row else 0) >= len(message_types)

    def jobs(self) -> List[NotificationJob]:
        with self.connect() as connection:
            rows = connection.execute("SELECT text, payload_json FROM notification_jobs ORDER BY created_at, job_id").fetchall()
        return [self.job_from_row(row) for row in rows]

    def recent(self, limit: int = 40, message_type: str = "", status: str = "") -> List[NotificationJob]:
        jobs, _ = self.recent_page(limit=limit, message_type=message_type, status=status)
        return jobs

    def recent_for_symbol(
        self,
        symbol: str,
        account_id: str = "",
        limit: int = 100,
    ) -> List[NotificationJob]:
        """Load the bounded notification trace for one indexed instrument."""

        clean_symbol = str(symbol or "").upper().strip()[:64]
        if not clean_symbol:
            return []
        clauses = ["symbol = %s"]
        params: List[object] = [clean_symbol]
        if str(account_id or "").strip():
            clauses.append("account_id = %s")
            params.append(str(account_id).strip()[:191])
        params.append(max(1, min(200, int(limit or 100))))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT text, payload_json FROM notification_jobs WHERE "
                + " AND ".join(clauses)
                + " ORDER BY updated_at DESC, job_id DESC LIMIT %s",
                params,
            ).fetchall()
        return [self.job_from_row(row) for row in rows or []]

    def timeline_for_symbol(
        self,
        symbol: str,
        account_id: str = "",
        limit: int = 100,
    ) -> List[Dict[str, object]]:
        """Return notification timeline markers without hydrating full jobs.

        Current rows use the indexed symbol column. A bounded compatibility
        query covers historical rows whose symbol still exists only in the JSON
        context, avoiding a Python recursive scan of every notification body.
        """

        clean_symbol = str(symbol or "").upper().strip()[:64]
        safe_limit = max(1, min(200, int(limit or 100)))
        if not clean_symbol:
            return []

        def select_rows(connection, clauses, params, row_limit):
            return connection.execute(
                "SELECT job_id, account_id, message_type, source_event_name, symbol, "
                "api_source, data_quality, status, created_at, updated_at, text "
                "FROM notification_jobs WHERE "
                + " AND ".join(clauses)
                + " ORDER BY updated_at DESC, job_id DESC LIMIT %s",
                [*params, row_limit],
            ).fetchall()

        account = str(account_id or "").strip()[:191]
        with self.connect() as connection:
            indexed_clauses = ["symbol = %s"]
            indexed_params: List[object] = [clean_symbol]
            if account:
                indexed_clauses.append("account_id = %s")
                indexed_params.append(account)
            rows = list(select_rows(connection, indexed_clauses, indexed_params, safe_limit) or [])
            remaining = safe_limit - len(rows)
            if remaining > 0:
                legacy_clauses = [
                    "symbol = ''",
                    "UPPER(CASE WHEN JSON_VALID(payload_json) THEN COALESCE("
                    "JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.context.symbol')), "
                    "JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.context.rawSymbol')), ''"
                    ") ELSE '' END) = %s",
                ]
                legacy_params: List[object] = [clean_symbol]
                if account:
                    legacy_clauses.append("account_id = %s")
                    legacy_params.append(account)
                rows.extend(select_rows(connection, legacy_clauses, legacy_params, remaining) or [])

        payloads = [{
            "jobId": str(row.get("job_id") or ""),
            "accountId": str(row.get("account_id") or ""),
            "messageType": str(row.get("message_type") or "notification"),
            "sourceEventName": str(row.get("source_event_name") or ""),
            "symbol": str(row.get("symbol") or clean_symbol).upper(),
            "apiSource": str(row.get("api_source") or "notification_jobs"),
            "dataQuality": str(row.get("data_quality") or "actual"),
            "status": str(row.get("status") or "pending"),
            "createdAt": str(row.get("created_at") or ""),
            "updatedAt": str(row.get("updated_at") or ""),
            "text": str(row.get("text") or ""),
            "context": {"symbol": str(row.get("symbol") or clean_symbol).upper()},
        } for row in rows]
        return sorted(
            payloads,
            key=lambda item: (str(item.get("updatedAt") or item.get("createdAt") or ""), str(item.get("jobId") or "")),
            reverse=True,
        )[:safe_limit]

    def delivered_cadence_timestamps(
        self,
        cadence_keys: Iterable[str],
    ) -> Dict[str, str]:
        """Return cooldown clocks only for investment insights actually delivered."""

        keys = list(dict.fromkeys(
            str(value or "").strip()
            for value in cadence_keys or []
            if str(value or "").strip()
        ))[:100]
        if not keys:
            return {}
        cadence_path = "$.context.ontologyInsight.cadenceKey"
        placeholders = ",".join(["%s"] * len(keys))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT JSON_UNQUOTE(JSON_EXTRACT(payload_json, %s)) AS cadence_key,
                       MAX(updated_at) AS delivered_at
                FROM notification_jobs
                WHERE message_type = %s
                  AND status IN ('done', 'sent')
                  AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, %s)) IN ("""
                + placeholders
                + ") GROUP BY cadence_key",
                [cadence_path, INVESTMENT_INSIGHT, cadence_path, *keys],
            ).fetchall()
        return {
            str(row.get("cadence_key") or ""): str(row.get("delivered_at") or "")
            for row in rows or []
            if str(row.get("cadence_key") or "").strip()
            and str(row.get("delivered_at") or "").strip()
        }

    def active_or_delivered_holding_review_exists(
        self,
        account_id: str,
        symbol: str,
    ) -> bool:
        """Return whether the first TypeDB holding review is already underway.

        Suppressed and failed rows deliberately do not count. They are audit
        evidence, not proof that the user received the holding judgement.
        """

        account = str(account_id or "").strip()[:191]
        instrument = str(symbol or "").upper().strip()[:64]
        if not account or not instrument:
            return False
        role_paths = (
            "$.context.ontologyRelationContext.actionEnvelope.targetRole",
            "$.context.ontologyRelationContext.targetRole",
        )
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 AS found
                FROM notification_jobs
                WHERE account_id = %s
                  AND symbol = %s
                  AND message_type = %s
                  AND status IN ('pending', 'processing', 'awaiting_ai', 'done', 'sent')
                  AND (
                    LOWER(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload_json, %s)), '')) = 'holding'
                    OR LOWER(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload_json, %s)), '')) = 'holding'
                  )
                LIMIT 1
                """,
                (account, instrument, INVESTMENT_INSIGHT, *role_paths),
            ).fetchone()
        return bool(row)

    def recent_page(
        self,
        limit: int = 40,
        offset: int = 0,
        message_type: str = "",
        status: str = "",
        query: str = "",
        scope: str = "all",
    ) -> Tuple[List[NotificationJob], int]:
        clauses, params = self._recent_filters(message_type, status, query, scope)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        page_size = max(1, min(100, int(limit or 40)))
        page_offset = max(0, int(offset or 0))
        with self.connect() as connection:
            total_row = connection.execute(
                "SELECT COUNT(*) AS count FROM notification_jobs" + where,
                params,
            ).fetchone()
            rows = connection.execute(
                "SELECT text, payload_json FROM notification_jobs" + where + " ORDER BY updated_at DESC, job_id DESC LIMIT %s OFFSET %s",
                params + [page_size, page_offset],
            ).fetchall()
        return [self.job_from_row(row) for row in rows], int(total_row["count"] or 0) if total_row else 0

    def jobs_for_decision_episodes(self, episode_ids: Iterable[str], limit: int = 200) -> List[NotificationJob]:
        ids = list(dict.fromkeys(
            str(item or "").strip()
            for item in episode_ids or []
            if str(item or "").strip()
        ))
        if not ids:
            return []
        placeholders = ",".join(["%s"] * len(ids))
        params = ids + [max(1, min(1000, int(limit or 200)))]
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT text, payload_json FROM notification_jobs "
                "WHERE decision_episode_id IN (" + placeholders + ") "
                "ORDER BY updated_at DESC, job_id DESC LIMIT %s",
                params,
            ).fetchall()
        return [self.job_from_row(row) for row in rows or []]

    def job_summaries_for_decision_episodes(
        self,
        episode_ids: Iterable[str],
        limit: int = 200,
    ) -> List[Dict[str, object]]:
        """Return compact delivery linkage after large notification bodies expire."""

        ids = list(dict.fromkeys(
            str(item or "").strip()
            for item in episode_ids or []
            if str(item or "").strip()
        ))
        if not ids:
            return []
        placeholders = ",".join(["%s"] * len(ids))
        params = ids + [max(1, min(1000, int(limit or 200)))]
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT job_id, decision_episode_id, message_type, status, created_at, updated_at "
                "FROM decision_notification_receipts WHERE decision_episode_id IN (" + placeholders + ") "
                "ORDER BY updated_at DESC, job_id DESC LIMIT %s",
                params,
            ).fetchall()
        return [{
            "jobId": str(row.get("job_id") or ""),
            "decisionEpisodeId": str(row.get("decision_episode_id") or ""),
            "messageType": str(row.get("message_type") or ""),
            "status": str(row.get("status") or ""),
            "createdAt": str(row.get("created_at") or ""),
            "updatedAt": str(row.get("updated_at") or ""),
        } for row in rows or []]

    def recent_page_with_summary(
        self,
        limit: int = 40,
        offset: int = 0,
        message_type: str = "",
        status: str = "",
        query: str = "",
        scope: str = "all",
    ) -> Tuple[List[NotificationJob], int, Dict[str, int]]:
        """Read the visible ledger page and global status totals from one connection."""
        clauses, params = self._recent_filters(message_type, status, query, scope)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        page_size = max(1, min(100, int(limit or 40)))
        page_offset = max(0, int(offset or 0))
        with self.connect() as connection:
            total_row = connection.execute(
                "SELECT COUNT(*) AS count FROM notification_jobs" + where,
                params,
            ).fetchone()
            rows = connection.execute(
                "SELECT text, payload_json FROM notification_jobs" + where + " ORDER BY updated_at DESC, job_id DESC LIMIT %s OFFSET %s",
                params + [page_size, page_offset],
            ).fetchall()
            summary_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM notification_jobs" + where + " GROUP BY status",
                params,
            ).fetchall()
        total = int(total_row["count"] or 0) if total_row else 0
        summary = {row["status"]: int(row["count"] or 0) for row in summary_rows}
        return [self.job_from_row(row) for row in rows], total, summary

    def recent_list_page_with_summary(
        self,
        limit: int = 40,
        offset: int = 0,
        message_type: str = "",
        status: str = "",
        query: str = "",
        scope: str = "all",
        recipient_id: str = "",
        inbox: str = "all",
        cursor_updated_at: str = "",
        cursor_job_id: str = "",
    ) -> Tuple[List[NotificationJob], int, Dict[str, int]]:
        """Read a ledger page without deserializing each immutable audit payload.

        Notification payloads intentionally retain the complete graph-backed
        reasoning trace.  A ledger needs only transport metadata and the saved
        message preview, so selecting ``payload_json`` here made opening the
        notification screen proportional to historical graph size.  The full
        payload remains available through :meth:`get` for the detail report.
        """
        clauses, params = self._recent_filters(
            message_type,
            status,
            query,
            scope,
            include_payload_query=False,
        )
        safe_recipient = str(recipient_id or "").strip()[:191]
        inbox_state = str(inbox or "all").strip().lower()
        join = ""
        if safe_recipient:
            join = (
                " LEFT JOIN notification_inbox_receipts AS receipt"
                " ON receipt.job_id = notification_jobs.job_id AND receipt.recipient_id = %s"
            )
            params = [safe_recipient] + params
            if inbox_state == "unread":
                clauses.append("COALESCE(receipt.read_at, '') = ''")
            elif inbox_state == "important":
                clauses.append("COALESCE(receipt.important, 0) = 1")
            elif inbox_state == "action":
                clauses.append("notification_jobs.status IN ('pending', 'awaiting_ai', 'processing', 'failed')")
                clauses.append("COALESCE(receipt.acknowledged_at, '') = ''")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        page_clauses = list(clauses)
        page_params = list(params)
        if str(cursor_updated_at or "").strip() and str(cursor_job_id or "").strip():
            page_clauses.append(
                "(notification_jobs.updated_at < %s OR "
                "(notification_jobs.updated_at = %s AND notification_jobs.job_id < %s))"
            )
            page_params.extend([cursor_updated_at, cursor_updated_at, cursor_job_id])
        page_where = (" WHERE " + " AND ".join(page_clauses)) if page_clauses else ""
        page_size = max(1, min(100, int(limit or 40)))
        page_offset = max(0, int(offset or 0))
        column_names = (
            "job_id", "account_id", "account_label", "message_type", "source_event_id",
            "source_event_name", "symbol", "decision_episode_id", "decision_key", "api_source",
            "data_quality", "is_mock", "status", "attempts", "created_at", "updated_at", "last_error", "text",
        )
        columns = ", ".join("notification_jobs." + name + " AS " + name for name in column_names)
        if safe_recipient:
            columns += (
                ", receipt.read_at AS receipt_read_at"
                ", receipt.acknowledged_at AS receipt_acknowledged_at"
                ", receipt.important AS receipt_important"
                ", receipt.updated_at AS receipt_updated_at"
            )
        with self.connect() as connection:
            total_row = connection.execute(
                "SELECT COUNT(*) AS count FROM notification_jobs" + join + where,
                params,
            ).fetchone()
            rows = connection.execute(
                "SELECT " + columns + " FROM notification_jobs" + join + page_where
                + " ORDER BY notification_jobs.updated_at DESC, notification_jobs.job_id DESC LIMIT %s OFFSET %s",
                page_params + [page_size, 0 if cursor_updated_at else page_offset],
            ).fetchall()
            summary_rows = connection.execute(
                "SELECT notification_jobs.status, COUNT(*) AS count FROM notification_jobs" + join + where
                + " GROUP BY notification_jobs.status",
                params,
            ).fetchall()
        total = int(total_row["count"] or 0) if total_row else 0
        summary = {row["status"]: int(row["count"] or 0) for row in summary_rows}
        return [self.list_job_from_row(row) for row in rows], total, summary

    @staticmethod
    def _recent_filters(
        message_type: str = "",
        status: str = "",
        query: str = "",
        scope: str = "all",
        include_payload_query: bool = True,
    ) -> Tuple[List[str], List[object]]:
        clauses = []
        params = []
        normalized_scope = str(scope or "all").strip().lower()
        investment_types = (
            INVESTMENT_INSIGHT,
            NEWS_DIGEST,
            INVESTMENT_CALENDAR_REMINDER,
            MODEL_BUY,
            MODEL_SELL,
            WATCHLIST_BUY_CANDIDATE,
            WATCHLIST_ONTOLOGY_SIGNAL,
            HOLDING_TIMING,
        )
        if normalized_scope in {"investment", "operations"}:
            operator = "IN" if normalized_scope == "investment" else "NOT IN"
            placeholders = ",".join(["%s"] * len(investment_types))
            clauses.append("message_type " + operator + " (" + placeholders + ")")
            params.extend(investment_types)
        if str(message_type or "").strip():
            clauses.append("message_type = %s")
            params.append(str(message_type or "").strip())
        if str(status or "").strip():
            clauses.append("status = %s")
            params.append(str(status or "").strip())
        needle = str(query or "").strip()
        if needle:
            clauses.append(
                "(text LIKE %s OR source_event_name LIKE %s OR message_type LIKE %s)"
                if not include_payload_query
                else "(text LIKE %s OR payload_json LIKE %s OR message_type LIKE %s)"
            )
            like = "%" + needle[:120] + "%"
            params.extend([like, like, like])
        return clauses, params

    @staticmethod
    def notification_linkage(job: NotificationJob) -> Dict[str, object]:
        context = job.context if isinstance(job.context, dict) else {}
        episode = context.get("investmentDecisionEpisode") if isinstance(context.get("investmentDecisionEpisode"), dict) else {}
        relation = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
        symbol = str(context.get("symbol") or context.get("rawSymbol") or "").strip().upper()[:64]
        decision_episode_id = str(
            context.get("investmentDecisionEpisodeId")
            or context.get("decisionEpisodeId")
            or episode.get("episodeId")
            or relation.get("investmentDecisionEpisodeId")
            or ""
        ).strip()[:191]
        decision_key = str(context.get("decisionKey") or "").strip()[:191]
        if not decision_key and symbol and decision_episode_id:
            decision_key = investment_decision_key(job.account_id or "default", symbol, decision_episode_id)
        data_quality = str(context.get("dataQuality") or relation.get("dataQuality") or "actual").strip()[:32]
        return {
            "symbol": symbol,
            "decisionEpisodeId": decision_episode_id,
            "decisionKey": decision_key,
            "apiSource": str(context.get("apiSource") or context.get("quoteSource") or context.get("sourceApi") or "notification_jobs").strip()[:191],
            "dataQuality": data_quality,
            "isMock": bool(context.get("isMock")) or data_quality.lower() in {"mock", "demo"} or str(context.get("dataMode") or context.get("mode") or "").lower() in {"mock", "demo", "preview"},
        }

    def receipt_states(self, recipient_id: str, job_ids: List[str]) -> Dict[str, Dict[str, object]]:
        recipient = str(recipient_id or "local-owner").strip()[:191] or "local-owner"
        ids = [str(item or "").strip()[:191] for item in job_ids if str(item or "").strip()]
        if not ids:
            return {}
        placeholders = ",".join(["%s"] * len(ids))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT job_id, read_at, acknowledged_at, important, updated_at "
                "FROM notification_inbox_receipts WHERE recipient_id = %s AND job_id IN (" + placeholders + ")",
                [recipient] + ids,
            ).fetchall()
        return {
            str(row.get("job_id") or ""): {
                "readAt": str(row.get("read_at") or ""),
                "acknowledgedAt": str(row.get("acknowledged_at") or ""),
                "important": bool(row.get("important")),
                "receiptUpdatedAt": str(row.get("updated_at") or ""),
            }
            for row in rows
        }

    def inbox_summary(self, recipient_id: str, scope: str = "investment") -> Dict[str, int]:
        recipient = str(recipient_id or "local-owner").strip()[:191] or "local-owner"
        clauses, params = self._recent_filters(scope=scope)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN COALESCE(receipt.read_at, '') = '' THEN 1 ELSE 0 END) AS unread, "
                "SUM(CASE WHEN COALESCE(receipt.important, 0) = 1 THEN 1 ELSE 0 END) AS important, "
                "SUM(CASE WHEN notification_jobs.status IN ('pending', 'awaiting_ai', 'processing', 'failed') "
                "AND COALESCE(receipt.acknowledged_at, '') = '' THEN 1 ELSE 0 END) AS action_required "
                "FROM notification_jobs LEFT JOIN notification_inbox_receipts AS receipt "
                "ON receipt.job_id = notification_jobs.job_id AND receipt.recipient_id = %s" + where,
                [recipient] + params,
            ).fetchone() or {}
        return {
            "total": int(row.get("total") or 0),
            "unread": int(row.get("unread") or 0),
            "important": int(row.get("important") or 0),
            "actionRequired": int(row.get("action_required") or 0),
        }

    def update_receipt(
        self,
        job_id: str,
        recipient_id: str,
        read: Optional[bool] = None,
        acknowledged: Optional[bool] = None,
        important: Optional[bool] = None,
    ) -> Dict[str, object]:
        job_key = str(job_id or "").strip()[:191]
        recipient = str(recipient_id or "local-owner").strip()[:191] or "local-owner"
        if not job_key:
            raise ValueError("Notification job id is required")
        existing = self.receipt_states(recipient, [job_key]).get(job_key, {})
        stamp = utc_now()
        read_at = str(existing.get("readAt") or "")
        acknowledged_at = str(existing.get("acknowledgedAt") or "")
        important_value = bool(existing.get("important"))
        if read is not None:
            read_at = stamp if read else ""
        if acknowledged is not None:
            acknowledged_at = stamp if acknowledged else ""
            if acknowledged:
                read_at = read_at or stamp
        if important is not None:
            important_value = bool(important)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO notification_inbox_receipts "
                "(recipient_id, job_id, read_at, acknowledged_at, important, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE read_at = VALUES(read_at), acknowledged_at = VALUES(acknowledged_at), "
                "important = VALUES(important), updated_at = VALUES(updated_at)",
                (recipient, job_key, read_at, acknowledged_at, int(important_value), stamp),
            )
        return {
            "jobId": job_key,
            "recipientId": recipient,
            "readAt": read_at,
            "acknowledgedAt": acknowledged_at,
            "important": important_value,
            "receiptUpdatedAt": stamp,
        }

    def mark_all_read(self, recipient_id: str, scope: str = "investment") -> int:
        recipient = str(recipient_id or "local-owner").strip()[:191] or "local-owner"
        clauses, params = self._recent_filters(scope=scope)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        stamp = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO notification_inbox_receipts "
                "(recipient_id, job_id, read_at, acknowledged_at, important, updated_at) "
                "SELECT %s, notification_jobs.job_id, %s, '', 0, %s FROM notification_jobs" + where + " "
                "ON DUPLICATE KEY UPDATE read_at = VALUES(read_at), updated_at = VALUES(updated_at)",
                [recipient, stamp, stamp] + params,
            )
        return max(0, int(cursor.rowcount or 0))

    def get(self, job_id: str) -> Optional[NotificationJob]:
        target = str(job_id or "").strip()
        if not target:
            return None
        with self.connect() as connection:
            row = connection.execute(
                "SELECT text, payload_json FROM notification_jobs WHERE job_id = %s",
                (target,),
            ).fetchone()
        return self.job_from_row(row) if row else None

    def record_lifecycle_with_connection(
        self,
        connection,
        job: NotificationJob,
        stage: str,
        outcome: str,
        reason: str = "",
        metadata: Dict[str, object] = None,
    ) -> Dict[str, object]:
        details = {
            "messageType": str(job.message_type or ""),
            "status": str(job.status or ""),
            "attempts": int(job.attempts or 0),
            **dict(metadata or {}),
        }
        event = NotificationLifecycleEvent(
            job_id=str(job.job_id or ""),
            stage=str(stage or ""),
            outcome=str(outcome or ""),
            reason=str(reason or "")[:2000],
            metadata=details,
        )
        payload = event.to_dict()
        connection.execute(
            "INSERT INTO notification_lifecycle_events "
            "(event_id, job_id, stage, outcome, reason, metadata_json, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                payload["eventId"],
                payload["jobId"],
                payload["stage"],
                payload["outcome"],
                payload["reason"],
                json_dumps(payload["metadata"]),
                payload["createdAt"],
            ),
        )
        return payload

    def record_lifecycle(
        self,
        job: NotificationJob,
        stage: str,
        outcome: str,
        reason: str = "",
        metadata: Dict[str, object] = None,
    ) -> Dict[str, object]:
        with self.transaction() as connection:
            return self.record_lifecycle_with_connection(
                connection,
                job,
                stage,
                outcome,
                reason,
                metadata,
            )

    def lifecycle_for_job(self, job_id: str) -> List[Dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT event_id, job_id, stage, outcome, reason, metadata_json, created_at "
                "FROM notification_lifecycle_events WHERE job_id = %s "
                "ORDER BY created_at, event_id",
                (str(job_id or "")[:191],),
            ).fetchall()
        return [
            {
                "eventId": str(row.get("event_id") or ""),
                "jobId": str(row.get("job_id") or ""),
                "stage": str(row.get("stage") or ""),
                "outcome": str(row.get("outcome") or ""),
                "reason": str(row.get("reason") or ""),
                "metadata": _json_loads(row.get("metadata_json"), {}),
                "createdAt": str(row.get("created_at") or ""),
            }
            for row in rows
        ]

    def start_delivery_attempt(
        self,
        job: NotificationJob,
        channel: str,
        audience: str,
        metadata: Dict[str, object] = None,
    ) -> str:
        attempt_id = uuid.uuid4().hex
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO notification_delivery_attempts "
                "(attempt_id, job_id, channel, audience, provider, status, reason, metadata_json, started_at, completed_at) "
                "VALUES (%s, %s, %s, %s, '', 'started', '', %s, %s, '')",
                (
                    attempt_id,
                    str(job.job_id or "")[:191],
                    str(channel or "")[:64],
                    str(audience or "")[:64],
                    json_dumps(dict(metadata or {})),
                    stamp,
                ),
            )
            self.record_lifecycle_with_connection(
                connection,
                job,
                "dispatching",
                "started",
                metadata={"attemptId": attempt_id, "channel": channel, "audience": audience},
            )
        return attempt_id

    def complete_delivery_attempt(
        self,
        job: NotificationJob,
        attempt_id: str,
        delivered: bool,
        provider: str = "",
        reason: str = "",
        metadata: Dict[str, object] = None,
    ) -> None:
        stamp = utc_now()
        status = "delivered" if delivered else "failed"
        with self.transaction() as connection:
            connection.execute(
                "UPDATE notification_delivery_attempts SET provider = %s, status = %s, reason = %s, "
                "metadata_json = %s, completed_at = %s WHERE attempt_id = %s AND job_id = %s",
                (
                    str(provider or "")[:191],
                    status,
                    str(reason or "")[:2000],
                    json_dumps(dict(metadata or {})),
                    stamp,
                    str(attempt_id or "")[:191],
                    str(job.job_id or "")[:191],
                ),
            )

    def delivery_attempts_for_job(self, job_id: str) -> List[Dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT attempt_id, job_id, channel, audience, provider, status, reason, metadata_json, "
                "started_at, completed_at FROM notification_delivery_attempts WHERE job_id = %s "
                "ORDER BY started_at, attempt_id",
                (str(job_id or "")[:191],),
            ).fetchall()
        return [
            {
                "attemptId": str(row.get("attempt_id") or ""),
                "jobId": str(row.get("job_id") or ""),
                "channel": str(row.get("channel") or ""),
                "audience": str(row.get("audience") or ""),
                "provider": str(row.get("provider") or ""),
                "status": str(row.get("status") or ""),
                "reason": str(row.get("reason") or ""),
                "metadata": _json_loads(row.get("metadata_json"), {}),
                "startedAt": str(row.get("started_at") or ""),
                "completedAt": str(row.get("completed_at") or ""),
            }
            for row in rows
        ]

    @staticmethod
    def compact_job_payload(job: NotificationJob) -> Dict[str, object]:
        """Keep the message body in its indexed column only.

        Older rows contain ``text`` in both columns. ``job_from_row`` accepts
        both layouts so rows migrate naturally on their next state update.
        """
        payload = job.to_dict()
        payload.pop("text", None)
        return payload

    @staticmethod
    def job_from_row(row) -> NotificationJob:
        payload = _json_loads(row.get("payload_json"), {})
        if not payload.get("text"):
            payload["text"] = str(row.get("text") or "")
        return NotificationJob.from_dict(payload)

    @staticmethod
    def list_job_from_row(row) -> NotificationJob:
        """Build the small read model used by the outbox list.

        This deliberately does not inspect ``payload_json``.  A missing graph
        context is represented as an empty mapping; the selected job is then
        re-read through ``get`` before its reasoning detail is rendered.
        """
        context = {
            "symbol": str(row.get("symbol") or ""),
            "investmentDecisionEpisodeId": str(row.get("decision_episode_id") or ""),
            "decisionKey": str(row.get("decision_key") or ""),
            "apiSource": str(row.get("api_source") or "notification_jobs"),
            "dataQuality": str(row.get("data_quality") or "actual"),
            "isMock": bool(row.get("is_mock")),
        }
        if any(
            key in row
            for key in (
                "receipt_read_at",
                "receipt_acknowledged_at",
                "receipt_important",
                "receipt_updated_at",
            )
        ):
            context["notificationReceipt"] = {
                "readAt": str(row.get("receipt_read_at") or ""),
                "acknowledgedAt": str(row.get("receipt_acknowledged_at") or ""),
                "important": bool(row.get("receipt_important")),
                "receiptUpdatedAt": str(row.get("receipt_updated_at") or ""),
            }
        return NotificationJob(
            job_id=str(row.get("job_id") or ""),
            account_id=str(row.get("account_id") or ""),
            account_label=str(row.get("account_label") or ""),
            message_type=str(row.get("message_type") or "notification"),
            text=str(row.get("text") or ""),
            context=context,
            status=str(row.get("status") or "pending"),
            attempts=int(row.get("attempts") or 0),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
            last_error=str(row.get("last_error") or ""),
            source_event_id=str(row.get("source_event_id") or ""),
            source_event_name=str(row.get("source_event_name") or ""),
        )

    def upsert_job_with_connection(self, connection, job: NotificationJob) -> None:
        payload = self.compact_job_payload(job)
        dedupe_value = str(job.dedupe_key or "").strip()[:191] or None
        linkage = self.notification_linkage(job)
        cursor = connection.execute(
            """
            UPDATE notification_jobs
            SET account_id = %s,
                account_label = %s,
                message_type = %s,
                source_event_id = %s,
                source_event_name = %s,
                symbol = %s,
                decision_episode_id = %s,
                decision_key = %s,
                api_source = %s,
                data_quality = %s,
                is_mock = %s,
                dedupe_key = %s,
                status = %s,
                attempts = %s,
                created_at = %s,
                updated_at = %s,
                last_error = %s,
                text = %s,
                payload_json = %s
            WHERE job_id = %s
            """,
            (
                job.account_id,
                job.account_label,
                job.message_type,
                job.source_event_id,
                job.source_event_name,
                linkage["symbol"],
                linkage["decisionEpisodeId"],
                linkage["decisionKey"],
                linkage["apiSource"],
                linkage["dataQuality"],
                int(linkage["isMock"]),
                dedupe_value,
                job.status,
                job.attempts,
                job.created_at,
                job.updated_at,
                job.last_error,
                job.text,
                json_dumps(payload),
                job.job_id,
            ),
        )
        if not cursor.rowcount:
            connection.execute(
                """
                INSERT INTO notification_jobs (
                    job_id, account_id, account_label, message_type, source_event_id, source_event_name,
                    symbol, decision_episode_id, decision_key, api_source, data_quality, is_mock, dedupe_key, status, attempts,
                    created_at, updated_at, last_error, text, payload_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    job.job_id,
                    job.account_id,
                    job.account_label,
                    job.message_type,
                    job.source_event_id,
                    job.source_event_name,
                    linkage["symbol"],
                    linkage["decisionEpisodeId"],
                    linkage["decisionKey"],
                    linkage["apiSource"],
                    linkage["dataQuality"],
                    int(linkage["isMock"]),
                    dedupe_value,
                    job.status,
                    job.attempts,
                    job.created_at,
                    job.updated_at,
                    job.last_error,
                    job.text,
                    json_dumps(payload),
                ),
            )
        decision_episode_id = str(linkage.get("decisionEpisodeId") or "").strip()[:191]
        if decision_episode_id:
            connection.execute(
                """
                INSERT INTO decision_notification_receipts (
                    job_id, decision_episode_id, decision_key, account_id, symbol,
                    message_type, status, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    decision_episode_id = VALUES(decision_episode_id),
                    decision_key = VALUES(decision_key),
                    account_id = VALUES(account_id),
                    symbol = VALUES(symbol),
                    message_type = VALUES(message_type),
                    status = VALUES(status),
                    updated_at = VALUES(updated_at)
                """,
                (
                    job.job_id,
                    decision_episode_id,
                    str(linkage.get("decisionKey") or "")[:191],
                    str(job.account_id or "")[:191],
                    str(linkage.get("symbol") or "")[:64],
                    str(job.message_type or "notification")[:191],
                    str(job.status or "pending")[:32],
                    str(job.created_at or ""),
                    str(job.updated_at or job.created_at or ""),
                ),
            )

    def upsert_job(self, job: NotificationJob) -> None:
        with self.transaction() as connection:
            self.upsert_job_with_connection(connection, job)

    def rule_for_connection(self, connection, message_type: str) -> NotificationRuleConfig:
        key = str(message_type or "notification").strip() or "notification"
        row = connection.execute("SELECT * FROM notification_rules WHERE message_type = %s", (key,)).fetchone()
        return rule_from_row(row) if row else default_notification_rule(key)

    def similar_history_for_rule(self, job: NotificationJob, rule: NotificationRuleConfig, fingerprint: str):
        with self.connect() as connection:
            return self.similar_history_with_connection(connection, job, rule, fingerprint)

    def sent_article_filter_enabled(self) -> bool:
        value = self.runtime_settings.get("sentArticleFilterEnabled", self.runtime_settings.get("newsSentArticleFilterEnabled"))
        if value in (None, ""):
            return True
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def sent_article_history_limit(self) -> int:
        try:
            return max(20, min(300, int(self.runtime_settings.get("sentArticleFilterHistoryLimit") or 120)))
        except (TypeError, ValueError):
            return 120

    def sent_article_delivery_ledger_key_limit(self) -> int:
        return max(100, min(1000, self.sent_article_history_limit() * 8))

    def sent_article_delivery_ledger_keys_with_connection(self, connection, account_id: str = "") -> set:
        """Read compact, durable article identities without retaining alert payloads."""
        if not self.sent_article_filter_enabled():
            return set()
        rows = connection.execute(
            """
            SELECT identity_key
            FROM notification_article_delivery_ledger
            WHERE account_id = %s
              AND delivered_at >= CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci
            ORDER BY delivered_at DESC, identity_key DESC
            LIMIT %s
            """,
            (
                str(account_id or "").strip()[:191],
                sent_article_delivery_ledger_cutoff(self.runtime_settings),
                self.sent_article_delivery_ledger_key_limit(),
            ),
        ).fetchall()
        return {
            str(row["identity_key"] or "").strip()
            for row in rows
            if suppressible_identity_key(row["identity_key"])
        }

    def record_article_delivery_context_with_connection(
        self,
        connection,
        account_id: str,
        message_type: str,
        context: Dict[str, object],
        source_job_id: str = "",
        delivered_at: str = "",
    ) -> int:
        """Persist only article identity keys once delivery succeeds.

        Full notification payloads are intentionally retained for a very short
        operator window. This compact ledger is the independent duplicate
        memory needed after those payloads are removed for disk control.
        """
        probe = NotificationJob(
            job_id=str(source_job_id or ""),
            account_id=str(account_id or ""),
            account_label="",
            message_type=str(message_type or ""),
            text="",
            context=dict(context or {}),
        )
        if not self.sent_article_filter_enabled() or not self.article_driven_job(probe):
            return 0
        keys = sorted({
            str(key or "").strip()[:191]
            for key in collect_article_identity_keys_from_context(probe.context, max_depth=7, max_nodes=600, max_keys=800)
            if str(key or "").strip()
        })[: self.sent_article_delivery_ledger_key_limit()]
        if not keys:
            return 0
        stamp = str(delivered_at or utc_now()).strip() or utc_now()
        safe_account_id = str(account_id or "").strip()[:191]
        safe_job_id = str(source_job_id or "").strip()[:191]
        safe_message_type = str(message_type or "").strip()[:191]
        for identity_key in keys:
            connection.execute(
                """
                INSERT INTO notification_article_delivery_ledger (
                    account_id, identity_key, delivered_at, updated_at, source_job_id, message_type
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    delivered_at = VALUES(delivered_at),
                    updated_at = VALUES(updated_at),
                    source_job_id = VALUES(source_job_id),
                    message_type = VALUES(message_type)
                """,
                (safe_account_id, identity_key, stamp, stamp, safe_job_id, safe_message_type),
            )
        return len(keys)

    def record_article_delivery_with_connection(self, connection, job: NotificationJob) -> int:
        return self.record_article_delivery_context_with_connection(
            connection,
            job.account_id,
            job.message_type,
            job.context or {},
            source_job_id=job.job_id,
            delivered_at=job.updated_at or job.created_at,
        )

    def backfill_sent_article_delivery_ledger(self) -> int:
        """Seed the durable ledger from the bounded live delivery history."""
        if not self.sent_article_filter_enabled():
            return 0
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT job_id, account_id, message_type, updated_at, created_at, payload_json
                FROM notification_jobs
                WHERE status IN ('done', 'sent')
                  AND message_type IN (%s, %s)
                ORDER BY updated_at DESC, job_id DESC
                LIMIT %s
                """,
                (NEWS_DIGEST, INVESTMENT_INSIGHT, self.sent_article_delivery_ledger_key_limit()),
            ).fetchall()
            written = 0
            for row in rows:
                payload = _json_loads(row["payload_json"], {})
                context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
                written += self.record_article_delivery_context_with_connection(
                    connection,
                    row["account_id"],
                    row["message_type"],
                    context,
                    source_job_id=row["job_id"],
                    delivered_at=row["updated_at"] or row["created_at"],
                )
            return written

    def sent_article_identity_keys(self, account_id: str = "", limit: int = 0) -> set:
        """Expose the durable duplicate-filter surface to news ingestion."""
        if not self.sent_article_filter_enabled():
            return set()
        probe = NotificationJob.create(
            "",
            account_id=str(account_id or ""),
            message_type=NEWS_DIGEST,
        )
        with self.transaction() as connection:
            return self.sent_article_history_keys_with_connection(connection, probe)

    def remove_weak_article_delivery_identities(self, dry_run: bool = True) -> Dict[str, object]:
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM notification_article_delivery_ledger
                WHERE identity_key LIKE 'takeaway:%%' OR identity_key LIKE 'title:%%'
                """
            ).fetchone() or {}
            count = int(row.get("count") or 0)
            deleted = 0
            if count and not dry_run:
                cursor = connection.execute(
                    """
                    DELETE FROM notification_article_delivery_ledger
                    WHERE identity_key LIKE 'takeaway:%%' OR identity_key LIKE 'title:%%'
                    """
                )
                deleted = max(0, int(getattr(cursor, "rowcount", 0) or 0))
        return {
            "dryRun": bool(dry_run),
            "weakIdentityCount": count,
            "deletedCount": deleted,
        }

    def backfill_news_event_family_delivery_identities(
        self,
        evidence_store,
        limit: int = 300,
        dry_run: bool = True,
    ) -> Dict[str, object]:
        jobs = self.recent(
            limit=max(1, min(500, int(limit or 300))),
            message_type=NEWS_DIGEST,
            status="done",
        )
        candidates = []
        for job in jobs or []:
            context = job.context if isinstance(job.context, dict) else {}
            digest = context.get("newsDigest") if isinstance(context.get("newsDigest"), dict) else {}
            evidence_id = str(digest.get("primaryEvidenceId") or "").strip()
            item = evidence_store.get(evidence_id) if evidence_id else None
            if not item or not callable(getattr(item, "to_dict", None)):
                continue
            for identity_key in sorted(article_event_family_keys(item.to_dict())):
                candidates.append((job, identity_key))

        inserted = 0
        if candidates and not dry_run:
            with self.transaction() as connection:
                for job, identity_key in candidates:
                    stamp = str(job.updated_at or job.created_at or utc_now()).strip() or utc_now()
                    cursor = connection.execute(
                        """
                        INSERT IGNORE INTO notification_article_delivery_ledger (
                            account_id, identity_key, delivered_at, updated_at, source_job_id, message_type
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            str(job.account_id or "")[:191],
                            identity_key[:191],
                            stamp,
                            stamp,
                            str(job.job_id or "")[:191],
                            NEWS_DIGEST,
                        ),
                    )
                    inserted += max(0, int(getattr(cursor, "rowcount", 0) or 0))
        return {
            "dryRun": bool(dry_run),
            "scannedJobCount": len(jobs or []),
            "candidateKeyCount": len(candidates),
            "insertedKeyCount": inserted,
        }

    def record_news_notification_admission(self, payload: Dict[str, object]) -> str:
        values = dict(payload or {})
        identity = "|".join([
            str(values.get("accountId") or ""),
            str(values.get("evidenceId") or ""),
            str(values.get("policyVersion") or ""),
        ])
        head_id = "news-admission-head:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        stamp = utc_now()
        decision = str(values.get("decision") or "suppressed")[:32]
        reason_code = str(values.get("reasonCode") or "")[:96]
        matched_keys_json = json_dumps(sorted({str(key or "") for key in values.get("matchedIdentityKeys") or [] if str(key or "")}))
        source_event_id = str(values.get("sourceEventId") or "")[:191]
        notification_job_id = str(values.get("notificationJobId") or "")[:191]
        with self.transaction() as connection:
            previous = connection.execute(
                "SELECT * FROM news_notification_admission_heads WHERE admission_head_id = %s FOR UPDATE",
                (head_id,),
            ).fetchone()
            previous_decision = str((previous or {}).get("decision") or "")
            previous_reason = str((previous or {}).get("reason_code") or "")
            previous_keys = str((previous or {}).get("matched_identity_keys_json") or "[]")
            previous_job = str((previous or {}).get("notification_job_id") or "")
            effective_job = notification_job_id
            if (
                previous
                and not effective_job
                and previous_decision == decision
                and previous_reason == reason_code
            ):
                effective_job = previous_job
            transitioned = not previous or (
                previous_decision,
                previous_reason,
                previous_keys,
                previous_job,
            ) != (
                decision,
                reason_code,
                matched_keys_json,
                effective_job,
            )
            if not previous:
                connection.execute(
                    """
                    INSERT INTO news_notification_admission_heads (
                        admission_head_id, account_id, evidence_id, symbol,
                        source_revision, enrichment_revision, policy_version,
                        decision, reason_code, matched_identity_keys_json,
                        source_event_id, notification_job_id, observation_count,
                        first_observed_at, last_observed_at, last_transition_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s)
                    """,
                    (
                        head_id,
                        str(values.get("accountId") or "")[:191],
                        str(values.get("evidenceId") or "")[:191],
                        str(values.get("symbol") or "")[:64],
                        str(values.get("sourceRevision") or "")[:191],
                        str(values.get("enrichmentRevision") or "")[:191],
                        str(values.get("policyVersion") or "")[:191],
                        decision,
                        reason_code,
                        matched_keys_json,
                        source_event_id,
                        effective_job,
                        stamp,
                        stamp,
                        stamp,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE news_notification_admission_heads
                    SET source_revision = %s, enrichment_revision = %s,
                        decision = %s, reason_code = %s,
                        matched_identity_keys_json = %s,
                        source_event_id = %s, notification_job_id = %s,
                        observation_count = observation_count + 1,
                        last_observed_at = %s,
                        last_transition_at = CASE WHEN %s THEN %s ELSE last_transition_at END
                    WHERE admission_head_id = %s
                    """,
                    (
                        str(values.get("sourceRevision") or "")[:191],
                        str(values.get("enrichmentRevision") or "")[:191],
                        decision,
                        reason_code,
                        matched_keys_json,
                        source_event_id,
                        effective_job,
                        stamp,
                        int(transitioned),
                        stamp,
                        head_id,
                    ),
                )
            if transitioned:
                admission_id = "news-admission:" + uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO news_notification_admissions (
                        admission_id, account_id, evidence_id, symbol,
                        source_revision, enrichment_revision, policy_version,
                        decision, reason_code, matched_identity_keys_json,
                        source_event_id, notification_job_id, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        admission_id,
                        str(values.get("accountId") or "")[:191],
                        str(values.get("evidenceId") or "")[:191],
                        str(values.get("symbol") or "")[:64],
                        str(values.get("sourceRevision") or "")[:191],
                        str(values.get("enrichmentRevision") or "")[:191],
                        str(values.get("policyVersion") or "")[:191],
                        decision,
                        reason_code,
                        matched_keys_json,
                        source_event_id,
                        effective_job,
                        stamp,
                        stamp,
                    ),
                )
        return head_id

    def news_notification_admission_status(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT decision, reason_code, COUNT(*) AS count, MAX(last_observed_at) AS latest_updated_at
                FROM news_notification_admission_heads
                GROUP BY decision, reason_code
                """
            ).fetchall()
            history = connection.execute(
                "SELECT COUNT(*) AS count, MAX(updated_at) AS latest_updated_at FROM news_notification_admissions"
            ).fetchone() or {}
        return {
            "decisions": [
                {
                    "decision": str(row.get("decision") or ""),
                    "reasonCode": str(row.get("reason_code") or ""),
                    "count": int(row.get("count") or 0),
                    "latestUpdatedAt": str(row.get("latest_updated_at") or ""),
                }
                for row in rows or []
            ],
            "headCount": sum(int(row.get("count") or 0) for row in rows or []),
            "transitionCount": int(history.get("count") or 0),
            "latestTransitionAt": str(history.get("latest_updated_at") or ""),
        }

    def repair_news_notification_admissions(self, dry_run: bool = True) -> Dict[str, object]:
        """Collapse replay observations into one head and meaningful transitions."""

        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM news_notification_admissions ORDER BY created_at, admission_id FOR UPDATE"
            ).fetchall()
            existing_heads = connection.execute(
                "SELECT * FROM news_notification_admission_heads FOR UPDATE"
            ).fetchall()
            grouped: Dict[Tuple[str, ...], List[Dict[str, object]]] = {}
            for raw in rows or []:
                row = dict(raw or {})
                key = (
                    str(row.get("account_id") or ""),
                    str(row.get("evidence_id") or ""),
                    str(row.get("policy_version") or ""),
                )
                grouped.setdefault(key, []).append(row)
            existing_by_key: Dict[Tuple[str, ...], List[Dict[str, object]]] = {}
            for raw in existing_heads or []:
                row = dict(raw or {})
                key = (
                    str(row.get("account_id") or ""),
                    str(row.get("evidence_id") or ""),
                    str(row.get("policy_version") or ""),
                )
                existing_by_key.setdefault(key, []).append(row)
            transition_groups: Dict[Tuple[str, ...], List[Dict[str, object]]] = {}
            for key, group_rows in grouped.items():
                previous_state = None
                for row in group_rows:
                    state = (
                        str(row.get("decision") or ""),
                        str(row.get("reason_code") or ""),
                        str(row.get("matched_identity_keys_json") or "[]"),
                        str(row.get("notification_job_id") or ""),
                    )
                    if state != previous_state:
                        transition_groups.setdefault(key, []).append(row)
                        previous_state = state
            transitions = [
                row
                for key in grouped
                for row in transition_groups.get(key, [])
            ]
            logical_keys = list(dict.fromkeys([*grouped.keys(), *existing_by_key.keys()]))
            result = {
                "dryRun": bool(dry_run),
                "observationCount": len(rows or []),
                "headCount": len(logical_keys),
                "transitionCount": len(transitions),
                "removedReplayObservationCount": max(0, len(rows or []) - len(transitions)),
            }
            if dry_run:
                return result
            connection.execute("DELETE FROM news_notification_admission_heads")
            connection.execute("DELETE FROM news_notification_admissions")
            for key in logical_keys:
                group_rows = grouped.get(key, [])
                head_rows = existing_by_key.get(key, [])
                latest_history = group_rows[-1] if group_rows else {}
                latest_head = max(
                    head_rows,
                    default={},
                    key=lambda row: str(row.get("last_observed_at") or ""),
                )
                history_at = str(latest_history.get("updated_at") or latest_history.get("created_at") or "")
                head_at = str(latest_head.get("last_observed_at") or "")
                latest = latest_head if head_at >= history_at else latest_history
                first_candidates = [
                    str(row.get("created_at") or "") for row in group_rows[:1]
                ] + [
                    str(row.get("first_observed_at") or "") for row in head_rows
                ]
                first_at = min((value for value in first_candidates if value), default=utc_now())
                last_at = max(history_at, head_at, first_at)
                transition_rows = transition_groups.get(key, [])
                transition_at = str(
                    (transition_rows[-1] if transition_rows else {}).get("updated_at")
                    or (transition_rows[-1] if transition_rows else {}).get("created_at")
                    or ""
                )
                existing_transition_at = max(
                    (str(row.get("last_transition_at") or "") for row in head_rows),
                    default="",
                )
                last_transition_at = max(transition_at, existing_transition_at, first_at)
                observation_count = max(
                    len(group_rows),
                    sum(int(row.get("observation_count") or 0) for row in head_rows),
                    1,
                )
                head_identity = "|".join(key)
                head_id = "news-admission-head:" + hashlib.sha256(head_identity.encode("utf-8")).hexdigest()[:32]
                connection.execute(
                    """
                    INSERT INTO news_notification_admission_heads (
                        admission_head_id, account_id, evidence_id, symbol,
                        source_revision, enrichment_revision, policy_version,
                        decision, reason_code, matched_identity_keys_json,
                        source_event_id, notification_job_id, observation_count,
                        first_observed_at, last_observed_at, last_transition_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        head_id,
                        key[0],
                        key[1],
                        str(latest.get("symbol") or "")[:64],
                        str(latest.get("source_revision") or "")[:191],
                        str(latest.get("enrichment_revision") or "")[:191],
                        key[2],
                        str(latest.get("decision") or "suppressed")[:32],
                        str(latest.get("reason_code") or "")[:96],
                        str(latest.get("matched_identity_keys_json") or "[]"),
                        str(latest.get("source_event_id") or "")[:191],
                        str(latest.get("notification_job_id") or "")[:191],
                        observation_count,
                        first_at,
                        last_at,
                        last_transition_at,
                    ),
                )
            for row in transitions:
                connection.execute(
                    """
                    INSERT INTO news_notification_admissions (
                        admission_id, account_id, evidence_id, symbol,
                        source_revision, enrichment_revision, policy_version,
                        decision, reason_code, matched_identity_keys_json,
                        source_event_id, notification_job_id, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(row.get("admission_id") or "news-admission:" + uuid.uuid4().hex),
                        str(row.get("account_id") or "")[:191],
                        str(row.get("evidence_id") or "")[:191],
                        str(row.get("symbol") or "")[:64],
                        str(row.get("source_revision") or "")[:191],
                        str(row.get("enrichment_revision") or "")[:191],
                        str(row.get("policy_version") or "")[:191],
                        str(row.get("decision") or "suppressed")[:32],
                        str(row.get("reason_code") or "")[:96],
                        str(row.get("matched_identity_keys_json") or "[]"),
                        str(row.get("source_event_id") or "")[:191],
                        str(row.get("notification_job_id") or "")[:191],
                        str(row.get("created_at") or utc_now()),
                        str(row.get("updated_at") or row.get("created_at") or utc_now()),
                    ),
                )
            return result

    def sent_article_history_keys_with_connection(self, connection, job: NotificationJob):
        if not self.sent_article_filter_enabled():
            return set()
        account_id = str(job.account_id or "").strip()
        keys = self.sent_article_delivery_ledger_keys_with_connection(connection, account_id)
        clauses = ["status IN ('done', 'pending', 'processing', 'awaiting_ai')", "message_type IN (%s, %s)"]
        params: List[object] = [NEWS_DIGEST, INVESTMENT_INSIGHT]
        if account_id:
            clauses.append("account_id = %s")
            params.append(account_id)
        params.append(self.sent_article_history_limit())
        rows = connection.execute(
            """
            SELECT job_id, status, account_id, message_type, updated_at, created_at, payload_json
            FROM notification_jobs
            WHERE """ + " AND ".join(clauses) + """
            ORDER BY created_at DESC, job_id DESC
            LIMIT %s
            """,
            params,
        ).fetchall()
        for row in rows:
            previous_payload = _json_loads(row["payload_json"], {})
            if str(row["job_id"] or "") == job.job_id:
                continue
            context = previous_payload.get("context") if isinstance(previous_payload.get("context"), dict) else {}
            previous_keys = collect_article_identity_keys_from_context(context, max_depth=7, max_nodes=600, max_keys=800)
            keys.update(previous_keys)
            if str(row["status"] or "") in {"done", "sent"} and previous_keys:
                self.record_article_delivery_context_with_connection(
                    connection,
                    row["account_id"],
                    row["message_type"],
                    context,
                    source_job_id=row["job_id"],
                    delivered_at=row["updated_at"] or row["created_at"],
                )
            if len(keys) >= 800:
                break
        return keys

    def article_signal_tokens(self, value) -> List[str]:
        if isinstance(value, dict):
            tokens: List[str] = []
            for key in ("type", "kind", "name", "messageType", "signalType", "sourceSignalType"):
                if key in value:
                    tokens.extend(self.article_signal_tokens(value.get(key)))
            return tokens
        if isinstance(value, (list, tuple, set)):
            tokens = []
            for item in value:
                tokens.extend(self.article_signal_tokens(item))
            return tokens
        text = str(value or "").strip().casefold()
        return [text] if text else []

    def article_driven_job(self, job: NotificationJob) -> bool:
        if str(job.message_type or "") == NEWS_DIGEST:
            return True
        if str(job.message_type or "") != INVESTMENT_INSIGHT:
            return False
        context = job.context or {}
        insight = context.get("ontologyInsight") if isinstance(context.get("ontologyInsight"), dict) else {}
        values = [
            context.get("dispatchInsightType"),
            context.get("signalType"),
            context.get("sourceSignalType"),
            context.get("sourceSignalTypes"),
            insight.get("dispatchInsightType"),
            insight.get("signalType"),
            insight.get("sourceSignalType"),
            insight.get("sourceSignalTypes"),
        ]
        tokens = []
        for value in values:
            tokens.extend(self.article_signal_tokens(value))
        blob = " ".join(tokens)
        article_markers = [
            "article",
            "dart",
            "disclosure",
            "feed",
            "filing",
            "news",
            "research",
            "rss",
            "sec",
        ]
        return any(marker in blob for marker in article_markers)

    def apply_sent_article_filter_with_connection(self, connection, job: NotificationJob) -> bool:
        if str(job.message_type or "") == OPERATOR_REASONING_REPORT:
            return False
        if not self.sent_article_filter_enabled():
            return False
        current_keys = collect_article_identity_keys_from_context(job.context or {})
        if not current_keys:
            return False
        sent_keys = self.sent_article_history_keys_with_connection(connection, job)
        matched_keys = current_keys.intersection(sent_keys)
        if not matched_keys:
            return False
        result = filter_sent_articles_from_context(job.context or {}, sent_keys)
        context = dict(result.context or {})
        context["sentArticleFilter"] = article_filter_context_summary(result, matched_keys)
        job.context = context
        if self.article_driven_job(job) and result.after_count <= 0:
            job.status = "suppressed"
            job.updated_at = utc_now()
            job.last_error = "이미 발송한 기사 또는 같은 제목의 기사만 남아 다시 판단하지 않았습니다."
            job.context["deliverySuppressionReason"] = "sent_article_repeat"
            self.upsert_job_with_connection(connection, job)
            return True
        return False

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
            return 0, {}, ""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=history_minutes)
        cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
        rows = connection.execute(
            """
            SELECT text, payload_json, created_at, status FROM notification_jobs
            WHERE message_type = %s AND created_at >= %s AND status IN ('pending', 'processing', 'awaiting_ai', 'done')
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (job.message_type, cutoff_text, NOTIFICATION_HISTORY_LOOKBACK_LIMIT),
        ).fetchall()
        count = 0
        most_recent_context: Dict[str, object] = {}
        most_recent_at = ""
        state_group_key = notification_state_group_key(job)
        for row in rows:
            previous = self.job_from_row(row)
            if previous.job_id == job.job_id:
                continue
            previous_context = previous.context or {}
            previous_fingerprint = str(
                previous_context.get("deliveryFingerprint")
                or previous_context.get("honeyFingerprint")
                or notification_fingerprint(previous, rule)
            )
            previous_state_group_key = str(
                previous_context.get("deliveryStateGroupKey")
                or previous_context.get("honeyStateGroupKey")
                or notification_state_group_key(previous)
            )
            if previous_fingerprint != fingerprint and (not state_group_key or previous_state_group_key != state_group_key):
                continue
            status = str(row["status"] or "").strip()
            if status != "done" and not notification_history_is_recent_in_flight(row):
                continue
            count += 1
            if not most_recent_context:
                most_recent_context = dict(previous_context)
            if status == "done" and not most_recent_at:
                most_recent_at = row["created_at"] or previous.created_at
        return count, most_recent_context, most_recent_at

    def relation_predecessor_with_connection(
        self,
        connection,
        job: NotificationJob,
        rule: NotificationRuleConfig,
    ) -> Dict[str, object]:
        """Find the prior same-subject graph context for an explainable diff.

        The normal cooldown lookup intentionally treats a changed semantic
        graph fingerprint as a new state. This companion read keeps the most
        recent comparable context solely to explain *why* that new state is
        allowed through; it never changes TypeDB judgement or delivery gates.
        """

        metadata = ontology_relation_delivery_metadata(job.context or {})
        if not metadata.get("fingerprint"):
            return {}
        history_minutes = max(
            60,
            int(rule.similarity_window_minutes or 0),
            int(rule.state_cooldown_minutes or 0) + 60 if rule.state_cooldown_enabled else 0,
        )
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=history_minutes)
        cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
        current_group = notification_subject_group_key(job)
        if not current_group:
            return {}
        rows = connection.execute(
            """
            SELECT text, payload_json, created_at, status FROM notification_jobs
            WHERE message_type = %s AND created_at >= %s AND status IN ('pending', 'processing', 'awaiting_ai', 'done', 'suppressed')
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (job.message_type, cutoff_text, NOTIFICATION_HISTORY_LOOKBACK_LIMIT),
        ).fetchall()
        selected_context: Dict[str, object] = {}
        selected_at = ""
        selected_status = ""
        selected_fingerprint = ""
        cooldown_boundary_at = ""
        cooldown_boundary_status = ""
        baseline_observed_at = ""
        current_fingerprint = str(metadata.get("fingerprint") or "").strip()
        for row in rows:
            previous = self.job_from_row(row)
            if previous.job_id == job.job_id:
                continue
            if notification_subject_group_key(previous) != current_group:
                continue
            status = str(row["status"] or "").strip()
            context = dict(previous.context or {})
            if status == "suppressed":
                if not suppressed_relation_context_is_comparable(context):
                    continue
            elif status != "done" and not notification_history_is_recent_in_flight(row):
                continue
            created_at = str(row["created_at"] or previous.created_at or "")
            previous_fingerprint = str(
                context.get("ontologyRelationFingerprint")
                or (context.get("ontologyRelationDelivery") or {}).get("fingerprint")
                or ontology_relation_delivery_metadata(context).get("fingerprint")
                or ""
            ).strip()
            carried_baseline_fingerprint = str(context.get("_relationBaselineFingerprint") or "").strip()
            carried_baseline_at = str(context.get("_relationBaselineObservedAt") or "").strip()
            if (
                carried_baseline_at
                and carried_baseline_fingerprint == current_fingerprint
                and not baseline_observed_at
            ):
                baseline_observed_at = carried_baseline_at
            if (
                str(context.get("deliverySuppressionReason") or "") == "initial_graph_baseline"
                and previous_fingerprint == current_fingerprint
            ):
                baseline_observed_at = created_at
            if status == "done" and not cooldown_boundary_at:
                # Suppressed candidates remain useful for semantic diffs, but
                # only a successfully delivered job may start cooldown.
                cooldown_boundary_at = created_at
                cooldown_boundary_status = status
            if not selected_context:
                selected_context = context
                selected_at = created_at
                selected_status = status
                selected_fingerprint = previous_fingerprint
        if not selected_context:
            return {}
        selected_context["_relationPredecessorObservedAt"] = selected_at
        selected_context["_relationPredecessorStatus"] = selected_status
        if cooldown_boundary_at:
            selected_context["_relationPredecessorSentAt"] = cooldown_boundary_at
            selected_context["_relationPredecessorSentStatus"] = cooldown_boundary_status
        if baseline_observed_at and selected_fingerprint == current_fingerprint:
            selected_context["_relationBaselineObservedAt"] = baseline_observed_at
            selected_context["_relationBaselineFingerprint"] = current_fingerprint
        return selected_context

    def evaluate_job_with_connection(self, connection, job: NotificationJob):
        policy = getattr(self, "admission_policy", None) or NotificationAdmissionPolicy()
        rule = self.rule_for_connection(connection, job.message_type)
        decision = policy.prepare(job, rule)
        recent_count, previous_context, last_sent_at = self.similar_history_with_connection(
            connection,
            job,
            rule,
            decision.fingerprint,
        )
        relation_previous_context = self.relation_predecessor_with_connection(connection, job, rule)
        return policy.evaluate(
            job,
            rule,
            decision,
            recent_count=recent_count,
            previous_context=previous_context,
            last_sent_at=last_sent_at,
            relation_previous_context=relation_previous_context,
        )

    def enqueue_with_connection(self, connection, job: NotificationJob) -> bool:
        if not job.text.strip():
            return False
        existing = connection.execute("SELECT job_id FROM notification_jobs WHERE job_id = %s", (job.job_id,)).fetchone()
        if existing:
            return False
        dedupe_value = str(job.dedupe_key or "").strip()[:191]
        if dedupe_value:
            existing = connection.execute(
                "SELECT job_id FROM notification_jobs WHERE dedupe_key = %s",
                (dedupe_value,),
            ).fetchone()
            if existing:
                return False

        if self.apply_sent_article_filter_with_connection(connection, job):
            self.record_lifecycle_with_connection(connection, job, "received", "accepted")
            self.record_lifecycle_with_connection(
                connection,
                job,
                "eligibility_checked",
                "suppressed",
                job.last_error,
                {"suppressionReason": str((job.context or {}).get("deliverySuppressionReason") or "")},
            )
            return False

        decision = self.evaluate_job_with_connection(connection, job)
        policy = getattr(self, "admission_policy", None) or NotificationAdmissionPolicy()
        outcome = policy.apply_result(job, decision, self.runtime_settings)
        if job.status == "suppressed":
            job.updated_at = utc_now()
        try:
            self.upsert_job_with_connection(connection, job)
        except Exception as error:
            if _is_duplicate_key_error(error):
                return False
            raise
        self.record_lifecycle_with_connection(connection, job, "received", "accepted")
        self.record_lifecycle_with_connection(
            connection,
            job,
            "eligibility_checked",
            "accepted" if outcome.accepted else "suppressed",
            outcome.reason,
            {"suppressionReason": str((job.context or {}).get("deliverySuppressionReason") or "")},
        )
        return bool(outcome.accepted)

    def enqueue(self, job: NotificationJob) -> bool:
        with self.transaction() as connection:
            return self.enqueue_with_connection(connection, job)

    def pending(self, limit: int = 10) -> List[NotificationJob]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT text, payload_json FROM notification_jobs
                WHERE status IN ('pending', 'failed')
                ORDER BY created_at, job_id
                LIMIT %s
                """,
                (int(limit or 10),),
            ).fetchall()
        return [self.job_from_row(row) for row in rows]

    def claim_pending(
        self,
        limit: int = 10,
        stale_after_minutes: int = 2,
        include_message_types: Tuple[str, ...] = (),
        exclude_message_types: Tuple[str, ...] = (),
    ) -> List[NotificationJob]:
        stamp = utc_now()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max(1, int(stale_after_minutes or 2)))).isoformat().replace("+00:00", "Z")
        requested = max(1, int(limit or 10))
        included = tuple(dict.fromkeys(str(item).strip() for item in include_message_types or () if str(item).strip()))
        excluded = tuple(dict.fromkeys(str(item).strip() for item in exclude_message_types or () if str(item).strip()))
        lane_clauses: List[str] = []
        lane_params: List[object] = []
        if included:
            lane_clauses.append("message_type IN (" + ",".join(["%s"] * len(included)) + ")")
            lane_params.extend(included)
        if excluded:
            lane_clauses.append("message_type NOT IN (" + ",".join(["%s"] * len(excluded)) + ")")
            lane_params.extend(excluded)
        lane_sql = (" AND " + " AND ".join(lane_clauses)) if lane_clauses else ""
        claimed: List[NotificationJob] = []
        with self.transaction() as connection:
            query_specs = [
                (
                    """
                    SELECT job_id, text, payload_json FROM notification_jobs
                    WHERE status = 'pending'
                    """ + lane_sql + """
                    ORDER BY created_at, job_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    tuple(lane_params),
                ),
                (
                    """
                    SELECT job_id, text, payload_json FROM notification_jobs
                    WHERE status = 'processing'
                      AND COALESCE(NULLIF(processing_started_at, ''), NULLIF(updated_at, ''), created_at) <= %s
                    """ + lane_sql + """
                    ORDER BY created_at, job_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (cutoff, *lane_params),
                ),
                (
                    """
                    SELECT job_id, text, payload_json FROM notification_jobs
                    WHERE status = 'failed' AND attempts < %s
                    """ + lane_sql + """
                    ORDER BY attempts, created_at, job_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (MAX_NOTIFICATION_DELIVERY_ATTEMPTS, *lane_params),
                ),
            ]
            for sql, params in query_specs:
                remaining = requested - len(claimed)
                if remaining <= 0:
                    break
                rows = connection.execute(sql, tuple(params) + (remaining,)).fetchall()
                for row in rows:
                    job = self.job_from_row(row)
                    if not job.job_id:
                        continue
                    job.status = "processing"
                    job.attempts += 1
                    job.updated_at = stamp
                    job.last_error = ""
                    payload = self.compact_job_payload(job)
                    cursor = connection.execute(
                        """
                        UPDATE notification_jobs
                        SET status = %s, attempts = %s, updated_at = %s, last_error = %s,
                            processing_started_at = %s, payload_json = %s
                        WHERE job_id = %s
                          AND (
                            status = 'pending'
                            OR (status = 'failed' AND attempts < %s)
                            OR (
                              status = 'processing'
                              AND COALESCE(NULLIF(processing_started_at, ''), NULLIF(updated_at, ''), created_at) <= %s
                            )
                          )
                        """,
                        (
                            job.status,
                            job.attempts,
                            job.updated_at,
                            job.last_error,
                            stamp,
                            json_dumps(payload),
                            job.job_id,
                            MAX_NOTIFICATION_DELIVERY_ATTEMPTS,
                            cutoff,
                        ),
                    )
                    if cursor.rowcount:
                        self.record_lifecycle_with_connection(
                            connection,
                            job,
                            "eligibility_checked",
                            "claimed",
                        )
                        claimed.append(job)
        return claimed

    def update(self, updated: NotificationJob) -> None:
        self.upsert_job(updated)

    def mark_processing(self, job: NotificationJob) -> NotificationJob:
        job.status = "processing"
        job.attempts += 1
        job.updated_at = utc_now()
        with self.transaction() as connection:
            self.upsert_job_with_connection(connection, job)
            connection.execute(
                "UPDATE notification_jobs SET processing_started_at = %s WHERE job_id = %s",
                (job.updated_at, job.job_id),
            )
            self.record_lifecycle_with_connection(connection, job, "eligibility_checked", "claimed")
        return job

    def mark_done(self, job: NotificationJob) -> None:
        job.status = "done"
        job.last_error = ""
        job.updated_at = utc_now()
        with self.transaction() as connection:
            self.upsert_job_with_connection(connection, job)
            self.record_article_delivery_with_connection(connection, job)
            self.record_lifecycle_with_connection(connection, job, "delivered", "done")

    def mark_failed(self, job: NotificationJob, error: str) -> None:
        job.status = "failed"
        job.last_error = error
        job.updated_at = utc_now()
        with self.transaction() as connection:
            self.upsert_job_with_connection(connection, job)
            self.record_lifecycle_with_connection(connection, job, "failed", "retryable", error)

    def mark_suppressed(self, job: NotificationJob, reason: str) -> None:
        job.status = "suppressed"
        job.last_error = str(reason or "알림 정책으로 발송하지 않았습니다.")
        job.updated_at = utc_now()
        with self.transaction() as connection:
            self.upsert_job_with_connection(connection, job)
            self.record_lifecycle_with_connection(connection, job, "suppressed", "suppressed", job.last_error)

    def summary(self) -> Dict[str, object]:
        try:
            active_window_minutes = max(5, min(24 * 60, int(float(
                self.runtime_settings.get("operationalActiveFailureWindowMinutes") or 60
            ))))
        except (TypeError, ValueError):
            active_window_minutes = 60
        active_cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=active_window_minutes)
        ).isoformat().replace("+00:00", "Z")
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM notification_jobs GROUP BY status").fetchall()
            active_failure_row = connection.execute(
                "SELECT COUNT(*) AS count, MIN(updated_at) AS oldest_at "
                "FROM notification_jobs WHERE status = 'failed' AND updated_at >= %s",
                (active_cutoff,),
            ).fetchone()
            suppression_rows = connection.execute(
                "SELECT CASE "
                "WHEN last_error LIKE '%%행동 범위가 같아%%' OR last_error LIKE '%%평소 상태가 그대로%%' "
                "OR last_error LIKE '%%새 변화 없이%%' THEN 'unchanged_decision' "
                "WHEN last_error LIKE '%%기준선으로 저장%%' THEN 'baseline' "
                "WHEN last_error LIKE '%%같은 내용이%%' OR last_error LIKE '%%이미 발송한 기사%%' "
                "OR last_error LIKE '%%중복%%' THEN 'duplicate_or_cooldown' "
                "WHEN last_error LIKE '%%자료가 부족%%' OR last_error LIKE '%%근거가 없어%%' "
                "OR last_error LIKE '%%검증이 차단%%' THEN 'data_guard' "
                "ELSE 'other_policy' END AS category, COUNT(*) AS count "
                "FROM notification_jobs WHERE status = 'suppressed' GROUP BY category"
            ).fetchall()
        result = {row["status"]: int(row["count"] or 0) for row in rows}
        suppression_categories = {
            str(row.get("category") or "other_policy"): int(row.get("count") or 0)
            for row in suppression_rows or []
        }
        result.update({
            "actionable_failed": int((active_failure_row or {}).get("count") or 0),
            "historical_failed": int(result.get("failed") or 0),
            "active_failure_window_minutes": active_window_minutes,
            "oldest_actionable_failure_at": str(
                (active_failure_row or {}).get("oldest_at") or ""
            ),
            "intentional_suppressed": int(result.get("suppressed") or 0),
            "suppression_categories": suppression_categories,
        })
        return result
