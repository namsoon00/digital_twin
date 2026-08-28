"""Application services for deferred, validated notification AI inference."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict

from ..domain.ai_inference_queue import (
    AIInferenceRequest,
    AIInferenceResult,
    notification_ai_review_mode,
)
from ..domain.investment_brain import decision_episode_from_context
from ..domain.message_types import INVESTMENT_INSIGHT
from ..domain.notification_ai_decision_brief import (
    AI_DECISION_CONTRACT_VERSION,
    AI_DECISION_PROMPT_VERSION,
    notification_ai_decision_brief,
    notification_ai_execution_profile,
)
from ..domain.notification_ai_gate_validation import (
    local_validated_ai_response,
)
from ..domain.notification_ai_inference_packet import build_notification_ai_inference_packet
from ..domain.notification_narrative import narrative_fingerprint
from ..domain.ontology_decision_quality import build_ontology_decision_quality_snapshot
from ..domain.notifications import NotificationJob
from .notification_ai_gate_audit import context_with_validated_ai_response
from .notification_ai_judgement_service import (
    NotificationAIContractError,
    NotificationAIJudgementService,
    ai_response_contract_error,
    hypothesis_comparison_needs_repair,
    hypothesis_comparison_repair_prompt,
)
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


def attach_execution_writer_provenance(
    context: Dict[str, object],
    execution_audit: Dict[str, object],
) -> None:
    writer = dict(context.get("notificationWriterProvenance") or {})
    if not writer:
        return
    writer.update({
        "model": str(execution_audit.get("model") or ""),
        "promptVersion": str(execution_audit.get("promptVersion") or ""),
        "requestId": str(execution_audit.get("requestId") or ""),
    })
    context["notificationWriterProvenance"] = writer
    narrative = dict(context.get("notificationNarrativeBrief") or {})
    if narrative:
        narrative["writerProvenance"] = writer
        narrative["fingerprint"] = narrative_fingerprint(narrative)
        context["notificationNarrativeBrief"] = narrative
    for key in (
        "notificationAiValidatedResponse",
        "validatedDecisionResponse",
        "notificationInferenceResponse",
    ):
        response = context.get(key)
        if isinstance(response, dict):
            response = dict(response)
            response["writerProvenance"] = writer
            context[key] = response


def sync_execution_publication_audit(
    context: Dict[str, object],
    execution_audit: Dict[str, object],
    response,
    *,
    fallback_reason: str = "",
    narrative_only: bool = False,
    initial_contract_error: str = "",
    contract_error: str = "",
) -> None:
    """Bind the execution audit to the one canonical publication decision."""

    publication = dict(context.get("notificationNarrativePublication") or {})
    ai_claim_count = max(0, int(publication.get("aiClaimCount") or 0))
    publication_fallback = bool(publication.get("fallbackUsed"))
    if fallback_reason:
        adoption_state = "typedb-fallback"
    elif ai_claim_count <= 0 or publication_fallback:
        adoption_state = "executed-not-adopted"
    elif narrative_only:
        adoption_state = "narrative-adopted-action-not-applicable"
    else:
        adoption_state = "decision-and-narrative-adopted"
    validation = dict(getattr(response, "claim_validation", {}) or {})
    execution_audit.update({
        "adoptionState": adoption_state,
        "actionAuthority": str(
            publication.get("actionAuthority")
            or ("typedb" if narrative_only else "ai-judgement")
        ),
        # This is intentionally an exact copy. Validation counters belong in
        # claimValidationSummary and must never create a second publication state.
        "claimPublication": publication,
        "claimValidationSummary": {
            "status": str(validation.get("status") or "unavailable"),
            "verifiedClaimCount": max(0, int(validation.get("verifiedClaimCount") or 0)),
            "rejectedClaimCount": max(0, int(validation.get("rejectedClaimCount") or 0)),
            "sections": sorted({
                str(item.get("section") or "")
                for item in validation.get("validations") or []
                if isinstance(item, dict)
                and str(item.get("status") or "") == "verified"
                and str(item.get("section") or "")
            }),
            "initialContractError": str(initial_contract_error or ""),
            "contractError": str(contract_error or ""),
        },
    })


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


def preserve_verified_ai_narrative(fallback, reviewed):
    """Keep ledger-verified prose while TypeDB retains action authority."""

    if reviewed is None or int(getattr(reviewed, "verified_claim_count", 0) or 0) <= 0:
        return fallback
    claims = [dict(item) for item in getattr(reviewed, "narrative_claims", []) or [] if isinstance(item, dict)]
    if not claims:
        return fallback
    fallback.narrative_claims = claims
    fallback.claim_validation = dict(getattr(reviewed, "claim_validation", {}) or {})
    view = next((str(item.get("text") or "") for item in claims if item.get("section") == "view"), "")
    next_condition = next((
        str(item.get("text") or "")
        for item in claims
        if item.get("section") in {"next-condition", "limitation"}
    ), "")
    if view:
        fallback.investment_view = view
        fallback.opinion = view
    if next_condition:
        fallback.next_action_plan = next_condition
    fallback.writer_provenance = {
        "decisionOwner": "typedb",
        "narrativeOwner": "ai-partial",
        "aiAuthored": False,
        "aiNarrativePartiallyAdopted": True,
        "verifiedClaimCount": len(claims),
    }
    fallback.validation_warnings.append(
        "AI 행동 판단은 제외하고 증거 원장 검증을 통과한 설명만 부분 사용했습니다."
    )
    return fallback


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
        subject_case_context = (
            context.get("investmentSubjectDecisionCase")
            if isinstance(context.get("investmentSubjectDecisionCase"), dict)
            else {}
        )
        subject_case_id = str(
            context.get("investmentSubjectDecisionCaseId")
            or subject_case_context.get("subjectCaseId")
            or ""
        )
        review_mode = notification_ai_review_mode(context)
        narrative_only = review_mode == "context-narrative"
        context["notificationAiReviewMode"] = review_mode
        decision_case_id = subject_case_id or reasoning_case_id
        if decision_case_id and self.reasoning_orchestrator is not None and not narrative_only:
            context = self.reasoning_orchestrator.capture_ai_context(
                decision_case_id,
                context,
            )
            subject_case_id = str(context.get("investmentSubjectDecisionCaseId") or subject_case_id)
        quality_snapshot = build_ontology_decision_quality_snapshot(context)
        if quality_snapshot:
            context["ontologyDecisionQuality"] = quality_snapshot
        context["ontologyQualityGate"] = ontology_quality_gate_context(context, self.settings)
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
        if (subject_case_id or reasoning_case_id) and self.reasoning_orchestrator is not None and not narrative_only:
            status = str(outcome.get("status") or "")
            if status in {"awaiting-ai", "pending", "processing", "retry"}:
                self.reasoning_orchestrator.ai_queued(
                    subject_case_id or reasoning_case_id,
                    str(outcome.get("requestId") or request.request_id),
                    request.notification_job_id,
                )
            elif status == "coalesced-identical":
                self.reasoning_orchestrator.ai_queued(
                    subject_case_id or reasoning_case_id,
                    str(outcome.get("requestId") or request.request_id),
                    request.notification_job_id,
                )
            elif status == "superseded":
                self.reasoning_orchestrator.case_superseded(
                    subject_case_id or reasoning_case_id,
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
        self.judgement_service = NotificationAIJudgementService(
            reviewer,
            self.settings,
            max_prompt_bytes=self.max_prompt_bytes,
            repair_reasoning_effort=self.comparison_repair_reasoning_effort,
            repair_timeout_seconds=self.comparison_repair_timeout_seconds,
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
        narrative_only = request.review_mode == "context-narrative"
        subject_case_id = str(context.get("investmentSubjectDecisionCaseId") or "")
        canonical_subject_publication = bool(
            subject_case_id and self.reasoning_orchestrator is not None
        )
        atomic_canonical_publication = bool(
            canonical_subject_publication
            and getattr(self.queue, "supports_atomic_subject_publication", False)
        )
        context["notificationAiReviewMode"] = request.review_mode
        try:
            if request.message_type == INVESTMENT_INSIGHT:
                context = context_with_previous_investment_decision(
                    context,
                    self.decision_episode_store,
                    self.continuity_service,
                    account_id=request.account_id,
                    symbol=request.symbol,
                )
            quality_snapshot = build_ontology_decision_quality_snapshot(context)
            if quality_snapshot:
                context["ontologyDecisionQuality"] = quality_snapshot
            context["ontologyQualityGate"] = ontology_quality_gate_context(context, self.settings)
            execution_profile = dict(context.get("notificationAiExecutionProfile") or {})
            if not execution_profile:
                execution_profile = notification_ai_execution_profile(context, self.settings)
                context["notificationAiExecutionProfile"] = execution_profile
            execution_profile["reasoningEffort"] = request.reasoning_effort
            decision_brief = notification_ai_decision_brief(context, self.settings, execution_profile)
            packet = build_notification_ai_inference_packet(
                context,
                self.settings,
                max_prompt_bytes=min(
                    self.max_prompt_bytes,
                    int(execution_profile.get("maxPromptBytes") or self.max_prompt_bytes),
                ),
                profile=execution_profile,
                decision_brief=decision_brief,
            )
            prompt = packet.prompt
            decision_core = packet.decision_core
            context_routing = packet.context_routing
            prompt_release = packet.prompt_release
            context["_notificationAiInferencePacket"] = packet.to_audit_dict()
        except Exception as error:  # noqa: BLE001 - TypeDB inference remains publishable without AI preparation.
            if self.fallback_enabled:
                return self.publish_preparation_fallback(request, context, error)
            raise
        executed_prompt = prompt
        prompt_bytes = len(executed_prompt.encode("utf-8"))
        prompt_hash = packet.prompt_hash
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
        judgement_outcome = None
        rejected_ai_response = None
        try:
            remaining_seconds = self.remaining_delivery_seconds(request)
            if remaining_seconds < 5:
                raise TimeoutError(
                    "notification AI delivery deadline exceeded before model execution"
                )
            ai_attempted = True
            judgement_outcome = self.judgement_service.judge(
                context,
                timeout_seconds=remaining_seconds,
                timeout_provider=lambda: self.remaining_delivery_seconds(request),
                profile=execution_profile,
                decision_brief=decision_brief,
                packet=packet,
            )
            response = judgement_outcome.response
            executed_prompt = judgement_outcome.executed_prompt
            comparison_repair_attempted = judgement_outcome.repair_attempted
            comparison_repair_succeeded = judgement_outcome.repair_succeeded
            comparison_repair_error = judgement_outcome.repair_error
            comparison_repair_contract_error = judgement_outcome.final_contract_error
            comparison_repair_initial_contract_error = judgement_outcome.initial_contract_error
            if not judgement_outcome.publishable:
                rejected_ai_response = response
                raise NotificationAIContractError(
                    judgement_outcome.final_contract_error
                    or judgement_outcome.final_publication_error
                    or judgement_outcome.repair_error
                    or "AI publication contract failed"
                )
        except Exception as error:  # noqa: BLE001 - retry policy is applied below.
            review_error = error
            response = rejected_ai_response
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=max(1.0, self.heartbeat_seconds + 1.0))
        latency_ms = int((time.monotonic() - started) * 1000)
        prompt_bytes = len(executed_prompt.encode("utf-8"))
        prompt_hash = (
            judgement_outcome.executed_prompt_hash
            if judgement_outcome is not None
            else packet.prompt_hash
        )
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
            response = preserve_verified_ai_narrative(
                typedb_inference_fallback_response(context, review_error),
                response,
            )

        quality_gate = context.get("ontologyQualityGate")
        if not isinstance(quality_gate, dict):
            quality_gate = ontology_quality_gate_context(context, self.settings)
            context["ontologyQualityGate"] = quality_gate
        apply_ontology_quality_gate_to_response(response, quality_gate)
        episode = None if narrative_only or canonical_subject_publication else self.decision_episode_context(
            request,
            context,
            response,
            typedb_only=bool(fallback_reason),
        )
        action_plan = None if narrative_only or canonical_subject_publication else self.action_plan_context(context, episode)
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
            "reviewMode": request.review_mode,
            "promptHash": prompt_hash,
            "promptBytes": prompt_bytes,
            "prompt": executed_prompt,
            "inferencePacket": packet.to_audit_dict(),
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
            "ontologyDecisionQuality": dict(context.get("ontologyDecisionQuality") or {}),
            "ontologyQualityGate": dict(context.get("ontologyQualityGate") or {}),
            "latencyMs": latency_ms,
        }
        sync_execution_publication_audit(
            enriched,
            execution_audit,
            response,
            fallback_reason=fallback_reason,
            narrative_only=narrative_only,
            initial_contract_error=(
                judgement_outcome.initial_publication_error
                if judgement_outcome is not None
                else ""
            ),
            contract_error=(
                judgement_outcome.final_publication_error
                if judgement_outcome is not None
                else ""
            ),
        )
        enriched["notificationAiExecutionAudit"] = execution_audit
        attach_execution_writer_provenance(enriched, execution_audit)
        result = AIInferenceResult.create(
            request,
            response.to_dict(),
            source=response.source,
            validation_state=response.validation_state,
            latency_ms=latency_ms,
            prompt_bytes=prompt_bytes,
        )
        if self.reasoning_orchestrator is not None and not fallback_reason and not narrative_only:
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
                rejected_ai_response = response
                response = preserve_verified_ai_narrative(
                    typedb_inference_fallback_response(context, validation_reason),
                    rejected_ai_response,
                )
                apply_ontology_quality_gate_to_response(response, quality_gate)
                episode = None if canonical_subject_publication else self.decision_episode_context(
                    request,
                    context,
                    response,
                    typedb_only=True,
                )
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
                sync_execution_publication_audit(
                    enriched,
                    execution_audit,
                    response,
                    fallback_reason=fallback_reason,
                    narrative_only=False,
                )
                enriched["notificationAiExecutionAudit"] = execution_audit
                attach_execution_writer_provenance(enriched, execution_audit)
                result = AIInferenceResult.create(
                    request,
                    response.to_dict(),
                    source=response.source,
                    validation_state=response.validation_state,
                    latency_ms=latency_ms,
                    prompt_bytes=prompt_bytes,
                )
        canonical_case = None
        if canonical_subject_publication and not atomic_canonical_publication:
            if fallback_reason:
                canonical_case = self.storage_call_with_retry(
                    lambda: self.reasoning_orchestrator.ai_fallback_completed(
                        request,
                        enriched,
                        result,
                        fallback_reason,
                    )
                )
            else:
                canonical_case = self.storage_call_with_retry(
                    lambda: self.reasoning_orchestrator.ai_completed(request, enriched, result)
                )
            if canonical_case is None or canonical_case.publication is None:
                failed = self.storage_call_with_retry(
                    lambda: self.queue.fail(
                        request,
                        self.worker_id,
                        "Canonical subject decision publication was not created.",
                    )
                )
                return request.request_id[:8] + (
                    " blocked-missing-publication" if failed else " lease-lost"
                )
            enriched["decisionPublication"] = canonical_case.publication.to_dict()
            enriched["investmentSubjectDecisionCase"] = canonical_case.to_dict()
            if canonical_case.publication.decision_episode_id:
                enriched["investmentDecisionEpisodeId"] = canonical_case.publication.decision_episode_id
                getter = getattr(self.decision_episode_store, "get", None)
                stored_episode = getter(canonical_case.publication.decision_episode_id) if callable(getter) else None
                if stored_episode is not None:
                    enriched["investmentDecisionEpisode"] = stored_episode.to_dict()
        if episode is not None:
            is_current = getattr(self.queue, "is_current", None)
            if callable(is_current) and not is_current(request.request_id, self.worker_id):
                return request.request_id[:8] + " superseded-before-decision-persist"
            saved_episode = self.storage_call_with_retry(
                lambda: self.persist_decision_episode(request, context, episode, action_plan)
            )
            if saved_episode is not None:
                enriched["investmentDecisionEpisodeId"] = saved_episode.episode_id
                enriched["investmentDecisionEpisode"] = saved_episode.to_dict()
                execution_audit["decisionPersistence"] = {
                    "status": "stored",
                    "episodeId": saved_episode.episode_id,
                    "comparisonState": str(
                        (saved_episode.facts_at_decision or {}).get("decisionComparisonState") or ""
                    ),
                }
                enriched["notificationAiExecutionAudit"] = execution_audit
        if atomic_canonical_publication:
            def publish_subject_with_queue(connection):
                if fallback_reason:
                    canonical_case = self.reasoning_orchestrator.ai_fallback_completed(
                        request,
                        enriched,
                        result,
                        fallback_reason,
                        connection=connection,
                    )
                else:
                    canonical_case = self.reasoning_orchestrator.ai_completed(
                        request,
                        enriched,
                        result,
                        connection=connection,
                    )
                if canonical_case is None or canonical_case.publication is None:
                    raise RuntimeError("Canonical subject decision publication was not created.")
                enriched["decisionPublication"] = canonical_case.publication.to_dict()
                enriched["investmentSubjectDecisionCase"] = canonical_case.to_dict()
                if canonical_case.publication.decision_episode_id:
                    enriched["investmentDecisionEpisodeId"] = canonical_case.publication.decision_episode_id

            published = self.storage_call_with_retry(
                lambda: self.queue.complete(
                    request,
                    self.worker_id,
                    result,
                    enriched,
                    before_complete=publish_subject_with_queue,
                )
            )
        else:
            published = self.storage_call_with_retry(
                lambda: self.queue.complete(request, self.worker_id, result, enriched)
            )
        if not published:
            return request.request_id[:8] + " superseded-before-publish"
        if atomic_canonical_publication:
            try:
                self.storage_call_with_retry(
                    lambda: self.reasoning_orchestrator.enqueue_hypothesis_gap(subject_case_id)
                )
            except Exception:  # noqa: BLE001 - proposal work is an asynchronous follow-up.
                pass
        if (
            self.reasoning_orchestrator is not None
            and not narrative_only
            and not canonical_subject_publication
        ):
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
        canonical_subject_publication = bool(
            context.get("investmentSubjectDecisionCaseId")
            and self.reasoning_orchestrator is not None
        )
        atomic_canonical_publication = bool(
            canonical_subject_publication
            and getattr(self.queue, "supports_atomic_subject_publication", False)
        )
        episode = None if canonical_subject_publication else self.decision_episode_context(
            request, context, response, typedb_only=True,
        )
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
        sync_execution_publication_audit(
            enriched,
            enriched["notificationAiExecutionAudit"],
            response,
            fallback_reason=fallback_reason,
            narrative_only=request.review_mode == "context-narrative",
        )
        attach_execution_writer_provenance(
            enriched,
            enriched["notificationAiExecutionAudit"],
        )
        result = AIInferenceResult.create(
            request,
            response.to_dict(),
            source=response.source,
            validation_state=response.validation_state,
            latency_ms=0,
            prompt_bytes=0,
        )
        if canonical_subject_publication and not atomic_canonical_publication:
            canonical_case = self.storage_call_with_retry(
                lambda: self.reasoning_orchestrator.ai_fallback_completed(
                    request,
                    enriched,
                    result,
                    fallback_reason,
                )
            )
            if canonical_case is None or canonical_case.publication is None:
                failed = self.storage_call_with_retry(
                    lambda: self.queue.fail(
                        request,
                        self.worker_id,
                        "Canonical review-only publication was not created.",
                    )
                )
                return request.request_id[:8] + (
                    " blocked-missing-publication" if failed else " lease-lost"
                )
            enriched["decisionPublication"] = canonical_case.publication.to_dict()
            enriched["investmentSubjectDecisionCase"] = canonical_case.to_dict()
        if episode is not None:
            is_current = getattr(self.queue, "is_current", None)
            if callable(is_current) and not is_current(request.request_id, self.worker_id):
                return request.request_id[:8] + " superseded-before-decision-persist"
            saved_episode = self.storage_call_with_retry(
                lambda: self.persist_decision_episode(request, context, episode)
            )
            if saved_episode is not None:
                enriched["investmentDecisionEpisodeId"] = saved_episode.episode_id
                enriched["investmentDecisionEpisode"] = saved_episode.to_dict()
                enriched["notificationAiExecutionAudit"]["decisionPersistence"] = {
                    "status": "stored",
                    "episodeId": saved_episode.episode_id,
                    "comparisonState": "typedb-only",
                }
        if atomic_canonical_publication:
            def publish_review_with_queue(connection):
                canonical_case = self.reasoning_orchestrator.ai_fallback_completed(
                    request,
                    enriched,
                    result,
                    fallback_reason,
                    connection=connection,
                )
                if canonical_case is None or canonical_case.publication is None:
                    raise RuntimeError("Canonical review-only publication was not created.")
                enriched["decisionPublication"] = canonical_case.publication.to_dict()
                enriched["investmentSubjectDecisionCase"] = canonical_case.to_dict()

            published = self.storage_call_with_retry(
                lambda: self.queue.complete(
                    request,
                    self.worker_id,
                    result,
                    enriched,
                    before_complete=publish_review_with_queue,
                )
            )
        else:
            published = self.storage_call_with_retry(
                lambda: self.queue.complete(request, self.worker_id, result, enriched)
            )
        if not published:
            return request.request_id[:8] + " superseded-before-publish"
        if self.reasoning_orchestrator is not None and not canonical_subject_publication:
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

    def decision_episode_context(self, request, context, response, *, typedb_only: bool = False):
        if request.message_type != INVESTMENT_INSIGHT:
            return None
        try:
            episode = decision_episode_from_context(context, response.to_dict(), job_id=request.notification_job_id)
        except Exception:  # noqa: BLE001 - decision memory does not own alert delivery.
            return None
        if episode:
            if typedb_only:
                episode.source = "typedb-inference-fallback"
                episode.status = "reference-only"
                episode.selected_hypothesis_id = ""
                episode.decision_abstention = {}
                episode.hypothesis_selection_source = "typedb-fallback-no-ai-selection"
                episode.facts_at_decision = {
                    **dict(episode.facts_at_decision or {}),
                    "decisionComparisonState": "typedb-only",
                    "decisionWriter": "typedb",
                }
            else:
                episode.facts_at_decision = {
                    **dict(episode.facts_at_decision or {}),
                    "decisionWriter": "ai",
                }
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

    def persist_decision_episode(self, request, context, episode, action_plan=None):
        if not self.decision_episode_store or not episode:
            return None
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
        return saved_episode
