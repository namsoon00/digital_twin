"""Question-scoped research coverage, never an investment action or truth score.

Source eligibility and temporal checks precede an AI's relevance review.  The
review is bound to the exact task and evidence packet, and remains an analyst
assessment rather than empirical validation of a market hypothesis.
"""

import hashlib
import json
from datetime import timedelta
from typing import Dict, Iterable, List

from digital_twin.modules.market_data.contracts import parse_datetime
from .investment_research import ResearchEvidence


RESEARCH_PROGRESS_VERSION = "question-evidence-coverage-v1"
AUDIT_RETENTION_DAYS = {"intraday": 30, "short-term": 90, "medium-term": 365, "long-term": 730, "multi-horizon": 730}
SEMANTIC_REQUIREMENTS = {
    "company-guidance", "competitive-event", "customer-event", "supplier-event",
    "regulatory-event", "industry-demand",
}
METADATA_REQUIREMENTS = {"provenance", "observation-time", "source-reliability"}
KIND_TYPES = {
    "disclosure": {"official-filing"}, "filing": {"official-filing"},
    "sec-filing": {"official-filing"}, "sec_filing": {"official-filing"},
    "financial-fact": {"financial-fact"},
    "market-move": {"market-data", "price-reaction"},
    "investor-flow": {"investor-flow"}, "macro-observation": {"macro-observation"},
}


def texts(values: object, limit: int = 40) -> List[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))[:limit]


