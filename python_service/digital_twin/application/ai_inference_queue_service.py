"""Application services for deferred, validated notification AI inference."""

from __future__ import annotations

import threading
import time
import uuid
import hashlib
from typing import Dict

from ..domain.ai_inference_queue import AIInferenceRequest, AIInferenceResult
from ..domain.investment_brain import decision_episode_from_context
from ..domain.message_types import INVESTMENT_INSIGHT
from ..domain.notification_ai_decision_brief import (
    AI_DECISION_PROMPT_VERSION,
    build_notification_ai_decision_prompt,
    notification_ai_decision_brief,
    notification_ai_execution_profile,
)
from ..domain.notification_ai_gate_validation import (
    local_validated_ai_response,
)
from ..domain.notifications import NotificationJob
from .notification_ai_gate_audit import context_with_validated_ai_response
from .notification_decision_memory import context_with_previous_investment_decision
from .notification_service import (
    apply_ontology_quality_gate_to_response,
    ontology_quality_gate_context,
)


def _int_setting(settings: Dict[str, object], key: str, fallback: int, minimum: int, maximum: int) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(minimum, min(maximum, value))


class NotificationAIRequestEnqueuer:
    """Capture one immutable AI context and move its notification to waiting."""

    def __init__(
        self,
        queue,
        context_preparer=None,
        settings: Dict[str, object] = None,
        decision_episode_store=None,
    ):
        self.queue = queue
        self.context_preparer = context_preparer
        self.settings = dict(settings or {})
        self.decision_episode_store = decision_episode_store

    def enqueue(self, job: NotificationJob) -> Dict[str, object]:
        if self.context_preparer:
            self.context_preparer(job)
        context = dict(job.context or {})
        context.setdefault("messageType", job.message_type)
        context.setdefault("accountId", job.account_id)
        context.setdefault("accountLabel", job.account_label)
        context.setdefault("jobId", job.job_id)
        if job.message_type == INVESTMENT_INSIGHT:
            context = context_with_previous_investment_decision(
                context,
                self.decision_episode_store,
                account_id=job.account_id,
            )
        context["ontologyQualityGate"] = ontology_quality_gate_context(context, self.settings)
        execution_profile = notification_ai_execution_profile(context, self.settings)
        context["notificationAiExecutionProfile"] = execution_profile
        request = AIInferenceRequest.create(
            job,
            context,
            model=str(self.settings.get("notificationAiModel") or "gpt-5.6-sol"),
            reasoning_effort=str(execution_profile.get("reasoningEffort") or "high"),
            prompt_version=AI_DECISION_PROMPT_VERSION,
        )
        return self.queue.enqueue(job, request)


