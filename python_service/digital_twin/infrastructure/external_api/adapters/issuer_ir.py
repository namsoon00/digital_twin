import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from datetime import datetime, timezone
from typing import Dict, Iterable, List
from xml.etree import ElementTree

from digital_twin.modules.market_data.public import CollectionJob, CollectionPartition, DatasetDescriptor, ExternalSubject
from digital_twin.modules.market_data.domain.issuer_ir import ISSUER_IR_DATASET_ID, issuer_ir_source
from digital_twin.modules.portfolio.domain.portfolio import utc_now_iso
from .base import equity_partitions, observation


IR_TERMS = (
    "earnings", "financial results", "quarterly results", "presentation", "shareholder letter",
    "annual report", "conference call", "webcast", "transcript", "press release", "실적", "경영실적",
    "발표자료", "주주총회", "주주서한", "사업보고서", "감사보고서", "재무제표", "기업설명회",
)
DOCUMENT_EXTENSIONS = (".pdf", ".ppt", ".pptx", ".xls", ".xlsx", ".doc", ".docx")
IGNORED_EXTENSIONS = (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2")
IGNORED_PATH_PARTS = ("/tag/", "/category/", "/search", "/privacy", "/contact", "/email-alert")
DATE_PATTERNS = (
    re.compile(r"\b(20\d{2})[.\-/ ](0?[1-9]|1[0-2])[.\-/ ]([0-2]?\d|3[01])\b"),
    re.compile(r"\b(20\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])\b"),
)
MONTHS = {name.casefold(): index for index, name in enumerate((
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
), start=1)}
MONTH_DATE_PATTERN = re.compile(
    r"\b(" + "|".join(MONTHS) + r")\s+([0-2]?\d|3[01]),?\s+(20\d{2})\b",
    re.IGNORECASE,
)


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def _date(value: object) -> str:
    text = _clean(value)
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            return "-".join([match.group(1), match.group(2).zfill(2), match.group(3).zfill(2)])
    match = MONTH_DATE_PATTERN.search(text)
    if match:
        return "-".join([match.group(3), str(MONTHS[match.group(1).casefold()]).zfill(2), match.group(2).zfill(2)])
    return ""


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs or [])
        if str(tag or "").lower() not in {"a", "button"}:
            return
        href = values.get("href") or values.get("data-url") or values.get("data-file") or values.get("data-download")
        if not href and values.get("onclick"):
            match = re.search(r"['\"]([^'\"]+\.(?:pdf|pptx?|xlsx?|docx?)(?:\?[^'\"]*)?)['\"]", str(values.get("onclick")), re.IGNORECASE)
            if match:
                href = re.sub(r"\\u([0-9a-fA-F]{4})", lambda item: chr(int(item.group(1), 16)), match.group(1)).replace("\\/", "/")
        if href:
            self._current = {"href": str(href), "text": "", "title": str(values.get("title") or values.get("aria-label") or "")}
            self.links.append(self._current)

    def handle_data(self, data):
        if self._current is not None:
            self._current["text"] += " " + str(data or "")

    def handle_endtag(self, tag):
        if str(tag or "").lower() in {"a", "button"}:
            self._current = None


def parse_ir_documents(markup: str, page_url: str, limit: int = 80) -> List[Dict[str, object]]:
    parser = _LinkParser()
    parser.feed(str(markup or ""))
    results = []
    seen = set()
    for raw in parser.links:
        href = _clean(raw.get("href"))
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urllib.parse.urljoin(page_url, href)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        title = _clean(raw.get("text") or raw.get("title"))
        path = parsed.path.casefold()
        if path.endswith(IGNORED_EXTENSIONS) or any(part in path for part in IGNORED_PATH_PARTS):
            continue
        searchable = title.casefold()
        extension = next((value for value in DOCUMENT_EXTENSIONS if path.endswith(value)), "")
        if not extension and not any(term.casefold() in searchable for term in IR_TERMS):
            continue
        identity = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
        if identity in seen:
            continue
        seen.add(identity)
        results.append({
            "documentId": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
            "title": title or parsed.path.rsplit("/", 1)[-1] or "IR document",
            "url": identity,
            "publishedAt": _date(title + " " + parsed.path),
            "documentType": extension.lstrip(".") if extension else "web",
            "officialSource": True,
            "bodyState": "reference-only",
        })
        if len(results) >= max(1, min(200, int(limit or 80))):
            break
    results.sort(key=lambda item: (str(item.get("publishedAt") or ""), str(item.get("title") or "")), reverse=True)
    return results


