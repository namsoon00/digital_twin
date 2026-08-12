import json
import re
import urllib.parse
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Tuple

from .data_freshness import parse_datetime
from .investment_brain import stable_id, utc_now_iso
from .investment_research import NewsCollectionTarget, ResearchEvidence, target_aliases
from . import news_analysis as news_domain
from ..news_intelligence.domain.provenance import resolve_source_provenance


PRIMARY_SOURCE_MARKERS = (
    "opendart",
    "dart",
    "sec edgar",
    "sec.gov",
    "bok",
    "한국은행",
    "exchange",
    "거래소",
    "investor relations",
    "company ir",
)

CLAIM_LEDGER_VERSION = "research-claim-ledger-v1"
CLAIM_STATES = (
    "reported",
    "verified-primary",
    "corroborated",
    "conflicted",
    "superseded",
    "expired",
    "rejected",
)

OFFICIAL_EVIDENCE_KINDS = {"disclosure", "filing"}
CORRECTION_MARKERS = (
    "correction",
    "corrected",
    "clarification",
    "retraction",
    "withdrawn",
    "정정",
    "정정공시",
    "정정 보도",
    "오보",
    "철회",
    "바로잡",
)
CLAIM_STOP_WORDS = {
    "about", "after", "ahead", "amid", "and", "article", "before", "company",
    "for", "from", "has", "into", "its", "latest", "news", "over", "said",
    "that", "the", "this", "with", "will", "있다", "관련", "기사", "뉴스",
    "대한", "대해", "이번", "통해", "한다", "했다", "하는", "에서", "으로",
}

# Delivery channels frequently keep their own name while linking to the
# original publisher.  These aliases make the independence test conservative:
# a syndication wrapper must not become a second source merely by changing the
# visible source label or adding tracking parameters to the same URL.
GENERIC_DELIVERY_CHANNELS = {
    "google news", "google news kr", "google_rss", "yahoo finance",
    "yahoo search", "gdelt",
}
PUBLISHER_ORIGIN_ALIASES = {
    "finance.yahoo.com": "yahoo-finance",
    "yahoo finance": "yahoo-finance",
    "yahoo search": "yahoo-finance",
    "fool.com": "motley-fool",
    "motley fool": "motley-fool",
    "simplywall.st": "simply-wall-st",
    "simply wall st": "simply-wall-st",
    "insidermonkey.com": "insider-monkey",
    "insider monkey": "insider-monkey",
    "reuters.com": "reuters",
    "reuters": "reuters",
}
URL_TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "guccounter", "guce_referrer", "guce_referrer_sig",
    "mc_cid", "mc_eid", "ocid", "output", "src", "taid", "tsrc",
}
OFFICIAL_DOCUMENT_MIN_CHARS = 120


RESEARCH_REASONING_HANDOFF_VERSION = "research-reasoning-generation-v1"
RESEARCH_REASONING_HANDOFF_STATES = {
    "not-requested",
    "pending",
    "applied",
    "blocked",
}

HYPOTHESIS_RESEARCH_PLANNING_STATES = {
    "rule-derived",
    "ai-augmented",
    "planner-disabled",
    "planner-unavailable",
    "planner-failed",
    "no-valid-guidance",
    "not-required",
}


def unique_texts(values: Iterable[object], limit: int = 200) -> List[str]:
    """Keep persisted evidence and generation references deterministic."""
    result: List[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= max(1, int(limit or 1)):
            break
    return result


@dataclass(frozen=True)
class ReasoningGeneration:
    """One active TypeDB inference generation and its ABox source Manifest."""

    inference_generation_id: str = ""
    source_abox_snapshot_id: str = ""
    world_id: str = ""
    generation_aligned: bool = False
    observed_at: str = ""

    def complete(self) -> bool:
        return bool(
            self.inference_generation_id
            and self.source_abox_snapshot_id
            and self.world_id
            and self.generation_aligned
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "inferenceGenerationId": self.inference_generation_id,
            "sourceAboxSnapshotId": self.source_abox_snapshot_id,
            "worldId": self.world_id,
            "generationAligned": bool(self.generation_aligned),
            "observedAt": self.observed_at,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, object] = None):
        payload = dict(payload or {})
        return cls(
            inference_generation_id=str(
                payload.get("inferenceGenerationId")
                or payload.get("inference_generation_id")
                or ""
            ).strip(),
            source_abox_snapshot_id=str(
                payload.get("sourceAboxSnapshotId")
                or payload.get("source_abox_snapshot_id")
                or payload.get("aboxSnapshotId")
                or payload.get("abox_snapshot_id")
                or ""
            ).strip(),
            world_id=str(payload.get("worldId") or payload.get("world_id") or "").strip(),
            generation_aligned=bool(
                payload.get("generationAligned")
                if "generationAligned" in payload
                else payload.get("generation_aligned")
            ),
            observed_at=str(
                payload.get("observedAt")
                or payload.get("observed_at")
                or payload.get("inferenceGenerationAt")
                or payload.get("inference_generation_at")
                or ""
            ).strip(),
        )


@dataclass(frozen=True)
class ResearchReasoningHandoff:
    """Auditable bridge from verified evidence to a new active TypeDB generation.

    A research result is not an investment input merely because it was stored.
    It becomes eligible only after the exact account world has materialized a
    newer, aligned InferenceBox from a newer ABox Manifest.
    """

    request_id: str = ""
    source_generation: ReasoningGeneration = field(default_factory=ReasoningGeneration)
    changed_evidence_ids: List[str] = field(default_factory=list)
    status: str = "not-requested"
    applied_generation: ReasoningGeneration = field(default_factory=ReasoningGeneration)
    reason: str = ""
    requested_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": RESEARCH_REASONING_HANDOFF_VERSION,
            "requestId": self.request_id,
            "sourceGeneration": self.source_generation.to_dict(),
            "changedEvidenceIds": list(self.changed_evidence_ids),
            "status": self.status,
            "appliedGeneration": self.applied_generation.to_dict(),
            "reason": self.reason,
            "requestedAt": self.requested_at,
            "completedAt": self.completed_at,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, object] = None):
        payload = dict(payload or {})
        source = payload.get("sourceGeneration") or payload.get("source_generation") or {}
        applied = payload.get("appliedGeneration") or payload.get("applied_generation") or {}
        status = str(payload.get("status") or "not-requested").strip().lower()
        if status not in RESEARCH_REASONING_HANDOFF_STATES:
            status = "not-requested"
        return cls(
            request_id=str(payload.get("requestId") or payload.get("request_id") or "").strip(),
            source_generation=ReasoningGeneration.from_dict(source if isinstance(source, dict) else {}),
            changed_evidence_ids=unique_texts(
                payload.get("changedEvidenceIds") or payload.get("changed_evidence_ids") or []
            ),
            status=status,
            applied_generation=ReasoningGeneration.from_dict(applied if isinstance(applied, dict) else {}),
            reason=str(payload.get("reason") or "").strip(),
            requested_at=str(payload.get("requestedAt") or payload.get("requested_at") or "").strip(),
            completed_at=str(payload.get("completedAt") or payload.get("completed_at") or "").strip(),
        )

    def requested(self, changed_evidence_ids: Iterable[object]) -> "ResearchReasoningHandoff":
        evidence_ids = unique_texts(changed_evidence_ids)
        if not evidence_ids:
            return replace(
                self,
                changed_evidence_ids=[],
                status="not-requested",
                reason="검증된 근거의 내용 변경이 없어 기존 추론 세대를 유지합니다.",
                requested_at="",
                completed_at="",
            )
        if not self.source_generation.complete():
            return replace(
                self,
                changed_evidence_ids=evidence_ids,
                status="blocked",
                reason="검증 시작 시점의 활성 TypeDB 세대 참조가 완전하지 않아 새 근거를 투자 판단으로 승격하지 않습니다.",
                requested_at=utc_now_iso(),
                completed_at="",
            )
        return replace(
            self,
            changed_evidence_ids=evidence_ids,
            status="pending",
            reason="검증 근거를 새 ABox Manifest에 반영하고 같은 계정 월드의 TypeDB 재추론 완료를 기다립니다.",
            requested_at=utc_now_iso(),
            completed_at="",
        )

    def applied(self) -> bool:
        return self.status == "applied" and self.applied_generation.complete()


