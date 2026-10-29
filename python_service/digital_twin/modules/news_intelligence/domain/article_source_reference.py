"""Exact captured article passages; never an upgrade of claim eligibility."""
import hashlib
import json
from copy import deepcopy

VERSION = "article-source-reference-v1"


def article_projection_proof(raw):
    """Bounded verification inputs for ABox assembly, not an AI summary.

    Retain the original body whole so the receiving owner can revalidate every
    passage. Oversized documents remain in their source store, never truncated.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("articleText"), str):
        return {}
    quality = raw.get("articleSummaryQuality")
    grounding = quality.get("sourceGrounding") if isinstance(quality, dict) else None
    if not isinstance(grounding, dict) or grounding.get("version") != "article-source-contract-v2-preserved-passages":
        return {}
    proof = {key: raw[key] for key in ("articleText", "articleReadStatus", "bodyQualityPassed", "articleSourceRevision") if key in raw}
    proof["articleSummaryQuality"] = {"sourceGrounding": grounding}
    return deepcopy(proof) if len(json.dumps(proof, ensure_ascii=False).encode()) <= 32000 else {}


def article_source_reference(item):
    raw = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    body = raw.get("articleText")
    quality = raw.get("articleSummaryQuality")
    grounding = quality.get("sourceGrounding") if isinstance(quality, dict) else None
    grounding = grounding if isinstance(grounding, dict) else {}
    if (item.kind != "news" or not isinstance(body, str) or not body.strip() or not item.url
            or raw.get("articleReadStatus") not in {"body", "full-body", "full", "article-body"}
            or raw.get("bodyQualityPassed") is not True
            or grounding.get("version") != "article-source-contract-v2-preserved-passages"
            or grounding.get("passed") is not True):
        return {}
    passages = grounding.get("sourcePassages") or []
    if not isinstance(passages, list) or not 1 <= len(passages) <= 16:
        return {}
    for row in passages:
        if not isinstance(row, dict) or row.get("scope") != "body" or not isinstance(row.get("text"), str):
            return {}
        text = row["text"]
        expected = "article-passage:" + hashlib.sha256(("body\n" + text).encode()).hexdigest()[:20]
        if not text or text not in body or row.get("id") != expected:
            return {}
    text = "\n\n".join(dict.fromkeys(row["text"] for row in passages))
    value = {"version": VERSION, "evidenceId": item.evidence_id, "symbol": item.symbol,
        "sourceUrl": item.url, "publishedAt": item.published_at, "observedAt": item.observed_at,
        "sourceRevision": raw.get("articleSourceRevision", ""),
        "bodyHash": hashlib.sha256(body.encode()).hexdigest(),
        "passageIds": [row["id"] for row in passages], "text": text,
        "passageTextHash": hashlib.sha256(text.encode()).hexdigest(),
        "usage": "captured-source-passages; preserve-parent-eligibility",
        "limitation": "저장된 기사 본문의 일부입니다. 기사 전체·공식 발표·인과관계 확인이 아니며 원래 품질 차단은 유지됩니다."}
    return value if len(json.dumps(value, ensure_ascii=False).encode()) <= 8000 else {}
