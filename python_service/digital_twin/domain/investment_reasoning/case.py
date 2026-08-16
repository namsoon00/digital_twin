"""Aggregate state for one durable investment reasoning execution."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, Mapping, Optional, Tuple

from .contracts import (
    AIJudgmentResult,
    FACT_DELTA_VERSION,
    FinalDecision,
    FactDelta,
    HypothesisRecord,
    InferenceResult,
    INVESTMENT_REASONING_CONTRACT_VERSION,
)


CASE_CREATED = "CREATED"
CASE_INPUT_READY = "INPUT_READY"
CASE_INFERENCE_COMPLETED = "INFERENCE_COMPLETED"
CASE_HYPOTHESES_READY = "HYPOTHESES_READY"
CASE_AI_PENDING = "AI_PENDING"
CASE_AI_COMPLETED = "AI_COMPLETED"
CASE_VALIDATED = "VALIDATED"
CASE_COMPLETED = "COMPLETED"
CASE_PUBLISHED = "PUBLISHED"
CASE_DEFERRED = "DEFERRED"
CASE_BLOCKED = "BLOCKED"
CASE_FAILED = "FAILED"

CASE_TERMINAL_STAGES = {CASE_COMPLETED, CASE_PUBLISHED, CASE_BLOCKED, CASE_FAILED}

CASE_TRANSITIONS = {
    CASE_CREATED: {CASE_INPUT_READY, CASE_DEFERRED, CASE_BLOCKED, CASE_FAILED},
    CASE_DEFERRED: {CASE_INPUT_READY, CASE_DEFERRED, CASE_BLOCKED, CASE_FAILED},
    CASE_INPUT_READY: {CASE_INFERENCE_COMPLETED, CASE_DEFERRED, CASE_BLOCKED, CASE_FAILED},
    CASE_INFERENCE_COMPLETED: {CASE_HYPOTHESES_READY, CASE_BLOCKED, CASE_FAILED},
    CASE_HYPOTHESES_READY: {CASE_AI_PENDING, CASE_VALIDATED, CASE_COMPLETED, CASE_BLOCKED, CASE_FAILED},
    CASE_AI_PENDING: {CASE_AI_COMPLETED, CASE_VALIDATED, CASE_BLOCKED, CASE_FAILED},
    CASE_AI_COMPLETED: {CASE_VALIDATED, CASE_BLOCKED, CASE_FAILED},
    CASE_VALIDATED: {CASE_COMPLETED, CASE_PUBLISHED, CASE_BLOCKED, CASE_FAILED},
    CASE_COMPLETED: set(),
    CASE_PUBLISHED: set(),
    CASE_BLOCKED: set(),
    CASE_FAILED: set(),
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def reasoning_case_id(request_id: object) -> str:
    digest = hashlib.sha256(str(request_id or "").encode("utf-8")).hexdigest()
    return "reasoning-case:" + digest[:32]


@dataclass
class ReasoningCase:
    case_id: str
    request_id: str
    deployment_id: str
    release_fingerprint: str
    validation_cohort_id: str
    stage: str
    fact_delta: FactDelta
    input_fingerprint: str = ""
    hypotheses: Tuple[HypothesisRecord, ...] = ()
    inference_result: Optional[InferenceResult] = None
    ai_judgment: Optional[AIJudgmentResult] = None
    final_decision: Optional[FinalDecision] = None
    ai_request_id: str = ""
    notification_job_id: str = ""
    stage_history: Tuple[Dict[str, object], ...] = ()
    errors: Tuple[Dict[str, object], ...] = ()
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = ""
    completed_at: str = ""
    version: int = 1
    contract_version: str = INVESTMENT_REASONING_CONTRACT_VERSION

    @classmethod
    def create(cls, request, release_identity: Mapping[str, object] = None) -> "ReasoningCase":
        stamp = utc_now_iso()
        release = dict(release_identity or {})
        case = cls(
            case_id=reasoning_case_id(getattr(request, "request_id", "")),
            request_id=str(getattr(request, "request_id", "") or ""),
            deployment_id=str(getattr(request, "deployment_id", "") or ""),
            release_fingerprint=str(release.get("releaseFingerprint") or ""),
            validation_cohort_id=str(release.get("validationCohortId") or ""),
            stage=CASE_CREATED,
            fact_delta=FactDelta.from_request(request),
            input_fingerprint=str(getattr(request, "input_fingerprint", "") or ""),
            created_at=stamp,
            updated_at=stamp,
        )
        case.stage_history = ({"stage": CASE_CREATED, "at": stamp, "reason": "reasoning-request-created"},)
        return case

    def transition(self, target: str, reason: str = "", details: Mapping[str, object] = None) -> "ReasoningCase":
        target_stage = str(target or "").upper()
        if target_stage == self.stage:
            return self
        allowed = CASE_TRANSITIONS.get(self.stage, set())
        if target_stage not in allowed:
            raise ValueError("Invalid reasoning case transition: " + self.stage + " -> " + target_stage)
        stamp = utc_now_iso()
        self.stage = target_stage
        self.updated_at = stamp
        self.version += 1
        self.stage_history = tuple([
            *self.stage_history,
            {
                "stage": target_stage,
                "at": stamp,
                "reason": str(reason or "")[:240],
                "details": dict(details or {}),
            },
        ][-40:])
        if target_stage in CASE_TERMINAL_STAGES:
            self.completed_at = stamp
        return self

    def record_error(self, stage: str, reason: str, retryable: bool = False) -> None:
        self.errors = tuple([
            *self.errors,
            {
                "stage": str(stage or self.stage),
                "reason": str(reason or "")[:500],
                "retryable": bool(retryable),
                "at": utc_now_iso(),
            },
        ][-20:])

    def to_dict(self) -> Dict[str, object]:
        return {
            "caseId": self.case_id,
            "requestId": self.request_id,
            "deploymentId": self.deployment_id,
            "releaseFingerprint": self.release_fingerprint,
            "validationCohortId": self.validation_cohort_id,
            "stage": self.stage,
            "factDelta": self.fact_delta.to_dict(),
            "inputFingerprint": self.input_fingerprint,
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "inferenceResult": self.inference_result.to_dict() if self.inference_result else {},
            "aiJudgment": self.ai_judgment.to_dict() if self.ai_judgment else {},
            "finalDecision": self.final_decision.to_dict() if self.final_decision else {},
            "aiRequestId": self.ai_request_id,
            "notificationJobId": self.notification_job_id,
            "stageHistory": [dict(item) for item in self.stage_history],
            "errors": [dict(item) for item in self.errors],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "completedAt": self.completed_at,
            "version": self.version,
            "contractVersion": self.contract_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ReasoningCase":
        payload = dict(value or {})
        inference = payload.get("inferenceResult") or payload.get("inference_result") or {}
        judgment = payload.get("aiJudgment") or payload.get("ai_judgment") or {}
        decision = payload.get("finalDecision") or payload.get("final_decision") or {}
        return cls(
            case_id=str(payload.get("caseId") or payload.get("case_id") or ""),
            request_id=str(payload.get("requestId") or payload.get("request_id") or ""),
            deployment_id=str(payload.get("deploymentId") or payload.get("deployment_id") or ""),
            release_fingerprint=str(payload.get("releaseFingerprint") or payload.get("release_fingerprint") or ""),
            validation_cohort_id=str(payload.get("validationCohortId") or payload.get("validation_cohort_id") or ""),
            stage=str(payload.get("stage") or CASE_CREATED),
            fact_delta=FactDelta.from_dict(payload.get("factDelta") or payload.get("fact_delta") or {"version": FACT_DELTA_VERSION}),
            input_fingerprint=str(payload.get("inputFingerprint") or payload.get("input_fingerprint") or ""),
            hypotheses=tuple(
                HypothesisRecord.from_dict(item)
                for item in payload.get("hypotheses") or []
                if isinstance(item, Mapping) and (item.get("hypothesisId") or item.get("hypothesis_id"))
            ),
            inference_result=InferenceResult.from_dict(inference) if inference else None,
            ai_judgment=AIJudgmentResult.from_dict(judgment) if judgment else None,
            final_decision=FinalDecision.from_dict(decision) if decision else None,
            ai_request_id=str(payload.get("aiRequestId") or payload.get("ai_request_id") or ""),
            notification_job_id=str(payload.get("notificationJobId") or payload.get("notification_job_id") or ""),
            stage_history=tuple(dict(item) for item in payload.get("stageHistory") or payload.get("stage_history") or [] if isinstance(item, Mapping)),
            errors=tuple(dict(item) for item in payload.get("errors") or [] if isinstance(item, Mapping)),
            created_at=str(payload.get("createdAt") or payload.get("created_at") or utc_now_iso()),
            updated_at=str(payload.get("updatedAt") or payload.get("updated_at") or ""),
            completed_at=str(payload.get("completedAt") or payload.get("completed_at") or ""),
            version=max(1, int(payload.get("version") or 1)),
            contract_version=str(payload.get("contractVersion") or payload.get("contract_version") or INVESTMENT_REASONING_CONTRACT_VERSION),
        )