def parse_ir_json(markup: str, page_url: str, public_page_url: str, limit: int = 80) -> List[Dict[str, object]]:
    """Parse public issuer JSON indexes without treating their text as verified document bodies."""

    try:
        body = json.loads(str(markup or ""))
    except (TypeError, ValueError):
        return []
    content = body.get("data") if isinstance(body, dict) else None
    content = content.get("content") if isinstance(content, dict) else None
    if not isinstance(content, list):
        return []
    results = []
    for raw in content:
        if not isinstance(raw, dict):
            continue
        identity_value = raw.get("announcementId") or raw.get("id")
        title = _clean(raw.get("title") or raw.get("titleEn"))
        if not identity_value or not title:
            continue
        detail_url = public_page_url.rstrip("/") + "/" + urllib.parse.quote(str(identity_value)) + "?id=" + urllib.parse.quote(str(identity_value))
        attachment = _clean(raw.get("attachmentFileUrl"))
        url = urllib.parse.urljoin(page_url, attachment) if attachment else detail_url
        published = _date(raw.get("createdAt") or raw.get("date"))
        results.append({
            "documentId": hashlib.sha256((page_url + ":" + str(identity_value)).encode("utf-8")).hexdigest()[:24],
            "title": title,
            "url": url,
            "publishedAt": published,
            "documentType": "pdf" if urllib.parse.urlparse(url).path.casefold().endswith(".pdf") else "web",
            "officialSource": True,
            "bodyState": "reference-only",
        })
        if len(results) >= max(1, min(200, int(limit or 80))):
            break
    results.sort(key=lambda item: (str(item.get("publishedAt") or ""), str(item.get("title") or "")), reverse=True)
    return results


def parse_ir_xml(markup: str, page_url: str, public_page_url: str, limit: int = 80) -> List[Dict[str, object]]:
    """Parse XML content negotiation responses from public issuer JSON endpoints."""

    try:
        root = ElementTree.fromstring(str(markup or ""))
    except (ElementTree.ParseError, TypeError, ValueError):
        return []
    rows = []
    for node in root.findall(".//data/content/content"):
        row = {child.tag: child.text or "" for child in list(node)}
        if row:
            rows.append(row)
    if not rows:
        return []
    return parse_ir_json(json.dumps({"data": {"content": rows}}), page_url, public_page_url, limit=limit)


def _fetch(url: str, timeout: float) -> tuple[str, str, str]:
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(2_000_000)
        content_type = str(response.headers.get_content_type() or "")
        charset = response.headers.get_content_charset() or "utf-8"
        return body.decode(charset, errors="replace"), str(response.geturl() or url), content_type


