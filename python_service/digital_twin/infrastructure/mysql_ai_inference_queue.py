"""MySQL-backed, latest-wins queue for notification AI inference."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Mapping, Optional

from ..domain.ai_inference_queue import (
    AI_INFERENCE_COMPLETED,
    AI_INFERENCE_FAILED,
    AI_INFERENCE_PENDING,
    AI_INFERENCE_PROCESSING,
    AI_INFERENCE_RETRY,
    AI_INFERENCE_SUPERSEDED,
    AIInferenceRequest,
    AIInferenceResult,
)
from ..domain.events import (
    AI_INFERENCE_COMPLETED as AI_INFERENCE_COMPLETED_EVENT,
    AI_INFERENCE_REQUESTED,
    AI_INFERENCE_SUPERSEDED as AI_INFERENCE_SUPERSEDED_EVENT,
    ai_inference_event,
)
from ..domain.notifications import NotificationJob
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_events import insert_domain_event_with_connection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps
from .settings import utc_now


ACTIVE_STATES = (AI_INFERENCE_PENDING, AI_INFERENCE_PROCESSING, AI_INFERENCE_RETRY)
TERMINAL_STATES = (AI_INFERENCE_COMPLETED, AI_INFERENCE_FAILED, AI_INFERENCE_SUPERSEDED)


def _clean(value: object) -> str:
    return str(value or "").strip()


def _timestamp_after(seconds: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=max(0, int(seconds or 0)))
    ).isoformat().replace("+00:00", "Z")


def _notification_job_from_row(row: Mapping[str, object]) -> NotificationJob:
    payload = _json_loads(row.get("payload_json"), {})
    if not payload.get("text"):
        payload["text"] = _clean(row.get("text"))
    return NotificationJob.from_dict(payload)


def _compact_notification_payload(job: NotificationJob) -> Dict[str, object]:
    payload = job.to_dict()
    payload.pop("text", None)
    return payload


def _decision_case_id_from_context(value: object) -> str:
    context = _json_loads(value, {})
    subject = context.get("investmentSubjectDecisionCase")
    subject = subject if isinstance(subject, dict) else {}
    subject_case_id = _clean(
        context.get("investmentSubjectDecisionCaseId") or subject.get("subjectCaseId")
    )
    if subject_case_id:
        return subject_case_id
    embedded = context.get("investmentReasoningCase")
    embedded = embedded if isinstance(embedded, dict) else {}
    return _clean(context.get("investmentReasoningCaseId") or embedded.get("caseId"))


def compact_ai_queue_context(context: Mapping[str, object]) -> Dict[str, object]:
    """Persist only execution identity; the source notification owns facts.

    ``notification_jobs.payload_json`` remains the canonical immutable input
    for the AI decision. Duplicating that megabyte-scale context in every
    retry row increased MySQL I/O without giving the model more information.
    Claim joins the source notification and rehydrates the full context.
    """

    values = dict(context or {})
    embedded_case = (
        dict(values.get("investmentReasoningCase") or {})
        if isinstance(values.get("investmentReasoningCase"), Mapping)
        else {}
    )
    compact = {
        key: values.get(key)
        for key in [
            "messageType",
            "accountId",
            "accountLabel",
            "jobId",
            "rawSymbol",
            "symbol",
            "investmentReasoningCaseId",
            "investmentReasoningBatchRunId",
            "investmentSubjectDecisionCaseId",
            "decisionCandidateFingerprint",
            "notificationAiDecisionContractVersion",
            "notificationAiReviewMode",
            "notificationAiExecutionProfile",
            "notificationAiReplayManifest",
        ]
        if values.get(key) not in (None, "", [], {})
    }
    case_id = _clean(values.get("investmentReasoningCaseId") or embedded_case.get("caseId"))
    if case_id:
        compact["investmentReasoningCaseId"] = case_id
        compact["investmentReasoningCase"] = {
            key: embedded_case.get(key)
            for key in ["caseId", "state", "inferenceGenerationId", "sourceAboxSnapshotId"]
            if embedded_case.get(key) not in (None, "", [], {})
        }
        compact["investmentReasoningCase"].setdefault("caseId", case_id)
    subject = (
        dict(values.get("investmentSubjectDecisionCase") or {})
        if isinstance(values.get("investmentSubjectDecisionCase"), Mapping)
        else {}
    )
    subject_case_id = _clean(
        values.get("investmentSubjectDecisionCaseId") or subject.get("subjectCaseId")
    )
    if subject_case_id:
        compact["investmentSubjectDecisionCaseId"] = subject_case_id
        compact["investmentSubjectDecisionCase"] = {
            key: subject.get(key)
            for key in [
                "subjectCaseId", "batchCaseId", "stage", "accountId", "symbol",
                "inferenceGenerationId", "sourceAboxSnapshotId", "candidateSetId",
                "candidateFingerprint",
            ]
            if subject.get(key) not in (None, "", [], {})
        }
        compact["investmentSubjectDecisionCase"].setdefault("subjectCaseId", subject_case_id)
    return compact


class MySQLAIInferenceQueueStore(MySQLOperationalConnection):
    supports_atomic_subject_publication = True

    """Atomically coordinate AI work and its source notification outbox row."""

    def enqueue(self, job: NotificationJob, request: AIInferenceRequest) -> Dict[str, object]:
        stamp = utc_now()
        superseded_case_ids = []
        with self.transaction() as connection:
            source = connection.execute(
                "SELECT status, text, payload_json FROM notification_jobs WHERE job_id = %s FOR UPDATE",
                (job.job_id,),
            ).fetchone()
            if not source:
                raise RuntimeError("source notification job does not exist: " + job.job_id)

            existing = connection.execute(
                "SELECT * FROM ai_inference_requests WHERE notification_job_id = %s FOR UPDATE",
                (job.job_id,),
            ).fetchone()
            if existing:
                restored = self.request_from_row(existing)
                if restored.status in ACTIVE_STATES:
                    source_status = _clean(source.get("status"))
                    if source_status != "awaiting_ai":
                        self.set_notification_status_with_connection(
                            connection,
                            job.job_id,
                            "awaiting_ai",
                            "",
                            {
                                "notificationAiQueue": {
                                    "status": "awaiting-ai",
                                    "requestId": restored.request_id,
                                    "subjectKey": restored.subject_key,
                                    "contextHash": restored.context_hash,
                                    "inferenceGenerationId": restored.inference_generation_id,
                                    "model": restored.model,
                                    "reasoningEffort": restored.reasoning_effort,
                                    "recoveredAt": stamp,
                                }
                            },
                            only_if_statuses=("pending", "processing", "failed", "awaiting_ai"),
                            attempts_delta=-1 if source_status == "processing" else 0,
                        )
                    return {
                        "status": restored.status,
                        "requestId": restored.request_id,
                        "notificationJobId": restored.notification_job_id,
                        "subjectKey": restored.subject_key,
                        "existing": True,
                    }
                if restored.status == AI_INFERENCE_COMPLETED:
                    result = connection.execute(
                        "SELECT result_id, response_json FROM ai_inference_results WHERE request_id = %s",
                        (restored.request_id,),
                    ).fetchone()
                    if result:
                        recovered_context = dict(job.context or {})
                        recovered_context["notificationAiValidatedResponse"] = _json_loads(
                            result.get("response_json"),
                            {},
                        )
                        recovered_context["notificationAiQueue"] = {
                            "status": AI_INFERENCE_COMPLETED,
                            "requestId": restored.request_id,
                            "resultId": _clean(result.get("result_id")),
                            "subjectKey": restored.subject_key,
                            "contextHash": restored.context_hash,
                            "model": restored.model,
                            "reasoningEffort": restored.reasoning_effort,
                            "recoveredAt": stamp,
                        }
                        self.set_notification_status_with_connection(
                            connection,
                            job.job_id,
                            "pending",
                            "",
                            replacement_context=recovered_context,
                            only_if_statuses=("pending", "processing", "failed", "awaiting_ai"),
                            attempts_delta=-1 if _clean(source.get("status")) == "processing" else 0,
                        )
                        return {
                            "status": "completed-recovered",
                            "requestId": restored.request_id,
                            "notificationJobId": restored.notification_job_id,
                            "subjectKey": restored.subject_key,
                            "existing": True,
                        }
                if restored.status == AI_INFERENCE_SUPERSEDED:
                    self.set_notification_status_with_connection(
                        connection,
                        job.job_id,
                        AI_INFERENCE_SUPERSEDED,
                        "더 최신인 동일 계정·종목 AI 추론 요청으로 대체되었습니다.",
                        {"notificationAiQueue": {"status": AI_INFERENCE_SUPERSEDED}},
                        only_if_statuses=("pending", "processing", "failed", "awaiting_ai"),
                    )
                    return {
                        "status": AI_INFERENCE_SUPERSEDED,
                        "requestId": restored.request_id,
                        "notificationJobId": restored.notification_job_id,
                        "subjectKey": restored.subject_key,
                        "existing": True,
                    }

                # A terminal failure may be retried by the notification outbox.
                # Replace its one-to-one AI row in the same transaction; the
                # bounded lifecycle events retain the previous failure audit.
                connection.execute(
                    "DELETE FROM ai_inference_subject_heads WHERE latest_request_id = %s",
                    (restored.request_id,),
                )
                connection.execute(
                    "DELETE FROM ai_inference_results WHERE request_id = %s",
                    (restored.request_id,),
                )
                connection.execute(
                    "DELETE FROM ai_inference_requests WHERE request_id = %s",
                    (restored.request_id,),
                )

            connection.execute(
                """
                INSERT IGNORE INTO ai_inference_subject_heads (subject_key, latest_request_id, updated_at)
                VALUES (%s, '', %s)
                """,
                (request.subject_key, stamp),
            )
            head = connection.execute(
                "SELECT latest_request_id FROM ai_inference_subject_heads WHERE subject_key = %s FOR UPDATE",
                (request.subject_key,),
            ).fetchone()
            latest_id = _clean(head.get("latest_request_id") if head else "")
            latest = None
            if latest_id:
                latest = connection.execute(
                    "SELECT * FROM ai_inference_requests WHERE request_id = %s FOR UPDATE",
                    (latest_id,),
                ).fetchone()

            if latest and _clean(latest.get("context_hash")) == request.context_hash:
                current_status = _clean(latest.get("status"))
                if current_status in ACTIVE_STATES + (AI_INFERENCE_COMPLETED,):
                    reason = "같은 계정·종목·추론 컨텍스트가 이미 처리 중이거나 완료되어 중복 AI 요청을 합쳤습니다."
                    self.set_notification_status_with_connection(
                        connection,
                        job.job_id,
                        AI_INFERENCE_SUPERSEDED,
                        reason,
                        {
                            "notificationAiQueue": {
                                "status": "coalesced-identical",
                                "requestId": latest_id,
                                "subjectKey": request.subject_key,
                                "contextHash": request.context_hash,
                            }
                        },
                    )
                    return {
                        "status": "coalesced-identical",
                        "requestId": latest_id,
                        "notificationJobId": job.job_id,
                        "subjectKey": request.subject_key,
                        "existing": True,
                    }

            self.insert_request_with_connection(connection, request)
            if latest:
                superseded_case_id = self.supersede_request_with_connection(
                    connection,
                    latest,
                    request.request_id,
                    stamp,
                )
                if superseded_case_id:
                    superseded_case_ids.append(superseded_case_id)
            connection.execute(
                """
                UPDATE ai_inference_subject_heads
                SET latest_request_id = %s, updated_at = %s
                WHERE subject_key = %s
                """,
                (request.request_id, stamp, request.subject_key),
            )

            queue_context = dict(request.context or {})
            queue_context["notificationAiQueue"] = {
                "status": "awaiting-ai",
                "requestId": request.request_id,
                "subjectKey": request.subject_key,
                "contextHash": request.context_hash,
                "inferenceGenerationId": request.inference_generation_id,
                "model": request.model,
                "reasoningEffort": request.reasoning_effort,
                "queuedAt": stamp,
            }
            self.set_notification_status_with_connection(
                connection,
                job.job_id,
                "awaiting_ai",
                "",
                replacement_context=queue_context,
                attempts_delta=-1,
            )
            insert_domain_event_with_connection(
                connection,
                ai_inference_event(
                    AI_INFERENCE_REQUESTED,
                    request.request_id,
                    notification_job_id=request.notification_job_id,
                    account_id=request.account_id,
                    symbol=request.symbol,
                    inference_generation_id=request.inference_generation_id,
                    model=request.model,
                    reasoning_effort=request.reasoning_effort,
                    status=AI_INFERENCE_PENDING,
                ),
            )
        return {
            "status": "awaiting-ai",
            "requestId": request.request_id,
            "notificationJobId": request.notification_job_id,
            "subjectKey": request.subject_key,
            "existing": False,
            "supersededReasoningCaseIds": superseded_case_ids,
        }

    def insert_request_with_connection(self, connection, request: AIInferenceRequest) -> None:
        durable_context = compact_ai_queue_context(request.context)
        connection.execute(
            """
            INSERT INTO ai_inference_requests (
                request_id, notification_job_id, account_id, account_label,
                message_type, subject_key, symbol, inference_generation_id,
                context_hash, prompt_version, model, reasoning_effort, priority,
                status, attempts, available_at, lease_owner, lease_expires_at,
                heartbeat_at, superseded_by, created_at, updated_at, started_at,
                completed_at, last_error, context_json
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                request.request_id,
                request.notification_job_id,
                request.account_id,
                request.account_label,
                request.message_type,
                request.subject_key,
                request.symbol,
                request.inference_generation_id,
                request.context_hash,
                request.prompt_version,
                request.model,
                request.reasoning_effort,
                request.priority,
                request.status,
                request.attempts,
                request.available_at,
                request.lease_owner,
                request.lease_expires_at,
                request.heartbeat_at,
                request.superseded_by,
                request.created_at,
                request.updated_at,
                request.started_at,
                request.completed_at,
                request.last_error,
                json_dumps(durable_context),
            ),
        )

    def supersede_request_with_connection(
        self,
        connection,
        row: Mapping[str, object],
        superseded_by: str,
        stamp: str,
    ) -> str:
        request_id = _clean(row.get("request_id"))
        notification_job_id = _clean(row.get("notification_job_id"))
        status = _clean(row.get("status"))
        reasoning_case_id = _decision_case_id_from_context(row.get("context_json"))
        if status in ACTIVE_STATES:
            connection.execute(
                """
                UPDATE ai_inference_requests
                SET status = %s, superseded_by = %s, lease_owner = '',
                    lease_expires_at = '', completed_at = %s, updated_at = %s,
                    context_json = '{}'
                WHERE request_id = %s
                """,
                (AI_INFERENCE_SUPERSEDED, superseded_by, stamp, stamp, request_id),
            )
        else:
            connection.execute(
                "UPDATE ai_inference_requests SET superseded_by = %s, updated_at = %s WHERE request_id = %s",
                (superseded_by, stamp, request_id),
            )
        if notification_job_id:
            self.set_notification_status_with_connection(
                connection,
                notification_job_id,
                AI_INFERENCE_SUPERSEDED,
                "더 최신인 동일 계정·종목 AI 추론 요청으로 대체되었습니다.",
                {"notificationAiQueue": {"status": AI_INFERENCE_SUPERSEDED, "supersededBy": superseded_by}},
                only_if_statuses=("pending", "processing", "failed", "awaiting_ai"),
            )
        insert_domain_event_with_connection(
            connection,
            ai_inference_event(
                AI_INFERENCE_SUPERSEDED_EVENT,
                request_id,
                notification_job_id=notification_job_id,
                account_id=_clean(row.get("account_id")),
                symbol=_clean(row.get("symbol")),
                inference_generation_id=_clean(row.get("inference_generation_id")),
                model=_clean(row.get("model")),
                reasoning_effort=_clean(row.get("reasoning_effort")),
                status=AI_INFERENCE_SUPERSEDED,
                superseded_by=superseded_by,
            ),
        )
        return reasoning_case_id

    def claim(self, worker_id: str, limit: int = 1, lease_seconds: int = 360) -> List[AIInferenceRequest]:
        worker = _clean(worker_id) or "notification-ai"
        bounded_limit = max(1, min(10, int(limit or 1)))
        bounded_lease = max(30, min(3600, int(lease_seconds or 360)))
        stamp = utc_now()
        lease_expires = _timestamp_after(bounded_lease)
        claimed: List[AIInferenceRequest] = []
        with self.transaction() as connection:
            expired = connection.execute(
                """
                SELECT request_id, subject_key FROM ai_inference_requests
                WHERE status = %s AND lease_expires_at != '' AND lease_expires_at < %s
                ORDER BY lease_expires_at, request_id LIMIT 100 FOR UPDATE SKIP LOCKED
                """,
                (AI_INFERENCE_PROCESSING, stamp),
            ).fetchall()
            for item in expired or []:
                head = connection.execute(
                    "SELECT latest_request_id FROM ai_inference_subject_heads WHERE subject_key = %s FOR UPDATE",
                    (_clean(item.get("subject_key")),),
                ).fetchone()
                is_latest = _clean(head.get("latest_request_id") if head else "") == _clean(item.get("request_id"))
                connection.execute(
                    """
                    UPDATE ai_inference_requests
                    SET status = %s, available_at = %s, lease_owner = '',
                        lease_expires_at = '', heartbeat_at = '', updated_at = %s,
                        completed_at = %s
                    WHERE request_id = %s AND status = %s
                    """,
                    (
                        AI_INFERENCE_RETRY if is_latest else AI_INFERENCE_SUPERSEDED,
                        stamp if is_latest else "",
                        stamp,
                        "" if is_latest else stamp,
                        _clean(item.get("request_id")),
                        AI_INFERENCE_PROCESSING,
                    ),
                )

            rows = connection.execute(
                """
                SELECT request.*, notification.payload_json AS notification_payload_json,
                       notification.text AS notification_text
                FROM ai_inference_requests request
                JOIN ai_inference_subject_heads head
                  ON head.subject_key = request.subject_key
                 AND head.latest_request_id = request.request_id
                JOIN notification_jobs notification
                  ON notification.job_id = request.notification_job_id
                WHERE request.status IN (%s, %s) AND request.available_at <= %s
                ORDER BY request.priority DESC, request.created_at ASC, request.request_id ASC
                LIMIT %s FOR UPDATE SKIP LOCKED
                """,
                (AI_INFERENCE_PENDING, AI_INFERENCE_RETRY, stamp, bounded_limit),
            ).fetchall()
            for row in rows or []:
                request_id = _clean(row.get("request_id"))
                cursor = connection.execute(
                    """
                    UPDATE ai_inference_requests
                    SET status = %s, attempts = attempts + 1, lease_owner = %s,
                        lease_expires_at = %s, heartbeat_at = %s,
                        started_at = CASE WHEN started_at = '' THEN %s ELSE started_at END,
                        updated_at = %s, last_error = ''
                    WHERE request_id = %s AND status IN (%s, %s)
                    """,
                    (
                        AI_INFERENCE_PROCESSING,
                        worker,
                        lease_expires,
                        stamp,
                        stamp,
                        stamp,
                        request_id,
                        AI_INFERENCE_PENDING,
                        AI_INFERENCE_RETRY,
                    ),
                )
                if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                    continue
                claimed.append(self.request_from_row({
                    **dict(row),
                    "status": AI_INFERENCE_PROCESSING,
                    "attempts": int(row.get("attempts") or 0) + 1,
                    "lease_owner": worker,
                    "lease_expires_at": lease_expires,
                    "heartbeat_at": stamp,
                    "started_at": _clean(row.get("started_at")) or stamp,
                    "updated_at": stamp,
                }))
        return claimed

    def heartbeat(self, request_id: str, worker_id: str, lease_seconds: int = 360) -> bool:
        stamp = utc_now()
        lease_expires = _timestamp_after(max(30, min(3600, int(lease_seconds or 360))))
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE ai_inference_requests request
                JOIN ai_inference_subject_heads head
                  ON head.subject_key = request.subject_key
                 AND head.latest_request_id = request.request_id
                SET request.heartbeat_at = %s, request.lease_expires_at = %s,
                    request.updated_at = %s
                WHERE request.request_id = %s AND request.status = %s
                  AND request.lease_owner = %s
                """,
                (stamp, lease_expires, stamp, _clean(request_id), AI_INFERENCE_PROCESSING, _clean(worker_id)),
            )
        return int(getattr(cursor, "rowcount", 0) or 0) == 1

    def is_current(self, request_id: str, worker_id: str = "") -> bool:
        clauses = ["request.request_id = %s", "request.status = %s"]
        params: List[object] = [_clean(request_id), AI_INFERENCE_PROCESSING]
        if _clean(worker_id):
            clauses.append("request.lease_owner = %s")
            params.append(_clean(worker_id))
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT request.request_id
                FROM ai_inference_requests request
                JOIN ai_inference_subject_heads head
                  ON head.subject_key = request.subject_key
                 AND head.latest_request_id = request.request_id
                WHERE """ + " AND ".join(clauses) + " LIMIT 1",
                params,
            ).fetchone()
        return bool(row)

    def retry(
        self,
        request: AIInferenceRequest,
        worker_id: str,
        reason: object,
        retry_seconds: int = 30,
    ) -> Dict[str, object]:
        stamp = utc_now()
        delay = max(5, min(900, int(retry_seconds or 30) * max(1, request.attempts)))
        with self.transaction() as connection:
            head = connection.execute(
                "SELECT latest_request_id FROM ai_inference_subject_heads WHERE subject_key = %s FOR UPDATE",
                (request.subject_key,),
            ).fetchone()
            current = connection.execute(
                "SELECT status, lease_owner FROM ai_inference_requests WHERE request_id = %s FOR UPDATE",
                (request.request_id,),
            ).fetchone()
            if not current or _clean(current.get("status")) != AI_INFERENCE_PROCESSING or _clean(current.get("lease_owner")) != _clean(worker_id):
                return {"status": "lease-lost", "requestId": request.request_id}
            if _clean(head.get("latest_request_id") if head else "") != request.request_id:
                connection.execute(
                    """
                    UPDATE ai_inference_requests SET status = %s, lease_owner = '',
                        lease_expires_at = '', completed_at = %s, updated_at = %s
                    WHERE request_id = %s
                    """,
                    (AI_INFERENCE_SUPERSEDED, stamp, stamp, request.request_id),
                )
                return {"status": AI_INFERENCE_SUPERSEDED, "requestId": request.request_id}
            available_at = _timestamp_after(delay)
            connection.execute(
                """
                UPDATE ai_inference_requests
                SET status = %s, available_at = %s, lease_owner = '',
                    lease_expires_at = '', heartbeat_at = '', last_error = %s,
                    updated_at = %s
                WHERE request_id = %s
                """,
                (AI_INFERENCE_RETRY, available_at, _clean(reason)[:2000], stamp, request.request_id),
            )
        return {
            "status": AI_INFERENCE_RETRY,
            "requestId": request.request_id,
            "retryAfterSeconds": delay,
            "availableAt": available_at,
        }

    def complete(
        self,
        request: AIInferenceRequest,
        worker_id: str,
        result: AIInferenceResult,
        notification_context: Dict[str, object],
        before_complete=None,
    ) -> bool:
        stamp = utc_now()
        with self.transaction() as connection:
            head = connection.execute(
                "SELECT latest_request_id FROM ai_inference_subject_heads WHERE subject_key = %s FOR UPDATE",
                (request.subject_key,),
            ).fetchone()
            current = connection.execute(
                "SELECT status, lease_owner FROM ai_inference_requests WHERE request_id = %s FOR UPDATE",
                (request.request_id,),
            ).fetchone()
            publishable = bool(
                current
                and _clean(current.get("status")) == AI_INFERENCE_PROCESSING
                and _clean(current.get("lease_owner")) == _clean(worker_id)
                and _clean(head.get("latest_request_id") if head else "") == request.request_id
            )
            if not publishable:
                if current and _clean(current.get("status")) == AI_INFERENCE_PROCESSING:
                    connection.execute(
                        """
                        UPDATE ai_inference_requests SET status = %s, lease_owner = '',
                            lease_expires_at = '', completed_at = %s, updated_at = %s
                        WHERE request_id = %s
                        """,
                        (AI_INFERENCE_SUPERSEDED, stamp, stamp, request.request_id),
                    )
                return False

            if callable(before_complete):
                before_complete(connection)

            connection.execute(
                """
                INSERT INTO ai_inference_results (
                    result_id, request_id, notification_job_id, model,
                    reasoning_effort, source, validation_state, latency_ms,
                    prompt_bytes, response_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE result_id = VALUES(result_id),
                    source = VALUES(source), validation_state = VALUES(validation_state),
                    latency_ms = VALUES(latency_ms), prompt_bytes = VALUES(prompt_bytes),
                    response_json = VALUES(response_json), created_at = VALUES(created_at)
                """,
                (
                    result.result_id,
                    result.request_id,
                    result.notification_job_id,
                    result.model,
                    result.reasoning_effort,
                    result.source,
                    result.validation_state,
                    result.latency_ms,
                    result.prompt_bytes,
                    json_dumps(result.response),
                    result.created_at,
                ),
            )
            connection.execute(
                """
                UPDATE ai_inference_requests
                SET status = %s, lease_owner = '', lease_expires_at = '',
                    heartbeat_at = '', completed_at = %s, updated_at = %s,
                    last_error = '', context_json = '{}'
                WHERE request_id = %s
                """,
                (AI_INFERENCE_COMPLETED, stamp, stamp, request.request_id),
            )
            completed_context = dict(notification_context or {})
            completed_context["notificationAiQueue"] = {
                "status": AI_INFERENCE_COMPLETED,
                "requestId": request.request_id,
                "resultId": result.result_id,
                "subjectKey": request.subject_key,
                "contextHash": request.context_hash,
                "inferenceGenerationId": request.inference_generation_id,
                "model": request.model,
                "reasoningEffort": request.reasoning_effort,
                "attempts": request.attempts,
                "latencyMs": result.latency_ms,
                "promptBytes": result.prompt_bytes,
                "completedAt": stamp,
            }
            updated = self.set_notification_status_with_connection(
                connection,
                request.notification_job_id,
                "pending",
                "",
                replacement_context=completed_context,
                only_if_statuses=("awaiting_ai",),
            )
            if not updated:
                raise RuntimeError("source notification is no longer awaiting AI: " + request.notification_job_id)
            insert_domain_event_with_connection(
                connection,
                ai_inference_event(
                    AI_INFERENCE_COMPLETED_EVENT,
                    request.request_id,
                    notification_job_id=request.notification_job_id,
                    account_id=request.account_id,
                    symbol=request.symbol,
                    inference_generation_id=request.inference_generation_id,
                    model=request.model,
                    reasoning_effort=request.reasoning_effort,
                    status=AI_INFERENCE_COMPLETED,
                ),
            )
        return True

    def fail(self, request: AIInferenceRequest, worker_id: str, reason: object) -> bool:
        stamp = utc_now()
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT status, lease_owner FROM ai_inference_requests WHERE request_id = %s FOR UPDATE",
                (request.request_id,),
            ).fetchone()
            if not current or _clean(current.get("status")) != AI_INFERENCE_PROCESSING or _clean(current.get("lease_owner")) != _clean(worker_id):
                return False
            connection.execute(
                """
                UPDATE ai_inference_requests SET status = %s, lease_owner = '',
                    lease_expires_at = '', completed_at = %s, updated_at = %s,
                    last_error = %s, context_json = '{}' WHERE request_id = %s
                """,
                (AI_INFERENCE_FAILED, stamp, stamp, _clean(reason)[:2000], request.request_id),
            )
            self.set_notification_status_with_connection(
                connection,
                request.notification_job_id,
                "failed",
                "AI 추론 큐 처리 실패: " + _clean(reason)[:1000],
                {"notificationAiQueue": {"status": AI_INFERENCE_FAILED, "requestId": request.request_id}},
                only_if_statuses=("awaiting_ai",),
            )
        return True

    def set_notification_status_with_connection(
        self,
        connection,
        notification_job_id: str,
        status: str,
        error: str = "",
        context_updates: Dict[str, object] = None,
        *,
        replacement_context: Dict[str, object] = None,
        only_if_statuses=(),
        attempts_delta: int = 0,
    ) -> bool:
        row = connection.execute(
            "SELECT status, text, payload_json FROM notification_jobs WHERE job_id = %s FOR UPDATE",
            (_clean(notification_job_id),),
        ).fetchone()
        if not row:
            return False
        if only_if_statuses and _clean(row.get("status")) not in set(only_if_statuses):
            return False
        job = _notification_job_from_row(row)
        context = dict(replacement_context) if isinstance(replacement_context, dict) else dict(job.context or {})
        for key, value in dict(context_updates or {}).items():
            if isinstance(value, dict) and isinstance(context.get(key), dict):
                context[key] = {**dict(context.get(key) or {}), **value}
            else:
                context[key] = value
        job.context = context
        job.status = _clean(status) or job.status
        job.attempts = max(0, int(job.attempts or 0) + int(attempts_delta or 0))
        job.updated_at = utc_now()
        job.last_error = _clean(error)
        cursor = connection.execute(
            """
            UPDATE notification_jobs
            SET status = %s, attempts = %s, updated_at = %s, last_error = %s,
                processing_started_at = '', retry_at = '', payload_json = %s
            WHERE job_id = %s
            """,
            (
                job.status,
                job.attempts,
                job.updated_at,
                job.last_error,
                json_dumps(_compact_notification_payload(job)),
                job.job_id,
            ),
        )
        return int(getattr(cursor, "rowcount", 0) or 0) == 1

    def get(self, request_id: str) -> Optional[AIInferenceRequest]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT request.*, notification.payload_json AS notification_payload_json,
                       notification.text AS notification_text
                FROM ai_inference_requests request
                LEFT JOIN notification_jobs notification
                  ON notification.job_id = request.notification_job_id
                WHERE request.request_id = %s
                """,
                (_clean(request_id),),
            ).fetchone()
        return self.request_from_row(row) if row else None

    def trace_for_notification(self, notification_job_id: str) -> Dict[str, object]:
        """Return one read-only AI execution trace for notification diagnostics."""

        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT request.request_id, request.notification_job_id,
                       request.subject_key, request.symbol,
                       request.inference_generation_id, request.context_hash,
                       request.prompt_version, request.model,
                       request.reasoning_effort, request.priority,
                       request.status, request.attempts, request.available_at,
                       request.created_at, request.updated_at,
                       request.started_at, request.completed_at,
                       request.superseded_by, request.last_error, request.context_json,
                       result.result_id, result.source AS result_source,
                       result.validation_state AS result_validation_state,
                       result.latency_ms, result.prompt_bytes,
                       result.response_json, result.created_at AS result_created_at
                FROM ai_inference_requests request
                LEFT JOIN ai_inference_results result
                  ON result.request_id = request.request_id
                WHERE request.notification_job_id = %s
                ORDER BY request.created_at DESC, request.request_id DESC
                LIMIT 1
                """,
                (_clean(notification_job_id),),
            ).fetchone()
        if not row:
            return {}
        queue_context = _json_loads(row.get("context_json"), {})
        response = _json_loads(row.get("response_json"), {})
        return {
            "requestId": _clean(row.get("request_id")),
            "notificationJobId": _clean(row.get("notification_job_id")),
            "subjectKey": _clean(row.get("subject_key")),
            "symbol": _clean(row.get("symbol")),
            "inferenceGenerationId": _clean(row.get("inference_generation_id")),
            "contextHash": _clean(row.get("context_hash")),
            "promptVersion": _clean(row.get("prompt_version")),
            "model": _clean(row.get("model")),
            "reasoningEffort": _clean(row.get("reasoning_effort")),
            "reviewMode": _clean(queue_context.get("notificationAiReviewMode") or response.get("reviewMode")) or "investment-judgement",
            "priority": int(row.get("priority") or 0),
            "status": _clean(row.get("status")),
            "attempts": int(row.get("attempts") or 0),
            "availableAt": _clean(row.get("available_at")),
            "createdAt": _clean(row.get("created_at")),
            "updatedAt": _clean(row.get("updated_at")),
            "startedAt": _clean(row.get("started_at")),
            "completedAt": _clean(row.get("completed_at")),
            "supersededBy": _clean(row.get("superseded_by")),
            "lastError": _clean(row.get("last_error")),
            "resultId": _clean(row.get("result_id")),
            "resultSource": _clean(row.get("result_source")),
            "validationState": _clean(row.get("result_validation_state")),
            "latencyMs": int(row.get("latency_ms") or 0),
            "promptBytes": int(row.get("prompt_bytes") or 0),
            "resultCreatedAt": _clean(row.get("result_created_at")),
            "response": response,
        }

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
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count, MIN(created_at) AS oldest_at
                FROM ai_inference_requests GROUP BY status
                """
            ).fetchall()
            active_failure_row = connection.execute(
                "SELECT COUNT(*) AS count, MIN(updated_at) AS oldest_at "
                "FROM ai_inference_requests WHERE status = %s AND updated_at >= %s",
                (AI_INFERENCE_FAILED, active_cutoff),
            ).fetchone()
        states = {
            _clean(row.get("status")): {
                "count": int(row.get("count") or 0),
                "oldestAt": _clean(row.get("oldest_at")),
            }
            for row in rows or []
        }
        historical_failed = int((states.get(AI_INFERENCE_FAILED) or {}).get("count") or 0)
        actionable_failed = int((active_failure_row or {}).get("count") or 0)
        return {
            "states": states,
            "pendingCount": int((states.get(AI_INFERENCE_PENDING) or {}).get("count") or 0),
            "retryCount": int((states.get(AI_INFERENCE_RETRY) or {}).get("count") or 0),
            "processingCount": int((states.get(AI_INFERENCE_PROCESSING) or {}).get("count") or 0),
            # ``failedCount`` remains for API compatibility. Health readers
            # use actionableFailedCount so retained audit rows cannot keep a
            # recovered runtime in a permanent critical state.
            "failedCount": historical_failed,
            "actionableFailedCount": actionable_failed,
            "historicalFailedCount": historical_failed,
            "activeFailureWindowMinutes": active_window_minutes,
            "oldestActionableFailureAt": _clean(
                (active_failure_row or {}).get("oldest_at")
            ),
        }

    def prune_terminal(self, retention_hours: int = 24, limit: int = 50) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=max(1, min(24 * 30, int(retention_hours or 24))))
        ).isoformat().replace("+00:00", "Z")
        bounded = max(1, min(200, int(limit or 50)))
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT request_id, subject_key FROM ai_inference_requests
                WHERE status IN (%s, %s, %s) AND completed_at != '' AND completed_at < %s
                ORDER BY completed_at, request_id LIMIT %s FOR UPDATE SKIP LOCKED
                """,
                (*TERMINAL_STATES, cutoff, bounded),
            ).fetchall()
            request_ids = [_clean(row.get("request_id")) for row in rows or [] if _clean(row.get("request_id"))]
            if not request_ids:
                return 0
            placeholders = ",".join(["%s"] * len(request_ids))
            connection.execute(
                "DELETE FROM ai_inference_subject_heads WHERE latest_request_id IN (" + placeholders + ")",
                request_ids,
            )
            connection.execute(
                "DELETE FROM ai_inference_results WHERE request_id IN (" + placeholders + ")",
                request_ids,
            )
            cursor = connection.execute(
                "DELETE FROM ai_inference_requests WHERE request_id IN (" + placeholders + ")",
                request_ids,
            )
        return int(getattr(cursor, "rowcount", 0) or 0)

    @staticmethod
    def request_from_row(row: Mapping[str, object]) -> AIInferenceRequest:
        values = dict(row or {})
        queue_context = _json_loads(values.pop("context_json", "{}"), {})
        notification_payload = _json_loads(values.pop("notification_payload_json", "{}"), {})
        notification_context = (
            dict(notification_payload.get("context") or {})
            if isinstance(notification_payload.get("context"), dict)
            else {}
        )
        values.pop("notification_text", None)
        # The notification payload is the canonical immutable AI input. Queue
        # metadata only fills identities missing from old notification rows;
        # its compact reasoning-case stub must never replace the full TypeDB
        # hypothesis and action-envelope contract.
        values["context"] = {**queue_context, **notification_context}
        return AIInferenceRequest.from_dict(values)
