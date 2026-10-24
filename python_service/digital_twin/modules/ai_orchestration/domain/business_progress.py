"""Publication evidence for a changed business judgment, distinct from prices."""
from .insight_contract import resolve_ref
from .research_evidence_use import usable_fact

VERSION = "business-insight-v1"
IMPACTS = ("new", "supports", "weakens", "invalidates", "unchanged", "insufficient")


def enabled(packet):
    return packet.get("businessInsightPolicy") == VERSION


def focus(result):
    return result.get("judgmentChange", {}).get("kind", "") if enabled(result.get("input", {})) else ""


def business_fact(fact):
    return (fact.get("kind") in {"company-financial-state", "company-relationship", "fair-value-estimate"}
            or bool(fact.get("documentVerified"))
            or ((fact.get("questionSourceReference") or {}).get("sourceKind") == "structured-report"
                and usable_fact(fact)))


def business_ref(ref, fact):
    if not business_fact(fact):
        return False
    if (fact.get("kind") == "company-financial-state"
            or (fact.get("questionSourceReference") or {}).get("sourceKind") == "structured-report"):
        return ref["field"].startswith("reportedValues.") or any(ref["field"] in fact.get(key, {})
            for key in ("reportedValues", "derivedMetricEvidence", "comparisonEvidence"))
    if fact.get("documentVerified"):
        return ref["field"] in {"historicalDocumentReference.text", "questionSourceReference.text",
                                "officialDocumentText", "text", "body", "summary"}
    return ref["field"].split(".")[-1] not in {
        "id", "sourceAsOf", "asOf", "observedAt", "publishedAt", "periodEnd", "sourceRevision",
        "valuationDecisionEligible", "modelApprovalState", "currency", "provider", "source", "title", "label"}


def report_gaps(packet, facts=None, purpose=None):
    """Only a market explanation using older reports needs the newer periods.

    Original-period research keeps its own question contract. Unrelated news,
    crypto and price explanations do not acquire a financial-report dependency.
    """
    if not enabled(packet) or (purpose or packet.get("analysisPurpose")) == "original-question":
        return []
    rows = packet.get("facts", []) if facts is None else facts
    financials = [row for row in rows if row.get("kind") == "company-financial-state"]
    gaps = []
    for required in packet.get("businessEvidence", {}).get("reportRequirements", []):
        used = [row for row in financials if row.get("frequency") == required["frequency"]]
        if used and not any(row["id"] in required["factIds"] for row in used):
            gaps.append(required)
    return gaps


def validate_change(value, packet):
    from copy import deepcopy
    if (not isinstance(value, dict) or set(value) != {"kind", "impact", "reason", "evidence"}
            or value["kind"] not in {"business", "market", "none"} or value["impact"] not in IMPACTS
            or not isinstance(value["reason"], str) or not 8 <= len(value["reason"].strip()) <= 600
            or not isinstance(value["evidence"], list) or len(value["evidence"]) > 8):
        raise ValueError("judgment change requires a bounded purpose, impact and evidence")
    for ref in value["evidence"]:
        fact, _ = resolve_ref(packet, ref)
        if ref["period"] != "current" or not usable_fact(fact, ref["field"]):
            raise ValueError("judgment change requires usable current source evidence")
    return deepcopy(value)


def quality_errors(result):
    packet = result["input"]
    if not enabled(packet):
        return []
    try:
        change = validate_change(result.get("judgmentChange"), packet)
    except (ValueError, KeyError, TypeError):
        return ["판단 변화의 종류·영향·원천 근거가 완전하지 않습니다."]
    if not result.get("notification", {}).get("send"):
        return []
    if change["kind"] == "none" or change["impact"] in {"unchanged", "insufficient"}:
        return ["판단 진전이 없는 연구 상태는 투자 관찰로 발송할 수 없습니다."]
    errors = []
    if report_gaps(packet):
        errors.append("최신 관련 보고서를 읽지 않고 과거 재무만으로 판단을 완료했습니다.")
    refs = change["evidence"]
    cited = result.get("claimEvidence", {})
    if not refs or any(ref not in cited.get("summary", []) + cited.get("hypothesis", []) for ref in refs):
        errors.append("판단 변화 근거를 핵심 설명 또는 가설에 연결해야 합니다.")
    facts = {row["id"]: row for row in packet.get("facts", [])}
    if any(ref["factId"] not in result.get("evidenceIds", []) for ref in refs):
        errors.append("판단 변화 근거가 선택된 인용에 없습니다.")
    business_refs = [ref for ref in refs if business_ref(ref, facts[ref["factId"]])]
    if change["kind"] == "business":
        if not business_refs:
            errors.append("가격·계정 표시 변경이나 평가 대기는 사업 판단 진전의 근거가 아닙니다.")
        if report_gaps(packet, [facts[ref["factId"]] for ref in business_refs]):
            errors.append("선택한 최신 보고서를 실제 사업 판단 근거에도 연결해야 합니다.")
        for section in ("summary", "hypothesis"):
            if not any(ref in cited.get(section, []) for ref in business_refs):
                errors.append(section + ": 사업 판단을 바꾼 원천 근거가 필요합니다.")
        # A successful receipt is the comparison baseline. Rewording the same
        # cited scalar is not a new business observation, even after a price tick.
        baseline = {row["id"]: row for row in (packet.get("lastDeliveredNotification") or {}).get("facts", [])}
        novel = []
        for ref in business_refs:
            old = baseline.get(ref["factId"])
            try:
                _, before = resolve_ref(packet, {**ref, "period": "baseline"})
                _, after = resolve_ref(packet, ref)
                novel.append(before != after)
            except ValueError:
                novel.append(old is None or ref["field"] not in {"sourceAsOf", "asOf", "observedAt"})
        if novel and not any(novel):
            errors.append("지난 발송과 같은 사업 근거에 가격 변화만 더해 재발송할 수 없습니다.")
    elif any(facts.get(ref.get("factId"), {}).get("kind") in
             {"company-financial-state", "company-relationship", "fair-value-estimate"} for section in ("summary", "hypothesis")
             for ref in cited.get(section, []) if ref.get("period") == "current"):
        errors.append("시세 관찰의 핵심 설명에 미완료 사업 가설을 섞을 수 없습니다.")
    return errors


def review_errors(result, review):
    if not enabled(result["input"]):
        return []
    value = review.get("judgmentChange", {}) if isinstance(review, dict) else {}
    if (not isinstance(value, dict) or value.get("supported") is not True or not isinstance(value.get("reason"), str)
            or len(value["reason"].strip()) < 8):
        return ["새 근거가 판단에 미친 영향을 독립 검토에서 확인하지 못했습니다."]
    return []
