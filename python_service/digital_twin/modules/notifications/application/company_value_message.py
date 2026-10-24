"""Render frozen calculation values, never a model-authored target price."""
from ..domain.company_value_status import COMPANY_VALUE_PRESENTATION_VERSION, hold_details, unread_value_lines

LABELS = {
    "non-positive-equity-value": "계산된 주주가치가 양수가 아닙니다",
    "per-share-currency-unconfirmed": "주당 기준·통화 확인 필요",
    "future-source-reference": "분석 시점 이후 자료가 포함돼 평가 보류",
    "scenario-range-unavailable": "보수적·기준·낙관적 평가 범위 확인 필요",
    "input-lineage-unavailable": "계산 결과와 원천 입력의 연결 확인 필요",
    "estimate-lineage-unavailable": "주당 가치 결과와 계산 근거의 연결 확인 필요",
    "source-revisions-incomplete": "재현 가능한 원천 자료 확인 필요",
    "valuation-clock-unavailable": "평가·자료 기준 시점 확인 필요",
    "valuation-not-admitted": "모델·가정·입력 자료의 검증 미완료",
    "source-not-admitted": "판단에 사용할 수 없는 원천 자료 포함",
    "estimate-calculation-mismatch": "계산 결과와 표시 적정가의 불일치",
}

MODEL_LABELS = {
    "preferred-income-yield": "배당 수익 기준",
    "bitcoin-treasury-nav": "보유 자산 가치 기준",
    "semiconductor-cycle-earnings": "반도체 경기·이익 기준",
    "growth-quality-earnings": "성장성·이익 기준",
    "generic-fundamental-earnings": "기업 이익 기준",
    "earnings-multiple": "기업 이익 기준",
    "driver-dcf": "미래 현금흐름 기준",
    "driver-fcff-dcf": "미래 현금흐름 기준",
}


def value_lines(value, clock_label, figure, *, packet=None, presentation_version=None, compact=False):
    if compact and value:
        available = [model for model in value.get("models", []) if model.get("status") == "available"]
        if available:
            return legacy_value_lines({**value, "models": available}, clock_label, figure)
        pending = any(model.get("nextStep") == "model-review"
                      for model in (packet or {}).get("companyValueReadiness", {}).get("models", []))
        return ["", "기업 가치 평가: " + ("모델 검토 대기" if pending else "검증된 평가 범위 확인 필요")]
    if not value or presentation_version != COMPANY_VALUE_PRESENTATION_VERSION:
        return legacy_value_lines(value, clock_label, figure)
    packet = packet or {}
    available = [m for m in value.get("models", []) if m.get("status") == "available"]
    lines = legacy_value_lines({**value, "models": available}, clock_label, figure) if available else ["", "기업의 주당 가치"]
    for index, model in enumerate(value.get("models", []), 1):
        if model.get("status") == "available":
            continue
        details = hold_details(model, packet)
        lines.append(MODEL_LABELS.get(model.get("modelId"), "평가 방법 " + str(index)))
        lines.append(details["stage"] + " · " + " / ".join(label for label, _ in details["checks"][:3]))
        lines.extend("• " + description for _, description in details["checks"][:3])
        if len(details["checks"]) > 3:
            lines.append("• 추가 확인: " + " / ".join(label for label, _ in details["checks"][3:]))
    return lines + unread_value_lines(value, packet)


def legacy_value_lines(value, clock_label, figure):
    if not value:
        return []
    lines = ["", "기업의 주당 가치"]
    available = [m for m in value.get("models", []) if m.get("status") == "available"]
    if not available:
        reasons = list(dict.fromkeys(LABELS.get(reason, "평가 입력·가정의 추가 확인 필요")
            for m in value.get("models", []) for reason in m.get("blockedReasons", [])))
        return lines + ["평가 보류 · " + (" / ".join(reasons[:3]) or "검증된 가치평가 자료가 아직 연결되지 않았습니다.")]
    for index, model in enumerate(available, 1):
        currency = model["currency"]
        def money(number):
            return (f"{number:,.0f}원" if currency == "KRW" else figure(number, "달러" if currency == "USD" else " " + currency))
        scenarios = {s["scenarioId"]: s["valuePerShare"] for s in model["scenarios"]}
        lines += [MODEL_LABELS.get(model.get("modelId"), "평가 방법 " + str(index)),
                  "• 보수적 " + money(scenarios["downside"]) + " · 기준 " + money(scenarios["base"]) + " · 낙관적 " + money(scenarios["upside"]),
                  "평가 시점 " + clock_label(model["valuationAt"]) + " · 자료 기준 " + clock_label(model["knowledgeCutoffAt"])]
        comparison = model.get("priceComparison", {})
        if comparison.get("status") == "source-dated":
            lines.append("시세 " + money(comparison["price"]) + " 대비 기준 가치 차이 " + figure(comparison["baseDifferencePct"], "%", True)
                         + " · 시세 기준 " + clock_label(comparison["sourceAsOf"]))
        lines.append({"initial": "이전 평가와 비교할 기록이 없습니다.", "unchanged": "기존 가치 범위와 사업 가정 유지 · 가격 변화는 별도 비교",
                      "basis-changed": "이전 평가 대비 계산 결과·입력·가정 또는 사용 가능 상태가 달라졌습니다."}.get(model.get("change"), ""))
    lines.append("가정에 따른 계산 범위이며 목표주가나 보장 수익률이 아닙니다.")
    if len(available) > 1:
        lines.append("서로 다른 평가 모델의 결과를 각각 표시했으며 평균하지 않았습니다.")
    return lines
