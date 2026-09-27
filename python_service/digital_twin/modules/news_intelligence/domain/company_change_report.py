"""Deterministic, source-bound company change reports.

The report is a factual read model.  It never creates an investment action and
does not ask an LLM to fill missing fields.  Graph-backed investment judgement
continues through the existing ``investmentInsight`` path.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
from typing import Dict, Iterable, Mapping


COMPANY_CHANGE_REPORT_VERSION = "company-change-report-v1"

ISSUE_LABELS = {
    "financial-statements": "재무제표 기간 자료",
    "executive-governance": "경영진·지배구조 자료",
    "valuation-metrics": "PER·PBR 등 시장 평가 지표",
    "capital-structure": "발행주식수·부채 등 자본구조 자료",
    "company-currency-exposure-missing": "기업의 매출·비용 통화 노출",
    "company-debt-rate-exposure-missing": "기업의 고정·변동금리 부채 구조",
    "verified-event-missing": "가격 변화와 연결할 공식 사건",
    "verified-event-source-missing": "사건을 확인할 원문 출처",
    "precise-event-clock-missing": "사건이 공개된 정확한 시각",
    "adjusted-session-price-window-missing": "기업행동을 조정한 가격 구간",
    "market-sector-benchmarks-missing": "같은 시각의 시장·업종 비교",
    "alternative-explanations-not-checked": "시장·업종 등 다른 원인 점검",
    "independent-source-family-missing": "독립적으로 확인할 원문 출처",
    "dcf-assumption-review-required": "DCF 장기 가정 검토",
    "official-financial-evidence-incomplete": "공식 공시 재무 입력 확인",
    "official-financial-metric-coverage-incomplete": "공식 공시의 DCF 필수 재무 항목",
    "official-financial-source-revision-missing": "공식 재무 원문의 정확한 revision",
    "valuation-models-materially-disagree": "평가 모델 간 적정가 차이 검토",
    "valuation-model-currency-conflict": "평가 모델 간 통화 단위 확인",
    "unapproved-assumptions-present": "검토가 끝나지 않은 장기 DCF 가정",
    "official-ir-source-not-ready": "공식 IR 원문 수집 상태",
}

MODEL_LABELS = {
    "semiconductor-cycle-earnings": "반도체 이익·업황 방식",
    "growth-quality-earnings": "성장주 이익 방식",
    "bitcoin-treasury-nav": "비트코인 보유가치 방식",
    "preferred-income-yield": "배당수익률 방식",
    "generic-fundamental-earnings": "기업 이익 방식",
    "driver-fcff-dcf": "사업 변수 현금흐름 방식",
    "current-price-reference": "현재가 참고 방식",
}


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _number(value: object):
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _mapping(value: object) -> Dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: object) -> list[Dict[str, object]]:
    return [dict(item) for item in value or [] if isinstance(item, Mapping)]


def _unique_text(values: Iterable[object]) -> list[str]:
    return list(dict.fromkeys(_text(value) for value in values if _text(value)))


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _issue_label(value: object) -> str:
    text = _text(value)
    return ISSUE_LABELS.get(text, text)


def _model_label(value: object) -> str:
    text = _text(value)
    return MODEL_LABELS.get(text, text or "평가 모델")


def _normalized_fact(row: Mapping[str, object]) -> Dict[str, object]:
    return {
        "factId": _text(row.get("driverId") or row.get("evidenceId") or row.get("label")),
        "label": _text(row.get("label") or "기업 지표"),
        "value": _number(row.get("value")),
        "unit": _text(row.get("unit")),
        "period": _text(row.get("period")),
        "evidenceId": _text(row.get("evidenceId") or row.get("observationId")),
    }


def _normalized_model(row: Mapping[str, object]) -> Dict[str, object]:
    return {
        "modelId": _text(row.get("modelId") or row.get("id")),
        "status": _text(row.get("status")),
        "fairValue": _number(row.get("fairValue")),
        "currency": _text(row.get("currency")),
        "decisionEligible": bool(row.get("decisionEligible")),
        "referenceOnly": bool(row.get("referenceOnly")),
    }


def _material_packet(payload: Mapping[str, object]) -> Dict[str, object]:
    analysis = _mapping(payload.get("investmentAnalysis"))
    valuation = _mapping(payload.get("valuation"))
    readiness = _mapping(analysis.get("dataReadiness") or valuation.get("dataReadiness"))
    financials = _mapping(readiness.get("officialFinancials"))
    consensus = _mapping(readiness.get("consensus"))
    dcf = _mapping(readiness.get("dcf"))
    agreement = _mapping(readiness.get("modelAgreement") or analysis.get("modelAgreement"))
    cause = _mapping(analysis.get("priceExplanation"))
    facts = [_normalized_fact(item) for item in _rows(analysis.get("currentVerifiedFacts"))]
    models = [_normalized_model(item) for item in _rows(analysis.get("valuationModels"))]
    facts.sort(key=lambda item: (item["factId"], item["period"], item["evidenceId"]))
    models.sort(key=lambda item: item["modelId"])
    return {
        "facts": facts,
        "models": models,
        "companyDriverFingerprint": _text(_mapping(analysis.get("companyDrivers")).get("materialFingerprint")),
        "priceExplanation": {
            "eventId": _text(cause.get("eventId") or cause.get("observationId")),
            "claimStrength": _text(cause.get("claimStrength")),
            "blockingReasons": sorted(_unique_text(cause.get("blockingReasons") or [])),
        },
        "readiness": {
            "status": _text(readiness.get("status")),
            "officialFinancials": {
                "status": _text(financials.get("status")),
                "period": _text(financials.get("period")),
                "officialMetricCount": int(_number(financials.get("officialMetricCount")) or 0),
                "requiredMetricCount": int(_number(financials.get("requiredMetricCount")) or 0),
            },
            "consensus": {
                "status": _text(consensus.get("status")),
                "currency": _text(consensus.get("currency")),
            },
            "dcf": {
                "status": _text(dcf.get("status")),
                "assumptionReviewState": _text(dcf.get("assumptionReviewState")),
            },
            "modelAgreement": {
                "status": _text(agreement.get("status")),
                "blockingReasons": sorted(_unique_text(agreement.get("blockingReasons") or [])),
            },
        },
        "missingData": sorted(_unique_text(payload.get("missingData") or [])),
        "nextChecks": sorted(_unique_text(analysis.get("nextChecks") or [])),
    }


def _fact_changes(current: list[Dict[str, object]], previous: list[Dict[str, object]]) -> list[Dict[str, object]]:
    previous_by_id = {str(item.get("factId") or ""): item for item in previous if item.get("factId")}
    changes = []
    for item in current:
        fact_id = str(item.get("factId") or "")
        old = previous_by_id.get(fact_id)
        if old is None:
            changes.append({"kind": "new", "label": item.get("label"), "current": item})
            continue
        if any(old.get(key) != item.get(key) for key in ("value", "unit", "period", "evidenceId")):
            changes.append({"kind": "changed", "label": item.get("label"), "previous": old, "current": item})
    return changes


def _model_changes(current: list[Dict[str, object]], previous: list[Dict[str, object]]) -> list[Dict[str, object]]:
    previous_by_id = {str(item.get("modelId") or ""): item for item in previous if item.get("modelId")}
    changes = []
    for item in current:
        model_id = str(item.get("modelId") or "")
        old = previous_by_id.get(model_id)
        if old is None:
            changes.append({"kind": "new", "modelId": model_id, "current": item})
            continue
        if any(old.get(key) != item.get(key) for key in ("status", "fairValue", "currency", "decisionEligible", "referenceOnly")):
            changes.append({"kind": "changed", "modelId": model_id, "previous": old, "current": item})
    return changes


def build_company_change_report(
    valuation_payload: Mapping[str, object],
    previous_report: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Build one baseline or change report from the valuation read model."""

    payload = dict(valuation_payload or {})
    previous = dict(previous_report or {})
    instrument = _mapping(payload.get("instrument"))
    current = _material_packet(payload)
    previous_material = _mapping(previous.get("material"))
    material_fingerprint = _digest(current)
    previous_fingerprint = _text(previous.get("materialFingerprint"))
    report_kind = "baseline" if not previous_fingerprint else (
        "unchanged" if previous_fingerprint == material_fingerprint else "change"
    )
    fact_changes = _fact_changes(current["facts"], _rows(previous_material.get("facts")))
    model_changes = _model_changes(current["models"], _rows(previous_material.get("models")))
    symbol = _text(instrument.get("symbol") or instrument.get("sourceSymbol")).upper()
    name = _text(instrument.get("name") or symbol)
    generated_at = _text(payload.get("generatedAt"))
    report_id = "company-change-report:" + _digest({
        "accountId": _text(_mapping(payload.get("snapshot")).get("accountId")),
        "symbol": symbol,
        "materialFingerprint": material_fingerprint,
    })[:32]
    change_count = len(fact_changes) + len(model_changes)
    if report_kind == "baseline":
        headline = name + " 기준 보고서"
        summary = "현재 확인 가능한 기업 상태와 가치평가 자료를 비교 기준으로 저장했습니다."
    elif report_kind == "change":
        headline = name + " 변화 보고서"
        summary = (
            "이전 전달 보고서 이후 핵심 사실 또는 평가 상태 " + str(max(1, change_count)) + "건이 달라졌습니다."
        )
    else:
        headline = name + " 변화 없음"
        summary = "마지막으로 전달한 보고서와 비교해 보고할 만한 기업 상태 변화가 없습니다."
    return {
        "contractVersion": COMPANY_CHANGE_REPORT_VERSION,
        "reportId": report_id,
        "reportKind": report_kind,
        "reportRole": "factual-reference",
        "analysisMode": "deterministic-source-bound",
        "aiInterpretationState": "separate-graph-backed-investment-insight",
        "accountId": _text(_mapping(payload.get("snapshot")).get("accountId")),
        "symbol": symbol,
        "name": name,
        "market": _text(instrument.get("market")),
        "currency": _text(instrument.get("currency")),
        "generatedAt": generated_at,
        "sourceCutoffAt": _text(_mapping(payload.get("snapshot")).get("generatedAt") or instrument.get("priceAsOf")),
        "headline": headline,
        "summary": summary,
        "materialFingerprint": material_fingerprint,
        "previousReportId": _text(previous.get("reportId")),
        "previousMaterialFingerprint": previous_fingerprint,
        "materialChange": report_kind == "change",
        "deliveryEligible": report_kind in {"baseline", "change"},
        "changes": {
            "factChanges": fact_changes,
            "valuationChanges": model_changes,
            "count": change_count,
        },
        "currentState": {
            "facts": current["facts"],
            "valuationModels": current["models"],
            "priceExplanation": current["priceExplanation"],
            "dataReadiness": current["readiness"],
        },
        "uncertainties": current["missingData"],
        "nextChecks": current["nextChecks"],
        "sources": _rows(payload.get("sources")),
        "material": current,
        "boundary": "이 보고서는 확인된 사실과 계산 상태를 요약하며 매수·매도 판단을 만들지 않습니다.",
    }


