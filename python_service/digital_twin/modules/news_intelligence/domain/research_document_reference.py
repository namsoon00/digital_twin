"""Dated original filing passages, independently of current-event admission."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from urllib.parse import urlparse
from .disclosure_quality import normalize_official_document_text
from .historical_document_admission import current_or_dated_source, allowed_reference_reasons

VERSION = "research-document-reference-v1"
MAX_BYTES = 16000


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def question_document_changed_symbols(changes):
    """Refresh original-source projections even when a change revokes their use.

    This identifies the source owner's question documents, not their admission.
    Requiring a currently valid reference here would strand retracted references
    in the graph. Unchanged provider polls never reach this change boundary.
    """
    return sorted({
        str(row.get("symbol") or "").strip().upper()
        for row in changes or []
        if isinstance(row, dict) and str(row.get("symbol") or "").strip()
        and str(row.get("evidenceId") or "").startswith(
            tuple("research:" + str(row["symbol"]).strip().upper() + ":" + suffix
                  for suffix in ("sec-question:", "dart-question:", "explored:", "financial-report:")))
    })


def research_document_reference(item):
    """Fail closed; this reference never changes the filing's event eligibility."""
    if item.kind == "disclosure" and (item.raw_payload.get("documentIssuerIdentity") or {}).get("verification") == "opendart-discovered-issuer-document":
        from .dart_document_reference import dart_document_reference
        return dart_document_reference(item)
    if item.kind == "issuer-ir":
        from .question_source_reference import issuer_document_reference
        return issuer_document_reference(item)
    raw = item.raw_payload
    governance = raw.get("evidenceGovernance") or {}
    admission = raw.get("promptEvidenceAdmission") or {}
    issuer = raw.get("documentIssuerIdentity") or {}
    passages = raw.get("documentPassages") or []
    document = raw.get("officialDocumentText") or ""
    if (item.kind != "filing" or not raw.get("researchTaskIds")
            or not raw.get("documentVerified") or not raw.get("analysisReady")
            or raw.get("officialDocumentState") != "document-verified"
            or not current_or_dated_source(raw)
            or governance.get("sourceTrustState") != "trusted"
            or governance.get("validationState") != "ready"
            or governance.get("supersededByEvidenceId") or governance.get("conflictingEvidenceIds")
            or raw.get("evidenceLifecycleState") != "active"
            or set(admission.get("reasonCodes") or []) - allowed_reference_reasons(raw)
            or issuer.get("symbol") != item.symbol or issuer.get("sourceUrl") != item.url
            or issuer.get("verification") != "sec-discovered-issuer-document"
            or urlparse(item.url).hostname not in {"sec.gov", "www.sec.gov"}
            or not document or digest(document) != raw.get("documentHash")
            or not isinstance(passages, list) or not 1 <= len(passages) <= 8):
        return {}
    exact = []
    normalized = normalize_official_document_text(document)
    for passage in passages:
        quote = passage.get("quote") if isinstance(passage, dict) else None
        if (not isinstance(quote, str) or not quote or digest(quote) != passage.get("passageHash")
                or normalize_official_document_text(quote) not in normalized):
            return {}
        exact.append({"quote": quote, "passageHash": passage["passageHash"]})
    text = "\n\n".join(row["quote"] for row in exact)
    if normalize_official_document_text(text) != normalized:
        return {}
    try:
        clocks = [datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                  for value in (raw.get("reportDate"), item.published_at, item.observed_at)]
        clocks = [value if value.tzinfo else value.replace(tzinfo=timezone.utc) for value in clocks]
    except (TypeError, ValueError):
        return {}
    if not clocks[0] <= clocks[1] <= clocks[2]:
        return {}
    value = {"version": VERSION, "purpose": "historical-period-comparison", "evidenceId": item.evidence_id,
             "symbol": item.symbol, "sourceUrl": item.url, "periodEnd": raw["reportDate"],
             "publishedAt": item.published_at, "observedAt": item.observed_at,
             "documentHash": raw["documentHash"], "sourceDocumentHash": raw.get("sourceDocumentHash", ""),
             "passageHashes": [row["passageHash"] for row in exact], "text": text,
             "passageTextHash": digest(text), "sourceNormalization": "disclosure-document-quality-v3",
             "limitation": "해당 보고 기간의 원문 발췌입니다. 최신 사건·현재 수치·현재 주가의 원인을 증명하지 않습니다."}
    from .filing_history import verified_filing_history
    history = verified_filing_history(raw.get("filingHistory"), item.symbol, str(issuer.get("cik") or "").zfill(10))
    if history:
        value["filingHistory"] = history
        if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_BYTES:
            value.pop("filingHistory")
            # Shorter than the original limitation, so the omission notice
            # cannot push a previously fitting document over the byte limit.
            value["limitation"] = "과거 원문으로 현재 사실을 입증하지 않습니다. 제출 이력은 크기 제한으로 생략됐습니다."
    return deepcopy(value) if len(json.dumps(value, ensure_ascii=False).encode()) <= MAX_BYTES else {}
