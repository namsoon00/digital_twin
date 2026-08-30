"""Project every terminal V2 reasoning result into the canonical decision ledger."""

from __future__ import annotations

from copy import deepcopy
from typing import Dict, Iterable, List, Mapping, Tuple

from ...domain.investment_brain import (
    DecisionEpisode,
    canonical_investment_timestamp,
    reasoning_case_decision_episode_id,
    stable_id,
)
from ...domain.hypothesis_outcome_contract import (
    HYPOTHESIS_OUTCOME_CONTRACT_VERSION,
    outcome_contract_completeness,
    outcome_contract_fingerprint,
)
from ...domain.rule_claim_contract import (
    RuleClaimContract,
    authored_outcome_contract_complete,
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
    SubjectDecisionCase,
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

NO_AI_FINAL_SOURCES = {
    "typedb-delivery-suppressed",
    "typedb-no-material-change",
    "typedb-shadow",
    "typedb-context-observation",
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
        "claimContract": dict(hypothesis.claim_contract or {}),
        "qualification": dict(hypothesis.qualification or {}),
        "predictionTarget": hypothesis.prediction_target,
        "expectedDirection": hypothesis.expected_direction,
        "expectedOutcome": hypothesis.expected_outcome,
        "competingFamilyIds": list(hypothesis.competing_family_ids),
        "outcomeMetric": hypothesis.outcome_metric,
        "falsificationContract": hypothesis.falsification_contract,
        "inferenceGenerationId": hypothesis.inference_generation_id,
        "candidateAction": hypothesis.candidate_action,
    }


def _selected_hypothesis_outcome_contract(
    hypothesis,
    inference_generation_id: str,
    effective_at: str,
    source_fact_independence_key: str = "",
) -> Tuple[Dict[str, object], Dict[str, object]]:
    """Freeze only the exact predictive RuleBox contract carried by V2.

    Re-resolving an old rule against today's catalog would contaminate replay.
    The selected hypothesis must therefore carry the authored contract from the
    generation that produced it or remain explicitly ineligible.
    """

    if not hypothesis:
        return {}, {"eligible": False, "reason": "no-selected-hypothesis"}
    claim = RuleClaimContract.from_dict(hypothesis.claim_contract)
    rule_ids = _unique(hypothesis.supporting_rule_ids)
    if not claim.claim_contract_id:
        return {}, {"eligible": False, "reason": "selected-hypothesis-claim-contract-missing"}
    if claim.rule_id not in rule_ids:
        return {}, {"eligible": False, "reason": "selected-hypothesis-claim-lineage-mismatch"}
    if not claim.is_predictive or claim.decision_authority != "conditional-investment-evidence":
        return {}, {"eligible": False, "reason": "selected-claim-is-not-predictive"}
    if not authored_outcome_contract_complete(claim.outcome_contract):
        return {}, {"eligible": False, "reason": "selected-predictive-contract-incomplete"}
    generation_id = _text(inference_generation_id)
    if not generation_id:
        return {}, {"eligible": False, "reason": "inference-generation-missing"}
    independence_identity = (
        claim.evidence_independence_key
        or hypothesis.family_id
        or hypothesis.hypothesis_id
    )
    independence_event = _text(source_fact_independence_key) or generation_id
    contract = {
        **claim.outcome_contract.to_dict(),
        "contractVersion": HYPOTHESIS_OUTCOME_CONTRACT_VERSION,
        "criteriaOrigin": "rulebox",
        "effectiveAt": canonical_investment_timestamp(effective_at) or effective_at,
        "selectedHypothesisId": hypothesis.hypothesis_id,
        "hypothesisContractId": claim.claim_contract_id,
        "claimContractId": claim.claim_contract_id,
        "sourceRuleIds": rule_ids,
        "inferenceGenerationId": generation_id,
        "sourceFactIndependenceKey": independence_event,
        "marketIndependenceKey": stable_id(
            "market-hypothesis-outcome-episode",
            independence_identity,
            independence_event,
        ),
        "accountIndependenceKey": stable_id(
            "account-hypothesis-outcome-episode",
            hypothesis.account_id or "default",
            independence_identity,
            independence_event,
        ),
        "predictionTarget": claim.prediction_target or hypothesis.prediction_target,
        "expectedDirection": claim.expected_direction or hypothesis.expected_direction,
        "expectedOutcome": claim.expected_outcome or hypothesis.expected_outcome,
        "outcomeMetric": claim.outcome_metric or hypothesis.outcome_metric,
        "falsificationContract": (
            claim.falsification_contract or hypothesis.falsification_contract
        ),
    }
    contract["contractFingerprint"] = outcome_contract_fingerprint(contract)
    completeness = outcome_contract_completeness(contract)
    if not completeness.get("complete"):
        return contract, {
            "eligible": False,
            "reason": "frozen-outcome-contract-incomplete",
            "missing": list(completeness.get("missing") or []),
        }
    return contract, {
        "eligible": True,
        "reason": "selected-predictive-rulebox-contract-frozen",
        "contractFingerprint": contract["contractFingerprint"],
        "hypothesisContractId": claim.claim_contract_id,
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
    final_source = _text(final.source if final else "")
    if judgment and selected_id:
        ai_execution_state = "completed"
    elif judgment:
        ai_execution_state = "abstained"
    elif final_source == "typedb-context-observation":
        ai_execution_state = "not-required"
    elif final_source in NO_AI_FINAL_SOURCES:
        ai_execution_state = "not-run"
    else:
        ai_execution_state = "failed" if failed else "not-run"
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
    source_fact_independence_key = stable_id(
        "reasoning-source-fact-delta",
        list(reasoning_case.fact_delta.source_event_ids),
        reasoning_case.fact_delta.source_observed_at,
        symbol,
        list(reasoning_case.fact_delta.fact_types),
    ) if reasoning_case.fact_delta.source_event_ids else inference_generation_id
    outcome_contract, calibration_policy = _selected_hypothesis_outcome_contract(
        selected,
        inference_generation_id,
        decided_at,
        source_fact_independence_key,
    )
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
        reviews = [dict(item) for item in judgment.hypothesis_reviews]
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
        "hypothesisComparisonState": (
            "completed" if ai_execution_state == "completed"
            else "incomplete" if ai_execution_state == "abstained"
            else "unavailable"
        ),
        "hypothesisSelectionSource": "ai-v2" if judgment and selected_id else "not-selected",
        "aiExecution": {
            "version": "ai-judgment-execution-v1",
            "state": ai_execution_state,
            "source": final_source,
            "reason": _text(final.reason if final else ""),
            "attempted": bool(judgment),
        },
        "decisionGuardrails": [],
        "decisionAbstention": ({
            "abstained": True,
            "reason": _text((reasoning_case.errors or ({},))[-1].get("reason"))
            if reasoning_case.errors else "No validated final hypothesis selection.",
        } if ai_execution_state == "abstained" else {}),
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
            **({"hypothesisOutcomeContract": outcome_contract} if outcome_contract else {}),
            "calibrationPolicy": calibration_policy,
        },
        "engineVersion": "independent-reasoning-v2",
    }
    return DecisionEpisode.from_dict(payload)


