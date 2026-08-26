"""Per-subject decision state derived from one batched reasoning run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Dict, Iterable, Mapping, Optional, Tuple

from .contracts import AIJudgmentResult, DecisionSynthesis, FinalDecision, HypothesisRecord


SUBJECT_CASE_VERSION = "investment-subject-decision-case-v1"
CANDIDATE_SET_VERSION = "investment-candidate-set-snapshot-v1"
PUBLICATION_VERSION = "investment-decision-publication-v1"

SUBJECT_CREATED = "CREATED"
SUBJECT_READY = "READY"
SUBJECT_AI_PENDING = "AI_PENDING"
SUBJECT_AI_COMPLETED = "AI_COMPLETED"
SUBJECT_VALIDATED = "VALIDATED"
SUBJECT_PUBLISHED = "PUBLISHED"
SUBJECT_REVIEW_ONLY = "REVIEW_ONLY"
SUBJECT_ABSTAINED = "ABSTAINED"
SUBJECT_OBSERVATION = "OBSERVATION"
SUBJECT_SUPPRESSED = "SUPPRESSED"
SUBJECT_BLOCKED = "BLOCKED"

FINAL_DECISION = "FINAL_DECISION"
REVIEW_ONLY = "REVIEW_ONLY"
ABSTAIN = "ABSTAIN"
OBSERVATION = "OBSERVATION"
SUPPRESSED = "SUPPRESSED"

FINAL_INVESTMENT_ACTIONS = frozenset({"BUY", "ADD", "HOLD", "TRIM", "SELL", "AVOID", "WATCH"})
EXECUTABLE_INVESTMENT_ACTIONS = frozenset({"BUY", "ADD", "TRIM", "SELL"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_id(prefix: str, *values: object) -> str:
    material = json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return prefix + ":" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _fingerprint(value: object) -> str:
    material = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _texts(values: Iterable[object]) -> Tuple[str, ...]:
    return tuple(sorted({str(value or "").strip() for value in values or [] if str(value or "").strip()}))


@dataclass(frozen=True)
class CandidateSetSnapshot:
    candidate_set_id: str
    fingerprint: str
    account_id: str
    symbol: str
    source_abox_snapshot_id: str
    inference_generation_id: str
    synthesis_id: str
    hypotheses: Tuple[HypothesisRecord, ...] = ()
    eligible_hypothesis_ids: Tuple[str, ...] = ()
    reference_hypothesis_ids: Tuple[str, ...] = ()
    allowed_actions: Tuple[str, ...] = ()
    blocked_actions: Tuple[str, ...] = ()
    missing_data: Tuple[str, ...] = ()
    validation_errors: Tuple[str, ...] = ()
    created_at: str = ""
    version: str = CANDIDATE_SET_VERSION

    @property
    def valid(self) -> bool:
        return not self.validation_errors

    @classmethod
    def create(
        cls,
        batch_case_id: str,
        synthesis: DecisionSynthesis,
        hypotheses: Iterable[HypothesisRecord],
    ) -> "CandidateSetSnapshot":
        eligible_ids = _texts(synthesis.eligible_hypothesis_ids)
        reference_ids = _texts(synthesis.reference_hypothesis_ids)
        candidate_ids = set(eligible_ids).union(reference_ids)
        scoped = []
        scope_errors = []
        for hypothesis in hypotheses or []:
            if hypothesis.hypothesis_id not in candidate_ids:
                continue
            mismatches = []
            if hypothesis.account_id != synthesis.account_id:
                mismatches.append("account")
            if hypothesis.subject_symbol.upper() != synthesis.symbol.upper():
                mismatches.append("symbol")
            if hypothesis.inference_generation_id != synthesis.inference_generation_id:
                mismatches.append("generation")
            if mismatches:
                scope_errors.append(
                    "cross-scope-hypothesis:"
                    + hypothesis.hypothesis_id
                    + ":"
                    + ",".join(mismatches)
                )
                continue
            scoped.append(hypothesis)
        scoped.sort(key=lambda item: item.hypothesis_id)
        available_ids = {item.hypothesis_id for item in scoped}
        missing_eligible = sorted(set(eligible_ids) - available_ids)
        if missing_eligible:
            scope_errors.append("eligible-hypothesis-missing:" + ",".join(missing_eligible))
        material = {
            "batchCaseId": str(batch_case_id or ""),
            "accountId": synthesis.account_id,
            "symbol": synthesis.symbol.upper(),
            "sourceAboxSnapshotId": synthesis.source_abox_snapshot_id,
            "inferenceGenerationId": synthesis.inference_generation_id,
            "synthesisId": synthesis.synthesis_id,
            "synthesis": synthesis.to_dict(),
            "eligibleHypothesisIds": list(eligible_ids),
            "referenceHypothesisIds": list(reference_ids),
            "allowedActions": list(_texts(synthesis.allowed_actions)),
            "blockedActions": list(_texts(synthesis.blocked_actions)),
            "hypotheses": [item.to_dict() for item in scoped],
            "missingData": list(_texts(synthesis.missing_data)),
        }
        fingerprint = _fingerprint(material)
        return cls(
            candidate_set_id=_stable_id("candidate-set", batch_case_id, synthesis.synthesis_id, fingerprint),
            fingerprint=fingerprint,
            account_id=synthesis.account_id,
            symbol=synthesis.symbol.upper(),
            source_abox_snapshot_id=synthesis.source_abox_snapshot_id,
            inference_generation_id=synthesis.inference_generation_id,
            synthesis_id=synthesis.synthesis_id,
            hypotheses=tuple(scoped),
            eligible_hypothesis_ids=eligible_ids,
            reference_hypothesis_ids=reference_ids,
            allowed_actions=_texts(synthesis.allowed_actions),
            blocked_actions=_texts(synthesis.blocked_actions),
            missing_data=_texts(synthesis.missing_data),
            validation_errors=_texts(scope_errors),
            created_at=_now(),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "candidateSetId": self.candidate_set_id,
            "fingerprint": self.fingerprint,
            "accountId": self.account_id,
            "symbol": self.symbol,
            "sourceAboxSnapshotId": self.source_abox_snapshot_id,
            "inferenceGenerationId": self.inference_generation_id,
            "synthesisId": self.synthesis_id,
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "eligibleHypothesisIds": list(self.eligible_hypothesis_ids),
            "referenceHypothesisIds": list(self.reference_hypothesis_ids),
            "allowedActions": list(self.allowed_actions),
            "blockedActions": list(self.blocked_actions),
            "missingData": list(self.missing_data),
            "validationErrors": list(self.validation_errors),
            "createdAt": self.created_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CandidateSetSnapshot":
        payload = dict(value or {})
        return cls(
            candidate_set_id=str(payload.get("candidateSetId") or ""),
            fingerprint=str(payload.get("fingerprint") or ""),
            account_id=str(payload.get("accountId") or ""),
            symbol=str(payload.get("symbol") or "").upper(),
            source_abox_snapshot_id=str(payload.get("sourceAboxSnapshotId") or ""),
            inference_generation_id=str(payload.get("inferenceGenerationId") or ""),
            synthesis_id=str(payload.get("synthesisId") or ""),
            hypotheses=tuple(
                HypothesisRecord.from_dict(item)
                for item in payload.get("hypotheses") or []
                if isinstance(item, Mapping)
            ),
            eligible_hypothesis_ids=_texts(payload.get("eligibleHypothesisIds") or []),
            reference_hypothesis_ids=_texts(payload.get("referenceHypothesisIds") or []),
            allowed_actions=_texts(payload.get("allowedActions") or []),
            blocked_actions=_texts(payload.get("blockedActions") or []),
            missing_data=_texts(payload.get("missingData") or []),
            validation_errors=_texts(payload.get("validationErrors") or []),
            created_at=str(payload.get("createdAt") or ""),
            version=str(payload.get("version") or CANDIDATE_SET_VERSION),
        )


@dataclass(frozen=True)
class DecisionAbstention:
    reason_code: str
    reason: str
    details: Dict[str, object] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, object]:
        return {
            "reasonCode": self.reason_code,
            "reason": self.reason,
            "details": dict(self.details or {}),
            "createdAt": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "DecisionAbstention":
        payload = dict(value or {})
        return cls(
            reason_code=str(payload.get("reasonCode") or ""),
            reason=str(payload.get("reason") or ""),
            details=dict(payload.get("details") or {}),
            created_at=str(payload.get("createdAt") or _now()),
        )


@dataclass(frozen=True)
class DecisionPublication:
    publication_id: str
    subject_case_id: str
    outcome_kind: str
    fingerprint: str
    decision_episode_id: str = ""
    notification_job_id: str = ""
    explanation_snapshot: Dict[str, object] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    delivered_at: str = ""
    version: str = PUBLICATION_VERSION

    def to_dict(self) -> Dict[str, object]:
        return {
            "publicationId": self.publication_id,
            "subjectCaseId": self.subject_case_id,
            "outcomeKind": self.outcome_kind,
            "fingerprint": self.fingerprint,
            "decisionEpisodeId": self.decision_episode_id,
            "notificationJobId": self.notification_job_id,
            "explanationSnapshot": dict(self.explanation_snapshot or {}),
            "createdAt": self.created_at,
            "deliveredAt": self.delivered_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "DecisionPublication":
        payload = dict(value or {})
        return cls(
            publication_id=str(payload.get("publicationId") or ""),
            subject_case_id=str(payload.get("subjectCaseId") or ""),
            outcome_kind=str(payload.get("outcomeKind") or ""),
            fingerprint=str(payload.get("fingerprint") or ""),
            decision_episode_id=str(payload.get("decisionEpisodeId") or ""),
            notification_job_id=str(payload.get("notificationJobId") or ""),
            explanation_snapshot=dict(payload.get("explanationSnapshot") or {}),
            created_at=str(payload.get("createdAt") or _now()),
            delivered_at=str(payload.get("deliveredAt") or ""),
            version=str(payload.get("version") or PUBLICATION_VERSION),
        )


@dataclass
class SubjectDecisionCase:
    subject_case_id: str
    batch_case_id: str
    request_id: str
    deployment_id: str
    release_fingerprint: str
    account_id: str
    symbol: str
    source_abox_snapshot_id: str
    inference_generation_id: str
    synthesis: DecisionSynthesis
    candidate_set: CandidateSetSnapshot
    stage: str = SUBJECT_CREATED
    ai_request_id: str = ""
    notification_job_id: str = ""
    ai_judgment: Optional[AIJudgmentResult] = None
    final_decision: Optional[FinalDecision] = None
    abstention: Optional[DecisionAbstention] = None
    publication: Optional[DecisionPublication] = None
    errors: Tuple[Dict[str, object], ...] = ()
    created_at: str = field(default_factory=_now)
    updated_at: str = ""
    completed_at: str = ""
    version: int = 1
    contract_version: str = SUBJECT_CASE_VERSION

    @classmethod
    def create(
        cls,
        batch_case,
        synthesis: DecisionSynthesis,
        hypotheses: Iterable[HypothesisRecord],
    ) -> "SubjectDecisionCase":
        candidate_set = CandidateSetSnapshot.create(batch_case.case_id, synthesis, hypotheses)
        subject_case_id = _stable_id(
            "subject-decision-case",
            batch_case.case_id,
            synthesis.account_id,
            synthesis.symbol.upper(),
            synthesis.inference_generation_id,
            synthesis.synthesis_id,
        )
        stamp = _now()
        stage = SUBJECT_READY if candidate_set.valid else SUBJECT_BLOCKED
        abstention = None
        errors: Tuple[Dict[str, object], ...] = ()
        completed_at = ""
        if not candidate_set.valid:
            reason = "Candidate set violates the account, subject, or generation scope."
            abstention = DecisionAbstention(
                reason_code="candidate-scope-invalid",
                reason=reason,
                details={"validationErrors": list(candidate_set.validation_errors)},
            )
            errors = ({"stage": SUBJECT_BLOCKED, "reason": reason, "at": stamp},)
            completed_at = stamp
        return cls(
            subject_case_id=subject_case_id,
            batch_case_id=batch_case.case_id,
            request_id=batch_case.request_id,
            deployment_id=batch_case.deployment_id,
            release_fingerprint=batch_case.release_fingerprint,
            account_id=synthesis.account_id,
            symbol=synthesis.symbol.upper(),
            source_abox_snapshot_id=synthesis.source_abox_snapshot_id,
            inference_generation_id=synthesis.inference_generation_id,
            synthesis=synthesis,
            candidate_set=candidate_set,
            stage=stage,
            abstention=abstention,
            errors=errors,
            created_at=stamp,
            updated_at=stamp,
            completed_at=completed_at,
        )

    def mark(self, stage: str, reason: str = "", details: Mapping[str, object] = None) -> None:
        stamp = _now()
        self.stage = str(stage or self.stage).upper()
        self.updated_at = stamp
        self.version += 1
        if reason:
            self.errors = tuple([*self.errors, {
                "stage": self.stage,
                "reason": str(reason or "")[:500],
                "details": dict(details or {}),
                "at": stamp,
            }][-20:])
        if self.stage in {
            SUBJECT_PUBLISHED, SUBJECT_REVIEW_ONLY, SUBJECT_ABSTAINED,
            SUBJECT_OBSERVATION, SUBJECT_SUPPRESSED, SUBJECT_BLOCKED,
        }:
            self.completed_at = stamp

    def to_dict(self) -> Dict[str, object]:
        return {
            "subjectCaseId": self.subject_case_id,
            "batchCaseId": self.batch_case_id,
            "requestId": self.request_id,
            "deploymentId": self.deployment_id,
            "releaseFingerprint": self.release_fingerprint,
            "accountId": self.account_id,
            "symbol": self.symbol,
            "sourceAboxSnapshotId": self.source_abox_snapshot_id,
            "inferenceGenerationId": self.inference_generation_id,
            "synthesis": self.synthesis.to_dict(),
            "candidateSet": self.candidate_set.to_dict(),
            "stage": self.stage,
            "aiRequestId": self.ai_request_id,
            "notificationJobId": self.notification_job_id,
            "aiJudgment": self.ai_judgment.to_dict() if self.ai_judgment else {},
            "finalDecision": self.final_decision.to_dict() if self.final_decision else {},
            "abstention": self.abstention.to_dict() if self.abstention else {},
            "publication": self.publication.to_dict() if self.publication else {},
            "errors": [dict(item) for item in self.errors],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "completedAt": self.completed_at,
            "version": self.version,
            "contractVersion": self.contract_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SubjectDecisionCase":
        payload = dict(value or {})
        judgment = payload.get("aiJudgment") or {}
        decision = payload.get("finalDecision") or {}
        abstention = payload.get("abstention") or {}
        publication = payload.get("publication") or {}
        return cls(
            subject_case_id=str(payload.get("subjectCaseId") or ""),
            batch_case_id=str(payload.get("batchCaseId") or ""),
            request_id=str(payload.get("requestId") or ""),
            deployment_id=str(payload.get("deploymentId") or ""),
            release_fingerprint=str(payload.get("releaseFingerprint") or ""),
            account_id=str(payload.get("accountId") or ""),
            symbol=str(payload.get("symbol") or "").upper(),
            source_abox_snapshot_id=str(payload.get("sourceAboxSnapshotId") or ""),
            inference_generation_id=str(payload.get("inferenceGenerationId") or ""),
            synthesis=DecisionSynthesis.from_dict(payload.get("synthesis") or {}),
            candidate_set=CandidateSetSnapshot.from_dict(payload.get("candidateSet") or {}),
            stage=str(payload.get("stage") or SUBJECT_CREATED),
            ai_request_id=str(payload.get("aiRequestId") or ""),
            notification_job_id=str(payload.get("notificationJobId") or ""),
            ai_judgment=AIJudgmentResult.from_dict(judgment) if judgment else None,
            final_decision=FinalDecision.from_dict(decision) if decision else None,
            abstention=DecisionAbstention.from_dict(abstention) if abstention else None,
            publication=DecisionPublication.from_dict(publication) if publication else None,
            errors=tuple(dict(item) for item in payload.get("errors") or [] if isinstance(item, Mapping)),
            created_at=str(payload.get("createdAt") or _now()),
            updated_at=str(payload.get("updatedAt") or ""),
            completed_at=str(payload.get("completedAt") or ""),
            version=max(1, int(payload.get("version") or 1)),
            contract_version=str(payload.get("contractVersion") or SUBJECT_CASE_VERSION),
        )


def publication_for_subject_case(
    subject_case: SubjectDecisionCase,
    outcome_kind: str,
    decision_episode_id: str = "",
    explanation_snapshot: Mapping[str, object] = None,
) -> DecisionPublication:
    abstention = subject_case.abstention
    material = {
        "subjectCaseId": subject_case.subject_case_id,
        "candidateFingerprint": subject_case.candidate_set.fingerprint,
        "outcomeKind": str(outcome_kind or ""),
        "decisionEpisodeId": str(decision_episode_id or ""),
        "notificationJobId": subject_case.notification_job_id,
        "aiResultId": subject_case.ai_judgment.result_id if subject_case.ai_judgment else "",
        "finalDecision": subject_case.final_decision.to_dict() if subject_case.final_decision else {},
        "abstention": {
            "reasonCode": abstention.reason_code,
            "reason": abstention.reason,
            "details": dict(abstention.details or {}),
        } if abstention else {},
    }
    fingerprint = _fingerprint(material)
    return DecisionPublication(
        publication_id=_stable_id("decision-publication", subject_case.subject_case_id, fingerprint),
        subject_case_id=subject_case.subject_case_id,
        outcome_kind=str(outcome_kind or ""),
        fingerprint=fingerprint,
        decision_episode_id=str(decision_episode_id or ""),
        notification_job_id=subject_case.notification_job_id,
        explanation_snapshot=dict(explanation_snapshot or {}),
    )
