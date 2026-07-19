from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Tuple

from .data_freshness import parse_datetime
from .investment_brain import stable_id, utc_now_iso
from .investment_research import NewsCollectionTarget, ResearchEvidence, target_aliases


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
    confidence: float
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
    started_at: str = field(default_factory=utc_now_iso)
    completed_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = {camel_key(key): value for key, value in asdict(self).items()}
        payload["verifiedClaims"] = [item.to_dict() for item in self.verified_claims]
        payload["rejectedClaims"] = [item.to_dict() for item in self.rejected_claims]
        return payload


def camel_key(value: str) -> str:
    head, *tail = str(value or "").split("_")
    return head + "".join(item[:1].upper() + item[1:] for item in tail)


def normalized_score(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if 0 < parsed <= 1:
        parsed *= 100
    return max(0.0, min(100.0, parsed))


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
    minimum_source_reliability: float,
) -> Tuple[EvidenceClaim, bool]:
    payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    resolution, reasons = entity_resolution(item, target)
    age = evidence_age_minutes(item)
    if age is None:
        reasons.append("reference-time-missing")
    elif age > max(1, int(max_age_minutes or 1)):
        reasons.append("evidence-stale")
    reliability = normalized_score(payload.get("sourceReliability") or item.confidence)
    quality_gate = payload.get("qualityGate") if isinstance(payload.get("qualityGate"), dict) else {}
    if quality_gate and quality_gate.get("passed") is False:
        reasons.append("source-quality-gate-failed")
    if reliability < normalized_score(minimum_source_reliability):
        reasons.append("source-reliability-below-policy")
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
        confidence=round(reliability, 1),
        reasons=reasons,
    )
    return claim, accepted


def governed_evidence(
    items: Iterable[ResearchEvidence],
    target: NewsCollectionTarget,
    max_age_minutes: int,
    minimum_source_reliability: float,
) -> Tuple[List[ResearchEvidence], List[EvidenceClaim], List[EvidenceClaim]]:
    accepted_items: List[ResearchEvidence] = []
    verified: List[EvidenceClaim] = []
    rejected: List[EvidenceClaim] = []
    for item in items or []:
        claim, accepted = verification_for_evidence(
            item,
            target,
            max_age_minutes,
            minimum_source_reliability,
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
        }
        item.raw_payload = payload
        if accepted:
            accepted_items.append(item)
            verified.append(claim)
        else:
            rejected.append(claim)
    return accepted_items, verified, rejected