class AIInferenceQueueRunner:
    """Run one leased request at a time; parallelism comes from worker count."""

    def __init__(
        self,
        queue,
        reviewer,
        settings: Dict[str, object] = None,
        decision_episode_store=None,
        action_planning_service=None,
        worker_id: str = "",
    ):
        self.queue = queue
        self.reviewer = reviewer
        self.settings = dict(settings or {})
        self.decision_episode_store = decision_episode_store
        self.action_planning_service = action_planning_service
        self.worker_id = str(worker_id or "notification-ai-" + uuid.uuid4().hex[:10])
        self.lease_seconds = _int_setting(self.settings, "notificationAiQueueLeaseSeconds", 360, 60, 3600)
        self.heartbeat_seconds = _int_setting(self.settings, "notificationAiQueueHeartbeatSeconds", 10, 2, 120)
        self.max_attempts = _int_setting(self.settings, "notificationAiQueueMaxAttempts", 2, 1, 8)
        self.retry_seconds = _int_setting(self.settings, "notificationAiQueueRetrySeconds", 30, 5, 900)
        self.max_prompt_bytes = _int_setting(
            self.settings,
            "notificationAiQueueMaxPromptBytes",
            48 * 1024,
            24 * 1024,
            256 * 1024,
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
        if request.message_type == INVESTMENT_INSIGHT:
            context = context_with_previous_investment_decision(
                context,
                self.decision_episode_store,
                account_id=request.account_id,
                symbol=request.symbol,
            )
        execution_profile = dict(context.get("notificationAiExecutionProfile") or {})
        if not execution_profile:
            execution_profile = notification_ai_execution_profile(context, self.settings)
            context["notificationAiExecutionProfile"] = execution_profile
        execution_profile["reasoningEffort"] = request.reasoning_effort
        decision_brief = notification_ai_decision_brief(context, self.settings, execution_profile)
        prompt = build_notification_ai_decision_prompt(
            context,
            self.settings,
            max_prompt_bytes=min(
                self.max_prompt_bytes,
                int(execution_profile.get("maxPromptBytes") or self.max_prompt_bytes),
            ),
            profile=execution_profile,
            decision_brief=decision_brief,
        )
        prompt_bytes = len(prompt.encode("utf-8"))
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
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
        review_context = dict(context)
        review_context["_notificationAiPreparedPrompt"] = prompt
        review_context["_notificationAiPreparedDecisionBrief"] = decision_brief
        try:
            response = self.reviewer.review(review_context)
        except Exception as error:  # noqa: BLE001 - retry policy is applied below.
            review_error = error
            response = None
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=max(1.0, self.heartbeat_seconds + 1.0))
        latency_ms = int((time.monotonic() - started) * 1000)
        reviewer_prompt_bytes = max(
            0,
            int(getattr(self.reviewer, "last_prompt_bytes", 0) or 0),
        )
        if reviewer_prompt_bytes:
            prompt_bytes = reviewer_prompt_bytes

        if lease_lost.is_set() or not self.queue.is_current(request.request_id, self.worker_id):
            return request.request_id[:8] + " superseded-during-review"
        if review_error is not None and (self.stopping or request.attempts < self.max_attempts):
            outcome = self.queue.retry(
                request,
                self.worker_id,
                review_error,
                retry_seconds=self.retry_seconds,
            )
            return request.request_id[:8] + " " + str(outcome.get("status") or "retry")
        if review_error is not None:
            response = local_validated_ai_response(context, source="local fallback after max retries")
            response.validation_warnings.append(
                "AI 추론이 " + str(request.attempts) + "회 실패해 TypeDB 근거 기반 로컬 설명을 사용했습니다: "
                + str(review_error)[:240]
            )

        quality_gate = context.get("ontologyQualityGate")
        if not isinstance(quality_gate, dict):
            quality_gate = ontology_quality_gate_context(context, self.settings)
            context["ontologyQualityGate"] = quality_gate
        apply_ontology_quality_gate_to_response(response, quality_gate)
        episode = self.decision_episode_context(request, context, response)
        action_plan = self.action_plan_context(context, episode)
        enriched = context_with_validated_ai_response(context, response, self.settings)
        enriched["notificationAiExecutionAudit"] = {
            "version": "notification-ai-execution-audit-v2",
            "status": "fallback" if "fallback" in str(response.source or "").lower() else "completed",
            "requestId": request.request_id,
            "notificationJobId": request.notification_job_id,
            "promptVersion": request.prompt_version,
            "model": request.model,
            "reasoningEffort": request.reasoning_effort,
            "promptHash": prompt_hash,
            "promptBytes": prompt_bytes,
            "prompt": prompt,
            "decisionBriefVersion": decision_brief.get("schemaVersion"),
            "decisionBrief": decision_brief,
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
            "latencyMs": latency_ms,
        }
        result = AIInferenceResult.create(
            request,
            response.to_dict(),
            source=response.source,
            validation_state=response.validation_state,
            latency_ms=latency_ms,
            prompt_bytes=prompt_bytes,
        )
        published = self.queue.complete(request, self.worker_id, result, enriched)
        if not published:
            return request.request_id[:8] + " superseded-before-publish"
        self.persist_decision_episode(request, context, episode, action_plan)
        fallback = " fallback" if "fallback" in str(response.source or "").lower() else ""
        return (
            request.request_id[:8]
            + " completed"
            + fallback
            + " latencyMs="
            + str(latency_ms)
            + " promptBytes="
            + str(prompt_bytes)
        )

    def heartbeat_loop(self, request_id: str, stop_event: threading.Event, lease_lost: threading.Event) -> None:
        while not stop_event.wait(self.heartbeat_seconds):
            try:
                alive = self.queue.heartbeat(request_id, self.worker_id, self.lease_seconds)
            except Exception:  # noqa: BLE001 - a later publication check remains authoritative.
                continue
            if not alive:
                lease_lost.set()
                return

    def recover_request(self, request: AIInferenceRequest, error: Exception) -> str:
        try:
            if request.attempts < self.max_attempts and self.queue.is_current(request.request_id, self.worker_id):
                outcome = self.queue.retry(
                    request,
                    self.worker_id,
                    error,
                    retry_seconds=self.retry_seconds,
                )
                return request.request_id[:8] + " " + str(outcome.get("status") or "retry")
            self.queue.fail(request, self.worker_id, error)
            return request.request_id[:8] + " failed"
        except Exception as recovery_error:  # noqa: BLE001 - lease expiry is the final recovery boundary.
            return request.request_id[:8] + " recovery-error=" + str(recovery_error)[:160]

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
