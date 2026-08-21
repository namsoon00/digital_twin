"""Shared, evidence-bound AI judgement path for investment notifications."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Callable, Dict

from ..domain.context_observation_notifications import typedb_context_observation_contract
from ..domain.message_types import INVESTMENT_INSIGHT
from ..domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from ..domain.notification_ai_inference_packet import (
    NotificationAIInferencePacket,
    build_notification_ai_inference_packet,
)
from ..domain.notification_narrative import normalize_narrative_claims


class NotificationAIContractError(ValueError):
    """The model answered, but its result is not safe to publish as AI advice."""


def hypothesis_comparison_needs_repair(
    message_type: object,
    response: NotificationAIValidatedResponse,
) -> bool:
    return bool(
        str(message_type or "") == INVESTMENT_INSIGHT
        and getattr(response, "hypotheses", None)
        and str(getattr(response, "hypothesis_comparison_state", "") or "") != "completed"
    )


def ai_response_contract_error(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
) -> str:
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
        explicit_abstention = (
            not hypothesis_ids
            and hypothesis_set.get("comparisonRequired") is False
            and str(hypothesis_set.get("minimumComparisonCount") or "0") == "0"
        )
        if explicit_abstention:
            if selected_id:
                return "selectedHypothesisId is not present in the empty routed TypeDB hypothesis set."
            if getattr(response, "hypotheses", None):
                return "AI returned hypotheses when the routed TypeDB hypothesis set is empty."
            return ""
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


def _claim_validation_ledger_ids(response: NotificationAIValidatedResponse) -> set:
    return {
        str(item.get("evidenceId") or "")
        for item in (response.claim_validation or {}).get("evidenceLedger") or []
        if isinstance(item, dict) and str(item.get("evidenceId") or "")
    }


def ensure_packet_claim_validation(
    context: Dict[str, object],
    packet: NotificationAIInferencePacket,
    response: NotificationAIValidatedResponse,
) -> None:
    """Bind claim verification to the exact ledger that the model received."""

    validation = dict(response.claim_validation or {})
    ledger_ids = _claim_validation_ledger_ids(response)
    if validation.get("version") and ledger_ids == set(packet.evidence_ids):
        validation["inferencePacketId"] = packet.packet_id
        validation["evidenceFingerprint"] = packet.evidence_fingerprint
        response.claim_validation = validation
        return
    claims, validation = normalize_narrative_claims(
        context,
        {"narrativeClaims": list(response.narrative_claims or [])},
        writer_kind="ai",
    )
    response.narrative_claims = claims
    validation["inferencePacketId"] = packet.packet_id
    validation["evidenceFingerprint"] = packet.evidence_fingerprint
    response.claim_validation = validation


def narrative_publication_contract_error(
    context: Dict[str, object],
    packet: NotificationAIInferencePacket,
    response: NotificationAIValidatedResponse,
) -> str:
    if str(context.get("messageType") or "") != INVESTMENT_INSIGHT:
        return ""
    # In-memory test doubles and deterministic adapters may return a typed
    # response directly. Production model responses always retain raw JSON.
    if not str(response.raw_response or "").strip():
        return ""
    validation = dict(response.claim_validation or {})
    if _claim_validation_ledger_ids(response) != set(packet.evidence_ids):
        return "claim validation did not use the inference packet evidence ledger."
    sections = response.verified_claim_sections
    missing = []
    if "view" not in sections:
        missing.append("view")
    if not sections.intersection({"next-condition", "limitation"}):
        missing.append("next-condition-or-limitation")
    if str(response.action or "").upper() in {"BUY", "ADD", "TRIM", "SELL", "AVOID"}:
        if "support" not in sections:
            missing.append("support")
    if bool(typedb_context_observation_contract(context).get("requiresAiNarrative")):
        missing = [
            value for value in missing
            if value not in {"support", "next-condition-or-limitation"}
        ]
    if missing:
        return "required verified narrative sections are missing: " + ", ".join(missing)
    if not response.verified_claim_count:
        return "no verified narrative claim is available for publication."
    return ""


def ai_contract_repair_prompt(
    prompt: str,
    response: NotificationAIValidatedResponse,
    contract_error: str = "",
    publication_error: str = "",
) -> str:
    abstention = dict(getattr(response, "decision_abstention", {}) or {})
    rejected = [
        dict(item)
        for item in (response.claim_validation or {}).get("validations") or []
        if isinstance(item, dict) and item.get("status") == "rejected"
    ]
    audit = {
        "reason": abstention.get("reason") or "AI 발행 계약 미충족",
        "unreviewedHypothesisIds": abstention.get("unreviewedHypothesisIds") or [],
        "invalidHypothesisIds": abstention.get("invalidHypothesisIds") or [],
        "invalidEvidenceIds": abstention.get("invalidEvidenceIds") or [],
        "duplicateHypothesisIds": abstention.get("duplicateHypothesisIds") or [],
        "decisionContractError": str(contract_error or ""),
        "publicationContractError": str(publication_error or ""),
        "rejectedNarrativeClaims": rejected[:8],
    }
    return (
        str(prompt or "")
        + "\n\n이전 응답은 아래 검증 오류로 발행되지 않았다. 같은 DecisionCore만 사용해 새로운 JSON 객체 하나를 출력한다. "
        + "가설 ID와 행동 범위를 지키고, narrativeClaims는 narrativeClaimContract.allowedEvidenceIdsBySection에 있는 ID만 연결한다. "
        + "확인된 사실은 단정적으로, 자료 한계는 limitation으로 쓴다. 확인되지 않은 반대 사실은 만들지 않는다.\n"
        + json.dumps(audit, ensure_ascii=False, sort_keys=True)
    )


# Compatibility name retained for existing callers and tests.
hypothesis_comparison_repair_prompt = ai_contract_repair_prompt


@dataclass(frozen=True)
class NotificationAIJudgementOutcome:
    response: NotificationAIValidatedResponse
    packet: NotificationAIInferencePacket
    executed_prompt: str
    ai_attempted: bool
    repair_attempted: bool
    repair_succeeded: bool
    initial_contract_error: str
    initial_publication_error: str
    final_contract_error: str
    final_publication_error: str
    repair_error: str

    @property
    def executed_prompt_hash(self) -> str:
        return hashlib.sha256(self.executed_prompt.encode("utf-8")).hexdigest()

    @property
    def executed_prompt_bytes(self) -> int:
        return len(self.executed_prompt.encode("utf-8"))

    @property
    def publishable(self) -> bool:
        return bool(
            not self.final_contract_error
            and not self.final_publication_error
            and not self.repair_error
        )

    def audit_dict(self) -> Dict[str, object]:
        return {
            "packet": self.packet.to_audit_dict(),
            "executedPromptHash": self.executed_prompt_hash,
            "executedPromptBytes": self.executed_prompt_bytes,
            "aiAttempted": self.ai_attempted,
            "repair": {
                "attempted": self.repair_attempted,
                "succeeded": self.repair_succeeded,
                "error": self.repair_error,
                "initialContractError": self.initial_contract_error,
                "initialPublicationError": self.initial_publication_error,
                "contractError": self.final_contract_error,
                "publicationError": self.final_publication_error,
            },
            "claimPublication": dict(self.response.claim_validation or {}),
        }


class NotificationAIJudgementService:
    """Prepare, call, validate and optionally repair one AI judgement."""

    def __init__(
        self,
        reviewer,
        settings: Dict[str, object] = None,
        *,
        max_prompt_bytes: int = 0,
        repair_reasoning_effort: str = "low",
        repair_timeout_seconds: int = 60,
        enforce_contract_for_typed_response: bool = True,
    ):
        self.reviewer = reviewer
        self.settings = dict(settings or {})
        self.max_prompt_bytes = int(max_prompt_bytes or 0)
        self.repair_reasoning_effort = str(repair_reasoning_effort or "low")
        self.repair_timeout_seconds = max(5, int(repair_timeout_seconds or 60))
        self.enforce_contract_for_typed_response = bool(enforce_contract_for_typed_response)

    def judge(
        self,
        context: Dict[str, object],
        *,
        timeout_seconds: int = 0,
        profile: Dict[str, object] = None,
        decision_brief: Dict[str, object] = None,
        packet: NotificationAIInferencePacket = None,
        timeout_provider: Callable[[], int] = None,
    ) -> NotificationAIJudgementOutcome:
        prepared_packet = packet or build_notification_ai_inference_packet(
            context,
            self.settings,
            max_prompt_bytes=self.max_prompt_bytes,
            profile=profile,
            decision_brief=decision_brief,
        )
        current_timeout = int(timeout_provider() if timeout_provider else timeout_seconds or 0)
        if current_timeout and current_timeout < 5:
            raise TimeoutError("notification AI delivery deadline exceeded before model execution")
        review_context = prepared_packet.bind_context(
            context,
            timeout_seconds=current_timeout or None,
        )
        response = self.reviewer.review(review_context)
        ensure_packet_claim_validation(review_context, prepared_packet, response)
        enforce_contract = bool(
            self.enforce_contract_for_typed_response
            or str(response.raw_response or "").strip()
        )
        contract_error = ai_response_contract_error(review_context, response) if enforce_contract else ""
        publication_error = narrative_publication_contract_error(
            review_context,
            prepared_packet,
            response,
        )
        initial_contract_error = contract_error
        initial_publication_error = publication_error
        repair_attempted = bool(
            (enforce_contract and hypothesis_comparison_needs_repair(context.get("messageType"), response))
            or contract_error
            or publication_error
        )
        repair_succeeded = False
        repair_error = ""
        executed_prompt = prepared_packet.prompt
        if repair_attempted:
            executed_prompt = ai_contract_repair_prompt(
                prepared_packet.prompt,
                response,
                contract_error,
                publication_error,
            )
            repair_remaining = int(timeout_provider() if timeout_provider else timeout_seconds or 0)
            if repair_remaining and repair_remaining < 5:
                repair_error = "notification AI delivery deadline exceeded before contract repair"
                repair_remaining = 5
            repair_context = prepared_packet.bind_context(
                context,
                timeout_seconds=(
                    min(self.repair_timeout_seconds, repair_remaining)
                    if repair_remaining
                    else self.repair_timeout_seconds
                ),
            )
            repair_context["_notificationAiPreparedPrompt"] = executed_prompt
            repair_context["notificationAiExecutionProfile"] = {
                **dict(profile or context.get("notificationAiExecutionProfile") or {}),
                "name": "contractRepair",
                "reasoningEffort": self.repair_reasoning_effort,
            }
            try:
                if repair_error:
                    raise TimeoutError(repair_error)
                response = self.reviewer.review(repair_context)
                ensure_packet_claim_validation(repair_context, prepared_packet, response)
                enforce_contract = bool(
                    self.enforce_contract_for_typed_response
                    or str(response.raw_response or "").strip()
                )
                contract_error = ai_response_contract_error(repair_context, response) if enforce_contract else ""
                publication_error = narrative_publication_contract_error(
                    repair_context,
                    prepared_packet,
                    response,
                )
                repair_succeeded = bool(
                    not (enforce_contract and hypothesis_comparison_needs_repair(context.get("messageType"), response))
                    and not contract_error
                    and not publication_error
                )
            except Exception as error:  # noqa: BLE001 - caller owns retry/fallback policy.
                repair_error = str(error)[:320]
        if enforce_contract and hypothesis_comparison_needs_repair(context.get("messageType"), response) and not contract_error:
            contract_error = "the routed TypeDB hypothesis comparison is incomplete."
        return NotificationAIJudgementOutcome(
            response=response,
            packet=prepared_packet,
            executed_prompt=executed_prompt,
            ai_attempted=True,
            repair_attempted=repair_attempted,
            repair_succeeded=repair_succeeded,
            initial_contract_error=initial_contract_error,
            initial_publication_error=initial_publication_error,
            final_contract_error=contract_error,
            final_publication_error=publication_error,
            repair_error=repair_error,
        )
