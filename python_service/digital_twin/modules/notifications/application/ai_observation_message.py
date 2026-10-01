"""Render verified central observations without another AI or live enrichment."""
from datetime import datetime
import math
from zoneinfo import ZoneInfo


def clock_label(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Seoul")).strftime("%m/%d %H:%M KST")
    except (TypeError, ValueError):
        return "시점 미확인"


def figure(value, suffix="", signed=False):
    try:
        number = float(value)
        if not math.isfinite(number):
            return ""
        formatted = f"{number:+,.2f}" if signed else f"{number:,.2f}"
        return formatted.rstrip("0").rstrip(".") + suffix
    except (TypeError, ValueError):
        return ""


def render_ai_observation(result, *, sent_at="", debug_number=""):
    packet = result["input"]
    quote = next((row for row in packet["facts"] if figure(row.get("currentPrice")) and float(row["currentPrice"]) > 0), {})
    unit = {"KRW": "원", "USD": "달러"}.get(quote.get("currency"), str(quote.get("currency") or ""))
    lines = [f"🧠 AI 관찰 · {packet['name']} ({packet['symbol']})", "", result["summary"], "", "확인한 현재 데이터"]
    metrics = [
        ("시세", "currentPrice", unit, False), ("전일 대비", "changeRate", "%", True),
        ("내 평균 매입가", "averagePrice", unit, False), ("평가 손익률", "profitLossRate", "%", True),
        ("20일 평균 가격", "ma20", unit, False), ("60일 평균 가격", "ma60", unit, False),
        ("평소 대비 거래량", "volumeRatio", "배", False), ("체결 강도", "tradeStrength", "%", False),
        ("외국인 순매수", "foreignNetVolume", "주", True), ("기관 순매수", "institutionNetVolume", "주", True),
    ]
    for label, key, suffix, signed in metrics:
        if key in {"averagePrice", "ma20", "ma60", "volumeRatio", "tradeStrength"} and float(quote.get(key) or 0) <= 0:
            continue
        if key in {"foreignNetVolume", "institutionNetVolume"} and not quote.get(key):
            continue
        value = figure(quote.get(key), suffix, signed)
        if value and (key not in {"averagePrice", "profitLossRate"} or float(quote.get("quantity") or 0) > 0):
            lines.append(f"• {label}: {value}")
    lines.append("시세 기준 " + clock_label(quote.get("sourceAsOf") or quote.get("asOf")))
    if quote.get("source"):
        lines.append("출처 " + str(quote["source"])[:180])
    if quote.get("dataState") == "partial":
        lines.append("일부 항목이 수집되지 않은 데이터입니다.")
    for title, body in (("지금 알려드리는 이유", result["notification"]["reason"]),
                        ("지난 알림과 달라진 점", result["comparison"]),
                        ("가능한 설명 · 가설", result["hypothesis"]),
                        ("함께 봐야 할 반대 근거", result["counterEvidence"]),
                        ("다음에 확인할 점", " / ".join(result.get("questions", [])))):
        if body:
            lines.extend(["", title, body])
    lines.extend(["", f"분석 기준 {clock_label(result['observedAt'])} · 근거 {len(result['evidenceIds'])}개",
                  "AI가 제시한 설명이며 확인된 사실과 구분해 읽어주세요."])
    if sent_at:
        lines.append(f"발송 {clock_label(sent_at)} · {debug_number}")
    return "\n".join(lines)
