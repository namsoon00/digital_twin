"""Source-bound accounting explanations and projections of existing assessments.

These observations explain arithmetic and model assumptions. They do not infer
business causes, rank investments, or create an action or an AI opinion.
"""

import hashlib
import json

from .company_report_evidence import amount, mapping, number, rows, text


READING_VERSION = "company-report-reading-v1"
BASIS_KEYS = ("period", "durationBasis", "scope", "provider", "currency")


def _same_basis(*metrics):
    return bool(metrics) and all(
        all(metric.get(key) and metric.get(key) == metrics[0].get(key) for key in BASIS_KEYS)
        and number(metric.get("value")) is not None for metric in metrics
    ) and all(metric.get("periodStart") == metrics[0].get("periodStart")
              and metric.get("sourceDocumentId") == metrics[0].get("sourceDocumentId") for metric in metrics)


def _evidence(metric):
    material = {key: metric.get(key) for key in (
        "key", "value", "currency", "period", "periodStart", "sourceMetric", "durationBasis", "scope", "provider",
        "sourceDocumentId", "sourceReferences", "comparison",
    )}
    identity = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    return {**metric, "evidenceId": "company-report-metric:" + identity}


def _card(key, title, fact, meaning, metrics, checks, limits, **extra):
    return {"key": key, "title": title, "kind": "accounting-observation",
            "fact": fact + " · " + ", ".join(dict.fromkeys(item["provider"] for item in metrics)),
            "meaning": meaning, "evidence": [_evidence(item) for item in metrics],
            "nextChecks": checks, "limitations": limits, **extra}


def _compact_money(value, currency):
    if currency == "USD" and abs(value) >= 1e8:
        return f"{value / 1e8:,.2f}억 달러"
    if currency == "USD" and abs(value) >= 1e4:
        return f"{value / 1e4:,.1f}만 달러"
    return amount(value, currency)


def _asset_remeasurement_card(metrics):
    revenue, operating = mapping(metrics.get("revenue")), mapping(metrics.get("operatingIncome"))
    gain = mapping(metrics.get("cryptoAssetUnrealizedGainLossOperating"))
    loss = mapping(metrics.get("cryptoAssetUnrealizedLossOperating"))
    # The two SEC concepts have different signs. Do not treat a positive loss
    # as a gain, or combine a prior filing/period with current operating income.
    for metric, sign in ((gain, 1), (loss, -1)):
        inputs = [revenue, operating, metric]
        if (not _same_basis(*inputs) or not all(item.get("official") is True for item in inputs)
                or not all(item.get("sourceDocumentId") == operating.get("sourceDocumentId")
                           and item.get("sourceDocumentId") for item in inputs)
                or not all(item.get("periodStart") == operating.get("periodStart")
                           and item.get("periodStart") for item in inputs)):
            continue
        unrealized, reported = metric["value"] * sign, operating["value"]
        if (not reported or unrealized * reported <= 0 or abs(unrealized) < abs(reported) / 2
                or (sign == -1 and metric["value"] < 0)):
            continue
        residual = reported - unrealized
        word = "손실" if unrealized < 0 else "이익"
        currency = operating["currency"]
        headline = "보고된 영업" + word + "에는 디지털자산 평가" + word + "의 영향이 큽니다."
        meaning = ("보유자산의 가격 변화가 손익에 반영된 것이므로 같은 금액의 현금 유출·유입이나 제품 판매 수익성으로 해석하면 안 됩니다. "
                   "미실현손실도 보유자산 가치가 줄었다는 위험은 남습니다." if unrealized < 0 else
                   "보유자산의 가격 상승이 반영된 이익입니다. 같은 금액의 현금 유입이나 반복 가능한 제품 판매 이익으로 볼 수 없습니다.")
        if residual < 0:
            meaning += " 해당 평가손익만 제외해도 영업손실이 남습니다."
        fact = (operating["basisLabel"] + " · 보고 영업손익 " + _compact_money(reported, currency)
                + " 중 디지털자산 미실현손익 " + _compact_money(unrealized, currency)
                + " · 해당 평가손익만 제외한 계산 " + _compact_money(residual, currency))
        return _card("operating-margin", "평가손익과 사업 실적을 분리해서 보기", fact, meaning, inputs,
                     ["보유 디지털자산 가치와 부채·우선주 지급 부담, 증자에 따른 주당 가치 변화를 함께 확인해야 합니다."],
                     ["평가손익만 제외한 금액은 정상화 이익이나 현금흐름이 아닙니다. 나머지 일회성 항목은 별도 확인이 필요합니다."],
                     headline=headline, driver="digital-asset-remeasurement",
                     briefFact=operating["basisLabel"].replace("official-filing", "공시 기준") + " · 영업손익 " + _compact_money(reported, currency) + " 중 자산 평가손익 " + _compact_money(unrealized, currency),
                     valuationMeaning="이 영업이익률로 본업 가치를 추정하면 왜곡될 수 있습니다. 보유자산 가치에서 부채·우선주 청구권을 고려하고 주식 수 증가의 영향을 함께 봐야 합니다.",
                     briefCheck="디지털자산 가치, 이자·우선주 배당의 지급 여력, 증자에 따른 주당 가치 변화를 확인합니다.")
    return None


