"""Persist and resume the inference-to-AI lifecycle without coupling workers."""

from __future__ import annotations

from copy import deepcopy
from typing import Dict, Iterable, Mapping, Optional

from ...domain.investment_reasoning import (
    AIJudgmentResult,
    CASE_AI_COMPLETED,
    CASE_AI_PENDING,
    CASE_BLOCKED,
    CASE_COMPLETED,
    CASE_CREATED,
    CASE_DEFERRED,
    CASE_DECISION_SYNTHESIZED,
    CASE_EXPIRED,
    CASE_FAILED,
    CASE_HYPOTHESES_READY,
    CASE_INFERENCE_COMPLETED,
    CASE_INPUT_READY,
    CASE_PUBLISHED,
    CASE_SUPPRESSED,
    CASE_SUPERSEDED,
    CASE_VALIDATED,
    FinalDecision,
    GraphHypothesisManager,
    InferenceResult,
    ReasoningCase,
    DecisionSynthesis,
)


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _hypothesis_rule_ids(value: Mapping[str, object]) -> tuple:
    payload = _mapping(value)
    values = payload.get("supportingRuleIds") or payload.get("supporting_rule_ids") or []
    return tuple(sorted({str(item or "").strip() for item in values if str(item or "").strip()}))


def _candidate_action(reasoning_case: ReasoningCase, hypothesis_id: str) -> str:
    for synthesis in reasoning_case.decision_syntheses:
        for alternative in synthesis.alternatives:
            if hypothesis_id in alternative.hypothesis_ids:
                return str(alternative.action or "").upper()
    return ""


def _prompt_hypotheses(reasoning_case: ReasoningCase, relation: Mapping[str, object]):
    brain = _mapping(_mapping(relation).get("investmentBrain"))
    display_set = _mapping(brain.get("hypothesisSet")) or _mapping(_mapping(relation).get("hypothesisSet"))
    display_rows = [
        dict(item)
        for item in display_set.get("hypotheses") or []
        if isinstance(item, Mapping)
    ]
    eligible_ids = {
        hypothesis_id
        for synthesis in reasoning_case.decision_syntheses
        for hypothesis_id in synthesis.eligible_hypothesis_ids
    }
    rows = []
    for hypothesis in reasoning_case.hypotheses:
        if hypothesis.hypothesis_id not in eligible_ids:
            continue
        rule_ids = tuple(sorted(hypothesis.supporting_rule_ids))
        display = next(
            (item for item in display_rows if rule_ids and _hypothesis_rule_ids(item) == rule_ids),
            {},
        )
        action = hypothesis.candidate_action or _candidate_action(reasoning_case, hypothesis.hypothesis_id)
        row = deepcopy(display)
        row.update({
            "hypothesisId": hypothesis.hypothesis_id,
            "familyId": hypothesis.family_id,
            "claim": hypothesis.label,
            "label": hypothesis.label,
            "candidateAction": action,
            "supportingRuleIds": list(hypothesis.supporting_rule_ids),
            "supportingEvidenceIds": list(hypothesis.supporting_evidence_ids),
            "counterEvidenceIds": list(hypothesis.counter_evidence_ids),
            "causalTraceIds": list(hypothesis.causal_trace_ids),
            "assumptions": list(hypothesis.assumptions),
            "invalidationConditions": list(hypothesis.invalidation_conditions),
            "horizon": hypothesis.horizon,
            "validationState": hypothesis.validation_state,
            "theoryFamily": hypothesis.theory_family,
            "thesisFamily": hypothesis.thesis_family,
            "evidenceIndependenceKey": hypothesis.evidence_independence_key,
            "predictionTarget": hypothesis.prediction_target,
            "expectedDirection": hypothesis.expected_direction,
            "expectedOutcome": hypothesis.expected_outcome,
            "competingFamilyIds": list(hypothesis.competing_family_ids),
            "outcomeMetric": hypothesis.outcome_metric,
            "falsificationContract": hypothesis.falsification_contract,
            "knowledgeBasis": deepcopy(hypothesis.knowledge_basis),
            "evidenceState": "supported",
            "approvalStatus": "approved-active",
            "verificationStatus": hypothesis.validation_state or "verified",
            "status": "active",
            "decisionEligibilitySource": "investmentReasoningCase.decisionSyntheses",
        })
        if not str(row.get("stance") or "").strip():
            row["stance"] = (
                "support" if action in {"BUY", "ADD"}
                else "risk" if action in {"TRIM", "SELL", "AVOID"}
                else "context"
            )
        rows.append(row)
    return rows


