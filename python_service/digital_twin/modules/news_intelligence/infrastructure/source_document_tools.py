"""Read original public documents; admit only dated, issuer-bound source text."""
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from digital_twin.modules.market_data.contracts import issuer_ir_source, parse_datetime
from digital_twin.infrastructure.sec_report_passages import question_document_blocks
from digital_twin.infrastructure.external_api.adapters.issuer_ir import parse_ir_documents, _date
from ..domain.investment_research import ResearchEvidence, target_aliases
from ..domain.question_passages import select_question_passages
from ..domain.source_exploration import VERSION, public_url
from .public_document_http import fetch_public_document


class _Metadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.in_title, self.dates, self.explicit_dates, self.article_dates = [], False, [], [], []
        self.date_depth, self.date_parts = 0, []
        self.article_date = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "title":
            self.in_title = not self.title  # Later SVG titles describe controls, not this document.
        if tag == "meta" and (values.get("property") or values.get("name") or values.get("itemprop")) in {
                "article:published_time", "datePublished", "date", "pubdate", "publication_date"}:
            self.explicit_dates.append(values.get("content", ""))
        if tag == "time" and values.get("datetime") and values.get("itemprop") != "dateModified":
            self.dates.append(values["datetime"])
        if tag in {"span", "div"}:
            if self.date_depth:
                self.date_depth += 1
            elif (re.search(r"(?:^|[-_\s])(?:date|published)(?:$|[-_\s])", values.get("class", "") + " " + values.get("id", ""), re.I)
                  and not re.search(r"updated|modified", values.get("class", "") + " " + values.get("id", ""), re.I)):
                self.date_depth = 1
                self.article_date = bool(re.search(r"(?:article|release|publication|publish)[-_]date(?:$|\s)",
                                                   values.get("class", "") + " " + values.get("id", ""), re.I))

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if self.date_depth:
            self.date_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in {"span", "div"} and self.date_depth:
            self.date_depth -= 1
            if not self.date_depth:
                (self.article_dates if self.article_date else self.dates).append(" ".join(self.date_parts))
                self.date_parts = []


def publication_metadata(markup):
    parser = _Metadata()
    parser.feed(markup)
    parser.explicit_dates.extend(re.findall(r'"datePublished"\s*:\s*"([^"]+)"', markup))
    dated = {}
    for value in parser.explicit_dates or parser.article_dates or parser.dates:
        date = _date(value)
        if date and parse_datetime(date):
            dated[date] = value[:200]
    if len(dated) == 1:
        date = next(iter(dated))
        return date, {"kind": "original-publication-metadata", "value": dated[date]}, " ".join(parser.title)[:300]
    return "", {}, " ".join(parser.title)[:300]


def pdf_pages(data, timeout):
    completed = subprocess.run([sys.executable, str(Path(__file__).with_name("pdf_text.py"))],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=max(1, min(18, timeout)),
        env={"PYTHONIOENCODING": "utf-8"}, check=False)
    if completed.returncode or len(completed.stdout) > 3_000_000:
        raise ValueError("pdf-extraction-unavailable")
    result = json.loads(completed.stdout)
    if not result.get("pages"):
        raise ValueError("pdf-text-unavailable-ocr-required")
    return result