def _margin_card(metrics):
    revenue, operating = mapping(metrics.get("revenue")), mapping(metrics.get("operatingIncome"))
    if not _same_basis(revenue, operating) or revenue["value"] <= 0:
        return None
    remeasurement = _asset_remeasurement_card(metrics)
    if remeasurement:
        return remeasurement
    margin = operating["value"] / revenue["value"] * 100
    fact = operating["basisLabel"] + f" · 영업이익률 {margin:.2f}%"
    headline = "영업흑자이지만 이익의 지속성은 비용 구조와 현금흐름을 함께 봐야 합니다." if margin >= 0 else "영업적자입니다. 매출 증가만으로 이익 회복을 판단할 수 없습니다."
    meaning = ("매출에서 영업비용을 차감한 금액이 양수입니다. 같은 매출에서도 비용이 늘면 이익이 줄기 때문에 매출 성장과 이익률을 함께 확인해야 합니다."
               if margin >= 0 else "매출보다 영업비용이 큽니다. 판매 증가로 손실을 줄일 수 있는 비용인지, 평가손실·일회성 비용인지 구분해야 회복 가능성을 판단할 수 있습니다.")
    if abs(margin) >= 100:
        headline = "보고 손익의 규모가 매출보다 커 영업이익률만으로 사업 수익성을 판단하기 어렵습니다."
        meaning = "평가손익·일회성 항목이 포함됐는지 분해해야 합니다. 현재 연결된 수치만으로 손익의 원인이나 같은 금액의 현금 유출·유입을 확정할 수 없습니다."
    revenue_comparison, operating_comparison = mapping(revenue.get("comparison")), mapping(operating.get("comparison"))
    old_revenue, old_operating = number(revenue_comparison.get("previousValue")), number(operating_comparison.get("previousValue"))
    if (old_revenue is not None and old_revenue > 0 and old_operating is not None
            and number(revenue_comparison.get("currentValue")) == revenue["value"]
            and number(operating_comparison.get("currentValue")) == operating["value"]
            and revenue_comparison.get("currentPeriod") == revenue.get("period")
            and operating_comparison.get("currentPeriod") == operating.get("period")
            and revenue_comparison.get("previousPeriod")
            and revenue_comparison.get("previousPeriod") == operating_comparison.get("previousPeriod")
            and all(mapping(revenue_comparison.get("previousSource")).get(key)
                    and mapping(revenue_comparison.get("previousSource")).get(key)
                    == mapping(operating_comparison.get("previousSource")).get(key)
                    for key in BASIS_KEYS)):
        previous_margin = old_operating / old_revenue * 100
        delta = margin - previous_margin
        fact += f" · 전년 동기 {previous_margin:.2f}% → {margin:.2f}% ({delta:+.2f}%p)"
        meaning += (" 같은 매출에서 남는 영업이익의 비율이 높아졌습니다." if delta > 0
                    else " 같은 매출에서 남는 영업이익의 비율이 낮아졌습니다." if delta < 0
                    else " 전년 동기와 매출 대비 영업이익 비율이 같습니다.")
    return _card("operating-margin", "매출이 영업이익으로 남는 정도", fact, meaning,
                 [revenue, operating],
                 ["다음 동일 기준 실적에서 영업이익률의 유지·변화를 확인하고, 가격·판매량·비용 및 일회성 항목을 원문과 대조해야 합니다."],
                 ["이 계산만으로 개선·악화의 원인이나 지속 가능한 이익 수준을 확정할 수 없습니다."],
                 headline=headline,
                 briefCheck="영업손익에 포함된 평가손익·일회성 비용과 실제 영업현금흐름을 구분해 확인합니다.")


