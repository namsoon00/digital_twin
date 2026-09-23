"""Provider-neutral company-event observations with immutable source lineage.

News articles, filings and corporate actions describe different documents but
may refer to the same business event.  This contract keeps document identity,
event time, lifecycle and correction semantics separate.  It deliberately
does not infer an investment action or invent a correction link that the
provider did not supply.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Dict, Iterable, Mapping


COMPANY_EVENT_CONTRACT_VERSION = "company-event-observation-v1"
COMPANY_EVENT_KINDS = frozenset({"news", "disclosure", "filing", "corporate-action"})
COMPANY_EVENT_LIFECYCLE_STATES = frozenset({
    "announced", "upcoming", "active", "completed", "cancelled", "unknown",
})
COMPANY_EVENT_REVISION_STATES = frozenset({
    "original", "corrected", "restated", "withdrawn", "unknown",
})


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _mapping(value: object) -> Dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first(source: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if value not in (None, "", [], {}):
            return _text(value)
    return ""


def _date_value(value: object) -> str:
    text = _text(value)
    if re.fullmatch(r"20\d{6}", text):
        return text[:4] + "-" + text[4:6] + "-" + text[6:8]
    return text


def _source_references(values: Iterable[object]) -> list:
    rows = {}
    for value in values or []:
        row = _mapping(value)
        dataset_id = _text(row.get("datasetId"))
        revision_id = _text(row.get("revisionId"))
        payload_hash = _text(row.get("payloadHash"))
        if not dataset_id or not revision_id or not payload_hash:
            continue
        normalized = {
            key: row.get(key)
            for key in (
                "contractVersion", "datasetId", "providerId", "subjectKey",
                "revisionId", "providerRevision", "payloadHash",
                "sourceSchemaVersion", "sourceAsOf", "availability",
            )
            if row.get(key) not in (None, "")
        }
        rows[(dataset_id, revision_id)] = normalized
    return [rows[key] for key in sorted(rows)]


def source_reference_from_fact_row(row: Mapping[str, object]) -> Dict[str, object]:
    """Build the exact external source reference already assigned by storage."""

    source = _mapping(row)
    dataset_id = _text(source.get("datasetId"))
    revision_id = _text(source.get("revisionId"))
    payload_hash = _text(source.get("payloadHash"))
    if not dataset_id or not revision_id or not payload_hash:
        return {}
    return {
        "contractVersion": "external-source-reference-v1",
        "datasetId": dataset_id,
        "providerId": _text(source.get("providerId")),
        "subjectKey": _text(source.get("subjectKey")),
        "revisionId": revision_id,
        "providerRevision": _text(source.get("sourceRevision")),
        "payloadHash": payload_hash,
        "sourceSchemaVersion": _text(source.get("sourceSchemaVersion")),
        "sourceAsOf": _text(source.get("sourceAsOf")),
        "availability": _text(source.get("availability")) or "unknown",
    }


def _revision_state(payload: Mapping[str, object], title: str, document_type: str) -> str:
    explicit = _first(payload, "eventRevisionState", "revisionState", "correctionState").lower()
    aliases = {
        "amended": "corrected", "amendment": "corrected", "correction": "corrected",
        "revised": "corrected", "corrected": "corrected", "restated": "restated",
        "withdrawn": "withdrawn", "retracted": "withdrawn", "cancelled": "withdrawn",
        "original": "original",
    }
    if explicit in aliases:
        return aliases[explicit]
    corpus = " ".join([title, document_type, _first(payload, "remarks", "remark")]).casefold()
    if any(marker in corpus for marker in ("철회", "취소", "withdrawn", "retracted")):
        return "withdrawn"
    if any(marker in corpus for marker in ("재작성", "재공시", "restated", "restatement")):
        return "restated"
    if document_type.upper().endswith("/A") or any(
        marker in corpus for marker in ("기재정정", "정정공시", "정정", "amended", "amendment", "correction")
    ):
        return "corrected"
    return "original"


def _lifecycle_state(payload: Mapping[str, object], revision_state: str) -> str:
    if revision_state == "withdrawn":
        return "cancelled"
    explicit = _first(payload, "eventLifecycleState", "lifecycleState").lower()
    aliases = {
        "scheduled": "upcoming", "pending": "upcoming", "open": "active",
        "closed": "completed", "done": "completed", "expired": "completed",
        "withdrawn": "cancelled", "retracted": "cancelled",
    }
    explicit = aliases.get(explicit, explicit)
    return explicit if explicit in COMPANY_EVENT_LIFECYCLE_STATES else "announced"


def _normalized_title(value: object) -> str:
    text = _text(value).casefold()
    text = re.sub(r"\b(amended|amendment|correction|corrected)\b", " ", text)
    text = re.sub(r"(?:기재)?정정(?:공시)?", " ", text)
    return re.sub(r"[^0-9a-z가-힣]+", " ", text).strip()


def _event_identity(
    symbol: str,
    kind: str,
    payload: Mapping[str, object],
    title: str,
    event_type: str,
    effective_from: str,
    reporting_period: str,
) -> str:
    explicit = _first(
        payload,
        "canonicalEventId", "eventEpisodeId", "eventId", "storyClusterId",
        "receiptNo", "receipt_no", "accessionNumber",
    )
    if explicit:
        return explicit
    material = {
        "version": COMPANY_EVENT_CONTRACT_VERSION,
        "symbol": symbol,
        "kind": kind,
        "eventType": event_type,
        "title": _normalized_title(title),
        "effectiveFrom": effective_from,
        "reportingPeriod": reporting_period,
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "company-event:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def company_event_contract(
    *,
    symbol: object,
    kind: object,
    payload: Mapping[str, object] = None,
    title: object = "",
    published_at: object = "",
    source_references: Iterable[object] = (),
) -> Dict[str, object]:
    """Normalize one company event without deciding whether it is investable."""

    source = _mapping(payload)
    normalized_symbol = _text(symbol or source.get("symbol")).upper()
    normalized_kind = _text(kind or source.get("kind")).lower() or "news"
    if normalized_kind in {"sec-filing", "sec_filing"}:
        normalized_kind = "filing"
    if normalized_kind not in COMPANY_EVENT_KINDS:
        normalized_kind = "corporate-action" if source.get("eventId") else "news"
    normalized_title = _text(title or source.get("title") or source.get("reportName") or source.get("form"))
    document_type = _first(source, "officialDocumentType", "form", "reportName", "report_name")
    # Corporate-action research evidence uses a broad internal eventType
    # (capital_policy), while the source keeps the concrete action in
    # corporateActionType.  Preserve the concrete type in this source-facing
    # contract so read-model and research-evidence routes identify it equally.
    event_type = _first(source, "corporateActionType", "eventType") or "general"
    category = _first(source, "disclosureCategory", "eventCategory", "category")
    published = _date_value(
        published_at
        or _first(source, "publishedAt", "filingDate", "receiptDate", "receipt_date")
    )
    announced = _date_value(_first(source, "announcedAt", "announcementDate")) or published
    effective_from = _date_value(_first(
        source,
        "effectiveFrom", "exerciseStartDate", "releaseDate", "issueDate", "recordDate", "listingDate",
    ))
    effective_to = _date_value(_first(
        source,
        "effectiveTo", "exerciseEndDate", "cashPaymentDate", "stockDeliveryDate",
    ))
    reporting_period = _date_value(_first(source, "reportingPeriod", "reportDate", "fiscalDateEnding"))
    revision_state = _revision_state(source, normalized_title, document_type)
    lifecycle_state = _lifecycle_state(source, revision_state)
    references = _source_references([
        *(source.get("sourceReferences") if isinstance(source.get("sourceReferences"), list) else []),
        *list(source_references or []),
    ])
    event_id = _event_identity(
        normalized_symbol,
        normalized_kind,
        source,
        normalized_title,
        event_type,
        effective_from,
        reporting_period,
    )
    source_document_id = _first(
        source,
        "receiptNo", "receipt_no", "accessionNumber", "articleSourceRevision", "documentIdentity",
    )
    corrects_document_id = _first(
        source,
        "correctsSourceDocumentId", "originalReceiptNo", "originalAccessionNumber", "amendsDocumentId",
    )
    material = {
        "version": COMPANY_EVENT_CONTRACT_VERSION,
        "eventId": event_id,
        "symbol": normalized_symbol,
        "kind": normalized_kind,
        "eventType": event_type,
        "category": category,
        "title": normalized_title,
        "announcedAt": announced,
        "publishedAt": published,
        "effectiveFrom": effective_from,
        "effectiveTo": effective_to,
        "recordDate": _date_value(_first(source, "recordDate")),
        "reportingPeriod": reporting_period,
        "lifecycleState": lifecycle_state,
        "revisionState": revision_state,
        "sourceDocumentId": source_document_id,
        "correctsSourceDocumentId": corrects_document_id,
        "sourceReferences": references,
    }
    digest = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return {**material, "observationId": "company-event-observation:" + digest}


def bind_company_event_contract(
    payload: Mapping[str, object],
    *,
    symbol: object,
    kind: object,
    title: object = "",
    published_at: object = "",
    source_references: Iterable[object] = (),
) -> Dict[str, object]:
    result = dict(payload or {})
    contract = company_event_contract(
        symbol=symbol,
        kind=kind,
        payload=result,
        title=title,
        published_at=published_at,
        source_references=source_references,
    )
    result["companyEventContract"] = contract
    result["sourceReferences"] = list(contract["sourceReferences"])
    result["eventLifecycleState"] = contract["lifecycleState"]
    result["eventRevisionState"] = contract["revisionState"]
    return result


__all__ = [
    "COMPANY_EVENT_CONTRACT_VERSION",
    "bind_company_event_contract",
    "company_event_contract",
    "source_reference_from_fact_row",
]
