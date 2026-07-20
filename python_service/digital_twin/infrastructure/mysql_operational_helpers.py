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
    source_trust_state: str,
    materiality_state: str,
    data_state: str,
    validation_state: str,
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
        "sourceTrustState": str(source_trust_state or "unknown"),
        "materialityState": str(materiality_state or "context"),
        "dataState": str(data_state or "partial"),
        "validationState": str(validation_state or "conditional"),
        "payload": payload if isinstance(payload, dict) else {},
    })

def _sent_key_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

def _is_duplicate_key_error(error: Exception) -> bool:
    return bool(getattr(error, "args", None)) and str(error.args[0]) == "1062"