def _cash_card(metrics):
    cash, capex = mapping(metrics.get("operatingCashFlow")), mapping(metrics.get("capitalExpenditure"))
    if not _same_basis(cash, capex):
        return None
    remaining = cash["value"] - abs(capex["value"])
    currency = cash["currency"]
    fact = (cash["basisLabel"] + " · 영업현금흐름 " + amount(cash["value"], currency)
            + " − 설비투자 현금유출 " + amount(abs(capex["value"]), currency)
            + " = " + amount(remaining, currency))
    meaning = ("영업으로 들어온 현금이 설비투자 지출을 충당했습니다. 다만 차입 상환·배당·자산 매입까지 감당한다는 뜻은 아닙니다." if remaining >= 0 else
               "영업현금만으로 설비투자 지출을 충당하지 못했습니다. 부족분의 조달 방법과 투자가 향후 현금 창출로 이어질지 확인해야 합니다.")
    return _card("cash-after-investment", "투자 후 남는 영업현금", fact, meaning,
                 [cash, capex],
                 ["다음 비교 가능한 기간에서 영업현금흐름과 설비투자를 함께 확인해야 합니다. 차감액이 음수이면 투자 확대와 영업현금 감소 중 어느 항목이 설명하는지 확인해야 합니다."],
                 ["차입·상환·배당 및 기업 인수대금은 이 계산에 포함되지 않습니다. 음수만으로 재무위기나 투자 실패를 뜻하지 않습니다."],
                 briefFact=cash["basisLabel"] + " · 설비투자 후 현금 " + amount(remaining, currency) + " · " + cash["provider"],
                 briefCheck="다음 누적 실적에서 영업현금과 설비투자 지출의 변화를 함께 확인합니다.")


def _earnings_card(metrics):
    operating, net = mapping(metrics.get("operatingIncome")), mapping(metrics.get("netIncome"))
    if not _same_basis(operating, net) or net["value"] <= operating["value"]:
        return None
    meaning = ("순손실이 영업손실보다 작습니다. 영업외손익·법인세가 최종 손실에 미친 영향을 확인해야 하며, 흑자를 뜻하지 않습니다."
               if net["value"] < 0 else "순이익이 영업손익보다 큽니다. 영업외손익·법인세 영향을 구분해야 반복 가능한 이익인지 판단할 수 있습니다.")
    return _card("earnings-basis", "순손익과 영업손익의 차이", net["basisLabel"]
                 + " · 영업이익 " + amount(operating["value"], operating["currency"])
                 + " · 순이익 " + amount(net["value"], net["currency"]),
                 meaning,
                 [operating, net],
                 ["영업외손익과 법인세 내역을 확인해야 합니다. 일회성 항목이 확인되면 반복 가능한 이익과 구분해 평가 입력을 검토해야 합니다."],
                 ["차이의 원인과 반복 여부는 이 두 금액만으로 확인되지 않습니다."])


