"""Web notification inbox boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.service_factory import build_notification_queue_runner
from digital_twin.infrastructure.web.adapters.notification_presentation import decode_notification_cursor
from digital_twin.infrastructure.web.adapters.notification_presentation import encode_notification_cursor
from digital_twin.infrastructure.web.adapters.notification_presentation import full_notification_text
from digital_twin.infrastructure.web.adapters.notification_presentation import notification_action_flow
from digital_twin.infrastructure.web.adapters.notification_presentation import notification_customer_text
from digital_twin.infrastructure.web.adapters.notification_presentation import notification_job_diagnostics
from digital_twin.infrastructure.web.adapters.notification_presentation import notification_job_list_payload
from digital_twin.infrastructure.web.adapters.notification_presentation import notification_job_public_payload
from digital_twin.infrastructure.web.adapters.notification_storage import notification_queue_store
from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.common import request_bool
from digital_twin.infrastructure.web.events import new_domain_event
from digital_twin.modules.notifications.domain.event_types import NOTIFICATION_USEFULNESS_RECORDED
from digital_twin.modules.notifications.domain.notification_reverse_reasoning import build_notification_reverse_reasoning_trace
from digital_twin.modules.notifications.public import NotificationFeedbackService
from digital_twin.modules.notifications.public import NotificationReplayService
from typing import Dict
from typing import List


def notification_jobs_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = max(1, min(100, int(first_query(query, "limit") or 20)))
    offset = max(0, int(first_query(query, "offset") or 0))
    message_type = first_query(query, "messageType") or first_query(query, "message_type")
    status = first_query(query, "status")
    search = first_query(query, "query") or first_query(query, "q")
    scope = (first_query(query, "scope") or "investment").strip().lower()
    recipient_id = (first_query(query, "recipientId") or "local-owner").strip()[:191] or "local-owner"
    inbox = (first_query(query, "inbox") or "all").strip().lower()
    if inbox not in {"all", "unread", "important", "action"}:
        inbox = "all"
    cursor_value = first_query(query, "cursor") or ""
    cursor = decode_notification_cursor(cursor_value)
    if scope not in {"investment", "operations", "all"}:
        scope = "investment"
    try:
        settings = operational_read_settings()
        try:
            stale_minutes = max(1, int(settings.get("notificationProcessingStaleMinutes") or 2))
        except (TypeError, ValueError):
            stale_minutes = 2
        store = notification_queue_store(settings)
        if hasattr(store, "recent_list_page_with_summary"):
            jobs, total, summary = store.recent_list_page_with_summary(
                limit=limit,
                offset=offset,
                message_type=message_type,
                status=status,
                query=search,
                scope=scope,
                recipient_id=recipient_id,
                inbox=inbox,
                cursor_updated_at=cursor["updatedAt"],
                cursor_job_id=cursor["jobId"],
            )
        elif hasattr(store, "recent_page_with_summary"):
            jobs, total, summary = store.recent_page_with_summary(
                limit=limit,
                offset=offset,
                message_type=message_type,
                status=status,
                query=search,
                scope=scope,
            )
        else:
            jobs, total = store.recent_page(
                limit=limit,
                offset=offset,
                message_type=message_type,
                status=status,
                query=search,
                scope=scope,
            )
            summary = store.summary()
    except Exception:  # noqa: BLE001 - empty queue keeps the console readable without MySQL.
        jobs = []
        total = 0
        summary = {
            "pending": 0,
            "awaiting_ai": 0,
            "processing": 0,
            "done": 0,
            "superseded": 0,
            "suppressed": 0,
            "failed": 0,
        }
        stale_minutes = 30
        store = None
    receipts = {
        job.job_id: dict((job.context or {}).get("notificationReceipt") or {})
        for job in jobs
        if isinstance((job.context or {}).get("notificationReceipt"), dict)
    }
    inbox_summary = {"total": total, "unread": total, "important": 0, "actionRequired": 0}
    if store and hasattr(store, "receipt_states") and len(receipts) < len(jobs):
        receipts = store.receipt_states(recipient_id, [job.job_id for job in jobs])
    if store and hasattr(store, "inbox_summary"):
        inbox_summary = store.inbox_summary(recipient_id, scope=scope)
    items = []
    for job in jobs:
        item = notification_job_list_payload(job, stale_minutes=stale_minutes, settings=settings)
        receipt = receipts.get(job.job_id, {})
        item.update({
            "readAt": str(receipt.get("readAt") or ""),
            "acknowledgedAt": str(receipt.get("acknowledgedAt") or ""),
            "important": bool(receipt.get("important")),
            "usefulness": str(receipt.get("usefulness") or ""),
            "feedbackReason": str(receipt.get("feedbackReason") or ""),
            "feedbackAt": str(receipt.get("feedbackAt") or ""),
            "receiptUpdatedAt": str(receipt.get("receiptUpdatedAt") or ""),
        })
        items.append(item)
    next_cursor = ""
    if jobs and len(jobs) >= limit and offset + len(jobs) < total:
        next_cursor = encode_notification_cursor(jobs[-1])
    return {
        "jobs": items,
        "summary": summary,
        "inboxSummary": inbox_summary,
        "diagnostics": notification_job_diagnostics(jobs, stale_minutes=stale_minutes),
        "limit": limit,
        "offset": offset,
        "total": total,
        "query": search,
        "messageType": message_type,
        "status": status,
        "scope": scope,
        "recipientId": recipient_id,
        "inbox": inbox,
        "cursor": cursor_value,
        "nextCursor": next_cursor,
    }


def _compact_notification_stage(stage: object) -> Dict[str, object]:
    value = dict(stage or {}) if isinstance(stage, dict) else {}
    return {
        key: value.get(key)
        for key in (
            "sequence", "key", "title", "status", "summary", "startedAt",
            "completedAt", "durationMs", "identifiers",
        )
        if value.get(key) not in (None, "", [], {})
    }


def _compact_notification_reasoning_trace(trace: object) -> Dict[str, object]:
    value = dict(trace or {}) if isinstance(trace, dict) else {}
    ai = dict(value.get("aiExecution") or {}) if isinstance(value.get("aiExecution"), dict) else {}
    narrative = dict(value.get("narrative") or {}) if isinstance(value.get("narrative"), dict) else {}
    rule_evaluations = [
        dict(item) for item in value.get("ruleEvaluations") or [] if isinstance(item, dict)
    ]
    proof_available = len([
        item for item in rule_evaluations
        if str(((item.get("proof") or {}).get("status") if isinstance(item.get("proof"), dict) else "") or "") == "available"
    ])
    return {
        key: value.get(key)
        for key in (
            "version", "status", "reason", "jobId", "jobStatus", "snapshotBound",
            "subject", "snapshot", "finalDecision", "aiComparison", "steps", "completeness",
            "missingData",
        )
        if value.get(key) not in (None, "", [], {})
    } | {
        "aiExecution": {
            key: ai.get(key)
            for key in (
                "status", "requestId", "model", "reasoningEffort", "reviewMode",
                "adoptionState", "actionAuthority", "validationState", "latencyMs",
                "executed", "responseSource", "writerProvenance", "claimPublication",
                "executionSpans",
            )
            if ai.get(key) not in (None, "", [], {})
        },
        "narrative": {
            "writerProvenance": narrative.get("writerProvenance") or {},
            "publication": narrative.get("publication") or {},
            "metrics": narrative.get("metrics") or {},
        },
        "ruleProofSummary": {
            "evaluationCount": len(rule_evaluations),
            "availableProofCount": proof_available,
            "legacyUnavailableCount": max(0, len(rule_evaluations) - proof_available),
        },
    }


def _compact_notification_pipeline(trace: object) -> Dict[str, object]:
    value = dict(trace or {}) if isinstance(trace, dict) else {}
    pipeline = dict(value.get("pipeline") or {}) if isinstance(value.get("pipeline"), dict) else {}
    compact_pipeline = {
        key: pipeline.get(key)
        for key in ("contractVersion", "status", "complete", "stageCount", "bottleneck", "links")
        if pipeline.get(key) not in (None, "", [], {})
    }
    compact_pipeline["stages"] = [_compact_notification_stage(item) for item in pipeline.get("stages") or []]
    return {
        "contractVersion": value.get("contractVersion") or "notification-trace-v2",
        "jobId": value.get("jobId") or "",
        "pipeline": compact_pipeline,
    }


def _notification_detail_section_payload(
    payload: Dict[str, object],
    section: str,
    *,
    include_sensitive: bool,
) -> Dict[str, object]:
    reasoning = dict(payload.get("reasoningTrace") or {})
    trace = dict(payload.get("notificationTrace") or {})
    if section == "reasoning":
        ai = dict(reasoning.get("aiExecution") or {})
        reasoning["aiExecution"] = {
            key: ai.get(key)
            for key in ("status", "requestId", "reviewMode", "adoptionState", "actionAuthority", "executed")
            if ai.get(key) not in (None, "", [], {})
        }
        reasoning.pop("narrative", None)
        return {"jobId": payload.get("jobId"), "section": section, "reasoning": reasoning}
    if section == "ai-review":
        ai = dict(reasoning.get("aiExecution") or {})
        if not include_sensitive:
            from digital_twin.modules.notifications.public import redact_notification_trace_data

            ai = dict(redact_notification_trace_data(ai) or {})
            ai.pop("prompt", None)
            ai["promptAccess"] = "owner-only"
        return {
            "jobId": payload.get("jobId"),
            "section": section,
            "aiExecution": ai,
            "aiRuntime": payload.get("aiRuntime") or {},
            "aiComparison": reasoning.get("aiComparison") or {},
            "finalDecision": reasoning.get("finalDecision") or {},
            "narrative": reasoning.get("narrative") or {},
        }
    if section == "delivery":
        pipeline = dict(trace.get("pipeline") or {})
        stages = [
            dict(item) for item in pipeline.get("stages") or []
            if isinstance(item, dict) and item.get("key") in {"source-event", "rendering", "delivery"}
        ]
        pipeline["stages"] = stages
        pipeline["stageCount"] = len(stages)
        return {
            "jobId": payload.get("jobId"),
            "section": section,
            "lifecycle": trace.get("lifecycle") or [],
            "deliveryAttempts": trace.get("deliveryAttempts") or [],
            "timeline": trace.get("timeline") or [],
            "pipeline": pipeline,
        }
    return {}


def notification_job_detail_payload(
    job_id: str,
    recipient_id: str = "local-owner",
    *,
    section: str = "summary",
    include_sensitive: bool = True,
) -> Dict[str, object]:
    configured = operational_read_settings()
    store = notification_queue_store(configured)
    job = store.get(job_id)
    if not job:
        return {}
    normalized_section = str(section or "summary").strip().lower()
    include_full_reasoning = normalized_section == "reasoning"
    payload = notification_job_public_payload(
        job,
        detail=include_full_reasoning,
        settings=configured,
        include_customer_document=True,
    )
    if not include_full_reasoning:
        customer_text = notification_customer_text(job)
        payload["fullText"] = full_notification_text(customer_text)
        payload["actionFlow"] = notification_action_flow(job.context or {})
        payload["reasoningTrace"] = build_notification_reverse_reasoning_trace(
            job.context or {},
            job_id=job.job_id,
            job_status=job.status,
        )
    if hasattr(store, "receipt_states"):
        receipt = store.receipt_states(recipient_id, [job.job_id]).get(job.job_id, {})
        payload.update({
            "readAt": str(receipt.get("readAt") or ""),
            "acknowledgedAt": str(receipt.get("acknowledgedAt") or ""),
            "important": bool(receipt.get("important")),
            "usefulness": str(receipt.get("usefulness") or ""),
            "feedbackReason": str(receipt.get("feedbackReason") or ""),
            "feedbackAt": str(receipt.get("feedbackAt") or ""),
            "receiptUpdatedAt": str(receipt.get("receiptUpdatedAt") or ""),
        })
    try:
        from digital_twin.modules.notifications.public import NotificationTraceQueryService

        source_event = {}
        if job.source_event_id and normalized_section in {"reasoning", "delivery"}:
            try:
                event = stores.event_log(configured).get(job.source_event_id)
                source_event = event.to_dict() if event else {}
            except Exception as error:  # noqa: BLE001 - a pruned event must not hide the remaining lineage.
                source_event = {"event_id": job.source_event_id, "lookupError": str(error)}
        context = dict(job.context or {})
        case_context = context.get("investmentReasoningCase")
        case_context = dict(case_context or {}) if isinstance(case_context, dict) else {}
        case_id = str(
            context.get("investmentReasoningCaseId")
            or case_context.get("caseId")
            or ""
        ).strip()
        reasoning_case = {}
        subject_case_context = context.get("investmentSubjectDecisionCase")
        subject_case_context = dict(subject_case_context or {}) if isinstance(subject_case_context, dict) else {}
        subject_case_id = str(
            context.get("investmentSubjectDecisionCaseId")
            or subject_case_context.get("subjectCaseId")
            or ""
        ).strip()
        subject_case = {}
        if case_id and normalized_section == "reasoning":
            try:
                case = stores.investment_reasoning_case_store(configured).get(case_id)
                reasoning_case = case.to_dict() if case else case_context
            except Exception as error:  # noqa: BLE001 - compact immutable context remains usable.
                reasoning_case = {**case_context, "caseId": case_id, "lookupError": str(error)}
        if subject_case_id and normalized_section == "reasoning":
            try:
                item = stores.subject_decision_case_store(configured).get(subject_case_id)
                subject_case = item.to_dict() if item else subject_case_context
            except Exception as error:  # noqa: BLE001 - compact immutable context remains usable.
                subject_case = {
                    **subject_case_context,
                    "subjectCaseId": subject_case_id,
                    "lookupError": str(error),
                }
        ai_trace = {}
        if normalized_section == "ai-review":
            try:
                ai_trace = stores.ai_inference_queue_store(configured).trace_for_notification(job.job_id)
            except Exception as error:  # noqa: BLE001 - notification execution audit is the fallback.
                ai_trace = {"lookupError": str(error)}
        if normalized_section in {"summary", "delivery"}:
            payload["notificationTrace"] = NotificationTraceQueryService(store).trace_for_job(
                job,
                reasoning_trace=payload.get("reasoningTrace") or {},
                source_event=source_event,
                reasoning_case=reasoning_case,
                ai_trace=ai_trace,
                rendered_message=str(payload.get("fullText") or ""),
                include_stage_details=False,
            )
        if ai_trace:
            payload["aiRuntime"] = ai_trace
        if reasoning_case and isinstance(payload.get("reasoningTrace"), dict):
            payload["reasoningTrace"]["reasoningCase"] = reasoning_case
        if subject_case and isinstance(payload.get("reasoningTrace"), dict):
            payload["reasoningTrace"]["subjectDecisionCase"] = subject_case
    except Exception as error:  # noqa: BLE001 - the saved notification remains readable without its timeline.
        payload["notificationTrace"] = {
            "contractVersion": "notification-trace-v2",
            "jobId": job.job_id,
            "status": "error",
            "reason": str(error)[:220],
            "lifecycle": [],
            "deliveryAttempts": [],
            "timeline": [],
            "pipeline": {
                "contractVersion": "notification-pipeline-trace-v1",
                "status": "error",
                "complete": False,
                "stageCount": 0,
                "stages": [],
            },
        }
    if normalized_section in {"reasoning", "ai-review", "delivery"}:
        return _notification_detail_section_payload(
            payload,
            normalized_section,
            include_sensitive=include_sensitive,
        )
    payload["detailContractVersion"] = "notification-detail-v2"
    payload["detailSections"] = {
        "summary": "/api/notification-jobs/" + job.job_id,
        "reasoning": "/api/notification-jobs/" + job.job_id + "/reasoning",
        "aiReview": "/api/notification-jobs/" + job.job_id + "/ai-review",
        "delivery": "/api/notification-jobs/" + job.job_id + "/delivery",
    }
    payload["reasoningTrace"] = _compact_notification_reasoning_trace(payload.get("reasoningTrace"))
    payload["notificationTrace"] = _compact_notification_pipeline(payload.get("notificationTrace"))
    return {"job": payload}


def update_notification_receipt_payload(job_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    configured = operational_read_settings()
    store = notification_queue_store(configured)
    service = NotificationFeedbackService(
        store,
        stores.investment_decision_episode_store(configured),
        configured,
    )
    result = service.record(
        job_id,
        str(body.get("recipientId") or "local-owner"),
        read=request_bool(body.get("read")) if "read" in body else None,
        acknowledged=request_bool(body.get("acknowledged")) if "acknowledged" in body else None,
        important=request_bool(body.get("important")) if "important" in body else None,
        usefulness=str(body.get("usefulness") or "") if "usefulness" in body else None,
        feedback_reason=str(body.get("feedbackReason") or "") if "feedbackReason" in body else None,
    )
    if result.get("error"):
        return result
    receipt = dict(result.get("receipt") or {})
    if "usefulness" in body:
        new_domain_event(
            NOTIFICATION_USEFULNESS_RECORDED,
            str(job_id or ""),
            {
                "jobId": str(job_id or ""),
                "recipientId": str(receipt.get("recipientId") or ""),
                "usefulness": str(receipt.get("usefulness") or ""),
                "feedbackReason": str(receipt.get("feedbackReason") or ""),
                "feedbackAt": str(receipt.get("feedbackAt") or ""),
                "learningProposalId": str(
                    (result.get("learningProposal") or {}).get("proposalId") or ""
                ),
            },
        )
    result["inboxSummary"] = store.inbox_summary(
        str(receipt.get("recipientId") or "local-owner"),
        scope="investment",
    )
    return result


def mark_all_notifications_read_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    recipient_id = str(body.get("recipientId") or "local-owner")
    scope = str(body.get("scope") or "investment")
    store = notification_queue_store()
    updated = store.mark_all_read(recipient_id, scope=scope)
    return {"updated": updated, "inboxSummary": store.inbox_summary(recipient_id, scope=scope)}


def replay_notification_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    identifier = configured(body.get("identifier") or body.get("notificationNumber") or body.get("jobId"))
    result = NotificationReplayService(
        queue=notification_queue_store(),
        account_repository=stores.account_reader(),
        runner_factory=build_notification_queue_runner,
        lookup_limit=int(body.get("lookupLimit") or 200),
    ).replay(
        identifier,
        direct=request_bool(body.get("direct")),
        dry_run=request_bool(body.get("dryRun", body.get("dry_run"))),
    )
    return result.to_dict()
