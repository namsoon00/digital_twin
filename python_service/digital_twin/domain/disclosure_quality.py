import html
import re
from dataclasses import dataclass, field
from typing import Dict, List


DISCLOSURE_DOCUMENT_QUALITY_VERSION = "disclosure-document-quality-v1"
ERROR_PATTERNS = (
    re.compile(r"\b014\s*파일이\s*존재하지\s*않습니다", re.IGNORECASE),
    re.compile(r"(?:status|error)\s*[:=]\s*(?:013|014|error)", re.IGNORECASE),
    re.compile(r"document\s+(?:is\s+)?not\s+found", re.IGNORECASE),
)
CSS_BLOCK_RE = re.compile(
    r"(?:^|\s)(?:\.?[a-z][a-z0-9_.:#-]*|#[a-z0-9_-]+)\s*\{[^{}]{0,4000}\}",
    re.IGNORECASE,
)


def normalize_official_document_text(value: object, limit: int = 20000) -> str:
    text = html.unescape(str(value or "")).replace("\x00", " ")
    text = re.sub(r"<\s*(?:script|style|noscript)[^>]*>.*?<\s*/\s*(?:script|style|noscript)\s*>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = CSS_BLOCK_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    bounded = text[:max(500, min(50000, int(limit or 20000)))]
    if any(pattern.search(bounded) for pattern in ERROR_PATTERNS):
        return ""
    return bounded


@dataclass(frozen=True)
class DisclosureDocumentQuality:
    state: str
    metadata_verified: bool
    document_verified: bool
    analysis_ready: bool
    data_state: str
    validation_state: str
    document_text: str
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": DISCLOSURE_DOCUMENT_QUALITY_VERSION,
            "state": self.state,
            "metadataVerified": self.metadata_verified,
            "documentVerified": self.document_verified,
            "analysisReady": self.analysis_ready,
            "dataState": self.data_state,
            "validationState": self.validation_state,
            "documentCharCount": len(self.document_text),
            "issues": list(self.issues),
        }


def assess_disclosure_document(
    document_text: object,
    document_quality: object = "",
    *,
    metadata_verified: bool = True,
    minimum_chars: int = 120,
) -> DisclosureDocumentQuality:
    raw = str(document_text or "")
    normalized = normalize_official_document_text(raw)
    quality = str(document_quality or "").strip().lower()
    issues: List[str] = []
    explicit_error = bool(raw and not normalized and any(pattern.search(raw) for pattern in ERROR_PATTERNS))
    if explicit_error:
        issues.append("official-document-error-response")
    elif raw and len(normalized) < max(80, int(minimum_chars or 120)):
        issues.append("official-document-too-short")
    elif not raw:
        issues.append("official-document-missing")
    if quality in {"unavailable", "insufficient", "error", "failed"}:
        issues.append("official-document-provider-failure")
    document_verified = bool(
        len(normalized) >= max(80, int(minimum_chars or 120))
        and not explicit_error
        and quality not in {"unavailable", "insufficient", "error", "failed", "deferred-contact"}
    )
    if document_verified:
        state = "document-verified"
        data_state = "sufficient"
        validation_state = "ready"
    elif explicit_error or (raw and quality in {"unavailable", "insufficient", "error", "failed"}):
        state = "document-rejected"
        data_state = "insufficient"
        validation_state = "blocked"
    else:
        state = "metadata-only"
        data_state = "partial" if metadata_verified else "insufficient"
        validation_state = "conditional" if metadata_verified else "blocked"
    return DisclosureDocumentQuality(
        state,
        bool(metadata_verified),
        document_verified,
        document_verified,
        data_state,
        validation_state,
        normalized,
        list(dict.fromkeys(issues)),
    )


def apply_disclosure_document_quality(
    payload: Dict[str, object],
    *,
    metadata_verified: bool = True,
) -> Dict[str, object]:
    result = dict(payload or {})
    assessment = assess_disclosure_document(
        result.get("officialDocumentText"),
        result.get("officialDocumentQuality"),
        metadata_verified=metadata_verified,
    )
    result["officialDocumentText"] = assessment.document_text
    result["officialDocumentPreview"] = assessment.document_text[:700]
    result["disclosureDocumentQuality"] = assessment.to_dict()
    result["officialDocumentState"] = assessment.state
    result["metadataVerified"] = assessment.metadata_verified
    result["documentVerified"] = assessment.document_verified
    result["analysisReady"] = assessment.analysis_ready
    result["dataState"] = assessment.data_state
    result["validationState"] = assessment.validation_state
    return result
