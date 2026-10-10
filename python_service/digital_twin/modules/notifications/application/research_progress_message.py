"""Render the frozen research event; no fresh facts, model calls or actions."""
from html import escape
from digital_twin.modules.notifications.domain.display_time import kst_timestamp, notification_times_kst
from digital_twin.modules.decisions.contracts import narrative_presentation_errors


def render_research_progress(value):
    def text(item, limit=600):
        value = str(item or "")
        if narrative_presentation_errors("NO_ACTION", [value]):
            return "표현 검토가 필요한 내용은 연구 기록에 보관했습니다."
        return escape(value[:limit], quote=False)

    lines = ["<b>🔬 연구 진행 · " + text(value["symbol"], 64) + "</b>",
             text(value["label"]), "", "<b>연구 질문</b>", text(value["question"])]
    answer = value.get("documentaryAnswer") or {}
    if answer:
        lines.extend(["", "<b>자료 검토 답변</b>", text(answer.get("text"))])
        if answer.get("missingRequirements"):
            lines.extend(["<b>아직 확인하지 못한 자료</b>", text(" · ".join(answer["missingRequirements"]))])
        for index, source in enumerate(answer.get("sources", [])[:8], 1):
            label = text(source.get("title") or source.get("evidenceId"), 100)
            url = source.get("sourceUrl") or ""
            lines.append(f'근거 {index}: <a href="{escape(url, quote=True)}">{label}</a>' if url.startswith("https://") else f"근거 {index}: {label}")
        lines.append("자료에 대한 답변 검토이며, 투자 가설의 입증 여부는 별도로 평가합니다.")
    blocker = value.get("reviewBlocker") or {}
    if blocker:
        lines.extend(["", "<b>판단이 보류된 이유</b>", text(blocker.get("reason")), text(" · ".join(blocker.get("errors") or []))])
    contract = value.get("contract", {})
    for key, label in (("mechanism", "검토 중인 설명"), ("assumption", "필요한 가정"),
                       ("alternative", "다른 가능한 설명"), ("invalidation", "가설을 재검토할 조건")):
        if contract.get(key):
            lines.extend(["", "<b>" + label + "</b>", text(contract[key])])
    if value.get("reason"):
        lines.extend(["", "<b>진행 기록 · AI 평가 포함</b>", text(value["reason"])])
    missing = contract.get("missingEvidence")
    if missing:
        lines.extend(["", "<b>더 필요한 자료</b>", text(" · ".join(missing) if isinstance(missing, list) else missing)])
    if contract.get("horizonDays"):
        lines.append("등록된 연구 기간: " + text(contract["horizonDays"], 4) + "일")
    if value.get("evidenceIds"):
        lines.append(f"연결된 근거 기록: {len(value['evidenceIds'])}건 (연구 기록에 보관)")
    if value["stage"] == "research-returned" and not answer:
        lines.append("자료 갱신을 확인했습니다. 질문의 답변과 가설의 타당성은 아직 검토 중입니다.")
    results = value.get("observations", [])
    observed, missed = results.count("direction-observed"), results.count("direction-not-observed")
    if observed or missed:
        lines.append(f"등록 지표 방향 관측: 일치 {observed}건 · 불일치 {missed}건. 가설 전체의 입증을 뜻하지 않습니다.")
    if value.get("nextCheckAt") and value["status"] not in {"answered", "dismissed", "retired", "superseded"}:
        lines.append("다음 확인 가능 시각: " + text(kst_timestamp(value["nextCheckAt"]), 60) + " (완료 예정 시각이 아닙니다)")
    lines.extend(["", "연구 과정과 AI 검토 기록입니다. 예측 성과가 검증되었다는 뜻이나 매매 권고가 아닙니다.",
                  "기록 시각: " + text(kst_timestamp(value["at"]), 60)])
    return notification_times_kst("\n".join(lines))
