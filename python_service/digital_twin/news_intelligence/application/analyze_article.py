from typing import Dict

from ..domain.eligibility import annotate_news_eligibility


def annotate_evidence_eligibility(evidence):
    """Apply news-owned eligibility without importing the legacy evidence type."""
    payload = getattr(evidence, "raw_payload", {})
    payload = dict(payload or {}) if isinstance(payload, dict) else {}
    payload.setdefault("kind", str(getattr(evidence, "kind", "news") or "news"))
    evidence.raw_payload = annotate_news_eligibility(
        payload,
        title=getattr(evidence, "title", ""),
        summary=getattr(evidence, "summary", ""),
        symbol=getattr(evidence, "symbol", ""),
        name=payload.get("name") or payload.get("companyName") or "",
        source=getattr(evidence, "source", ""),
        provider=payload.get("provider") or "",
        url=getattr(evidence, "url", ""),
        lifecycle_state=getattr(evidence, "lifecycle_state", "active"),
    )
    return evidence


def evidence_eligibility(evidence) -> Dict[str, object]:
    annotated = annotate_evidence_eligibility(evidence)
    payload = annotated.raw_payload if isinstance(getattr(annotated, "raw_payload", None), dict) else {}
    return dict(payload.get("newsEligibility") or {})
