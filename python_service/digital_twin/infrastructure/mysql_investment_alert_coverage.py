"""MySQL projection for material-event to notification coverage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Dict, Iterable, Mapping

from ..domain.investment_alert_coverage import (
    ALERT_COVERAGE_CONTRACT_VERSION,
    derive_coverage_outcome,
    derive_delivery_eligibility,
    evaluate_alert_coverage_health,
    material_event_assessment,
)
from ..domain.context_observation_notifications import (
    is_typedb_context_observation_notification,
)
from ..domain.message_types import INVESTMENT_INSIGHT
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps
from .settings import utc_now


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _upper(value: object) -> str:
    return _text(value).upper()


def _mapping(value: object) -> Dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    return _json_loads(value, {}) if value else {}


def _utc_iso(value: datetime) -> str:
    current = value
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _coverage_id(deployment_id: str, source_event_id: str, account_id: str, symbol: str) -> str:
    raw = "|".join([deployment_id, source_event_id, account_id, symbol])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_context(payload: Mapping[str, object]) -> Dict[str, object]:
    values = dict(payload or {})
    context = values.get("context")
    return dict(context) if isinstance(context, Mapping) else values


class MySQLInvestmentAlertCoverageStore(MySQLOperationalConnection):
    """Build a durable operational projection from existing source-of-truth rows."""

    def reconcile(
        self,
        deployment_id: str,
        *,
        lookback_hours: int = 24,
        deadline_seconds: int = 300,
        starvation_min_candidates: int = 8,
        now: datetime = None,
    ) -> Dict[str, object]:
        clean_deployment = _text(deployment_id)
        if not clean_deployment:
            return {"status": "deployment-required", "updatedCount": 0}
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cutoff = _utc_iso(current - timedelta(hours=max(1, int(lookback_hours or 24))))
        stamp = _utc_iso(current)
        with self.connect() as connection:
            source_rows = connection.execute(
                """
                SELECT source.deployment_id, source.survivor_job_id,
                       source.source_event_id, source.account_id, source.symbol,
                       source.source_snapshot_id, source.source_snapshot_at,
                       source.representation_mode, source.created_at,
                       job.job_status, job.result_json, job.last_error,
                       job.completed_at, job.updated_at AS job_updated_at,
                       event.name AS event_name, event.occurred_at,
                       event.payload_json AS event_payload_json
                FROM reasoning_engine_job_sources AS source
                JOIN reasoning_engine_jobs AS job
                  ON job.job_id = source.survivor_job_id
                 AND job.deployment_id = source.deployment_id
                LEFT JOIN domain_events AS event
                  ON event.event_id = source.source_event_id
                WHERE source.deployment_id = %s AND source.created_at >= %s
                ORDER BY source.created_at, source.source_event_id, source.symbol
                """,
                (clean_deployment, cutoff),
            ).fetchall()

            parsed_rows = []
            reasoning_case_ids = set()
            for row in source_rows or []:
                result = _mapping(row.get("result_json"))
                event_payload = _mapping(row.get("event_payload_json"))
                reasoning_case_id = _text(result.get("reasoning_case_id") or result.get("reasoningCaseId"))
                if reasoning_case_id:
                    reasoning_case_ids.add(reasoning_case_id)
                parsed_rows.append((dict(row), result, event_payload, reasoning_case_id))

            subject_rows = []
            if reasoning_case_ids:
                placeholders = ",".join(["%s"] * len(reasoning_case_ids))
                subject_rows = connection.execute(
                    """
                    SELECT subject_case_id, batch_case_id, account_id, symbol,
                           inference_generation_id, candidate_fingerprint,
                           stage, outcome_kind, ai_request_id,
                           notification_job_id, payload_json,
                           created_at, updated_at, completed_at
                    FROM investment_subject_decision_cases
                    WHERE batch_case_id IN (""" + placeholders + ")",
                    tuple(sorted(reasoning_case_ids)),
                ).fetchall()
            subjects = {
                (
                    _text(row.get("batch_case_id")),
                    _text(row.get("account_id")),
                    _upper(row.get("symbol")),
                ): dict(row)
                for row in subject_rows or []
            }
            ai_request_ids = {
                _text(row.get("ai_request_id"))
                for row in subject_rows or []
                if _text(row.get("ai_request_id"))
            }
            ai_requests = {}
            if ai_request_ids:
                placeholders = ",".join(["%s"] * len(ai_request_ids))
                request_rows = connection.execute(
                    "SELECT request_id, status, last_error, updated_at "
                    "FROM ai_inference_requests WHERE request_id IN (" + placeholders + ")",
                    tuple(sorted(ai_request_ids)),
                ).fetchall()
                ai_requests = {
                    _text(item.get("request_id")): dict(item)
                    for item in request_rows or []
                }

            notification_rows = connection.execute(
                """
                SELECT job_id, status, last_error, payload_json, updated_at, created_at
                FROM notification_jobs
                WHERE message_type = %s AND created_at >= %s
                ORDER BY created_at
                """,
                (INVESTMENT_INSIGHT, cutoff),
            ).fetchall()
            notifications_by_subject = {}
            notifications_by_id = {}
            for row in notification_rows or []:
                notification = dict(row)
                context = _event_context(_mapping(row.get("payload_json")))
                subject_case_id = _text(context.get("investmentSubjectDecisionCaseId"))
                notification["context"] = context
                notifications_by_id[_text(row.get("job_id"))] = notification
                if subject_case_id:
                    notifications_by_subject[subject_case_id] = notification

            values = []
            for row, result, event_payload, reasoning_case_id in parsed_rows:
                symbol = _upper(row.get("symbol"))
                if not symbol:
                    continue
                candidate_events = [
                    dict(item)
                    for item in result.get("candidate_events") or []
                    if isinstance(item, Mapping)
                    and _upper(item.get("symbol")) == symbol
                ]
                candidate_accounts = {
                    _text(item.get("accountId"))
                    for item in candidate_events
                    if _text(item.get("accountId"))
                }
                accounts = {
                    _text(row.get("account_id")),
                    _text(event_payload.get("accountId")),
                    *{
                        _text(item)
                        for item in result.get("account_ids") or []
                        if _text(item)
                    },
                    *candidate_accounts,
                }
                accounts.discard("")
                if not accounts:
                    accounts = {"default"}
                internal_shard = str(row.get("source_event_id") or "").startswith("reasoning-shard:")
                for account_id in sorted(accounts):
                    candidate = next((
                        item for item in candidate_events
                        if not _text(item.get("accountId")) or _text(item.get("accountId")) == account_id
                    ), None)
                    candidate_present = bool(
                        candidate
                        and not is_typedb_context_observation_notification(
                            (candidate or {}).get("metadata") or {}
                        )
                    )
                    material, material_reason = material_event_assessment(
                        event_payload,
                        symbol,
                        candidate_present=candidate_present,
                    )
                    if internal_shard:
                        material = False
                        material_reason = "internal-reasoning-shard"
                    subject = subjects.get((reasoning_case_id, account_id, symbol)) or {}
                    if not candidate:
                        # A batch can include subjects unrelated to this source
                        # row. Only its own candidate may determine delivery.
                        subject = {}
                    subject_payload = _mapping(subject.get("payload_json"))
                    subject_case_id = _text(subject.get("subject_case_id"))
                    notification_job_id = _text(subject.get("notification_job_id"))
                    notification = (
                        notifications_by_id.get(notification_job_id)
                        or notifications_by_subject.get(subject_case_id)
                        or {}
                    )
                    if notification and not notification_job_id:
                        notification_job_id = _text(notification.get("job_id"))
                    notification_context = _mapping(notification.get("context"))
                    suppression_reason = _text(
                        notification_context.get("deliverySuppressionReason")
                        or notification_context.get("suppressionReason")
                    )
                    publication = _mapping(subject_payload.get("publication"))
                    ai_request = ai_requests.get(_text(subject.get("ai_request_id"))) or {}
                    outcome = derive_coverage_outcome({
                        "notificationStatus": notification.get("status"),
                        "notificationError": notification.get("last_error"),
                        "suppressionReason": suppression_reason,
                        "subjectStage": subject.get("stage"),
                        "subjectDeliveryState": subject_payload.get("deliveryState"),
                        "subjectDeliveryReason": subject_payload.get("deliveryReason"),
                        "aiRequestStatus": ai_request.get("status"),
                        "aiRequestError": ai_request.get("last_error"),
                        "publicationDelivered": bool(publication.get("deliveredAt")),
                        "reasoningJobStatus": row.get("job_status"),
                        "reasoningResultStatus": result.get("status"),
                        "candidatePresent": candidate_present,
                        "reasonCode": result.get("reason_code"),
                        "reason": result.get("reason") or row.get("last_error"),
                    })
                    eligibility = derive_delivery_eligibility({
                        "candidatePresent": candidate_present,
                        "notificationStatus": notification.get("status"),
                        "subjectStage": subject.get("stage"),
                        "subjectOutcomeKind": subject.get("outcome_kind"),
                        "subjectDeliveryState": subject_payload.get("deliveryState"),
                        "subjectDeliveryEligible": subject_payload.get("deliveryEligible"),
                        "subjectDeliveryReasonCode": subject_payload.get("deliveryReasonCode"),
                        "subjectDeliveryValueClass": subject_payload.get("deliveryValueClass"),
                        "publicationDelivered": bool(publication.get("deliveredAt")),
                        "suppressionReason": suppression_reason,
                        "cooldownDecision": notification_context.get("cooldownDecision"),
                        "finalAiDeliveryGate": notification_context.get("finalAiDeliveryGate"),
                        "preAiDeferredDeliveryDecision": notification_context.get(
                            "preAiDeferredDeliveryDecision"
                        ),
                    })
                    source_event_id = _text(row.get("source_event_id"))
                    coverage_id = _coverage_id(
                        clean_deployment, source_event_id, account_id, symbol
                    )
                    root_event_id = _text(event_payload.get("sourceEventId"))
                    event_at = _text(
                        event_payload.get("sourceObservedAt")
                        or row.get("occurred_at")
                        or row.get("source_snapshot_at")
                        or row.get("created_at")
                    )
                    terminal_at = ""
                    if bool(outcome.get("terminal")):
                        terminal_at = _text(
                            notification.get("updated_at")
                            or subject.get("updated_at")
                            or row.get("completed_at")
                            or row.get("job_updated_at")
                            or stamp
                        )
                    payload = {
                        "contractVersion": ALERT_COVERAGE_CONTRACT_VERSION,
                        "reasoningResultStatus": _text(result.get("status")),
                        "reasoningJobStatus": _text(row.get("job_status")),
                        "candidatePresent": candidate_present,
                        "candidateEventKey": _text((candidate or {}).get("key")),
                        "subjectStage": _text(subject.get("stage")),
                        "subjectOutcomeKind": _text(subject.get("outcome_kind")),
                        "subjectDeliveryState": _text(subject_payload.get("deliveryState")),
                        "subjectDeliveryReason": _text(subject_payload.get("deliveryReason")),
                        "subjectDeliveryEligible": subject_payload.get("deliveryEligible"),
                        "subjectDeliveryReasonCode": _text(subject_payload.get("deliveryReasonCode")),
                        "subjectDeliveryValueClass": _text(subject_payload.get("deliveryValueClass")),
                        "aiRequestStatus": _text(ai_request.get("status")),
                        "notificationStatus": _text(notification.get("status")),
                        "suppressionReason": suppression_reason,
                        "pushEligible": bool(eligibility.get("eligible")),
                        "pushEligibilityDetermined": bool(eligibility.get("determined")),
                        "pushEligibilityReasonCode": _text(eligibility.get("reasonCode")),
                        "pushValueClass": _text(eligibility.get("pushValueClass")),
                        "representationMode": _text(row.get("representation_mode")),
                    }
                    values.append((
                        coverage_id,
                        clean_deployment,
                        source_event_id,
                        root_event_id,
                        _text(event_payload.get("sourceEventName") or row.get("event_name")),
                        account_id,
                        symbol,
                        int(material),
                        material_reason[:500],
                        _text(outcome.get("state")) or "RECEIVED",
                        int(bool(outcome.get("terminal"))),
                        _text(outcome.get("reasonCode"))[:96],
                        _text(outcome.get("reason"))[:500],
                        _text(row.get("survivor_job_id")),
                        reasoning_case_id,
                        subject_case_id,
                        _text(
                            subject.get("inference_generation_id")
                            or next(iter(result.get("inference_generation_ids") or []), "")
                        ),
                        _text(subject.get("candidate_fingerprint"))[:64],
                        int(candidate_present),
                        notification_job_id,
                        _text(notification.get("status"))[:32],
                        event_at,
                        _text(row.get("completed_at")),
                        terminal_at,
                        json_dumps(payload),
                        stamp,
                        stamp,
                    ))

            if values:
                connection.executemany(
                    """
                    INSERT INTO investment_alert_coverage (
                        coverage_id, deployment_id, source_event_id,
                        root_source_event_id, source_event_name, account_id,
                        symbol, material, material_reason, coverage_state,
                        terminal, reason_code, reason, survivor_job_id,
                        reasoning_case_id, subject_case_id,
                        inference_generation_id, candidate_fingerprint,
                        candidate_present, notification_job_id,
                        notification_status, event_at, reasoning_completed_at,
                        terminal_at, payload_json, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON DUPLICATE KEY UPDATE
                        root_source_event_id = VALUES(root_source_event_id),
                        source_event_name = VALUES(source_event_name),
                        material = VALUES(material),
                        material_reason = VALUES(material_reason),
                        coverage_state = VALUES(coverage_state),
                        terminal = VALUES(terminal),
                        reason_code = VALUES(reason_code),
                        reason = VALUES(reason),
                        survivor_job_id = VALUES(survivor_job_id),
                        reasoning_case_id = VALUES(reasoning_case_id),
                        subject_case_id = VALUES(subject_case_id),
                        inference_generation_id = VALUES(inference_generation_id),
                        candidate_fingerprint = VALUES(candidate_fingerprint),
                        candidate_present = VALUES(candidate_present),
                        notification_job_id = VALUES(notification_job_id),
                        notification_status = VALUES(notification_status),
                        event_at = VALUES(event_at),
                        reasoning_completed_at = VALUES(reasoning_completed_at),
                        terminal_at = VALUES(terminal_at),
                        payload_json = VALUES(payload_json),
                        updated_at = VALUES(updated_at)
                    """,
                    values,
                )
        summary = self.summary(
            clean_deployment,
            lookback_hours=lookback_hours,
            deadline_seconds=deadline_seconds,
            starvation_min_candidates=starvation_min_candidates,
            now=current,
        )
        return {"status": "ok", "updatedCount": len(values), **summary}

    def summary(
        self,
        deployment_id: str = "",
        *,
        lookback_hours: int = 24,
        deadline_seconds: int = 300,
        starvation_min_candidates: int = 8,
        now: datetime = None,
    ) -> Dict[str, object]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cutoff = _utc_iso(current - timedelta(hours=max(1, int(lookback_hours or 24))))
        clean_deployment = _text(deployment_id)
        with self.connect() as connection:
            if not clean_deployment:
                row = connection.execute(
                    "SELECT deployment_id FROM investment_alert_coverage "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
                clean_deployment = _text((row or {}).get("deployment_id"))
            rows = []
            if clean_deployment:
                rows = connection.execute(
                    """
                    SELECT coverage_id, source_event_id, account_id, symbol,
                           material, material_reason, coverage_state, terminal,
                           reason_code, reason, candidate_present,
                           notification_job_id, notification_status,
                           event_at, created_at, updated_at, payload_json
                    FROM investment_alert_coverage
                    WHERE deployment_id = %s AND event_at >= %s
                    ORDER BY event_at DESC, coverage_id DESC
                    """,
                    (clean_deployment, cutoff),
                ).fetchall()
        records = [{
            "coverageId": _text(row.get("coverage_id")),
            "sourceEventId": _text(row.get("source_event_id")),
            "accountId": _text(row.get("account_id")),
            "symbol": _upper(row.get("symbol")),
            "material": bool(row.get("material")),
            "materialReason": _text(row.get("material_reason")),
            "state": _text(row.get("coverage_state")),
            "terminal": bool(row.get("terminal")),
            "reasonCode": _text(row.get("reason_code")),
            "reason": _text(row.get("reason")),
            "candidatePresent": bool(row.get("candidate_present")),
            "pushEligible": bool(_mapping(row.get("payload_json")).get("pushEligible")),
            "pushEligibilityDetermined": bool(
                _mapping(row.get("payload_json")).get("pushEligibilityDetermined")
            ),
            "pushEligibilityReasonCode": _text(
                _mapping(row.get("payload_json")).get("pushEligibilityReasonCode")
            ),
            "pushValueClass": _text(_mapping(row.get("payload_json")).get("pushValueClass")),
            "notificationJobId": _text(row.get("notification_job_id")),
            "notificationStatus": _text(row.get("notification_status")),
            "eventAt": _text(row.get("event_at")),
            "createdAt": _text(row.get("created_at")),
            "updatedAt": _text(row.get("updated_at")),
        } for row in rows or []]
        health = evaluate_alert_coverage_health(
            records,
            now=current,
            deadline_seconds=deadline_seconds,
            starvation_min_candidates=starvation_min_candidates,
        )
        state_counts = {}
        for record in records:
            state = _text(record.get("state")) or "UNKNOWN"
            state_counts[state] = state_counts.get(state, 0) + 1
        return {
            "deploymentId": clean_deployment,
            "lookbackHours": max(1, int(lookback_hours or 24)),
            "health": health,
            "stateCounts": state_counts,
            "recordCount": len(records),
            "latest": records[:40],
            "checkedAt": _utc_iso(current),
        }

    def load_health_state(self, deployment_id: str) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM investment_alert_coverage_health "
                "WHERE deployment_id = %s",
                (_text(deployment_id),),
            ).fetchone()
        return _mapping((row or {}).get("payload_json"))

    def save_health_state(self, deployment_id: str, payload: Mapping[str, object]) -> None:
        stamp = utc_now()
        values = dict(payload or {})
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO investment_alert_coverage_health (
                    deployment_id, health_state, incident_id,
                    consecutive_observations, payload_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    health_state = VALUES(health_state),
                    incident_id = VALUES(incident_id),
                    consecutive_observations = VALUES(consecutive_observations),
                    payload_json = VALUES(payload_json),
                    updated_at = VALUES(updated_at)
                """,
                (
                    _text(deployment_id),
                    _text(values.get("state")) or "healthy",
                    _text(values.get("incidentId")),
                    int(values.get("consecutiveObservations") or 0),
                    json_dumps(values),
                    stamp,
                    stamp,
                ),
            )
