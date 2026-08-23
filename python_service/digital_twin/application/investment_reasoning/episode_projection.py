"""Project every terminal V2 reasoning result into the canonical decision ledger."""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Tuple

from ...domain.investment_brain import (
    DecisionEpisode,
    reasoning_case_decision_episode_id,
    stable_id,
)
from ...domain.investment_reasoning import (
    CASE_BLOCKED,
    CASE_COMPLETED,
    CASE_EXPIRED,
    CASE_FAILED,
    CASE_PUBLISHED,
    CASE_SUPPRESSED,
    CASE_SUPERSEDED,
    CASE_VALIDATED,
    ReasoningCase,
)


PROJECTABLE_STAGES = {
    CASE_VALIDATED,
    CASE_COMPLETED,
    CASE_PUBLISHED,
    CASE_BLOCKED,
    CASE_FAILED,
    CASE_SUPPRESSED,
    CASE_SUPERSEDED,
    CASE_EXPIRED,
}


def _text(value: object) -> str:
    return str(value or "").strip()


def _unique(values: Iterable[object]) -> List[str]:
    rows: List[str] = []
    for value in values or []:
        item = _text(value)
        if item and item not in rows:
            rows.append(item)
    return rows


def _targets(reasoning_case: ReasoningCase) -> List[Tuple[str, str, object]]:
    rows: List[Tuple[str, str, object]] = []
    for synthesis in reasoning_case.decision_syntheses:
        account_id = _text(synthesis.account_id)
        symbol = _text(synthesis.symbol).upper()
        key = (account_id, symbol)
        if symbol and not any((item[0], item[1]) == key for item in rows):
            rows.append((account_id, symbol, synthesis))
    if rows:
        return rows
    accounts = list(reasoning_case.fact_delta.account_ids) or [""]
    symbols = list(reasoning_case.fact_delta.symbols) or [""]
    return [
        (_text(account_id), _text(symbol).upper(), None)
        for account_id in accounts
        for symbol in symbols
        if _text(symbol)
    ]


def _hypothesis_payload(hypothesis) -> Dict[str, object]:
    stance = (
        "support" if hypothesis.candidate_action in {"BUY", "ADD"}
        else "risk" if hypothesis.candidate_action in {"TRIM", "SELL", "AVOID"}
        else "context"
    )
    return {
        "hypothesisId": hypothesis.hypothesis_id,
        "templateId": hypothesis.family_id or hypothesis.hypothesis_id,
        "templateLabel": hypothesis.label or hypothesis.family_id or "TypeDB hypothesis",
        "claim": hypothesis.label or hypothesis.family_id or hypothesis.hypothesis_id,
        "stance": stance,
        "horizon": hypothesis.horizon or "multi-horizon",
        "evidenceState": "supported" if hypothesis.supporting_evidence_ids else "unresolved",
        "supportingEvidenceIds": list(hypothesis.supporting_evidence_ids),
        "counterEvidenceIds": list(hypothesis.counter_evidence_ids),
        "supportingRuleIds": list(hypothesis.supporting_rule_ids),
        "assumptions": list(hypothesis.assumptions),
        "invalidationConditions": list(hypothesis.invalidation_conditions),
        "causalPathIds": list(hypothesis.causal_trace_ids),
        "verificationStatus": hypothesis.validation_state or "verified-current-generation",
        "status": "active",
        "familyId": hypothesis.family_id,
        "theoryFamily": hypothesis.theory_family,
        "thesisFamily": hypothesis.thesis_family,
        "knowledgeBasis": dict(hypothesis.knowledge_basis or {}),
        "predictionTarget": hypothesis.prediction_target,
        "expectedDirection": hypothesis.expected_direction,
        "expectedOutcome": hypothesis.expected_outcome,
        "competingFamilyIds": list(hypothesis.competing_family_ids),
        "outcomeMetric": hypothesis.outcome_metric,
        "falsificationContract": hypothesis.falsification_contract,
        "inferenceGenerationId": hypothesis.inference_generation_id,
        "candidateAction": hypothesis.candidate_action,
    }


