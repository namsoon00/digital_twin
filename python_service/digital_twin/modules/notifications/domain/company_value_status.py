"""Explain frozen valuation limits without changing calculation admission."""

COMPANY_VALUE_PRESENTATION_VERSION = "company-value-status-v1"

# The second sentence is a required check, never a promised automatic task.
CHECKS = {
    "estimate-lineage-unavailable": ("주당 가치 연결 필요", "같은 계산에서 나온 주당 가치 결과를 연결해야 합니다."),
    "input-lineage-unavailable": ("계산 입력 연결 필요", "계산에 사용한 원천 입력과 결과의 연결을 확인해야 합니다."),
    "per-share-currency-unconfirmed": ("주당 기준·통화 확인 필요", "기업 전체 가치와 주당 가치를 구분하고 계산 통화를 맞춰야 합니다."),
    "source-revisions-incomplete": ("원천 자료 버전 확인 필요", "계산을 재현할 수 있도록 당시 사용한 원천 자료의 버전을 확인해야 합니다."),
    "valuation-clock-unavailable": ("평가·자료 기준 시점 확인 필요", "평가 시점과 자료 기준 시점이 분석 시점에 맞는지 확인해야 합니다."),
    "future-source-reference": ("분석 시점 이후 자료 제외 필요", "당시 알 수 있었던 자료만 사용해 계산해야 합니다."),
    "scenario-range-unavailable": ("평가 범위 확인 필요", "보수적·기준·낙관적 주당 가치의 값·순서·통화를 확인해야 합니다."),
    "estimate-calculation-mismatch": ("주당 가치 불일치 확인 필요", "표시할 주당 가치와 연결된 계산 결과를 일치시켜야 합니다."),
    "source-not-admitted": ("원천 자료 사용 제한 확인 필요", "판단 근거에서 제외된 자료의 품질·사용 제한 사유를 확인해야 합니다."),
    "non-positive-equity-value": ("양의 주당 가치 제시 불가", "계산된 주주가치가 양수가 아닌 원인과 입력·가정을 확인해야 합니다."),
}
UNKNOWN_CHECK = ("평가 사용 조건 확인 필요", "기록된 평가 제한 사유와 입력·가정의 검증 상태를 확인해야 합니다.")


def hold_details(model, packet):
    """Read the exact assessment; another model's completion cannot fill a gap."""
    fact = next((fact for fact in packet.get("facts", [])
        if fact.get("id") == model.get("assessmentFactId") and fact.get("kind") == "valuation-assessment"
        and fact.get("symbol") == packet.get("symbol")
        and (fact.get("payload") or {}).get("modelId") == model.get("modelId")
        and (fact.get("payload") or {}).get("modelVersion") == model.get("modelVersion")), {})
    assessment = fact.get("payload") or {}
    stage = {"calculated": "계산 완료", "blocked": "계산 미완료"}.get(
        assessment.get("calculationStatus"), "계산 상태 확인 필요")
    reasons = set(model.get("blockedReasons", []))
    checks = []
    if "valuation-not-admitted" in reasons:
        approval = assessment.get("modelApprovalState")
        if approval in {"shadow", "unapproved", "not-registered"}:
            checks.append(("모델 검증 필요", "판단에 사용하도록 허용되지 않은 모델입니다. 모델과 가정의 검증이 필요합니다."))
        elif approval == "reference-released":
            checks.append(("참고용 모델", "참고용으로 허용된 모델입니다. 판단에 사용하려면 별도의 검증이 필요합니다."))
        if assessment.get("inputEligibility") in {"partial", "unavailable"}:
            checks.append(("계산 자료 보완 필요", "계산에 필요한 입력 중 부족하거나 사용할 수 없는 항목을 확인·보완해야 합니다."))
        if not checks and not (fact.get("valuationDecisionEligible") is True
                and assessment.get("valuationDecisionEligible") is True and "estimate-lineage-unavailable" in reasons):
            # A missing estimate also trips the admission gate. Do not invent
            # a second model-validation problem when that is the only cause.
            checks.append(UNKNOWN_CHECK)
    for reason, check in CHECKS.items():
        if reason not in reasons:
            continue
        if reason == "estimate-lineage-unavailable" and assessment.get("modelApprovalState") in {"shadow", "unapproved", "not-registered", "reference-released"}:
            # Reference-only projections can intentionally withhold the public
            # estimate. Its absence does not prove a broken source connection.
            check = (check[0], "모델 검증을 마친 뒤 같은 계산의 주당 가치 결과가 연결되는지 확인해야 합니다.")
        checks.append(check)
    if reasons - set(CHECKS) - {"valuation-not-admitted"}:
        checks.append(UNKNOWN_CHECK)
    return {"stage": stage, "checks": list(dict.fromkeys(checks)) or [UNKNOWN_CHECK]}


def unread_value_lines(value, packet):
    inventory = packet.get("companyValueEvidence") or {}
    models = value.get("models", [])
    # Baseline exclusions may subsequently have been read and selected.
    included = {m.get("assessmentFactId") for m in models}
    omitted = {row.get("factId") for row in inventory.get("omitted", [])
               if row.get("reason") == "company-value-budget" and row.get("factId")
               and row["factId"] not in included}
    if omitted:
        return ["자료 일부 미조회" if models else "가치평가 자료 미조회",
                f"조회 용량 제한으로 평가 {len(omitted)}건을 이번 분석에서 제외했습니다. 해당 평가의 사용 가능 여부는 확인하지 못했습니다."]
    if models:
        return []
    if inventory.get("availableAssessments") == 0:
        return ["가치평가 기록 미확보 · 조회한 자료 범위에서 계산 기록을 찾지 못했습니다."]
    return ["가치평가 확인 전 · 이번 분석에 계산 기록이 연결되지 않아 상태를 확인할 수 없습니다."]
