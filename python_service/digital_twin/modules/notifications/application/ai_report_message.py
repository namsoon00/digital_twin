"""Frozen summary of an explicitly requested report; never regenerate an opinion."""
import html
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from digital_twin.modules.ai_orchestration.contracts import verify_portfolio_report, portfolio_report_digest

VERSION = "ai-report-summary-v1"


def date_label(value):
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not moment.tzinfo:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ZoneInfo("Asia/Seoul")).strftime("%m/%d %H:%M KST")


def report_summary(report):
    if not verify_portfolio_report(report):
        raise ValueError("보고서 내용 검증에 실패했습니다.")
    coverage = report["coverage"]
    lines = ["📑 보유·관심 종목 AI 보고서", "",
        "보고서 생성 " + date_label(report["createdAt"]),
        "계좌 자료 기준 " + date_label(report["snapshotAt"]),
        f"보유 {coverage['holdings']}개 · 관심 {coverage['watchlist']}개",
        f"검토된 과거 AI 관찰 {coverage['withOpinion']}종목 · 질문 답변 {coverage['reviewedAnswers']}건", "",
        "저장된 분석·자료를 모았습니다. 새 AI 판단을 내린 시각이 아니며, 아래 의견은 표시된 당시의 설명입니다."]
    included = 0
    for subject in report["subjects"]:
        block = []
        if subject.get("answers"):
            answer = subject["answers"][0]
            label = "부분 답변" if answer["answerStatus"] == "partial" else "검토된 답변"
            text = answer.get("answer") or " ".join(p["text"] for p in answer.get("answerParts", {}).get("confirmed", []))
            block = [f"{subject['name']} ({subject['symbol']}) · {label} · {date_label(answer['at'])}",
                answer["question"], text]
            if answer.get("missingRequirements"):
                block.append("아직 확인하지 못한 항목: " + " / ".join(answer["missingRequirements"]))
        elif subject.get("opinion"):
            opinion = subject["opinion"]
            block = [f"{subject['name']} ({subject['symbol']}) · 과거 AI 관찰 · {date_label(opinion['at'])}",
                     opinion["summary"], "반대 근거·한계: " + opinion.get("counterEvidence", "")]
        # Fit complete qualified claims; do not cut a caveat or partial sentence.
        if block and included < 4 and len("\n".join(lines + block)) <= 2800:
            lines += ["", *block]
            included += 1
    if not included:
        lines += ["", "요약에 실을 수 있는 검토된 의견·답변이 없거나 길이 제한을 넘었습니다. 종목별 기업 자료와 확인 한계는 전문에 담았습니다."]
    lines += ["", f"요약 {included}종목 · 전체 {coverage['subjects']}종목은 웹 보고서에서 확인",
              "Orbit Alpha → 중앙 AI → AI 보고서", "보고서 번호 " + report["reportId"][:12],
              "부분 답변은 질문 해결이나 매매 판단을 뜻하지 않습니다."]
    value = {"version": VERSION, "reportId": report["reportId"], "accountId": report["accountId"],
        "reportHash": report["contentHash"], "createdAt": report["createdAt"], "body": "\n".join(lines),
        "requestedByOwner": True}
    value["summaryHash"] = portfolio_report_digest(value)
    return value


def render_ai_report(value):
    if (value.get("version") != VERSION or value.get("requestedByOwner") is not True
            or portfolio_report_digest({k: v for k, v in value.items() if k != "summaryHash"}) != value.get("summaryHash")):
        raise ValueError("요청 보고서의 저장된 요약이 일치하지 않습니다.")
    return html.escape(value["body"], quote=False)
