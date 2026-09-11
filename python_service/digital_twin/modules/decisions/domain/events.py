from digital_twin.modules.decisions.domain.event_types import AI_INFERENCE_REQUESTED, AI_INFERENCE_COMPLETED, AI_INFERENCE_SUPERSEDED, INVESTMENT_INFERENCE_EPISODE_COMPLETED, INVESTMENT_INFERENCE_DISPATCH_DECIDED, INVESTMENT_AI_INSIGHT_REQUESTED, INVESTMENT_AI_INSIGHT_COMPLETED, INVESTMENT_AI_INSIGHT_FAILED, INVESTMENT_DECISION_RECONCILED, INVESTMENT_DECISION_CHANGED, INVESTMENT_VALIDATION_CHANGED
from digital_twin.shared_kernel.events import DomainEvent
from digital_twin.shared_kernel.events import _stable_reasoning_event_id
from typing import Dict, Iterable, List, Mapping
























def ai_inference_event(
    name: str,
    request_id: str,
    *,
    notification_job_id: str = "",
    account_id: str = "",
    symbol: str = "",
    inference_generation_id: str = "",
    model: str = "",
    reasoning_effort: str = "",
    status: str = "",
    superseded_by: str = "",
) -> DomainEvent:
    """Publish bounded lifecycle metadata without duplicating AI context."""

    return DomainEvent(
        name=str(name or AI_INFERENCE_REQUESTED),
        aggregate_id=str(request_id or notification_job_id or "ai-inference")[:191],
        payload={
            "requestId": str(request_id or "")[:191],
            "notificationJobId": str(notification_job_id or "")[:191],
            "accountId": str(account_id or "")[:191],
            "symbol": str(symbol or "")[:64],
            "inferenceGenerationId": str(inference_generation_id or "")[:191],
            "model": str(model or "")[:120],
            "reasoningEffort": str(reasoning_effort or "")[:32],
            "status": str(status or "")[:32],
            "supersededBy": str(superseded_by or "")[:191],
        },
        correlation_id="ai-inference:" + str(notification_job_id or request_id or "")[:160],
    )


def investment_inference_episode_completed_event(subject_case) -> DomainEvent:
    """Publish the immutable TypeDB subject boundary independently of AI."""

    payload = (
        subject_case.to_dict()
        if callable(getattr(subject_case, "to_dict", None))
        else dict(subject_case or {})
    )
    subject_case_id = str(payload.get("subjectCaseId") or "")
    account_id = str(payload.get("accountId") or "")
    symbol = str(payload.get("symbol") or "").upper()
    candidate = dict(payload.get("candidateSet") or {})
    candidate_set_id = str(
        candidate.get("candidateSetId") or payload.get("candidateSetId") or ""
    )
    candidate_fingerprint = str(
        candidate.get("fingerprint") or payload.get("candidateFingerprint") or ""
    )
    return DomainEvent(
        name=INVESTMENT_INFERENCE_EPISODE_COMPLETED,
        aggregate_id=("inference-episode:" + subject_case_id)[:191],
        event_id=_stable_reasoning_event_id(
            INVESTMENT_INFERENCE_EPISODE_COMPLETED,
            subject_case_id,
            candidate_fingerprint,
        ),
        payload={
            "subjectCaseId": subject_case_id,
            "batchCaseId": str(payload.get("batchCaseId") or ""),
            "accountId": account_id,
            "symbol": symbol,
            "sourceAboxSnapshotId": str(payload.get("sourceAboxSnapshotId") or ""),
            "inferenceGenerationId": str(payload.get("inferenceGenerationId") or ""),
            "candidateSetId": candidate_set_id,
            "candidateFingerprint": candidate_fingerprint,
            "eligibleHypothesisIds": list(
                candidate.get("eligibleHypothesisIds")
                or payload.get("eligibleHypothesisIds")
                or []
            ),
            "allowedActions": list(
                candidate.get("allowedActions") or payload.get("allowedActions") or []
            ),
            "blockedActions": list(
                candidate.get("blockedActions") or payload.get("blockedActions") or []
            ),
            "stage": str(payload.get("stage") or ""),
            "source": "typedb-subject-decision-case",
        },
        correlation_id=str(payload.get("batchCaseId") or subject_case_id)[:191],
    )


