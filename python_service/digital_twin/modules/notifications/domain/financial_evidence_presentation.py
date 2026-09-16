"""Present reported facts without turning a refresh into a new disclosure."""

import math
from digital_twin.modules.news_intelligence.contracts import compact_financial_evidence
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


def financial_evidence_rows(context, limit=3):
    relation = context.get("ontologyRelationContext") or {}
    facts = relation.get("facts") or {}
    company = facts.get("companyContext") or {}
    synthesis = context.get("v2DecisionSynthesis") or relation.get("decisionSynthesis") or {}
    rule_text = str(synthesis.get("selected_rule_id") or "") + str(relation.get("activeRules") or [])
    if "graph.company." not in rule_text:
        return []
    packet = compact_financial_evidence(company)
    if not packet:
        return ["재무 규칙의 비교 수치와 보고 기간을 확인하지 못했습니다. 공시로 확인된 사실과 구분해 다시 검증합니다."]
    previous = context.get("previousDeliveredInvestmentAIInsightEpisode") or context.get("previousInvestmentAIInsightEpisode") or {}
    previous_packet = previous.get("financialEvidence") or (previous.get("insight") or {}).get("financialEvidence") or {}
    period = str(packet.get("period") or "")[:10]
    if previous_packet.get("fingerprint") == packet.get("fingerprint"):
        return [period + " 기준 기존 재무 근거를 유지합니다. 이번 가격·거래 변화가 새 재무 공시를 뜻하지는 않습니다."]
    rows = [period + " 보고 기간의 재무 비교입니다. 이번 조회 시각에 새로 발표된 실적이라는 뜻은 아닙니다."]
    preferred = ("sharesOutstanding", "freeCashFlow") if "dilution" in rule_text else ("operatingIncome", "revenue", "freeCashFlow")
    comparisons = sorted(packet.get("comparisons") or [], key=lambda r: preferred.index(r["metric"]) if r.get("metric") in preferred else 99)
    for item in comparisons:
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
        rows.append(LABELS[field] + ": " + amount(item.get("previousValue")) + " → " + amount(item.get("currentValue"))
                    + " (" + basis + " " + format(float(item.get("changePct") or 0), "+.2f") + "%) · "
                    + str(item.get("previousPeriod") or "")[:10] + " → " + str(item.get("currentPeriod") or "")[:10]
                    + " · " + str(item.get("provider") or "출처 미확인"))
        if len(rows) >= limit:
            break
    return rows
