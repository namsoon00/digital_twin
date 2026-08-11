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
from typing import Dict

from .notification_ai_gate_contracts import NOTIFICATION_AI_GATE_VERSION
from .notifications import NotificationJob
from .portfolio import utc_now_iso


AI_INFERENCE_PENDING = "pending"
AI_INFERENCE_PROCESSING = "processing"
AI_INFERENCE_RETRY = "retry"
AI_INFERENCE_COMPLETED = "completed"
AI_INFERENCE_FAILED = "failed"
AI_INFERENCE_SUPERSEDED = "superseded"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _clean(value: object) -> str:
    return str(value or "").strip()


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
            response=dict(response or {}),
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
            "response": dict(self.response or {}),
            "createdAt": self.created_at,
        }
