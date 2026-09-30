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
from copy import deepcopy
from datetime import datetime
from typing import Dict, Iterable, Mapping
from zoneinfo import ZoneInfo

from .company_report_evidence import amount, company_evidence_sections, evidence_material, profile_value
from .company_report_reading import build_company_report_reading, reading_sections
from .company_report_delivery import attach_delivery_brief
from .company_research_record import research_record_section


COMPANY_CHANGE_REPORT_VERSION = "company-change-report-v3"

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
    "fy1-revenue-consensus-growth-outlier": "내년 매출 전망치의 이례적인 증가율 검증",
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

PROVIDER_LABELS = {
    "data-go-kr-fsc": "금융위원회·공공데이터포털",
    "opendart": "OpenDART",
    "opendart-xbrl": "OpenDART XBRL",
    "kis": "KIS Open API",
    "kis-open-api": "KIS Open API",
    "yfinance": "yfinance",
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
    return ISSUE_LABELS.get(text, "수집 자료의 기간·단위·비교 가능성 추가 검증" if text.isascii() and "-" in text else text)


def _model_label(value: object) -> str:
    text = _text(value)
    return MODEL_LABELS.get(text, text or "평가 모델")


def _provider_label(value: object) -> str:
    text = _text(value)
    return PROVIDER_LABELS.get(text.lower(), text or "출처 미기록")


def _display_time(value: object) -> str:
    raw = _text(value)
    if not raw:
        return ""
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        return raw
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        if len(raw) == 8 and raw.isdigit():
            return raw[:4] + "-" + raw[4:6] + "-" + raw[6:]
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")


def _normalized_source_reference(row: Mapping[str, object]) -> Dict[str, object]:
    return {
        "providerId": _text(row.get("providerId")),
        "providerLabel": _provider_label(row.get("providerId")),
        "datasetId": _text(row.get("datasetId")),
        "revisionId": _text(row.get("revisionId")),
        "sourceAsOf": _text(row.get("sourceAsOf")),
        "sourceAsOfDisplay": _display_time(row.get("sourceAsOf")),
        "availability": _text(row.get("availability")),
    }


def _normalized_fact(row: Mapping[str, object], driver: Mapping[str, object] = None) -> Dict[str, object]:
    driver = _mapping(driver)
    metric = _text(driver.get("metric"))
    label = _text(row.get("label") or driver.get("label") or "기업 지표")
    if metric == "revenueGrowthPct" or "revenue-growth" in _text(row.get("driverId")):
        label = "매출 성장률"
    return {
        "factId": _text(row.get("driverId") or row.get("evidenceId") or row.get("label")),
        "label": label,
        "metric": metric,
        "value": _number(row.get("value")),
        "unit": _text(row.get("unit")),
        "period": _text(row.get("period")),
        "scope": _text(driver.get("scope")),
        "evidenceId": _text(row.get("evidenceId") or row.get("observationId")),
        "validationState": _text(driver.get("validationState")),
        "sourceClass": _text(driver.get("sourceClass")),
        "evidenceTier": _text(driver.get("evidenceTier")),
        "sourceReferences": [
            _normalized_source_reference(item)
            for item in _rows(driver.get("sourceReferences"))
        ],
        "sourceLocation": _mapping(driver.get("sourceLocation")),
    }


def _normalized_model(row: Mapping[str, object]) -> Dict[str, object]:
    return {
        "modelId": _text(row.get("modelId") or row.get("id")),
        "status": _text(row.get("status")),
        "fairValue": _number(row.get("fairValue")),
        "currency": _text(row.get("currency")),
        "decisionEligible": bool(row.get("decisionEligible")),
        "referenceOnly": bool(row.get("referenceOnly")),
        "reviewStatus": _text(row.get("reviewStatus")),
        "sourceBacked": bool(row.get("sourceBacked")),
        "evidenceBacked": bool(row.get("evidenceBacked")),
        "officialFinancialsReady": bool(row.get("officialFinancialsReady")),
        "blockedReasons": sorted(_unique_text(row.get("blockedReasons") or [])),
        "assumptionReviewState": _text(row.get("assumptionReviewState")),
        "assumptions": _rows(row.get("assumptions")),
        "sensitivity": _mapping(row.get("sensitivity")),
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
    drivers = {
        _text(item.get("driverId")): item
        for item in _rows(_mapping(analysis.get("companyDrivers")).get("drivers"))
        if _text(item.get("driverId"))
    }
    facts = [
        _normalized_fact(item, drivers.get(_text(item.get("driverId")), {}))
        for item in _rows(analysis.get("currentVerifiedFacts"))
    ]
    models = [_normalized_model(item) for item in _rows(analysis.get("valuationModels"))]
    facts.sort(key=lambda item: (item["factId"], item["period"], item["evidenceId"]))
    models.sort(key=lambda item: item["modelId"])
    return {
        "facts": facts,
        "models": models,
        "companyExposureState": _mapping(_mapping(analysis.get("companyDrivers")).get("exposureReadiness")),
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
        "companyEvidence": evidence_material(payload.get("companyReportEvidence")),
    }


def _fact_comparison(row):
    """Keep provenance in the report; cache refresh IDs alone are not news."""
    return {**{key: value for key, value in row.items() if key not in {"evidenceId", "sourceReferences"}},
            "sourceReferences": [{key: item.get(key) for key in ("providerId", "datasetId")} for item in _rows(row.get("sourceReferences"))]}


def _fact_changes(current: list[Dict[str, object]], previous: list[Dict[str, object]]) -> list[Dict[str, object]]:
    previous_by_id = {str(item.get("factId") or ""): item for item in previous if item.get("factId")}
    changes = []
    for item in current:
        fact_id = str(item.get("factId") or "")
        old = previous_by_id.get(fact_id)
        if old is None:
            changes.append({"kind": "new", "label": item.get("label"), "current": item})
            continue
        if _fact_comparison(old) != _fact_comparison(item):
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
        if old != item:
            changes.append({"kind": "changed", "modelId": model_id, "previous": old, "current": item})
    return changes


def _evidence_changes(current, previous):
    current, previous = _mapping(current), _mapping(previous)
    changes = []
    if current.get("profile") != previous.get("profile") or current.get("businessDescription") != previous.get("businessDescription"):
        changes.append("수집된 기업 소개 정보가 갱신됐습니다.")
    if current.get("financials") != previous.get("financials"):
        changes.append("보고기간별 재무 수치 또는 회계·출처 기준이 갱신됐습니다. 실적 표에서 해당 기간과 출처를 확인하세요.")
    old_documents = {_text(row.get("documentId")): row for row in _rows(previous.get("documents"))}
    for document in _rows(current.get("documents")):
        old = old_documents.get(_text(document.get("documentId")))
        if old != document:
            changes.append(("새로 수집한 문서: " if old is None else "문서·검증 상태 변경: ") + _text(document.get("title")) + " · " + _text(document.get("publishedAt")))
    if not changes and current != previous:
        changes.append("보고서에 포함된 문서 범위가 갱신됐습니다.")
    return changes


def build_company_change_report(
    valuation_payload: Mapping[str, object],
    previous_report: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Build one baseline or change report from the valuation read model."""

    payload = deepcopy(dict(valuation_payload or {}))
    previous = dict(previous_report or {})
    instrument = _mapping(payload.get("instrument"))
    current = _material_packet(payload)
    current["reportContractVersion"] = COMPANY_CHANGE_REPORT_VERSION
    previous_material = _mapping(previous.get("material"))
    material_fingerprint = _digest({**current, "facts": [_fact_comparison(row) for row in current["facts"]]})
    previous_fingerprint = _text(previous.get("materialFingerprint"))
    report_kind = "baseline" if not previous_fingerprint else "expanded" if previous.get("contractVersion") != COMPANY_CHANGE_REPORT_VERSION else (
        "unchanged" if previous_fingerprint == material_fingerprint else "change"
    )
    fact_changes = _fact_changes(current["facts"], _rows(previous_material.get("facts"))) if report_kind == "change" else []
    model_changes = _model_changes(current["models"], _rows(previous_material.get("models"))) if report_kind == "change" else []
    evidence_changes = _evidence_changes(current.get("companyEvidence"), previous_material.get("companyEvidence")) if report_kind == "change" else []
    symbol = _text(instrument.get("symbol") or instrument.get("sourceSymbol")).upper()
    name = _text(instrument.get("name") or symbol)
    generated_at = _text(payload.get("generatedAt"))
    report_id = "company-change-report:" + _digest({
        "accountId": _text(_mapping(payload.get("snapshot")).get("accountId")),
        "symbol": symbol,
        "materialFingerprint": material_fingerprint,
    })[:32]
    change_count = len(fact_changes) + len(model_changes) + len(evidence_changes)
    if report_kind == "baseline":
        headline = name + " 기준 보고서"
        summary = "처음 작성한 비교 기준입니다. 현재 실적·회사 발표·평가 조건을 정리했으며 기업 변화가 발생했다는 뜻은 아닙니다."
    elif report_kind == "expanded":
        headline = name + " 상세 보고서"
        summary = "기존 기준 보고서에 사업·실적·공시와 근거를 보강했습니다. 보고 범위 확장이며 새로운 기업 사건을 뜻하지 않습니다."
    elif report_kind == "change":
        headline = name + " 변화 보고서"
        summary = "이전 전달 보고서와 비교해 기업 자료 또는 평가 상태가 달라졌습니다."
    else:
        headline = name + " 변화 없음"
        summary = "마지막으로 전달한 보고서와 비교해 보고할 만한 기업 상태 변화가 없습니다."
    evidence = _mapping(payload.get("companyReportEvidence"))
    report = {
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
        "sourceCutoffDisplay": _display_time(_mapping(payload.get("snapshot")).get("generatedAt") or instrument.get("priceAsOf")),
        "headline": headline,
        "summary": summary,
        "materialFingerprint": material_fingerprint,
        "previousReportId": _text(previous.get("reportId")),
        "previousMaterialFingerprint": previous_fingerprint,
        "materialChange": report_kind == "change",
        "deliveryEligible": False,  # Set only by the financial change delivery policy.
        "changes": {
            "factChanges": fact_changes,
            "valuationChanges": model_changes,
            "evidenceChanges": evidence_changes,
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
        "evidence": evidence,
        "marketContext": {"price": _number(instrument.get("currentPrice")), "asOf": _text(instrument.get("priceAsOf")), "currency": _text(instrument.get("currency")), "metrics": _mapping(payload.get("marketMetrics"))},
        "valuationContext": {key: _mapping(payload.get("valuation")).get(key) for key in ("model", "fairValue", "earningsScenario", "multipleBand", "quality", "dcfReadiness", "impliedExpectations")},
        "material": current,
        "boundary": "이 보고서는 확인된 사실과 계산 상태를 요약하며 매수·매도 판단을 만들지 않습니다.",
    }
    report["reading"] = build_company_report_reading(payload, previous)
    report["aiInterpretationState"] = _text(report["reading"]["insight"].get("state") or "unavailable")
    if report["aiInterpretationState"] == "available":
        report["analysisMode"] = "source-bound-with-validated-interpretation"
    report["changeSummary"] = summary
    financial_reading = report["reading"]["financial"]
    if financial_reading:
        first = financial_reading[0]
        report["summary"] = first.get("headline") or first["meaning"]
    assessment_sections = _assessment_sections(report)
    changes_section = [section for section in assessment_sections if section["key"] == "changes"]
    if changes_section:
        business_changed = bool(fact_changes or evidence_changes)
        if not business_changed and report_kind == "change":
            report["summary"] = "기업 수치·문서의 변경 없이 평가 계산·검토 상태가 갱신됐습니다."
        changes_section[0]["paragraphs"] = [
            "기업 수치 또는 수집 문서가 바뀌었습니다. 변경 사실과 사업 영향의 해석을 구분해 확인하세요."
            if business_changed else "기업 수치·문서의 변경 없이 보고서 구성, 평가 계산 또는 검토 상태가 갱신됐습니다. 기업 실적이나 투자 판단이 달라졌다는 뜻은 아닙니다."
        ]
        transition = report["reading"]["interpretationChange"]
        changes_section[0]["rows"].append({
            "changed": "직전 보고서에 연결된 AI 해석의 내용이 달라졌습니다. 강화·약화 방향은 연결된 근거와 해석을 확인하세요.",
            "unchanged": "직전 보고서와 연결된 AI 해석의 핵심 내용이 같습니다.",
        }.get(transition, "같은 기준으로 비교할 검증된 해석이 없어 관점의 강화·약화를 판정하지 않았습니다."))
    report["researchRecord"] = _mapping(payload.get("companyResearchRecord"))
    report["sections"] = reading_sections(report["reading"]) + [research_record_section(report["researchRecord"])] + changes_section + company_evidence_sections(evidence)
    report["sections"].extend(section for section in assessment_sections if section["key"] != "changes")
    insight = report["reading"]["insight"]
    if insight.get("state") == "available" and not financial_reading:
        report["summary"] = insight["thesis"] + " · 분석 " + _display_time(insight.get("asOf"))
    report["brief"] = _reading_notification_content(report)
    attach_delivery_brief(report, previous)
    report["notificationContent"] = report["brief"]
    return report


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


def _model_state(model):
    if model.get("decisionEligible") and not model.get("referenceOnly"):
        return "평가 입력 사용 가능 · 매매 권고 아님"
    if model.get("reviewStatus") == "ai_applied_pending_review":
        return "모델 검토 대기 · 투자 판단에 사용하지 않음"
    if model.get("assumptionReviewState") == "required":
        return "장기 가정 검토 필요 · 참고 계산"
    return "참고 계산 · 투자 판단에 사용하지 않음"


def _financial_line(metric):
    value = amount(metric.get("value"), metric.get("currency"))
    if metric.get("comparisonLabel"):
        value += " (" + _text(metric["comparisonLabel"]) + ")"
    return _text(metric.get("label")) + ": " + value + " · " + _text(metric.get("basisLabel")) + " · " + _text(metric.get("provider"))


def _assessment_sections(report):
    currency = report.get("currency") or ""
    sections = []
    if report.get("reportKind") == "change":
        changed_rows = []
        for change in _rows(report["changes"].get("factChanges")):
            before, after = _mapping(change.get("previous")), _mapping(change.get("current"))
            changed_rows.append(_text(change.get("label")) + ": " + (_format_value(before, currency) if before else "직전 자료 없음") + " → " + _format_value(after, currency))
        for change in _rows(report["changes"].get("valuationChanges")):
            model = _mapping(change.get("current"))
            changed_rows.append(_model_label(model.get("modelId")) + ": 계산 입력·결과 또는 검토 상태 변경 · " + _model_state(model))
        changed_rows.extend(report["changes"].get("evidenceChanges") or [])
        sections.append({"key": "changes", "title": "이번에 달라진 점", "rows": changed_rows or ["수집된 재무 자료·기업 소개 또는 회사 발표 문서가 직전 보고서와 달라졌습니다. 아래 기간과 문서 기준을 확인하세요."]})
    market = _mapping(report.get("marketContext"))
    metrics = _mapping(market.get("metrics"))
    market_rows = []
    if market.get("price") is not None:
        market_rows.append("현재가 " + _format_price(market.get("price"), currency) + " · 관측 " + _display_time(market.get("asOf")))
    for key, label in (("currentPER", "현재 PER"), ("forwardPER", "예상 PER"), ("pbr", "PBR"), ("dividendYieldPct", "배당수익률")):
        value = _number(metrics.get(key))
        if value is not None:
            market_rows.append(label + f": {value:,.2f}" + ("%" if key == "dividendYieldPct" else "배"))
    sections.append({"key": "market", "title": "현재 가격과 시장 평가 지표", "rows": market_rows or ["확인 가능한 시장 지표가 없습니다."], "paragraphs": ["시세와 배수는 참고 지표이며 공시 실적의 보고기간과 다를 수 있습니다."]})
    models = _rows(_mapping(report.get("currentState")).get("valuationModels"))
    valuation = _mapping(report.get("valuationContext"))
    model_rows = [_model_label(model.get("modelId")) + ": " + _model_state(model) for model in models]
    dcf = _mapping(valuation.get("dcfReadiness"))
    financial = _mapping(dcf.get("financialEvidence"))
    if dcf:
        model_rows.append("현금흐름 할인 평가(DCF): " + ("평가 입력 사용 가능" if dcf.get("decisionEligible") else "추정 가정·필수 입력 검토 필요"))
    if financial:
        model_rows.append(
            "DCF에 선택된 공식 재무 입력 " + str(financial.get("officialMetricCount", 0)) + "/" + str(financial.get("requiredMetricCount", 0))
            + "개 · " + _text(financial.get("period")) + " · " + _text(financial.get("provider"))
        )
    if dcf.get("missingInputs"):
        model_rows.append("DCF 추가 확인: " + ", ".join(_unique_text(_issue_label(item) for item in dcf["missingInputs"])[:4]))
    reasons = _unique_text(reason for model in models for group in model.get("blockedReasons") or [] for reason in str(group).split(","))
    if reasons:
        model_rows.append("회계 기준·이익 기간·주당이익 기준·비교 배수의 정합성 검토가 남아 있습니다.")
    sections.append({"key": "valuation", "title": "가치평가와 주요 가정", "rows": model_rows or ["평가에 필요한 입력이 부족합니다."],
                     "models": [{**model, "label": _model_label(model.get("modelId")), "stateLabel": _model_state(model)} for model in models], "calculation": valuation,
                     "paragraphs": ["검토가 끝나지 않은 평가금액은 요약에서 제외합니다. 계산 내역은 상세에서 확인할 수 있으며 목표주가로 제시하지 않습니다."]})
    limits = _unique_text([*(_mapping(report.get("evidence")).get("limitations") or []), *(_issue_label(item) for item in report.get("uncertainties") or []), *(_issue_label(item) for item in report.get("nextChecks") or [])])
    sections.append({"key": "limitations", "title": "위험 검토에 필요한 자료와 확인 한계", "rows": limits,
                     "paragraphs": ["자료가 없다는 사실은 기업의 악재가 확인됐다는 뜻이 아닙니다. 아래 항목은 추가 확인 사항이며 자동 감시 등록을 의미하지 않습니다."]})
    return sections


def company_report_notification_content(report):
    """Plain structured fields are escaped once by the delivery renderer."""
    if isinstance(report.get("brief"), Mapping):
        return deepcopy(dict(report["brief"]))
    evidence = _mapping(report.get("evidence"))
    if _mapping(report.get("reading")).get("contractVersion"):
        return _reading_notification_content(report)
    sections = [
        {"title": section["title"], "rows": section.get("rows", [])[:3]}
        for section in _rows(report.get("sections")) if section.get("key") == "changes"
    ]
    profile = _mapping(evidence.get("profile"))
    profile_line = " · ".join(profile_value(profile.get(key)) for key in ("industry", "sector", "ceoName") if profile.get(key))
    if profile_line:
        sections.append({"title": "기업 개요", "rows": [profile_line + " · 수집 프로필 기준"]})
    reports = _rows(evidence.get("recentFinancials")) or _rows(evidence.get("annualFinancials"))
    if reports:
        financials = [item for item in _rows(reports[0].get("metrics")) if item.get("key") in {"revenue", "operatingIncome", "netIncome"}]
        sections.append({"title": "최근 실적", "rows": [_financial_line(item) for item in financials]})
    else:
        facts = _rows(_mapping(report.get("currentState")).get("facts"))
        sections.append({"title": "확인된 기업 지표", "rows": [
            _text(fact.get("label")) + ": " + _format_value(fact, report.get("currency", "")) + " · " + _text(fact.get("period"))
            + " · " + ", ".join(_text(source.get("providerLabel")) for source in _rows(fact.get("sourceReferences")))
            for fact in facts[:3]
        ] or ["비교 가능한 재무 자료가 부족합니다."]})
    reading = next((item for item in _rows(report.get("sections")) if item.get("key") == "financialReading"), {})
    if reading.get("rows"):
        sections.append({"title": "실적 읽기", "rows": reading["rows"][:2]})
    documents = _rows(evidence.get("documents"))
    if documents:
        sections.append({"title": "최근 공시·회사 발표", "rows": [item["publishedAt"] + " · " + item["title"] + " · " + item["useLabel"] for item in documents[:2]]})
    models = _rows(_mapping(report.get("currentState")).get("valuationModels"))
    sections.append({"title": "가치평가 상태", "rows": [_model_label(item.get("modelId")) + ": " + _model_state(item) for item in models[:3]] or ["필수 입력 확인 필요"]})
    checks = _unique_text(_issue_label(item) for item in report.get("nextChecks") or [])
    if checks:
        sections.append({"title": "추가 확인", "rows": checks[:3]})
    coverage = _mapping(evidence.get("coverage"))
    sections.append({"title": "상세 보고서", "rows": [
        "자료 확보 수준: " + _text(coverage.get("label") or "준비 중"),
        "연간 " + str(coverage.get("annualPeriods", 0)) + "개 기간 · 최근 " + str(coverage.get("recentPeriods", 0)) + "개 보고자료 · 공시·IR " + str(coverage.get("documents", 0)) + "건의 근거와 계산 내역을 담았습니다.",
        _text(report.get("boundary")),
    ]})
    return {"kind": "company-change-report", "subject": {"symbol": report.get("symbol"), "name": report.get("name")},
            "summary": _text(report.get("summary")), "sections": sections}


def _reading_notification_content(report):
    """Select a compact view of captured evidence without shortening AI claims."""
    reading = _mapping(report.get("reading"))
    financial = _rows(reading.get("financial"))[:2]
    valuation = _rows(reading.get("valuation"))
    insight = _mapping(reading.get("insight"))
    lead = next(iter(financial), {})
    facts = [lead["meaning"], lead.get("briefFact", lead["fact"])] if lead else ["손익의 원인을 설명할 비교 가능한 재무 근거가 아직 없습니다."]
    if len(financial) > 1 and lead.get("components") and financial[1].get("components"):
        facts.append(financial[1].get("briefFact", financial[1]["fact"]))
    value = next((card for card in valuation if card.get("key") == "price-requirements"), None)
    if value:
        value_text = value["fact"] + " · 조건부 역산이며 실제 시장 기대를 관측한 값이 아닙니다."
        if value.get("limitations"):
            value_text += " " + value["limitations"][0]
    else:
        value = next(iter(valuation), {})
        value_text = (value.get("fact", "") + " · 가정에 따른 계산 범위이며 확정 가치가 아닙니다."
                      if value.get("calculationEligible") else "평가 가정 검토 전으로 저평가·고평가 판단을 보류합니다.")
    if lead.get("valuationMeaning"):
        value_text = lead["valuationMeaning"]
    check = next((card.get("briefCheck") or item for card in financial for item in card.get("nextChecks", [])), "비교 가능한 재무 자료를 먼저 확보해야 합니다.")
    if insight.get("state") == "available" and not lead:
        # A revision-bound insight can discuss price action, not these accounts.
        # Never let it replace the meaning of reported financial numbers.
        facts = [insight["mechanism"], insight["meaning"],
                 *("반대 근거·한계: " + risk for risk in insight.get("risks", []))]
        check = insight["invalidation"]
    return {"kind": "company-change-report", "presentation": "company-report-brief-v1",
            "subject": {"symbol": report.get("symbol"), "name": report.get("name")},
            "summary": _text(report.get("summary")),
            "sections": [
                {"title": "이 숫자의 의미", "rows": facts},
                {"title": "가치 판단", "rows": [value_text]},
                {"title": "다음 확인", "rows": [check]},
            ]}


def render_company_change_report(report: Mapping[str, object]) -> str:
    """Render a standalone preview; production uses the same plain sections."""
    content = company_report_notification_content(report)
    lines = ["<b>📊 " + html.escape(_text(report.get("headline")), quote=False) + "</b>", "", html.escape(content["summary"], quote=False)]
    for section in content["sections"]:
        lines.extend(["", "<b>" + html.escape(section["title"], quote=False) + "</b>"])
        lines.extend("• " + html.escape(_text(row), quote=False) for row in section.get("rows") or [])
    lines.extend(["", "<i>자료 확인 " + html.escape(_text(report.get("sourceCutoffDisplay")) or _display_time(report.get("sourceCutoffAt")), quote=False) + "</i>"])
    return "\n".join(lines)


__all__ = [
    "COMPANY_CHANGE_REPORT_VERSION",
    "build_company_change_report",
    "render_company_change_report",
]
