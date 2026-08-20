"""Application services for deferred, validated notification AI inference."""

from __future__ import annotations

import json
import threading
import time
import uuid
import hashlib
from datetime import datetime, timezone
from typing import Dict

from ..domain.ai_inference_queue import AIInferenceRequest, AIInferenceResult
from ..domain.investment_brain import decision_episode_from_context
from ..domain.message_types import INVESTMENT_INSIGHT
from ..domain.notification_ai_decision_brief import (
    AI_DECISION_CONTRACT_VERSION,
    AI_DECISION_PROMPT_VERSION,
    build_notification_ai_prompt_bundle,
    notification_ai_decision_brief,
    notification_ai_execution_profile,
)
from ..domain.notification_ai_gate_validation import (
    local_validated_ai_response,
)
from ..domain.notifications import NotificationJob
from .notification_ai_gate_audit import context_with_validated_ai_response
from .notification_decision_memory import context_with_previous_investment_decision
from .notification.quality import (
    apply_ontology_quality_gate_to_response,
    ontology_quality_gate_context,
)


def _int_setting(settings: Dict[str, object], key: str, fallback: int, minimum: int, maximum: int) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(minimum, min(maximum, value))


def _bool_setting(settings: Dict[str, object], key: str, fallback: bool = False) -> bool:
    value = (settings or {}).get(key)
    if value is None:
        return bool(fallback)
    return str(value).strip().lower() not in {"", "0", "false", "no", "off", "disabled"}


