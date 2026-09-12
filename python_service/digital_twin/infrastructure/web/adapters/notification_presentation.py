"""Web notification presentation boundary."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.common import parse_utc
from digital_twin.infrastructure.web.common import utc_iso
from digital_twin.modules.decisions.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.modules.decisions.domain.notification_ai_gate_text import user_friendly_ai_text
from digital_twin.modules.notifications.domain.message_types import INVESTMENT_INSIGHT
from digital_twin.modules.notifications.domain.notification_icon_policy import notification_title_with_context_icon
from digital_twin.modules.notifications.domain.notification_reverse_reasoning import build_notification_reverse_reasoning_trace
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.public import NotificationRenderingService
from digital_twin.modules.notifications.public import compact_invalidation_line
from digital_twin.modules.notifications.public import compact_next_action_line
from digital_twin.modules.notifications.public import decision_transition_presentation
from digital_twin.modules.notifications.public import execution_headline
from digital_twin.modules.read_models.domain.investment_analysis import investment_decision_key
from digital_twin.modules.reasoning.domain.ontology_decision_state import ACTION_ENVELOPE_STATUS_LABELS
from typing import Dict
from typing import List
import base64
import html
import json
import re


def compact_notification_text(value: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", full_notification_text(value)).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def notification_action_label(action: object, target_role: object = "") -> str:
    code = str(action or "").strip().upper()
    watchlist = str(target_role or "").strip().lower() == "watchlist"
    labels = {
        "BUY": "소액 진입 검토",
        "ADD": "소액 추가매수 검토",
        "HOLD": "관심 유지" if watchlist else "보유 유지",
        "TRIM": "분할축소 검토",
        "SELL": "매도 검토",
        "AVOID": "신규 진입 회피",
    }
    return labels.get(code, code or "조건 확인")


def notification_action_flow(context: Dict[str, object]) -> Dict[str, object]:
    """Expose the user-facing TypeDB action flow without raw debug payloads."""

    context = context if isinstance(context, dict) else {}
    relation = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
    if not relation:
        relation = context.get("relationContext") if isinstance(context.get("relationContext"), dict) else {}
    decision = relation.get("decision") if isinstance(relation.get("decision"), dict) else {}
    envelope = relation.get("actionEnvelope") if isinstance(relation.get("actionEnvelope"), dict) else {}
    if not envelope:
        envelope = decision.get("actionEnvelope") if isinstance(decision.get("actionEnvelope"), dict) else {}
    relation_diff = context.get("ontologyRelationDiff") if isinstance(context.get("ontologyRelationDiff"), dict) else {}
    transition = context.get("decisionTransition") if isinstance(context.get("decisionTransition"), dict) else {}
    if not transition:
        transition = relation_diff.get("decisionTransition") if isinstance(relation_diff.get("decisionTransition"), dict) else {}
    validated = context.get("notificationAiValidatedResponse") if isinstance(context.get("notificationAiValidatedResponse"), dict) else {}
    user_state = context.get("investmentNotificationState") if isinstance(context.get("investmentNotificationState"), dict) else {}
    user_transition = context.get("investmentNotificationTransition") if isinstance(context.get("investmentNotificationTransition"), dict) else {}
    target_role = str(envelope.get("targetRole") or decision.get("targetRole") or relation.get("targetRole") or "")
    action = str(validated.get("action") or envelope.get("preferredAction") or decision.get("candidateAction") or "")
    action_label = str(validated.get("actionLabel") or "") or notification_action_label(action, target_role)
    if not any([envelope, transition, action]):
        return {}

    effect_rows = envelope.get("effectLabels") if isinstance(envelope.get("effectLabels"), list) else []
    effects = []
    for item in effect_rows:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if label and label not in effects:
            effects.append(label)
    news_impact = context.get("newsImpact") if isinstance(context.get("newsImpact"), dict) else {}
    inline_news = {}
    if (
        news_impact.get("decisionChanging")
        and news_impact.get("decisionInlineEligible") is True
        and news_impact.get("decisionDriverConfirmed") is True
    ):
        inline_news = {
            "headline": compact_notification_text(user_friendly_ai_text(news_impact.get("headline") or "", 180), 180),
            "source": user_friendly_ai_text(news_impact.get("source") or "", 80),
            "impact": str(news_impact.get("impact") or "")[:80],
        }
    readiness = envelope.get("dataReadiness") if isinstance(envelope.get("dataReadiness"), dict) else {}
    transition_presentation = decision_transition_presentation(context, action)
    response = (
        NotificationAIValidatedResponse.from_dict(validated)
        if validated
        else NotificationAIValidatedResponse(action=action, action_label=action_label)
    )
    execution_plan = relation.get("executionPlan") if isinstance(relation.get("executionPlan"), dict) else {}
    has_next_check = bool(response.next_checks or envelope.get("nextChecks") or execution_plan.get("nextChecks"))
    has_invalidation = bool(
        response.invalidation_condition
        or envelope.get("invalidationConditions")
        or execution_plan.get("weakenConditions")
    )
    next_action = compact_next_action_line(context, response) if has_next_check else ""
    invalidation = compact_invalidation_line(context, response) if has_invalidation else ""
    return {
        "status": str(envelope.get("status") or ""),
        "statusLabel": str(envelope.get("statusLabel") or ACTION_ENVELOPE_STATUS_LABELS.get(str(envelope.get("status") or "").upper(), "조건 확인")),
        "currentAction": action,
        "currentActionLabel": action_label,
        "userState": {
            "code": str(user_state.get("code") or ""),
            "label": str(user_state.get("label") or ""),
            "readiness": str(user_state.get("readiness") or ""),
            "readinessLabel": str(user_state.get("readinessLabel") or ""),
        } if user_state else {},
        "userTransition": {
            "kind": str(user_transition.get("kind") or ""),
            "changed": bool(user_transition.get("changed")),
            "changedFieldLabels": [str(item) for item in user_transition.get("changedFieldLabels") or []],
            "summary": compact_notification_text(str(user_transition.get("summary") or ""), 260),
            "previousState": dict(user_transition.get("previousState") or {}) if isinstance(user_transition.get("previousState"), dict) else {},
            "currentState": dict(user_transition.get("currentState") or {}) if isinstance(user_transition.get("currentState"), dict) else {},
        } if user_transition else {},
        "transition": {
            "kind": str(transition.get("kind") or ""),
            "category": str(transition_presentation.get("category") or ""),
            "label": str(transition_presentation.get("label") or ""),
            "summary": compact_notification_text(
                str(transition_presentation.get("summary") or user_friendly_ai_text(transition.get("summary") or "", 180)),
                220,
            ),
            "previousAction": str(transition.get("previousAction") or ""),
            "currentAction": str(transition.get("currentAction") or action),
            "previousStatus": str(transition.get("previousStatus") or ""),
            "currentStatus": str(transition.get("currentStatus") or envelope.get("status") or ""),
        },
        "effects": effects[:4],
        "nextChecks": [compact_notification_text(next_action, 180)] if next_action else [],
        "invalidationConditions": [compact_notification_text(invalidation, 180)] if invalidation else [],
        "dataReadiness": {
            "state": str(readiness.get("state") or ""),
            "dataState": str(readiness.get("dataState") or relation.get("dataState") or ""),
            "usable": bool(readiness.get("usable")) if readiness else None,
        },
        "newsImpact": inline_news,
    }


def full_notification_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def notification_customer_text(job: NotificationJob) -> str:
    """Render persisted decisions with the current customer-safe format."""

    return NotificationRenderingService.render_persisted_customer_text(job)


def notification_processing_age_minutes(job: NotificationJob) -> float:
    started_at = parse_utc(str((job.context or {}).get("processingStartedAt") or job.updated_at or job.created_at or ""))
    if job.status != "processing" or not started_at:
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds() / 60)


def notification_next_eligible_at(context: Dict[str, object]) -> str:
    if not context.get("cooldownEnabled"):
        return ""
    if str(context.get("cooldownDecision") or "") != "cooldown" and not context.get("cooldownSuppressed"):
        return ""
    last_sent_at = parse_utc(str(context.get("cooldownLastSentAt") or ""))
    if not last_sent_at:
        return ""
    try:
        minutes = int(float(context.get("cooldownMinutes") or 0))
    except (TypeError, ValueError):
        minutes = 0
    if minutes <= 0:
        return ""
    return utc_iso(last_sent_at + timedelta(minutes=minutes))


def notification_suppression_summary(job: NotificationJob) -> str:
    context = dict(job.context or {})
    if job.status != "suppressed":
        return ""
    if job.last_error:
        return job.last_error
    if context.get("cooldownReason"):
        return str(context.get("cooldownReason"))
    if context.get("marketHoursReason"):
        return str(context.get("marketHoursReason"))
    if context.get("quietHoursReason"):
        return str(context.get("quietHoursReason"))
    reason = str(context.get("deliverySuppressionReason") or "").strip()
    if reason in {"stale_data", "stale_data_at_dispatch"}:
        return "데이터 신선도 기준 미통과"
    if reason == "stale_data_recheck_requested":
        return "오래된 판단은 보내지 않고 최신 데이터 재수집을 예약했습니다."
    if reason == "market_closed":
        return "장 시간 외 발송 보류"
    if reason == "state_cooldown":
        return "같은 상태 반복 발송 보류"
    if reason == "initial_graph_baseline":
        return "최초 참고 관계는 비교 기준으로만 저장"
    if reason == "unchanged_graph_inference":
        return "TypeDB 행동 범위가 이전과 같아 반복 발송 보류"
    if reason == "unresolved_material_evidence":
        return "관계를 만든 정확한 원문 근거를 연결하지 못해 발송 보류"
    return reason or "알림 정책으로 발송 보류"


def notification_job_diagnostics(
    jobs: List[NotificationJob],
    *,
    stale_minutes: int = None,
    settings: Dict[str, object] = None,
) -> Dict[str, object]:
    if stale_minutes is None:
        configured_settings = settings or operational_read_settings()
        try:
            stale_minutes = max(1, int(configured_settings.get("notificationProcessingStaleMinutes") or 2))
        except (TypeError, ValueError):
            stale_minutes = 2
    reason_counts: Dict[str, int] = {}
    stale_processing = 0
    for job in jobs:
        if job.status == "suppressed":
            reason = notification_suppression_summary(job) or "보류 사유 없음"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if job.status == "processing" and notification_processing_age_minutes(job) >= stale_minutes:
            stale_processing += 1
    top_reasons = sorted(reason_counts.items(), key=lambda item: item[1], reverse=True)[:6]
    return {
        "processingStaleMinutes": stale_minutes,
        "staleProcessingCount": stale_processing,
        "suppressionReasons": [{"reason": reason, "count": count} for reason, count in top_reasons],
    }


def notification_job_public_payload(
    job: NotificationJob,
    detail: bool = False,
    stale_minutes: int = None,
    settings: Dict[str, object] = None,
    include_customer_document: bool = False,
) -> Dict[str, object]:
    context = job.context or {}
    configured_settings = settings
    customer_document = (
        dict(context.get("customerInvestmentDocument") or {})
        if isinstance(context.get("customerInvestmentDocument"), dict)
        else {}
    )
    customer_document_quality = (
        dict(context.get("customerInvestmentDocumentQuality") or {})
        if isinstance(context.get("customerInvestmentDocumentQuality"), dict)
        else {}
    )
    customer_text = str(job.text or "")
    if context.get("_notificationListProjection"):
        from digital_twin.modules.notifications.public import present_notification

        customer_text = present_notification(job.message_type, context, customer_text)
    elif job.message_type == INVESTMENT_INSIGHT:
        presentation_job = NotificationJob.from_dict(job.to_dict())
        try:
            NotificationRenderingService.apply_investment_presentation_contract(
                presentation_job
            )
            customer_document = dict(
                (presentation_job.context or {}).get("customerInvestmentDocument") or {}
            )
            customer_document_quality = dict(
                (presentation_job.context or {}).get("customerInvestmentDocumentQuality") or {}
            )
            customer_text = str(
                (presentation_job.context or {}).get("telegramMessage")
                or presentation_job.text
                or customer_text
            )
        except Exception:  # noqa: BLE001 - archived text remains the safe fallback.
            customer_document = {}
    reasons = context.get("deliveryReasons") if isinstance(context.get("deliveryReasons"), list) else []
    trigger_ledger = context.get("deliveryTriggerLedger") if isinstance(context.get("deliveryTriggerLedger"), list) else []
    delivery_explanation = (
        dict(context.get("customerDeliveryExplanation") or {})
        if isinstance(context.get("customerDeliveryExplanation"), dict)
        else {}
    )
    title_source = context.get("headline") if job.message_type == INVESTMENT_INSIGHT else (context.get("title") or context.get("headline") or "")
    if job.message_type == INVESTMENT_INSIGHT:
        if customer_document.get("headline"):
            title_source = customer_document.get("headline")
        validated = context.get("notificationAiValidatedResponse") if isinstance(context.get("notificationAiValidatedResponse"), dict) else {}
        if validated and not customer_document.get("headline"):
            try:
                title_source = execution_headline(context, NotificationAIValidatedResponse.from_dict(validated))
            except Exception:  # noqa: BLE001 - old incomplete alert payloads keep their saved title.
                pass
    title = notification_title_with_context_icon(
        job.message_type,
        title_source,
        context,
    )
    processing_age = notification_processing_age_minutes(job)
    episode = context.get("investmentDecisionEpisode") if isinstance(context.get("investmentDecisionEpisode"), dict) else {}
    relation = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
    symbol = str(context.get("symbol") or context.get("rawSymbol") or "").strip().upper()
    decision_episode_id = str(
        context.get("investmentDecisionEpisodeId")
        or context.get("decisionEpisodeId")
        or episode.get("episodeId")
        or relation.get("investmentDecisionEpisodeId")
        or ""
    ).strip()
    decision_key = str(context.get("decisionKey") or "").strip()
    if not decision_key and symbol and decision_episode_id:
        decision_key = investment_decision_key(job.account_id or "default", symbol, decision_episode_id)
    data_quality = str(context.get("dataQuality") or relation.get("dataQuality") or "actual")
    data_mode = str(context.get("dataMode") or context.get("mode") or "").lower()
    is_mock = bool(context.get("isMock")) or data_quality.lower() in {"mock", "demo"} or data_mode in {"mock", "demo", "preview"}
    api_source = str(context.get("apiSource") or context.get("quoteSource") or context.get("sourceApi") or "notification_jobs")
    configured_settings = configured_settings or operational_read_settings()
    if stale_minutes is None:
        try:
            stale_minutes = max(1, int(configured_settings.get("notificationProcessingStaleMinutes") or 2))
        except (TypeError, ValueError):
            stale_minutes = 2
    try:
        active_failure_window_minutes = max(5, min(24 * 60, int(float(
            configured_settings.get("operationalActiveFailureWindowMinutes") or 60
        ))))
    except (TypeError, ValueError):
        active_failure_window_minutes = 60
    changed_at = parse_utc(str(job.updated_at or job.created_at or ""))
    changed_age_minutes = (
        max(0.0, (datetime.now(timezone.utc) - changed_at).total_seconds() / 60)
        if changed_at else None
    )
    recoverable_processing = bool(
        job.status == "processing" and processing_age >= stale_minutes
    )
    recent_failure = bool(
        job.status == "failed"
        and changed_age_minutes is not None
        and changed_age_minutes <= active_failure_window_minutes
    )
    priority_queue_eligible = recent_failure or recoverable_processing
    priority_queue_state = (
        "recent-failure" if recent_failure
        else "stalled-processing" if recoverable_processing
        else "historical-failure" if job.status == "failed"
        else "not-actionable"
    )
    from digital_twin.modules.notifications.contracts import presentation_metadata
    from digital_twin.modules.notifications.public import notification_heading

    presentation = presentation_metadata(job.message_type, context)
    stored_document = context.get("customerInvestmentDocument")
    stored_document = stored_document if isinstance(stored_document, dict) else {}
    stored_content = context.get("notificationContent")
    stored_content = stored_content if isinstance(stored_content, dict) else {}
    investment_summary = {
        "headline": str(stored_document.get("headline") or ""),
        "reason": str(stored_document.get("lead") or stored_content.get("summary") or ""),
    }
    payload = {
        "jobId": job.job_id,
        "messageType": job.message_type,
        "messageTypeLabel": presentation["label"],
        "messageTypeIcon": presentation["icon"],
        "notificationKind": presentation["kind"],
        "notificationKindLabel": presentation["label"],
        "notificationKindIcon": presentation["icon"],
        "status": job.status,
        "accountId": job.account_id,
        "accountLabel": job.account_label,
        "decisionEpisodeId": decision_episode_id,
        "decisionKey": decision_key,
        "createdAt": job.created_at,
        "updatedAt": job.updated_at,
        "sourceEventId": job.source_event_id,
        "sourceEventName": job.source_event_name,
        "title": notification_heading(job.message_type, context),
        "symbol": symbol,
        "rawSymbol": str(context.get("rawSymbol") or context.get("symbol") or "").strip(),
        "symbolName": str(context.get("symbolDisplayName") or context.get("displaySymbolName") or "").strip(),
        "textPreview": compact_notification_text(customer_text),
        "investmentSummary": investment_summary,
        "lastError": job.last_error,
        "suppressionSummary": notification_suppression_summary(job),
        "nextEligibleAt": notification_next_eligible_at(context),
        "processingAgeMinutes": round(processing_age, 1),
        "recoverableProcessing": recoverable_processing,
        "priorityQueueEligible": priority_queue_eligible,
        "priorityQueueState": priority_queue_state,
        "priorityQueueWindowMinutes": active_failure_window_minutes,
        "deliveryDecision": context.get("deliveryDecision") or ("send" if job.status in {"pending", "processing", "done"} else job.status),
        "apiSource": api_source,
        "dataQuality": data_quality,
        "isMock": is_mock,
        "deliveryGateState": context.get("deliveryGateState") or "",
        "deliveryGateReason": context.get("deliveryGateReason") or "",
        "deliveryReasons": [str(item) for item in reasons],
        "deliveryTriggerLedgerVersion": context.get("deliveryTriggerLedgerVersion") or "",
        # This bounded, customer-safe contract drives the summary screen too.
        # Raw trigger provenance remains restricted to the detailed projection.
        "customerDeliveryExplanation": delivery_explanation,
        "customerDeliveryExplanationVersion": delivery_explanation.get("version") or "",
        "customerDeliveryExplanationValidationState": str((
            (delivery_explanation.get("validation") or {}).get("state")
            if isinstance(delivery_explanation.get("validation"), dict)
            else context.get("customerDeliveryExplanationValidationState")
        ) or ""),
        "deliveryTriggerLedger": [
            dict(item) for item in trigger_ledger if isinstance(item, dict)
        ] if detail else [],
        "customerDeliveryTriggers": [
            dict(item)
            for item in trigger_ledger
            if isinstance(item, dict) and item.get("customerVisible") is True
        ] if detail else [],
        "internalDeliveryChecks": [
            dict(item)
            for item in trigger_ledger
            if isinstance(item, dict) and item.get("customerVisible") is not True
        ] if detail else [],
        "deliveryFingerprint": context.get("deliveryFingerprint") or "",
        "deliveryReviewLevel": context.get("deliveryReviewLevel") or "",
        "deliveryDataState": context.get("deliveryDataState") or "",
        "deliveryChangeState": context.get("deliveryChangeState") or "",
        "deliveryConflictState": context.get("deliveryConflictState") or "",
        "deliveryValidationState": context.get("deliveryValidationState") or "",
        "repeatRecentCount": context.get("repeatRecentCount"),
        "repeatWindowMinutes": context.get("repeatWindowMinutes"),
        "repeatBypassed": bool(context.get("repeatBypassed")),
        "repeatBypassReason": context.get("repeatBypassReason") or "",
        "deliverySuppressionReason": context.get("deliverySuppressionReason") or "",
        "investmentNotificationState": dict(context.get("investmentNotificationState") or {}) if isinstance(context.get("investmentNotificationState"), dict) else {},
        "investmentNotificationTransition": dict(context.get("investmentNotificationTransition") or {}) if isinstance(context.get("investmentNotificationTransition"), dict) else {},
        "freshDataRecheck": dict(context.get("freshDataRecheck") or {}) if isinstance(context.get("freshDataRecheck"), dict) else {},
        "cooldownEnabled": bool(context.get("cooldownEnabled")),
        "cooldownMinutes": context.get("cooldownMinutes"),
        "cooldownRecentSentCount": context.get("cooldownRecentSentCount"),
        "cooldownLastSentAt": context.get("cooldownLastSentAt") or "",
        "cooldownLastSentAgeMinutes": context.get("cooldownLastSentAgeMinutes"),
        "cooldownDecision": context.get("cooldownDecision") or "",
        "cooldownReason": context.get("cooldownReason") or "",
        "cooldownSuppressed": bool(context.get("cooldownSuppressed")),
        "marketHoursEnabled": bool(context.get("marketHoursEnabled")),
        "marketHoursMarket": context.get("marketHoursMarket") or "",
        "marketHoursLabel": context.get("marketHoursLabel") or "",
        "marketHoursStatus": context.get("marketHoursStatus") or "",
        "marketHoursDecision": context.get("marketHoursDecision") or "",
        "marketHoursReason": context.get("marketHoursReason") or "",
        "marketHoursLocalTime": context.get("marketHoursLocalTime") or "",
        "marketHoursOpenTime": context.get("marketHoursOpenTime") or "",
        "marketHoursCloseTime": context.get("marketHoursCloseTime") or "",
        "marketHoursTimezone": context.get("marketHoursTimezone") or "",
        "offHoursDeliveryMode": context.get("offHoursDeliveryMode") or "",
        "quietHoursSuppressed": bool(context.get("quietHoursSuppressed")),
        "quietHoursReason": context.get("quietHoursReason") or "",
        "quietHoursStart": context.get("quietHoursStart") or "",
        "quietHoursEnd": context.get("quietHoursEnd") or "",
        "quietHoursTimezone": context.get("quietHoursTimezone") or "",
    }
    if detail or include_customer_document:
        payload["customerInvestmentDocument"] = customer_document
        payload["customerInvestmentDocumentQuality"] = customer_document_quality
    if detail:
        configured_settings = configured_settings or operational_read_settings()
        payload["fullText"] = full_notification_text(customer_text)
        payload["actionFlow"] = notification_action_flow(context)
        # The trace is rebuilt from the immutable context captured with this
        # job, never from the currently active graph generation.
        payload["reasoningTrace"] = build_notification_reverse_reasoning_trace(
            context,
            job_id=job.job_id,
            job_status=job.status,
        )
        try:
            relation = context.get("ontologyRelationContext")
            relation = dict(relation or {}) if isinstance(relation, dict) else {}
            generation_id = str(relation.get("inferenceGenerationId") or "").strip()
            if generation_id:
                execution_store = stores.ontology_projection_run_store(configured_settings)
                payload["reasoningTrace"]["executionLedger"] = (
                    execution_store.execution_trace_for_inference_generation(
                        generation_id,
                        account_id=job.account_id,
                    )
                )
        except Exception as error:  # noqa: BLE001 - saved notification trace remains available.
            payload["reasoningTrace"]["executionLedger"] = {
                "status": "error",
                "reason": str(error)[:220],
                "runCount": 0,
                "runs": [],
            }
        try:
            episode = context.get("investmentDecisionEpisode") if isinstance(context.get("investmentDecisionEpisode"), dict) else {}
            episode_id = str(
                context.get("investmentDecisionEpisodeId")
                or episode.get("episodeId")
                or ""
            ).strip()
            lifecycle = stores.investment_domain_store(configured_settings).lifecycle_trace(episode_id)
            payload["investmentLifecycle"] = lifecycle
            payload["reasoningTrace"]["investmentLifecycle"] = lifecycle
        except Exception as error:  # noqa: BLE001 - the immutable reasoning trace remains usable.
            payload["investmentLifecycle"] = {
                "status": "error",
                "reason": str(error)[:220],
            }
            payload["reasoningTrace"]["investmentLifecycle"] = payload["investmentLifecycle"]
    return payload


def notification_job_list_payload(
    job: NotificationJob,
    stale_minutes: int,
    settings: Dict[str, object] = None,
) -> Dict[str, object]:
    """Expose only the fields needed to render an outbox ledger row.

    The message body and policy audit trail remain available from the existing
    job-detail endpoint. This keeps a 20-row ledger from carrying 20 copies of
    full notification text and cooldown metadata.
    """
    payload = notification_job_public_payload(
        job,
        detail=False,
        stale_minutes=stale_minutes,
        settings=settings,
    )
    if not payload.get("title"):
        headline = full_notification_text(job.text).split("\n", 1)[0].strip()
        payload["title"] = compact_notification_text(job.source_event_name or headline, 120)
    fields = {
        "jobId", "messageType", "messageTypeLabel", "messageTypeIcon", "status",
        "notificationKind", "notificationKindLabel", "notificationKindIcon",
        "accountId", "accountLabel", "decisionEpisodeId", "decisionKey",
        "createdAt", "updatedAt", "sourceEventName", "title", "symbol", "rawSymbol",
        "symbolName", "textPreview", "investmentSummary", "lastError", "suppressionSummary", "nextEligibleAt",
        "processingAgeMinutes", "recoverableProcessing", "deliveryDecision",
        "priorityQueueEligible", "priorityQueueState", "priorityQueueWindowMinutes",
        "apiSource", "dataQuality", "isMock",
    }
    return {key: value for key, value in payload.items() if key in fields}


def encode_notification_cursor(job: NotificationJob) -> str:
    raw = json.dumps({"updatedAt": job.updated_at or job.created_at, "jobId": job.job_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_notification_cursor(value: str) -> Dict[str, str]:
    cursor = str(value or "").strip()
    if not cursor:
        return {"updatedAt": "", "jobId": ""}
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return {
            "updatedAt": str(payload.get("updatedAt") or "")[:40],
            "jobId": str(payload.get("jobId") or "")[:191],
        }
    except (ValueError, TypeError, json.JSONDecodeError):
        return {"updatedAt": "", "jobId": ""}
