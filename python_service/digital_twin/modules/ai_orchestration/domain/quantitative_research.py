"""Numeric business expectations stay scoped, prospective and explicitly assumed."""
from copy import deepcopy
from datetime import date, timedelta
import math

from digital_twin.modules.reasoning.contracts import content_hash

POLICY = "company-research-cycle-v1"
VERSION = "quantitative-business-checkpoint-v1"
FIELDS = ("factId", "metric", "targetPeriodStart", "targetPeriodEnd", "expectedLow", "expectedHigh",
          "expectationKind", "assumption", "evidenceIds", "meaning")


def enabled(packet):
    return packet.get("companyResearchPolicy") == POLICY


def schema(metrics, fact_ids):
    from .insight_schema import obj, choice, array
    text = {"type": "string", "minLength": 8, "maxLength": 500}
    return obj({"factId": choice(sorted({row["factId"] for row in metrics}) or [""]),
        "metric": choice(sorted({row["metric"] for row in metrics}) or [""]),
        "targetPeriodStart": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
        "targetPeriodEnd": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
        "expectedLow": {"type": "number"}, "expectedHigh": {"type": "number"},
        "expectationKind": choice(["ai-assumption"]), "assumption": text,
        "evidenceIds": {**array(choice(fact_ids)), "minItems": 1, "maxItems": 6}, "meaning": text})


def validate(check, baseline, metrics, packet, horizon, allowed_ids, text):
    if not isinstance(check, dict) or set(check) != set(FIELDS):
        raise ValueError("quantitative checkpoint fields mismatch")
    if check["expectationKind"] != "ai-assumption":
        raise ValueError("research range must remain an explicit AI assumption")
    numbers = (check["expectedLow"], check["expectedHigh"])
    if any(isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or abs(value) > 1e30 for value in numbers) or numbers[0] > numbers[1]:
        raise ValueError("invalid expected report range")
    try:
        start, end, before, base_start = (date.fromisoformat(value) for value in
            (check["targetPeriodStart"], check["targetPeriodEnd"], baseline["periodEnd"], baseline["periodStart"]))
        cutoff = date.fromisoformat(packet["capturedAt"][:10])
    except (ValueError, TypeError):
        raise ValueError("quantitative checkpoint requires exact reporting dates") from None
    if not (start < end and start > before and end > cutoff and end <= cutoff+timedelta(days=horizon)):
        raise ValueError("expected report must be a future non-overlapping period within the horizon")
    if abs((end-start).days-(before-base_start).days) > 8:
        raise ValueError("expected report duration must match the baseline")
    gap = (end-before).days
    if not (350 <= gap <= 380 or baseline["frequency"] == "quarterly" and 75 <= gap <= 106):
        raise ValueError("expectation requires next period or same season next year")
    latest = max(row["periodEnd"] for row in metrics.values()
                 if all(row[key] == baseline[key] for key in ("metric", "frequency", "basis")))
    if latest != baseline["periodEnd"]:
        raise ValueError("quantitative baseline must be the latest comparable report")
    refs = check["evidenceIds"]
    if (not isinstance(refs, list) or not 1 <= len(refs) <= 6 or check["factId"] not in refs
            or any(not isinstance(key, str) or key not in allowed_ids for key in refs)):
        raise ValueError("expected range requires selected thesis evidence")
    from .research_evidence_use import usable_fact
    facts = {fact["id"]: fact for fact in packet["facts"]}
    if not usable_fact(facts[check["factId"]], "reportedValues." + check["metric"]):
        raise ValueError("quantitative baseline is reference-only")
    if any(not usable_fact(facts[key]) or facts[key].get("evidenceCategory") not in {"company", "research", "valuation"}
           for key in refs):
        raise ValueError("expected range requires usable business evidence")
    row = {**deepcopy(check), "assumption": text(check["assumption"]), "meaning": text(check["meaning"]),
           "contractVersion": VERSION, "baseline": deepcopy(baseline), "qualification": "metric-observation-only",
           "comparison": "specified-future-period", "registeredCutoff": packet["capturedAt"]}
    row["checkpointId"] = content_hash(row)
    return row


INSTRUCTIONS = """기업 연구의 정량 계약:
새 사업 가설은 이미 확인된 사실, 가장 근거가 강한 해석, 대안 설명, 미해결 항목을 구분하세요. 일부 근거가 부족해도 확인된 범위의 설명까지 포기하지 마세요. 원래 질문의 완료 조건을 임의로 줄이지 마세요.
businessResearch.checkpoints는 {factId,metric,targetPeriodStart,targetPeriodEnd,expectedLow,expectedHigh,expectationKind,assumption,evidenceIds,meaning}입니다. 다음 보고기간 또는 다음 해 같은 기간의 원래 지표 값 범위를 기록합니다. expectedLow/expectedHigh는 증가율이 아니라 baseline과 같은 통화·단위의 절대값입니다. expectationKind는 ai-assumption이며 회사 가이던스나 시장 컨센서스로 표현하지 마세요. 수치 범위의 근거와 가정을 assumption 및 evidenceIds로 설명하세요. 정당화할 수 없는 범위는 만들지 말고 checkpoints=[]와 missingEvidence 및 구체적인 조사 질문을 남기세요.
기존 가설은 원래 contractVersion과 기준대로 평가합니다. 범위를 벗어난 실적은 예상과 불일치이지 투자 실패나 인과성 반증의 자동 판정이 아닙니다. 개정은 기존 가설을 덮어쓰지 않고 revise와 새 계약으로 연결하세요.
derivedMetricEvidence.cashFlowBridge는 이미 계산된 현금흐름 변화입니다. read_report의 cashFlowBridge로 입력·식·원천 버전과 재무 기여도를 확인할 수 있습니다. calculated일 때만 해당 숫자를 인용하세요. 이 분해는 영업현금흐름과 설비투자 기여만 설명합니다. 운전자본·제품·수요의 원인은 별도 원문으로 조사하세요. reportedFreeCashFlow와 calculatedFreeCashFlow가 다르면 정의 차이를 명시하고 같은 지표로 대체하지 마세요.
계산이 unavailable이면 requiredEvidence의 대상 보고기간·지표·누락 필드를 확인하세요. 이를 원래 질문의 questions 조사 요청과 완료 기준에 연결하고, 같은 자료 재조회나 막연한 추가 확인으로 끝내지 마세요. 기간 시작일이 없으면 보고 빈도에서 날짜를 만들어 넣지 말고 해당 기업의 원문 재무제표에서 기간·연결 범위·통화·영업현금흐름·설비투자를 함께 확보하세요. 앞서 등록한 동일 질문이 있으면 기존 질문을 이어가세요.
companyValueContext의 사용 가능한 평가·가정·민감도를 읽고 확인된 사업 변화가 기존 평가 가정을 바꾸는지 설명하세요. 불완전하거나 승인 대기인 계산은 가격 대비 저평가 근거로 쓰지 마세요. 없는 시나리오를 계산했다고 주장하지 마세요. 기존 계산에 없는 입력이 필요하면 지표·대상 기간·원천을 명시한 조사 질문으로 연결하세요.
summary에는 현재 근거로 답한 핵심 결론, comparison에는 이전 판단의 수정 이유, hypothesis에는 해석과 가정, counterEvidence에는 가장 강한 반론과 미해결 부분을 작성하세요. 핵심 질문을 닫거나 다음 조사·검증 조건으로 연결하세요. 이전 AI 설명을 새 증거로 쓰거나 발송을 위해 가설을 만들어서는 안 됩니다.
"""