def _timestamp(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def typedb_inference_fallback_response(context: Dict[str, object], reason: object):
    """Build a customer-safe TypeDB-only alert when the optional AI stage fails."""

    response = local_validated_ai_response(
        context,
        source="TypeDB inference fallback",
    )
    detail = str(reason or "AI judgment unavailable").strip()
    lowered = detail.lower()
    if "timeout" in lowered or "exceeded" in lowered or "deadline" in lowered:
        notice = (
            "AI 판단을 설정한 시간 안에 완료하지 못해 검증된 TypeDB 관계 추론만 먼저 전달합니다. "
            "아래 내용은 AI 최종 의견이 아니라 현재 추론 결과와 확인 조건입니다."
        )
    elif "hypothesis" in lowered or "contract" in lowered or "action envelope" in lowered:
        notice = (
            "AI 응답이 TypeDB 가설·행동 계약을 통과하지 못해 검증된 TypeDB 관계 추론만 먼저 전달합니다. "
            "검증되지 않은 AI 의견은 알림에서 제외했습니다."
        )
    else:
        notice = (
            "AI 판단을 사용할 수 없어 검증된 TypeDB 관계 추론만 먼저 전달합니다. "
            "아래 내용에는 AI 최종 의견이 포함되지 않았습니다."
        )
    response.summary = notice
    response.investment_view = notice
    response.source = "TypeDB inference fallback"
    response.selected_hypothesis_id = ""
    response.hypothesis_selection_source = "typedb-fallback-no-ai-selection"
    response.validation_warnings.append("AI stage fallback: " + detail[:320])
    return response


def hypothesis_comparison_needs_repair(
    message_type: object,
    response,
) -> bool:
    return bool(
        str(message_type or "") == INVESTMENT_INSIGHT
        and getattr(response, "hypotheses", None)
        and str(getattr(response, "hypothesis_comparison_state", "") or "") != "completed"
    )


def ai_response_contract_error(context: Dict[str, object], response) -> str:
    """Preflight the AI selection against the compact TypeDB decision contract."""

    prepared_core = context.get("_notificationAiPreparedDecisionCore")
    if isinstance(prepared_core, dict):
        hypothesis_set = prepared_core.get("hypothesisSet")
        hypothesis_set = hypothesis_set if isinstance(hypothesis_set, dict) else {}
        hypothesis_ids = {
            str(item.get("hypothesisId") or "").strip()
            for item in hypothesis_set.get("hypotheses") or []
            if isinstance(item, dict) and str(item.get("hypothesisId") or "").strip()
        }
        selected_id = str(getattr(response, "selected_hypothesis_id", "") or "")
        if hypothesis_ids and selected_id not in hypothesis_ids:
            return "selectedHypothesisId is not present in the routed TypeDB hypothesis set."
        decision = prepared_core.get("decision")
        decision = decision if isinstance(decision, dict) else {}
        envelope = decision.get("actionEnvelope")
        envelope = envelope if isinstance(envelope, dict) else {}
        action = str(getattr(response, "action", "") or "").upper()
        blocked_actions = {
            str(value or "").upper()
            for value in envelope.get("blockedActions") or []
            if str(value or "")
        }
        if action in blocked_actions:
            return "The selected action is blocked by the routed TypeDB action envelope."
        allowed_actions = {
            str(value or "").upper()
            for value in envelope.get("allowedActions") or []
            if str(value or "")
        }
        if allowed_actions and action not in allowed_actions:
            return "The selected action is outside the routed TypeDB action envelope."
        if hypothesis_ids and (allowed_actions or blocked_actions):
            return ""

    reasoning_case = context.get("investmentReasoningCase")
    if not isinstance(reasoning_case, dict) or not reasoning_case:
        return ""
    selected_id = str(getattr(response, "selected_hypothesis_id", "") or "")
    hypothesis_ids = {
        str(value or "") for value in reasoning_case.get("hypothesisIds") or [] if str(value or "")
    }
    if hypothesis_ids and selected_id not in hypothesis_ids:
        return "selectedHypothesisId is not present in the TypeDB hypothesis set."
    syntheses = [
        dict(value) for value in reasoning_case.get("decisionSyntheses") or [] if isinstance(value, dict)
    ]
    if not syntheses:
        return ""
    eligible_ids = {
        str(value or "")
        for synthesis in syntheses
        for value in synthesis.get("eligibleHypothesisIds") or []
        if str(value or "")
    }
    if selected_id not in eligible_ids:
        return "selectedHypothesisId is reference-only in the TypeDB decision synthesis."
    action = str(getattr(response, "action", "") or "").upper()
    blocked_actions = {
        str(value or "").upper()
        for synthesis in syntheses
        for value in synthesis.get("blockedActions") or []
        if str(value or "")
    }
    if action in blocked_actions:
        return "The selected action is blocked by the TypeDB action envelope."
    applicable = [
        synthesis for synthesis in syntheses
        if selected_id in {
            str(value or "") for value in synthesis.get("eligibleHypothesisIds") or []
        }
    ]
    allowed_actions = {
        str(value or "").upper()
        for synthesis in applicable
        for value in synthesis.get("allowedActions") or []
        if str(value or "")
    }
    if allowed_actions and action not in allowed_actions:
        return "The selected action is outside the TypeDB action envelope."
    return ""


def hypothesis_comparison_repair_prompt(
    prompt: str,
    response,
    contract_error: str = "",
) -> str:
    abstention = dict(getattr(response, "decision_abstention", {}) or {})
    audit = {
        "reason": abstention.get("reason") or "가설 비교 계약 미충족",
        "unreviewedHypothesisIds": abstention.get("unreviewedHypothesisIds") or [],
        "invalidHypothesisIds": abstention.get("invalidHypothesisIds") or [],
        "invalidEvidenceIds": abstention.get("invalidEvidenceIds") or [],
        "duplicateHypothesisIds": abstention.get("duplicateHypothesisIds") or [],
        "decisionContractError": str(contract_error or ""),
    }
    return (
        str(prompt or "")
        + "\n\n이전 응답은 아래 가설 비교 검증 오류로 거부됐다. 새로운 JSON 객체 하나만 다시 출력한다. "
        + "입력 hypothesisSet.hypotheses의 각 hypothesisId를 정확히 한 번씩 사용하고, "
        + "각 근거 ID는 해당 입력 가설에 실제 연결된 ID만 사용하며, selectedHypothesisId는 입력 규칙 가설 중 하나여야 한다.\n"
        + json.dumps(audit, ensure_ascii=False, sort_keys=True)
    )


class NotificationAIRequestEnqueuer:
    """Capture one immutable AI context and move its notification to waiting."""

    def __init__(
        self,
        queue,
        context_preparer=None,
        settings: Dict[str, object] = None,
        decision_episode_store=None,
        continuity_service=None,
        reasoning_orchestrator=None,
    ):
        self.queue = queue
        self.context_preparer = context_preparer
        self.settings = dict(settings or {})
        self.decision_episode_store = decision_episode_store
        self.continuity_service = continuity_service
        self.reasoning_orchestrator = reasoning_orchestrator

    def enqueue(self, job: NotificationJob) -> Dict[str, object]:
        if self.context_preparer:
            self.context_preparer(job)
        context = dict(job.context or {})
        context.setdefault("messageType", job.message_type)
        context.setdefault("accountId", job.account_id)
        context.setdefault("accountLabel", job.account_label)
        context.setdefault("jobId", job.job_id)
        context["notificationAiDecisionContractVersion"] = AI_DECISION_CONTRACT_VERSION
        if job.message_type == INVESTMENT_INSIGHT:
            context = context_with_previous_investment_decision(
                context,
                self.decision_episode_store,
                self.continuity_service,
                account_id=job.account_id,
            )
        context["ontologyQualityGate"] = ontology_quality_gate_context(context, self.settings)
        reasoning_case_context = (
            context.get("investmentReasoningCase")
            if isinstance(context.get("investmentReasoningCase"), dict)
            else {}
        )
        reasoning_case_id = str(
            context.get("investmentReasoningCaseId")
            or reasoning_case_context.get("caseId")
            or ""
        )
        if reasoning_case_id and self.reasoning_orchestrator is not None:
            context = self.reasoning_orchestrator.capture_ai_context(
                reasoning_case_id,
                context,
            )
        execution_profile = notification_ai_execution_profile(context, self.settings)
        context["notificationAiExecutionProfile"] = execution_profile
        model = str(self.settings.get("notificationAiModel") or "gpt-5.6-sol")
        context["notificationAiReplayManifest"] = {
            "promptVersion": AI_DECISION_PROMPT_VERSION,
            "modelVersion": model,
            "decisionContractVersion": AI_DECISION_CONTRACT_VERSION,
            "reasoningEffort": str(execution_profile.get("reasoningEffort") or "max"),
        }
        request = AIInferenceRequest.create(
            job,
            context,
            model=model,
            reasoning_effort=str(execution_profile.get("reasoningEffort") or "max"),
            prompt_version=AI_DECISION_PROMPT_VERSION,
        )
        outcome = self.queue.enqueue(job, request)
        if reasoning_case_id and self.reasoning_orchestrator is not None:
            status = str(outcome.get("status") or "")
            if status in {"awaiting-ai", "pending", "processing", "retry"}:
                self.reasoning_orchestrator.ai_queued(
                    reasoning_case_id,
                    str(outcome.get("requestId") or request.request_id),
                    request.notification_job_id,
                )
            elif status in {"coalesced-identical", "superseded"}:
                self.reasoning_orchestrator.case_superseded(
                    reasoning_case_id,
                    "A newer or identical AI request owns this subject decision.",
                )
            for superseded_case_id in outcome.get("supersededReasoningCaseIds") or []:
                self.reasoning_orchestrator.case_superseded(
                    str(superseded_case_id or ""),
                    "A newer AI request replaced this subject decision.",
                )
        return outcome


class AIInferenceQueueRunner:
    """Run one leased request at a time; parallelism comes from worker count."""

    def __init__(
        self,
        queue,
        reviewer,
        settings: Dict[str, object] = None,
        decision_episode_store=None,
        continuity_service=None,
        action_planning_service=None,
        reasoning_orchestrator=None,
        worker_id: str = "",
    ):
        self.queue = queue
        self.reviewer = reviewer
        self.settings = dict(settings or {})
        self.decision_episode_store = decision_episode_store
        self.continuity_service = continuity_service
        self.action_planning_service = action_planning_service
        self.reasoning_orchestrator = reasoning_orchestrator
        self.worker_id = str(worker_id or "notification-ai-" + uuid.uuid4().hex[:10])
        self.lease_seconds = _int_setting(self.settings, "notificationAiQueueLeaseSeconds", 360, 60, 3600)
        self.heartbeat_seconds = _int_setting(self.settings, "notificationAiQueueHeartbeatSeconds", 10, 2, 120)
        self.max_attempts = _int_setting(self.settings, "notificationAiQueueMaxAttempts", 2, 1, 8)
        self.retry_seconds = _int_setting(self.settings, "notificationAiQueueRetrySeconds", 30, 5, 900)
        self.delivery_deadline_seconds = _int_setting(
            self.settings,
            "notificationAiDeliveryDeadlineSeconds",
            180,
            15,
            600,
        )
        self.fallback_enabled = _bool_setting(
            self.settings,
            "notificationAiTypeDbFallbackEnabled",
            True,
        )
        self.fallback_on_first_failure = _bool_setting(
            self.settings,
            "notificationAiFallbackOnFirstFailure",
            True,
        )
        self.storage_retry_attempts = _int_setting(
            self.settings,
            "notificationAiQueueStorageRetryAttempts",
            3,
            1,
            8,
        )
        self.storage_retry_backoff_milliseconds = _int_setting(
            self.settings,
            "notificationAiQueueStorageRetryBackoffMilliseconds",
            250,
            0,
            5000,
        )
        self.max_prompt_bytes = _int_setting(
            self.settings,
            "notificationAiQueueMaxPromptBytes",
            24 * 1024,
            12 * 1024,
            24 * 1024,
        )
        repair_effort = str(
            self.settings.get("notificationAiComparisonRepairReasoningEffort") or "low"
        ).strip().lower()
        self.comparison_repair_reasoning_effort = (
            repair_effort if repair_effort in {"low", "medium", "high", "max"} else "low"
        )
        self.comparison_repair_timeout_seconds = _int_setting(
            self.settings,
            "notificationAiComparisonRepairTimeoutSeconds",
            60,
            10,
            120,
        )
        self.last_run_details = []
        self.stopping = False

    def stop(self) -> None:
        self.stopping = True
        stopper = getattr(self.reviewer, "stop", None)
        if callable(stopper):
            stopper()

    def run_once(self, limit: int = 1) -> int:
        self.last_run_details = []
        requests = self.queue.claim(
            self.worker_id,
            limit=max(1, int(limit or 1)),
            lease_seconds=self.lease_seconds,
        )
        processed = 0
        for request in requests:
            try:
                detail = self.process_request(request)
            except Exception as error:  # noqa: BLE001 - one AI request must not stop a worker.
                detail = self.recover_request(request, error)
            self.last_run_details.append(detail)
            processed += 1
        return processed

    def process_request(self, request: AIInferenceRequest) -> str:
        context = dict(request.context or {})
        try:
            if request.message_type == INVESTMENT_INSIGHT:
                context = context_with_previous_investment_decision(
                    context,
                    self.decision_episode_store,
                    self.continuity_service,
                    account_id=request.account_id,
                    symbol=request.symbol,
                )
            execution_profile = dict(context.get("notificationAiExecutionProfile") or {})
            if not execution_profile:
                execution_profile = notification_ai_execution_profile(context, self.settings)
                context["notificationAiExecutionProfile"] = execution_profile
            execution_profile["reasoningEffort"] = request.reasoning_effort
            decision_brief = notification_ai_decision_brief(context, self.settings, execution_profile)
            prompt_bundle = build_notification_ai_prompt_bundle(
                context,
                self.settings,
                max_prompt_bytes=min(
                    self.max_prompt_bytes,
                    int(execution_profile.get("maxPromptBytes") or self.max_prompt_bytes),
                ),
                profile=execution_profile,
                decision_brief=decision_brief,
            )
            prompt = str(prompt_bundle.get("prompt") or "")
            decision_core = dict(prompt_bundle.get("decisionCore") or {})
            context_routing = dict(prompt_bundle.get("contextRouting") or {})
            prompt_release = dict(prompt_bundle.get("promptRelease") or {})
        except Exception as error:  # noqa: BLE001 - TypeDB inference remains publishable without AI preparation.
            if self.fallback_enabled:
                return self.publish_preparation_fallback(request, context, error)
            raise
        executed_prompt = prompt
        prompt_bytes = len(executed_prompt.encode("utf-8"))
        prompt_hash = hashlib.sha256(executed_prompt.encode("utf-8")).hexdigest()
        stop_heartbeat = threading.Event()
        lease_lost = threading.Event()
        heartbeat = threading.Thread(
            target=self.heartbeat_loop,
            args=(request.request_id, stop_heartbeat, lease_lost),
            name="ai-inference-heartbeat-" + request.request_id[:8],
            daemon=True,
        )
        heartbeat.start()
        started = time.monotonic()
        review_error = None
        comparison_repair_attempted = False
        comparison_repair_succeeded = False
        comparison_repair_error = ""
        comparison_repair_contract_error = ""
        comparison_repair_initial_contract_error = ""
        fallback_reason = ""
        ai_attempted = False
        review_context = dict(context)
        review_context["_notificationAiPreparedPrompt"] = prompt
        review_context["_notificationAiPreparedDecisionBrief"] = decision_brief
        review_context["_notificationAiPreparedDecisionCore"] = decision_core
        try:
            remaining_seconds = self.remaining_delivery_seconds(request)
            if remaining_seconds < 5:
                raise TimeoutError(
                    "notification AI delivery deadline exceeded before model execution"
                )
            review_context["_notificationAiTimeoutSecondsOverride"] = remaining_seconds
            ai_attempted = True
            response = self.reviewer.review(review_context)
            comparison_repair_contract_error = ai_response_contract_error(review_context, response)
            comparison_repair_initial_contract_error = comparison_repair_contract_error
            if (
                hypothesis_comparison_needs_repair(request.message_type, response)
                or comparison_repair_contract_error
            ):
                comparison_repair_attempted = True
                repair_context = dict(review_context)
                executed_prompt = hypothesis_comparison_repair_prompt(
                    prompt,
                    response,
                    comparison_repair_contract_error,
                )
                repair_context["_notificationAiPreparedPrompt"] = executed_prompt
                repair_context["notificationAiExecutionProfile"] = {
                    **execution_profile,
                    "name": "contractRepair",
                    "reasoningEffort": self.comparison_repair_reasoning_effort,
                }
                repair_remaining = self.remaining_delivery_seconds(request)
                repair_context["_notificationAiTimeoutSecondsOverride"] = min(
                    self.comparison_repair_timeout_seconds,
                    repair_remaining,
                )
                try:
                    if repair_remaining < 5:
                        raise TimeoutError(
                            "notification AI delivery deadline exceeded before contract repair"
                        )
                    repaired = self.reviewer.review(repair_context)
                except Exception as error:  # noqa: BLE001 - a failed repair becomes an explicit abstention.
                    comparison_repair_error = str(error)[:320]
                    response.validation_warnings.append(
                        "가설 비교 교정 요청이 실패해 선택 가설 없이 판단을 유보했습니다: "
                        + comparison_repair_error
                    )
                else:
                    response = repaired
                    comparison_repair_contract_error = ai_response_contract_error(
                        repair_context, response
                    )
                    comparison_repair_succeeded = bool(
                        not hypothesis_comparison_needs_repair(request.message_type, response)
                        and not comparison_repair_contract_error
                    )
                    if not comparison_repair_succeeded:
                        response.validation_warnings.append(
                            "가설 비교를 한 번 교정했지만 계약을 충족하지 못해 선택 가설 없이 판단을 유보했습니다."
                        )
        except Exception as error:  # noqa: BLE001 - retry policy is applied below.
            review_error = error
            response = None
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=max(1.0, self.heartbeat_seconds + 1.0))
        latency_ms = int((time.monotonic() - started) * 1000)
        prompt_bytes = len(executed_prompt.encode("utf-8"))
        prompt_hash = hashlib.sha256(executed_prompt.encode("utf-8")).hexdigest()
        reviewer_prompt_bytes = max(
            0,
            int(getattr(self.reviewer, "last_prompt_bytes", 0) or 0),
        )
        if reviewer_prompt_bytes:
            prompt_bytes = reviewer_prompt_bytes

        if lease_lost.is_set():
            return request.request_id[:8] + " superseded-during-review"
        if (
            review_error is not None
            and not self.fallback_on_first_failure
            and (self.stopping or request.attempts < self.max_attempts)
        ):
            outcome = self.queue.retry(
                request,
                self.worker_id,
                review_error,
                retry_seconds=self.retry_seconds,
            )
            return request.request_id[:8] + " " + str(outcome.get("status") or "retry")
        if review_error is not None:
            if not self.fallback_enabled:
                failed = self.storage_call_with_retry(
                    lambda: self.queue.fail(request, self.worker_id, review_error)
                )
                if failed and self.reasoning_orchestrator is not None:
                    self.reasoning_orchestrator.ai_failed(context, str(review_error))
                return request.request_id[:8] + (" failed" if failed else " lease-lost")
            fallback_reason = str(review_error)
            response = typedb_inference_fallback_response(context, review_error)

        quality_gate = context.get("ontologyQualityGate")
        if not isinstance(quality_gate, dict):
            quality_gate = ontology_quality_gate_context(context, self.settings)
            context["ontologyQualityGate"] = quality_gate
        apply_ontology_quality_gate_to_response(response, quality_gate)
        episode = self.decision_episode_context(request, context, response)
        action_plan = self.action_plan_context(context, episode)
        enriched = context_with_validated_ai_response(context, response, self.settings)
        continuity_packet = (
            dict(context.get("decisionContinuityPacket") or {})
            if isinstance(context.get("decisionContinuityPacket"), dict)
            else {}
        )
        execution_audit = {
            "version": "notification-ai-execution-audit-v2",
            "status": "typedb-fallback" if fallback_reason else "completed",
            "requestId": request.request_id,
            "notificationJobId": request.notification_job_id,
            "promptVersion": request.prompt_version,
            "model": request.model,
            "reasoningEffort": request.reasoning_effort,
            "promptHash": prompt_hash,
            "promptBytes": prompt_bytes,
            "prompt": executed_prompt,
            "decisionBriefVersion": decision_brief.get("schemaVersion"),
            "decisionBrief": decision_brief,
            "decisionCore": decision_core,
            "contextRouting": context_routing,
            "promptRelease": prompt_release,
            "decisionContinuity": {
                "contractVersion": str(continuity_packet.get("contractVersion") or ""),
                "packetId": str(continuity_packet.get("packetId") or ""),
                "materialFingerprint": str(continuity_packet.get("materialFingerprint") or ""),
                "status": str(continuity_packet.get("status") or "unavailable"),
                "summary": dict(continuity_packet.get("summary") or {}),
            },
            "executionProfile": execution_profile,
            "internalDataAudit": (
                (context.get("notificationAiInternalData") or {}).get("audit")
                if isinstance(context.get("notificationAiInternalData"), dict)
                else {}
            ),
            "researchCycle": (
                (context.get("ontologyRelationContext") or {}).get("researchCycle")
                if isinstance(context.get("ontologyRelationContext"), dict)
                else context.get("researchCycle") or {}
            ),
            "responseSource": str(response.source or ""),
            "validationState": str(response.validation_state or ""),
            "aiAttempted": ai_attempted,
            "fallback": {
                "used": bool(fallback_reason),
                "reason": fallback_reason[:500],
                "deliveryDeadlineSeconds": self.delivery_deadline_seconds,
            },
            "hypothesisComparisonRepair": {
                "attempted": comparison_repair_attempted,
                "succeeded": comparison_repair_succeeded,
                "error": comparison_repair_error,
                "initialContractError": comparison_repair_initial_contract_error,
                "contractError": comparison_repair_contract_error,
                "reasoningEffort": self.comparison_repair_reasoning_effort,
                "timeoutSeconds": self.comparison_repair_timeout_seconds,
                "finalState": str(response.hypothesis_comparison_state or ""),
                "selectedHypothesisId": str(response.selected_hypothesis_id or ""),
            },
            "latencyMs": latency_ms,
        }
        enriched["notificationAiExecutionAudit"] = execution_audit
        result = AIInferenceResult.create(
            request,
            response.to_dict(),
            source=response.source,
            validation_state=response.validation_state,
            latency_ms=latency_ms,
            prompt_bytes=prompt_bytes,
        )
        if self.reasoning_orchestrator is not None and not fallback_reason:
            valid, validation_reason = self.reasoning_orchestrator.validate_ai_result(
                enriched,
                result,
            )
            if not valid:
                if not self.fallback_enabled:
                    failed = self.storage_call_with_retry(
                        lambda: self.queue.fail(request, self.worker_id, validation_reason)
                    )
                    if failed:
                        self.reasoning_orchestrator.ai_failed(enriched, validation_reason)
                    return request.request_id[:8] + (
                        " blocked-invalid-reasoning-contract" if failed else " lease-lost"
                    )
                fallback_reason = str(validation_reason)
                response = typedb_inference_fallback_response(context, validation_reason)
                apply_ontology_quality_gate_to_response(response, quality_gate)
                episode = None
                action_plan = None
                enriched = context_with_validated_ai_response(context, response, self.settings)
                execution_audit.update({
                    "status": "typedb-fallback",
                    "responseSource": str(response.source or ""),
                    "validationState": str(response.validation_state or ""),
                    "fallback": {
                        "used": True,
                        "reason": fallback_reason[:500],
                        "deliveryDeadlineSeconds": self.delivery_deadline_seconds,
                    },
                })
                enriched["notificationAiExecutionAudit"] = execution_audit
                result = AIInferenceResult.create(
                    request,
                    response.to_dict(),
                    source=response.source,
                    validation_state=response.validation_state,
                    latency_ms=latency_ms,
                    prompt_bytes=prompt_bytes,
                )
        published = self.storage_call_with_retry(
            lambda: self.queue.complete(request, self.worker_id, result, enriched)
        )
        if not published:
            return request.request_id[:8] + " superseded-before-publish"
        if self.reasoning_orchestrator is not None:
            try:
                if fallback_reason:
                    self.storage_call_with_retry(
                        lambda: self.reasoning_orchestrator.ai_fallback_completed(
                            request,
                            enriched,
                            result,
                            fallback_reason,
                        )
                    )
                else:
                    self.storage_call_with_retry(
                        lambda: self.reasoning_orchestrator.ai_completed(request, enriched, result)
                    )
            except Exception:  # noqa: BLE001 - notification publication remains authoritative.
                pass
        if not fallback_reason:
            self.persist_decision_episode(request, context, episode, action_plan)
        fallback = " typedb-fallback" if fallback_reason else ""
        return (
            request.request_id[:8]
            + " completed"
            + fallback
            + " latencyMs="
            + str(latency_ms)
            + " promptBytes="
            + str(prompt_bytes)
        )

    def remaining_delivery_seconds(self, request: AIInferenceRequest) -> int:
        created_at = _timestamp(getattr(request, "created_at", ""))
        if created_at is None:
            return self.delivery_deadline_seconds
        elapsed = max(0, int((datetime.now(timezone.utc) - created_at).total_seconds()))
        return max(0, self.delivery_deadline_seconds - elapsed)

    def publish_preparation_fallback(
        self,
        request: AIInferenceRequest,
        context: Dict[str, object],
        reason: object,
    ) -> str:
        """Release graph inference when AI context or prompt preparation fails."""

        response = typedb_inference_fallback_response(context, reason)
        quality_gate = context.get("ontologyQualityGate")
        if not isinstance(quality_gate, dict):
            quality_gate = ontology_quality_gate_context(context, self.settings)
            context["ontologyQualityGate"] = quality_gate
        apply_ontology_quality_gate_to_response(response, quality_gate)
        enriched = context_with_validated_ai_response(context, response, self.settings)
        fallback_reason = str(reason or "AI preparation failed")
        enriched["notificationAiExecutionAudit"] = {
            "version": "notification-ai-execution-audit-v2",
            "status": "typedb-fallback",
            "requestId": request.request_id,
            "notificationJobId": request.notification_job_id,
            "promptVersion": request.prompt_version,
            "model": request.model,
            "reasoningEffort": request.reasoning_effort,
            "promptHash": "",
            "promptBytes": 0,
            "prompt": "",
            "decisionBriefVersion": "",
            "decisionBrief": {},
            "executionProfile": dict(context.get("notificationAiExecutionProfile") or {}),
            "responseSource": str(response.source or ""),
            "validationState": str(response.validation_state or ""),
            "aiAttempted": False,
            "fallback": {
                "used": True,
                "stage": "ai-preparation",
                "reason": fallback_reason[:500],
                "deliveryDeadlineSeconds": self.delivery_deadline_seconds,
            },
            "latencyMs": 0,
        }
        result = AIInferenceResult.create(
            request,
            response.to_dict(),
            source=response.source,
            validation_state=response.validation_state,
            latency_ms=0,
            prompt_bytes=0,
        )
        published = self.storage_call_with_retry(
            lambda: self.queue.complete(request, self.worker_id, result, enriched)
        )
        if not published:
            return request.request_id[:8] + " superseded-before-publish"
        if self.reasoning_orchestrator is not None:
            try:
                self.storage_call_with_retry(
                    lambda: self.reasoning_orchestrator.ai_fallback_completed(
                        request,
                        enriched,
                        result,
                        fallback_reason,
                    )
                )
            except Exception:  # noqa: BLE001 - notification publication remains authoritative.
                pass
        return request.request_id[:8] + " completed typedb-fallback preparation"

    def heartbeat_loop(self, request_id: str, stop_event: threading.Event, lease_lost: threading.Event) -> None:
        while not stop_event.wait(self.heartbeat_seconds):
            try:
                alive = self.queue.heartbeat(request_id, self.worker_id, self.lease_seconds)
            except Exception:  # noqa: BLE001 - a later publication check remains authoritative.
                continue
            if not alive:
                lease_lost.set()
                stopper = getattr(self.reviewer, "stop", None)
                if callable(stopper):
                    stopper()
                return

    def recover_request(self, request: AIInferenceRequest, error: Exception) -> str:
        try:
            if request.attempts < self.max_attempts:
                outcome = self.storage_call_with_retry(
                    lambda: self.queue.retry(
                        request,
                        self.worker_id,
                        error,
                        retry_seconds=self.retry_seconds,
                    )
                )
                return request.request_id[:8] + " " + str(outcome.get("status") or "retry")
            failed = self.storage_call_with_retry(
                lambda: self.queue.fail(request, self.worker_id, error)
            )
            if failed and self.reasoning_orchestrator is not None:
                try:
                    self.reasoning_orchestrator.ai_failed(request.context, str(error))
                except Exception:  # noqa: BLE001 - queue failure state remains authoritative.
                    pass
            return request.request_id[:8] + (" failed" if failed else " lease-lost")
        except Exception as recovery_error:  # noqa: BLE001 - lease expiry is the final recovery boundary.
            return (
                request.request_id[:8]
                + " recovery-error attempts="
                + str(self.storage_retry_attempts)
                + " error="
                + str(recovery_error)[:160]
            )

    def storage_call_with_retry(self, callback):
        """Retry short queue publications without repeating the expensive AI call."""

        for attempt in range(self.storage_retry_attempts):
            try:
                return callback()
            except Exception:  # noqa: BLE001 - the final error keeps its original traceback.
                if attempt + 1 >= self.storage_retry_attempts:
                    raise
                delay_ms = self.storage_retry_backoff_milliseconds * (attempt + 1)
                if delay_ms:
                    time.sleep(delay_ms / 1000.0)

    def decision_episode_context(self, request, context, response):
        if request.message_type != INVESTMENT_INSIGHT:
            return None
        try:
            episode = decision_episode_from_context(context, response.to_dict(), job_id=request.notification_job_id)
        except Exception:  # noqa: BLE001 - decision memory does not own alert delivery.
            return None
        if episode:
            context["investmentDecisionEpisodeId"] = episode.episode_id
            context["investmentDecisionEpisode"] = episode.to_dict()
        return episode

    def action_plan_context(self, context, episode):
        if not episode or not self.action_planning_service:
            return None
        try:
            plan = self.action_planning_service.prepare(episode, context)
            episode.action_plan_id = plan.plan_id
            context["investmentActionPlan"] = plan.to_dict()
            context["investmentActionEnvelope"] = plan.envelope.to_dict() if plan.envelope else {}
            context["investmentDecisionEpisode"] = episode.to_dict()
            return plan
        except Exception as error:  # noqa: BLE001 - categorical AI decision remains usable.
            context["investmentActionPlanning"] = {
                "status": "error",
                "reason": str(error)[:180],
            }
            return None

    def persist_decision_episode(self, request, context, episode, action_plan=None) -> None:
        if not self.decision_episode_store or not episode:
            return
        try:
            relation_context = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
            subject = relation_context.get("subject") if isinstance(relation_context.get("subject"), dict) else {}
            facts = dict(relation_context.get("facts") or {})
            facts["inferenceGenerationId"] = relation_context.get("inferenceGenerationId") or ""
            self.decision_episode_store.record_observation(
                request.account_id,
                str(subject.get("symbol") or request.symbol or ""),
                facts,
                str(relation_context.get("inferenceGenerationAt") or context.get("referenceDate") or ""),
            )
            saved_episode = self.decision_episode_store.save(episode)
            if action_plan and self.action_planning_service:
                self.action_planning_service.save(action_plan)
            context["investmentDecisionEpisodeId"] = saved_episode.episode_id
            context["investmentDecisionEpisode"] = saved_episode.to_dict()
        except Exception:  # noqa: BLE001 - the validated notification is already atomically publishable.
            return
