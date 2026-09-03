"""Domain contracts for deferred notification AI inference.

The queue priority below is an operational scheduling band. It must never be
presented as an investment score, confidence, or probability. Investment
meaning remains owned by the TypeDB-backed relation context captured in each
immutable request.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, Mapping

from .notification_ai_gate_contracts import NOTIFICATION_AI_GATE_VERSION
from .context_observation_notifications import typedb_narrative_only_contract
from .notifications import NotificationJob
from .portfolio import utc_now_iso


AI_INFERENCE_PENDING = "pending"
AI_INFERENCE_PROCESSING = "processing"
AI_INFERENCE_RETRY = "retry"
AI_INFERENCE_COMPLETED = "completed"
AI_INFERENCE_FAILED = "failed"
AI_INFERENCE_SUPERSEDED = "superseded"
AI_REVIEW_MODE_INVESTMENT_JUDGEMENT = "investment-judgement"
AI_REVIEW_MODE_CONTEXT_NARRATIVE = "context-narrative"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _clean(value: object) -> str:
    return str(value or "").strip()


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _items(value: object) -> list:
    return list(value or []) if isinstance(value, (list, tuple, set)) else []


def _texts(values: Iterable[object]) -> list:
    return sorted({_clean(value) for value in values or [] if _clean(value)})


def notification_ai_action_eligibility(context: Mapping[str, object]) -> Dict[str, object]:
    """Return whether AI has a complete TypeDB action contract to judge.

    Legacy/non-investment payloads remain eligible. V2 investment payloads
    must name at least one eligible hypothesis and an allowed graph action;
    otherwise invoking the model can only fabricate or violate authority.
    """

    values = dict(context or {})
    subject = _mapping(values.get("investmentSubjectDecisionCase"))
    synthesis = (
        _mapping(values.get("v2DecisionSynthesis"))
        or _mapping(subject.get("synthesis"))
        or _mapping(_mapping(values.get("ontologyRelationContext")).get("decisionSynthesis"))
    )
    if not synthesis:
        return {"eligible": True, "reasonCode": "legacy-contract", "reason": "No V2 action contract is present."}
    hypothesis_ids = _texts(
        synthesis.get("eligible_hypothesis_ids")
        or synthesis.get("eligibleHypothesisIds")
        or _mapping(subject.get("candidateSet")).get("eligibleHypothesisIds")
        or []
    )
    allowed_actions = [
        value.upper() for value in _texts(
            synthesis.get("allowed_actions")
            or synthesis.get("allowedActions")
            or _mapping(subject.get("candidateSet")).get("allowedActions")
            or []
        )
    ]
    candidate_action = _clean(
        synthesis.get("graph_candidate_action") or synthesis.get("graphCandidateAction")
    ).upper()
    judgement_blocked = bool(
        synthesis.get("judgement_blocked") or synthesis.get("judgementBlocked")
    )
    if judgement_blocked:
        reason_code = "judgement-blocked"
    elif not hypothesis_ids:
        reason_code = "no-eligible-hypothesis"
    elif not allowed_actions:
        reason_code = "no-allowed-action"
    elif candidate_action in {"", "NO_ACTION"}:
        reason_code = "no-graph-action"
    elif candidate_action not in allowed_actions:
        reason_code = "graph-action-outside-envelope"
    else:
        reason_code = "eligible"
    return {
        "eligible": reason_code == "eligible",
        "reasonCode": reason_code,
        "reason": {
            "judgement-blocked": "TypeDB marked the subject as judgement blocked.",
            "no-eligible-hypothesis": "No eligible hypothesis can support an AI action judgement.",
            "no-allowed-action": "TypeDB did not authorize an investment action for AI selection.",
            "no-graph-action": "TypeDB produced an observation without an action candidate.",
            "graph-action-outside-envelope": "The graph candidate is outside the allowed action envelope.",
            "eligible": "The TypeDB hypothesis and action envelope are complete.",
        }.get(reason_code, reason_code),
        "candidateAction": candidate_action,
        "allowedActions": allowed_actions,
        "eligibleHypothesisIds": hypothesis_ids,
    }


def _rule_ids(values: object) -> list:
    return _texts(
        _mapping(item).get("ruleId")
        or _mapping(item).get("sourceRuleId")
        or _mapping(item).get("id")
        for item in _items(values)
    )


def notification_ai_material_contract(context: Mapping[str, object]) -> Dict[str, object]:
    """Return the semantic fields that justify replacing active AI work.

    Polling provenance, prices and generation-specific instance IDs are
    intentionally absent. A new quote for the same decision contract should
    not cancel a model process that is already comparing the same rules and
    hypothesis families. Action, eligibility, rule, evidence-event or causal
    family changes remain material and receive a fresh request.
    """

    values = dict(context or {})
    relation = _mapping(values.get("ontologyRelationContext"))
    insight = _mapping(values.get("ontologyInsight"))
    relation_diff = _mapping(values.get("ontologyRelationDiff"))
    transition = (
        _mapping(values.get("decisionTransition"))
        or _mapping(relation_diff.get("decisionTransition"))
        or _mapping(relation.get("decisionTransition"))
    )
    decision = _mapping(relation.get("decision"))
    execution_plan = _mapping(relation.get("executionPlan"))
    brain = _mapping(relation.get("investmentBrain"))
    envelope = (
        _mapping(relation.get("actionEnvelope"))
        or _mapping(execution_plan.get("actionEnvelope"))
        or _mapping(decision.get("actionEnvelope"))
        or _mapping(brain.get("actionEnvelope"))
    )
    selection = (
        _mapping(envelope.get("coreInferenceSelection"))
        or _mapping(relation.get("coreInferenceSelection"))
    )
    readiness = (
        _mapping(envelope.get("dataReadiness"))
        or _mapping(relation.get("dataReadiness"))
    )
    hypothesis_set = (
        _mapping(brain.get("hypothesisSet"))
        or _mapping(relation.get("hypothesisSet"))
    )
    hypothesis_families = []
    for item in _items(hypothesis_set.get("hypotheses")):
        row = _mapping(item)
        family_key = _clean(
            row.get("familyId")
            or row.get("causalSignature")
            or row.get("templateId")
            or row.get("hypothesisContractId")
        )
        if not family_key:
            continue
        hypothesis_families.append({
            "family": family_key,
            "template": _clean(row.get("templateId")),
            "action": _clean(row.get("candidateAction")).upper(),
            "rules": _texts(row.get("supportingRuleIds") or []),
            # Evidence entity IDs are generation-scoped and therefore change
            # on every quote refresh. Cardinality preserves a material change
            # in the comparison shape without treating polling IDs as meaning.
            "supportingEvidenceCount": len(_texts(row.get("supportingEvidenceIds") or [])),
            "counterEvidenceCount": len(_texts(row.get("counterEvidenceIds") or [])),
        })
    hypothesis_families.sort(
        key=lambda item: (item["family"], item["template"], item["action"])
    )
    source_events = _texts(
        values.get("materialSourceEventKeys")
        or insight.get("materialSourceEventKeys")
        or _mapping(insight.get("semanticComponents")).get("materialSourceEventKeys")
        or relation.get("materialSourceEventKeys")
        or []
    )
    return {
        "reviewMode": notification_ai_review_mode(values),
        "targetRole": _clean(relation.get("targetRole")),
        "reviewLevel": _clean(relation.get("reviewLevel")),
        "dataState": _clean(relation.get("dataState")),
        "conflictState": _clean(relation.get("conflictState")),
        "judgementBlocked": bool(envelope.get("judgementBlocked")),
        "executionAction": _clean(
            envelope.get("executionAction")
            or envelope.get("preferredAction")
            or transition.get("currentAction")
        ).upper(),
        "allowedActions": _texts(
            envelope.get("aiAllowedActions")
            or envelope.get("allowedActions")
            or relation.get("allowedActions")
            or []
        ),
        "blockedActions": _texts(
            envelope.get("blockedActions")
            or relation.get("blockedActions")
            or []
        ),
        "selectedRuleId": _clean(
            selection.get("selectedRuleId")
            or envelope.get("selectedRuleId")
            or decision.get("selectedRuleId")
        ),
        "drivingRuleIds": _texts(envelope.get("drivingRuleIds") or []),
        "eligibleRuleIds": _texts(readiness.get("eligibleRuleIds") or []),
        "activeRuleIds": _rule_ids(relation.get("activeRules")),
        "hypothesisFamilies": hypothesis_families,
        "materialSourceEventKeys": source_events,
        "transition": {
            "kind": _clean(transition.get("kind")),
            "currentAction": _clean(transition.get("currentAction")).upper(),
            "currentStatus": _clean(transition.get("currentStatus")),
            "currentDataReadiness": _clean(transition.get("currentDataReadiness")),
            "judgementBlocked": bool(transition.get("currentJudgementBlocked")),
        } if bool(transition.get("material")) else {},
    }


def notification_ai_material_fingerprint(context: Mapping[str, object]) -> str:
    return _canonical_hash(notification_ai_material_contract(context))


def notification_ai_can_join_active(
    active_context: Mapping[str, object],
    incoming_context: Mapping[str, object],
) -> bool:
    incoming = dict(incoming_context or {})
    transition = (
        _mapping(incoming.get("decisionTransition"))
        or _mapping(_mapping(incoming.get("ontologyRelationDiff")).get("decisionTransition"))
        or _mapping(_mapping(incoming.get("ontologyRelationContext")).get("decisionTransition"))
    )
    if bool(transition.get("material")):
        return False
    return notification_ai_material_fingerprint(active_context) == notification_ai_material_fingerprint(incoming)


def notification_ai_subject(context: Dict[str, object]) -> Dict[str, str]:
    values = _mapping(context)
    relation = _mapping(values.get("ontologyRelationContext"))
    subject = _mapping(relation.get("subject"))
    symbol = _clean(
        subject.get("symbol")
        or values.get("rawSymbol")
        or values.get("symbol")
        or values.get("rawTarget")
    ).upper()
    name = _clean(
        subject.get("name")
        or subject.get("displayName")
        or values.get("displayTarget")
        or values.get("target")
        or values.get("title")
    )
    generation_id = _clean(
        relation.get("inferenceGenerationId")
        or _mapping(values.get("ontologyInsight")).get("inferenceGenerationId")
    )
    return {
        "symbol": symbol,
        "name": name,
        "inferenceGenerationId": generation_id,
    }


def notification_ai_subject_key(job: NotificationJob, context: Dict[str, object]) -> str:
    subject = notification_ai_subject(context)
    identity = subject["symbol"] or subject["name"] or job.job_id
    return "|".join([
        _clean(job.account_id) or "global",
        _clean(job.message_type) or "notification",
        identity.casefold(),
    ])[:255]


def notification_ai_queue_priority(context: Dict[str, object]) -> int:
    """Map categorical ontology state to an operational worker order."""

    relation = _mapping(_mapping(context).get("ontologyRelationContext"))
    review_level = _clean(relation.get("reviewLevel")).lower()
    change_state = _clean(relation.get("changeState")).lower()
    priority = {
        "immediate": 50,
        "act": 40,
        "blocked": 35,
        "check": 30,
        "observe": 20,
        "normal": 10,
    }.get(review_level, 20)
    if change_state in {"direction-changed", "new-condition", "new-evidence"}:
        priority += 5
    return min(60, priority)


def notification_ai_review_mode(context: Dict[str, object]) -> str:
    configured = _clean(_mapping(context).get("notificationAiReviewMode")).lower()
    if configured in {AI_REVIEW_MODE_INVESTMENT_JUDGEMENT, AI_REVIEW_MODE_CONTEXT_NARRATIVE}:
        return configured
    if typedb_narrative_only_contract(context):
        return AI_REVIEW_MODE_CONTEXT_NARRATIVE
    return AI_REVIEW_MODE_INVESTMENT_JUDGEMENT


@dataclass
class AIInferenceRequest:
    request_id: str
    notification_job_id: str
    account_id: str
    account_label: str
    message_type: str
    subject_key: str
    symbol: str
    inference_generation_id: str
    context_hash: str
    review_mode: str = AI_REVIEW_MODE_INVESTMENT_JUDGEMENT
    context: Dict[str, object] = field(default_factory=dict)
    prompt_version: str = NOTIFICATION_AI_GATE_VERSION
    model: str = "gpt-5.6-sol"
    reasoning_effort: str = "max"
    priority: int = 20
    status: str = AI_INFERENCE_PENDING
    attempts: int = 0
    available_at: str = ""
    lease_owner: str = ""
    lease_expires_at: str = ""
    heartbeat_at: str = ""
    superseded_by: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    last_error: str = ""

    @classmethod
    def create(
        cls,
        job: NotificationJob,
        context: Dict[str, object],
        *,
        model: str = "gpt-5.6-sol",
        reasoning_effort: str = "max",
        prompt_version: str = NOTIFICATION_AI_GATE_VERSION,
    ) -> "AIInferenceRequest":
        captured = dict(context or {})
        review_mode = notification_ai_review_mode(captured)
        captured["notificationAiReviewMode"] = review_mode
        subject = notification_ai_subject(captured)
        stamp = utc_now_iso()
        return cls(
            request_id=uuid.uuid4().hex,
            notification_job_id=job.job_id,
            account_id=job.account_id,
            account_label=job.account_label,
            message_type=job.message_type,
            subject_key=notification_ai_subject_key(job, captured),
            symbol=subject["symbol"],
            inference_generation_id=subject["inferenceGenerationId"],
            context_hash=_canonical_hash(captured),
            review_mode=review_mode,
            context=captured,
            prompt_version=_clean(prompt_version) or NOTIFICATION_AI_GATE_VERSION,
            model=_clean(model) or "gpt-5.6-sol",
            reasoning_effort=_clean(reasoning_effort).lower() or "max",
            priority=notification_ai_queue_priority(captured),
            available_at=stamp,
            created_at=stamp,
            updated_at=stamp,
        )

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "AIInferenceRequest":
        values = dict(payload or {})
        aliases = {
            "requestId": "request_id",
            "notificationJobId": "notification_job_id",
            "accountId": "account_id",
            "accountLabel": "account_label",
            "messageType": "message_type",
            "subjectKey": "subject_key",
            "inferenceGenerationId": "inference_generation_id",
            "contextHash": "context_hash",
            "reviewMode": "review_mode",
            "promptVersion": "prompt_version",
            "reasoningEffort": "reasoning_effort",
            "availableAt": "available_at",
            "leaseOwner": "lease_owner",
            "leaseExpiresAt": "lease_expires_at",
            "heartbeatAt": "heartbeat_at",
            "supersededBy": "superseded_by",
            "createdAt": "created_at",
            "updatedAt": "updated_at",
            "startedAt": "started_at",
            "completedAt": "completed_at",
            "lastError": "last_error",
        }
        allowed = set(cls.__dataclass_fields__)
        normalized = {
            aliases.get(str(key), str(key)): value
            for key, value in values.items()
            if aliases.get(str(key), str(key)) in allowed
        }
        normalized["context"] = _mapping(normalized.get("context"))
        normalized["review_mode"] = notification_ai_review_mode({
            **normalized["context"],
            "notificationAiReviewMode": normalized.get("review_mode"),
        })
        normalized["priority"] = int(normalized.get("priority") or 20)
        normalized["attempts"] = int(normalized.get("attempts") or 0)
        return cls(**normalized)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        aliases = {
            "request_id": "requestId",
            "notification_job_id": "notificationJobId",
            "account_id": "accountId",
            "account_label": "accountLabel",
            "message_type": "messageType",
            "subject_key": "subjectKey",
            "inference_generation_id": "inferenceGenerationId",
            "context_hash": "contextHash",
            "review_mode": "reviewMode",
            "prompt_version": "promptVersion",
            "reasoning_effort": "reasoningEffort",
            "available_at": "availableAt",
            "lease_owner": "leaseOwner",
            "lease_expires_at": "leaseExpiresAt",
            "heartbeat_at": "heartbeatAt",
            "superseded_by": "supersededBy",
            "created_at": "createdAt",
            "updated_at": "updatedAt",
            "started_at": "startedAt",
            "completed_at": "completedAt",
            "last_error": "lastError",
        }
        return {aliases.get(key, key): value for key, value in payload.items()}


@dataclass
class AIInferenceResult:
    result_id: str
    request_id: str
    notification_job_id: str
    model: str
    reasoning_effort: str
    source: str
    validation_state: str
    latency_ms: int
    prompt_bytes: int
    review_mode: str = AI_REVIEW_MODE_INVESTMENT_JUDGEMENT
    response: Dict[str, object] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def create(
        cls,
        request: AIInferenceRequest,
        response: Dict[str, object],
        *,
        source: str,
        validation_state: str,
        latency_ms: int,
        prompt_bytes: int,
    ) -> "AIInferenceResult":
        response_payload = dict(response or {})
        response_payload.setdefault("reviewMode", request.review_mode)
        return cls(
            result_id=uuid.uuid4().hex,
            request_id=request.request_id,
            notification_job_id=request.notification_job_id,
            model=request.model,
            reasoning_effort=request.reasoning_effort,
            source=_clean(source),
            validation_state=_clean(validation_state) or "conditional",
            latency_ms=max(0, int(latency_ms or 0)),
            prompt_bytes=max(0, int(prompt_bytes or 0)),
            review_mode=request.review_mode,
            response=response_payload,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "resultId": self.result_id,
            "requestId": self.request_id,
            "notificationJobId": self.notification_job_id,
            "model": self.model,
            "reasoningEffort": self.reasoning_effort,
            "source": self.source,
            "validationState": self.validation_state,
            "latencyMs": self.latency_ms,
            "promptBytes": self.prompt_bytes,
            "reviewMode": self.review_mode,
            "response": dict(self.response or {}),
            "createdAt": self.created_at,
        }
