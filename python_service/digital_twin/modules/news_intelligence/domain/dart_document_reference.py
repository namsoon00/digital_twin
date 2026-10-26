"""Dated DART excerpts cross the graph boundary only with original source proof."""
from copy import deepcopy
import hashlib
import json
import re

from digital_twin.modules.market_data.contracts import parse_datetime
from .disclosure_quality import normalize_official_document_text
from .dart_question_filings import period_end
from .historical_document_admission import current_or_dated_source, allowed_reference_reasons
from .dart_filing_history import verified_history


def dart_document_reference(item):
    raw = item.raw_payload or {}
    issuer = raw.get("documentIssuerIdentity") or {}
    governance = raw.get("evidenceGovernance") or {}
    receipt = str(raw.get("receiptNo") or "")
    period = (raw.get("reportingPeriodMetadata") or {}).get("value", "")
    url = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt
    text = raw.get("officialDocumentText") or ""
    passages = raw.get("documentPassages") or []
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    if (item.kind != "disclosure" or not re.fullmatch(r"\d{6}", item.symbol)
            or not re.fullmatch(r"\d{14}", receipt)
            or not item.evidence_id.startswith("research:"+item.symbol+":dart-question:"+receipt+":")
            or issuer.get("symbol") != item.symbol or issuer.get("receiptNo") != receipt
            or not re.fullmatch(r"\d{8}", str(issuer.get("corpCode", "")))
            or issuer.get("sourceUrl") != url or item.url != url
            or issuer.get("verification") != "opendart-discovered-issuer-document"
            or period != period_end(raw.get("reportName")) or raw.get("reportPeriodEnd") != period or not period
            or raw.get("documentTextVersion") != "dart-question-original-v1"
            or not raw.get("researchTaskIds") or not raw.get("documentVerified") or not raw.get("analysisReady")
            or raw.get("officialDocumentState") != "document-verified"
            or not current_or_dated_source(raw)
            or governance.get("sourceTrustState") != "trusted" or governance.get("validationState") != "ready"
            or governance.get("supersededByEvidenceId") or governance.get("conflictingEvidenceIds")
            or raw.get("evidenceLifecycleState", item.lifecycle_state) != "active"
            or set((raw.get("promptEvidenceAdmission") or {}).get("reasonCodes", [])) - allowed_reference_reasons(raw)
            or not text or digest(text) != raw.get("documentHash")
            or not re.fullmatch(r"[a-f0-9]{64}", str(raw.get("sourceDocumentHash", "")))
            or not isinstance(passages, list) or not 1 <= len(passages) <= 8):
        return {}
    quotes = []
    for passage in passages:
        quote = passage.get("quote") if isinstance(passage, dict) else None
        if not isinstance(quote, str) or not quote or digest(quote) != passage.get("passageHash"):
            return {}
        quotes.append(quote)
    joined = "\n\n".join(quotes)
    if normalize_official_document_text(joined) != normalize_official_document_text(text):
        return {}
    clocks = [parse_datetime(value) for value in (period, item.published_at, item.observed_at)]
    if not all(clocks) or not clocks[0] <= clocks[1] <= clocks[2]:
        return {}
    value = {"version": "research-document-reference-v1", "purpose": "historical-period-comparison",
             "evidenceId": item.evidence_id, "symbol": item.symbol, "sourceUrl": url, "periodEnd": period,
             "publishedAt": item.published_at, "observedAt": item.observed_at,
             "documentHash": raw["documentHash"], "sourceDocumentHash": raw["sourceDocumentHash"],
             "passageHashes": [row["passageHash"] for row in passages], "text": joined,
             "passageTextHash": digest(joined), "sourceNormalization": "disclosure-document-quality-v3",
             "limitation": "해당 보고 기간의 공식 원문 발췌입니다. 제목의 보고 기간은 표의 단위·연결 범위·누계/단일 기간을 확인한 결과가 아니며 현재 주가의 원인을 증명하지 않습니다."}
    history = verified_history(raw.get("filingHistory"), item.symbol, issuer["corpCode"])
    if history:
        value["filingHistory"] = history
        if len(json.dumps(value, ensure_ascii=False).encode()) > 16000:
            value.pop("filingHistory")
            value["limitation"] += " 제출 이력은 입력 예산으로 제외되어 이 근거에서 확인할 수 없습니다."
    return deepcopy(value) if len(json.dumps(value, ensure_ascii=False).encode()) <= 16000 else {}
