"""Bounded issuer/receipt-bound DART originals, using the existing API guard."""
import hashlib
import io
import re
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlencode

from digital_twin.modules.market_data.contracts import ExternalCallDeferred
from digital_twin.modules.news_intelligence.contracts import (
    question_terms, select_question_passages, select_dart_question_filings,
    research_evidence_from_external_signals,
    required_report_periods, question_collection_scope,
)
from .external_signal_utils import dart_document_error_response
from .dart_document_passages import document_passages, VERSION as PASSAGES_VERSION

VERSION = "dart-question-original-v1"
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 12 * 1024 * 1024
MAX_EXPANDED_BYTES = 24 * 1024 * 1024


def document_members(raw):
    data = bytes(raw or b"")
    if len(data) > MAX_ARCHIVE_BYTES or not zipfile.is_zipfile(io.BytesIO(data)):
        raise ValueError("invalid-document-archive")
    result = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [row for row in archive.infolist() if row.filename.lower().endswith((".xml", ".html", ".xhtml", ".txt"))]
        if (len(members) > 12 or sum(row.file_size for row in members) > MAX_EXPANDED_BYTES
                or any(row.file_size > MAX_MEMBER_BYTES or row.flag_bits & 1 for row in members)):
            raise ValueError("document-size-budget")
        for member in members:
            content = archive.read(member)
            decoded = None
            for encoding in ("utf-8-sig", "cp949", "euc-kr"):
                try:
                    decoded = content.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if decoded is None:
                raise ValueError("document-encoding-unavailable")
            result.append((member.filename, decoded))
    if not result:
        raise ValueError("document-text-unavailable")
    return result