class IssuerIrDocumentsAdapter:
    descriptor = DatasetDescriptor(
        dataset_id=ISSUER_IR_DATASET_ID,
        provider_id="issuer-ir",
        capability="official-issuer-ir-index",
        cadence_seconds=43200,
        freshness_seconds=172800,
        priority=72,
        rate_limit_seconds=1,
        failure_threshold=4,
        circuit_cooldown_seconds=3600,
        max_partitions=100,
        revision_mode="changes",
        materiality_policy="source-revision",
        source_schema_version="issuer.ir_documents-source-v1",
    )

    def partitions(self, subjects: Iterable[ExternalSubject], _settings: Dict[str, object]) -> List[CollectionPartition]:
        return equity_partitions(self.descriptor, subjects, predicate=lambda subject: issuer_ir_source(subject.symbol or subject.subject_key) is not None)

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        source = issuer_ir_source(job.subject.symbol or job.subject.subject_key)
        if source is None:
            raise RuntimeError("official issuer IR source is not registered: " + job.subject.subject_key)
        try:
            timeout = max(2.0, min(30.0, float(settings.get("externalIssuerIrTimeoutSeconds") or 12)))
        except (TypeError, ValueError):
            timeout = 12.0
        try:
            limit = max(5, min(200, int(float(settings.get("externalIssuerIrMaxDocuments") or 80))))
        except (TypeError, ValueError):
            limit = 80
        blocked = []
        reachable = []
        for url in source.source_urls:
            try:
                markup, resolved_url, content_type = _fetch(url, timeout)
            except urllib.error.HTTPError as error:
                if int(getattr(error, "code", 0) or 0) in {401, 403, 404, 410, 429}:
                    blocked.append({"url": url, "statusCode": int(error.code)})
                    continue
                raise
            stripped = str(markup or "").lstrip("\ufeff \t\r\n")
            if "json" in content_type.casefold() or stripped.startswith(("{", "[")):
                documents = parse_ir_json(markup, resolved_url, source.primary_url, limit=limit)
            elif stripped.startswith("<Result"):
                documents = parse_ir_xml(markup, resolved_url, source.primary_url, limit=limit)
            else:
                documents = parse_ir_documents(markup, resolved_url, limit=limit)
            reachable.append({"url": resolved_url, "contentType": content_type, "documentCount": len(documents)})
            if not documents:
                continue
            latest = max((str(item.get("publishedAt") or "") for item in documents), default="")
            fragment = {
                "issuerIrDocuments": {
                    source.symbol: {
                        "symbol": source.symbol,
                        "issuerName": source.issuer_name,
                        "market": source.market,
                        "provider": "Official issuer IR",
                        "officialSource": True,
                        "sourceUrl": source.primary_url,
                        "retrievalUrl": resolved_url,
                        "sourceHost": urllib.parse.urlparse(source.primary_url).netloc,
                        "contentType": content_type,
                        "checkedAt": utc_now_iso(),
                        "documentCount": len(documents),
                        "latestPublishedAt": latest,
                        "items": documents,
                        "fallbackDatasets": list(source.fallback_datasets),
                        "documentUsePolicy": "reference-only-until-body-verified",
                        "blockedAttempts": blocked,
                    }
                }
            }
            return observation(
                self.descriptor,
                source.symbol,
                fragment,
                preferred_source_as_of=latest or datetime.now(timezone.utc).date().isoformat(),
                watermark={"sourceUrl": resolved_url, "documentCount": len(documents), "latestPublishedAt": latest},
                quality={
                    "dataUsable": bool(documents),
                    "provider": self.descriptor.provider_id,
                    "officialSource": True,
                    "accessState": "reachable",
                    "documentCount": len(documents),
                    "latestPublishedAt": latest,
                    "bodyVerifiedCount": 0,
                    "decisionUse": "reference-only",
                },
            )
        access_state = "reachable" if reachable else "blocked"
        use_policy = "official-page-empty-use-filing-fallback" if reachable else "official-page-blocked-use-filing-fallback"
        fragment = {
            "issuerIrDocuments": {
                source.symbol: {
                    "symbol": source.symbol,
                    "issuerName": source.issuer_name,
                    "market": source.market,
                    "provider": "Official issuer IR",
                    "officialSource": True,
                    "sourceUrl": source.primary_url,
                    "checkedAt": utc_now_iso(),
                    "documentCount": 0,
                    "latestPublishedAt": "",
                    "items": [],
                    "fallbackDatasets": list(source.fallback_datasets),
                    "documentUsePolicy": use_policy,
                    "blockedAttempts": blocked,
                    "reachableAttempts": reachable,
                }
            }
        }
        return observation(
            self.descriptor,
            source.symbol,
            fragment,
            preferred_revision=access_state + ":" + hashlib.sha256(str({"blocked": blocked, "reachable": reachable}).encode("utf-8")).hexdigest()[:24],
            preferred_source_as_of=datetime.now(timezone.utc).date().isoformat(),
            watermark={"sourceUrl": source.primary_url, "accessState": access_state, "blockedAttempts": blocked, "reachableAttempts": reachable},
            quality={
                "dataUsable": False,
                "provider": self.descriptor.provider_id,
                "officialSource": True,
                "availability": "missing",
                "accessState": access_state,
                "documentCount": 0,
                "bodyVerifiedCount": 0,
                "decisionUse": "fallback-official-filing-only",
            },
        )