def _format_value(row: Mapping[str, object], currency: str = "") -> str:
    value = _number(row.get("value"))
    if value is None:
        return "값 확인 필요"
    unit = _text(row.get("unit"))
    suffix = "%" if unit == "percent" else (" " + unit if unit and unit != "reported-currency" else (" " + currency if currency else ""))
    return (f"{value:,.2f}".rstrip("0").rstrip(".") + suffix).strip()


def _format_price(value: object, currency: str = "") -> str:
    number = _number(value)
    if number is None:
        return "계산 보류"
    code = _text(currency).upper()
    formatted = f"{number:,.0f}" if code == "KRW" else f"{number:,.2f}".rstrip("0").rstrip(".")
    return (formatted + "원") if code == "KRW" else (("$" + formatted) if code == "USD" else (formatted + (" " + code if code else "")))


def render_company_change_report(report: Mapping[str, object]) -> str:
    """Render a compact Telegram-safe HTML representation."""

    values = dict(report or {})
    currency = _text(values.get("currency"))
    changes = _mapping(values.get("changes"))
    current = _mapping(values.get("currentState"))
    facts = _rows(current.get("facts"))
    models = _rows(current.get("valuationModels"))
    lines = [
        "<b>📊 " + html.escape(_text(values.get("headline")) or "기업 변화 보고서", quote=False) + "</b>",
        "<code>" + html.escape(_text(values.get("symbol")), quote=False) + "</code>",
        "",
        "<b>한눈에 보기</b>",
        "• " + html.escape(_text(values.get("summary")), quote=False),
    ]
    changed_rows = _rows(changes.get("factChanges")) + _rows(changes.get("valuationChanges"))
    if changed_rows:
        lines.extend(["", "<b>이번에 달라진 점</b>"])
        for change in changed_rows[:6]:
            before = _mapping(change.get("previous"))
            after = _mapping(change.get("current"))
            label = _text(change.get("label") or after.get("label")) or _model_label(change.get("modelId") or after.get("modelId"))
            if after.get("modelId"):
                before_text = _format_price(before.get("fairValue"), before.get("currency") or currency) if before else "이전 자료 없음"
                after_text = _format_price(after.get("fairValue"), after.get("currency") or currency)
            else:
                before_text = _format_value(before, currency) if before else "이전 자료 없음"
                after_text = _format_value(after, currency)
            lines.append("• " + html.escape(label + ": " + before_text + " → " + after_text, quote=False))
    if facts:
        lines.extend(["", "<b>확인된 기업 상태</b>"])
        for fact in facts[:5]:
            detail = " · " + _text(fact.get("period")) if _text(fact.get("period")) else ""
            lines.append("• " + html.escape(_text(fact.get("label")) + ": " + _format_value(fact, currency) + detail, quote=False))
    if models:
        lines.extend(["", "<b>가치평가 상태</b>"])
        for model in models[:4]:
            role = "판단 입력 가능" if model.get("decisionEligible") else "참고용"
            lines.append("• " + html.escape(
                _model_label(model.get("modelId")) + ": "
                + _format_price(model.get("fairValue"), model.get("currency") or currency) + " · " + role,
                quote=False,
            ))
    uncertainties = _unique_text(values.get("uncertainties") or [])
    if uncertainties:
        lines.extend(["", "<b>확인 한계</b>"])
        lines.extend("• " + html.escape(_issue_label(item), quote=False) for item in uncertainties[:5])
    next_checks = _unique_text(values.get("nextChecks") or [])
    if next_checks:
        lines.extend(["", "<b>다음 확인</b>"])
        lines.extend("• " + html.escape(_issue_label(item), quote=False) for item in next_checks[:5])
    source_names = _unique_text(item.get("provider") for item in _rows(values.get("sources")))
    if source_names:
        lines.extend(["", "<b>출처</b>", "• " + html.escape(", ".join(source_names), quote=False)])
    lines.extend([
        "",
        "<i>" + html.escape(_text(values.get("boundary")), quote=False) + "</i>",
        "<i>기준 " + html.escape(_text(values.get("sourceCutoffAt") or values.get("generatedAt")), quote=False) + "</i>",
    ])
    return "\n".join(lines).strip()


__all__ = [
    "COMPANY_CHANGE_REPORT_VERSION",
    "build_company_change_report",
    "render_company_change_report",
]