def investment_inference_dispatch_decided_event(subject_case) -> DomainEvent:
    """Publish the immutable route chosen after one TypeDB subject result."""

    payload = (
        subject_case.to_dict()
        if callable(getattr(subject_case, "to_dict", None))
        else dict(subject_case or {})
    )
    decision = dict(payload.get("inferenceDispatchDecision") or {})
    subject_case_id = str(payload.get("subjectCaseId") or "")
    fingerprint = str(
        decision.get("candidateFingerprint")
        or dict(payload.get("candidateSet") or {}).get("fingerprint")
        or ""
    )
    return DomainEvent(
        name=INVESTMENT_INFERENCE_DISPATCH_DECIDED,
        aggregate_id=("inference-dispatch:" + subject_case_id)[:191],
        event_id=_stable_reasoning_event_id(
            INVESTMENT_INFERENCE_DISPATCH_DECIDED,
            decision.get("decisionId"),
            subject_case_id,
            fingerprint,
        ),
        payload={
            **decision,
            "subjectCaseId": subject_case_id,
            "batchCaseId": str(payload.get("batchCaseId") or ""),
            "accountId": str(payload.get("accountId") or ""),
            "symbol": str(payload.get("symbol") or "").upper(),
            "sourceAboxSnapshotId": str(payload.get("sourceAboxSnapshotId") or ""),
            "inferenceGenerationId": str(payload.get("inferenceGenerationId") or ""),
            "candidateFingerprint": fingerprint,
            "source": "typedb-inference-dispatch",
        },
        correlation_id=str(payload.get("batchCaseId") or subject_case_id)[:191],
    )


def investment_ai_insight_event(
    name: str,
    handoff,
    *,
    request=None,
    episode=None,
    error: object = "",
) -> DomainEvent:
    handoff_payload = (
        handoff.to_dict()
        if callable(getattr(handoff, "to_dict", None))
        else dict(handoff or {})
    )
    episode_payload = (
        episode.to_dict()
        if callable(getattr(episode, "to_dict", None))
        else dict(episode or {})
    )
    request_id = str(
        getattr(request, "request_id", "")
        or episode_payload.get("requestId")
        or ""
    )
    subject_case_id = str(handoff_payload.get("subjectCaseId") or "")
    return DomainEvent(
        name=str(name or INVESTMENT_AI_INSIGHT_REQUESTED),
        aggregate_id=("ai-insight:" + (request_id or subject_case_id))[:191],
        event_id=_stable_reasoning_event_id(
            str(name or INVESTMENT_AI_INSIGHT_REQUESTED),
            request_id,
            subject_case_id,
            episode_payload.get("episodeId"),
        ),
        payload={
            "requestId": request_id,
            "episodeId": str(episode_payload.get("episodeId") or ""),
            "handoffId": str(handoff_payload.get("handoffId") or ""),
            "subjectCaseId": subject_case_id,
            "accountId": str(handoff_payload.get("accountId") or ""),
            "symbol": str(handoff_payload.get("symbol") or "").upper(),
            "sourceAboxSnapshotId": str(handoff_payload.get("sourceAboxSnapshotId") or ""),
            "inferenceGenerationId": str(handoff_payload.get("inferenceGenerationId") or ""),
            "candidateFingerprint": str(handoff_payload.get("candidateFingerprint") or ""),
            "model": str(getattr(request, "model", "") or episode_payload.get("model") or ""),
            "reasoningEffort": str(
                getattr(request, "reasoning_effort", "")
                or episode_payload.get("reasoningEffort")
                or ""
            ),
            "validationState": str(episode_payload.get("validationState") or ""),
            "notificationJobId": str(episode_payload.get("notificationJobId") or ""),
            "error": str(error or "")[:500],
        },
        correlation_id=str(handoff_payload.get("batchCaseId") or subject_case_id)[:191],
    )


