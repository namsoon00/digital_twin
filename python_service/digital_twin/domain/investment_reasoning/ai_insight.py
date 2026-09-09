"""Independent AI insight contracts downstream of TypeDB inference."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Dict, Mapping, Tuple

from ..portfolio import utc_now_iso


AI_INSIGHT_HANDOFF_VERSION = "investment-ai-insight-handoff-v1"
AI_INSIGHT_EPISODE_VERSION = "investment-ai-insight-episode-v4"
DECISION_RECONCILIATION_VERSION = "investment-decision-reconciliation-v1"
SUBJECT_DECISION_ORIGIN = "subject-decision"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _compact_notification_draft(value: Mapping[str, object]) -> Dict[str, object]:
    draft = _mapping(value)
    return {
        key: draft.get(key)
        for key in (
            "jobId",
            "accountId",
            "accountLabel",
            "messageType",
            "text",
            "createdAt",
            "sourceEventId",
            "sourceEventName",
            "dedupeKey",
        )
        if draft.get(key) not in (None, "", [], {})
    }


@dataclass(frozen=True)
class AIInsightHandoff:
    """Immutable boundary between graph inference and model interpretation.

    The notification payload is only a draft at this stage. It must not become
    a notification job until a validated AI result has been reconciled with the
    TypeDB candidate set and the delivery policy.
    """

    handoff_id: str
    subject_case_id: str
    batch_case_id: str
    account_id: str
    symbol: str
    source_abox_snapshot_id: str
    inference_generation_id: str
    candidate_set_id: str
    candidate_fingerprint: str
    reserved_notification_job_id: str
    notification_draft: Dict[str, object] = field(default_factory=dict)
    source_event_id: str = ""
    source_event_name: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    version: str = AI_INSIGHT_HANDOFF_VERSION

    @classmethod
    def create(
        cls,
        context: Mapping[str, object],
        notification_draft: Mapping[str, object],
    ) -> "AIInsightHandoff":
        values = _mapping(context)
        subject = _mapping(values.get("investmentSubjectDecisionCase"))
        candidate = _mapping(subject.get("candidateSet"))
        relation = _mapping(values.get("ontologyRelationContext"))
        source_trace = _mapping(values.get("notificationSourceTrace"))
        draft = _compact_notification_draft(notification_draft)
        subject_case_id = _text(
            values.get("investmentSubjectDecisionCaseId")
            or subject.get("subjectCaseId")
        )
        batch_case_id = _text(
            values.get("investmentReasoningCaseId")
            or subject.get("batchCaseId")
        )
        account_id = _text(values.get("accountId") or subject.get("accountId"))
        symbol = _text(
            values.get("rawSymbol")
            or values.get("symbol")
            or subject.get("symbol")
        ).upper()
        source_abox_snapshot_id = _text(
            subject.get("sourceAboxSnapshotId")
            or relation.get("sourceAboxSnapshotId")
        )
        inference_generation_id = _text(
            subject.get("inferenceGenerationId")
            or relation.get("inferenceGenerationId")
        )
        candidate_set_id = _text(
            subject.get("candidateSetId")
            or candidate.get("candidateSetId")
        )
        candidate_fingerprint = _text(
            values.get("decisionCandidateFingerprint")
            or subject.get("candidateFingerprint")
            or candidate.get("fingerprint")
        )
        reserved_job_id = _text(draft.get("jobId"))
        material = {
            "subjectCaseId": subject_case_id,
            "candidateFingerprint": candidate_fingerprint,
            "inferenceGenerationId": inference_generation_id,
            "reservedNotificationJobId": reserved_job_id,
        }
        return cls(
            handoff_id="ai-insight-handoff:" + _fingerprint(material)[:32],
            subject_case_id=subject_case_id,
            batch_case_id=batch_case_id,
            account_id=account_id,
            symbol=symbol,
            source_abox_snapshot_id=source_abox_snapshot_id,
            inference_generation_id=inference_generation_id,
            candidate_set_id=candidate_set_id,
            candidate_fingerprint=candidate_fingerprint,
            reserved_notification_job_id=reserved_job_id,
            notification_draft=draft,
            source_event_id=_text(
                draft.get("sourceEventId") or source_trace.get("sourceEventId")
            ),
            source_event_name=_text(
                draft.get("sourceEventName") or source_trace.get("sourceEventName")
            ),
        )

    @property
    def validation_errors(self) -> Tuple[str, ...]:
        required = {
            "subject-case-id": self.subject_case_id,
            "batch-case-id": self.batch_case_id,
            "account-id": self.account_id,
            "source-abox-snapshot-id": self.source_abox_snapshot_id,
            "inference-generation-id": self.inference_generation_id,
            "candidate-set-id": self.candidate_set_id,
            "candidate-fingerprint": self.candidate_fingerprint,
            "reserved-notification-job-id": self.reserved_notification_job_id,
            "notification-text": self.notification_draft.get("text"),
        }
        return tuple(
            "missing-" + key
            for key, value in required.items()
            if not _text(value)
        )

    @property
    def valid(self) -> bool:
        return not self.validation_errors

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "handoffId": payload["handoff_id"],
            "originKind": SUBJECT_DECISION_ORIGIN,
            "subjectCaseId": payload["subject_case_id"],
            "batchCaseId": payload["batch_case_id"],
            "accountId": payload["account_id"],
            "symbol": payload["symbol"],
            "sourceAboxSnapshotId": payload["source_abox_snapshot_id"],
            "inferenceGenerationId": payload["inference_generation_id"],
            "candidateSetId": payload["candidate_set_id"],
            "candidateFingerprint": payload["candidate_fingerprint"],
            "reservedNotificationJobId": payload["reserved_notification_job_id"],
            "notificationDraft": dict(payload["notification_draft"] or {}),
            "sourceEventId": payload["source_event_id"],
            "sourceEventName": payload["source_event_name"],
            "createdAt": payload["created_at"],
            "version": payload["version"],
            "validationErrors": list(self.validation_errors),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AIInsightHandoff":
        payload = _mapping(value)
        return cls(
            handoff_id=_text(payload.get("handoffId")),
            subject_case_id=_text(payload.get("subjectCaseId")),
            batch_case_id=_text(payload.get("batchCaseId")),
            account_id=_text(payload.get("accountId")),
            symbol=_text(payload.get("symbol")).upper(),
            source_abox_snapshot_id=_text(payload.get("sourceAboxSnapshotId")),
            inference_generation_id=_text(payload.get("inferenceGenerationId")),
            candidate_set_id=_text(payload.get("candidateSetId")),
            candidate_fingerprint=_text(payload.get("candidateFingerprint")),
            reserved_notification_job_id=_text(payload.get("reservedNotificationJobId")),
            notification_draft=_compact_notification_draft(
                _mapping(payload.get("notificationDraft"))
            ),
            source_event_id=_text(payload.get("sourceEventId")),
            source_event_name=_text(payload.get("sourceEventName")),
            created_at=_text(payload.get("createdAt")) or utc_now_iso(),
            version=_text(payload.get("version")) or AI_INSIGHT_HANDOFF_VERSION,
        )


def ai_insight_handoff(context: Mapping[str, object]) -> AIInsightHandoff | None:
    payload = _mapping(_mapping(context).get("investmentAIInsightHandoff"))
    return AIInsightHandoff.from_dict(payload) if payload else None


def decision_reconciliation(
    handoff: AIInsightHandoff,
    delivery_decision: Mapping[str, object],
) -> Dict[str, object]:
    decision = _mapping(delivery_decision)
    # Delivery is fail-closed: an absent or unknown policy result must never
    # turn an AI insight into a push notification.
    notify = _text(decision.get("decision")).lower() == "send"
    return {
        "version": DECISION_RECONCILIATION_VERSION,
        "status": "reconciled",
        "subjectCaseId": handoff.subject_case_id,
        "candidateFingerprint": handoff.candidate_fingerprint,
        "inferenceGenerationId": handoff.inference_generation_id,
        "notificationDecision": "send" if notify else "suppress",
        "notificationJobId": handoff.reserved_notification_job_id if notify else "",
        "reasonCode": _text(
            decision.get("suppressionReason")
            or decision.get("pushValueClass")
            or decision.get("reasonCode")
        ),
        "reason": _text(decision.get("reason")),
        "deliveryPolicy": decision,
        "reconciledAt": utc_now_iso(),
    }


def reconciliation_after_delivery(
    reconciliation: Mapping[str, object],
    delivery_outcome: Mapping[str, object],
) -> Dict[str, object]:
    """Resolve the semantic delivery decision against actual outbox admission."""

    current = _mapping(reconciliation)
    outcome = _mapping(delivery_outcome)
    queued = bool(outcome.get("queued"))
    semantic_decision = _text(current.get("notificationDecision")).lower() or "suppress"
    actual_job_id = _text(outcome.get("notificationJobId")) if queued else ""
    actual_reason = _text(outcome.get("reason")) or _text(current.get("reason"))
    return {
        **current,
        "semanticNotificationDecision": semantic_decision,
        "notificationDecision": "send" if queued else "suppress",
        "notificationJobId": actual_job_id,
        "reason": actual_reason,
        "deliveryOutcome": {
            "status": _text(outcome.get("status")),
            "queued": queued,
            "notificationJobId": actual_job_id,
            "attemptedNotificationJobId": _text(outcome.get("notificationJobId")),
            "reason": _text(outcome.get("reason")),
        },
        "reconciledAt": utc_now_iso(),
    }


def compact_ai_insight(value: Mapping[str, object]) -> Dict[str, object]:
    payload = _mapping(value)
    return {
        key: payload.get(key)
        for key in (
            "action",
            "actionLabel",
            "validationState",
            "dataState",
            "reviewLevel",
            "summary",
            "investmentView",
            "currentActionPlan",
            "changeAnalysis",
            "nextActionPlan",
            "evidence",
            "counterEvidence",
            "invalidationCondition",
            "nextChecks",
            "selectedHypothesisId",
            "researchLeadHypothesisId",
            "hypothesisComparisonState",
            "reviewedHypothesisIds",
            "hypothesisReviews",
            "hypotheses",
            "unresolvedQuestions",
            "epistemicSummary",
            "decisionReadiness",
            "insightAssessment",
            "causalChain",
            "followUpConditions",
            "source",
        )
        if payload.get(key) not in (None, "", [], {})
    }


@dataclass(frozen=True)
class AIInsightEpisode:
    episode_id: str
    request_id: str
    result_id: str
    handoff_id: str
    subject_case_id: str
    account_id: str
    symbol: str
    source_abox_snapshot_id: str
    inference_generation_id: str
    candidate_fingerprint: str
    model: str
    reasoning_effort: str
    validation_state: str
    prompt_version: str = ""
    publication_mode: str = ""
    ai_authored: bool = False
    publication_contract_passed: bool = False
    contract_failure_code: str = ""
    insight: Dict[str, object] = field(default_factory=dict)
    reconciliation: Dict[str, object] = field(default_factory=dict)
    notification_job_id: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    version: str = AI_INSIGHT_EPISODE_VERSION

    @classmethod
    def create(cls, request, result, context: Mapping[str, object]) -> "AIInsightEpisode":
        values = _mapping(context)
        handoff = ai_insight_handoff(values)
        if handoff is None or not handoff.valid:
            raise ValueError("A valid subject-decision AI insight handoff is required.")
        reconciliation = _mapping(values.get("decisionReconciliation"))
        provenance = _mapping(values.get("notificationAIInsightProvenance"))
        insight = compact_ai_insight(getattr(result, "response", {}) or {})
        transition = _mapping(values.get("investmentInsightTransition"))
        if transition:
            insight["insightTransition"] = transition
        material = {
            "requestId": _text(getattr(request, "request_id", "")),
            "resultId": _text(getattr(result, "result_id", "")),
            "handoffId": handoff.handoff_id,
            "candidateFingerprint": handoff.candidate_fingerprint,
        }
        return cls(
            episode_id="ai-insight-episode:" + _fingerprint(material)[:32],
            request_id=material["requestId"],
            result_id=material["resultId"],
            handoff_id=handoff.handoff_id,
            subject_case_id=handoff.subject_case_id,
            account_id=handoff.account_id,
            symbol=handoff.symbol,
            source_abox_snapshot_id=handoff.source_abox_snapshot_id,
            inference_generation_id=handoff.inference_generation_id,
            candidate_fingerprint=handoff.candidate_fingerprint,
            model=_text(getattr(request, "model", "")),
            reasoning_effort=_text(getattr(request, "reasoning_effort", "")),
            validation_state=_text(getattr(result, "validation_state", "")),
            prompt_version=_text(getattr(request, "prompt_version", "")),
            publication_mode=_text(provenance.get("publicationMode")),
            ai_authored=bool(provenance.get("aiAuthored")),
            publication_contract_passed=bool(
                provenance.get("publicationContractPassed")
            ),
            contract_failure_code=_text(provenance.get("contractFailureCode")),
            insight=insight,
            reconciliation=reconciliation,
            notification_job_id=_text(reconciliation.get("notificationJobId")),
            created_at=_text(getattr(result, "created_at", "")) or utc_now_iso(),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "episodeId": payload["episode_id"],
            "requestId": payload["request_id"],
            "resultId": payload["result_id"],
            "handoffId": payload["handoff_id"],
            "subjectCaseId": payload["subject_case_id"],
            "accountId": payload["account_id"],
            "symbol": payload["symbol"],
            "sourceAboxSnapshotId": payload["source_abox_snapshot_id"],
            "inferenceGenerationId": payload["inference_generation_id"],
            "candidateFingerprint": payload["candidate_fingerprint"],
            "model": payload["model"],
            "reasoningEffort": payload["reasoning_effort"],
            "validationState": payload["validation_state"],
            "promptVersion": payload["prompt_version"],
            "publicationMode": payload["publication_mode"],
            "aiAuthored": payload["ai_authored"],
            "publicationContractPassed": payload["publication_contract_passed"],
            "contractFailureCode": payload["contract_failure_code"],
            "insight": dict(payload["insight"] or {}),
            "reconciliation": dict(payload["reconciliation"] or {}),
            "notificationJobId": payload["notification_job_id"],
            "createdAt": payload["created_at"],
            "version": payload["version"],
        }
