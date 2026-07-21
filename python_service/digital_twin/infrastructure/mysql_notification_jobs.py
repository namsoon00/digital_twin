from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from ..domain.data_freshness import evaluate_notification_data_freshness, sanitize_notification_context_for_freshness
from ..domain.message_types import INVESTMENT_INSIGHT, NEWS_DIGEST, OPERATOR_REASONING_REPORT
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
    notification_state_group_key,
)
from ..domain.notifications import NotificationJob
from ..domain.sent_article_filter import (
    article_filter_context_summary,
    collect_article_identity_keys_from_context,
    filter_sent_articles_from_context,
)
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _is_duplicate_key_error, _json_loads
from .operational_common import (
    MAX_NOTIFICATION_DELIVERY_ATTEMPTS,
    NOTIFICATION_HISTORY_LOOKBACK_LIMIT,
    age_minutes_since,
    json_dumps,
    notification_history_is_recent_in_flight,
    rule_from_row,
)
from .settings import utc_now


class MySQLNotificationJobStore(MySQLOperationalConnection):
    def __init__(self, settings: Dict[str, str] = None):
        super().__init__(settings)
        from .mysql_operational import MySQLNotificationRuleStore

        MySQLNotificationRuleStore(self.runtime_settings)

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

    def recent_page(
        self,
        limit: int = 40,
        offset: int = 0,
        message_type: str = "",
        status: str = "",
        query: str = "",
    ) -> Tuple[List[NotificationJob], int]:
        clauses = []
        params = []
        if str(message_type or "").strip():
            clauses.append("message_type = %s")
            params.append(str(message_type or "").strip())
        if str(status or "").strip():
            clauses.append("status = %s")
            params.append(str(status or "").strip())
        needle = str(query or "").strip()
        if needle:
            clauses.append("(text LIKE %s OR payload_json LIKE %s OR message_type LIKE %s)")
            like = "%" + needle[:120] + "%"
            params.extend([like, like, like])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        page_size = max(1, min(100, int(limit or 40)))
        page_offset = max(0, int(offset or 0))
        with self.connect() as connection:
            total_row = connection.execute(
                "SELECT COUNT(*) AS count FROM notification_jobs" + where,
                params,
            ).fetchone()
            rows = connection.execute(
                "SELECT text, payload_json FROM notification_jobs" + where + " ORDER BY created_at DESC, job_id DESC LIMIT %s OFFSET %s",
                params + [page_size, page_offset],
            ).fetchall()
        return [self.job_from_row(row) for row in rows], int(total_row["count"] or 0) if total_row else 0

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

    def upsert_job_with_connection(self, connection, job: NotificationJob) -> None:
        payload = self.compact_job_payload(job)
        dedupe_value = str(job.dedupe_key or "").strip()[:191] or None
        cursor = connection.execute(
            """
            UPDATE notification_jobs
            SET account_id = %s,
                account_label = %s,
                message_type = %s,
                source_event_id = %s,
                source_event_name = %s,
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
        if cursor.rowcount:
            return
        connection.execute(
            """
            INSERT INTO notification_jobs (
                job_id, account_id, account_label, message_type, source_event_id, source_event_name,
                dedupe_key, status, attempts, created_at, updated_at, last_error, text, payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                job.job_id,
                job.account_id,
                job.account_label,
                job.message_type,
                job.source_event_id,
                job.source_event_name,
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

    def sent_article_history_keys_with_connection(self, connection, job: NotificationJob):
        if not self.sent_article_filter_enabled():
            return set()
        account_id = str(job.account_id or "").strip()
        clauses = ["status IN ('done', 'pending', 'processing')", "message_type IN (%s, %s)"]
        params: List[object] = [NEWS_DIGEST, INVESTMENT_INSIGHT]
        if account_id:
            clauses.append("account_id = %s")
            params.append(account_id)
        params.append(self.sent_article_history_limit())
        rows = connection.execute(
            """
            SELECT payload_json
            FROM notification_jobs
            WHERE """ + " AND ".join(clauses) + """
            ORDER BY created_at DESC, job_id DESC
            LIMIT %s
            """,
            params,
        ).fetchall()
        keys = set()
        for row in rows:
            previous_payload = _json_loads(row["payload_json"], {})
            if str(previous_payload.get("jobId") or previous_payload.get("job_id") or "") == job.job_id:
                continue
            context = previous_payload.get("context") if isinstance(previous_payload.get("context"), dict) else {}
            keys.update(collect_article_identity_keys_from_context(context, max_depth=7, max_nodes=600, max_keys=800))
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
            WHERE message_type = %s AND created_at >= %s AND status IN ('pending', 'processing', 'done')
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

    def evaluate_job_with_connection(self, connection, job: NotificationJob):
        rule = self.rule_for_connection(connection, job.message_type)
        decision = evaluate_notification_rule(job, rule)
        recent_count, previous_context, last_sent_at = self.similar_history_with_connection(
            connection,
            job,
            rule,
            decision.fingerprint,
        )
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
            return False

        decision = self.evaluate_job_with_connection(connection, job)
        context = dict(job.context or {})
        context.update(decision.to_context())
        state_group_key = notification_state_group_key(job)
        if state_group_key:
            context["deliveryStateGroupKey"] = state_group_key
        freshness_decision = evaluate_notification_data_freshness(context, self.runtime_settings)
        context.update(freshness_decision.to_context())
        context = sanitize_notification_context_for_freshness(context, freshness_decision)
        job.context = context
        if decision.should_send and not freshness_decision.should_send:
            job.status = "suppressed"
            job.updated_at = utc_now()
            job.last_error = "데이터 신선도 기준 미통과로 발송하지 않았습니다. " + str(freshness_decision.reason or "")
            job.context["deliverySuppressionReason"] = "stale_data"
            try:
                self.upsert_job_with_connection(connection, job)
            except Exception as error:
                if _is_duplicate_key_error(error):
                    return False
                raise
            return False
        if not decision.should_send:
            job.status = "suppressed"
            job.updated_at = utc_now()
            if decision.suppression_reason == "market_closed":
                job.last_error = "장 시간 외라 발송하지 않았습니다. " + str(decision.market_hours_reason or "")
            elif decision.suppression_reason == "state_cooldown":
                job.last_error = decision.state_reason or "같은 임계값 상태가 지속되어 발송하지 않았습니다."
            else:
                job.last_error = decision.gate_reason or "발송 조건을 충족하지 않아 보내지 않았습니다."
            try:
                self.upsert_job_with_connection(connection, job)
            except Exception as error:
                if _is_duplicate_key_error(error):
                    return False
                raise
            return False

        try:
            self.upsert_job_with_connection(connection, job)
        except Exception as error:
            if _is_duplicate_key_error(error):
                return False
            raise
        return True

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

    def claim_pending(self, limit: int = 10, stale_after_minutes: int = 30) -> List[NotificationJob]:
        stamp = utc_now()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max(1, int(stale_after_minutes or 30)))).isoformat().replace("+00:00", "Z")
        requested = max(1, int(limit or 10))
        claimed: List[NotificationJob] = []
        with self.transaction() as connection:
            query_specs = [
                (
                    """
                    SELECT job_id, text, payload_json FROM notification_jobs
                    WHERE status = 'pending'
                    ORDER BY created_at, job_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (),
                ),
                (
                    """
                    SELECT job_id, text, payload_json FROM notification_jobs
                    WHERE status = 'processing'
                      AND COALESCE(NULLIF(processing_started_at, ''), NULLIF(updated_at, ''), created_at) <= %s
                    ORDER BY created_at, job_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (cutoff,),
                ),
                (
                    """
                    SELECT job_id, text, payload_json FROM notification_jobs
                    WHERE status = 'failed' AND attempts < %s
                    ORDER BY attempts, created_at, job_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (MAX_NOTIFICATION_DELIVERY_ATTEMPTS,),
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
        return {row["status"]: int(row["count"] or 0) for row in rows}
