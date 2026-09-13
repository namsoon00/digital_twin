"""Read-only, source-bound facts; never an investment decision or admission rule."""

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .disclosure_quality import normalize_official_document_text
from .news_ai_analysis import article_text_parts, source_text_hash


INFORMATION_BRIEF_VERSION = "source-bound-information-v1"
OFFICIAL_KINDS = {"disclosure", "filing", "sec-filing", "sec_filing"}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def source_url(value):
    text = str(value or "").strip()
    try:
        parts = urlsplit(text)
        return text if parts.scheme in {"http", "https"} and parts.hostname and not parts.username and not parts.password and not re.search(r"[\x00-\x20]", text) else ""
    except ValueError:
        return ""


def _time(value):
    try:
        result = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _count(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def build_information_brief(item, now=None, eligibility=None):
    """Recheck citations against retained text without upgrading source trust."""
    raw = _mapping(getattr(item, "raw_payload", {}))
    official = str(item.kind).lower() in OFFICIAL_KINDS
    analysis = _mapping(raw.get("disclosureAnalysis") if official else raw.get("aiAnalysis"))
    quality = _mapping(eligibility if eligibility is not None else raw.get("newsEligibility"))
    body = normalize_official_document_text(raw.get("officialDocumentText"), 50000) if official else _text(raw.get("articleText"))
    source_hash = hashlib.sha256(body.encode("utf-8")).hexdigest() if body else ""
    invalid = quality.get("reviewState") == "content-invalid" or raw.get("analysisConflict") is True
    lifecycle = str(getattr(item, "lifecycle_state", "") or raw.get("evidenceLifecycleState") or "active")
    document_lifecycle = _mapping(raw.get("documentLifecycle"))
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    published = _time(item.published_at)
    future = bool(published and published > now)
    facts = []
    unmatched = 0
    candidates = _mapping(raw.get("claimLedger")).get("claims") or []
    candidates = list(candidates) if isinstance(candidates, list) else []
    if official:
        candidates = [{"statement": row.get("text"), "state": "document-statement"}
                      for row in analysis.get("sourceSections") or [] if isinstance(row, dict)] + candidates
    seen = set()
    for candidate in candidates[:30]:
        candidate = _mapping(candidate)
        statement = _text(candidate.get("excerpt") or candidate.get("statement"))
        # A truncated source section can still be cited, but the ellipsis is not source text.
        statement = statement.rstrip("…").rstrip()
        if len(statement) < 20 or statement in seen:
            continue
        seen.add(statement)
        start = body.find(statement) if body else -1
        wrong_scope = (
            candidate.get("sourceEvidenceId") not in (None, "", item.evidence_id)
            or str(candidate.get("subjectSymbol") or candidate.get("symbol") or item.symbol).upper() != str(item.symbol).upper()
        )
        if start < 0 or wrong_scope or invalid or future or lifecycle in {"retracted", "rejected", "superseded"}:
            unmatched += 1
            continue
        claim_state = str(candidate.get("state") or "reported")
        if claim_state in {"rejected", "superseded", "conflicted"}:
            unmatched += 1
            continue
        corroborated = claim_state == "corroborated" and _count(candidate.get("independentSourceCount")) >= 2
        facts.append({
            "text": statement[:600],
            "evidenceId": item.evidence_id,
            "claimId": str(candidate.get("claimId") or ""),
            "basis": "official-document" if official and raw.get("documentVerified") is True else "unverified-document" if official else "reported",
            "label": ("공식 문서 기재" if raw.get("documentVerified") is True else "보관 본문 기재 · 출처 검증 전") if official else ("복수 원출처 보도" if corroborated else "기사에 기재"),
            "sourceUrl": source_url(item.url),
            "sourceHash": source_hash,
            "excerptStart": start,
            "excerptEnd": start + min(len(statement), 600),
        })
        if len(facts) == 6:
            break
    news_summary = _mapping(analysis.get("summary"))
    summary = _text(analysis.get("summary")) if official else _text(news_summary.get("oneLineKo") or news_summary.get("briefKo") or raw.get("articleSummaryKo"))
    interpretation = _text(analysis.get("impactSummary") if official else news_summary.get("whyItMatters") or analysis.get("impactReasonKo") or raw.get("stockImpactReasonKo"))
    checks = analysis.get("watchItems") if official else news_summary.get("watchPoints") or news_summary.get("watchItems") or analysis.get("nextChecks")
    if not isinstance(checks, list):
        checks = []
    if not checks and not official:
        checks = news_summary.get("checkPoints") or []
    checks = checks if isinstance(checks, list) else []
    analysis_stale = False
    if not official and analysis.get("sourceTextHash"):
        title, article_body, feed_summary, _ = article_text_parts(item)
        analysis_stale = analysis["sourceTextHash"] != source_text_hash(title, article_body, feed_summary)
    elif official and analysis.get("sourceTextHash") and raw.get("documentHash"):
        analysis_stale = analysis["sourceTextHash"] != raw["documentHash"]
    metadata_only = official and not body
    if metadata_only:
        summary = _text(item.title)
        interpretation = ""
        checks = []
    display_analysis = not (invalid or future or analysis_stale or lifecycle in {"retracted", "rejected", "superseded"})
    warnings = []
    if invalid:
        warnings.append("제목·본문 또는 분석 내용의 불일치가 감지됐습니다.")
    if future:
        warnings.append("발행시각이 현재보다 미래여서 근거를 표시하지 않습니다.")
    if unmatched:
        warnings.append("보관된 원문에서 대조되지 않거나 사용할 수 없는 문장이 있습니다.")
    if not body:
        warnings.append("원문 본문이 없어 제목·접수 정보만 확인할 수 있습니다.")
    if analysis_stale:
        warnings.append("원문이 분석 이후 변경되어 이전 요약·해석을 표시하지 않습니다.")
    if lifecycle != "active":
        warnings.append("현재 자료 상태: " + {"expired": "활용 기한이 지난 과거 자료", "retracted": "철회됨", "superseded": "후속 자료로 대체됨", "rejected": "검증에서 제외됨"}.get(lifecycle, "검토 필요"))
    state = "content-invalid" if invalid or future else "source-linked" if facts else "metadata-only" if not body else "verification-pending"
    return {
        "version": INFORMATION_BRIEF_VERSION,
        "state": state,
        "summary": summary if display_analysis else "",
        "summaryRole": ("metadata" if metadata_only else "analysis") if summary and display_analysis else "unavailable",
        "facts": facts,
        "interpretation": interpretation if display_analysis else "",
        "interpretationRole": "analysis-opinion",
        "followUps": [{"text": _text(value)[:500], "status": "not-registered", "statusLabel": "자동 관찰 미등록"}
                      for value in checks[:4] if display_analysis and isinstance(value, str) and _text(value)],
        "warnings": warnings,
        "unmatchedClaimCount": unmatched,
        "publishedAt": str(item.published_at or ""),
        "sourceAgeHours": round((now - published).total_seconds() / 3600, 1) if published and not future else None,
        "collectedAt": str(raw.get("sourceFetchedAt") or item.observed_at or ""),
        "sourceHash": source_hash,
        "sourceUrl": source_url(item.url),
        "sourceRevision": str(raw.get("articleSourceRevision") or raw.get("documentHash") or raw.get("sourceRevision") or ""),
        "lifecycle": lifecycle,
        "correction": document_lifecycle,
        "independentSourceCount": _count(_mapping(raw.get("evidenceGovernance")).get("independentSourceCount")),
        "marketReaction": {"status": "not-observed", "label": "시장 반응 관측 미연결"},
        "decisionAuthority": False,
    }