def collect_dart_question_documents(provider, target, signals, tasks):
    symbol = target.normalized_symbol()
    disclosure = (signals.get("dartDisclosures") or {}).get(symbol) or {}
    settings = getattr(provider, "settings", {})
    settings = settings if isinstance(settings, dict) else {}
    key = str(settings.get("opendartApiKey") or "").strip()
    corp = str(disclosure.get("corpCode") or "")
    base = {"source": "official-question-research", "symbol": symbol, "version": VERSION,
            "taskIds": [t.get("taskId", "") for t in tasks], "queryTerms": question_terms(tasks)}
    if not key:
        return [], [{**base, "ok": False, "status": "configuration-required", "count": 0}]
    if not re.fullmatch(r"\d{8}", corp):
        return [], [{**base, "ok": False, "status": "issuer-identity-unavailable", "count": 0}]
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    rows = [row for row in disclosure.get("items", []) if isinstance(row, dict)
            and str(row.get("corpCode") or row.get("corp_code") or corp) == corp]
    selected = select_dart_question_filings(rows, tasks, now)
    required = required_report_periods(tasks)
    statuses, evidence = [], []
    # Normal monitoring's short filing lookback is not a research inventory.
    # One guarded, bounded list read discovers comparable older originals.
    if len(selected) < 2:
        text = " ".join(str(t.get("question", "")) + " " + " ".join(t.get("queryTerms") or []) for t in tasks)
        years = [int(value[:4]) for value in required] or [int(y) for y in re.findall(r"(?:19|20)\d{2}", text)]
        year = datetime.now(timezone.utc).year
        begin = str(max(year-5, min([year-1, *years]))) + "0101"
        params = {"crtfc_key": key, "corp_code": corp, "bgn_de": begin, "end_de": now[:10].replace("-", ""),
                  "pblntf_ty": "A", "page_no": 1, "page_count": 100, "sort": "date", "sort_mth": "desc"}
        try:
            url = "https://opendart.fss.or.kr/api/list.json?" + urlencode(params)
            listing = provider.guarded_call("OpenDART", "question-list:"+symbol+":"+begin+":"+params["end_de"],
                                            lambda: provider.fetch_json(url))
            if str(listing.get("status")) == "000":
                admitted = [row for row in (listing.get("list") or []) if isinstance(row, dict) and row.get("corp_code") == corp]
                rows.extend(admitted[:100])
                selected = select_dart_question_filings(rows, tasks, now)
            else:
                statuses.append({**base, "ok": str(listing.get("status")) == "013", "status": "metadata-unavailable",
                                 "reasonCode": "dart-list-"+str(listing.get("status", "unknown"))})
        except ExternalCallDeferred as error:
            return [], [{**base, "ok": False, "status": "deferred", "reason": error.reason, "retryAt": error.retry_at}]
        except Exception as error:
            statuses.append({**base, "ok": False, "status": "metadata-unavailable", "errorKind": type(error).__name__})
    attempted = 0
    for filing in selected:
        attempted += 1
        receipt = filing["receiptNo"]
        try:
            url = "https://opendart.fss.or.kr/api/document.xml?" + urlencode({"crtfc_key": key, "rcept_no": receipt})
            raw = provider.guarded_call("OpenDART", "document:"+symbol+":"+receipt,
                                        lambda: provider.fetch_bytes(url, {"Accept": "application/zip,application/xml"}))
            error = dart_document_error_response(raw)
            if error:
                statuses.append({**base, "ok": False, "status": "document-unavailable",
                                 "reasonCode": "dart-document-"+error["status"], "receiptNo": receipt})
                if error["status"] in {"020", "800"}:
                    break
                continue
            members = document_members(raw)
            blocks = [block for _, markup in members for block in document_passages(markup)]
            passages = select_question_passages(blocks, tasks, 14000, max_block_chars=14000)
            # The source reference has a separate UTF-8 budget. Keep whole
            # tables/qualifications; never cut a Korean table to fit a byte cap.
            bounded, used = [], 0
            for passage in passages:
                size = len(passage["quote"].encode()) + 2
                if used + size <= 11000:
                    bounded.append(passage); used += size
            excluded_count = len(passages) - len(bounded)
            passages = bounded
            if excluded_count:
                statuses.append({**base, "ok": True, "status": "passage-budget-excluded",
                                 "receiptNo": receipt, "excludedCount": excluded_count})
            if not passages:
                statuses.append({**base, "ok": True, "status": "no-matching-passages", "receiptNo": receipt})
                continue
            document = {**filing, "documentText": "\n\n".join(p["quote"] for p in passages), "documentTextQuality": "body"}
            items = research_evidence_from_external_signals(symbol, {"dartDisclosures": {symbol:
                {**disclosure, "receiptNo": receipt, "items": [document]}}})
            digest = hashlib.sha256("\n".join(name+"\n"+markup for name, markup in members).encode()).hexdigest()
            public_url = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt
            for item in items:
                passage_hash = hashlib.sha256("|".join(p["passageHash"] for p in passages).encode()).hexdigest()[:24]
                item.evidence_id = "research:"+symbol+":dart-question:"+receipt+":"+passage_hash
                item.observed_at = now
                item.raw_payload.update(documentPassages=passages, documentTextVersion=VERSION,
                    passageSelectionVersion=PASSAGES_VERSION,
                    reportPeriodEnd=filing["reportPeriodEnd"],
                    officialDocumentScope="question-selected-passages", sourceDocumentHash=digest,
                    documentIssuerIdentity={"symbol": symbol, "corpCode": corp, "receiptNo": receipt,
                        "sourceUrl": public_url, "verification": "opendart-discovered-issuer-document"},
                    researchTaskIds=base["taskIds"], researchQueryTerms=base["queryTerms"],
                    reportingPeriodMetadata={"value": filing["reportPeriodEnd"], "source": "official-filing-title",
                        "limitation": "title-does-not-verify-consolidation-units-or-single-period"})
            evidence.extend(items)
        except ExternalCallDeferred as error:
            statuses.append({**base, "ok": False, "status": "deferred", "reason": error.reason, "retryAt": error.retry_at})
            break
        except Exception as error:
            code = str(error) if type(error) is ValueError and str(error) in {
                "invalid-document-archive", "document-size-budget", "document-encoding-unavailable", "document-text-unavailable"} else ""
            statuses.append({**base, "ok": False, "status": "document-unavailable", "errorKind": type(error).__name__,
                             "reasonCode": code, "receiptNo": receipt})
    statuses.append({**base, "ok": True, "status": "passages-collected" if evidence else "unresolved",
                     "count": len(evidence), "documentsAttempted": attempted, "metadataPageLimit": 100,
                     **({"collectionScope": question_collection_scope(tasks,
                         [row["reportPeriodEnd"] for row in selected],
                         [row.raw_payload.get("reportPeriodEnd", "") for row in evidence])} if required else {})})
    return evidence, statuses
