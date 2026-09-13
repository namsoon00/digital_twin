"""Exact document identities for bounded recovery; no title-based matching."""

import re
from urllib.parse import parse_qs, urlsplit

from .contracts import ExternalSubject, FollowupCollectionRequest


def document_recovery_request(item):
    payload = dict(getattr(item, "raw_payload", {}) or {})
    symbol = str(item.symbol or "").upper().strip()
    if payload.get("documentVerified") is True or getattr(item, "lifecycle_state", "active") in {"retracted", "rejected", "superseded"}:
        return None
    try:
        url = urlsplit(str(item.url or ""))
    except ValueError:
        return None
    if url.scheme != "https" or url.username or url.password:
        return None
    if item.kind == "disclosure":
        query = parse_qs(url.query)
        receipts = query.get("rcpNo") or []
        if url.hostname != "dart.fss.or.kr" or url.path != "/dsaf001/main.do" or len(receipts) != 1:
            return None
        identity = str(payload.get("receiptNo") or receipts[0])
        if not re.fullmatch(r"\d{14}", identity) or not re.fullmatch(r"\d{6}", symbol):
            return None
        if identity != receipts[0] or item.evidence_id != "research:" + symbol + ":dart:" + identity:
            return None
        metadata = {key: payload.get(key) for key in ["corpName", "receiptDate", "reportName", "filerName", "remarks"]}
        metadata.update(receiptNo=identity, receiptDate=payload.get("receiptDate") or item.published_at, reportName=item.title,
                        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + identity)
        watermark = {"receiptNo": identity, "metadata": metadata}
        dataset, market, currency = "opendart.document", "KR", "KRW"
    elif item.kind in {"filing", "sec-filing", "sec_filing"}:
        legacy = re.fullmatch(r"research:" + re.escape(symbol) + r":sec:(\d{10}-\d{2}-\d{6})", item.evidence_id)
        identity = str(payload.get("accessionNumber") or (legacy[1] if legacy else ""))
        match = re.fullmatch(r"/Archives/edgar/data/(\d+)/(\d{18})/(?:[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+", url.path)
        if not legacy or legacy[1] != identity or url.hostname != "www.sec.gov" or url.query or any(part in {".", ".."} for part in url.path.split("/")) or not match or match[2] != identity.replace("-", ""):
            return None
        metadata = {key: payload.get(key) for key in ["filingDate", "reportDate", "primaryDocument", "filingIndexUrl"]}
        metadata.update(accessionNumber=identity, filingDate=payload.get("filingDate") or item.published_at,
                        form=payload.get("reportName") or item.title, url=item.url)
        watermark = {"accessionNumber": identity, "cik": match[1], "companyName": payload.get("companyName") or symbol, "metadata": metadata}
        dataset, market, currency = "sec.document", "US", "USD"
    else:
        return None
    return FollowupCollectionRequest(dataset_id=dataset, partition_key=symbol + ":" + identity + ":body-v1",
        subject=ExternalSubject(symbol, symbol=symbol, name=str(payload.get("corpName") or payload.get("companyName") or symbol), market=market, currency=currency, source="document-recovery"),
        watermark=watermark, priority=35)