def reasoning_handoff_from_context(
    run_id: str,
    account_id: str,
    symbol: str,
    context: Dict[str, object] = None,
) -> ResearchReasoningHandoff:
    payload = dict(context or {})
    source_payload = payload.get("reasoningGeneration") or payload.get("reasoning_generation") or payload
    source = ReasoningGeneration.from_dict(source_payload if isinstance(source_payload, dict) else {})
    request_id = stable_id(
        "research-reasoning-handoff",
        run_id,
        account_id,
        str(symbol or "").upper().strip(),
        source.inference_generation_id,
        source.source_abox_snapshot_id,
        source.world_id,
    )
    return ResearchReasoningHandoff(
        request_id=request_id,
        source_generation=source,
    )


def complete_reasoning_handoff(
    handoff: ResearchReasoningHandoff,
    applied_generation: ReasoningGeneration,
    reason: str = "",
) -> ResearchReasoningHandoff:
    """Accept only a newer, aligned generation in the original portfolio world."""
    current = handoff or ResearchReasoningHandoff()
    applied = applied_generation or ReasoningGeneration()
    if not current.changed_evidence_ids:
        return replace(
            current,
            status="not-requested",
            applied_generation=applied,
            reason=reason or "변경된 검증 근거가 없어 재추론 전환이 필요하지 않습니다.",
            completed_at=utc_now_iso(),
        )
    if not current.source_generation.complete():
        return replace(
            current,
            status="blocked",
            applied_generation=applied,
            reason=reason or "기준 InferenceBox/ABox 세대가 완전하지 않아 재추론 결과를 연결하지 않습니다.",
            completed_at=utc_now_iso(),
        )
    if not applied.complete():
        return replace(
            current,
            status="blocked",
            applied_generation=applied,
            reason=reason or "새 TypeDB InferenceBox가 활성 ABox와 정렬되었다는 증거가 없습니다.",
            completed_at=utc_now_iso(),
        )
    if current.source_generation.world_id != applied.world_id:
        return replace(
            current,
            status="blocked",
            applied_generation=applied,
            reason=reason or "다른 계정 월드의 재추론 결과이므로 검증 근거를 연결하지 않습니다.",
            completed_at=utc_now_iso(),
        )
    if current.source_generation.source_abox_snapshot_id == applied.source_abox_snapshot_id:
        return replace(
            current,
            status="blocked",
            applied_generation=applied,
            reason=reason or "새 근거가 이전 ABox Manifest와 같은 세대에 머물러 재추론 완료로 처리하지 않습니다.",
            completed_at=utc_now_iso(),
        )
    if current.source_generation.inference_generation_id == applied.inference_generation_id:
        return replace(
            current,
            status="blocked",
            applied_generation=applied,
            reason=reason or "새 ABox에 대응하는 새 InferenceBox 세대가 생성되지 않아 투자 판단을 갱신하지 않습니다.",
            completed_at=utc_now_iso(),
        )
    return replace(
        current,
        status="applied",
        applied_generation=applied,
        reason=reason or "검증 근거가 새 ABox Manifest와 정렬된 TypeDB InferenceBox 세대에 반영됐습니다.",
        completed_at=utc_now_iso(),
    )


@dataclass(frozen=True)
class HypothesisResearchBrief:
    """The bounded, graph-derived research surface for one ResearchRun.

    It is intentionally not a new investment opinion.  The brief preserves
    the active TypeDB hypothesis candidates, their counter-candidates and
    evidence gaps so an AI may plan collection without inventing a fact or
    bypassing the active ABox/InferenceBox generation.
    """

    hypothesis_set_id: str = ""
    reasoning_generation: ReasoningGeneration = field(default_factory=ReasoningGeneration)
    candidate_hypotheses: List[Dict[str, object]] = field(default_factory=list)
    evidence_gaps: List[str] = field(default_factory=list)
    baseline_plan_id: str = ""
    planning_status: str = "rule-derived"
    planning_source: str = "typedb-hypothesis-set"
    planning_audit: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "hypothesisSetId": self.hypothesis_set_id,
            "reasoningGeneration": self.reasoning_generation.to_dict(),
            "candidateHypotheses": [dict(item) for item in self.candidate_hypotheses],
            "evidenceGaps": list(self.evidence_gaps),
            "baselinePlanId": self.baseline_plan_id,
            "planningStatus": self.planning_status,
            "planningSource": self.planning_source,
            "planningAudit": dict(self.planning_audit),
            "decisionEligibility": "research-only",
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, object] = None):
        payload = dict(payload or {})
        status = str(payload.get("planningStatus") or payload.get("planning_status") or "rule-derived").strip()
        if status not in HYPOTHESIS_RESEARCH_PLANNING_STATES:
            status = "rule-derived"
        candidates = [
            dict(item) for item in payload.get("candidateHypotheses") or payload.get("candidate_hypotheses") or []
            if isinstance(item, dict)
        ]
        generation = payload.get("reasoningGeneration") or payload.get("reasoning_generation") or {}
        return cls(
            hypothesis_set_id=str(payload.get("hypothesisSetId") or payload.get("hypothesis_set_id") or "").strip(),
            reasoning_generation=ReasoningGeneration.from_dict(generation if isinstance(generation, dict) else {}),
            candidate_hypotheses=candidates,
            evidence_gaps=unique_texts(payload.get("evidenceGaps") or payload.get("evidence_gaps") or [], 40),
            baseline_plan_id=str(payload.get("baselinePlanId") or payload.get("baseline_plan_id") or "").strip(),
            planning_status=status,
            planning_source=str(payload.get("planningSource") or payload.get("planning_source") or "typedb-hypothesis-set").strip(),
            planning_audit=dict(payload.get("planningAudit") or payload.get("planning_audit") or {}),
        )

    def with_planning(
        self,
        status: str,
        source: str,
        audit: Dict[str, object] = None,
    ) -> "HypothesisResearchBrief":
        normalized = str(status or "").strip()
        if normalized not in HYPOTHESIS_RESEARCH_PLANNING_STATES:
            normalized = "no-valid-guidance"
        return replace(
            self,
            planning_status=normalized,
            planning_source=str(source or self.planning_source or "typedb-hypothesis-set").strip(),
            planning_audit=dict(audit or {}),
        )


