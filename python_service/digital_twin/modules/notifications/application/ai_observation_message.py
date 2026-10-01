"""Render verified central observations without another AI or live enrichment."""
from datetime import datetime
import math
from digital_twin.modules.ai_orchestration.contracts import OBSERVATION_METRICS, resolve_observation_ref
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
        if 0 < abs(number) < 0.01:
            return ("-" if number < 0 else "+" if signed else "") + "0.01" + suffix + " 미만"
        formatted = f"{number:+,.2f}" if signed else f"{number:,.2f}"
        return formatted.rstrip("0").rstrip(".") + suffix
    except (TypeError, ValueError):
        return ""


def source_label(fact):
    source = fact.get("observationSource") or fact.get("sourceName") or fact.get("provider") or fact.get("source")
    return str(source)[:120] if source and source not in {"holding", "watchlist"} else "제공처 미확인"


def metric(fact, field):
    label, unit = OBSERVATION_METRICS[field]
    if unit == "money":
        unit = {"KRW": "원", "USD": "달러"}.get(fact.get("currency"), str(fact.get("currency") or ""))
    return label + ": " + figure(fact.get(field), unit, field in {"changeRate", "profitLossRate", "foreignNetVolume", "institutionNetVolume", "ma5Slope", "ma20Slope", "ma60Slope"})


def render_ai_observation(result, *, sent_at="", debug_number=""):
    packet = result["input"]
    quote = next((row for row in packet["facts"] if figure(row.get("currentPrice")) and float(row["currentPrice"]) > 0), {})
    lines = [f"🧠 AI 관찰 · {packet['name']} ({packet['symbol']})", "", result["summary"]]
    lines += ["", "이전 알림과 비교", result["comparison"]]
    compared = set()
    for row in result.get("observations", []):
        refs = [row["left"], row["right"]]
        if {ref["period"] for ref in refs} != {"current", "baseline"} or refs[0]["field"] != refs[1]["field"]:
            continue
        field = refs[0]["field"]
        if field not in OBSERVATION_METRICS or field in compared:
            continue
        current, baseline = [resolve_observation_ref(packet, next(ref for ref in refs if ref["period"] == period))[0] for period in ("current", "baseline")]
        lines.append("• " + metric(baseline, field) + " → " + metric(current, field).split(": ", 1)[1])
        compared.add(field)
    lines += ["", "확인한 현재 데이터"]
    fields = ["currentPrice", "changeRate", "averagePrice", "profitLossRate", "positionWeight", "ma5", "ma20", "ma60", "volume", "volumeRatio"]
    cited_fields = {ref["field"] for refs in result.get("claimEvidence", {}).values() for ref in refs if ref["period"] == "current" and ref["factId"] == quote.get("id")}
    fields += [field for field in OBSERVATION_METRICS if field in cited_fields and field not in fields]
    for key in fields:
        if key not in quote or quote[key] is None:
            continue
        if key in {"averagePrice", "profitLossRate", "positionWeight"} and float(quote.get("quantity") or 0) <= 0:
            continue
        value = float(quote[key])
        positive_only = OBSERVATION_METRICS[key][1] == "money" or key in {"volume", "volumeRatio", "tradeStrength", "positionWeight", "policyLimitRatio", "strategyMaxPositionWeightPct"}
        if not math.isfinite(value) or positive_only and value <= 0:
            continue
        if key in {"foreignNetVolume", "institutionNetVolume"}:
            participant = "foreign" if key.startswith("foreign") else "institution"
            if (quote.get("investorFlowParticipantStatus") or {}).get(participant) in {"unsupported", "missing"}:
                continue
        if key == "changeRate" and value == 0 and quote.get("dataState") == "partial":
            continue
        lines.append("• " + metric(quote, key))
    if quote.get("volumeRatio"):
        lines.append("거래량 비율은 같은 장중 시각끼리의 비교가 아닙니다.")
    session = " · 장 마감 자료" if quote.get("marketSessionStatus") == "closed" else ""
    lines += ["시세 기준 " + clock_label(quote.get("sourceAsOf") or quote.get("asOf")) + session,
              "출처 " + source_label(quote)]
    for title, key in (("가능한 설명", "hypothesis"), ("내 보유·관심 상황에서의 의미", "portfolioImpact"),
                       ("반대 근거와 확인 한계", "counterEvidence")):
        if result.get(key):
            lines += ["", title, result[key]]
    citations, seen = [], set()
    # JSON object order differs across task/outbox persistence. Select the same
    # three citations before the delivery guard reconstructs the exact body.
    evidence = result.get("claimEvidence", {})
    for section in sorted(evidence):
        refs = evidence[section]
        for ref in refs:
            fact, value = resolve_observation_ref(packet, ref)
            if ref["period"] != "current" or fact.get("kind") == "stock" or fact["id"] in seen:
                continue
            seen.add(fact["id"])
            title = str(fact.get("label") or fact.get("title") or "추가 근거")[:80]
            citations.append("• " + title + " · " + source_label(fact))
    if citations:
        lines += ["", "함께 확인한 근거", *citations[:3]]
    checks = result.get("followUpConditions", [])
    if checks:
        lines += ["", "다음 관찰에서 확인할 조건"]
        for row in checks:
            left = OBSERVATION_METRICS[row["left"]["field"]][0]
            right = OBSERVATION_METRICS[row["right"]["field"]][0]
            operator = {"gt": "초과로", "gte": "이상으로", "lt": "미만으로", "lte": "이하로"}[row["operator"]]
            effect = {"supports": "설명을 뒷받침", "weakens": "설명 약화", "invalidates": "설명 재검토"}[row["effect"]]
            particle = "이" if 0xAC00 <= ord(left[-1]) <= 0xD7A3 and (ord(left[-1]) - 0xAC00) % 28 else "가"
            lines.append(f"• {left}{particle} {right} {operator} 전환 → {effect}")
        lines.append("확인 기간 " + clock_label(min(row["expiresAt"] for row in checks)) + "까지 · 조건 성립이 예측 적중을 뜻하지는 않습니다.")
    evaluations = [row for row in result.get("followUpEvaluations", []) if row.get("transitionVerified")]
    if evaluations:
        lines += ["", "지난 설명의 확인 결과", *["• " + row["description"] + " — 새 관측에서 조건 성립" for row in evaluations]]
    if result.get("researchQuestions"):
        lines += ["", "추가 조사할 내용", *["• " + question for question in result["researchQuestions"]]]
    lines += ["", "지금 알리는 이유", result["notification"]["reason"], "",
              "분석 기준 " + clock_label(result["observedAt"]) + " · 가능한 설명이며 매매 지시가 아닙니다."]
    if sent_at:
        lines.append("발송 " + clock_label(sent_at) + " · " + debug_number)
    return "\n".join(lines)
