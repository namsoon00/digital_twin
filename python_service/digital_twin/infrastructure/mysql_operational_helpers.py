import hashlib
import json
from typing import Dict

from ..domain.fact_changes import research_evidence_fact_payload


def _json_loads(value, fallback):
    try:
        payload = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return payload if isinstance(payload, type(fallback)) else fallback

def research_evidence_change_payload(
    symbol: str,
    kind: str,
    source: str,
    title: str,
    summary: str,
    url: str,
    published_at: str,
    polarity: str,
    impact_score: float,
    confidence: float,
    payload: Dict[str, object],
) -> Dict[str, object]:
    return research_evidence_fact_payload({
        "symbol": symbol,
        "kind": kind,
        "source": source,
        "title": title,
        "summary": summary,
        "url": url,
        "publishedAt": published_at,
        "polarity": polarity,
        "impactScore": round(float(impact_score or 0), 6),
        "confidence": round(float(confidence or 0), 6),
        "payload": payload if isinstance(payload, dict) else {},
    })

def _sent_key_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

def _is_duplicate_key_error(error: Exception) -> bool:
    return bool(getattr(error, "args", None)) and str(error.args[0]) == "1062"
