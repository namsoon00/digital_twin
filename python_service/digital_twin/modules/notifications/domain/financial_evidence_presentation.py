"""Present reported facts without turning a refresh into a new disclosure."""

import math
from digital_twin.modules.news_intelligence.contracts import compact_financial_evidence, financial_evidence_use
from digital_twin.modules.read_models.contracts import CustomerInvestmentLink


LABELS = {"sharesOutstanding": "주식수", "freeCashFlow": "잉여현금흐름", "operatingIncome": "영업이익",
          "revenue": "매출", "netIncome": "순이익"}


def financial_evidence_links(context):
    company = (context.get("ontologyRelationContext") or {}).get("facts", {}).get("companyContext") or {}
    urls = {}
    for item in compact_financial_evidence(company).get("comparisons") or []:
        url = str(item.get("sourceUrl") or "")
        if url.startswith("https://"):
            urls[url] = CustomerInvestmentLink(label="재무 비교 원문 · " + str(item.get("currentPeriod") or "")[:10], url=url)
    return tuple(urls.values())[:3]


def financial_evidence_context(context):
    relation = context.get("ontologyRelationContext") or {}
    facts = relation.get("facts") or {}
    company = facts.get("companyContext") or {}
    synthesis = context.get("v2DecisionSynthesis") or relation.get("decisionSynthesis") or {}
    rule_text = str(synthesis.get("selected_rule_id") or "") + str(relation.get("activeRules") or [])
    packet = compact_financial_evidence(company)
    previous = context.get("previousDeliveredInvestmentAIInsightEpisode") or context.get("previousInvestmentAIInsightEpisode") or {}
    previous_packet = previous.get("financialEvidence") or (previous.get("insight") or {}).get("financialEvidence") or {}
    return packet, financial_evidence_use(packet, previous_packet), rule_text


def financial_evidence_title(context):
    _, use, _ = financial_evidence_context(context)
    if use.get("state") == "reused":
        return "기존 판단 근거 · 재무"
    if use.get("state") in {"revised", "new-period"}:
        return "갱신된 판단 근거 · 재무"
    return "판단 근거 · 재무"


def financial_evidence_rows(context, limit=5):
    packet, use, rule_text = financial_evidence_context(context)
    if "graph.company." not in rule_text:
        return []
    if not packet:
        return ["판단에 사용한 재무 수치와 보고 기간을 확인하지 못했습니다."]
    period = use.get("reportingPeriod") or "보고 기간 미확인"
    reused = use.get("state") == "reused"
    if reused:
        rows = [period + " 보고 기간의 기존 재무 근거입니다. 직전 알림과 같은 자료입니다."]
    elif use.get("state") == "revised":
        rows = [period + " 보고 기간의 재무 비교 자료가 갱신됐습니다. 새 공시 발표와는 구분합니다."]
    elif use.get("state") == "new-period":
        rows = ["비교 보고 기간이 " + use["previousReportingPeriod"] + "에서 " + period + "로 바뀌었습니다. 발표일과는 다릅니다."]
    else:
        rows = [period + " 보고 기간의 재무 비교입니다. 이번에 새로 발표된 실적이라는 뜻은 아닙니다."]
    quality_note = "일회성 손익을 분리하지 못해 이익 개선의 지속성은 확인되지 않았습니다."
    preferred = ("sharesOutstanding", "freeCashFlow") if "dilution" in rule_text else ("operatingIncome", "revenue", "freeCashFlow")
    comparisons = sorted(packet.get("comparisons") or [], key=lambda r: preferred.index(r["metric"]) if r.get("metric") in preferred else 99)
    for item in comparisons:
        if len(rows) >= max(1, limit - 1):
            break
        field = item.get("metric")
        if field not in preferred or item.get("status") != "verified-comparable":
            continue
        try:
            if not all(math.isfinite(float(item.get(key))) for key in ("currentValue", "previousValue", "changePct")):
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        def amount(value):
            if value is None:
                return "미확인"
            if field == "sharesOutstanding":
                return format(float(value), ",.0f") + "주"
            if item.get("currency") == "KRW":
                return format(float(value) / 100000000, ",.1f") + "억 원"
            return format(float(value), ",.0f") + (" " + str(item.get("currency")) if item.get("currency") else "")
        basis = {"year-over-year": "전년 동기 대비", "quarter-over-quarter": "전분기 대비"}.get(item.get("comparisonBasis"), "직전 보고 기간 대비")
        provider = {"OpenDART": "OpenDART 공시", "yfinance": "yfinance 집계"}.get(item.get("provider"), str(item.get("provider") or "출처 미확인"))
        if reused:
            rows.append(LABELS[field] + ": " + basis + " " + format(float(item["changePct"]), "+.2f") + "% · " + provider)
            continue
        rows.append(LABELS[field] + ": " + amount(item.get("previousValue")) + " → " + amount(item.get("currentValue"))
                    + " (" + basis + " " + format(float(item.get("changePct") or 0), "+.2f") + "%) · "
                    + str(item.get("previousPeriod") or "")[:10] + " → " + str(item.get("currentPeriod") or "")[:10]
                    + " · " + provider)
    if not (packet.get("earningsQuality") or {}).get("normalizedEarningsAvailable") and len(rows) < limit:
        rows.append(quality_note)
    return rows