def hypothesis_research_brief_from_brain(brain: Dict[str, object] = None) -> HypothesisResearchBrief:
    """Build a small research-only view from TypeDB-derived hypotheses."""

    context = dict(brain or {})
    hypothesis_set = context.get("hypothesisSet") if isinstance(context.get("hypothesisSet"), dict) else {}
    plan = context.get("researchPlan") if isinstance(context.get("researchPlan"), dict) else {}
    generation = ReasoningGeneration.from_dict(
        context.get("reasoningGeneration") if isinstance(context.get("reasoningGeneration"), dict) else {}
    )
    raw_hypotheses = [item for item in hypothesis_set.get("hypotheses") or [] if isinstance(item, dict)]
    candidate_hypotheses: List[Dict[str, object]] = []
    for item in raw_hypotheses[:8]:
        hypothesis_id = str(item.get("hypothesisId") or item.get("hypothesis_id") or "").strip()
        if not hypothesis_id:
            continue
        stance = str(item.get("stance") or "context").strip().lower()
        counter_ids = []
        for peer in raw_hypotheses:
            peer_id = str(peer.get("hypothesisId") or peer.get("hypothesis_id") or "").strip()
            peer_stance = str(peer.get("stance") or "context").strip().lower()
            if peer_id and peer_id != hypothesis_id and peer_stance != stance:
                counter_ids.append(peer_id)
        candidate_hypotheses.append({
            "hypothesisId": hypothesis_id,
            "templateId": str(item.get("templateId") or item.get("template_id") or "").strip(),
            "templateLabel": str(item.get("templateLabel") or item.get("template_label") or "").strip(),
            "claim": str(item.get("claim") or "").strip(),
            "stance": stance,
            "evidenceState": str(item.get("evidenceState") or item.get("evidence_state") or "unresolved").strip(),
            "verificationStatus": str(item.get("verificationStatus") or item.get("verification_status") or "").strip(),
            "supportingEvidenceIds": unique_texts(item.get("supportingEvidenceIds") or item.get("supporting_evidence_ids") or [], 20),
            "counterEvidenceIds": unique_texts(item.get("counterEvidenceIds") or item.get("counter_evidence_ids") or [], 20),
            "requiredEvidenceTypes": unique_texts(item.get("requiredEvidenceTypes") or item.get("required_evidence_types") or [], 12),
            "invalidationConditions": unique_texts(item.get("invalidationConditions") or item.get("invalidation_conditions") or [], 12),
            "counterHypothesisIds": unique_texts(counter_ids, 8),
        })
    missing = list(context.get("missingData") or [])
    missing.extend(plan.get("unresolvedQuestions") or [])
    for item in candidate_hypotheses:
        if item.get("verificationStatus") in {"requires-research", "counterfactual-challenge"}:
            missing.extend(item.get("requiredEvidenceTypes") or [])
    audit = {
        "status": "rule-derived",
        "candidateCount": len(candidate_hypotheses),
        "counterHypothesisPairCount": sum(len(item.get("counterHypothesisIds") or []) for item in candidate_hypotheses),
        "preservesTypeDbHypotheses": True,
        "decisionEligibility": "research-only",
    }
    return HypothesisResearchBrief(
        hypothesis_set_id=str(hypothesis_set.get("hypothesisSetId") or hypothesis_set.get("hypothesis_set_id") or "").strip(),
        reasoning_generation=generation,
        candidate_hypotheses=candidate_hypotheses,
        evidence_gaps=unique_texts(missing, 40),
        baseline_plan_id=str(plan.get("planId") or plan.get("plan_id") or "").strip(),
        planning_audit=audit,
    )


@dataclass(frozen=True)
class EvidenceClaim:
    claim_id: str
    evidence_id: str
    symbol: str
    statement: str
    source: str
    source_url: str
    published_at: str
    observed_at: str
    verification_status: str
    entity_resolution_status: str
    source_trust_state: str
    data_state: str
    validation_state: str
    reasons: List[str] = field(default_factory=list)
    claim_state: str = "reported"
    source_origin: str = ""
    independent_source_count: int = 0
    official_evidence_ids: List[str] = field(default_factory=list)
    corroborating_evidence_ids: List[str] = field(default_factory=list)
    conflicting_evidence_ids: List[str] = field(default_factory=list)
    superseded_by_evidence_id: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {camel_key(key): value for key, value in payload.items()}


@dataclass(frozen=True)
class ResearchRun:
    run_id: str
    question_id: str
    account_id: str
    symbol: str
    status: str
    task_ids: List[str]
    source_types: List[str]
    reused_evidence_ids: List[str] = field(default_factory=list)
    verified_claims: List[EvidenceClaim] = field(default_factory=list)
    rejected_claims: List[EvidenceClaim] = field(default_factory=list)
    provider_statuses: List[Dict[str, object]] = field(default_factory=list)
    round_count: int = 0
    changed_evidence_count: int = 0
    reasoning_refreshed: bool = False
    reasoning_handoff: ResearchReasoningHandoff = field(default_factory=ResearchReasoningHandoff)
    hypothesis_research_brief: HypothesisResearchBrief = field(default_factory=HypothesisResearchBrief)
    claim_quality: Dict[str, object] = field(default_factory=dict)
    request_context: Dict[str, object] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now_iso)
    completed_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = {camel_key(key): value for key, value in asdict(self).items()}
        payload["verifiedClaims"] = [item.to_dict() for item in self.verified_claims]
        payload["rejectedClaims"] = [item.to_dict() for item in self.rejected_claims]
        payload["reasoningHandoff"] = self.reasoning_handoff.to_dict()
        payload["hypothesisResearchBrief"] = self.hypothesis_research_brief.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        payload = dict(payload or {})

        def claim(item: Dict[str, object]) -> EvidenceClaim:
            return EvidenceClaim(
                claim_id=str(item.get("claimId") or item.get("claim_id") or ""),
                evidence_id=str(item.get("evidenceId") or item.get("evidence_id") or ""),
                symbol=str(item.get("symbol") or ""),
                statement=str(item.get("statement") or ""),
                source=str(item.get("source") or ""),
                source_url=str(item.get("sourceUrl") or item.get("source_url") or ""),
                published_at=str(item.get("publishedAt") or item.get("published_at") or ""),
                observed_at=str(item.get("observedAt") or item.get("observed_at") or ""),
                verification_status=str(item.get("verificationStatus") or item.get("verification_status") or ""),
                entity_resolution_status=str(item.get("entityResolutionStatus") or item.get("entity_resolution_status") or ""),
                source_trust_state=normalized_source_trust_state(
                    item.get("sourceTrustState") or item.get("source_trust_state") or item.get("confidence")
                ),
                data_state=normalized_data_state(item.get("dataState") or item.get("data_state")),
                validation_state=normalized_validation_state(item.get("validationState") or item.get("validation_state")),
                reasons=list(item.get("reasons") or []),
                claim_state=str(item.get("claimState") or item.get("claim_state") or "reported"),
                source_origin=str(item.get("sourceOrigin") or item.get("source_origin") or ""),
                independent_source_count=int(item.get("independentSourceCount") or item.get("independent_source_count") or 0),
                official_evidence_ids=list(item.get("officialEvidenceIds") or item.get("official_evidence_ids") or []),
                corroborating_evidence_ids=list(item.get("corroboratingEvidenceIds") or item.get("corroborating_evidence_ids") or []),
                conflicting_evidence_ids=list(item.get("conflictingEvidenceIds") or item.get("conflicting_evidence_ids") or []),
                superseded_by_evidence_id=str(item.get("supersededByEvidenceId") or item.get("superseded_by_evidence_id") or ""),
            )

        return cls(
            run_id=str(payload.get("runId") or payload.get("run_id") or ""),
            question_id=str(payload.get("questionId") or payload.get("question_id") or ""),
            account_id=str(payload.get("accountId") or payload.get("account_id") or ""),
            symbol=str(payload.get("symbol") or "").upper(),
            status=str(payload.get("status") or "ready"),
            task_ids=list(payload.get("taskIds") or payload.get("task_ids") or []),
            source_types=list(payload.get("sourceTypes") or payload.get("source_types") or []),
            reused_evidence_ids=list(payload.get("reusedEvidenceIds") or payload.get("reused_evidence_ids") or []),
            verified_claims=[claim(item) for item in payload.get("verifiedClaims") or [] if isinstance(item, dict)],
            rejected_claims=[claim(item) for item in payload.get("rejectedClaims") or [] if isinstance(item, dict)],
            provider_statuses=list(payload.get("providerStatuses") or payload.get("provider_statuses") or []),
            round_count=int(payload.get("roundCount") or payload.get("round_count") or 0),
            changed_evidence_count=int(payload.get("changedEvidenceCount") or payload.get("changed_evidence_count") or 0),
            reasoning_refreshed=bool(payload.get("reasoningRefreshed") or payload.get("reasoning_refreshed")),
            reasoning_handoff=ResearchReasoningHandoff.from_dict(
                payload.get("reasoningHandoff") or payload.get("reasoning_handoff") or {}
            ),
            hypothesis_research_brief=HypothesisResearchBrief.from_dict(
                payload.get("hypothesisResearchBrief") or payload.get("hypothesis_research_brief") or {}
            ),
            claim_quality=dict(payload.get("claimQuality") or payload.get("claim_quality") or {}),
            request_context=dict(payload.get("requestContext") or payload.get("request_context") or {}),
            started_at=str(payload.get("startedAt") or payload.get("started_at") or utc_now_iso()),
            completed_at=str(payload.get("completedAt") or payload.get("completed_at") or ""),
        )


def camel_key(value: str) -> str:
    head, *tail = str(value or "").split("_")
    return head + "".join(item[:1].upper() + item[1:] for item in tail)


SOURCE_TRUST_ORDER = tuple(news_domain.NEWS_SOURCE_TRUST_STATE_ORDER)
DATA_STATES = {"sufficient", "partial", "insufficient", "unavailable"}
VALIDATION_STATES = {"ready", "conditional", "blocked"}


