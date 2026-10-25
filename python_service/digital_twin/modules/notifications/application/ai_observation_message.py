"""Render verified central observations without another AI or live enrichment."""
from datetime import datetime, timezone
import math
from digital_twin.modules.ai_orchestration.contracts import OBSERVATION_METRICS, OBSERVATION_WORDING_VERSION, resolve_observation_ref
from digital_twin.modules.ai_orchestration.contracts import observation_subject_quote, QUOTE_SELECTION_VERSIONS, dated_observation
from digital_twin.modules.ai_orchestration.contracts import observation_judgment_focus
from digital_twin.modules.reasoning.contracts import quote_clock_assessment, OBSERVATION_QUOTE_KINDS
from digital_twin.modules.notifications.domain.observation_price_basis import PRICE_PRESENTATION_VERSION, observation_price_basis
from zoneinfo import ZoneInfo


def clock_label(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(ZoneInfo("Asia/Seoul")).strftime("%m/%d %H:%M KST")
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
    return label + ": " + figure(fact.get(field), unit, field in {"change24h", "change7d", "changeRate", "profitLossRate", "foreignNetVolume", "institutionNetVolume", "ma5Slope", "ma20Slope", "ma60Slope"})


def render_ai_observation(result, *, sent_at="", debug_number=""):
    packet = result["input"]
    focus = observation_judgment_focus(result)
    # Persisted jobs without this version retain their original rendered bytes.
    quote = (observation_subject_quote(packet) if result.get("quoteSelectionVersion") in QUOTE_SELECTION_VERSIONS
             else next((row for row in packet["facts"] if figure(row.get("currentPrice")) and float(row["currentPrice"]) > 0), {}))
    dated = dated_observation(result)
    if dated and quote.get("id") not in result.get("evidenceIds", []):
        quote = {}
    assessment = {}
    if "quoteAssessment" in packet or result.get("pricePresentationVersion") == PRICE_PRESENTATION_VERSION:
        current = quote_clock_assessment(packet["facts"], sent_at or result["observedAt"])
        assessment = next((row for row in current["quotes"] if row["evidenceId"] == quote.get("id")), {})
    basis = observation_price_basis(quote, assessment) if result.get("pricePresentationVersion") == PRICE_PRESENTATION_VERSION else {}
    label = {"business": "AI 기업 관찰", "market": "AI 시장 관찰"}.get(focus, "AI 관찰")
    lines = [f"🧠 {label} · {packet['name']} ({packet['symbol']})", ""]
    if dated:
        lines += ["각 근거에 표시된 시점의 자료로 작성한 관찰입니다.", ""]
    if basis and not basis["currentUseAllowed"] and (quote or not dated):
        lines += [basis["label"] + " 기준 · " + clock_label(basis.get("sourceAsOf")), basis["reason"], ""]
    lines.append(result["summary"])
    from .company_value_message import value_lines
    if focus not in {"market", "none"}:
        lines += value_lines(result.get("companyValue"), clock_label, figure, packet=packet,
                            presentation_version=result.get("companyValuePresentationVersion"), compact=bool(focus))
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
        label = metric(baseline, field)
        if basis and field == "currentPrice":
            label = "관측 시점 가격: " + label.split(": ", 1)[1]
        lines.append("• " + label + " → " + metric(current, field).split(": ", 1)[1])
        compared.add(field)
    if quote or not dated:
        lines += ["", "확인한 데이터" if assessment or dated else "확인한 현재 데이터"]
    fields = ["currentPrice", "change24h", "change7d", "volume24h", "marketCap", "changeRate", "averagePrice", "profitLossRate", "positionWeight", "ma5", "ma20", "ma60", "volume", "volumeRatio"]
    cited_fields = {ref["field"] for refs in result.get("claimEvidence", {}).values() for ref in refs if ref["period"] == "current" and ref["factId"] == quote.get("id")}
    if result.get("companyValue") or result.get("businessResearch", {}).get("theses") or result.get("businessResearch", {}).get("reviews"):
        fields = ["currentPrice", "profitLossRate", "positionWeight"]
    fields += [field for field in OBSERVATION_METRICS if field in cited_fields and field not in fields]
    displayed = set()
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
        text = metric(quote, key)
        if key == "currentPrice" and dated:
            text = "관측 시점 가격: " + text.split(": ", 1)[1]
        elif key == "currentPrice" and basis:
            text = basis["label"] + ": " + text.split(": ", 1)[1]
        elif key == "currentPrice" and assessment and assessment["status"] != "fresh":
            text = "기준 시점 가격: " + text.split(": ", 1)[1]
        lines.append("• " + text)
        displayed.add(key)
    if quote.get("volumeRatio") and (not focus or "volumeRatio" in displayed):
        lines.append("거래량 비율은 같은 장중 시각끼리의 비교가 아닙니다.")
    session = (" · 출처의 마감 참고값" if assessment.get("referenceState") == "last-close" else "") if assessment else (
        " · 장 마감 자료" if quote.get("marketSessionStatus") == "closed" else "")
    if quote or not dated:
        lines += [(basis.get("sourceClockLabel", "시세 기준") + " ") + clock_label(quote.get("sourceAsOf") or quote.get("asOf")) + session,
                  "출처 " + source_label(quote)]
    if assessment:
        status = assessment["status"]
        if status == "stale":
            lines.append("시세 경과 " + figure(assessment["ageMinutes"], "분") + " · 갱신 기준 "
                         + figure(assessment["maxAgeMinutes"], "분") + " 초과 · 과거 시점 참고 자료")
        elif status == "fresh" and focus and assessment.get("referenceState") == "last-close":
            lines.append("마감 참고 시점으로부터 " + figure(assessment["ageMinutes"], "분") + " 경과 · 실시간 시세 아님")
        elif status == "fresh":
            lines.append("시세 경과 " + figure(assessment["ageMinutes"], "분") + " · 갱신 기준 이내")
        elif status == "unknown-budget":
            lines.append("시세 경과 " + figure(assessment["ageMinutes"], "분") + " · 갱신 기준 미확인")
        else:
            lines.append("시세 기준 시각 확인 필요 · 현재 상황으로 해석할 수 없습니다.")
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
            if ref["period"] != "current" or fact.get("kind") in OBSERVATION_QUOTE_KINDS or fact["id"] in seen:
                continue
            seen.add(fact["id"])
            title = str(fact.get("label") or fact.get("title") or "추가 근거")[:80]
            source_clock = fact.get("sourceAsOf") or fact.get("publishedAt") or fact.get("asOf")
            dated_suffix = " · 자료 기준 " + clock_label(source_clock) if dated and source_clock else ""
            if focus and fact.get("kind") == "company-financial-state":
                dated_suffix = " · 보고기간 " + str(fact.get("periodEnd") or "미확인")
                if source_clock:
                    dated_suffix += " · 출처 기준 " + clock_label(source_clock)
            citations.append("• " + title + " · " + source_label(fact) + dated_suffix)
    if citations:
        lines += ["", "함께 확인한 근거", *citations[:3]]
    business = {} if focus in {"market", "none"} else result.get("businessResearch") or {}
    if business.get("theses"):
        lines += ["", "사업 가설과 확인할 실적"]
        for thesis in business["theses"][:2]:
            lines += ["• " + thesis["mechanism"], "다른 설명: " + thesis["alternative"], "재검토 조건: " + thesis["invalidation"]]
            for check in thesis.get("checkpoints", []):
                base = check["baseline"]
                if check.get("contractVersion") == "quantitative-business-checkpoint-v1":
                    lines.append("• " + check["meaning"] + " · 대상 " + check["targetPeriodStart"] + "~" + check["targetPeriodEnd"]
                                 + " · AI 가정 범위 " + figure(check["expectedLow"], base["basis"]["currency"])
                                 + "~" + figure(check["expectedHigh"], base["basis"]["currency"]))
                    lines.append("가정: " + check["assumption"])
                    continue
                lines.append("• " + check["meaning"] + " · 기준 " + base["periodEnd"] + " " + str(base["value"]) + " " + base["basis"]["currency"] + " · 다음 해 같은 기간 확인")
        lines.append("사업 지표 확인은 가설 전체의 성공이나 투자 수익률 적중을 뜻하지 않습니다.")
    if business.get("reviews"):
        lines += ["", "이전 사업 가설 재검토", *["• " + review["reason"] for review in business["reviews"]]]
    checks = result.get("followUpConditions", [])
    if result.get("documentaryReview"):
        lines += ["", "다음 자료에서 확인할 내용", *["• " + question for question in result["documentaryReview"]["questions"]],
                  "새 원문이나 다음 실적이 들어오면 다시 확인합니다. 가격 전망의 적중 조건은 아닙니다."]
    if checks:
        lines += ["", "다음 관찰에서 확인할 조건"]
        for row in checks:
            left = OBSERVATION_METRICS[row["left"]["field"]][0]
            right = OBSERVATION_METRICS[row["right"]["field"]][0]
            if row.get("conditionVersion") == "crypto-observation-followup-v1":
                unit = OBSERVATION_METRICS[row["right"]["field"]][1]
                if unit == "money":
                    unit = {"USD": "달러", "KRW": "원"}.get(row["right"].get("currency"), row["right"].get("currency", ""))
                right = "분석 시점 기준 " + figure(row["right"]["baselineValue"], unit)
            operator = {"gt": "초과로", "gte": "이상으로", "lt": "미만으로", "lte": "이하로"}[row["operator"]]
            effect = {"supports": "설명을 뒷받침", "weakens": "설명 약화", "invalidates": "설명 재검토"}[row["effect"]]
            if result.get("wordingVersion") == OBSERVATION_WORDING_VERSION:
                effect = row["description"]
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
