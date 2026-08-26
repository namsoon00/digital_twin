"""Notification lifecycle and end-to-end lineage read model for web diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Mapping

from ...domain.ontology_decision_quality import build_ontology_decision_quality_snapshot


PIPELINE_TRACE_VERSION = "notification-pipeline-trace-v2"
SENSITIVE_KEY_PARTS = (
    "apikey",
    "api_key",
    "secret",
    "password",
    "credential",
    "authorization",
    "accesstoken",
    "access_token",
    "refreshtoken",
    "refresh_token",
    "rawresponse",
    "raw_response",
)


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _safe(value: object, key: str = "") -> object:
    lowered = str(key or "").replace("-", "").replace("_", "").casefold()
    if key and any(part.replace("_", "") in lowered for part in SENSITIVE_KEY_PARTS):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _safe(item_value, str(item_key))
            for item_key, item_value in value.items()
            if not any(
                part.replace("_", "") in str(item_key or "").replace("-", "").replace("_", "").casefold()
                for part in SENSITIVE_KEY_PARTS
            )
        }
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return value


def redact_notification_trace_data(value: object) -> object:
    """Return the share-safe form used by notification trace projections."""

    return _safe(value)


def _timestamp(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _duration_ms(started_at: object, completed_at: object, fallback: object = 0) -> int:
    started = _timestamp(started_at)
    completed = _timestamp(completed_at)
    if started and completed:
        return max(0, int((completed - started).total_seconds() * 1000))
    try:
        return max(0, int(fallback or 0))
    except (TypeError, ValueError):
        return 0


def _first_at(rows, stages, field="createdAt") -> str:
    candidates = [
        str(item.get(field) or "")
        for item in rows or []
        if str(item.get("stage") or "") in set(stages) and str(item.get(field) or "")
    ]
    return min(candidates) if candidates else ""


def _last_at(rows, stages, field="createdAt") -> str:
    candidates = [
        str(item.get(field) or "")
        for item in rows or []
        if str(item.get("stage") or "") in set(stages) and str(item.get(field) or "")
    ]
    return max(candidates) if candidates else ""


def _history_at(history, stage: str) -> str:
    return next(
        (
            str(item.get("at") or "")
            for item in history or []
            if str(item.get("stage") or "").upper() == str(stage or "").upper()
        ),
        "",
    )


def _stage(
    key: str,
    title: str,
    status: str,
    summary: str,
    *,
    started_at: str = "",
    completed_at: str = "",
    duration_ms: int = 0,
    identifiers: Mapping[str, object] = None,
    details: Mapping[str, object] = None,
) -> Dict[str, object]:
    return {
        "key": key,
        "title": title,
        "status": status,
        "summary": str(summary or ""),
        "startedAt": str(started_at or ""),
        "completedAt": str(completed_at or ""),
        "durationMs": _duration_ms(started_at, completed_at, duration_ms),
        "identifiers": _safe(dict(identifiers or {})),
        "details": _safe(dict(details or {})),
    }


def _pipeline_status(stages: List[Dict[str, object]]) -> str:
    states = {str(item.get("status") or "") for item in stages}
    if states.intersection({"failed", "blocked"}):
        return "blocked"
    if states.intersection({"missing", "conditional", "in-progress"}):
        return "partial"
    return "complete"


class NotificationTraceQueryService:
    def __init__(self, store):
        self.store = store

    def trace_for_job(
        self,
        job_or_id,
        *,
        reasoning_trace: Mapping[str, object] = None,
        source_event: Mapping[str, object] = None,
        reasoning_case: Mapping[str, object] = None,
        ai_trace: Mapping[str, object] = None,
        rendered_message: str = "",
        include_stage_details: bool = True,
    ) -> Dict[str, object]:
        job = job_or_id if hasattr(job_or_id, "job_id") else None
        job_id = str(getattr(job, "job_id", "") or job_or_id or "")
        context = _mapping(getattr(job, "context", {}))
        lifecycle = (
            list(self.store.lifecycle_for_job(job_id) or [])
            if hasattr(self.store, "lifecycle_for_job")
            else []
        )
        attempts = (
            list(self.store.delivery_attempts_for_job(job_id) or [])
            if hasattr(self.store, "delivery_attempts_for_job")
            else []
        )
        timeline: List[Dict[str, object]] = []
        for event in lifecycle:
            timeline.append({
                "kind": "lifecycle",
                "id": str(event.get("eventId") or ""),
                "at": str(event.get("createdAt") or ""),
                "stage": str(event.get("stage") or ""),
                "outcome": str(event.get("outcome") or ""),
                "reason": str(event.get("reason") or ""),
                "metadata": _safe(dict(event.get("metadata") or {})),
            })
        for attempt in attempts:
            timeline.append({
                "kind": "deliveryAttemptStarted",
                "id": str(attempt.get("attemptId") or "") + ":started",
                "at": str(attempt.get("startedAt") or ""),
                "stage": "dispatching",
                "outcome": "started",
                "reason": "",
                "metadata": _safe({
                    "channel": str(attempt.get("channel") or ""),
                    "audience": str(attempt.get("audience") or ""),
                    **dict(attempt.get("metadata") or {}),
                }),
            })
            if str(attempt.get("completedAt") or ""):
                timeline.append({
                    "kind": "deliveryAttemptCompleted",
                    "id": str(attempt.get("attemptId") or "") + ":completed",
                    "at": str(attempt.get("completedAt") or ""),
                    "stage": "delivery_result",
                    "outcome": str(attempt.get("status") or ""),
                    "reason": str(attempt.get("reason") or ""),
                    "metadata": _safe({
                        "channel": str(attempt.get("channel") or ""),
                        "audience": str(attempt.get("audience") or ""),
                        "provider": str(attempt.get("provider") or ""),
                        **dict(attempt.get("metadata") or {}),
                    }),
                })
        timeline.sort(key=lambda item: (item.get("at") or "", item.get("id") or ""))
        for sequence, item in enumerate(timeline, start=1):
            item["sequence"] = sequence

        reasoning = _mapping(reasoning_trace)
        relation = _mapping(context.get("ontologyRelationContext"))
        snapshot = _mapping(reasoning.get("snapshot"))
        quality = (
            _mapping(context.get("ontologyDecisionQuality"))
            or _mapping(_mapping(reasoning.get("aiExecution")).get("ontologyDecisionQuality"))
            or build_ontology_decision_quality_snapshot(context)
        )
        quality_gate = (
            _mapping(context.get("ontologyQualityGate"))
            or _mapping(_mapping(reasoning.get("aiExecution")).get("ontologyQualityGate"))
        )
        synthesis = _mapping(context.get("v2DecisionSynthesis"))
        case = _mapping(reasoning_case) or _mapping(context.get("investmentReasoningCase"))
        case_history = [dict(item) for item in case.get("stageHistory") or [] if isinstance(item, Mapping)]
        ai_execution = _mapping(reasoning.get("aiExecution")) or _mapping(
            context.get("notificationAiExecutionAudit")
        )
        queue_state = _mapping(context.get("notificationAiQueue"))
        ai_runtime = _mapping(ai_trace)
        packet = _mapping(ai_execution.get("inferencePacket")) or _mapping(
            context.get("_notificationAiInferencePacket")
        )
        validated_response = (
            _mapping(context.get("notificationAiValidatedResponse"))
            or _mapping(context.get("validatedDecisionResponse"))
        )
        narrative = _mapping(reasoning.get("narrative"))
        presentation = _mapping(context.get("notificationPresentationAudit"))
        delivery_explanation = _mapping(context.get("customerDeliveryExplanation"))
        delivery_explanation_validation = _mapping(delivery_explanation.get("validation"))
        event = _mapping(source_event)

        event_id = str(
            getattr(job, "source_event_id", "")
            or event.get("eventId")
            or event.get("event_id")
            or ""
        )
        event_name = str(
            getattr(job, "source_event_name", "")
            or event.get("name")
            or ""
        )
        event_at = str(
            event.get("occurredAt")
            or event.get("occurred_at")
            or context.get("eventGeneratedAt")
            or getattr(job, "created_at", "")
            or ""
        )
        inference_started = _history_at(case_history, "INPUT_READY") or str(snapshot.get("generatedAt") or "")
        inference_completed = (
            _history_at(case_history, "DECISION_SYNTHESIZED")
            or _history_at(case_history, "HYPOTHESES_READY")
            or str(snapshot.get("inferenceGenerationAt") or snapshot.get("generatedAt") or "")
        )
        quality_at = str(quality.get("generatedAt") or inference_completed)
        ai_started = str(ai_runtime.get("startedAt") or ai_runtime.get("started_at") or "")
        ai_completed = str(
            ai_runtime.get("completedAt")
            or ai_runtime.get("completed_at")
            or queue_state.get("completedAt")
            or ""
        )
        ai_queued = str(
            ai_runtime.get("createdAt")
            or ai_runtime.get("created_at")
            or queue_state.get("queuedAt")
            or _history_at(case_history, "AI_PENDING")
            or ""
        )
        rendered_at = _last_at(lifecycle, {"rendered", "ready_to_render"})
        delivery_reason_at = _last_at(lifecycle, {"delivery_reason_validated"})
        delivery_started = min(
            [str(item.get("startedAt") or "") for item in attempts if str(item.get("startedAt") or "")],
            default="",
        )
        delivery_completed = max(
            [str(item.get("completedAt") or "") for item in attempts if str(item.get("completedAt") or "")],
            default=_last_at(lifecycle, {"delivered", "failed", "suppressed"}),
        )

        quality_status = str(quality.get("status") or quality_gate.get("validationState") or "missing")
        ai_status = str(ai_runtime.get("status") or ai_execution.get("status") or queue_state.get("status") or "missing")
        ai_review_mode = str(
            context.get("notificationAiReviewMode")
            or ai_execution.get("reviewMode")
            or "investment-judgement"
        )
        claim_publication = _mapping(ai_execution.get("claimPublication"))
        claim_status = str(
            claim_publication.get("status")
            or _mapping(narrative.get("claimValidation")).get("status")
            or "missing"
        )
        case_stage = str(case.get("stage") or "")
        def stage_details(value: Mapping[str, object]) -> Dict[str, object]:
            return dict(value or {}) if include_stage_details else {}

        stages = [
            _stage(
                "source-event",
                "원천 이벤트 수신",
                "completed" if event_id else "missing",
                (event_name or "원천 이벤트") + (" · 저장 완료" if event_id else " · 식별자 미기록"),
                started_at=event_at,
                completed_at=event_at,
                identifiers={"sourceEventId": event_id, "correlationId": event.get("correlationId") or event.get("correlation_id")},
                details=stage_details({"event": event, "notificationSourceTrace": context.get("notificationSourceTrace") or {}}),
            ),
            _stage(
                "v2-reasoning",
                "V2 TypeDB 추론",
                "failed" if case_stage in {"FAILED", "BLOCKED"} else "completed" if synthesis or snapshot.get("inferenceGenerationId") else "missing",
                "동일 ABox 세대에서 규칙 실행, 관계 생성, 가설 구성을 수행했습니다.",
                started_at=inference_started,
                completed_at=inference_completed,
                duration_ms=_mapping(case.get("inferenceResult")).get("duration_ms") or _mapping(case.get("inferenceResult")).get("durationMs") or 0,
                identifiers={
                    "reasoningCaseId": case.get("caseId") or context.get("investmentReasoningCaseId"),
                    "requestId": case.get("requestId"),
                    "sourceAboxSnapshotId": quality.get("sourceAboxSnapshotId") or snapshot.get("sourceAboxSnapshotId"),
                    "inferenceGenerationId": quality.get("inferenceGenerationId") or snapshot.get("inferenceGenerationId"),
                    "deploymentId": quality.get("deploymentId") or case.get("deploymentId"),
                },
                details=stage_details({
                    "reasoningCase": case,
                    "snapshot": snapshot,
                    "executionLedger": reasoning.get("executionLedger") or {},
                    "inputFacts": reasoning.get("inputFacts") or [],
                    "matchedRules": reasoning.get("matchedRules") or [],
                    "inferenceTraces": reasoning.get("inferenceTraces") or [],
                    "hypotheses": reasoning.get("hypotheses") or [],
                }),
            ),
            _stage(
                "ontology-quality",
                "온톨로지 품질 검증",
                "completed" if quality_status == "ready" else "blocked" if quality_status == "blocked" else "conditional" if quality else "missing",
                str(quality.get("pipelineValidation") or quality_gate.get("reason") or "품질 스냅샷이 없습니다."),
                started_at=quality_at,
                completed_at=quality_at,
                identifiers={"qualitySampleId": quality.get("qualitySampleId"), "fingerprint": quality.get("fingerprint")},
                details=stage_details({"qualitySnapshot": quality, "qualityGate": quality_gate}),
            ),
            _stage(
                "decision-synthesis",
                "DecisionSynthesis 생성",
                "blocked" if synthesis.get("judgement_blocked") or synthesis.get("judgementBlocked") else "completed" if synthesis else "missing",
                (
                    "사실 " + str(synthesis.get("evidence_state") or synthesis.get("evidenceState") or "미기록")
                    + " · 가설 " + str(synthesis.get("hypothesis_state") or synthesis.get("hypothesisState") or "미기록")
                    + " · 행동 " + str(synthesis.get("action_state") or synthesis.get("actionState") or "미기록")
                    + " · AI " + str(synthesis.get("ai_state") or synthesis.get("aiState") or "미기록")
                ) if synthesis else "DecisionSynthesis가 생성되지 않았습니다.",
                started_at=inference_completed,
                completed_at=inference_completed,
                identifiers={
                    "synthesisId": synthesis.get("synthesis_id") or synthesis.get("synthesisId"),
                    "selectedRuleId": synthesis.get("selected_rule_id") or synthesis.get("selectedRuleId"),
                    "evidenceState": synthesis.get("evidence_state") or synthesis.get("evidenceState"),
                    "hypothesisState": synthesis.get("hypothesis_state") or synthesis.get("hypothesisState"),
                    "actionState": synthesis.get("action_state") or synthesis.get("actionState"),
                    "aiState": synthesis.get("ai_state") or synthesis.get("aiState"),
                },
                details=stage_details({
                    "decisionSynthesis": synthesis,
                    "finalDecision": reasoning.get("finalDecision") or {},
                    "aiComparison": reasoning.get("aiComparison") or {},
                    "assessmentBundle": reasoning.get("assessmentBundle") or {},
                    "decisionAssurance": validated_response.get("decisionAssurance") or {},
                }),
            ),
            _stage(
                "ai-packet",
                "AI 입력 패킷 고정",
                "completed" if packet.get("packetId") else "missing",
                "프롬프트, 근거 원장, 가설과 결정 계약을 변경 불가능한 패킷으로 묶었습니다.",
                started_at=ai_queued,
                completed_at=ai_queued,
                identifiers={
                    "aiRequestId": ai_execution.get("requestId") or queue_state.get("requestId"),
                    "packetId": packet.get("packetId"),
                    "promptHash": packet.get("promptHash") or ai_execution.get("promptHash"),
                    "evidenceFingerprint": packet.get("evidenceFingerprint"),
                },
                details=stage_details({
                    "inferencePacket": packet,
                    "decisionCore": ai_execution.get("decisionCore") or {},
                    "decisionBrief": ai_execution.get("decisionBrief") or {},
                    "contextRouting": ai_execution.get("contextRouting") or {},
                    "promptRelease": ai_execution.get("promptRelease") or {},
                    "prompt": ai_execution.get("prompt") or "",
                }),
            ),
            _stage(
                "ai-response",
                "AI 참고 서술 응답" if ai_review_mode == "context-narrative" else "AI 판단 응답",
                "failed" if ai_status == "failed" else "conditional" if ai_status == "typedb-fallback" else "completed" if validated_response else "in-progress" if ai_status in {"pending", "processing", "awaiting-ai", "retry"} else "missing",
                (
                    "AI가 TypeDB 행동을 바꾸지 않고 참고 설명만 작성했습니다."
                    if ai_review_mode == "context-narrative"
                    else "AI 행동 판단과 경쟁 가설 비교 결과를 검증 가능한 응답 계약으로 받았습니다."
                ),
                started_at=ai_started or ai_queued,
                completed_at=ai_completed,
                duration_ms=ai_execution.get("latencyMs") or ai_runtime.get("latencyMs") or 0,
                identifiers={
                    "aiRequestId": ai_execution.get("requestId") or ai_runtime.get("requestId"),
                    "resultId": ai_runtime.get("resultId") or queue_state.get("resultId"),
                    "model": ai_execution.get("model") or ai_runtime.get("model"),
                },
                details=stage_details({"runtime": ai_runtime, "executionAudit": ai_execution, "validatedResponse": validated_response}),
            ),
            _stage(
                "claim-validation",
                "문장별 근거 검증",
                "completed" if claim_status == "verified" else "conditional" if claim_status == "partial" else "missing",
                "AI 문장마다 패킷 근거 ID, 역할, 수치 일치 여부를 검사했습니다.",
                started_at=ai_completed,
                completed_at=ai_completed,
                identifiers={
                    "inferencePacketId": _mapping(narrative.get("claimValidation")).get("inferencePacketId"),
                    "evidenceFingerprint": _mapping(narrative.get("claimValidation")).get("evidenceFingerprint"),
                },
                details=stage_details({"claimPublication": claim_publication, "narrative": narrative}),
            ),
            _stage(
                "delivery-explanation",
                "사용자 발송 사유 검증",
                "completed" if delivery_explanation_validation.get("state") == "valid" else "failed" if delivery_explanation_validation.get("state") == "invalid" else "missing",
                "원천 이벤트와 최종 판단 전이를 대조해 왜 지금 발송하는지 한 가지 주 사유로 확정했습니다.",
                started_at=delivery_reason_at,
                completed_at=delivery_reason_at,
                identifiers={
                    "contractVersion": delivery_explanation.get("version"),
                    "purpose": delivery_explanation.get("purpose"),
                    "primaryCause": _mapping(delivery_explanation.get("primaryCause")).get("category"),
                },
                details=stage_details({"customerDeliveryExplanation": delivery_explanation}),
            ),
            _stage(
                "rendering",
                "최종 메시지 렌더링",
                "completed" if rendered_at or rendered_message else "missing",
                "검증을 통과한 판단과 문장만 채널용 최종 메시지로 렌더링했습니다.",
                started_at=_first_at(lifecycle, {"ready_to_render", "rendered"}),
                completed_at=rendered_at,
                identifiers={"narrativeFingerprint": narrative.get("fingerprint") or presentation.get("narrativeFingerprint")},
                details=stage_details({"writerProvenance": narrative.get("writerProvenance") or {}, "presentationAudit": presentation, "renderedMessage": rendered_message}),
            ),
            _stage(
                "delivery",
                "알림 전달",
                "completed" if any(str(item.get("status") or "") == "delivered" for item in attempts) else "failed" if attempts and delivery_completed else "in-progress" if attempts else "missing",
                "발송 정책을 통과한 메시지를 채널 공급자에 전달하고 결과를 저장했습니다.",
                started_at=delivery_started,
                completed_at=delivery_completed,
                identifiers={"attemptIds": [item.get("attemptId") for item in attempts]},
                details=stage_details({"lifecycle": lifecycle, "deliveryAttempts": attempts}),
            ),
        ]
        for sequence, item in enumerate(stages, start=1):
            item["sequence"] = sequence
        bottleneck = max(stages, key=lambda item: int(item.get("durationMs") or 0), default={})
        pipeline = {
            "contractVersion": PIPELINE_TRACE_VERSION,
            "status": _pipeline_status(stages),
            "complete": all(item.get("status") == "completed" for item in stages),
            "stageCount": len(stages),
            "stages": stages,
            "bottleneck": {
                "stageKey": str(bottleneck.get("key") or ""),
                "title": str(bottleneck.get("title") or ""),
                "durationMs": int(bottleneck.get("durationMs") or 0),
            },
            "links": {
                "notificationJobId": job_id,
                "sourceEventId": event_id,
                "reasoningCaseId": str(case.get("caseId") or context.get("investmentReasoningCaseId") or ""),
                "sourceAboxSnapshotId": str(quality.get("sourceAboxSnapshotId") or snapshot.get("sourceAboxSnapshotId") or ""),
                "inferenceGenerationId": str(quality.get("inferenceGenerationId") or snapshot.get("inferenceGenerationId") or ""),
                "synthesisId": str(synthesis.get("synthesis_id") or synthesis.get("synthesisId") or ""),
                "aiRequestId": str(ai_execution.get("requestId") or queue_state.get("requestId") or ""),
                "inferencePacketId": str(packet.get("packetId") or ""),
            },
        }
        return {
            "contractVersion": "notification-trace-v2",
            "jobId": job_id,
            "lifecycle": _safe(lifecycle),
            "deliveryAttempts": _safe(attempts),
            "timeline": timeline,
            "pipeline": pipeline,
        }
