from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Tuple

from .data_freshness import parse_datetime
from .investment_brain import stable_id, utc_now_iso
from .investment_research import NewsCollectionTarget, ResearchEvidence, target_aliases
from . import news_analysis as news_domain


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


RESEARCH_REASONING_HANDOFF_VERSION = "research-reasoning-generation-v1"
RESEARCH_REASONING_HANDOFF_STATES = {
    "not-requested",
    "pending",
    "applied",
    "blocked",
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
    request_context: Dict[str, object] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now_iso)
    completed_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = {camel_key(key): value for key, value in asdict(self).items()}
        payload["verifiedClaims"] = [item.to_dict() for item in self.verified_claims]
        payload["rejectedClaims"] = [item.to_dict() for item in self.rejected_claims]
        payload["reasoningHandoff"] = self.reasoning_handoff.to_dict()
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


def verification_for_evidence(
    item: ResearchEvidence,
    target: NewsCollectionTarget,
    max_age_minutes: int,
    minimum_source_trust_state: str = "standard",
    **legacy_policy: object,
) -> Tuple[EvidenceClaim, bool]:
    if "minimum_source_reliability" in legacy_policy:
        minimum_source_trust_state = normalized_source_trust_state(legacy_policy["minimum_source_reliability"])
    payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    resolution, reasons = entity_resolution(item, target)
    age = evidence_age_minutes(item)
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
    if not str(item.source or "").strip():
        reasons.append("source-missing")
    if not str(item.title or "").strip():
        reasons.append("claim-text-missing")
    accepted = resolution.startswith("resolved") and not reasons
    if accepted and primary_source(item):
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


def governed_evidence(
    items: Iterable[ResearchEvidence],
    target: NewsCollectionTarget,
    max_age_minutes: int,
    minimum_source_trust_state: str = "standard",
    **legacy_policy: object,
) -> Tuple[List[ResearchEvidence], List[EvidenceClaim], List[EvidenceClaim]]:
    if "minimum_source_reliability" in legacy_policy:
        minimum_source_trust_state = normalized_source_trust_state(legacy_policy["minimum_source_reliability"])
    accepted_items: List[ResearchEvidence] = []
    verified: List[EvidenceClaim] = []
    rejected: List[EvidenceClaim] = []
    for item in items or []:
        claim, accepted = verification_for_evidence(
            item,
            target,
            max_age_minutes,
            minimum_source_trust_state,
        )
        payload = dict(item.raw_payload or {})
        payload["evidenceGovernance"] = {
            "claimId": claim.claim_id,
            "verificationStatus": claim.verification_status,
            "entityResolutionStatus": claim.entity_resolution_status,
            "checkedAt": utc_now_iso(),
            "reasons": list(claim.reasons),
            "investmentJudgmentEligible": bool(accepted),
            "sourcePolicy": "official-first-cache-first-v1",
            "sourceTrustState": claim.source_trust_state,
            "dataState": claim.data_state,
            "validationState": claim.validation_state,
        }
        item.raw_payload = payload
        if accepted:
            accepted_items.append(item)
            verified.append(claim)
        else:
            rejected.append(claim)
    return accepted_items, verified, rejected
