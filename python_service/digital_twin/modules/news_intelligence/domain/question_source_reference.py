"""Exact source-owner pointers for research; never a substitute for a full filing."""
from copy import deepcopy
import hashlib
import json
import re
from urllib.parse import urlsplit
from digital_twin.modules.market_data.contracts import parse_datetime
from .disclosure_quality import normalize_official_document_text
from .financial_reporting import financial_report_contract_assessment


def question_source_reference(item):
    raw = item.raw_payload or {}
    report = raw.get("financialReport") or {}
    contract = report.get("reportContract") or {}
    if (item.kind != "financial-fact" or raw.get("evidenceLifecycleState") != "active"
            or not financial_report_contract_assessment(report, raw.get("frequency")).get("eligible")
            or not contract.get("observationId") or contract["observationId"] != raw.get("reportObservationId")
            or item.evidence_id != "research:" + item.symbol + ":financial-report:" + contract["observationId"]
            or not raw.get("sourceReferences")
            or any(ref.get("subjectKey") != item.symbol for ref in raw["sourceReferences"])):
        return {}
    return deepcopy({"version": "question-source-reference-v1", "evidenceId": item.evidence_id,
        "sourceKind": "structured-report", "reportObservationId": contract["observationId"],
        "periodEnd": raw["periodEnd"], "publishedAt": item.published_at,
        "sourceRevision": raw.get("sourceRevision", ""),
        "limitation": "출처에 기록된 재무 수치입니다. 공시 전문·주석·정정 이력은 포함하지 않습니다."})


def issuer_document_reference(item):
    """The source tool's issuer/date/content proof remains mandatory after storage."""
    raw = item.raw_payload or {}
    issuer, governance = raw.get("documentIssuerIdentity") or {}, raw.get("evidenceGovernance") or {}
    text, digest = raw.get("officialDocumentText") or "", raw.get("documentHash") or ""
    passages = raw.get("documentPassages") or []
    published, observed = parse_datetime(item.published_at), parse_datetime(item.observed_at)
    hosts = {urlsplit(url).hostname for url in issuer.get("registryUrls") or []}
    verification = issuer.get("verification")
    source_bound = (urlsplit(item.url).hostname in hosts if verification == "registered-issuer-source" else
        urlsplit(issuer.get("requestedUrl", "")).hostname in hosts and bool(issuer.get("redirects"))
        if verification == "official-issuer-redirect" else
        urlsplit(issuer.get("discoveredFrom", "")).hostname in hosts
        and bool(re.fullmatch(r"[a-f0-9]{64}", issuer.get("parentDocumentHash", ""))))
    expected_id = "research:" + item.symbol + ":explored:" + hashlib.sha256((item.url + digest + text).encode()).hexdigest()[:24]
    if (item.kind != "issuer-ir" or item.evidence_id != expected_id or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or not raw.get("researchTaskIds") or not raw.get("documentVerified") or not raw.get("analysisReady")
            or raw.get("officialDocumentState") != "document-verified" or raw.get("evidenceLifecycleState") != "active"
            or issuer.get("symbol") != item.symbol or issuer.get("sourceUrl") != item.url
            or issuer.get("verification") not in {"registered-issuer-source", "official-issuer-redirect", "official-linked-document"}
            or not hosts or not source_bound or urlsplit(item.url).scheme != "https"
            or governance.get("verificationStatus") != "verified-primary" or governance.get("sourceTrustState") != "trusted"
            or governance.get("validationState") != "ready" or governance.get("supersededByEvidenceId")
            or governance.get("conflictingEvidenceIds")
            or set((raw.get("promptEvidenceAdmission") or {}).get("reasonCodes") or []) - {"evidence-stale"}
            or not published or not observed or published > observed
            or not raw.get("publicationProof") or not 1 <= len(passages) <= 8):
        return {}
    if any(not isinstance(row, dict) or not isinstance(row.get("quote"), str)
           or hashlib.sha256(row["quote"].encode()).hexdigest() != row.get("passageHash") for row in passages):
        return {}
    joined = "\n\n".join(row["quote"] for row in passages)
    if not text or normalize_official_document_text(joined) != normalize_official_document_text(text):
        return {}
    value = {"version": "research-document-reference-v1", "purpose": "dated-issuer-document",
        "evidenceId": item.evidence_id, "symbol": item.symbol, "sourceUrl": item.url,
        "periodEnd": "", "publishedAt": item.published_at, "observedAt": item.observed_at,
        "documentHash": digest, "sourceDocumentHash": raw.get("sourceDocumentHash", ""),
        "passageHashes": [row["passageHash"] for row in passages], "text": text,
        "passageTextHash": hashlib.sha256(text.encode()).hexdigest(), "sourceNormalization": "disclosure-document-quality-v3",
        "limitation": "발행일과 기업 출처를 확인한 원문 발췌입니다. 보고 기간은 본문에서 확인해야 하며 현재 주가의 원인을 증명하지 않습니다."}
    return deepcopy(value) if len(json.dumps(value, ensure_ascii=False).encode()) <= 16000 else {}