def decision_episode_from_subject_case(
    reasoning_case: ReasoningCase,
    subject_case: SubjectDecisionCase,
) -> DecisionEpisode:
    """Build one episode from one immutable account/subject candidate set."""

    scoped_batch = deepcopy(reasoning_case)
    scoped_batch.hypotheses = tuple(subject_case.candidate_set.hypotheses)
    scoped_batch.decision_syntheses = (subject_case.synthesis,)
    scoped_batch.ai_judgment = subject_case.ai_judgment
    scoped_batch.final_decision = subject_case.final_decision
    episode = decision_episode_from_reasoning_case(
        scoped_batch,
        subject_case.account_id,
        subject_case.symbol,
        subject_case.synthesis,
    )
    episode.facts_at_decision.update({
        "investmentReasoningBatchCaseId": reasoning_case.case_id,
        "investmentSubjectDecisionCaseId": subject_case.subject_case_id,
        "candidateSetId": subject_case.candidate_set.candidate_set_id,
        "candidateFingerprint": subject_case.candidate_set.fingerprint,
        "candidateSetSnapshot": subject_case.candidate_set.to_dict(),
    })
    return episode


def decision_episode_outcome_contract_readiness(episode: DecisionEpisode) -> Dict[str, object]:
    """Verify that a final decision can enter the outcome feedback loop."""

    facts = episode.facts_at_decision if isinstance(episode.facts_at_decision, Mapping) else {}
    calibration = (
        dict(facts.get("calibrationPolicy") or {})
        if isinstance(facts.get("calibrationPolicy"), Mapping)
        else {}
    )
    contract = (
        dict(facts.get("hypothesisOutcomeContract") or {})
        if isinstance(facts.get("hypothesisOutcomeContract"), Mapping)
        else {}
    )
    completeness = outcome_contract_completeness(contract)
    missing = list(completeness.get("missing") or [])
    expected_fingerprint = outcome_contract_fingerprint(contract) if completeness.get("complete") else ""
    actual_fingerprint = _text(contract.get("contractFingerprint"))
    fingerprint_valid = bool(expected_fingerprint and actual_fingerprint == expected_fingerprint)
    if completeness.get("complete") and not fingerprint_valid:
        missing.append("contract-fingerprint-mismatch")
    ready = bool(
        _text(episode.selected_hypothesis_id)
        and calibration.get("eligible") is True
        and completeness.get("complete")
        and fingerprint_valid
    )
    reason = "outcome-contract-ready"
    if not _text(episode.selected_hypothesis_id):
        reason = "no-selected-hypothesis"
    elif calibration.get("eligible") is not True:
        reason = _text(calibration.get("reason")) or "calibration-ineligible"
    elif missing:
        reason = "outcome-contract-incomplete"
    return {
        "ready": ready,
        "reason": reason,
        "missing": _unique(missing),
        "contractFingerprint": actual_fingerprint,
        "expectedContractFingerprint": expected_fingerprint,
        "horizonCount": len(contract.get("outcomeHorizonMinutes") or []),
    }


