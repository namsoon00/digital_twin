"""Immutable routing contract between TypeDB inference and downstream consumers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Dict, Mapping

from ..context_observation_notifications import (
    context_observation_delivery_decision,
    typedb_context_observation_contract,
    typedb_review_observation_contract,
)


INFERENCE_DISPATCH_VERSION = "investment-inference-dispatch-v1"

PUBLISH_TYPEDB = "PUBLISH_TYPEDB"
HANDOFF_AI = "HANDOFF_AI"
ARCHIVE = "ARCHIVE"
INVALID = "INVALID"

INFERENCE_DISPATCH_ROUTES = frozenset({
    PUBLISH_TYPEDB,
    HANDOFF_AI,
    ARCHIVE,
    INVALID,
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _stable_id(*values: object) -> str:
    material = json.dumps(
        values,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "inference-dispatch:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class InferenceDispatchDecision:
    """One auditable route for a persisted TypeDB subject decision.

    This contract does not decide whether an investment is attractive. It only
    selects the next bounded consumer: deterministic TypeDB publication, AI
    judgement, web history, or contract-error handling.
    """

    decision_id: str
    subject_case_id: str
    account_id: str
    symbol: str
    inference_generation_id: str
    candidate_fingerprint: str
    route: str
    reason_code: str
    reason: str
    source_event_id: str = ""
    details: Dict[str, object] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    version: str = INFERENCE_DISPATCH_VERSION

    @classmethod
    def create(
        cls,
        subject_case,
        route: str,
        reason_code: str,
        reason: str,
        *,
        source_event_id: str = "",
        details: Mapping[str, object] = None,
    ) -> "InferenceDispatchDecision":
        normalized_route = _text(route).upper()
        if normalized_route not in INFERENCE_DISPATCH_ROUTES:
            raise ValueError("Unsupported inference dispatch route: " + normalized_route)
        candidate = getattr(subject_case, "candidate_set", None)
        subject_case_id = _text(getattr(subject_case, "subject_case_id", ""))
        generation_id = _text(getattr(subject_case, "inference_generation_id", ""))
        fingerprint = _text(getattr(candidate, "fingerprint", ""))
        return cls(
            decision_id=_stable_id(
                subject_case_id,
                generation_id,
                fingerprint,
                normalized_route,
            ),
            subject_case_id=subject_case_id,
            account_id=_text(getattr(subject_case, "account_id", "")),
            symbol=_text(getattr(subject_case, "symbol", "")).upper(),
            inference_generation_id=generation_id,
            candidate_fingerprint=fingerprint,
            route=normalized_route,
            reason_code=_text(reason_code),
            reason=_text(reason)[:500],
            source_event_id=_text(source_event_id),
            details=dict(details or {}),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "InferenceDispatchDecision":
        payload = _mapping(value)
        route = _text(payload.get("route")).upper()
        if route not in INFERENCE_DISPATCH_ROUTES:
            route = INVALID
        return cls(
            decision_id=_text(payload.get("decisionId") or payload.get("decision_id")),
            subject_case_id=_text(payload.get("subjectCaseId") or payload.get("subject_case_id")),
            account_id=_text(payload.get("accountId") or payload.get("account_id")),
            symbol=_text(payload.get("symbol")).upper(),
            inference_generation_id=_text(
                payload.get("inferenceGenerationId") or payload.get("inference_generation_id")
            ),
            candidate_fingerprint=_text(
                payload.get("candidateFingerprint") or payload.get("candidate_fingerprint")
            ),
            route=route,
            reason_code=_text(payload.get("reasonCode") or payload.get("reason_code")),
            reason=_text(payload.get("reason"))[:500],
            source_event_id=_text(payload.get("sourceEventId") or payload.get("source_event_id")),
            details=_mapping(payload.get("details")),
            created_at=_text(payload.get("createdAt") or payload.get("created_at")) or _now(),
            version=_text(payload.get("version")) or INFERENCE_DISPATCH_VERSION,
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "decisionId": payload["decision_id"],
            "subjectCaseId": payload["subject_case_id"],
            "accountId": payload["account_id"],
            "symbol": payload["symbol"],
            "inferenceGenerationId": payload["inference_generation_id"],
            "candidateFingerprint": payload["candidate_fingerprint"],
            "route": payload["route"],
            "reasonCode": payload["reason_code"],
            "reason": payload["reason"],
            "sourceEventId": payload["source_event_id"],
            "details": dict(payload["details"] or {}),
            "createdAt": payload["created_at"],
            "version": payload["version"],
        }


def inference_dispatch_decision(
    context: Mapping[str, object],
    subject_case,
    *,
    source_event_id: str = "",
) -> InferenceDispatchDecision:
    """Choose exactly one downstream route from the persisted TypeDB output."""

    values = _mapping(context)
    subject_case_id = _text(getattr(subject_case, "subject_case_id", ""))
    candidate = getattr(subject_case, "candidate_set", None)
    generation_id = _text(getattr(subject_case, "inference_generation_id", ""))
    fingerprint = _text(getattr(candidate, "fingerprint", ""))
    candidate_valid = bool(getattr(candidate, "valid", False))
    if not subject_case_id or not generation_id or not fingerprint or not candidate_valid:
        return InferenceDispatchDecision.create(
            subject_case,
            INVALID,
            "invalid-subject-decision-contract",
            "TypeDB 종목 판단의 세대·후보 식별 계약이 완전하지 않아 후속 처리를 차단했습니다.",
            source_event_id=source_event_id,
        )

    context_observation = typedb_context_observation_contract(values)
    if context_observation:
        delivery = context_observation_delivery_decision(values)
        if _text(delivery.get("decision")).lower() == "send":
            return InferenceDispatchDecision.create(
                subject_case,
                PUBLISH_TYPEDB,
                "material-typedb-observation",
                _text(delivery.get("reason"))
                or "사용자가 확인할 구체적인 변화가 연결된 TypeDB 관찰을 발행합니다.",
                source_event_id=source_event_id,
                details={
                    "observationContract": context_observation,
                    "semanticDeliveryDecision": delivery,
                },
            )
        return InferenceDispatchDecision.create(
            subject_case,
            ARCHIVE,
            _text(delivery.get("suppressionReason")) or "typedb-observation-web-history",
            _text(delivery.get("reason"))
            or "사용자에게 알릴 새 변화가 없어 TypeDB 관찰을 웹 이력에만 저장합니다.",
            source_event_id=source_event_id,
            details={
                "observationContract": context_observation,
                "semanticDeliveryDecision": delivery,
            },
        )

    review_observation = typedb_review_observation_contract(values)
    if review_observation:
        return InferenceDispatchDecision.create(
            subject_case,
            HANDOFF_AI,
            "review-observation-ai-interpretation",
            "행동 권한이 없는 TypeDB 가설을 AI가 투자 의미와 다음 확인 조건으로 해석합니다.",
            source_event_id=source_event_id,
            details={"reviewObservationContract": review_observation},
        )

    synthesis = getattr(subject_case, "synthesis", None)
    eligible_ids = tuple(getattr(candidate, "eligible_hypothesis_ids", ()) or ())
    requires_ai = bool(values.get("requiresAiJudgement")) or bool(
        getattr(synthesis, "action_authority", "") == "originate" and eligible_ids
    )
    if requires_ai and eligible_ids:
        return InferenceDispatchDecision.create(
            subject_case,
            HANDOFF_AI,
            "actionable-candidate-ai-judgement",
            "TypeDB가 만든 행동 후보와 경쟁 가설을 AI 최종 판단 단계로 전달합니다.",
            source_event_id=source_event_id,
            details={"eligibleHypothesisIds": list(eligible_ids)},
        )

    return InferenceDispatchDecision.create(
        subject_case,
        ARCHIVE,
        "no-downstream-publication-value",
        "현재 TypeDB 결과에는 독립 알림이나 AI 판단으로 전달할 새 가치가 없어 웹 이력에 저장합니다.",
        source_event_id=source_event_id,
    )
