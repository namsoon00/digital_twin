"""On-demand, dated reading of existing AI work; no new investment authority."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit

from digital_twin.modules.decisions.contracts import narrative_presentation_errors
from .opinion_memory import current_question

VERSION = "ai-portfolio-report-v1"
MAX_SUBJECTS = 100
BOUNDARY = ("저장된 기업 자료와 검토된 AI 의견을 요청 시점에 모은 보고서입니다. "
            "AI 의견의 분석 시각은 각각 다르며, 보고서 생성이 새 분석이나 현재 유효성 재검증을 뜻하지 않습니다. "
            "매매 지시·목표 비중·예측 적중 판정은 포함하지 않습니다.")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def instant(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def known(value, cutoff):
    return bool(instant(value) and instant(cutoff) and instant(value) <= instant(cutoff))


def request_identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,64}", value):
        raise ValueError("요청 식별자가 올바르지 않습니다. 화면을 새로고침해 주세요.")
    return value


def safe_url(value):
    value = str(value or "")
    try:
        url = urlsplit(value)
        return value if (url.scheme in {"http", "https"} and url.hostname and not url.username
                         and not url.password and not re.search(r"[\s<>]", value)) else ""
    except ValueError:
        return ""


def reviewed_question(case, account, symbol, world, cutoff):
    if any(case.get(k) != v for k, v in (("accountId", account), ("symbol", symbol), ("worldId", world))):
        return None
    answer = current_question(case)
    if answer.get("status") != "reviewed" or not known(answer.get("at"), cutoff):
        return None
    # A legacy reviewed partial answer may predate answerParts. Preserve its
    # original scope and whole qualifications, never call it a completed answer.
    texts = [answer.get("answer", ""), *[p.get("text", "") for p in answer.get("answerParts", {}).get("confirmed", [])]]
    texts += [str(v) for v in answer.get("judgment", {}).values()]
    texts += [answer.get("judgmentImpact", {}).get("reason", "")]
    if narrative_presentation_errors("NO_ACTION", texts):
        return None
    sources = [{**{k: r[k] for k in ("factId", "title", "documentHash", "periodEnd") if k in r},
                "sourceUrl": safe_url(r.get("sourceUrl"))} for r in answer.get("sources", [])]
    cited = set(answer.get("evidenceIds", []))
    if not cited or not cited.issubset({r.get("factId") for r in sources}):
        return None
    if any(r.get("periodEnd") and not known(r["periodEnd"], cutoff) for r in sources):
        return None
    return {**{k: deepcopy(answer[k]) for k in ("caseId", "question", "answer", "answerStatus", "answerParts",
                "missingRequirements", "judgmentImpact", "judgment", "at", "taskId", "executionInputId",
                "researchReviewInputId", "evidenceIds") if k in answer}, "sources": sources,
            "authority": "dated-reviewed-research", "currentValidity": "not-reassessed"}


def build_report(account, captured_at, snapshot_at, subjects, *, request_id):
    request_identity(request_id)
    if not account or not known(snapshot_at, captured_at):
        raise ValueError("기준 시각을 확인할 수 있는 실계좌 스냅샷이 필요합니다.")
    if not subjects or len(subjects) > MAX_SUBJECTS:
        raise ValueError("보고서는 보유·관심 종목 1~100개를 대상으로 생성할 수 있습니다.")
    if len({s["symbol"] for s in subjects}) != len(subjects):
        raise ValueError("보고서에 중복 종목이 있습니다.")
    value = {"version": VERSION, "reportId": digest([VERSION, account, request_id]),
        "accountId": account, "requestId": request_id, "createdAt": captured_at,
        "snapshotAt": snapshot_at, "title": "보유·관심 종목 AI 보고서", "boundary": BOUNDARY,
        "creationMode": "saved-evidence-compilation", "newModelCalls": 0,
        "subjects": deepcopy(subjects)}
    value["coverage"] = {"subjects": len(subjects),
        "holdings": sum(s["scope"] == "holding" for s in subjects),
        "watchlist": sum(s["scope"] == "watchlist" for s in subjects),
        "withOpinion": sum(bool(s.get("opinion")) for s in subjects),
        "withReviewedAnswers": sum(bool(s.get("answers")) for s in subjects),
        "reviewedAnswers": sum(len(s.get("answers", [])) for s in subjects),
        "withCompanySources": sum(bool(s.get("companyReport", {}).get("evidence", {}).get("annualFinancials")
            or s.get("companyReport", {}).get("evidence", {}).get("recentFinancials")) for s in subjects)}
    value["contentHash"] = digest(value)
    return value


def verify_report(report):
    return (report.get("version") in {VERSION, "ai-portfolio-report-v2"} and bool(report.get("contentHash"))
            and digest({k: v for k, v in report.items() if k != "contentHash"}) == report["contentHash"])


def summary(report):
    return {k: deepcopy(report[k]) for k in ("reportId", "accountId", "createdAt", "snapshotAt", "title", "coverage", "contentHash", "version", "creationMode")}
