from typing import Dict

from ..domain.eligibility import annotate_news_eligibility


def annotate_evidence_eligibility(evidence, source_registry: object = ""):
    """Apply news-owned eligibility without importing the legacy evidence type."""
    payload = getattr(evidence, "raw_payload", {})
    payload = dict(payload or {}) if isinstance(payload, dict) else {}
    payload.setdefault("kind", str(getattr(evidence, "kind", "news") or "news"))
    context = {
        "title": getattr(evidence, "title", ""),
        "summary": getattr(evidence, "summary", ""),
        "symbol": getattr(evidence, "symbol", ""),
        "name": payload.get("name") or payload.get("companyName") or "",
        "source": getattr(evidence, "source", ""),
        "provider": payload.get("provider") or "",
        "url": getattr(evidence, "url", ""),
        "published_at": getattr(evidence, "published_at", "") or getattr(evidence, "observed_at", ""),
        "registry": source_registry,
        "lifecycle_state": getattr(evidence, "lifecycle_state", "active"),
    }
    evidence.raw_payload = annotate_news_eligibility(payload, **context)
    eligibility = evidence.raw_payload.get("newsEligibility") if isinstance(evidence.raw_payload.get("newsEligibility"), dict) else {}
    if not eligibility.get("reasoningEligible"):
        governance = evidence.raw_payload.get("evidenceGovernance") if isinstance(evidence.raw_payload.get("evidenceGovernance"), dict) else {}
        if governance:
            governance["investmentJudgmentEligible"] = False
            governance["reasons"] = list(dict.fromkeys(
                list(governance.get("reasons") or []) + ["news-reasoning-eligibility-blocked"]
            ))
            evidence.raw_payload["evidenceGovernance"] = governance
        ledger = evidence.raw_payload.get("claimLedger") if isinstance(evidence.raw_payload.get("claimLedger"), dict) else {}
        claims = []
        for raw_claim in ledger.get("claims") or []:
            claim = dict(raw_claim) if isinstance(raw_claim, dict) else {}
            claim["investmentJudgmentEligible"] = False
            claim["reasons"] = list(dict.fromkeys(
                list(claim.get("reasons") or []) + ["parent-evidence-reasoning-blocked"]
            ))
            claims.append(claim)
        if claims:
            ledger["claims"] = claims
            summary = dict(ledger.get("summary") or {}) if isinstance(ledger.get("summary"), dict) else {}
            summary["claimCount"] = len(claims)
            summary["eligibleClaimCount"] = 0
            ledger["summary"] = summary
            evidence.raw_payload["claimLedger"] = ledger
        evidence.raw_payload = annotate_news_eligibility(evidence.raw_payload, **context)
    return evidence


def evidence_eligibility(evidence) -> Dict[str, object]:
    annotated = annotate_evidence_eligibility(evidence)
    payload = annotated.raw_payload if isinstance(getattr(annotated, "raw_payload", None), dict) else {}
    return dict(payload.get("newsEligibility") or {})
