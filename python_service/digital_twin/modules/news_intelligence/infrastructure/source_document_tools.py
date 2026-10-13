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
from ..domain.source_recovery import EXTRACTION_VERSION
from .public_document_http import fetch_public_document


class _Metadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.in_title, self.dates, self.explicit_dates, self.article_dates = [], False, [], [], []
        self.date_depth, self.date_parts = 0, []
        self.article_date = False
        self.context = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        context = " ".join(value for _, value in self.context)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.context.append((tag, values.get("class", "") + " " + values.get("id", "")))
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
                                                   values.get("class", "") + " " + values.get("id", ""), re.I)
                    or re.search(r"(?:^|\s)(?:post-info|post-meta|entry-meta|article-header|entry-header|post-header)(?:$|\s)", context, re.I))

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if self.date_depth:
            self.date_parts.append(data)

    def handle_endtag(self, tag):
        for index in range(len(self.context) - 1, -1, -1):
            if self.context[index][0] == tag:
                del self.context[index:]
                break
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
    # Article metadata has precedence over JSON for related stories. Conflicting
    # primary publication clocks still fail closed.
    if not parser.explicit_dates and not parser.article_dates:
        parser.explicit_dates.extend(re.findall(r'"datePublished"\s*:\s*"([^"]+)"', markup))
    dated = {}
    for value in parser.explicit_dates or parser.article_dates or parser.dates:
        date = _date(value)
        if date and parse_datetime(date):
            dated[date] = value[:200]
    if len(dated) == 1:
        date = next(iter(dated))
        return date, {"kind": "original-publication-metadata", "value": dated[date]}, " ".join(parser.title)[:300]
    conflict = {"kind": "conflicting-publication-metadata"} if len(dated) > 1 and (parser.explicit_dates or parser.article_dates) else {}
    return "", conflict, " ".join(parser.title)[:300]


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


def index_publication(candidate, hosts):
    """Only a date on the exact link in an actually read issuer index is proof."""
    proof = candidate.get("publicationProof") or {}
    source_url = candidate.get("discoveredFrom", "")
    digest = candidate.get("parentDocumentHash", "")
    date = candidate.get("publishedAt", "")
    if (candidate.get("discovery") == "official-page-link" and _host(source_url) in hosts
            and proof.get("kind") == "official-index-label" and proof.get("sourceUrl") == source_url
            and re.fullmatch(r"[a-f0-9]{64}", digest) and proof.get("sourceDocumentHash") == digest
            and date == _date(proof.get("value")) and parse_datetime(date)):
        return date, proof
    return "", {}


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

    def extract(self, target, candidate, tasks, document, focus, timeout):
        """Re-read the exact original revision with focused passage selection."""
        digest = document.get("sourceDocumentHash", "")
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("extraction-requires-read-document")
        return self.read(target, candidate, tasks, timeout, focus=focus, expected_hash=digest)

    def read(self, target, candidate, tasks, timeout, *, focus=None, expected_hash=""):
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
        if expected_hash and document_hash != expected_hash:
            raise ValueError("document-revision-changed")
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
            if linked_pdf or source_bound or issuer_redirect:
                date, date_proof = index_publication(candidate, hosts)
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
            if not date and not date_proof and source_bound and url == candidate["url"]:
                date, date_proof = index_publication(candidate, hosts)
            title = page_title or title
            blocks = question_document_blocks(markup, include_context=bool(focus))
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
        selection = [{"question": " ".join(focus), "queryTerms": focus}] if focus else tasks
        passages = select_question_passages(blocks, selection, 14000, max_block_chars=14000 if focus else 12000)
        for passage in passages:
            matches = [row["page"] for row in pages if row["text"] == passage["quote"]]
            if matches:
                passage["page"] = matches[0]
        document = {"url": url, "title": title[:300], "documentType": document_type,
            "sourceDocumentHash": document_hash, "observedAt": now, "publishedAt": date,
            "publicationProof": date_proof, "passages": passages, **page_info,
            "issuerVerification": "registered-issuer-source" if source_bound else "official-issuer-redirect" if issuer_redirect else "official-linked-document" if linked_pdf else "unverified-source",
            "readScope": "question-selected-passages", "redirects": receipt.get("redirects", [])}
        if focus:
            document["extraction"] = {"version": EXTRACTION_VERSION, "focus": list(focus),
                "originalDocumentHash": expected_hash, "scope": "selected-passages-with-nearby-context"}
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
            "officialDocumentQuality": "body", "documentTextVersion": EXTRACTION_VERSION if focus else VERSION,
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
        if focus:
            raw["focusedExtraction"] = document["extraction"]
        result["evidence"] = ResearchEvidence(
            evidence_id="research:" + symbol + ":explored:" + hashlib.sha256((url + document_hash + text).encode()).hexdigest()[:24],
            symbol=symbol, kind="issuer-ir", source=source.issuer_name + " Investor Relations",
            title=title[:300], summary=text[:1000], url=url, observed_at=now, published_at=date,
            raw_payload=raw)
        result["status"] = "document-collected"
        return result