def fingerprint(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def audit_retain_until(horizon: str, observed_at: str) -> str:
    stamp = parse_datetime(observed_at)
    if stamp is None:
        raise ValueError("Research audit retention requires a valid observation clock")
    return (stamp + timedelta(days=AUDIT_RETENTION_DAYS.get(horizon, 730))).isoformat().replace("+00:00", "Z")


def task_contract(task: Dict[str, object]) -> Dict[str, object]:
    return {key: task.get(key) for key in (
        "taskId", "question", "purpose", "relatedHypothesisIds", "requiredEvidenceTypes",
        "sourceTypes", "maxAgeMinutes", "requiredPeriodEnds", "requiredMetrics", "queryTerms",
    )}


def evidence_packets(items: Iterable[ResearchEvidence], claims: Iterable[object]) -> List[Dict[str, object]]:
    """Persist the bounded input actually reviewed, including source clocks.

    inputFingerprint identifies our captured packet, not a provider revision.
    Missing provider revisions stay missing. No raw vendor/account payload is
    copied into the planner or audit record.
    """
    verified = {claim.evidence_id: claim for claim in claims}
    result = []
    for item in items:
        claim = verified.get(item.evidence_id)
        if not claim:
            continue
        raw = item.raw_payload or {}
        types = set(KIND_TYPES.get(item.kind.lower(), set()))
        if item.kind.lower() == "news" and str(raw.get("articleReadStatus") or raw.get("readScope") or "") in {"body", "full-body", "full", "article-body"}:
            types.add("news-full-text")
        source_types = set()
        if "official-filing" in types:
            source_types.update({"official", "official-filing"})
        if item.kind.lower() == "issuer-ir":
            source_types.update({"official", "company-ir"})
        if "financial-fact" in types:
            source_types.add("financial-data")
        if types.intersection({"market-data", "price-reaction", "investor-flow"}):
            source_types.add("market-data")
        if "macro-observation" in types:
            source_types.add("official-macro")
        if item.kind.lower() == "news":
            source_types.update({"news", "article"})
            if "news-full-text" in types:
                source_types.add("news-full-text")
        packet = {
            "evidenceId": item.evidence_id, "symbol": item.symbol,
            "kind": item.kind, "source": item.source, "sourceUrl": item.url,
            "sourceOrigin": claim.source_origin,
            "publishedAt": item.published_at, "observedAt": item.observed_at,
            "sourceAsOf": str(raw.get("sourceAsOf") or ""),
            "sourceRevision": str(raw.get("sourceRevision") or raw.get("articleSourceRevision") or ""),
            "periodEnd": str(raw.get("periodEnd") or raw.get("reportPeriodEnd") or ""),
            "frequency": str(raw.get("frequency") or ""),
            "durationBases": list(raw.get("durationBases") or []),
            "reportedValues": dict(raw.get("reportedValues") or {}),
            "metricUnits": dict(raw.get("metricUnits") or {}),
            "metricProvenance": dict(raw.get("metricProvenance") or {}),
            "freshnessBasis": str(raw.get("freshnessBasis") or "publication"),
            "freshnessObservedAt": str(raw.get("freshnessObservedAt") or ""),
            "title": item.title[:320], "statement": claim.statement[:1200],
            "evidenceTypes": sorted(types), "sourceTypes": sorted(source_types),
            "verificationStatus": claim.verification_status,
            "sourceTrustState": claim.source_trust_state,
            "independentSourceCount": claim.independent_source_count,
            "corroboratingEvidenceIds": list(claim.corroborating_evidence_ids),
            "officialEvidenceIds": list(claim.official_evidence_ids),
        }
        packet["inputFingerprint"] = fingerprint(packet)
        result.append(packet)
    return sorted(result, key=lambda row: row["evidenceId"])


def eligible_packets(task: Dict[str, object], packets: List[Dict[str, object]], symbol: str, assessed_at: str):
    cutoff = parse_datetime(assessed_at)
    accepted, excluded = [], []
    try:
        age_limit = max(1, int(task.get("maxAgeMinutes") or 360))
    except (ValueError, TypeError):
        age_limit = 360
    for packet in packets:
        reasons = []
        observed = parse_datetime(str(packet.get("observedAt") or ""))
        published = parse_datetime(str(packet.get("publishedAt") or ""))
        if not cutoff or not observed:
            reasons.append("observation-time-unavailable")
        if packet.get("publishedAt") and not published:
            reasons.append("publication-time-invalid")
        if cutoff and any(stamp and stamp > cutoff for stamp in (observed, published)):
            reasons.append("not-known-at-assessment")
        reference = (parse_datetime(packet.get("freshnessObservedAt")) or observed) if packet.get("freshnessBasis") == "source-observation" else published or observed
        if cutoff and reference and (cutoff - reference).total_seconds() > age_limit * 60:
            reasons.append("stale-for-task")
        if str(packet.get("symbol") or "").upper() != symbol.upper():
            reasons.append("different-subject")
        sources = set(texts(task.get("sourceTypes")))
        if sources and not sources.intersection(packet.get("sourceTypes") or []):
            reasons.append("source-type-mismatch")
        if not str(packet.get("source") or "").strip():
            reasons.append("source-missing")
        if reasons:
            excluded.append({"evidenceId": packet["evidenceId"], "reasons": reasons})
        else:
            accepted.append(packet)
    return accepted, excluded


def missing_requirements(task: Dict[str, object], packets: List[Dict[str, object]]) -> List[str]:
    available = {value for packet in packets for value in packet.get("evidenceTypes") or []}
    if packets:
        available.update(METADATA_REQUIREMENTS)
    # Counting articles or publishers does not demonstrate independent
    # confirmation of the same claim. Use the existing claim-governance links.
    packet_ids = {packet["evidenceId"] for packet in packets}
    if any(
        int(packet.get("independentSourceCount") or 0) >= 2
        and packet_ids.intersection((packet.get("corroboratingEvidenceIds") or []) + (packet.get("officialEvidenceIds") or []))
        for packet in packets
    ):
        available.add("independent-confirmation")
    missing = [requirement for requirement in texts(task.get("requiredEvidenceTypes"))
               if requirement not in available and requirement not in SEMANTIC_REQUIREMENTS]
    periods = {str(packet.get("periodEnd") or "")[:10] for packet in packets}
    missing.extend("period:" + period for period in texts(task.get("requiredPeriodEnds"), 12) if period not in periods)
    for metric in texts(task.get("requiredMetrics"), 20):
        comparable_bases = set()
        for period in texts(task.get("requiredPeriodEnds"), 12) or [""]:
            matching = [packet for packet in packets if not period or packet.get("periodEnd") == period]
            matching = [packet for packet in matching if (packet.get("reportedValues") or {}).get(metric) is not None]
            if not matching:
                missing.append("metric:" + metric + (":" + period if period else ""))
            for packet in matching:
                provenance = (packet.get("metricProvenance") or {}).get(metric) or {}
                basis = tuple(str(provenance.get(key) or "") for key in ("provider", "currency", "scope", "durationBasis"))
                if not all(basis):
                    missing.append("metric-basis:" + metric)
                comparable_bases.add(basis)
        if len(comparable_bases) > 1:
            missing.append("incomparable-metric:" + metric)
    if not packets:
        missing.append("question-evidence")
    return texts(missing)


def assess_tasks(
    tasks: List[Dict[str, object]], packets: List[Dict[str, object]],
    symbol: str, assessed_at: str, reviews: List[Dict[str, object]] = None,
) -> List[Dict[str, object]]:
    """Only a cited semantic review plus complete source coverage may stop work."""
    review_by_id = {str(row.get("taskId") or ""): row for row in reviews or [] if isinstance(row, dict)}
    assessments = []
    for task in tasks:
        candidates, excluded = eligible_packets(task, packets, symbol, assessed_at)
        candidate_ids = {row["evidenceId"] for row in candidates}
        assessment_id = fingerprint({"version": RESEARCH_PROGRESS_VERSION, "task": task_contract(task), "evidence": candidates})
        review = review_by_id.get(str(task.get("taskId") or ""), {})
        cited = texts(review.get("evidenceIds"))
        counter = texts(review.get("counterEvidenceIds"))
        review_valid = bool(
            review.get("assessmentFingerprint") == assessment_id
            and str(review.get("reason") or "").strip()
            and cited and set(cited + counter).issubset(candidate_ids)
            and review.get("status") in {"addressed", "partial", "unresolved"}
        )
        selected = [row for row in candidates if row["evidenceId"] in set(cited + counter)] if review_valid else candidates
        missing = missing_requirements(task, selected)
        satisfied = review_valid and review.get("status") == "addressed" and not missing
        assessments.append({
            "taskId": str(task.get("taskId") or ""), "question": str(task.get("question") or ""),
            "status": "addressed" if satisfied else "needs-evidence" if missing else "needs-review",
            "coverageState": "complete" if not missing else "incomplete",
            "semanticReviewState": str(review.get("status")) if review_valid else "unreviewed",
            "assessmentFingerprint": assessment_id, "assessedAt": assessed_at,
            "candidateEvidenceIds": sorted(candidate_ids),
            "resultEvidenceIds": cited if review_valid else [],
            "counterEvidenceIds": counter if review_valid else [],
            "missingRequirements": missing,
            "semanticRequirements": sorted(set(texts(task.get("requiredEvidenceTypes"))).intersection(SEMANTIC_REQUIREMENTS)),
            "excludedEvidence": excluded,
            "reason": str(review.get("reason") or "")[:600] if review_valid else "",
            "reviewRejected": bool(review) and not review_valid,
            "decisionEligibility": "research-only",
        })
    return assessments


def all_tasks_addressed(assessments: List[Dict[str, object]]) -> bool:
    return bool(assessments) and all(row.get("status") == "addressed" for row in assessments)


def progress_plan(plan: Dict[str, object], assessments: List[Dict[str, object]], packets=None) -> Dict[str, object]:
    by_id = {row["taskId"]: row for row in assessments}
    return {
        **plan,
        "tasks": [{**task, "evidenceAssessment": by_id.get(str(task.get("taskId") or ""), {}),
                   "resultEvidenceIds": by_id.get(str(task.get("taskId") or ""), {}).get("resultEvidenceIds", [])}
                  for task in plan.get("tasks") or [] if isinstance(task, dict)],
        "evidenceAssessment": {"version": RESEARCH_PROGRESS_VERSION, "tasks": assessments,
                               "evidencePackets": list(packets or []), "decisionEligibility": "research-only"},
    }