class V2DecisionEpisodeProjector:
    """Idempotent adapter from ReasoningCase to the shared decision history."""

    def __init__(
        self,
        decision_episode_store=None,
        hypothesis_proposal_request_store=None,
    ):
        self.decision_episode_store = decision_episode_store
        self.hypothesis_proposal_request_store = hypothesis_proposal_request_store

    @staticmethod
    def hypothesis_gap_request(
        reasoning_case: ReasoningCase,
        episode: DecisionEpisode,
        synthesis=None,
    ) -> Dict[str, object]:
        if not reasoning_case.hypotheses:
            return {}
        eligible_ids = set(getattr(synthesis, "eligible_hypothesis_ids", ()) or ())
        reasons = []
        if len(eligible_ids) < 2:
            reasons.append("fewer-than-two-independent-predictive-hypotheses")
        if not episode.selected_hypothesis_id:
            reasons.append("no-selected-hypothesis")
        if not reasons:
            return {}
        hypothesis_rows = [_hypothesis_payload(item) for item in reasoning_case.hypotheses]
        evidence_ids = _unique(
            evidence_id
            for item in reasoning_case.hypotheses
            for evidence_id in (
                list(item.supporting_evidence_ids)
                + list(item.counter_evidence_ids)
                + list(item.causal_trace_ids)
            )
        )
        proof_traces = []
        proof_relations = []
        inference = reasoning_case.inference_result
        for evaluation in list(getattr(inference, "rule_evaluations", ()) or ())[:20]:
            proof = evaluation.proof.to_dict()
            trace_id = _text(proof.get("trace_id") or proof.get("proof_id"))
            proof_traces.append({
                **proof,
                "id": trace_id,
                "ruleId": evaluation.rule_id,
                "selected": bool(evaluation.selected),
            })
            for condition in list(evaluation.proof.conditions or ()):
                relation_id = _text(condition.relation_id) or stable_id(
                    "hypothesis-gap-condition-evidence",
                    evaluation.rule_id,
                    condition.condition_id,
                    condition.field,
                    condition.observed_value,
                )
                proof_relations.append({
                    "id": relation_id,
                    "type": "RULE_CONDITION_EVIDENCE",
                    "ruleId": evaluation.rule_id,
                    "conditionId": condition.condition_id,
                    "field": condition.field,
                    "operator": condition.operator,
                    "expectedValue": condition.expected_value,
                    "observedValue": condition.observed_value,
                    "source": condition.source,
                    "sourceAsOf": condition.source_as_of,
                    "freshness": condition.freshness,
                })
        proof_relation_ids = {
            _text(item.get("id"))
            for item in proof_relations
            if _text(item.get("id"))
        }
        proposal_relations = proof_relations[:40]
        proposal_relations.extend(
            {"id": evidence_id, "type": "GRAPH_EVIDENCE_REFERENCE"}
            for evidence_id in evidence_ids
            if evidence_id not in proof_relation_ids
        )
        gap_fingerprint = stable_id(
            "hypothesis-gap",
            episode.symbol,
            sorted(eligible_ids),
            sorted(
                rule_id
                for item in reasoning_case.hypotheses
                for rule_id in item.supporting_rule_ids
            ),
            sorted(evidence_ids),
            reasons,
        )
        request_id = stable_id(
            "hypothesis-proposal-request",
            episode.account_id,
            episode.symbol,
            gap_fingerprint,
        )
        return {
            "requestId": request_id,
            "accountId": episode.account_id,
            "symbol": episode.symbol,
            "gapFingerprint": gap_fingerprint,
            "gapReasons": reasons,
            "sourceDecisionEpisodeId": episode.episode_id,
            "inferenceGenerationId": episode.inference_generation_id,
            "question": episode.question.to_dict(),
            "hypothesisSet": {
                **episode.hypothesis_set.to_dict(),
                "hypotheses": hypothesis_rows,
            },
            "researchRun": {},
            "relationContext": {
                "inferenceGenerationId": episode.inference_generation_id,
                "graphStoreInference": {
                    "relations": proposal_relations[:40],
                    "traces": proof_traces[:20] or [
                        {"id": trace_id, "status": "reference-only"}
                        for item in reasoning_case.hypotheses
                        for trace_id in item.causal_trace_ids
                    ][:20],
                },
            },
            "governance": "review-required-no-automatic-rulebox-deployment",
        }

    def project(self, reasoning_case: ReasoningCase) -> List[DecisionEpisode]:
        if (
            not self.decision_episode_store
            or reasoning_case.stage not in PROJECTABLE_STAGES
        ):
            return []
        final = reasoning_case.final_decision
        if (
            not final
            or str(final.action or "").upper() == "NO_ACTION"
            or not str(final.selected_hypothesis_id or "").strip()
            or str(final.source or "") != "ai-judgment"
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
            saved_episode = self.decision_episode_store.save(episode)
            saved.append(saved_episode)
            enqueue = getattr(
                self.hypothesis_proposal_request_store,
                "enqueue_hypothesis_proposal_request",
                None,
            )
            gap_request = self.hypothesis_gap_request(
                reasoning_case,
                saved_episode,
                synthesis,
            )
            if callable(enqueue) and gap_request:
                enqueue(gap_request)
        return saved


def hypothesis_gap_request_from_subject_case(
    reasoning_case: ReasoningCase,
    subject_case: SubjectDecisionCase,
    episode: DecisionEpisode,
) -> Dict[str, object]:
    scoped_batch = deepcopy(reasoning_case)
    scoped_batch.hypotheses = tuple(subject_case.candidate_set.hypotheses)
    scoped_batch.decision_syntheses = (subject_case.synthesis,)
    return V2DecisionEpisodeProjector.hypothesis_gap_request(
        scoped_batch,
        episode,
        subject_case.synthesis,
    )