class InvestmentReasoningOrchestrator:
    """One public lifecycle over replaceable TypeDB and AI worker stages."""

    def __init__(self, repository, hypothesis_manager=None):
        self.repository = repository
        self.hypothesis_manager = hypothesis_manager or GraphHypothesisManager()

    def start(self, request, release_identity: Mapping[str, object] = None) -> ReasoningCase:
        existing = self.repository.get_by_request(str(getattr(request, "request_id", "") or ""))
        if existing:
            return existing
        return self.repository.save(ReasoningCase.create(request, release_identity))

    def input_ready(self, case_id: str) -> ReasoningCase:
        reasoning_case = self.required(case_id)
        if reasoning_case.stage in {CASE_CREATED, CASE_DEFERRED}:
            reasoning_case.transition(CASE_INPUT_READY, "point-in-time-input-ready")
            self.repository.save(reasoning_case)
        return reasoning_case

    def defer(self, case_id: str, reason: str, retryable: bool = True) -> ReasoningCase:
        reasoning_case = self.required(case_id)
        reasoning_case.record_error(reasoning_case.stage, reason, retryable)
        if reasoning_case.stage in {CASE_CREATED, CASE_INPUT_READY, CASE_DEFERRED}:
            reasoning_case.transition(CASE_DEFERRED if retryable else CASE_BLOCKED, reason)
        self.repository.save(reasoning_case)
        return reasoning_case

    def inference_completed(
        self,
        case_id: str,
        identities: Mapping[str, Mapping[str, object]],
        projection_results: Mapping[str, object],
        duration_ms: int,
    ) -> ReasoningCase:
        reasoning_case = self.required(case_id)
        verified = tuple(sorted(
            str(account_id or "")
            for account_id, identity in identities.items()
            if identity.get("verified")
        ))
        failed = tuple(sorted(
            str(account_id or "")
            for account_id, identity in identities.items()
            if not identity.get("verified")
        ))
        reasoning_case.inference_result = InferenceResult(
            source_abox_snapshot_ids=tuple(sorted({
                str(identity.get("sourceAboxSnapshotId") or "")
                for identity in identities.values()
                if str(identity.get("sourceAboxSnapshotId") or "")
            })),
            inference_generation_ids=tuple(sorted({
                str(identity.get("inferenceGenerationId") or "")
                for identity in identities.values()
                if str(identity.get("inferenceGenerationId") or "")
            })),
            verified_account_ids=verified,
            failed_account_ids=failed,
            relation_count=sum(int(identity.get("relationCount") or 0) for identity in identities.values()),
            trace_count=sum(int(identity.get("traceCount") or 0) for identity in identities.values()),
            trace_complete=bool(identities) and not failed and all(
                identity.get("sourceAboxSnapshotId") and identity.get("inferenceGenerationId")
                for identity in identities.values()
            ),
            projection_results={str(key): dict(value or {}) for key, value in projection_results.items()},
            duration_ms=max(0, int(duration_ms or 0)),
        )
        if reasoning_case.stage == CASE_INPUT_READY:
            reasoning_case.transition(
                CASE_INFERENCE_COMPLETED,
                "typedb-inference-completed",
                {"verifiedAccounts": len(verified), "failedAccounts": len(failed)},
            )
        self.repository.save(reasoning_case)
        return reasoning_case

    def hypotheses_ready(self, case_id: str, candidates: Iterable[object]) -> ReasoningCase:
        reasoning_case = self.required(case_id)
        hypotheses = self.hypothesis_manager.from_candidates(
            candidates,
            subject_symbols=reasoning_case.fact_delta.symbols,
            inference_generation_ids=(
                reasoning_case.inference_result.inference_generation_ids
                if reasoning_case.inference_result else ()
            ),
            account_ids=reasoning_case.fact_delta.account_ids,
        )
        if hypotheses:
            reasoning_case.hypotheses = hypotheses
        if reasoning_case.stage == CASE_INFERENCE_COMPLETED:
            reasoning_case.transition(
                CASE_HYPOTHESES_READY,
                "graph-hypotheses-captured",
                {"hypothesisCount": len(reasoning_case.hypotheses)},
            )
        self.repository.save(reasoning_case)
        return reasoning_case

    def decisions_synthesized(
        self,
        case_id: str,
        syntheses: Iterable[object],
    ) -> ReasoningCase:
        reasoning_case = self.required(case_id)
        indexed = {}
        for item in syntheses or []:
            synthesis = item if isinstance(item, DecisionSynthesis) else DecisionSynthesis.from_dict(item)
            if synthesis.synthesis_id:
                indexed[synthesis.synthesis_id] = synthesis
        reasoning_case.decision_syntheses = tuple(indexed[key] for key in sorted(indexed))
        if reasoning_case.stage == CASE_HYPOTHESES_READY:
            reasoning_case.transition(
                CASE_DECISION_SYNTHESIZED,
                "typedb-action-alternatives-synthesized",
                {
                    "synthesisCount": len(reasoning_case.decision_syntheses),
                    "eligibleHypothesisCount": len({
                        hypothesis_id
                        for synthesis in reasoning_case.decision_syntheses
                        for hypothesis_id in synthesis.eligible_hypothesis_ids
                    }),
                },
            )
        self.repository.save(reasoning_case)
        return reasoning_case

    def attach_case_context(self, case_id: str, events: Iterable[object]) -> None:
        reasoning_case = self.required(case_id)
        compact = self.compact_context(reasoning_case)
        for event in events or []:
            metadata = deepcopy(getattr(event, "metadata", {}) or {})
            metadata["investmentReasoningCaseId"] = reasoning_case.case_id
            metadata["investmentReasoningCase"] = compact
            relation = _mapping(metadata.get("ontologyRelationContext"))
            if relation:
                relation["investmentReasoningCaseId"] = reasoning_case.case_id
                relation["reasoningEngineDeploymentId"] = reasoning_case.deployment_id
                relation["reasoningEngineReleaseFingerprint"] = reasoning_case.release_fingerprint
                metadata["ontologyRelationContext"] = relation
            event.metadata = metadata

    def capture_ai_context(self, case_id: str, context: Mapping[str, object]) -> Dict[str, object]:
        enriched = deepcopy(dict(context or {}))
        reasoning_case = self.required(case_id)
        hypotheses = self.hypothesis_manager.from_ai_context(enriched)
        # Once TypeDB action alternatives have been synthesized, their
        # hypothesis IDs are an immutable decision contract. Notification
        # enrichment may attach research and rebuild a display hypothesis set
        # with new IDs; replacing the persisted set here would make it
        # impossible for any AI selection to belong to both the hypothesis set
        # and the synthesis eligibility set.
        if hypotheses and not reasoning_case.decision_syntheses:
            reasoning_case.hypotheses = hypotheses
        if reasoning_case.stage == CASE_INFERENCE_COMPLETED:
            reasoning_case.transition(
                CASE_HYPOTHESES_READY,
                "ai-context-graph-hypotheses-captured",
                {"hypothesisCount": len(reasoning_case.hypotheses)},
            )
        if reasoning_case.decision_syntheses:
            relation = _mapping(enriched.get("ontologyRelationContext"))
            if relation:
                prompt_hypotheses = _prompt_hypotheses(reasoning_case, relation)
                brain = _mapping(relation.get("investmentBrain"))
                hypothesis_set = _mapping(brain.get("hypothesisSet")) or _mapping(relation.get("hypothesisSet"))
                hypothesis_set["hypotheses"] = prompt_hypotheses
                hypothesis_set["eligibleHypothesisIds"] = [
                    item["hypothesisId"] for item in prompt_hypotheses
                ]
                brain["hypothesisSet"] = hypothesis_set
                relation["investmentBrain"] = brain
                if isinstance(relation.get("hypothesisSet"), Mapping):
                    relation["hypothesisSet"] = deepcopy(hypothesis_set)
                enriched["ontologyRelationContext"] = relation
        enriched["investmentReasoningCaseId"] = reasoning_case.case_id
        enriched["investmentReasoningCase"] = self.compact_context(reasoning_case)
        self.repository.save(reasoning_case)
        return enriched

    def ai_queued(self, case_id: str, request_id: str, notification_job_id: str) -> ReasoningCase:
        reasoning_case = self.required(case_id)
        reasoning_case.ai_request_id = str(request_id or "")
        reasoning_case.notification_job_id = str(notification_job_id or "")
        if reasoning_case.stage in {CASE_HYPOTHESES_READY, CASE_DECISION_SYNTHESIZED}:
            reasoning_case.transition(CASE_AI_PENDING, "ai-judgment-queued")
        self.repository.save(reasoning_case)
        return reasoning_case

    def ai_completed(self, request, context: Mapping[str, object], result) -> Optional[ReasoningCase]:
        case_id = str(
            _mapping(context).get("investmentReasoningCaseId")
            or _mapping(_mapping(context).get("investmentReasoningCase")).get("caseId")
            or ""
        )
        if not case_id:
            return None
        reasoning_case = self.required(case_id)
        judgment = AIJudgmentResult.from_result(result, getattr(result, "response", {}) or {})
        reasoning_case.ai_judgment = judgment
        reasoning_case.ai_request_id = judgment.request_id
        reasoning_case.notification_job_id = str(getattr(request, "notification_job_id", "") or "")
        if reasoning_case.stage in {CASE_HYPOTHESES_READY, CASE_DECISION_SYNTHESIZED}:
            reasoning_case.transition(CASE_AI_PENDING, "ai-judgment-recovered")
        if reasoning_case.stage == CASE_AI_PENDING:
            reasoning_case.transition(CASE_AI_COMPLETED, "ai-judgment-completed")
        valid, reason = self.validate_judgment(reasoning_case, judgment)
        if not valid:
            reasoning_case.record_error(CASE_AI_COMPLETED, reason, False)
            reasoning_case.transition(CASE_BLOCKED, reason)
            self.repository.save(reasoning_case)
            return reasoning_case
        reasoning_case.final_decision = FinalDecision(
            action=judgment.action,
            source="ai-judgment",
            selected_hypothesis_id=judgment.selected_hypothesis_id,
            validation_state=judgment.validation_state,
            reason=judgment.rationale,
            notification_job_id=reasoning_case.notification_job_id,
            published=False,
        )
        reasoning_case.transition(CASE_VALIDATED, "ai-judgment-validated")
        self.repository.save(reasoning_case)
        return reasoning_case

    def validate_ai_result(self, context: Mapping[str, object], result):
        case_id = self.case_id_from_context(context)
        if not case_id:
            return True, ""
        reasoning_case = self.required(case_id)
        judgment = AIJudgmentResult.from_result(result, getattr(result, "response", {}) or {})
        return self.validate_judgment(reasoning_case, judgment)

    def notification_published(self, context: Mapping[str, object]) -> Optional[ReasoningCase]:
        case_id = self.case_id_from_context(context)
        if not case_id:
            return None
        reasoning_case = self.required(case_id)
        if reasoning_case.final_decision:
            reasoning_case.final_decision = FinalDecision(
                action=reasoning_case.final_decision.action,
                source=reasoning_case.final_decision.source,
                selected_hypothesis_id=reasoning_case.final_decision.selected_hypothesis_id,
                validation_state=reasoning_case.final_decision.validation_state,
                reason=reasoning_case.final_decision.reason,
                notification_job_id=reasoning_case.final_decision.notification_job_id,
                published=True,
            )
        if reasoning_case.stage == CASE_VALIDATED:
            reasoning_case.transition(CASE_PUBLISHED, "notification-delivery-completed")
        self.repository.save(reasoning_case)
        return reasoning_case

    def context_observation_validated(
        self,
        case_id: str,
        reason: str = "TypeDB reference-only market observation validated.",
    ) -> ReasoningCase:
        """Validate an informational graph observation without asking AI to act."""

        reasoning_case = self.required(case_id)
        if reasoning_case.stage == CASE_VALIDATED:
            return reasoning_case
        inference = reasoning_case.inference_result
        if not inference or not inference.trace_complete:
            raise ValueError("TypeDB context observation requires a complete inference trace.")
        if not reasoning_case.decision_syntheses:
            raise ValueError("TypeDB context observation requires a decision synthesis.")
        if any(
            synthesis.graph_candidate_action != "NO_ACTION"
            or synthesis.eligible_hypothesis_ids
            for synthesis in reasoning_case.decision_syntheses
        ):
            raise ValueError("Actionable or hypothesis-backed synthesis cannot use the context observation path.")
        if reasoning_case.stage not in {CASE_HYPOTHESES_READY, CASE_DECISION_SYNTHESIZED}:
            raise ValueError("Context observation cannot be validated from stage " + reasoning_case.stage + ".")
        reasoning_case.final_decision = FinalDecision(
            action="NO_ACTION",
            source="typedb-context-observation",
            validation_state="reference-only",
            reason=str(reason or ""),
            notification_job_id=reasoning_case.notification_job_id,
            published=False,
        )
        reasoning_case.transition(
            CASE_VALIDATED,
            "typedb-context-observation-validated",
            {
                "requiresAiJudgement": False,
                "synthesisCount": len(reasoning_case.decision_syntheses),
            },
        )
        self.repository.save(reasoning_case)
        return reasoning_case

    def notification_suppressed(
        self,
        context: Mapping[str, object],
        reason: str,
        disposition: str = CASE_SUPPRESSED,
    ) -> Optional[ReasoningCase]:
        case_id = self.case_id_from_context(context)
        if not case_id:
            return None
        reasoning_case = self.required(case_id)
        target = str(disposition or CASE_SUPPRESSED).upper()
        if target not in {CASE_SUPPRESSED, CASE_SUPERSEDED, CASE_EXPIRED}:
            target = CASE_SUPPRESSED
        if reasoning_case.stage not in {
            CASE_COMPLETED,
            CASE_PUBLISHED,
            CASE_BLOCKED,
            CASE_FAILED,
            CASE_SUPPRESSED,
            CASE_SUPERSEDED,
            CASE_EXPIRED,
        }:
            reasoning_case.transition(
                target,
                str(reason or "notification-delivery-suppressed"),
                {"published": False},
            )
            self.repository.save(reasoning_case)
        return reasoning_case

    def case_superseded(self, case_id: str, reason: str) -> Optional[ReasoningCase]:
        if not str(case_id or "").strip():
            return None
        return self.notification_suppressed(
            {"investmentReasoningCaseId": str(case_id or "").strip()},
            reason,
            CASE_SUPERSEDED,
        )

    def ai_failed(self, context: Mapping[str, object], reason: str) -> Optional[ReasoningCase]:
        case_id = str(_mapping(context).get("investmentReasoningCaseId") or "")
        if not case_id:
            return None
        reasoning_case = self.required(case_id)
        reasoning_case.record_error(CASE_AI_PENDING, reason, False)
        if reasoning_case.stage in {CASE_HYPOTHESES_READY, CASE_DECISION_SYNTHESIZED, CASE_AI_PENDING}:
            reasoning_case.transition(CASE_BLOCKED, "ai-judgment-failed")
        self.repository.save(reasoning_case)
        return reasoning_case

    def ai_fallback_completed(
        self,
        request,
        context: Mapping[str, object],
        result,
        reason: str,
    ) -> Optional[ReasoningCase]:
        """Complete the case with a TypeDB-only publication when AI is unavailable."""

        case_id = self.case_id_from_context(context)
        if not case_id:
            return None
        reasoning_case = self.required(case_id)
        payload = dict(getattr(result, "response", {}) or {})
        action = str(payload.get("action") or "HOLD").upper()
        if action not in {"BUY", "ADD", "HOLD", "TRIM", "SELL", "AVOID", "WATCH"}:
            action = "HOLD"
        reasoning_case.ai_request_id = str(getattr(request, "request_id", "") or "")
        reasoning_case.notification_job_id = str(
            getattr(request, "notification_job_id", "") or ""
        )
        reasoning_case.record_error(CASE_AI_PENDING, str(reason or "AI unavailable"), False)
        reasoning_case.final_decision = FinalDecision(
            action=action,
            source="typedb-inference-fallback",
            selected_hypothesis_id="",
            validation_state="reference-only",
            reason="AI를 사용하지 못해 TypeDB 추론 결과만 발송했습니다.",
            notification_job_id=reasoning_case.notification_job_id,
            published=False,
        )
        if reasoning_case.stage in {
            CASE_HYPOTHESES_READY,
            CASE_DECISION_SYNTHESIZED,
            CASE_AI_PENDING,
        }:
            reasoning_case.transition(
                CASE_VALIDATED,
                "typedb-inference-fallback-ready",
                {"aiAvailable": False},
            )
        self.repository.save(reasoning_case)
        return reasoning_case

    def complete_without_ai(self, case_id: str, reason: str, source: str = "typedb-shadow") -> ReasoningCase:
        reasoning_case = self.required(case_id)
        if reasoning_case.stage == CASE_INFERENCE_COMPLETED:
            reasoning_case.transition(CASE_HYPOTHESES_READY, "no-action-hypothesis-set")
        reasoning_case.final_decision = FinalDecision(
            action="NO_ACTION",
            source=source,
            validation_state="reference-only",
            reason=str(reason or ""),
            published=False,
        )
        if reasoning_case.stage in {
            CASE_HYPOTHESES_READY,
            CASE_DECISION_SYNTHESIZED,
            CASE_VALIDATED,
        }:
            reasoning_case.transition(CASE_COMPLETED, reason)
        self.repository.save(reasoning_case)
        return reasoning_case

    @staticmethod
    def validate_judgment(reasoning_case: ReasoningCase, judgment: AIJudgmentResult):
        if judgment.action not in {"BUY", "ADD", "HOLD", "TRIM", "SELL", "AVOID", "WATCH"}:
            return False, "AI judgment action is outside the investment decision contract."
        hypothesis_ids = {item.hypothesis_id for item in reasoning_case.hypotheses}
        if not hypothesis_ids:
            return False, "TypeDB hypothesis set is empty; AI publication is not allowed."
        if judgment.selected_hypothesis_id not in hypothesis_ids:
            return False, "AI selected hypothesis is not present in the TypeDB hypothesis set."
        if reasoning_case.decision_syntheses:
            eligible_ids = {
                hypothesis_id
                for synthesis in reasoning_case.decision_syntheses
                for hypothesis_id in synthesis.eligible_hypothesis_ids
            }
            if judgment.selected_hypothesis_id not in eligible_ids:
                return False, "AI selected hypothesis is reference-only in the TypeDB decision synthesis."
            blocked_actions = {
                action
                for synthesis in reasoning_case.decision_syntheses
                for action in synthesis.blocked_actions
            }
            if judgment.action in blocked_actions:
                return False, "AI judgment action is blocked by the TypeDB action envelope."
            applicable = [
                synthesis for synthesis in reasoning_case.decision_syntheses
                if judgment.selected_hypothesis_id in synthesis.eligible_hypothesis_ids
            ]
            allowed_actions = {
                action
                for synthesis in applicable
                for action in synthesis.allowed_actions
            }
            if allowed_actions and judgment.action not in allowed_actions:
                return False, "AI judgment action is outside the TypeDB action envelope."
        if str(judgment.validation_state or "").lower() in {"blocked", "invalid", "failed", "error"}:
            return False, "AI judgment validation state blocks publication."
        return True, ""

    @staticmethod
    def compact_context(reasoning_case: ReasoningCase) -> Dict[str, object]:
        inference = reasoning_case.inference_result
        return {
            "caseId": reasoning_case.case_id,
            "requestId": reasoning_case.request_id,
            "stage": reasoning_case.stage,
            "deploymentId": reasoning_case.deployment_id,
            "releaseFingerprint": reasoning_case.release_fingerprint,
            "validationCohortId": reasoning_case.validation_cohort_id,
            "lane": reasoning_case.fact_delta.lane,
            "sourceAboxSnapshotIds": list(inference.source_abox_snapshot_ids) if inference else [],
            "inferenceGenerationIds": list(inference.inference_generation_ids) if inference else [],
            "hypothesisIds": [item.hypothesis_id for item in reasoning_case.hypotheses],
            "decisionSyntheses": [
                {
                    "synthesisId": item.synthesis_id,
                    "symbol": item.symbol,
                    "graphCandidateAction": item.graph_candidate_action,
                    "allowedActions": list(item.allowed_actions),
                    "blockedActions": list(item.blocked_actions),
                    "eligibleHypothesisIds": list(item.eligible_hypothesis_ids),
                    "alternatives": [
                        {
                            "action": alternative.action,
                            "hypothesisIds": list(alternative.hypothesis_ids),
                            "decisionEligible": alternative.decision_eligible,
                        }
                        for alternative in item.alternatives
                    ],
                }
                for item in reasoning_case.decision_syntheses
            ],
            "contractVersion": reasoning_case.contract_version,
        }

    def required(self, case_id: str) -> ReasoningCase:
        reasoning_case = self.repository.get(str(case_id or ""))
        if not reasoning_case:
            raise ValueError("Unknown investment reasoning case: " + str(case_id or ""))
        return reasoning_case

    @staticmethod
    def case_id_from_context(context: Mapping[str, object]) -> str:
        values = _mapping(context)
        return str(
            values.get("investmentReasoningCaseId")
            or _mapping(values.get("investmentReasoningCase")).get("caseId")
            or ""
        )

    def explain(self, case_id: str) -> Dict[str, object]:
        reasoning_case = self.required(case_id)
        return reasoning_case.to_dict()
