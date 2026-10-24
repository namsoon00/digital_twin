"""Receipt-backed collection diagnostics, never evidence of an unpublished fact."""
from urllib.parse import urlsplit

VERSION = "source-recovery-v1"
EXTRACTION_VERSION = "question-focused-extraction-v1"

# These are operational next steps. They neither relax admission nor declare an
# answer complete. An empty search cannot establish that a report is unpublished.
CAUSES = {
    "access-denied": ("접근 거부", "search", "같은 자료를 제공하는 다른 공식 출처를 찾습니다."),
    "transport-failed": ("원문 요청 실패", "search", "다른 공식 원문 경로를 확인합니다."),
    "rate-limited": ("요청 제한", "wait", "제공처의 재시도 가능 시각을 기다립니다."),
    "configuration-required": ("접근 설정 필요", "configuration", "제공처 접근 설정을 확인합니다."),
    "unsupported-source": ("수집 경로 미지원", "search", "지원되는 공식 IR 원문을 찾습니다."),
    "extraction-unavailable": ("본문 추출 기능 부족", "search", "같은 보고서의 읽을 수 있는 HTML 원문을 찾습니다."),
    "passages-missing": ("질문에 필요한 본문 미확보", "extract", "읽은 원문에서 부족한 항목과 표 주변 설명을 집중 추출합니다."),
    "date-unverified": ("발행일 미확인", "read", "해당 문서의 발행일이 표시된 공식 원문 또는 색인을 확인합니다."),
    "issuer-unverified": ("발행사·출처 미확인", "search", "대상 기업이 확인되는 공식 원문을 찾습니다."),
    "source-changed": ("원문 버전 변경", "wait", "새 원문을 별도 조사에서 다시 읽고 검증합니다."),
    "search-empty": ("검색 후보 없음", "search", "기간·항목을 구체화하거나 다른 공식 출처를 검색합니다."),
    "operation-repeated": ("같은 작업 반복", "finish", "다른 경로가 없으면 재개 조건을 남기고 이번 탐색을 마칩니다."),
    "execution-deferred": ("실행 순서·예산 대기", "wait", "실행이 가능해지면 저장된 단계에서 이어갑니다."),
    "budget-exhausted": ("탐색 한도 도달", "finish", "확보한 자료를 검토하고 남은 항목을 다음 계획에 연결합니다."),
    "unclassified-failure": ("원인 미분류 실행 실패", "inspect", "실행 기록을 확인하며 자료 부재로 해석하지 않습니다."),
}


def failure_cause(code):
    if code == "issuer-identity-unavailable":
        return "issuer-unverified"
    if code in {"invalid-document-archive", "document-encoding-unavailable", "document-text-unavailable"}:
        return "extraction-unavailable"
    if code in {"dart-document-014", "dart-document-800", "metadata-unavailable"}:
        return "transport-failed"
    if code == "dart-document-020":
        return "rate-limited"
    if code in {"document-access-blocked", "official-document-unavailable", "document-http-401", "document-http-403"}:
        return "access-denied"
    if code in {"document-http-429", "deferred", "cooldown"}:
        return "rate-limited"
    if code.startswith("document-http-") or code in {"document-unavailable", "TimeoutError", "URLError"}:
        return "transport-failed"
    if code in {"configuration-required", "unsupported-document-source"}:
        return "configuration-required" if code == "configuration-required" else "unsupported-source"
    if code in {"pdf-extraction-unavailable", "pdf-text-unavailable-ocr-required", "unsupported-document-format", "unsupported-document-charset"}:
        return "extraction-unavailable"
    if code in {"question-passages-insufficient", "no-matching-passages"}:
        return "passages-missing"
    if code in {"publication-date-unverified", "publication-date-invalid"}:
        return "date-unverified"
    if code in {"issuer-source-unverified", "document-subject-unconfirmed", "invalid-document-identity"}:
        return "issuer-unverified"
    if code == "document-revision-changed":
        return "source-changed"
    if code == "search-no-results":
        return "search-empty"
    if code == "duplicate-exploration-operation":
        return "operation-repeated"
    if code in {"execution-deferred", "LocalAICapacityUnavailable", "AIControlBudgetWait", "AIExecutionDeferred"}:
        return "execution-deferred"
    if "budget" in code:
        return "budget-exhausted"
    return "unclassified-failure"


def source_recovery_summary(audit, provider_statuses=()):
    """Bounded, structured memory of observed failures and actual tool progress."""
    issues = []

    def add(code, step=0, candidate="", url=""):
        cause = failure_cause(str(code))
        label, action, explanation = CAUSES[cause]
        try:
            host = (urlsplit(str(url)).hostname or "")[:160]
        except ValueError:
            host = ""
        operation = action if action in {"search", "read", "extract", "finish"} else "finish"
        plan_action = "blocked" if action in {"configuration", "inspect"} else "wait" if action in {"wait", "finish"} else "research"
        item = {"cause": cause, "label": label, "code": str(code)[:120], "step": step,
                "candidateId": candidate, "host": host, "nextAction": operation,
                "planningAction": plan_action, "explanation": explanation}
        if item not in issues:
            issues.append(item)

    for row in provider_statuses:
        code = str(row.get("reasonCode") or row.get("status") or "")
        if failure_cause(code) != "unclassified-failure":
            add(code, url=row.get("url", ""))
        elif row.get("ok") is False or code in {"error", "failed"}:
            add(str(row.get("errorKind") or code or "provider-error"), url=row.get("url", ""))
    for row in audit.get("steps", []):
        decision, result = row.get("decision") or {}, row.get("result") or {}
        document = result.get("document") or {}
        codes = list(document.get("admissionBlockers") or [])
        if row.get("errorKind"):
            codes.append(row["errorKind"])
        if row.get("status") == "search-no-results":
            codes.append("search-no-results")
        for code in codes:
            add(code, row.get("step", 0), decision.get("candidateId", ""),
                document.get("url") or row.get("sourceUrl", ""))
    if (audit.get("stopReason") == "execution-deferred" or audit.get("complete") and
            audit.get("stopReason") in {"step-budget-exhausted", "time-budget-exhausted", "context-budget-exhausted", "tool-budget-exhausted"}):
        add(audit["stopReason"])
    measured = bool(audit.get("recoveryVersion"))
    progress = {key: audit[key] for key in ("searchCount", "readCount", "extractionCount") if key in audit}
    if "evidenceIds" in audit:
        progress["collectedEvidenceCount"] = len(set(audit["evidenceIds"]))
    if measured:
        progress["newPassageCount"] = sum((row.get("progress") or {}).get("newPassageCount", 0) for row in audit.get("steps", []))
    progress.update(measured=measured, answerCompletion="requires-independent-review")
    return {"version": VERSION, "authority": "collection-diagnostics-only",
            "issues": issues[-8:], "omittedIssueCount": max(0, len(issues) - 8),
            "causeCounts": {cause: sum(row["cause"] == cause for row in issues) for cause in sorted({row["cause"] for row in issues})},
            "progress": progress}