def normalized_source_trust_state(value: object, fallback: str = "standard") -> str:
    text = str(value or "").strip().lower()
    if text in SOURCE_TRUST_ORDER:
        return text
    # Legacy persisted rows may still carry a numeric reliability.  The value
    # is converted once at the boundary and is never kept in the claim.
    return news_domain.news_source_trust_state(value) if value not in (None, "") else fallback


def normalized_data_state(value: object, fallback: str = "partial") -> str:
    text = str(value or "").strip().lower()
    return text if text in DATA_STATES else fallback


def normalized_validation_state(value: object, fallback: str = "conditional") -> str:
    text = str(value or "").strip().lower()
    return text if text in VALIDATION_STATES else fallback


def source_trust_meets_policy(actual: object, required: object) -> bool:
    return SOURCE_TRUST_ORDER.index(normalized_source_trust_state(actual)) >= SOURCE_TRUST_ORDER.index(
        normalized_source_trust_state(required)
    )


def evidence_age_minutes(item: ResearchEvidence, now=None):
    raw_timestamp = str(item.published_at or item.observed_at or "").strip()
    parsed = parse_datetime(raw_timestamp)
    if not parsed and len(raw_timestamp) == 8 and raw_timestamp.isdigit():
        try:
            parsed = datetime.strptime(raw_timestamp, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            parsed = None
    if not parsed:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 60.0)


def primary_source(item: ResearchEvidence) -> bool:
    haystack = " ".join([str(item.source or ""), str(item.url or "")]).casefold()
    return any(marker.casefold() in haystack for marker in PRIMARY_SOURCE_MARKERS)


def entity_resolution(item: ResearchEvidence, target: NewsCollectionTarget) -> Tuple[str, List[str]]:
    payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    scope = str(payload.get("relationScope") or "").strip().lower()
    if str(item.symbol or "").upper().strip() != target.normalized_symbol():
        return "rejected", ["symbol-mismatch"]
    if scope == "noise":
        return "rejected", ["relation-scope-noise"]
    if (
        payload.get("directMention") is True
        or scope == "direct"
        or primary_source(item)
        or str(item.kind or "").lower() in {"market-move", "financial-fact"}
    ):
        return "resolved-direct", []
    text = " ".join([str(item.title or ""), str(item.summary or "")]).casefold()
    matched = [alias for alias in target_aliases(target) if str(alias or "").casefold() in text]
    if matched:
        return "resolved-alias", []
    return "unresolved", ["direct-subject-unconfirmed"]


def claim_bool(value: object, fallback: bool = False) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return fallback
    return text not in {"0", "false", "no", "off", "disabled"}


def claim_number(value: object, fallback: float) -> float:
    try:
        return float(str(value if value not in (None, "") else fallback))
    except (TypeError, ValueError):
        return fallback


def claim_policy(settings: Dict[str, object] = None) -> Dict[str, object]:
    """Normalize runtime settings without leaking provider-specific logic into callers."""
    raw = dict(settings or {})
    if "strictInvestmentEligibility" in raw:
        registry_value = raw.get("sourceRegistry")
        return {
            "strictInvestmentEligibility": claim_bool(raw.get("strictInvestmentEligibility"), False),
            "officialVerificationEnabled": claim_bool(raw.get("officialVerificationEnabled"), True),
            "minimumIndependentSources": max(2, min(5, int(claim_number(raw.get("minimumIndependentSources"), 2)))),
            "crossSourceWindowHours": max(1, min(24 * 30, int(claim_number(raw.get("crossSourceWindowHours"), 72)))),
            "similarityThreshold": max(0.2, min(0.95, claim_number(raw.get("similarityThreshold"), 0.72))),
            "sourceRegistry": registry_value if isinstance(registry_value, dict) else source_registry(registry_value),
        }
    return {
        "strictInvestmentEligibility": claim_bool(raw.get("researchClaimRequireVerifiedForInvestment"), False),
        "officialVerificationEnabled": claim_bool(raw.get("researchClaimOfficialVerificationEnabled"), True),
        "minimumIndependentSources": max(2, min(5, int(claim_number(raw.get("researchClaimMinimumIndependentSources"), 2)))),
        "crossSourceWindowHours": max(1, min(24 * 30, int(claim_number(raw.get("researchClaimCrossSourceWindowHours"), 72)))),
        "similarityThreshold": max(0.2, min(0.95, claim_number(raw.get("researchClaimSimilarityThreshold"), 0.72))),
        "sourceRegistry": source_registry(raw.get("researchClaimSourceRegistry")),
    }


def source_registry(value: object) -> Dict[str, Dict[str, object]]:
    """Parse a small local source-policy registry.

    The setting accepts either JSON (``{"reuters": {"tier": "trusted"}}``)
    or one line per source (``reuters=trusted,origin=reuters``).  The source
    name stays local configuration rather than a hard-coded judgement rule.
    """
    registry: Dict[str, Dict[str, object]] = {}
    raw = str(value or "").strip()
    if not raw:
        return registry
    decoded = None
    if raw.startswith("{"):
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            decoded = None
    if isinstance(decoded, dict):
        for key, profile in decoded.items():
            matcher = str(key or "").strip().casefold()
            if not matcher:
                continue
            values = dict(profile) if isinstance(profile, dict) else {"tier": profile}
            registry[matcher] = {
                "tier": str(values.get("tier") or values.get("sourceTrustState") or "").strip().lower(),
                "origin": str(values.get("origin") or "").strip(),
                "primary": claim_bool(values.get("primary"), False),
            }
        return registry
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        matcher, encoded = line.split("=", 1)
        matcher = matcher.strip().casefold()
        if not matcher:
            continue
        pieces = [part.strip() for part in encoded.split(",") if part.strip()]
        profile: Dict[str, object] = {"tier": pieces[0].lower() if pieces else ""}
        for part in pieces[1:]:
            if "=" in part:
                key, item = part.split("=", 1)
                profile[key.strip().lower()] = item.strip()
            elif part.casefold() == "primary":
                profile["primary"] = True
        registry[matcher] = {
            "tier": str(profile.get("tier") or "").strip().lower(),
            "origin": str(profile.get("origin") or "").strip(),
            "primary": claim_bool(profile.get("primary"), False),
        }
    return registry


