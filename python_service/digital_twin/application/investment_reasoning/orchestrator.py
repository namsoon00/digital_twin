"""Persist and resume the inference-to-AI lifecycle without coupling workers."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
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
    DecisionAbstention,
    SubjectDecisionCase,
    publication_for_subject_case,
    FINAL_DECISION,
    REVIEW_ONLY,
    ABSTAIN,
    OBSERVATION,
    SUPPRESSED,
    rule_evaluation_records_from_projection_results,
)
from ...domain.investment_reasoning.subject_case import (
    SUBJECT_ABSTAINED,
    SUBJECT_AI_COMPLETED,
    SUBJECT_AI_PENDING,
    SUBJECT_BLOCKED,
    SUBJECT_OBSERVATION,
    SUBJECT_PUBLISHED,
    SUBJECT_READY,
    SUBJECT_REVIEW_ONLY,
    SUBJECT_SUPPRESSED,
    SUBJECT_VALIDATED,
)
from .episode_projection import (
    decision_episode_from_subject_case,
    decision_episode_outcome_contract_readiness,
    hypothesis_gap_request_from_subject_case,
)


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _hypothesis_rule_ids(value: Mapping[str, object]) -> tuple:
    payload = _mapping(value)
    values = payload.get("supportingRuleIds") or payload.get("supporting_rule_ids") or []
    return tuple(sorted({str(item or "").strip() for item in values if str(item or "").strip()}))


def _customer_notification_reasons(values: Iterable[object]) -> list:
    generic = {
        "관찰 등급", "종목 지정", "핵심 투자 단어", "행동 필요 표현", "본문 있음",
    }
    rows = []
    for value in values or []:
        text = str(value or "").strip()
        normalized = text.lstrip("•- ").strip()
        if not normalized or normalized in generic or normalized in rows:
            continue
        rows.append(normalized)
    return rows[:5]


class _EphemeralSubjectDecisionCaseStore:
    """Test/compatibility store; production injects the durable MySQL adapter."""

    def __init__(self):
        self.items = {}

    def save(self, subject_case):
        self.items[subject_case.subject_case_id] = subject_case
        return subject_case

    def get(self, subject_case_id):
        return self.items.get(str(subject_case_id or ""))

    def for_batch(self, batch_case_id):
        return sorted(
            [item for item in self.items.values() if item.batch_case_id == str(batch_case_id or "")],
            key=lambda item: (item.account_id, item.symbol, item.subject_case_id),
        )

    def get_by_scope(self, batch_case_id, account_id, symbol, inference_generation_id, synthesis_id=""):
        return next((
            item
            for item in self.for_batch(batch_case_id)
            if item.account_id == str(account_id or "")
            and item.symbol == str(symbol or "").upper()
            and item.inference_generation_id == str(inference_generation_id or "")
            and (not synthesis_id or item.synthesis.synthesis_id == str(synthesis_id or ""))
        ), None)

    def stale_ready(self, max_age_minutes=30, limit=100):
        del max_age_minutes, limit
        return []


def _prompt_hypotheses(subject_case: SubjectDecisionCase, relation: Mapping[str, object]):
    brain = _mapping(_mapping(relation).get("investmentBrain"))
    display_set = _mapping(brain.get("hypothesisSet")) or _mapping(_mapping(relation).get("hypothesisSet"))
    display_rows = [
        dict(item)
        for item in display_set.get("hypotheses") or []
        if isinstance(item, Mapping)
    ]
    eligible_ids = set(subject_case.candidate_set.eligible_hypothesis_ids)
    rows = []
    for hypothesis in subject_case.candidate_set.hypotheses:
        if hypothesis.hypothesis_id not in eligible_ids:
            continue
        rule_ids = tuple(sorted(hypothesis.supporting_rule_ids))
        display = next(
            (item for item in display_rows if rule_ids and _hypothesis_rule_ids(item) == rule_ids),
            {},
        )
        action = hypothesis.candidate_action or next((
            alternative.action
            for alternative in subject_case.synthesis.alternatives
            if hypothesis.hypothesis_id in alternative.hypothesis_ids
        ), "")
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
            "claimContract": deepcopy(hypothesis.claim_contract),
            "qualification": deepcopy(hypothesis.qualification),
            "evidenceState": "supported",
            "approvalStatus": "approved-active",
            "verificationStatus": hypothesis.validation_state or "verified",
            "status": "active",
            "decisionEligibilitySource": "subjectDecisionCase.candidateSet",
            "candidateFingerprint": subject_case.candidate_set.fingerprint,
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

    def __init__(
        self,
        repository,
        hypothesis_manager=None,
        decision_episode_store=None,
        hypothesis_proposal_request_store=None,
        subject_case_repository=None,
    ):
        self.repository = repository
        self.hypothesis_manager = hypothesis_manager or GraphHypothesisManager()
        self.decision_episode_store = decision_episode_store
        self.hypothesis_proposal_request_store = hypothesis_proposal_request_store
        self.subject_cases = subject_case_repository or _EphemeralSubjectDecisionCaseStore()
        self._stale_recovery_checked = False

    def _persist(self, reasoning_case: ReasoningCase) -> ReasoningCase:
        return self.repository.save(reasoning_case)

    def _persist_subject(
        self,
        subject_case: SubjectDecisionCase,
        connection=None,
    ) -> SubjectDecisionCase:
        if connection is not None:
            save_with_connection = getattr(self.subject_cases, "save_with_connection", None)
            if not callable(save_with_connection):
                raise ValueError("Subject decision repository does not support the active transaction.")
            save_with_connection(connection, subject_case)
            return subject_case
        return self.subject_cases.save(subject_case)

    def start(self, request, release_identity: Mapping[str, object] = None) -> ReasoningCase:
        if not self._stale_recovery_checked:
            self.recover_stale_subject_cases()
            self._stale_recovery_checked = True
        existing = self.repository.get_by_request(str(getattr(request, "request_id", "") or ""))
        if existing:
            return existing
        return self.repository.save(ReasoningCase.create(request, release_identity))

    def recover_stale_subject_cases(
        self,
        max_age_minutes: int = 30,
        limit: int = 100,
    ) -> tuple:
        """Close point-in-time candidates that missed their AI handoff."""

        finder = getattr(self.subject_cases, "stale_ready", None)
        if not callable(finder):
            return ()
        recovered = []
        reason = (
            "AI handoff did not start before the point-in-time facts expired; "
            "fresh reasoning is required."
        )
        for subject_case in finder(max_age_minutes=max_age_minutes, limit=limit) or []:
            if subject_case.stage != SUBJECT_READY or subject_case.publication is not None:
                continue
            subject_case.abstention = DecisionAbstention(
                reason_code="stale-ready-ai-handoff-missed",
                reason=reason,
                details={
                    "candidateFingerprint": subject_case.candidate_set.fingerprint,
                    "maximumReadyAgeMinutes": max(1, int(max_age_minutes or 30)),
                    "recoveryAction": "fresh-reasoning-required",
                },
            )
            subject_case.mark(SUBJECT_ABSTAINED, reason)
            subject_case.publication = publication_for_subject_case(subject_case, ABSTAIN)
            self._persist_subject(subject_case)
            recovered.append(subject_case)
        return tuple(recovered)

    def input_ready(self, case_id: str) -> ReasoningCase:
        reasoning_case = self.required(case_id)
        if reasoning_case.stage in {CASE_CREATED, CASE_DEFERRED}:
            reasoning_case.transition(CASE_INPUT_READY, "point-in-time-input-ready")
            self._persist(reasoning_case)
        return reasoning_case

    def defer(self, case_id: str, reason: str, retryable: bool = True) -> ReasoningCase:
        reasoning_case = self.required(case_id)
        reasoning_case.record_error(reasoning_case.stage, reason, retryable)
        if reasoning_case.stage in {CASE_CREATED, CASE_INPUT_READY, CASE_DEFERRED}:
            reasoning_case.transition(CASE_DEFERRED if retryable else CASE_BLOCKED, reason)
        self._persist(reasoning_case)
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
            rule_evaluations=rule_evaluation_records_from_projection_results(projection_results),
            duration_ms=max(0, int(duration_ms or 0)),
        )
        if reasoning_case.stage == CASE_INPUT_READY:
            reasoning_case.transition(
                CASE_INFERENCE_COMPLETED,
                "typedb-inference-completed",
                {"verifiedAccounts": len(verified), "failedAccounts": len(failed)},
            )
        self._persist(reasoning_case)
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
        self._persist(reasoning_case)
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
        subject_case_ids = []
        for synthesis in reasoning_case.decision_syntheses:
            candidate = SubjectDecisionCase.create(
                reasoning_case,
                synthesis,
                reasoning_case.hypotheses,
            )
            subject_case = self.subject_cases.get(candidate.subject_case_id)
            if subject_case is not None:
                if subject_case.candidate_set.fingerprint != candidate.candidate_set.fingerprint:
                    raise ValueError(
                        "A subject decision case cannot replace its immutable candidate set: "
                        + candidate.subject_case_id
                    )
            else:
                subject_case = candidate
                self._persist_subject(subject_case)
            subject_case_ids.append(subject_case.subject_case_id)
        reasoning_case.subject_case_ids = tuple(sorted(subject_case_ids))
        if reasoning_case.stage == CASE_HYPOTHESES_READY:
            reasoning_case.transition(
                CASE_DECISION_SYNTHESIZED,
                "typedb-action-alternatives-synthesized",
                {
                    "synthesisCount": len(reasoning_case.decision_syntheses),
                    "subjectCaseCount": len(reasoning_case.subject_case_ids),
                    "eligibleHypothesisCount": len({
                        hypothesis_id
                        for synthesis in reasoning_case.decision_syntheses
                        for hypothesis_id in synthesis.eligible_hypothesis_ids
                    }),
                },
            )
        self._persist(reasoning_case)
        return reasoning_case

    def attach_case_context(self, case_id: str, events: Iterable[object]) -> None:
        reasoning_case = self.required(case_id)
        compact = self.compact_context(reasoning_case)
        for event in events or []:
            metadata = deepcopy(getattr(event, "metadata", {}) or {})
            synthesis_payload = _mapping(metadata.get("v2DecisionSynthesis"))
            synthesis = DecisionSynthesis.from_dict(synthesis_payload) if synthesis_payload else None
            subject_case = self.subject_cases.get_by_scope(
                reasoning_case.case_id,
                synthesis.account_id if synthesis else str(getattr(event, "account_id", "") or ""),
                synthesis.symbol if synthesis else str(getattr(event, "symbol", "") or ""),
                synthesis.inference_generation_id if synthesis else str(metadata.get("inferenceGenerationId") or ""),
                synthesis.synthesis_id if synthesis else "",
            )
            if subject_case is None:
                matches = [
                    item for item in self.subject_cases.for_batch(reasoning_case.case_id)
                    if item.symbol == str(getattr(event, "symbol", "") or "").upper()
                ]
                subject_case = matches[0] if len(matches) == 1 else None
            metadata["investmentReasoningCaseId"] = reasoning_case.case_id
            metadata["investmentReasoningCase"] = compact
            metadata["investmentReasoningBatchRunId"] = reasoning_case.case_id
            if subject_case:
                metadata["investmentSubjectDecisionCaseId"] = subject_case.subject_case_id
                metadata["investmentSubjectDecisionCase"] = self.compact_subject_context(subject_case)
            relation = _mapping(metadata.get("ontologyRelationContext"))
            if relation:
                relation["investmentReasoningCaseId"] = reasoning_case.case_id
                relation["investmentReasoningBatchRunId"] = reasoning_case.case_id
                if subject_case:
                    relation["investmentSubjectDecisionCaseId"] = subject_case.subject_case_id
                    relation["decisionCandidateFingerprint"] = subject_case.candidate_set.fingerprint
                relation["reasoningEngineDeploymentId"] = reasoning_case.deployment_id
                relation["reasoningEngineReleaseFingerprint"] = reasoning_case.release_fingerprint
                metadata["ontologyRelationContext"] = relation
            event.metadata = metadata

    def capture_ai_context(self, case_id: str, context: Mapping[str, object]) -> Dict[str, object]:
        enriched = deepcopy(dict(context or {}))
        subject_case = self.required_subject(case_id, enriched)
        reasoning_case = self.required(subject_case.batch_case_id)
        relation = _mapping(enriched.get("ontologyRelationContext"))
        if relation:
            prompt_hypotheses = _prompt_hypotheses(subject_case, relation)
            brain = _mapping(relation.get("investmentBrain"))
            hypothesis_set = _mapping(brain.get("hypothesisSet")) or _mapping(relation.get("hypothesisSet"))
            hypothesis_set.update({
                "hypotheses": prompt_hypotheses,
                "eligibleHypothesisIds": [item["hypothesisId"] for item in prompt_hypotheses],
                "candidateSetId": subject_case.candidate_set.candidate_set_id,
                "candidateFingerprint": subject_case.candidate_set.fingerprint,
                "accountId": subject_case.account_id,
                "subjectSymbol": subject_case.symbol,
                "inferenceGenerationId": subject_case.inference_generation_id,
            })
            brain["hypothesisSet"] = hypothesis_set
            relation["investmentBrain"] = brain
            if isinstance(relation.get("hypothesisSet"), Mapping):
                relation["hypothesisSet"] = deepcopy(hypothesis_set)
            relation["investmentSubjectDecisionCaseId"] = subject_case.subject_case_id
            relation["decisionCandidateFingerprint"] = subject_case.candidate_set.fingerprint
            enriched["ontologyRelationContext"] = relation
        enriched["investmentReasoningCaseId"] = reasoning_case.case_id
        enriched["investmentReasoningCase"] = self.compact_context(reasoning_case)
        enriched["investmentReasoningBatchRunId"] = reasoning_case.case_id
        enriched["investmentSubjectDecisionCaseId"] = subject_case.subject_case_id
        enriched["investmentSubjectDecisionCase"] = self.compact_subject_context(subject_case)
        enriched["decisionCandidateFingerprint"] = subject_case.candidate_set.fingerprint
        self._persist_subject(subject_case)
        return enriched

    def ai_queued(self, case_id: str, request_id: str, notification_job_id: str) -> SubjectDecisionCase:
        subject_case = self.required_subject(case_id)
        if subject_case.stage == SUBJECT_AI_PENDING and subject_case.ai_request_id == str(request_id or ""):
            return subject_case
        if subject_case.stage != SUBJECT_READY:
            raise ValueError(
                "Subject decision case cannot queue AI from stage " + subject_case.stage + "."
            )
        subject_case.ai_request_id = str(request_id or "")
        subject_case.notification_job_id = str(notification_job_id or "")
        subject_case.mark(SUBJECT_AI_PENDING)
        self._persist_subject(subject_case)
        return subject_case

    def ai_completed(
        self,
        request,
        context: Mapping[str, object],
        result,
        connection=None,
    ) -> Optional[SubjectDecisionCase]:
        subject_case_id = self.subject_case_id_from_context(context)
        if not subject_case_id:
            return None
        subject_case = self.required_subject(subject_case_id, context)
        reasoning_case = self.required(subject_case.batch_case_id)
        judgment = AIJudgmentResult.from_result(result, getattr(result, "response", {}) or {})
        if subject_case.publication is not None:
            # Delivery retries and superseded AI requests can race after the
            # one canonical decision has already been published. Publication
            # is immutable, so the only correct outcome is idempotent reuse;
            # treating a later result as a worker failure opens a false
            # incident while changing no customer-visible decision.
            return subject_case
        subject_case.ai_judgment = judgment
        subject_case.ai_request_id = judgment.request_id
        subject_case.notification_job_id = str(getattr(request, "notification_job_id", "") or "")
        subject_case.mark(SUBJECT_AI_COMPLETED)
        valid, reason = self.validate_judgment(subject_case, judgment)
        if not valid:
            subject_case.abstention = DecisionAbstention(
                reason_code="ai-decision-contract-invalid",
                reason=reason,
                details={
                    "candidateFingerprint": subject_case.candidate_set.fingerprint,
                    "aiRequestId": judgment.request_id,
                    "aiResultId": judgment.result_id,
                },
            )
            subject_case.mark(SUBJECT_ABSTAINED, reason)
            subject_case.publication = publication_for_subject_case(subject_case, ABSTAIN)
            self._persist_subject(subject_case, connection=connection)
            return subject_case
        subject_case.final_decision = FinalDecision(
            action=judgment.action,
            source="ai-judgment",
            selected_hypothesis_id=judgment.selected_hypothesis_id,
            validation_state=judgment.validation_state,
            reason=judgment.rationale,
            notification_job_id=subject_case.notification_job_id,
            published=False,
        )
        subject_case.mark(SUBJECT_VALIDATED)
        episode = decision_episode_from_subject_case(reasoning_case, subject_case)
        outcome_readiness = decision_episode_outcome_contract_readiness(episode)
        if not outcome_readiness.get("ready"):
            subject_case.final_decision = None
            subject_case.abstention = DecisionAbstention(
                reason_code="outcome-contract-invalid",
                reason=(
                    "The selected hypothesis has no complete point-in-time outcome contract; "
                    "the investment judgment was not published."
                ),
                details={
                    **outcome_readiness,
                    "candidateFingerprint": subject_case.candidate_set.fingerprint,
                    "selectedHypothesisId": judgment.selected_hypothesis_id,
                    "aiRequestId": judgment.request_id,
                    "aiResultId": judgment.result_id,
                },
            )
            subject_case.mark(
                SUBJECT_ABSTAINED,
                subject_case.abstention.reason,
                outcome_readiness,
            )
            subject_case.publication = publication_for_subject_case(subject_case, ABSTAIN)
            self._persist_subject(subject_case, connection=connection)
            return subject_case
        subject_case.publication = publication_for_subject_case(
            subject_case,
            FINAL_DECISION,
            decision_episode_id=episode.episode_id,
            explanation_snapshot=self.explanation_snapshot(subject_case, episode.to_dict(), context),
        )
        atomic_save = getattr(self.subject_cases, "save_final_with_episode", None)
        if callable(atomic_save) and self.decision_episode_store is not None:
            atomic_save(
                subject_case,
                episode,
                self.decision_episode_store,
                connection=connection,
            )
        else:
            if connection is not None:
                raise ValueError("Final decision publication requires transactional persistence support.")
            if self.decision_episode_store is not None:
                episode = self.decision_episode_store.save(episode)
            self._persist_subject(subject_case)
        enqueue_proposal = getattr(
            self.hypothesis_proposal_request_store,
            "enqueue_hypothesis_proposal_request",
            None,
        )
        if callable(enqueue_proposal) and connection is None:
            proposal_request = hypothesis_gap_request_from_subject_case(
                reasoning_case,
                subject_case,
                episode,
            )
            if proposal_request:
                enqueue_proposal(proposal_request)
        return subject_case

    def validate_ai_result(self, context: Mapping[str, object], result):
        subject_case_id = self.subject_case_id_from_context(context)
        if not subject_case_id:
            return True, ""
        subject_case = self.required_subject(subject_case_id, context)
        judgment = AIJudgmentResult.from_result(result, getattr(result, "response", {}) or {})
        fingerprint = str(_mapping(context).get("decisionCandidateFingerprint") or "")
        if fingerprint and fingerprint != subject_case.candidate_set.fingerprint:
            return False, "AI request candidate fingerprint does not match the persisted subject decision case."
        return self.validate_judgment(subject_case, judgment)

    def notification_published(self, context: Mapping[str, object]) -> Optional[SubjectDecisionCase]:
        subject_case_id = self.subject_case_id_from_context(context)
        if not subject_case_id:
            return None
        subject_case = self.required_subject(subject_case_id, context)
        if (
            subject_case.stage == SUBJECT_PUBLISHED
            and subject_case.publication
            and subject_case.publication.delivered_at
        ):
            return subject_case
        if subject_case.final_decision:
            subject_case.final_decision = FinalDecision(
                action=subject_case.final_decision.action,
                source=subject_case.final_decision.source,
                selected_hypothesis_id=subject_case.final_decision.selected_hypothesis_id,
                validation_state=subject_case.final_decision.validation_state,
                reason=subject_case.final_decision.reason,
                notification_job_id=subject_case.final_decision.notification_job_id,
                published=True,
            )
        if subject_case.publication:
            publication = subject_case.publication
            subject_case.publication = type(publication)(
                publication_id=publication.publication_id,
                subject_case_id=publication.subject_case_id,
                outcome_kind=publication.outcome_kind,
                fingerprint=publication.fingerprint,
                decision_episode_id=publication.decision_episode_id,
                notification_job_id=publication.notification_job_id,
                explanation_snapshot=publication.explanation_snapshot,
                created_at=publication.created_at,
                delivered_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                version=publication.version,
            )
        subject_case.mark_delivery("delivered")
        if (
            subject_case.publication is not None
            and subject_case.publication.outcome_kind == FINAL_DECISION
        ):
            subject_case.mark(SUBJECT_PUBLISHED)
        self._persist_subject(subject_case)
        return subject_case

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
        for subject_case in self.subject_cases.for_batch(reasoning_case.case_id):
            subject_case.mark(SUBJECT_OBSERVATION)
            subject_case.publication = publication_for_subject_case(
                subject_case,
                OBSERVATION,
                explanation_snapshot={"reason": str(reason or "")},
            )
            self._persist_subject(subject_case)
        self._persist(reasoning_case)
        return reasoning_case

    def notification_suppressed(
        self,
        context: Mapping[str, object],
        reason: str,
        disposition: str = CASE_SUPPRESSED,
    ) -> Optional[ReasoningCase]:
        subject_case_id = self.subject_case_id_from_context(context)
        if subject_case_id:
            subject_case = self.required_subject(subject_case_id, context)
            if subject_case.final_decision is not None or (
                subject_case.publication is not None
                and subject_case.publication.outcome_kind == FINAL_DECISION
            ):
                subject_case.mark_delivery("suppressed", reason)
                self._persist_subject(subject_case)
                return subject_case
            if subject_case.stage not in {
                SUBJECT_PUBLISHED, SUBJECT_ABSTAINED, SUBJECT_OBSERVATION,
                SUBJECT_REVIEW_ONLY, SUBJECT_SUPPRESSED, SUBJECT_BLOCKED,
            }:
                subject_case.mark(SUBJECT_SUPPRESSED, reason)
                subject_case.publication = publication_for_subject_case(
                    subject_case,
                    SUPPRESSED,
                    explanation_snapshot={"reason": str(reason or "")},
                )
                subject_case.mark_delivery("suppressed", reason)
                self._persist_subject(subject_case)
            return subject_case
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
            self._persist(reasoning_case)
        return reasoning_case

    def case_superseded(self, case_id: str, reason: str) -> Optional[ReasoningCase]:
        if not str(case_id or "").strip():
            return None
        context_key = (
            "investmentSubjectDecisionCaseId"
            if self.subject_cases.get(str(case_id or "").strip())
            else "investmentReasoningCaseId"
        )
        return self.notification_suppressed(
            {context_key: str(case_id or "").strip()},
            reason,
            CASE_SUPERSEDED,
        )

    def ai_failed(self, context: Mapping[str, object], reason: str) -> Optional[SubjectDecisionCase]:
        subject_case_id = self.subject_case_id_from_context(context)
        if not subject_case_id:
            return None
        subject_case = self.required_subject(subject_case_id, context)
        if subject_case.publication is not None:
            return subject_case
        subject_case.abstention = DecisionAbstention(
            reason_code="ai-judgment-failed",
            reason=str(reason or "AI judgment failed."),
            details={"candidateFingerprint": subject_case.candidate_set.fingerprint},
        )
        subject_case.mark(SUBJECT_ABSTAINED, reason)
        subject_case.publication = publication_for_subject_case(subject_case, ABSTAIN)
        self._persist_subject(subject_case)
        return subject_case

    def ai_fallback_completed(
        self,
        request,
        context: Mapping[str, object],
        result,
        reason: str,
        connection=None,
    ) -> Optional[SubjectDecisionCase]:
        """Complete the case with a TypeDB-only publication when AI is unavailable."""

        case_id = self.case_id_from_context(context)
        if not case_id:
            return None
        subject_case_id = self.subject_case_id_from_context(context)
        if not subject_case_id:
            return None
        subject_case = self.required_subject(subject_case_id, context)
        if subject_case.publication is not None:
            return subject_case
        subject_case.ai_request_id = str(getattr(request, "request_id", "") or "")
        subject_case.notification_job_id = str(
            getattr(request, "notification_job_id", "") or ""
        )
        reason_text = str(reason or "AI unavailable")
        contract_invalid = any(
            token in reason_text.lower()
            for token in ("contract", "hypothesis", "evidence", "candidate fingerprint")
        )
        subject_case.abstention = DecisionAbstention(
            reason_code=(
                "ai-contract-invalid-review-only"
                if contract_invalid
                else "ai-unavailable-review-only"
            ),
            reason=reason_text,
            details={"candidateFingerprint": subject_case.candidate_set.fingerprint},
        )
        subject_case.mark(SUBJECT_REVIEW_ONLY, reason)
        subject_case.publication = publication_for_subject_case(
            subject_case,
            REVIEW_ONLY,
            explanation_snapshot={
                "reason": "AI를 사용하지 못해 TypeDB 관계를 검토 정보로만 제공합니다.",
                "aiAvailable": False,
            },
        )
        self._persist_subject(subject_case, connection=connection)
        return subject_case

    def enqueue_hypothesis_gap(self, subject_case_id: str) -> Dict[str, object]:
        """Publish proposal work after the canonical decision transaction commits."""

        enqueue = getattr(
            self.hypothesis_proposal_request_store,
            "enqueue_hypothesis_proposal_request",
            None,
        )
        if not callable(enqueue) or self.decision_episode_store is None:
            return {}
        subject_case = self.required_subject(subject_case_id)
        publication = subject_case.publication
        if not publication or not publication.decision_episode_id:
            return {}
        get_episode = getattr(self.decision_episode_store, "get", None)
        episode = get_episode(publication.decision_episode_id) if callable(get_episode) else None
        if episode is None:
            return {}
        reasoning_case = self.required(subject_case.batch_case_id)
        proposal_request = hypothesis_gap_request_from_subject_case(
            reasoning_case,
            subject_case,
            episode,
        )
        return enqueue(proposal_request) if proposal_request else {}

    def complete_without_ai(self, case_id: str, reason: str, source: str = "typedb-shadow") -> ReasoningCase:
        reasoning_case = self.required(case_id)
        if reasoning_case.stage == CASE_INFERENCE_COMPLETED:
            reasoning_case.transition(CASE_HYPOTHESES_READY, "no-action-hypothesis-set")
        for subject_case in self.subject_cases.for_batch(reasoning_case.case_id):
            if subject_case.stage in {
                SUBJECT_AI_PENDING, SUBJECT_PUBLISHED, SUBJECT_ABSTAINED,
                SUBJECT_OBSERVATION, SUBJECT_REVIEW_ONLY, SUBJECT_SUPPRESSED,
                SUBJECT_BLOCKED,
            }:
                continue
            outcome = (
                OBSERVATION
                if not subject_case.candidate_set.eligible_hypothesis_ids
                and subject_case.synthesis.graph_candidate_action == "NO_ACTION"
                else SUPPRESSED
            )
            target_stage = SUBJECT_OBSERVATION if outcome == OBSERVATION else SUBJECT_SUPPRESSED
            subject_case.mark(target_stage, reason, {"source": source})
            subject_case.publication = publication_for_subject_case(
                subject_case,
                outcome,
                explanation_snapshot={"reason": str(reason or ""), "source": source},
            )
            self._persist_subject(subject_case)
        if reasoning_case.stage in {
            CASE_HYPOTHESES_READY,
            CASE_DECISION_SYNTHESIZED,
            CASE_VALIDATED,
        }:
            reasoning_case.transition(CASE_COMPLETED, reason)
        self._persist(reasoning_case)
        return reasoning_case

    def batch_handoff_completed(self, case_id: str, reason: str) -> ReasoningCase:
        """Close the batch audit after per-subject work has been handed off."""

        reasoning_case = self.required(case_id)
        if reasoning_case.stage in {
            CASE_HYPOTHESES_READY,
            CASE_DECISION_SYNTHESIZED,
            CASE_VALIDATED,
        }:
            reasoning_case.transition(
                CASE_COMPLETED,
                reason,
                {
                    "subjectCaseCount": len(reasoning_case.subject_case_ids),
                    "ownsFinalDecision": False,
                },
            )
            self._persist(reasoning_case)
        return reasoning_case

    def expire_stale_cases(
        self,
        cutoff_iso: str,
        limit: int = 100,
        reason: str = "reasoning-case-stale-timeout",
    ) -> Dict[str, object]:
        """Close abandoned cases through the same terminal episode projection."""

        expire = getattr(self.repository, "expire_stale_nonterminal", None)
        if not callable(expire):
            return {"status": "not-supported", "expiredCount": 0, "caseIds": []}
        expired = list(expire(cutoff_iso, limit=limit, reason=reason) or [])
        return {
            "status": "expired" if expired else "unchanged",
            "expiredCount": len(expired),
            "caseIds": [item.case_id for item in expired],
            "cutoffAt": str(cutoff_iso or ""),
        }

    @staticmethod
    def validate_judgment(subject_case: SubjectDecisionCase, judgment: AIJudgmentResult):
        if judgment.action not in {"BUY", "ADD", "HOLD", "TRIM", "SELL", "AVOID", "WATCH"}:
            return False, "AI judgment action is outside the investment decision contract."
        candidate_set = subject_case.candidate_set
        if not candidate_set.valid:
            return False, "Persisted candidate set failed account, subject, or generation validation."
        eligible_ids = set(candidate_set.eligible_hypothesis_ids)
        if not eligible_ids:
            return False, "TypeDB hypothesis set is empty; AI publication is not allowed."
        if set(judgment.reviewed_hypothesis_ids) != eligible_ids:
            return False, "AI judgment did not review the exact eligible TypeDB hypothesis set."
        if str(judgment.comparison_state or "").strip().lower() != "completed":
            return False, "AI hypothesis comparison is not complete."
        if judgment.selected_hypothesis_id not in eligible_ids:
            return False, "AI selected hypothesis is not eligible in the subject decision candidate set."
        selected_hypothesis = next((
            item
            for item in candidate_set.hypotheses
            if item.hypothesis_id == judgment.selected_hypothesis_id
        ), None)
        reviews = {
            str(item.get("hypothesisId") or ""): dict(item)
            for item in judgment.hypothesis_reviews
            if str(item.get("hypothesisId") or "")
        }
        for hypothesis in candidate_set.hypotheses:
            if hypothesis.hypothesis_id not in eligible_ids:
                continue
            review = reviews.get(hypothesis.hypothesis_id) or {}
            if str(review.get("verdict") or "").strip().lower() not in {
                "supported", "weakened", "rejected", "unresolved",
            }:
                return False, "AI judgment did not classify every eligible hypothesis."
            if not str(review.get("reasoning") or "").strip():
                return False, "AI judgment did not explain every eligible hypothesis review."
            reviewed_support = set(
                review.get("reviewedSupportingEvidenceIds")
                or review.get("supportingEvidenceIds")
                or []
            )
            known_support = set(hypothesis.supporting_evidence_ids)
            if known_support and not reviewed_support.intersection(known_support):
                return False, "AI judgment did not review supporting evidence for every eligible hypothesis."
            reviewed_counter = set(
                review.get("reviewedCounterEvidenceIds")
                or review.get("counterEvidenceIds")
                or []
            )
            missing_counter = set(hypothesis.counter_evidence_ids) - reviewed_counter
            if missing_counter:
                return False, "AI judgment did not classify every counter-evidence identifier."
        if (
            selected_hypothesis
            and selected_hypothesis.candidate_action
            and selected_hypothesis.candidate_action != judgment.action
            and not str(judgment.rejected_candidate_reason or "").strip()
        ):
            return False, "AI judgment differs from the selected TypeDB hypothesis without an explicit disagreement reason."
        if not str(judgment.rationale or "").strip():
            return False, "AI judgment has no decision rationale."
        if not selected_hypothesis or not selected_hypothesis.causal_trace_ids:
            return False, "Selected TypeDB hypothesis has no causal trace lineage."
        known_support_ids = set(selected_hypothesis.supporting_evidence_ids)
        if not known_support_ids:
            return False, "Selected TypeDB hypothesis has no supporting evidence lineage."
        reviewed_support_ids = set(judgment.supporting_evidence_ids)
        if not reviewed_support_ids:
            return False, "AI judgment did not review a supporting evidence identifier."
        if not reviewed_support_ids.intersection(known_support_ids):
            return False, "AI judgment supporting evidence is outside the selected TypeDB hypothesis."
        synthesis = subject_case.synthesis
        if synthesis.action_authority != "originate":
            return False, "TypeDB relations may review or constrain a decision but cannot originate an investment action."
        if judgment.action in set(synthesis.blocked_actions):
            return False, "AI judgment action is blocked by the TypeDB action envelope."
        if synthesis.allowed_actions and judgment.action not in set(synthesis.allowed_actions):
            return False, "AI judgment action is outside the TypeDB action envelope."
        if not synthesis.graph_trace_complete:
            return False, "AI judgment synthesis has incomplete TypeDB graph lineage."
        if synthesis.judgement_blocked:
            return False, "AI judgment synthesis is blocked by its TypeDB decision contract."
        if judgment.action in {"BUY", "ADD", "TRIM", "SELL"} and not (
            judgment.next_observations or judgment.reversal_conditions
        ):
            return False, "Actionable AI judgment has no follow-up or reversal condition."
        if str(judgment.validation_state or "").lower() in {"blocked", "invalid", "failed", "error"}:
            return False, "AI judgment validation state blocks publication."
        return True, ""

    @staticmethod
    def compact_subject_context(subject_case: SubjectDecisionCase) -> Dict[str, object]:
        return {
            "subjectCaseId": subject_case.subject_case_id,
            "batchCaseId": subject_case.batch_case_id,
            "stage": subject_case.stage,
            "accountId": subject_case.account_id,
            "symbol": subject_case.symbol,
            "sourceAboxSnapshotId": subject_case.source_abox_snapshot_id,
            "sourceSubjectId": subject_case.source_subject_id,
            "sourceSubjectRevision": subject_case.source_subject_revision,
            "inferenceGenerationId": subject_case.inference_generation_id,
            "synthesisId": subject_case.synthesis.synthesis_id,
            "candidateSetId": subject_case.candidate_set.candidate_set_id,
            "candidateFingerprint": subject_case.candidate_set.fingerprint,
            "eligibleHypothesisIds": list(subject_case.candidate_set.eligible_hypothesis_ids),
            "allowedActions": list(subject_case.candidate_set.allowed_actions),
            "blockedActions": list(subject_case.candidate_set.blocked_actions),
            "actionAuthority": subject_case.synthesis.action_authority,
            "decisionEffect": subject_case.synthesis.decision_effect,
            "contractVersion": subject_case.contract_version,
        }

    @staticmethod
    def explanation_snapshot(
        subject_case: SubjectDecisionCase,
        episode: Mapping[str, object],
        context: Mapping[str, object] = None,
    ) -> Dict[str, object]:
        selected_id = str(subject_case.final_decision.selected_hypothesis_id if subject_case.final_decision else "")
        selected = next((
            item for item in subject_case.candidate_set.hypotheses
            if item.hypothesis_id == selected_id
        ), None)
        values = _mapping(context)
        relation = _mapping(values.get("ontologyRelationContext"))
        graph = _mapping(relation.get("graphStoreInference"))
        selected_rule_ids = set(selected.supporting_rule_ids if selected else ())
        selected_rule_proofs = [
            {
                "traceId": str(item.get("id") or item.get("traceId") or ""),
                "ruleId": str(item.get("ruleId") or item.get("sourceRuleId") or ""),
                "label": str(item.get("label") or ""),
                "matchedConditions": [
                    dict(condition)
                    for condition in item.get("matchedConditions") or []
                    if isinstance(condition, Mapping)
                ],
            }
            for item in graph.get("traces") or []
            if isinstance(item, Mapping)
            and str(item.get("ruleId") or item.get("sourceRuleId") or "") in selected_rule_ids
        ]
        explanation = _mapping(values.get("customerDeliveryExplanation"))
        notification_reasons = _customer_notification_reasons([
            str(item or "").strip()
            for item in explanation.get("lines") or explanation.get("reasons") or []
            if str(item or "").strip()
        ])
        if not notification_reasons:
            notification_reasons = _customer_notification_reasons([
                str(item.get("reason") or "").strip()
                for item in values.get("deliveryTriggerLedger") or []
                if isinstance(item, Mapping)
                and str(item.get("status") or "").lower() in {"matched", "released", "eligible"}
                and str(item.get("reason") or "").strip()
            ])
        if not notification_reasons and selected:
            notification_reasons = [
                "현재 데이터에서 '"
                + str(selected.label or selected.hypothesis_id)
                + "' 가설이 성립해 이전 판단과 다시 비교했습니다."
            ]
        selected_review = next((
            dict(item)
            for item in (subject_case.ai_judgment.hypothesis_reviews if subject_case.ai_judgment else ())
            if str(item.get("hypothesisId") or "") == selected_id
        ), {})
        return {
            "version": "decision-explanation-snapshot-v1",
            "accountId": subject_case.account_id,
            "symbol": subject_case.symbol,
            "action": subject_case.final_decision.action if subject_case.final_decision else "",
            "selectedHypothesisId": selected_id,
            "selectedHypothesisLabel": selected.label if selected else "",
            "selectedHypothesisCandidateAction": selected.candidate_action if selected else "",
            "selectedRuleIds": list(selected.supporting_rule_ids) if selected else [],
            "selectedRuleProofs": selected_rule_proofs,
            "supportingEvidenceIds": list(selected.supporting_evidence_ids) if selected else [],
            "counterEvidenceIds": list(selected.counter_evidence_ids) if selected else [],
            "selectedHypothesisReview": selected_review,
            "decisionRationale": subject_case.ai_judgment.rationale if subject_case.ai_judgment else "",
            "missingData": list(subject_case.candidate_set.missing_data),
            "nextChecks": list(subject_case.synthesis.next_checks),
            "reversalConditions": list(subject_case.synthesis.reversal_conditions),
            "candidateFingerprint": subject_case.candidate_set.fingerprint,
            "sourceAboxSnapshotId": subject_case.source_abox_snapshot_id,
            "inferenceGenerationId": subject_case.inference_generation_id,
            "decisionEpisodeId": str(episode.get("episodeId") or ""),
            "notificationReasons": notification_reasons[:5],
        }

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
            "subjectCaseIds": list(reasoning_case.subject_case_ids),
            "hypothesisIds": [item.hypothesis_id for item in reasoning_case.hypotheses],
            "hypothesisCount": len(reasoning_case.hypotheses),
            "decisionSynthesisCount": len(reasoning_case.decision_syntheses),
            "decisionSyntheses": [
                {
                    "synthesisId": item.synthesis_id,
                    "accountId": item.account_id,
                    "symbol": item.symbol,
                    "graphCandidateAction": item.graph_candidate_action,
                    "allowedActions": list(item.allowed_actions),
                    "blockedActions": list(item.blocked_actions),
                    "eligibleHypothesisIds": list(item.eligible_hypothesis_ids),
                    "actionAuthority": item.action_authority,
                    "decisionEffect": item.decision_effect,
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

    def required_subject(
        self,
        subject_or_batch_case_id: str,
        context: Mapping[str, object] = None,
    ) -> SubjectDecisionCase:
        explicit = self.subject_case_id_from_context(context or {})
        subject_case = self.subject_cases.get(explicit or str(subject_or_batch_case_id or ""))
        if subject_case:
            return subject_case
        batch_case_id = str(subject_or_batch_case_id or "")
        matches = list(self.subject_cases.for_batch(batch_case_id))
        values = _mapping(context)
        symbol = str(values.get("symbol") or values.get("rawSymbol") or "").upper()
        account_id = str(values.get("accountId") or "")
        generation_id = str(
            values.get("inferenceGenerationId")
            or _mapping(values.get("ontologyRelationContext")).get("inferenceGenerationId")
            or ""
        )
        scoped = [
            item for item in matches
            if (not symbol or item.symbol == symbol)
            and (not account_id or item.account_id == account_id)
            and (not generation_id or item.inference_generation_id == generation_id)
        ]
        if len(scoped) == 1:
            return scoped[0]
        raise ValueError(
            "Unknown or ambiguous subject decision case: "
            + str(explicit or subject_or_batch_case_id or "")
        )

    @staticmethod
    def case_id_from_context(context: Mapping[str, object]) -> str:
        values = _mapping(context)
        return str(
            values.get("investmentReasoningCaseId")
            or _mapping(values.get("investmentReasoningCase")).get("caseId")
            or ""
        )

    @staticmethod
    def subject_case_id_from_context(context: Mapping[str, object]) -> str:
        values = _mapping(context)
        return str(
            values.get("investmentSubjectDecisionCaseId")
            or _mapping(values.get("investmentSubjectDecisionCase")).get("subjectCaseId")
            or _mapping(values.get("ontologyRelationContext")).get("investmentSubjectDecisionCaseId")
            or ""
        )

    def explain(self, case_id: str) -> Dict[str, object]:
        subject_case = self.subject_cases.get(str(case_id or ""))
        if subject_case:
            return subject_case.to_dict()
        reasoning_case = self.required(case_id)
        return {
            **reasoning_case.to_dict(),
            "subjectDecisionCases": [
                item.to_dict() for item in self.subject_cases.for_batch(reasoning_case.case_id)
            ],
        }