def _host(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


class SourceDocumentTools:
    def __init__(self, search_provider, fetch=fetch_public_document, pdf_reader=pdf_pages):
        self.search_provider, self.fetch, self.pdf_reader = search_provider, fetch, pdf_reader

    def seeds(self, target, packets):
        source = issuer_ir_source(target.normalized_symbol())
        rows = [{"url": url, "title": source.issuer_name + " 공식 IR", "discovery": "issuer-registry",
                 "verification": "registered-issuer-source"} for url in source.source_urls] if source else []
        # Reusing an exact dated source URL is a lead, not a new verification.
        rows.extend({"url": row["sourceUrl"], "title": row.get("title", ""), "discovery": "existing-evidence"}
                    for row in packets[:6] if row.get("sourceUrl"))
        return rows

    def search(self, target, query, timeout):
        source = issuer_ir_source(target.normalized_symbol())
        return self.search_provider.search(target, query, timeout,
            issuer_sites=list(source.source_urls) if source else [])

    def read(self, target, candidate, tasks, timeout):
        deadline = time.monotonic() + timeout
        receipt = self.fetch(candidate["url"], timeout)
        url, data = public_url(receipt["url"]), receipt["body"]
        source = issuer_ir_source(target.normalized_symbol())
        hosts = {_host(value) for value in source.source_urls} if source else set()
        source_bound = _host(url) in hosts
        requested = urlsplit(candidate["url"])
        issuer_redirect = (data.startswith(b"%PDF-") and _host(candidate["url"]) in hosts
                           and requested.path.lower().endswith(".pdf") and not requested.query
                           and bool(receipt.get("redirects")))
        # A PDF hosted by a CDN is eligible only when an actually read official
        # issuer page linked that exact URL. A search model cannot assert this.
        linked_pdf = (data.startswith(b"%PDF-") and candidate.get("discovery") == "official-page-link"
                      and _host(candidate.get("discoveredFrom", "")) in hosts
                      and url == candidate["url"])
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        document_hash = hashlib.sha256(data).hexdigest()
        content_type = str(receipt.get("contentType") or "").lower()
        date, date_proof, title, links, page_info = "", {}, candidate.get("title", ""), [], {}
        if data.startswith(b"%PDF-"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("document-time-budget-exhausted")
            parsed = self.pdf_reader(data, remaining)
            pages = parsed["pages"]
            blocks = [row["text"] for row in pages]
            page_info = {key: parsed[key] for key in ("pageCount", "omittedPages", "unreadPageCount")}
            if candidate.get("publicationProof") and (linked_pdf or source_bound or issuer_redirect):
                date, date_proof = candidate.get("publishedAt", ""), candidate["publicationProof"]
            document_type = "pdf"
        elif "html" in content_type or "text/plain" in content_type:
            charset = re.search(r"charset=([\w-]+)", content_type)
            try:
                markup = data.decode(charset.group(1) if charset else "utf-8", errors="replace")
            except LookupError:
                raise ValueError("unsupported-document-charset") from None
            if re.search(r"verify you are human|request rate threshold exceeded|undeclared automated tool|<title>[^<]*(?:access denied|just a moment)", markup, re.I):
                raise ValueError("document-access-blocked")
            date, date_proof, page_title = publication_metadata(markup)
            title = page_title or title
            blocks = question_document_blocks(markup)
            pages, document_type = [], "html"
            for link in parse_ir_documents(markup, url, limit=40):
                try:
                    child_url = public_url(urljoin(url, link["url"]))
                except ValueError:
                    continue
                row = {"url": child_url, "title": link["title"], "discoveredFrom": url,
                       "parentDocumentHash": document_hash,
                       "discovery": "official-page-link" if source_bound else "public-page-link"}
                if source_bound and link.get("publishedAt") and _date(link["title"]) == link["publishedAt"]:
                    row.update(publishedAt=link["publishedAt"], publicationProof={"kind": "official-index-label",
                        "sourceUrl": url, "sourceDocumentHash": document_hash, "value": link["title"]})
                links.append(row)
        else:
            raise ValueError("unsupported-document-format")
        passages = select_question_passages(blocks, tasks, 14000)
        for passage in passages:
            matches = [row["page"] for row in pages if row["text"] == passage["quote"]]
            if matches:
                passage["page"] = matches[0]
        document = {"url": url, "title": title[:300], "documentType": document_type,
            "sourceDocumentHash": document_hash, "observedAt": now, "publishedAt": date,
            "publicationProof": date_proof, "passages": passages, **page_info,
            "issuerVerification": "registered-issuer-source" if source_bound else "official-issuer-redirect" if issuer_redirect else "official-linked-document" if linked_pdf else "unverified-source",
            "readScope": "question-selected-passages", "redirects": receipt.get("redirects", [])}
        result = {"status": "document-read", "document": document, "candidates": links}
        reasons = []
        if not (source_bound or linked_pdf or issuer_redirect):
            reasons.append("issuer-source-unverified")
        subject_text = " ".join([title, *blocks]).casefold()
        if not any(re.search(r"(?<![\w])" + re.escape(alias.casefold()) + r"(?![\w])", subject_text)
                   for alias in target_aliases(target) if len(alias) >= 3):
            reasons.append("document-subject-unconfirmed")
        if not date:
            reasons.append("publication-date-unverified")
        elif not parse_datetime(date) or date[:10] > now[:10]:
            reasons.append("publication-date-invalid")
        if not passages or len("\n\n".join(row["quote"] for row in passages)) < 400:
            reasons.append("question-passages-insufficient")
        document["admissionBlockers"] = reasons
        if reasons:
            result["status"] = "document-reference-only"
            return result
        text = "\n\n".join(row["quote"] for row in passages)
        symbol = target.normalized_symbol()
        raw = {"provider": "question-source-exploration", "sourcePublisher": source.issuer_name,
            "officialSource": True, "primarySource": True, "relationScope": "direct",
            "officialDocumentText": text, "officialDocumentType": document_type,
            "officialDocumentScope": "question-selected-passages", "documentPassages": passages,
            "officialDocumentQuality": "body", "documentTextVersion": VERSION,
            "metadataVerified": True, "analysisReady": True, "documentHash": document_hash,
            "documentIssuerIdentity": {"symbol": symbol, "sourceUrl": url,
                "registryUrls": list(source.source_urls), "verification": document["issuerVerification"],
                "requestedUrl": candidate["url"], "redirects": receipt.get("redirects", []),
                "discoveredFrom": candidate.get("discoveredFrom", ""),
                "parentDocumentHash": candidate.get("parentDocumentHash", "")},
            "sourceDocumentHash": document_hash, "sourceRevision": document_hash,
            "publicationProof": date_proof, "sourceAsOf": date, "readScope": "body",
            "documentVerified": True, "officialDocumentState": "document-verified",
            "sourceTrustState": "trusted", "dataState": "sufficient", "validationState": "ready",
            "researchTaskIds": [row["taskId"] for row in tasks], "sourceExplorationVersion": VERSION,
            "valuationInputEligible": False, "documentUsePolicy": "issuer-reported-reference-until-cross-checked"}
        result["evidence"] = ResearchEvidence(
            evidence_id="research:" + symbol + ":explored:" + hashlib.sha256((url + document_hash + text).encode()).hexdigest()[:24],
            symbol=symbol, kind="issuer-ir", source=source.issuer_name + " Investor Relations",
            title=title[:300], summary=text[:1000], url=url, observed_at=now, published_at=date,
            raw_payload=raw)
        result["status"] = "document-collected"
        return result