def financial_reading_cards(evidence):
    evidence = mapping(evidence)
    reports = sorted(rows(evidence.get("recentFinancials")), key=lambda item: text(item.get("period")), reverse=True)
    reports += sorted(rows(evidence.get("annualFinancials")), key=lambda item: text(item.get("period")), reverse=True)
    cards, seen = [], set()
    # Choose the latest comparable inputs for each question, never join periods.
    for report in reports:
        metrics = {item.get("key"): item for item in rows(report.get("metrics"))}
        for builder in (_margin_card, _cash_card, _earnings_card):
            card = builder(metrics)
            if card and card["key"] not in seen:
                seen.add(card["key"])
                cards.append(card)
    return cards[:3]


def valuation_reading_cards(payload):
    valuation = mapping(payload.get("valuation"))
    analysis = mapping(payload.get("investmentAnalysis"))
    models = rows(analysis.get("valuationModels"))
    cards = []
    ready = [item for item in models if item.get("decisionEligible") is True and not item.get("referenceOnly")]
    agreement = mapping(analysis.get("modelAgreement"))
    quality = mapping(valuation.get("quality"))
    fair = mapping(valuation.get("fairValue"))
    low, base, high = (number(fair.get(key)) for key in ("low", "base", "high"))
    currency = text(fair.get("currency") or mapping(payload.get("instrument")).get("currency"))
    primary_id = mapping(valuation.get("model")).get("id")
    matching = [item for item in ready if item.get("modelId") == primary_id and item.get("currency") == currency]
    eligible = bool(primary_id and currency and matching and quality.get("decisionEligible") is True and agreement.get("status") != "conflict"
                    and all(value is not None and value > 0 for value in (low, base, high)) and low <= base <= high)
    if eligible:
        fact = "현재 가정의 계산 범위 " + amount(low, currency) + " ~ " + amount(high, currency) + " · 기준 " + amount(base, currency)
        meaning = "이 범위는 선택한 이익·평가 가정의 계산 결과입니다. 실제 가치가 이 안에 있을 확률을 뜻하지 않습니다."
    else:
        fact = "현재 가치평가 입력·가정의 검토가 완료되지 않았습니다." if not ready else "모델 간 차이 또는 주 평가 범위의 정합성 확인이 필요합니다."
        meaning = "현재 계산만으로 저평가·고평가를 확정할 수 없습니다. 참고 계산은 아래 가정과 함께 확인할 수 있습니다."
    cards.append({"key": "valuation-use", "kind": "conditional-model", "title": "가치평가를 어디까지 사용할 수 있나",
                  "fact": fact, "meaning": meaning, "evidence": [], "modelIds": [item.get("modelId") for item in models],
                  "nextChecks": ["매출·이익 전망 또는 적용 배수가 달라지면 가치 범위를 다시 검토해야 합니다. 검토 대기 가정은 확정값으로 사용하지 않습니다."],
                  "limitations": ["기업의 확정 가치나 매매 권고가 아닙니다."], "calculationEligible": eligible})
    implied = mapping(valuation.get("impliedExpectations"))
    evidence_period = text(mapping(implied.get("financialEvidence")).get("period"))
    annual_periods = [text(row.get("period")) for row in rows(mapping(payload.get("companyReportEvidence")).get("annualFinancials"))]
    if (implied.get("status") == "solved" and implied.get("assumptionReviewState") == "complete"
            and implied.get("officialFinancialsReady") is True and evidence_period
            and (not annual_periods or evidence_period >= max(annual_periods))):
        margin, growth = number(implied.get("impliedEbitMarginPct")), number(implied.get("impliedRevenueGrowthPct"))
        value = margin if margin is not None else growth
        if value is not None:
            variable = "영업이익률" if margin is not None else "매출 성장률"
            fact = "저장된 가격과 나머지 가정을 고정한 역산 " + variable + f" {value:.2f}%"
            meaning = "해당 계산에서 가격을 설명하려면 필요한 조건입니다. 회사 전망이나 실제 시장 기대를 관측한 값이 아닙니다."
            observed = number(implied.get("observedEbitMarginPct"))
            if margin is not None and observed is not None:
                fact += f" · 계산 기준 영업이익률 {observed:.2f}%와 차이 {margin - observed:+.2f}%p"
                meaning += " 매출 전망이 같더라도 이익률 가정의 변화가 가격 설명에 얼마나 필요한지 보여줍니다."
            cards.append({"key": "price-requirements", "kind": "conditional-model", "title": "가격을 설명하는 사업 조건",
                          "fact": fact, "meaning": meaning,
                          "evidence": [], "modelIds": ["driver-fcff-dcf"],
                          "calculationId": text(implied.get("solverId")), "asOf": text(implied.get("quoteAsOf")),
                          "fixedAssumptions": mapping(implied.get("fixedAssumptions")),
                          "nextChecks": ["실제 " + variable + "와 계산이 요구하는 조건을 같은 기준으로 비교해야 합니다. 할인율·성장·투자 가정이 달라지면 역산값도 달라집니다."],
                          "limitations": ["가정 검토 완료" if implied.get("assumptionReviewState") == "complete" else "가정 검토가 남은 조건부 계산입니다."]})
    return cards


