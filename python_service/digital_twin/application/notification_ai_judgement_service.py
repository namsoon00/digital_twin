"""Shared, evidence-bound AI judgement path for investment notifications."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
from typing import Callable, Dict, Optional

from ..domain.context_observation_notifications import typedb_context_observation_contract
from ..domain.investment_decision_actionability import investment_decision_actionability
from ..domain.investment_insight_assessment import investment_insight_assessment
from ..domain.message_types import INVESTMENT_INSIGHT
from ..domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from ..domain.notification_ai_gate_text import parse_ai_response_json
from ..domain.notification_ai_inference_packet import (
    NotificationAIInferencePacket,
    build_notification_ai_inference_packet,
)
from ..domain.notification_narrative import (
    normalize_narrative_claims,
    resolved_narrative_claim_evidence_contract,
)


class NotificationAIContractError(ValueError):
    """The model answered, but its result is not safe to publish as AI advice."""


def _actionability_contract_error(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
) -> str:
    if str(context.get("messageType") or "") != INVESTMENT_INSIGHT:
        return ""
    modern_contract = bool(
        context.get("notificationAiDecisionContractVersion")
        or context.get("_notificationAiPreparedDecisionCore")
        or context.get("investmentSubjectDecisionCaseId")
    )
    if not modern_contract:
        return ""
    assessment = investment_decision_actionability(context, response)
    gaps = list(assessment.get("gaps") or [])
    if not gaps:
        return ""
    return "investment decision actionability contract failed: " + ", ".join(gaps)


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
        narrative_only = str(
            context.get("notificationAiReviewMode") or ""
        ).strip().lower() == "context-narrative"
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
        if narrative_only:
            if not hypothesis_ids and selected_id:
                return "selectedHypothesisId is not present in the empty routed TypeDB hypothesis set."
            return ""
        selected_hypothesis = next((
            item
            for item in hypothesis_set.get("hypotheses") or []
            if isinstance(item, dict) and str(item.get("hypothesisId") or "").strip() == selected_id
        ), {})
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
        hypothesis_action = str(
            selected_hypothesis.get("candidateAction")
            or selected_hypothesis.get("candidate_action")
            or ""
        ).upper().strip()
        if (
            hypothesis_action
            and action != hypothesis_action
            and not str(getattr(response, "disagreement_reason", "") or "").strip()
        ):
            return (
                "The selected action differs from the selected TypeDB hypothesis "
                "without an explicit disagreement reason."
            )
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
            return _actionability_contract_error(context, response)

    reasoning_case = context.get("investmentReasoningCase")
    if not isinstance(reasoning_case, dict) or not reasoning_case:
        return _actionability_contract_error(context, response)
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
        return _actionability_contract_error(context, response)
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
    candidate_actions = {
        str(alternative.get("action") or "").upper().strip()
        for synthesis in applicable
        for alternative in synthesis.get("alternatives") or []
        if isinstance(alternative, dict)
        and selected_id in {
            str(value or "") for value in alternative.get("hypothesisIds") or []
        }
        and str(alternative.get("action") or "").strip()
    }
    if (
        len(candidate_actions) == 1
        and action not in candidate_actions
        and not str(getattr(response, "disagreement_reason", "") or "").strip()
    ):
        return (
            "The selected action differs from the selected TypeDB hypothesis "
            "without an explicit disagreement reason."
        )
    return _actionability_contract_error(context, response)


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


def refresh_investment_insight_assessment(
    response: NotificationAIValidatedResponse,
) -> None:
    """Rebuild the insight from the final packet-verified claim set."""

    response.insight_assessment = investment_insight_assessment(
        {"insightAssessment": dict(response.insight_assessment or {})},
        hypotheses=response.hypotheses,
        selected_hypothesis_id=response.selected_hypothesis_id,
        research_lead_hypothesis_id=response.research_lead_hypothesis_id,
        narrative_claims=response.narrative_claims,
        causal_chain=response.causal_chain,
        comparison_state=response.hypothesis_comparison_state,
        validation_state=response.validation_state,
        data_state=response.data_state,
        decision_readiness=response.decision_readiness,
        counter_evidence_status=response.counter_evidence_status,
        invalidation_condition=response.invalidation_condition,
        follow_up_conditions=response.follow_up_conditions,
    )


def _structured_claim_evidence_ids(
    prepared_core: Dict[str, object],
    packet: NotificationAIInferencePacket,
    section: str,
) -> list:
    claim_contract = resolved_narrative_claim_evidence_contract(
        prepared_core.get("narrativeClaimContract"),
        prepared_core.get("evidenceLedger") or [],
    )
    recommended = claim_contract.get("recommendedEvidenceIdsBySection")
    recommended = recommended if isinstance(recommended, dict) else {}
    allowed = claim_contract.get("allowedEvidenceIdsBySection")
    allowed = allowed if isinstance(allowed, dict) else {}
    packet_ids = set(packet.evidence_ids)
    permitted_ids = {
        str(value or "").strip()
        for value in allowed.get(section) or []
        if str(value or "").strip() in packet_ids
    }
    evidence_ids = []
    for value in [
        *(recommended.get(section) or []),
        *(allowed.get(section) or []),
    ]:
        evidence_id = str(value or "").strip()
        if (
            evidence_id
            and evidence_id in packet_ids
            and evidence_id in permitted_ids
            and evidence_id not in evidence_ids
        ):
            evidence_ids.append(evidence_id)
        if len(evidence_ids) >= 4:
            break
    ledger_by_id = {
        str(item.get("evidenceId") or ""): item
        for item in prepared_core.get("evidenceLedger") or []
        if isinstance(item, dict) and str(item.get("evidenceId") or "")
    }
    if not any(
        str((ledger_by_id.get(evidence_id) or {}).get("kind") or "")
        not in {"inference", "data-limit"}
        for evidence_id in evidence_ids
    ):
        return []
    return evidence_ids


def recover_structured_investment_insight_claims(
    context: Dict[str, object],
    packet: NotificationAIInferencePacket,
    response: NotificationAIValidatedResponse,
) -> Dict[str, object]:
    """Recover omitted claim rows from the model's own structured insight.

    This repairs schema duplication only.  It reuses the exact model text,
    attaches packet-approved observed evidence, and runs the normal validator;
    it never creates a market assertion or changes execution authority.
    """

    if str(context.get("messageType") or "") != INVESTMENT_INSIGHT:
        return {"status": "not-applicable"}
    prepared_core = context.get("_notificationAiPreparedDecisionCore")
    prepared_core = prepared_core if isinstance(prepared_core, dict) else packet.decision_core
    hypothesis_set = prepared_core.get("hypothesisSet")
    hypothesis_set = hypothesis_set if isinstance(hypothesis_set, dict) else {}
    if not any(
        isinstance(item, dict) and str(item.get("hypothesisId") or "").strip()
        for item in hypothesis_set.get("hypotheses") or []
    ):
        return {"status": "not-applicable"}
    raw_payload = parse_ai_response_json(str(response.raw_response or ""))
    raw_assessment = (
        raw_payload.get("insightAssessment")
        or raw_payload.get("insight_assessment")
        or {}
    )
    raw_assessment = raw_assessment if isinstance(raw_assessment, dict) else {}
    existing_sections = response.verified_claim_sections
    candidates = []
    candidate_sources = {}
    required_values = (
        ("view", raw_assessment.get("dominantThesis") or raw_assessment.get("dominant_thesis")),
        ("mechanism", raw_assessment.get("causalMechanism") or raw_assessment.get("causal_mechanism")),
        ("implication", raw_assessment.get("investmentImplication") or raw_assessment.get("investment_implication")),
    )
    for section, value in required_values:
        text = str(value or "").strip()
        if text and section not in existing_sections:
            candidates.append((section, text))
            candidate_sources[(section, text)] = "structured-insight"
    if "view" not in existing_sections and not any(
        section == "view" for section, _text in candidates
    ):
        verified_implication = next((
            str(item.get("text") or "").strip()
            for item in response.narrative_claims or []
            if isinstance(item, dict)
            and str(item.get("section") or "").strip() == "implication"
            and str(item.get("text") or "").strip()
        ), "")
        if verified_implication:
            # Some otherwise valid model responses express the investment
            # view only in the implication claim. Reuse that exact verified
            # sentence with view-approved evidence instead of spending a
            # second max-reasoning model call to duplicate schema content.
            candidates.insert(0, ("view", verified_implication))
            candidate_sources[("view", verified_implication)] = (
                "verified-implication-claim"
            )
    if "catalyst" not in existing_sections:
        catalysts = raw_assessment.get("catalysts") or []
        if not isinstance(catalysts, (list, tuple)):
            catalysts = [catalysts]
        for value in catalysts[:2]:
            text = str(value or "").strip()
            if text:
                candidates.append(("catalyst", text))
                candidate_sources[("catalyst", text)] = "structured-insight"
    if not candidates:
        missing_required_sections = sorted(
            {"view", "mechanism", "implication"} - existing_sections
        )
        if missing_required_sections and not raw_assessment:
            return {
                "status": "unavailable",
                "reason": "structured-insight-missing",
                "sections": missing_required_sections,
            }
        return {"status": "not-required"}

    additions = []
    unavailable_sections = []
    for section, text in candidates:
        evidence_ids = _structured_claim_evidence_ids(
            prepared_core,
            packet,
            section,
        )
        if not evidence_ids:
            unavailable_sections.append(section)
            continue
        claim_id = "claim:structured-insight:" + hashlib.sha256(
            json.dumps(
                {"section": section, "text": text, "evidenceIds": evidence_ids},
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:20]
        additions.append({
            "claimId": claim_id,
            "section": section,
            "text": text,
            "evidenceIds": evidence_ids,
        })
    if not additions:
        return {
            "status": "unavailable",
            "reason": "observable-evidence-missing",
            "sections": sorted(set(unavailable_sections)),
        }

    claims, validation = normalize_narrative_claims(
        context,
        {"narrativeClaims": [*(response.narrative_claims or []), *additions]},
        writer_kind="ai",
    )
    validation["inferencePacketId"] = packet.packet_id
    validation["evidenceFingerprint"] = packet.evidence_fingerprint
    response.narrative_claims = claims
    response.claim_validation = validation
    added_ids = {item["claimId"] for item in additions}
    repaired_sections = sorted({
        str(item.get("section") or "")
        for item in claims
        if item.get("claimId") in added_ids
    })
    return {
        "status": "repaired" if repaired_sections else "rejected",
        "sections": repaired_sections,
        "unavailableSections": sorted(set(unavailable_sections)),
        "sourceSections": {
            section: candidate_sources.get((section, text), "")
            for section, text in candidates
            if section in repaired_sections
        },
    }


def recover_structured_next_condition_claim(
    context: Dict[str, object],
    packet: NotificationAIInferencePacket,
    response: NotificationAIValidatedResponse,
) -> Dict[str, object]:
    """Bind an omitted narrative claim to already-returned structured output.

    The model can return a valid ``nextActionPlan`` while forgetting to repeat
    it in ``narrativeClaims``.  A second model call is unnecessary in that
    narrow case: reuse the returned text, attach only packet-approved evidence,
    and run the ordinary claim validator again.  This never invents a market
    fact or changes the selected action.
    """

    if str(context.get("messageType") or "") != INVESTMENT_INSIGHT:
        return {"status": "not-applicable"}
    if response.verified_claim_sections.intersection({"next-condition", "limitation"}):
        return {"status": "not-required"}
    structured_sources = [
        ("nextActionPlan", response.next_action_plan),
        ("invalidationCondition", response.invalidation_condition),
        *(
            ("nextChecks[" + str(index) + "]", value)
            for index, value in enumerate(response.next_checks or [])
        ),
    ]
    candidates = []
    seen_texts = set()
    for source_field, value in structured_sources:
        text = str(value or "").strip()
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        candidates.append((source_field, text))
    if not candidates:
        return {"status": "unavailable", "reason": "structured-next-condition-missing"}

    prepared_core = context.get("_notificationAiPreparedDecisionCore")
    prepared_core = prepared_core if isinstance(prepared_core, dict) else packet.decision_core
    claim_contract = resolved_narrative_claim_evidence_contract(
        prepared_core.get("narrativeClaimContract"),
        prepared_core.get("evidenceLedger") or [],
    )
    recommended = claim_contract.get("recommendedEvidenceIdsBySection")
    recommended = recommended if isinstance(recommended, dict) else {}
    allowed = claim_contract.get("allowedEvidenceIdsBySection")
    allowed = allowed if isinstance(allowed, dict) else {}
    packet_ids = set(packet.evidence_ids)
    permitted_ids = {
        str(value or "")
        for value in allowed.get("next-condition") or []
        if str(value or "") in packet_ids
    }
    evidence_ids = []
    for value in [
        *(recommended.get("next-condition") or []),
        *(allowed.get("next-condition") or []),
    ]:
        evidence_id = str(value or "").strip()
        if (
            evidence_id
            and evidence_id in packet_ids
            and (not permitted_ids or evidence_id in permitted_ids)
            and evidence_id not in evidence_ids
        ):
            evidence_ids.append(evidence_id)
        if len(evidence_ids) >= 4:
            break
    ledger_by_id = {
        str(item.get("evidenceId") or ""): item
        for item in prepared_core.get("evidenceLedger") or []
        if isinstance(item, dict) and str(item.get("evidenceId") or "")
    }
    if not evidence_ids or not any(
        str((ledger_by_id.get(evidence_id) or {}).get("kind") or "") != "inference"
        for evidence_id in evidence_ids
    ):
        return {"status": "unavailable", "reason": "observable-evidence-missing"}

    attempts = []
    for source_field, text in candidates:
        claim_id = "claim:structured-next:" + hashlib.sha256(
            json.dumps(
                {"text": text, "evidenceIds": evidence_ids},
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:20]
        claims, validation = normalize_narrative_claims(
            context,
            {
                "narrativeClaims": [
                    *(response.narrative_claims or []),
                    {
                        "claimId": claim_id,
                        "section": "next-condition",
                        "text": text,
                        "evidenceIds": evidence_ids,
                    },
                ]
            },
            writer_kind="ai",
        )
        validation["inferencePacketId"] = packet.packet_id
        validation["evidenceFingerprint"] = packet.evidence_fingerprint
        response.narrative_claims = claims
        response.claim_validation = validation
        repaired = any(
            item.get("claimId") == claim_id and item.get("section") == "next-condition"
            for item in claims
        )
        candidate_validation = next((
            item
            for item in validation.get("validations") or []
            if item.get("claimId") == claim_id
        ), {})
        attempt = {
            "status": "repaired" if repaired else "rejected",
            "claimId": claim_id,
            "sourceField": source_field,
            "evidenceIds": evidence_ids,
            "reasons": list(candidate_validation.get("reasons") or []),
        }
        attempts.append(attempt)
        if repaired:
            return {
                "status": "repaired",
                "claimId": claim_id,
                "sourceField": source_field,
                "evidenceIds": evidence_ids,
                "attempts": attempts,
            }
    return {
        "status": "rejected",
        "claimId": attempts[-1]["claimId"],
        "sourceField": attempts[-1]["sourceField"],
        "evidenceIds": evidence_ids,
        "attempts": attempts,
    }


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
    prepared_core = context.get("_notificationAiPreparedDecisionCore")
    prepared_core = prepared_core if isinstance(prepared_core, dict) else packet.decision_core
    hypothesis_set = prepared_core.get("hypothesisSet")
    hypothesis_set = hypothesis_set if isinstance(hypothesis_set, dict) else {}
    hypothesis_backed = bool([
        item for item in hypothesis_set.get("hypotheses") or []
        if isinstance(item, dict) and str(item.get("hypothesisId") or "").strip()
    ])
    missing = []
    if "view" not in sections:
        missing.append("view")
    if hypothesis_backed and "mechanism" not in sections:
        missing.append("mechanism")
    if hypothesis_backed and "implication" not in sections:
        missing.append("implication")
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
    if hypothesis_backed and (response.insight_assessment or {}).get("publishable") is not True:
        reasons = ", ".join(
            str(value or "")
            for value in (response.insight_assessment or {}).get("validationReasons") or []
            if str(value or "")
        )
        return "investment insight assessment is not publishable" + (
            ": " + reasons if reasons else "."
        )
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
    marker = "DecisionCore:\n"
    decision_core = str(prompt or "").split(marker, 1)[1] if marker in str(prompt or "") else "{}"
    previous = str(getattr(response, "raw_response", "") or "")[:8 * 1024]
    required_shape = {
        "action": "NO_ACTION|BUY|ADD|HOLD|TRIM|SELL|AVOID",
        "summary": "최종 결론",
        "currentActionPlan": "현재 대응",
        "nextActionPlan": "재관측 조건과 판단 변화",
        "counterEvidenceStatus": "confirmed|none-found|not-checked|unavailable",
        "invalidationCondition": "검증 근거가 연결된 구체적인 무효화 조건",
        "followUpConditions": [{
            "field": "관측 가능한 입력 필드",
            "operator": ">|>=|<|<=|==|!=",
            "threshold": "입력에서 재현 가능한 수치",
            "purpose": "weaken|invalidate|switch",
            "label": "조건 설명",
            "onSatisfied": "성립 시 판단 변화",
        }],
        "hypotheses": [{
            "hypothesisId": "입력 ID",
            "evidenceReviewStatus": "all-input-evidence-reviewed",
            "verdict": "supported|weakened|rejected|unresolved",
            "reasoning": "비교 이유",
        }],
        "selectedHypothesisId": "입력 ID",
        "decisionReadiness": "ready|conditional|insufficient",
        "causalChain": [{
            "driver": "확인 변화",
            "channel": "revenue|cost|cash-flow|valuation|flow|risk",
            "expectedEffect": "영향 경로",
            "evidenceIds": ["허용 근거 ID"],
            "status": "supported|contested|unresolved",
        }],
        "insightAssessment": {
            "direction": "positive|balanced|negative",
            "horizon": "intraday|short-term|medium-term|long-term|multi-horizon",
            "conviction": "tentative|moderate|strong",
            "dominantThesis": "지배 결론",
            "causalMechanism": "관측에서 투자 영향까지의 경로",
            "investmentImplication": "보유자·관심 투자자에게 주는 의미",
            "catalysts": ["강화 사건"],
            "risks": ["반대 시나리오"],
            "invalidationCondition": "무효화 조건",
            "thesisKey": "stable-key",
        },
        "narrativeClaims": [{
            "claimId": "고유 ID",
            "section": "view|mechanism|implication|catalyst|counter|next-condition|limitation",
            "text": "표시 문장",
            "evidenceIds": ["섹션별 허용 근거 ID"],
        }],
    }
    return "\n".join((
        "너는 TypeDB 투자 판단 JSON의 계약 오류만 수정한다. 도구나 파일을 사용하지 않는다.",
        "아래 DecisionCore 밖의 사실을 만들지 말고 JSON 객체 하나만 출력한다.",
        "action은 actionEnvelope 안에서 선택하고 모든 입력 가설을 한 번씩 검토한다.",
        "각 가설의 입력 근거와 반대 근거를 모두 확인한 뒤 evidenceReviewStatus를 all-input-evidence-reviewed로 쓴다. 근거 ID 배열을 응답에 복사하지 않는다.",
        "반대 근거 검사를 마쳤으면 counterEvidenceStatus를 쓴다. confirmed에는 근거 ID가 연결된 counter 문장이 필요하고, 모든 입력을 검토했지만 반대 사실이 없을 때만 none-found를 쓴다. not-checked와 unavailable은 허용되지 않는다.",
        "narrativeClaims는 허용된 evidence ID만 연결하며 가설이 있으면 view, mechanism, implication과 next-condition 또는 limitation을 포함한다.",
        "insightAssessment에는 가장 근거가 강한 방향, 기간, 근거 강도, 지배 가설, 인과 경로, 투자 의미, 촉매, 반대 시나리오와 무효화 조건을 채운다.",
        "무효화 조건은 관측 대상과 변화 방향 또는 입력 임계값을 구체적으로 쓰고 검증된 next-condition 근거와 연결한다. 일반적인 '근거가 사라지면 다시 본다' 문장은 쓰지 않는다.",
        "자료 한계는 conviction과 영향 범위를 낮추되, 가장 잘 지지되는 투자 결론 자체를 없애거나 양쪽 가능성 나열로 대체하지 않는다.",
        "currentActionPlan에는 지금 할 일과 보류할 일을, nextActionPlan에는 실제로 재관측할 가격·거래량·수급·실적·공시·금리·환율과 그 결과에 따른 판단 변화를 쓴다.",
        "BUY·ADD·TRIM·SELL은 decisionReadiness=ready, executionEligibility=eligible, qualification decisionUse=execution, 근거 ID가 있는 supported causalChain을 모두 만족할 때만 선택한다.",
        "필수 응답 골격: " + json.dumps(required_shape, ensure_ascii=False, separators=(",", ":")),
        "검증 오류: " + json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "이전 응답: " + previous,
        "DecisionCore:",
        decision_core,
    ))


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
    execution_spans: Dict[str, object]

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
            "executionSpans": dict(self.execution_spans or {}),
        }


class NotificationAIJudgementService:
    """Prepare, call, validate and optionally repair one AI judgement."""

    def __init__(
        self,
        reviewer,
        settings: Dict[str, object] = None,
        *,
        max_prompt_bytes: int = 0,
        repair_reasoning_effort: str = "max",
        repair_timeout_seconds: Optional[int] = None,
        enforce_contract_for_typed_response: bool = True,
    ):
        self.reviewer = reviewer
        self.settings = dict(settings or {})
        self.max_prompt_bytes = int(max_prompt_bytes or 0)
        normalized_repair_effort = str(repair_reasoning_effort or "max").strip().lower()
        self.repair_reasoning_effort = (
            normalized_repair_effort
            if normalized_repair_effort in {"low", "medium", "high", "max"}
            else "max"
        )
        self.repair_timeout_seconds = (
            max(5, int(repair_timeout_seconds))
            if repair_timeout_seconds not in (None, "", 0, "0")
            else None
        )
        self.enforce_contract_for_typed_response = bool(enforce_contract_for_typed_response)

    def judge(
        self,
        context: Dict[str, object],
        *,
        timeout_seconds: Optional[int] = None,
        profile: Dict[str, object] = None,
        decision_brief: Dict[str, object] = None,
        packet: NotificationAIInferencePacket = None,
        timeout_provider: Callable[[], Optional[int]] = None,
    ) -> NotificationAIJudgementOutcome:
        total_started = time.monotonic()
        preparation_started = total_started
        prepared_packet = packet or build_notification_ai_inference_packet(
            context,
            self.settings,
            max_prompt_bytes=self.max_prompt_bytes,
            profile=profile,
            decision_brief=decision_brief,
        )
        preparation_ms = int((time.monotonic() - preparation_started) * 1000)
        current_timeout = timeout_provider() if timeout_provider else timeout_seconds
        current_timeout = int(current_timeout) if current_timeout not in (None, "", 0, "0") else None
        if current_timeout is not None and current_timeout < 5:
            raise TimeoutError("notification AI execution budget exhausted before model execution")
        review_context = prepared_packet.bind_context(
            context,
            timeout_seconds=current_timeout,
        )
        if profile:
            review_context["notificationAiExecutionProfile"] = dict(profile)
        initial_model_started = time.monotonic()
        response = self.reviewer.review(review_context)
        initial_model_ms = int((time.monotonic() - initial_model_started) * 1000)
        validation_started = time.monotonic()
        ensure_packet_claim_validation(review_context, prepared_packet, response)
        refresh_investment_insight_assessment(response)
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
        structured_insight_repair = recover_structured_investment_insight_claims(
            review_context,
            prepared_packet,
            response,
        )
        if structured_insight_repair.get("status") == "repaired":
            refresh_investment_insight_assessment(response)
            publication_error = narrative_publication_contract_error(
                review_context,
                prepared_packet,
                response,
            )
        structured_claim_repair = {"status": "not-required"}
        if "next-condition-or-limitation" in publication_error:
            structured_claim_repair = recover_structured_next_condition_claim(
                review_context,
                prepared_packet,
                response,
            )
            if structured_claim_repair.get("status") == "repaired":
                refresh_investment_insight_assessment(response)
                publication_error = narrative_publication_contract_error(
                    review_context,
                    prepared_packet,
                    response,
                )
        initial_validation_ms = int((time.monotonic() - validation_started) * 1000)
        repair_attempted = bool(
            (enforce_contract and hypothesis_comparison_needs_repair(context.get("messageType"), response))
            or contract_error
            or publication_error
        )
        repair_succeeded = False
        repair_error = ""
        repair_model_ms = 0
        repair_validation_ms = 0
        repair_reasoning_effort = self.repair_reasoning_effort
        repair_structured_insight_repair = {"status": "not-attempted"}
        repair_structured_claim_repair = {"status": "not-attempted"}
        executed_prompt = prepared_packet.prompt
        if repair_attempted:
            executed_prompt = ai_contract_repair_prompt(
                prepared_packet.prompt,
                response,
                contract_error,
                publication_error,
            )
            repair_remaining = timeout_provider() if timeout_provider else timeout_seconds
            repair_remaining = int(repair_remaining) if repair_remaining not in (None, "", 0, "0") else None
            if repair_remaining is not None and repair_remaining < 5:
                repair_error = "notification AI execution budget exhausted before contract repair"
                repair_remaining = 5
            repair_context = prepared_packet.bind_context(
                context,
                timeout_seconds=(
                    min(self.repair_timeout_seconds, repair_remaining)
                    if self.repair_timeout_seconds is not None and repair_remaining is not None
                    else self.repair_timeout_seconds
                    if self.repair_timeout_seconds is not None
                    else repair_remaining
                ),
            )
            repair_context["_notificationAiPreparedPrompt"] = executed_prompt
            requested_profile = dict(
                profile or context.get("notificationAiExecutionProfile") or {}
            )
            requested_effort = str(
                requested_profile.get("reasoningEffort") or ""
            ).strip().lower()
            if requested_effort in {"low", "medium", "high", "max"}:
                repair_reasoning_effort = requested_effort
            repair_context["notificationAiExecutionProfile"] = {
                **requested_profile,
                "name": "contractRepair",
                "reasoningEffort": repair_reasoning_effort,
            }
            try:
                if repair_error:
                    raise TimeoutError(repair_error)
                repair_model_started = time.monotonic()
                response = self.reviewer.review(repair_context)
                repair_model_ms = int((time.monotonic() - repair_model_started) * 1000)
                repair_validation_started = time.monotonic()
                ensure_packet_claim_validation(repair_context, prepared_packet, response)
                refresh_investment_insight_assessment(response)
                repair_structured_insight_repair = recover_structured_investment_insight_claims(
                    repair_context,
                    prepared_packet,
                    response,
                )
                if repair_structured_insight_repair.get("status") == "repaired":
                    refresh_investment_insight_assessment(response)
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
                if "next-condition-or-limitation" in publication_error:
                    repair_structured_claim_repair = recover_structured_next_condition_claim(
                        repair_context,
                        prepared_packet,
                        response,
                    )
                    if repair_structured_claim_repair.get("status") == "repaired":
                        refresh_investment_insight_assessment(response)
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
                repair_validation_ms = int((time.monotonic() - repair_validation_started) * 1000)
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
            execution_spans={
                "preparationMs": preparation_ms,
                "initialModelMs": initial_model_ms,
                "initialValidationMs": initial_validation_ms,
                "repairModelMs": repair_model_ms,
                "repairValidationMs": repair_validation_ms,
                "repairReasoningEffort": repair_reasoning_effort,
                "structuredInsightRepair": structured_insight_repair,
                "structuredNarrativeRepair": structured_claim_repair,
                "repairStructuredInsightRepair": repair_structured_insight_repair,
                "repairStructuredNarrativeRepair": repair_structured_claim_repair,
                "totalJudgementMs": int((time.monotonic() - total_started) * 1000),
                "modelAttempts": list(getattr(self.reviewer, "execution_history", []) or []),
            },
        )