def canonical_evidence_url(value: object) -> str:
    """Drop delivery-only URL variants while retaining the article identity."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
    except (TypeError, ValueError):
        return raw
    host = re.sub(r"^(?:www|m)\.", "", str(parsed.netloc or "").casefold().split(":")[0])
    query = []
    for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        normalized = str(key or "").casefold()
        if normalized in URL_TRACKING_QUERY_KEYS or normalized.startswith("utm_"):
            continue
        query.append((key, item))
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
    return urllib.parse.urlunsplit((parsed.scheme.casefold() or "https", host, path, urllib.parse.urlencode(sorted(query)), ""))


def publisher_origin_identity(value: object) -> str:
    key = re.sub(r"^(?:www|m)\.", "", re.sub(r"\s+", " ", str(value or "").casefold()).strip())
    if not key:
        return ""
    for matcher, identity in PUBLISHER_ORIGIN_ALIASES.items():
        if key == matcher or key.endswith("." + matcher) or matcher in key:
            return identity
    return re.sub(r"[^0-9a-z가-힣.]+", "-", key).strip("-")


def official_document_text(item: ResearchEvidence) -> str:
    payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    facts = payload.get("articleFacts") if isinstance(payload.get("articleFacts"), dict) else {}
    return str(
        payload.get("officialDocumentText")
        or facts.get("officialDocumentText")
        or (payload.get("articleText") if str(item.kind or "").lower() in OFFICIAL_EVIDENCE_KINDS else "")
        or ""
    ).strip()


def source_origin_for_evidence(item: ResearchEvidence, registry: Dict[str, Dict[str, object]] = None) -> Dict[str, object]:
    """Separate publisher identity, canonical article URL, and delivery channel."""
    payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    provider = str(payload.get("provider") or "").strip()
    if str(item.kind or "").lower() != "news":
        source = str(
            payload.get("articlePublisher")
            or payload.get("sourcePublisher")
            or payload.get("publisher")
            or item.source
            or ""
        ).strip()
        canonical_url = canonical_evidence_url(
            payload.get("articleCanonicalUrl")
            or payload.get("canonicalUrl")
            or item.url
            or payload.get("articleSourceUrl")
        )
        source_key = re.sub(r"\s+", " ", source.casefold())
        configured_registry = registry if isinstance(registry, dict) else {}
        matched = {}
        for matcher, profile in configured_registry.items():
            if matcher and (matcher in source_key or matcher in provider.casefold() or matcher in canonical_url.casefold()):
                matched = dict(profile or {})
                break
        try:
            host = urllib.parse.urlparse(canonical_url).netloc.casefold().split(":")[0]
        except (TypeError, ValueError):
            host = ""
        host = re.sub(r"^(?:www|m)\.", "", host)
        source_identity = publisher_origin_identity(source_key)
        host_identity = publisher_origin_identity(host)
        origin_seed = host_identity if source_key in GENERIC_DELIVERY_CHANNELS else (source_identity or host_identity)
        origin = str(matched.get("origin") or origin_seed or source_identity or host_identity or "unknown").strip().casefold()
        origin = re.sub(r"[^0-9a-z가-힣.]+", "-", origin).strip("-") or "unknown"
        tier = str(matched.get("tier") or payload.get("sourceTrustState") or item.source_trust_state or "unknown").strip().lower()
        if tier not in SOURCE_TRUST_ORDER:
            tier = news_domain.source_trust_state_for_source(source, provider)
        primary = bool(matched.get("primary")) or primary_source(item) or str(item.kind or "").lower() in OFFICIAL_EVIDENCE_KINDS
        return {
            "publisher": source or host or "unknown",
            "origin": origin,
            "publisherIdentity": source_identity or host_identity or origin,
            "tier": tier,
            "primary": primary,
            "host": host,
            "provider": provider,
            "canonicalUrl": canonical_url,
        }
    provenance = resolve_source_provenance(
        payload,
        title=item.title,
        summary=item.summary,
        source=item.source,
        provider=provider,
        url=item.url,
        published_at=item.published_at or item.observed_at,
        registry=registry or {},
    )
    identity = provenance.identity
    primary = identity.publisher_type == "official" or primary_source(item) or str(item.kind or "").lower() in OFFICIAL_EVIDENCE_KINDS
    return {
        "publisher": identity.publisher,
        "origin": identity.publisher_id or "unknown",
        "publisherIdentity": identity.publisher_id or "unknown",
        "tier": identity.source_trust_state,
        "primary": primary,
        "host": identity.canonical_host,
        "provider": provider,
        "canonicalUrl": provenance.canonical_url,
    }


def article_claim_sentences(item: ResearchEvidence) -> List[str]:
    """Return short source-backed sentences, never an AI-only paraphrase."""
    payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    facts = payload.get("articleFacts") if isinstance(payload.get("articleFacts"), dict) else {}
    values: List[object] = []
    body = official_document_text(item) or payload.get("articleText") or facts.get("bodyText") or facts.get("bodyPreview")
    if body:
        values.append(body)
    key_sentences = facts.get("keySentences") if isinstance(facts.get("keySentences"), list) else []
    values.extend(key_sentences)
    if not body and str(item.kind or "").lower() not in OFFICIAL_EVIDENCE_KINDS:
        values.extend([facts.get("feedSummaryPreview"), item.summary, item.title])
    rows: List[str] = []
    for value in values:
        for part in re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", str(value or "")):
            text = re.sub(r"\s+", " ", part).strip(" -•·\t")
            if len(text) < 24 or news_domain.is_news_boilerplate_sentence(text):
                continue
            if text not in rows:
                rows.append(text[:600])
            if len(rows) >= 5:
                return rows
    if rows:
        return rows
    if str(item.kind or "").lower() in OFFICIAL_EVIDENCE_KINDS:
        return []
    fallback = str(item.summary or item.title or "").strip()
    return [fallback[:600]] if fallback else []


def claim_tokens(value: object) -> List[str]:
    tokens = re.findall(r"[가-힣]{2,}|[a-z][a-z0-9'-]{2,}|\d[\d,.]*(?:%|bp|억|만|원|달러|million|billion)?", str(value or "").casefold())
    return [token for token in tokens if token not in CLAIM_STOP_WORDS]


def claim_numbers(value: object) -> List[str]:
    return re.findall(r"\d[\d,.]*(?:%|bp|억|만|원|달러|million|billion)?", str(value or "").casefold())[:8]


def claim_is_correction(item: ResearchEvidence, statement: object) -> bool:
    payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    text = " ".join([
        str(item.title or ""),
        str(statement or ""),
        str(payload.get("articleSummaryKo") or ""),
    ]).casefold()
    return any(marker.casefold() in text for marker in CORRECTION_MARKERS)


def claim_excerpt_location(item: ResearchEvidence, statement: object) -> Tuple[int, int]:
    payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    facts = payload.get("articleFacts") if isinstance(payload.get("articleFacts"), dict) else {}
    excerpt = str(statement or "").strip()
    for source in [
        payload.get("officialDocumentText"),
        payload.get("articleText"),
        facts.get("officialDocumentText"),
        facts.get("bodyPreview"),
        facts.get("feedSummaryPreview"),
        item.summary,
        item.title,
    ]:
        text = str(source or "")
        if not text or not excerpt:
            continue
        start = text.find(excerpt)
        if start >= 0:
            return start, start + len(excerpt)
    return -1, -1


def extracted_claims_for_evidence(
    item: ResearchEvidence,
    target: NewsCollectionTarget,
    policy: Dict[str, object] = None,
) -> List[Dict[str, object]]:
    """Build a bounded claim ledger with source excerpts and stable provenance."""
    policy = policy if isinstance(policy, dict) else claim_policy()
    payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    profile = source_origin_for_evidence(item, policy.get("sourceRegistry"))
    event_type = str(payload.get("eventType") or "general").strip().lower() or "general"
    source_text = official_document_text(item) or str(payload.get("articleText") or "")
    content_fingerprint = stable_id(
        "research-evidence-content",
        re.sub(r"\s+", " ", source_text.casefold())[:12000],
    ) if source_text else ""
    rows: List[Dict[str, object]] = []
    for index, statement in enumerate(article_claim_sentences(item)):
        tokens = claim_tokens(statement)
        if not tokens:
            continue
        fingerprint = stable_id(
            "research-claim-fingerprint",
            target.normalized_symbol(),
            event_type,
            "|".join(sorted(set(tokens))[:14]),
        )
        rows.append({
            "claimId": stable_id("research-claim", item.evidence_id, str(index), statement),
            "fingerprint": fingerprint,
            "statement": statement,
            "claimKind": "quantified-event" if claim_numbers(statement) else "event",
            "eventType": event_type,
            "symbol": target.normalized_symbol(),
            "numbers": claim_numbers(statement),
            "tokens": tokens[:24],
            "sourceEvidenceId": str(item.evidence_id or ""),
            "source": profile["publisher"],
            "sourceOrigin": profile["origin"],
            "publisherIdentity": profile["publisherIdentity"],
            "sourceUrl": str(item.url or ""),
            "canonicalUrl": profile["canonicalUrl"],
            "contentFingerprint": content_fingerprint,
            "evidenceRelationship": str(payload.get("evidenceRelationship") or "original"),
            "syndicationRootEvidenceId": str(payload.get("syndicationRootEvidenceId") or item.evidence_id or ""),
            "evidenceKind": str(item.kind or "").lower(),
            "officialDocumentAvailable": bool(
                str(item.kind or "").lower() in OFFICIAL_EVIDENCE_KINDS
                and len(official_document_text(item)) >= OFFICIAL_DOCUMENT_MIN_CHARS
            ),
            "publishedAt": str(item.published_at or ""),
            "observedAt": str(item.observed_at or ""),
            "excerpt": statement,
            "excerptIndex": index,
            "excerptStart": claim_excerpt_location(item, statement)[0],
            "excerptEnd": claim_excerpt_location(item, statement)[1],
            "subjectSymbol": target.normalized_symbol(),
            "action": event_type,
            "isPrimarySource": bool(profile["primary"]),
            "isCorrection": claim_is_correction(item, statement),
            "state": "reported",
            "verificationStatus": "",
            "investmentJudgmentEligible": False,
            "reasons": [],
        })
    if rows or str(item.kind or "").lower() in OFFICIAL_EVIDENCE_KINDS:
        return rows
    return [{
        "claimId": stable_id("research-claim", item.evidence_id, "fallback"),
        "fingerprint": stable_id("research-claim-fingerprint", target.normalized_symbol(), event_type, item.title),
        "statement": str(item.title or item.summary or "")[:600],
        "claimKind": "event",
        "eventType": event_type,
        "symbol": target.normalized_symbol(),
        "numbers": [],
        "tokens": claim_tokens(item.title or item.summary),
        "sourceEvidenceId": str(item.evidence_id or ""),
        "source": profile["publisher"],
        "sourceOrigin": profile["origin"],
        "publisherIdentity": profile["publisherIdentity"],
        "sourceUrl": str(item.url or ""),
        "canonicalUrl": profile["canonicalUrl"],
        "contentFingerprint": content_fingerprint,
        "evidenceRelationship": str(payload.get("evidenceRelationship") or "original"),
        "syndicationRootEvidenceId": str(payload.get("syndicationRootEvidenceId") or item.evidence_id or ""),
        "evidenceKind": str(item.kind or "").lower(),
        "officialDocumentAvailable": False,
        "publishedAt": str(item.published_at or ""),
        "observedAt": str(item.observed_at or ""),
        "excerpt": str(item.title or item.summary or "")[:600],
        "excerptIndex": 0,
        "excerptStart": 0,
        "excerptEnd": len(str(item.title or item.summary or "")[:600]),
        "subjectSymbol": target.normalized_symbol(),
        "action": event_type,
        "isPrimarySource": bool(profile["primary"]),
        "isCorrection": claim_is_correction(item, item.title),
        "state": "reported",
        "verificationStatus": "",
        "investmentJudgmentEligible": False,
        "reasons": ["claim-fallback-title"],
    }]


def claim_similarity(left: Dict[str, object], right: Dict[str, object]) -> float:
    if str(left.get("symbol") or "") != str(right.get("symbol") or ""):
        return 0.0
    left_event = str(left.get("eventType") or "general")
    right_event = str(right.get("eventType") or "general")
    if left_event != right_event and "general" not in {left_event, right_event}:
        return 0.0
    left_tokens = set(left.get("tokens") or claim_tokens(left.get("statement")))
    right_tokens = set(right.get("tokens") or claim_tokens(right.get("statement")))
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens.intersection(right_tokens)) / max(1, len(left_tokens.union(right_tokens)))
    score = overlap + (0.18 if left_event == right_event and left_event != "general" else 0.0)
    left_numbers = set(left.get("numbers") or [])
    right_numbers = set(right.get("numbers") or [])
    if left_numbers and right_numbers:
        score += 0.18 if left_numbers.intersection(right_numbers) else -0.12
    return max(0.0, min(1.0, score))


def claim_timestamp(claim: Dict[str, object]):
    return parse_datetime(str(claim.get("publishedAt") or claim.get("observedAt") or ""))


def claims_within_window(left: Dict[str, object], right: Dict[str, object], window_hours: int) -> bool:
    left_time = claim_timestamp(left)
    right_time = claim_timestamp(right)
    if not left_time or not right_time:
        return True
    return abs((left_time - right_time).total_seconds()) <= max(1, int(window_hours or 1)) * 3600


def directional_polarity(item: ResearchEvidence) -> str:
    value = str(item.polarity or "context").strip().lower()
    if value in {"support", "positive", "bullish"}:
        return "support"
    if value in {"risk", "negative", "bearish"}:
        return "risk"
    return "context"


def claim_state_rank(value: object) -> int:
    order = {
        "rejected": 0,
        "expired": 1,
        "reported": 2,
        "verified-primary": 3,
        "corroborated": 4,
        "superseded": 0,
        "conflicted": 0,
    }
    return order.get(str(value or ""), 0)


def verification_for_evidence(
    item: ResearchEvidence,
    target: NewsCollectionTarget,
    max_age_minutes: int,
    minimum_source_trust_state: str = "standard",
    now: datetime = None,
    **legacy_policy: object,
) -> Tuple[EvidenceClaim, bool]:
    if "minimum_source_reliability" in legacy_policy:
        minimum_source_trust_state = normalized_source_trust_state(legacy_policy["minimum_source_reliability"])
    payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    resolution, reasons = entity_resolution(item, target)
    age = evidence_age_minutes(item, now=now)
    if age is None:
        reasons.append("reference-time-missing")
    elif age > max(1, int(max_age_minutes or 1)):
        reasons.append("evidence-stale")
    states = item.state_payload()
    source_trust_state = normalized_source_trust_state(states.get("sourceTrustState"))
    data_state = normalized_data_state(states.get("dataState"))
    validation_state = normalized_validation_state(states.get("validationState"))
    quality_gate = payload.get("qualityGate") if isinstance(payload.get("qualityGate"), dict) else {}
    if quality_gate and quality_gate.get("passed") is False:
        reasons.append("source-quality-gate-failed")
    if not source_trust_meets_policy(source_trust_state, minimum_source_trust_state):
        reasons.append("source-trust-below-policy")
    if data_state in {"insufficient", "unavailable"}:
        reasons.append("evidence-data-insufficient")
    if validation_state == "blocked":
        reasons.append("evidence-validation-blocked")
    if (
        str(item.kind or "").lower() in OFFICIAL_EVIDENCE_KINDS
        and len(official_document_text(item)) < OFFICIAL_DOCUMENT_MIN_CHARS
    ):
        reasons.append("official-document-content-missing")
    if not str(item.source or "").strip():
        reasons.append("source-missing")
    if not str(item.title or "").strip():
        reasons.append("claim-text-missing")
    accepted = resolution.startswith("resolved") and not reasons
    if accepted and (primary_source(item) or str(item.kind or "").lower() in OFFICIAL_EVIDENCE_KINDS):
        status = "verified-primary"
    elif accepted:
        status = "verified-secondary"
    else:
        status = "rejected"
    statement = str(item.summary or item.title or "").strip()[:1200]
    claim = EvidenceClaim(
        claim_id=stable_id("evidence-claim", item.evidence_id, statement),
        evidence_id=str(item.evidence_id or ""),
        symbol=target.normalized_symbol(),
        statement=statement,
        source=str(item.source or ""),
        source_url=str(item.url or ""),
        published_at=str(item.published_at or ""),
        observed_at=str(item.observed_at or ""),
        verification_status=status,
        entity_resolution_status=resolution,
        source_trust_state=source_trust_state,
        data_state=data_state,
        validation_state=validation_state,
        reasons=reasons,
    )
    return claim, accepted


def claim_matches_official(
    claim: Dict[str, object],
    peer: Dict[str, object],
    similarity: float,
    threshold: float,
) -> bool:
    # Two official filing headers are not independent verification.  A primary
    # document may only verify a non-primary report when its actual body was
    # collected, rather than a filing date/title metadata row.
    if claim.get("isPrimarySource") or not peer.get("isPrimarySource"):
        return False
    if not peer.get("officialDocumentAvailable"):
        return False
    if similarity >= threshold:
        return True
    same_event = str(claim.get("eventType") or "") == str(peer.get("eventType") or "")
    shared_numbers = set(claim.get("numbers") or []).intersection(peer.get("numbers") or [])
    shared_terms = set(claim.get("tokens") or []).intersection(peer.get("tokens") or [])
    return bool(same_event and (shared_numbers or len(shared_terms) >= 2))


def claims_are_republication(left: Dict[str, object], right: Dict[str, object]) -> bool:
    """Identify the same article transported through a different channel."""
    left_root = str(left.get("syndicationRootEvidenceId") or "").strip()
    right_root = str(right.get("syndicationRootEvidenceId") or "").strip()
    if left_root and left_root == right_root:
        return True
    left_url = str(left.get("canonicalUrl") or "").strip()
    right_url = str(right.get("canonicalUrl") or "").strip()
    if left_url and left_url == right_url:
        return True
    left_identity = str(left.get("publisherIdentity") or left.get("sourceOrigin") or "").strip()
    right_identity = str(right.get("publisherIdentity") or right.get("sourceOrigin") or "").strip()
    if left_identity and left_identity == right_identity:
        return True
    return False


def claim_ledger_summary(claims: Iterable[Dict[str, object]]) -> Dict[str, object]:
    rows = [dict(item) for item in claims or [] if isinstance(item, dict)]
    states = [str(item.get("state") or "reported") for item in rows]
    eligible = [item for item in rows if item.get("investmentJudgmentEligible")]
    syndicated = [item for item in rows if "syndicated-duplicate" in list(item.get("reasons") or [])]
    # A duplicate may coexist with a genuinely independent third source.  It
    # becomes an operational failure only if it was still able to establish
    # corroboration without the required independent-origin count.
    unsafe_syndicated = [
        item for item in syndicated
        if item.get("investmentJudgmentEligible")
        and str(item.get("state") or "") == "corroborated"
        and int(item.get("independentSourceCount") or 0) < 2
    ]
    return {
        "version": CLAIM_LEDGER_VERSION,
        "claimCount": len(rows),
        "eligibleClaimCount": len(eligible),
        "stateCounts": {state: states.count(state) for state in CLAIM_STATES if states.count(state)},
        "officialVerifiedCount": len([item for item in rows if item.get("officialEvidenceIds")]),
        "corroboratedCount": len([item for item in rows if item.get("state") == "corroborated"]),
        "conflictedCount": len([item for item in rows if item.get("state") == "conflicted"]),
        "supersededCount": len([item for item in rows if item.get("state") == "superseded"]),
        "syndicatedDuplicateCount": len(syndicated),
        "eligibleSyndicatedCount": len(unsafe_syndicated),
        "independentOrigins": unique_texts([item.get("sourceOrigin") for item in rows], 20),
    }


def claim_quality_summary(items: Iterable[ResearchEvidence]) -> Dict[str, object]:
    """Expose bounded, explainable quality metrics for the web read model."""
    claims: List[Dict[str, object]] = []
    ungoverned_count = 0
    official_document_count = 0
    official_metadata_only_count = 0
    for item in items or []:
        payload = item.raw_payload if isinstance(getattr(item, "raw_payload", None), dict) else {}
        ledger = payload.get("claimLedger") if isinstance(payload.get("claimLedger"), dict) else {}
        governance = payload.get("evidenceGovernance") if isinstance(payload.get("evidenceGovernance"), dict) else {}
        if not ledger or not governance:
            ungoverned_count += 1
        if str(getattr(item, "kind", "") or "").lower() in OFFICIAL_EVIDENCE_KINDS:
            if len(official_document_text(item)) >= OFFICIAL_DOCUMENT_MIN_CHARS:
                official_document_count += 1
            else:
                official_metadata_only_count += 1
        claims.extend(dict(row) for row in ledger.get("claims") or [] if isinstance(row, dict))
    summary = claim_ledger_summary(claims)
    summary.update({
        "ungovernedEvidenceCount": ungoverned_count,
        "officialDocumentContentCount": official_document_count,
        "officialMetadataOnlyCount": official_metadata_only_count,
    })
    summary["alertState"] = (
        "degraded" if summary["eligibleSyndicatedCount"] else
        "attention" if ungoverned_count or official_metadata_only_count else
        "healthy"
    )
    return summary


def governed_evidence(
    items: Iterable[ResearchEvidence],
    target: NewsCollectionTarget,
    max_age_minutes: int,
    minimum_source_trust_state: str = "standard",
    policy: Dict[str, object] = None,
    related_items: Iterable[ResearchEvidence] = None,
    now: datetime = None,
    **legacy_policy: object,
) -> Tuple[List[ResearchEvidence], List[EvidenceClaim], List[EvidenceClaim]]:
    """Apply source, claim, official-document and lifecycle governance.

    ``verificationStatus`` remains backward compatible at the evidence level.
    The claim ledger adds the stricter lifecycle state and is the only input
    used by the new investment-eligibility gate when that policy is enabled.
    """
    if "minimum_source_reliability" in legacy_policy:
        minimum_source_trust_state = normalized_source_trust_state(legacy_policy["minimum_source_reliability"])
    normalized_policy = claim_policy(policy)
    deduped: List[ResearchEvidence] = []
    seen_ids = set()
    for item in list(items or []) + list(related_items or []):
        if not isinstance(item, ResearchEvidence):
            continue
        identity = str(item.evidence_id or "").strip() or str(id(item))
        if identity in seen_ids:
            continue
        seen_ids.add(identity)
        deduped.append(item)

    baseline_by_evidence: Dict[str, Tuple[EvidenceClaim, bool]] = {}
    records: List[Dict[str, object]] = []
    for item in deduped:
        payload = dict(item.raw_payload or {})
        profile = source_origin_for_evidence(item, normalized_policy.get("sourceRegistry"))
        payload.update({
            "sourcePublisher": profile["publisher"],
            "sourceOrigin": profile["origin"],
            "sourceTrustState": profile["tier"],
            "articleCanonicalUrl": profile["canonicalUrl"],
            "publisherIdentity": profile["publisherIdentity"],
        })
        item.raw_payload = payload
        # ResearchEvidence retains categorical states for compatibility. Keep
        # that projection aligned with the configurable publisher registry.
        item.source_trust_state = profile["tier"]
        baseline, base_accepted = verification_for_evidence(
            item,
            target,
            max_age_minutes,
            minimum_source_trust_state,
            now=now,
        )
        baseline_by_evidence[str(item.evidence_id or "")] = (baseline, base_accepted)
        for claim in extracted_claims_for_evidence(item, target, normalized_policy):
            records.append({"item": item, "claim": claim, "baseline": baseline, "baseAccepted": base_accepted})

    threshold = float(normalized_policy["similarityThreshold"])
    window_hours = int(normalized_policy["crossSourceWindowHours"])
    minimum_sources = int(normalized_policy["minimumIndependentSources"])
    for record in records:
        item = record["item"]
        claim = record["claim"]
        baseline = record["baseline"]
        base_accepted = bool(record["baseAccepted"])
        peers: List[Tuple[Dict[str, object], Dict[str, object], float]] = []
        for other in records:
            other_claim = other["claim"]
            if other_claim.get("claimId") == claim.get("claimId"):
                continue
            if not claims_within_window(claim, other_claim, window_hours):
                continue
            similarity = claim_similarity(claim, other_claim)
            if similarity >= threshold:
                peers.append((other, other_claim, similarity))
        syndicated_matches = [
            other_claim for _other, other_claim, _similarity in peers
            if claims_are_republication(claim, other_claim)
        ]
        independent_peers = [
            (other, other_claim, similarity)
            for other, other_claim, similarity in peers
            if not claims_are_republication(claim, other_claim)
        ]
        official_ids = unique_texts([
            str(other_claim.get("sourceEvidenceId") or "")
            for other, other_claim, similarity in peers
            if normalized_policy["officialVerificationEnabled"] and claim_matches_official(claim, other_claim, similarity, threshold)
        ])
        independent_origins = unique_texts([
            str(claim.get("sourceOrigin") or "")
        ] + [str(other_claim.get("sourceOrigin") or "") for _other, other_claim, _similarity in independent_peers])
        corroborating_ids = unique_texts([
            str(other_claim.get("sourceEvidenceId") or "")
            for _other, other_claim, _similarity in independent_peers
            if not other_claim.get("isPrimarySource")
            and str(other_claim.get("sourceOrigin") or "") != str(claim.get("sourceOrigin") or "")
        ])
        same_origin_matches = [
            other_claim for _other, other_claim, _similarity in independent_peers
            if str(other_claim.get("sourceOrigin") or "") == str(claim.get("sourceOrigin") or "")
        ]
        polarity = directional_polarity(item)
        conflict_ids = unique_texts([
            str(other_claim.get("sourceEvidenceId") or "")
            for other, other_claim, _similarity in independent_peers
            if directional_polarity(other["item"]) in {"support", "risk"}
            and polarity in {"support", "risk"}
            and directional_polarity(other["item"]) != polarity
        ])
        reasons = list(baseline.reasons)
        state = "reported"
        if not base_accepted:
            state = "expired" if "evidence-stale" in reasons else "rejected"
        elif claim.get("isPrimarySource") or official_ids:
            state = "verified-primary"
            if official_ids and not claim.get("isPrimarySource"):
                reasons.append("official-document-match")
        if base_accepted and len(independent_origins) >= minimum_sources:
            state = "corroborated"
            reasons.append("independent-source-corroborated:" + str(len(independent_origins)))
        if base_accepted and official_ids:
            state = "verified-primary"
        if conflict_ids:
            state = "conflicted"
            reasons.append("independent-source-conflict")
        claim.update({
            "state": state,
            "verificationStatus": baseline.verification_status,
            "entityResolutionStatus": baseline.entity_resolution_status,
            "sourceTrustState": baseline.source_trust_state,
            "dataState": baseline.data_state,
            "validationState": baseline.validation_state,
            "officialEvidenceIds": official_ids,
            "corroboratingEvidenceIds": corroborating_ids,
            "conflictingEvidenceIds": conflict_ids,
            "independentSourceCount": len(independent_origins),
            "duplicateOfClaimId": str((syndicated_matches or same_origin_matches)[0].get("claimId") or "") if (syndicated_matches or same_origin_matches) else "",
            "reasons": unique_texts(reasons, 20),
            "investmentJudgmentEligible": bool(
                base_accepted
                and state not in {"conflicted", "superseded", "expired", "rejected"}
                and (not normalized_policy["strictInvestmentEligibility"] or state in {"verified-primary", "corroborated"})
            ),
        })
        if syndicated_matches or same_origin_matches:
            claim["reasons"] = unique_texts(list(claim.get("reasons") or []) + ["syndicated-duplicate"], 20)

    # A correction is source evidence about the prior report, not a second
    # independent confirmation.  It withdraws only a similar, older claim.
    for record in records:
        item = record["item"]
        correction = record["claim"]
        if not correction.get("isCorrection"):
            continue
        correction_time = claim_timestamp(correction)
        for previous_record in records:
            previous = previous_record["claim"]
            if previous.get("claimId") == correction.get("claimId") or previous.get("isCorrection"):
                continue
            if str(previous.get("sourceOrigin") or "") != str(correction.get("sourceOrigin") or ""):
                continue
            previous_time = claim_timestamp(previous)
            if correction_time and previous_time and previous_time > correction_time:
                continue
            if claim_similarity(previous, correction) < max(0.2, threshold * 0.55):
                continue
            previous.update({
                "state": "superseded",
                "investmentJudgmentEligible": False,
                "supersededByEvidenceId": str(item.evidence_id or ""),
                "supersededByClaimId": str(correction.get("claimId") or ""),
                "reasons": unique_texts(list(previous.get("reasons") or []) + ["superseded-by-correction"], 20),
            })
            correction["supersedesClaimIds"] = unique_texts(
                list(correction.get("supersedesClaimIds") or []) + [str(previous.get("claimId") or "")],
                20,
            )

    claims_by_evidence: Dict[str, List[Dict[str, object]]] = {}
    for record in records:
        item = record["item"]
        claims_by_evidence.setdefault(str(item.evidence_id or ""), []).append(record["claim"])

    accepted_items: List[ResearchEvidence] = []
    verified: List[EvidenceClaim] = []
    rejected: List[EvidenceClaim] = []
    item_ids = {str(item.evidence_id or "") for item in items or [] if isinstance(item, ResearchEvidence)}
    for item in deduped:
        evidence_id = str(item.evidence_id or "")
        baseline, base_accepted = baseline_by_evidence[evidence_id]
        claims = claims_by_evidence.get(evidence_id, [])
        best_claim = max(claims, key=lambda row: (bool(row.get("investmentJudgmentEligible")), claim_state_rank(row.get("state"))), default={})
        eligible = any(bool(row.get("investmentJudgmentEligible")) for row in claims)
        payload = dict(item.raw_payload or {})
        missing_claim_source = not claims
        missing_claim_reasons = list(baseline.reasons)
        if missing_claim_source:
            missing_claim_reasons.append(
                "official-document-content-missing"
                if str(item.kind or "").lower() in OFFICIAL_EVIDENCE_KINDS
                else "claim-source-text-missing"
            )
        payload["claimLedger"] = {
            "version": CLAIM_LEDGER_VERSION,
            "claims": claims,
            "summary": claim_ledger_summary(claims),
        }
        payload["evidenceGovernance"] = {
            "claimId": str(best_claim.get("claimId") or baseline.claim_id),
            "verificationStatus": str(best_claim.get("verificationStatus") or baseline.verification_status),
            "claimState": str(best_claim.get("state") or ("rejected" if missing_claim_source or not base_accepted else "verified-secondary")),
            "entityResolutionStatus": str(best_claim.get("entityResolutionStatus") or baseline.entity_resolution_status),
            "checkedAt": utc_now_iso(),
            "reasons": list(best_claim.get("reasons") or missing_claim_reasons),
            "investmentJudgmentEligible": bool(eligible and not missing_claim_source),
            "sourcePolicy": "official-first-claim-ledger-v1",
            "sourceTrustState": baseline.source_trust_state,
            "dataState": baseline.data_state,
            "validationState": baseline.validation_state,
            "sourcePublisher": payload.get("sourcePublisher"),
            "sourceOrigin": payload.get("sourceOrigin"),
            "canonicalUrl": payload.get("articleCanonicalUrl"),
            "publisherIdentity": payload.get("publisherIdentity"),
            "independentSourceCount": int(best_claim.get("independentSourceCount") or 0),
            "officialEvidenceIds": list(best_claim.get("officialEvidenceIds") or []),
            "corroboratingEvidenceIds": list(best_claim.get("corroboratingEvidenceIds") or []),
            "conflictingEvidenceIds": list(best_claim.get("conflictingEvidenceIds") or []),
            "supersededByEvidenceId": str(best_claim.get("supersededByEvidenceId") or ""),
        }
        item.raw_payload = payload
        aggregate = EvidenceClaim(
            claim_id=str(best_claim.get("claimId") or baseline.claim_id),
            evidence_id=evidence_id,
            symbol=target.normalized_symbol(),
            statement=str(best_claim.get("statement") or baseline.statement),
            source=str(best_claim.get("source") or baseline.source),
            source_url=str(best_claim.get("sourceUrl") or baseline.source_url),
            published_at=str(best_claim.get("publishedAt") or baseline.published_at),
            observed_at=str(best_claim.get("observedAt") or baseline.observed_at),
            verification_status=str(best_claim.get("verificationStatus") or baseline.verification_status),
            entity_resolution_status=str(best_claim.get("entityResolutionStatus") or baseline.entity_resolution_status),
            source_trust_state=baseline.source_trust_state,
            data_state=baseline.data_state,
            validation_state=baseline.validation_state,
            reasons=list(best_claim.get("reasons") or missing_claim_reasons),
            claim_state=str(best_claim.get("state") or ("rejected" if missing_claim_source else "reported")),
            source_origin=str(best_claim.get("sourceOrigin") or payload.get("sourceOrigin") or ""),
            independent_source_count=int(best_claim.get("independentSourceCount") or 0),
            official_evidence_ids=list(best_claim.get("officialEvidenceIds") or []),
            corroborating_evidence_ids=list(best_claim.get("corroboratingEvidenceIds") or []),
            conflicting_evidence_ids=list(best_claim.get("conflictingEvidenceIds") or []),
            superseded_by_evidence_id=str(best_claim.get("supersededByEvidenceId") or ""),
        )
        # Related cached rows are mutated for correction/corroboration but are
        # returned only when the caller explicitly included them in ``items``.
        if evidence_id not in item_ids:
            continue
        if eligible:
            accepted_items.append(item)
            verified.append(aggregate)
        else:
            rejected.append(aggregate)
    return accepted_items, verified, rejected