def investment_decision_reconciled_event(reconciliation: Mapping[str, object]) -> DomainEvent:
    payload = dict(reconciliation or {})
    subject_case_id = str(payload.get("subjectCaseId") or "")
    fingerprint = str(payload.get("candidateFingerprint") or "")
    return DomainEvent(
        name=INVESTMENT_DECISION_RECONCILED,
        aggregate_id=("decision-reconciliation:" + subject_case_id)[:191],
        event_id=_stable_reasoning_event_id(
            INVESTMENT_DECISION_RECONCILED,
            subject_case_id,
            fingerprint,
            payload.get("notificationDecision"),
        ),
        payload=payload,
        correlation_id=subject_case_id[:191],
    )


def investment_decision_changed_event(
    previous: Dict[str, object],
    current: Dict[str, object],
    *,
    notification_job_id: str = "",
) -> DomainEvent:
    """Record a material account decision transition without dispatching twice."""

    before = dict(previous or {})
    after = dict(current or {})
    account_id = str(after.get("accountId") or after.get("account_id") or "default")
    symbol = str(after.get("symbol") or "").upper()
    episode_id = str(after.get("episodeId") or after.get("episode_id") or "")
    flow_id = str(after.get("flowId") or "")
    return DomainEvent(
        name=INVESTMENT_DECISION_CHANGED,
        aggregate_id=("investment-decision:" + account_id + ":" + symbol)[:191],
        payload={
            "flowId": flow_id,
            "episodeId": episode_id,
            "accountId": account_id,
            "symbol": symbol,
            "previousAction": str(before.get("action") or ""),
            "currentAction": str(after.get("action") or ""),
            "previousReviewLevel": str(before.get("reviewLevel") or before.get("review_level") or ""),
            "currentReviewLevel": str(after.get("reviewLevel") or after.get("review_level") or ""),
            "previousDataState": str(before.get("dataState") or before.get("data_state") or ""),
            "currentDataState": str(after.get("dataState") or after.get("data_state") or ""),
            "previousValidationState": str(before.get("validationState") or before.get("validation_state") or ""),
            "currentValidationState": str(after.get("validationState") or after.get("validation_state") or ""),
            "inferenceGenerationId": str(after.get("inferenceGenerationId") or after.get("inference_generation_id") or ""),
            "selectedHypothesisId": str(after.get("selectedHypothesisId") or after.get("selected_hypothesis_id") or ""),
            "notificationJobId": str(notification_job_id or ""),
            "changedAt": str(after.get("decidedAt") or after.get("decided_at") or ""),
        },
        correlation_id=(flow_id or episode_id or (account_id + ":" + symbol))[:191],
    )


def investment_validation_changed_event(
    previous: Dict[str, object],
    current: Dict[str, object],
) -> DomainEvent:
    before = dict(previous or {})
    after = dict(current or {})
    account_id = str(after.get("accountId") or after.get("account_id") or "default")
    symbol = str(after.get("symbol") or "").upper()
    episode_id = str(after.get("episodeId") or after.get("episode_id") or "")
    flow_id = str(after.get("flowId") or "")
    return DomainEvent(
        name=INVESTMENT_VALIDATION_CHANGED,
        aggregate_id=("investment-validation:" + account_id + ":" + symbol)[:191],
        payload={
            "flowId": flow_id,
            "episodeId": episode_id,
            "accountId": account_id,
            "symbol": symbol,
            "previousDataState": str(before.get("dataState") or before.get("data_state") or ""),
            "currentDataState": str(after.get("dataState") or after.get("data_state") or ""),
            "previousValidationState": str(before.get("validationState") or before.get("validation_state") or ""),
            "currentValidationState": str(after.get("validationState") or after.get("validation_state") or ""),
            "inferenceGenerationId": str(after.get("inferenceGenerationId") or after.get("inference_generation_id") or ""),
            "changedAt": str(after.get("decidedAt") or after.get("decided_at") or ""),
        },
        correlation_id=(flow_id or episode_id or (account_id + ":" + symbol))[:191],
    )
