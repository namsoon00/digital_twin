"""Domain vocabulary for one instrument's price and event timeline."""

from dataclasses import dataclass
import hashlib
import re
from typing import Dict
from urllib.parse import parse_qs, urlsplit


TIMELINE_RANGES: Dict[str, Dict[str, object]] = {
    "1d": {"interval": "15m", "limit": 96},
    "1w": {"interval": "1h", "limit": 168},
    "1m": {"interval": "1d", "limit": 31},
    "3m": {"interval": "1d", "limit": 92},
    "6m": {"interval": "1d", "limit": 184},
    "1y": {"interval": "1d", "limit": 366},
    "3y": {"interval": "1d", "limit": 780},
    "all": {"interval": "1d", "limit": 1000},
}
TIMELINE_INTERVALS = {"3m", "15m", "1h", "1d"}


def normalize_instrument_symbol(value: object) -> str:
    return str(value or "").upper().strip()[:32]


OFFICIAL_EVIDENCE_KINDS = {"disclosure", "filing", "sec-filing", "sec_filing", "issuer-ir"}


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _payload(value: object) -> Dict[str, object]:
    raw = getattr(value, "raw_payload", None)
    if isinstance(raw, dict):
        return dict(raw)
    return dict(value or {}) if isinstance(value, dict) else {}


def official_document_id(value: object) -> str:
    """Return the provider-owned identity for one filing or disclosure."""

    payload = _payload(value)
    for key in ["receiptNo", "receipt_no", "rcept_no", "accessionNumber", "accession"]:
        identifier = _text(payload.get(key))
        if identifier:
            return identifier
    url = _text(getattr(value, "url", "") or payload.get("officialDocumentUrl") or payload.get("sourceUrl"))
    if url:
        try:
            receipt = (parse_qs(urlsplit(url).query).get("rcpNo") or [""])[0]
        except ValueError:
            receipt = ""
        if receipt:
            return _text(receipt)
        accession = re.search(r"/([0-9]{10}-[0-9]{2}-[0-9]{6})(?:/|$)", url)
        if accession:
            return accession.group(1)
    return ""


def disclosure_reporter(value: object) -> str:
    """Find the person/entity that makes otherwise identical filings distinct."""

    payload = _payload(value)
    for key in ["filerName", "reporterName", "reportingOwnerName", "submitterName", "flrName"]:
        reporter = _text(payload.get(key))
        if reporter:
            return reporter[:80]
    document = _text(payload.get("officialDocumentText") or payload.get("officialDocumentPreview"))
    if document:
        match = re.search(r"보고자\s*:\s*(.{1,80}?)\s+(?:1\.|발행회사에 관한 사항)", document)
        if match:
            return _text(match.group(1))[:80]
    return ""


def evidence_timeline_identity(value: object) -> str:
    """Identify one timeline item without merging distinct official documents."""

    payload = _payload(value)
    kind = _text(getattr(value, "kind", "") or payload.get("kind")).lower()
    evidence_id = _text(getattr(value, "evidence_id", "") or payload.get("evidenceId") or payload.get("id"))
    official_id = official_document_id(value)
    if kind in OFFICIAL_EVIDENCE_KINDS and official_id:
        return "official:" + official_id
    if kind == "news":
        story_id = _text(
            payload.get("storyClusterId")
            or payload.get("eventEpisodeId")
            or payload.get("canonicalEventId")
        )
        if story_id:
            return story_id if story_id.startswith("story:") else "story:" + story_id
        article_identity = _text(
            payload.get("articleIdentityUrl")
            or payload.get("articleCanonicalUrl")
            or payload.get("canonicalUrl")
        )
        if article_identity:
            return "article:" + hashlib.sha1(article_identity.encode("utf-8")).hexdigest()[:24]
    if evidence_id:
        return "evidence:" + evidence_id
    fallback = "|".join([
        kind,
        _text(getattr(value, "source", "") or payload.get("source")),
        _text(getattr(value, "title", "") or payload.get("title")),
        _text(getattr(value, "published_at", "") or payload.get("publishedAt")),
    ])
    return "evidence-anonymous:" + hashlib.sha1(fallback.encode("utf-8")).hexdigest()[:24]


def evidence_timeline_copy(value: object) -> Dict[str, object]:
    """Build concise, distinguishing copy for the instrument timeline."""

    payload = _payload(value)
    title = _text(getattr(value, "title", "") or payload.get("title") or "새 투자 근거")
    summary = _text(
        payload.get("articleSummaryKo")
        or getattr(value, "summary", "")
        or payload.get("summary")
    )
    kind = _text(getattr(value, "kind", "") or payload.get("kind")).lower()
    reporter = disclosure_reporter(value)
    document_id = official_document_id(value)
    if kind in OFFICIAL_EVIDENCE_KINDS:
        if reporter and reporter.casefold() not in title.casefold():
            title += " · " + reporter
        analysis = payload.get("disclosureAnalysis") if isinstance(payload.get("disclosureAnalysis"), dict) else {}
        summary = _text(analysis.get("summary") or summary)
        identifier_label = "접수번호" if document_id.isdigit() else "문서번호"
        identifier_text = (identifier_label + " " + document_id) if document_id else ""
        if identifier_text and identifier_text not in summary:
            summary = " · ".join(item for item in [summary, identifier_text] if item)
    return {
        "title": title,
        "summary": summary,
        "reporter": reporter,
        "documentId": document_id,
        "identity": evidence_timeline_identity(value),
    }


@dataclass(frozen=True)
class InstrumentTimelineQuery:
    symbol: str
    account_id: str = ""
    range_key: str = "3m"
    interval: str = ""

    def normalized(self) -> "InstrumentTimelineQuery":
        range_key = str(self.range_key or "3m").strip().lower()
        if range_key not in TIMELINE_RANGES:
            range_key = "3m"
        interval = str(self.interval or TIMELINE_RANGES[range_key]["interval"]).strip().lower()
        if interval not in TIMELINE_INTERVALS:
            interval = str(TIMELINE_RANGES[range_key]["interval"])
        return InstrumentTimelineQuery(
            symbol=normalize_instrument_symbol(self.symbol),
            account_id=str(self.account_id or "").strip()[:191],
            range_key=range_key,
            interval=interval,
        )

    @property
    def limit(self) -> int:
        return int(TIMELINE_RANGES[self.range_key]["limit"])