def build_company_report_reading(payload, previous):
    cards = financial_reading_cards(payload.get("companyReportEvidence"))
    insight = mapping(payload.get("companyReportInsight"))
    previous_insight = mapping(mapping(previous.get("reading")).get("insight"))
    transition = "not-comparable"
    if insight.get("state") == "available" and previous_insight.get("state") == "available":
        # Describe a textual interpretation change, never invent its polarity.
        fields = ("thesis", "meaning", "mechanism", "invalidation", "risks")
        transition = "unchanged" if all(insight.get(key) == previous_insight.get(key) for key in fields) else "changed"
    return {"contractVersion": READING_VERSION, "financial": cards,
            "valuation": valuation_reading_cards(payload), "insight": insight,
            "interpretationChange": transition}


def reading_sections(reading):
    reading = mapping(reading)
    insight = mapping(reading.get("insight"))
    sections = []
    if insight.get("state") == "available":
        sections.append({"key": "linkedInsight", "title": "근거가 연결된 AI 해석",
                         "paragraphs": ["분석 당시 " + text(insight.get("asOf")) + " · 보고서와 같은 재무 근거를 사용한 검증된 해석입니다. 시세·사건에 대한 설명은 분석 당시 기준입니다."],
                         "rows": [text(insight.get(key)) for key in ("thesis", "mechanism", "meaning") if insight.get(key)],
                         "insightEvidenceIds": insight.get("evidenceIds", [])})
    else:
        sections.append({"key": "linkedInsight", "title": "종합 해석의 연결 상태",
                         "paragraphs": [text(insight.get("reason")) or "현재 보고서의 근거와 연결할 검증된 AI 해석이 없습니다. 아래에는 수치로 확인할 수 있는 의미와 평가 조건을 표시합니다."]})
    financial = rows(reading.get("financial"))
    sections.append({"key": "businessMeaning", "title": "실적이 의미하는 것", "readingCards": financial,
                     "paragraphs": [] if financial else ["같은 기간·출처의 비교 가능한 수치가 부족해 실적의 의미를 설명하지 못했습니다."]})
    sections.append({"key": "valuationMeaning", "title": "기업가치와 현재 가격의 조건", "readingCards": rows(reading.get("valuation"))})
    checks = ([text(insight.get("invalidation"))] if insight.get("state") == "available" and insight.get("invalidation") else [])
    checks.extend(check for card in financial + rows(reading.get("valuation")) for check in card.get("nextChecks", []))
    sections.append({"key": "judgmentConditions", "title": "해석을 다시 확인할 조건",
                     "rows": list(dict.fromkeys(checks)),
                     "paragraphs": ["확인할 항목이며 자동 감시 등록을 뜻하지 않습니다."]})
    if insight.get("state") == "available" and insight.get("risks"):
        sections.append({"key": "counterEvidence", "title": "연결된 해석의 반대 근거와 한계", "rows": insight["risks"]})
    return sections