def decision_episode_from_reasoning_case(
    reasoning_case: ReasoningCase,
    account_id: str,
    symbol: str,
    synthesis=None,
) -> DecisionEpisode:
    final = reasoning_case.final_decision
    judgment = reasoning_case.ai_judgment
    inference = reasoning_case.inference_result
    selected_id = _text(
        (final.selected_hypothesis_id if final else "")
        or (judgment.selected_hypothesis_id if judgment else "")
    )
    hypotheses = [_hypothesis_payload(item) for item in reasoning_case.hypotheses]
    selected = next(
        (item for item in reasoning_case.hypotheses if item.hypothesis_id == selected_id),
        None,
    )
    source_abox_id = _text(
        getattr(synthesis, "source_abox_snapshot_id", "")
        or ((inference.source_abox_snapshot_ids or ("",))[0] if inference else "")
    )
    inference_generation_id = _text(
        getattr(synthesis, "inference_generation_id", "")
        or ((inference.inference_generation_ids or ("",))[0] if inference else "")
    )
    failed = reasoning_case.stage in {CASE_BLOCKED, CASE_FAILED}
    action = _text(final.action if final else "NO_ACTION").upper() or "NO_ACTION"
    validation_state = _text(final.validation_state if final else "").lower()
    if validation_state not in {"ready", "conditional", "blocked"}:
        validation_state = "blocked" if failed else "conditional"
    data_state = _text(getattr(synthesis, "data_state", "")).lower()
    if data_state not in {"sufficient", "partial", "insufficient", "unavailable"}:
        data_state = "sufficient" if inference and inference.trace_complete else "partial"
    review_level = _text(getattr(synthesis, "review_level", "")).lower()
    if review_level not in {"normal", "observe", "check", "act", "immediate", "blocked"}:
        review_level = "blocked" if failed else "observe"
    decision_ready = (
        "ready" if validation_state == "ready" and action != "NO_ACTION"
        else "insufficient" if failed
        else "conditional"
    )
    decided_at = reasoning_case.completed_at or reasoning_case.updated_at or reasoning_case.created_at
    episode_id = reasoning_case_decision_episode_id(reasoning_case.case_id, account_id, symbol)
    release_manifest = dict(reasoning_case.release_manifest or {})
    engine_manifest = {
        **release_manifest,
        "deploymentId": reasoning_case.deployment_id,
        "releaseFingerprint": reasoning_case.release_fingerprint,
        "validationCohortId": reasoning_case.validation_cohort_id,
        "reasoningEngineVersion": "independent-reasoning-v2",
        "investmentReasoningCaseId": reasoning_case.case_id,
        "sourceAboxSnapshotId": source_abox_id,
        "inferenceGenerationId": inference_generation_id,
    }
    evidence_ids = list(selected.supporting_evidence_ids) if selected else []
    counter_ids = list(selected.counter_evidence_ids) if selected else []
    reviews = []
    if judgment and selected_id:
        reviews = [{
            "hypothesisId": item.hypothesis_id,
            "verdict": "supported" if item.hypothesis_id == selected_id else "unreviewed",
            "reasoning": judgment.rationale if item.hypothesis_id == selected_id else "",
            "reviewedSupportingEvidenceIds": (
                list(judgment.supporting_evidence_ids)
                if item.hypothesis_id == selected_id else []
            ),
            "reviewedCounterEvidenceIds": (
                list(judgment.opposing_evidence_ids)
                if item.hypothesis_id == selected_id else []
            ),
        } for item in reasoning_case.hypotheses]
    payload = {
        "episodeId": episode_id,
        "accountId": account_id,
        "symbol": symbol,
        "subjectName": symbol,
        "question": {
            "questionId": stable_id("investment-question-v2", reasoning_case.case_id, account_id, symbol),
            "text": symbol + "의 최신 사실 변화가 투자 판단을 바꾸는가?",
            "subjectSymbol": symbol,
            "subjectName": symbol,
            "accountId": account_id,
            "askedAt": reasoning_case.created_at,
            "source": "independent-reasoning-v2",
        },
        "hypothesisSet": {
            "hypothesisSetId": stable_id("hypothesis-set-v2", reasoning_case.case_id, account_id, symbol),
            "subjectSymbol": symbol,
            "hypotheses": hypotheses,
            "comparisonRequired": bool(hypotheses),
            "minimumComparisonCount": min(3, len(hypotheses)),
            "comparisonPolicy": "typedb-decision-synthesis",
            "inferenceGenerationId": inference_generation_id,
            "createdAt": reasoning_case.updated_at or reasoning_case.created_at,
        },
        "action": action,
        "reviewLevel": review_level,
        "dataState": data_state,
        "validationState": validation_state,
        "decisionReadiness": decision_ready,
        "selectedHypothesisId": selected_id,
        "hypothesisReviews": reviews,
        "hypothesisComparisonState": "partial" if reviews else "unavailable",
        "hypothesisSelectionSource": "ai-v2" if judgment and selected_id else "not-selected",
        "decisionGuardrails": [],
        "decisionAbstention": ({
            "abstained": True,
            "reason": _text((reasoning_case.errors or ({},))[-1].get("reason"))
            if reasoning_case.errors else "No validated final hypothesis selection.",
        } if not selected_id else {}),
        "inferenceGenerationId": inference_generation_id,
        "portfolioId": "portfolio:" + (account_id or "default"),
        "sourceAboxSnapshotId": source_abox_id,
        "evidenceIds": evidence_ids,
        "counterEvidenceIds": counter_ids,
        "unresolvedQuestions": _unique(
            list(getattr(synthesis, "next_checks", ()) or ())
            + list(judgment.next_observations if judgment else ())
        ),
        "decisionSummary": _text(
            (final.reason if final else "")
            or (reasoning_case.errors[-1].get("reason") if reasoning_case.errors else "")
            or reasoning_case.stage
        ),
        "investmentView": _text(judgment.rationale if judgment else (final.reason if final else "")),
        "executionDecision": action,
        "decidedAt": decided_at,
        "status": "active" if reasoning_case.stage in {CASE_VALIDATED, CASE_COMPLETED, CASE_PUBLISHED} else reasoning_case.stage.lower(),
        "source": "v2-reasoning-case",
        "factsAtDecision": {
            "investmentReasoningCaseId": reasoning_case.case_id,
            "reasoningCaseStage": reasoning_case.stage,
            "factDelta": reasoning_case.fact_delta.to_dict(),
            "engineManifest": engine_manifest,
            "inferenceResult": inference.to_dict() if inference else {},
            "decisionSynthesis": synthesis.to_dict() if synthesis else {},
            "aiJudgment": judgment.to_dict() if judgment else {},
            "finalDecision": final.to_dict() if final else {},
            "pointInTimeInputFingerprint": reasoning_case.input_fingerprint,
            "calibrationPolicy": {
                "eligible": False,
                "reason": "explicit-hypothesis-outcome-contract-required",
            },
        },
        "engineVersion": "independent-reasoning-v2",
    }
    return DecisionEpisode.from_dict(payload)


class V2DecisionEpisodeProjector:
    """Idempotent adapter from ReasoningCase to the shared decision history."""

    def __init__(self, decision_episode_store=None):
        self.decision_episode_store = decision_episode_store

    def project(self, reasoning_case: ReasoningCase) -> List[DecisionEpisode]:
        if (
            not self.decision_episode_store
            or reasoning_case.stage not in PROJECTABLE_STAGES
        ):
            return []
        saved = []
        for account_id, symbol, synthesis in _targets(reasoning_case):
            episode = decision_episode_from_reasoning_case(
                reasoning_case,
                account_id,
                symbol,
                synthesis,
            )
            saved.append(self.decision_episode_store.save(episode))
        return saved
