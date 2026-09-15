"""Bounded source passages and summary provenance, not investment judgement."""

import hashlib
import json
import re
from typing import Dict

from digital_twin.modules.news_intelligence.domain import news_analysis


ARTICLE_SOURCE_CONTRACT_VERSION = "article-source-contract-v1"


def build_article_source_contract(target, title: str, body: str, feed_summary: str) -> Dict[str, object]:
    source = news_analysis.clean_article_body_text(body or feed_summary, 5000)
    scope = "body" if body else "feed-summary"
    selected = news_analysis.target_relevant_article_text(target, title, source, limit=3200) if source else ""
    terms = news_analysis.article_headline_terms(target, title)
    passages = []
    title_key = re.sub(r"[^a-z0-9가-힣]", "", news_analysis.clean_article_title(title).casefold())
    for text in news_analysis.article_source_sentences(selected):
        text = text.strip()
        text_key = re.sub(r"[^a-z0-9가-힣]", "", text.casefold())
        # A copied headline or a cut sentence is not an independent body source.
        if len(text) < 24 or text.endswith(("...", "…")) or text not in source:
            continue
        if title_key and text_key.startswith(title_key) and len(text_key) <= len(title_key) + 16:
            continue
        digest = hashlib.sha256((scope + "\n" + text).encode("utf-8")).hexdigest()[:20]
        if any(row["id"] == "article-passage:" + digest for row in passages):
            continue
        passages.append({
            "id": "article-passage:" + digest,
            "scope": scope,
            "text": text,
            "headlineTermHits": sum(1 for term in terms if term.casefold() in text.casefold()),
        })
    ranked = sorted(passages, key=lambda row: -row["headlineTermHits"])
    best = ranked[0]["headlineTermHits"] if ranked else 0
    primary = [row["id"] for row in ranked if best >= 2 and row["headlineTermHits"] >= best][:2]
    fingerprint = hashlib.sha256(json.dumps(
        [title, scope, source], ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:24]
    return {
        "version": ARTICLE_SOURCE_CONTRACT_VERSION,
        "sourceFingerprint": fingerprint,
        "passages": passages,
        "primaryEventCandidateIds": primary,
        "selectionBasis": "headline-overlap-candidates-not-semantic-proof",
    }


def assess_article_source_grounding(analysis: dict, contract: dict) -> Dict[str, object]:
    grounding = analysis.get("sourceGrounding")
    grounding = grounding if isinstance(grounding, dict) else {}
    passages = {row["id"]: row for row in contract.get("passages") or []}
    primary = set(contract.get("primaryEventCandidateIds") or [])
    issues = []
    state = str(grounding.get("headlineStatus") or "").strip().lower()
    if state not in {"confirmed", "contradicted", "unconfirmed", "mismatch"}:
        issues.append("source-grounding-missing")
    if state == "mismatch":
        issues.append("headline-body-event-mismatch")
    if state == "unconfirmed":
        issues.append("headline-event-unconfirmed")
    for field in ("primaryEventSourceIds", "summarySourceIds"):
        references = grounding.get(field)
        if not isinstance(references, list) or not references:
            issues.append("source-grounding-" + field + "-missing")
            continue
        if any(not isinstance(ref, str) or ref not in passages for ref in references):
            issues.append("source-grounding-unknown-passage")
            continue
        if primary and not primary.intersection(references):
            issues.append("summary-primary-event-omitted")
    if not passages:
        issues.append("source-passages-unavailable")
    issues = list(dict.fromkeys(issues))
    references = [ref for field in ("primaryEventSourceIds", "summarySourceIds")
                  for ref in (grounding.get(field) if isinstance(grounding.get(field), list) else [])
                  if isinstance(ref, str)]
    return {
        "version": ARTICLE_SOURCE_CONTRACT_VERSION,
        "sourceFingerprint": contract.get("sourceFingerprint"),
        "state": "ready" if not issues else "needs-review",
        "passed": not issues,
        "issues": issues,
        "headlineStatus": state,
        "primaryEventSourceIds": grounding.get("primaryEventSourceIds") or [],
        "summarySourceIds": grounding.get("summarySourceIds") or [],
        "sourcePassages": [
            {key: row[key] for key in ("id", "scope", "text")}
            for ref, row in passages.items() if ref in references
        ],
    }
