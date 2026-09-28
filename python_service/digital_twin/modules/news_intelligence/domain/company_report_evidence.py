"""Bounded, source-preserving evidence for a company research report.

This is a reading document, not an investment synthesis or an action scorer.
Reported periods and per-metric provenance stay separate across providers.
"""

from __future__ import annotations

import math
import re
from typing import Mapping
from urllib.parse import urlsplit

from .financial_reporting import financial_report_contract_assessment, reporting_period_end


METRICS = (
    ("revenue", "매출액"), ("operatingIncome", "영업이익"),
    ("netIncome", "당기순이익"), ("operatingCashFlow", "영업현금흐름"),
    ("capitalExpenditure", "설비투자 현금흐름"), ("freeCashFlow", "잉여현금흐름"),
    ("cash", "현금 및 현금성자산"), ("totalDebt", "이자부채"),
    ("totalAssets", "자산총계"), ("totalLiabilities", "부채총계"), ("equity", "자본총계"),
)
DURATIONS = {"annual": "연간", "quarterly": "단일 분기", "year-to-date": "연초부터 누적", "instant": "기말 잔액"}
SCOPES = {"CFS": "연결", "OFS": "별도", "provider-reported": "공급자 보고 기준"}
PROFILE_LABELS = {"Semiconductors": "반도체", "Technology": "기술", "Medical Devices": "의료기기",
                  "Biotechnology": "생명공학", "Healthcare": "헬스케어", "Financial Services": "금융"}


def text(value):
    return " ".join(str(value or "").split())


def mapping(value):
    return dict(value) if isinstance(value, Mapping) else {}


def rows(value):
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, (list, tuple)) else []


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (ValueError, TypeError):
        return None


def profile_value(value):
    return PROFILE_LABELS.get(text(value), text(value))


def safe_url(value):
    value = text(value)
    try:
        parts = urlsplit(value)
        if parts.scheme in {"http", "https"} and parts.hostname and not parts.username and not parts.password and not re.search(r"[\s<>]", value):
            return value
    except ValueError:
        pass
    return ""


def amount(value, currency=""):
    parsed = number(value)
    if parsed is None:
        return "자료 없음"
    unit, divisor = ("조원", 1e12) if currency == "KRW" and abs(parsed) >= 1e12 else ("억원", 1e8) if currency == "KRW" and abs(parsed) >= 1e8 else (currency or "통화 미확인", 1)
    return f"{parsed / divisor:,.2f}".rstrip("0").rstrip(".") + (" " + unit if unit else "")


def date_label(value):
    parsed = reporting_period_end(value)
    return parsed.isoformat() if parsed else text(value)


def comparison_label(comparison):
    current, previous = number(comparison.get("currentValue")), number(comparison.get("previousValue"))
    growth = number(comparison.get("changePct"))
    if current is None or previous is None or growth is None:
        return ""
    if previous < 0 <= current:
        return "전년 동기 적자 → 흑자" if current > 0 else "전년 동기 적자 → 손익 0"
    if previous > 0 > current:
        return "전년 동기 흑자 → 적자"
    if previous < 0 and current < 0:
        return f"전년 동기 대비 손실 규모 {abs(growth):.2f}% " + ("감소" if growth >= 0 else "증가")
    return f"전년 동기 대비 {growth:+.2f}%"


def _references(report, company, provider):
    references = rows(report.get("sourceReferences")) or rows(company.get("sourceReferences"))
    key = provider.lower()
    prefix = ("opendart.xbrl_facts" if "xbrl" in key else "opendart.company_facts" if "dart" in key
              else "sec." if "sec" in key else "yfinance." if "yfinance" in key
              else "public-data." if "금융위원회" in key else "")
    return [{key: item.get(key) for key in ("datasetId", "revisionId", "sourceAsOf") if item.get(key)}
            for item in references if prefix and text(item.get("datasetId")).startswith(prefix)]


def _financial_row(row, company):
    assessment = financial_report_contract_assessment(row)
    if not assessment.get("eligible"):
        return {}
    contract = mapping(row.get("reportContract"))
    metrics = []
    for field, label in METRICS:
        value = number(row.get(field))
        if value is None:
            continue
        provenance = mapping(mapping(row.get("metricProvenance")).get(field))
        provider = text(provenance.get("provider"))
        # A row-level provider may not label a metric merged from another source.
        if not provider:
            continue
        currency = text(provenance.get("currency"))
        period = date_label(provenance.get("period") or assessment["period"])
        duration = text(provenance.get("durationBasis"))
        scope = text(provenance.get("scope"))
        references = _references(contract, company, provider)
        source_url = safe_url(provenance.get("sourceUrl"))
        metric = {
            "key": field, "label": label, "value": value, "currency": currency,
            "period": period, "durationBasis": duration, "scope": scope,
            "basisLabel": " · ".join(filter(None, [period, DURATIONS.get(duration, "기간 기준 미확인"), SCOPES.get(scope, scope or "연결·별도 미확인")])),
            "provider": provider, "sourceUrl": source_url,
            "sourceReferences": references, "sourceMetric": text(provenance.get("metric") or provenance.get("tag")),
            "official": bool(provenance.get("official")), "derived": bool(provenance.get("derived")),
            "sourceLabel": "공시 원문" if provenance.get("official") and source_url else "공식 API 집계" if provenance.get("official") else "보조 공급자",
            "sourceDocumentId": text(provenance.get("receiptNo") or provenance.get("accessionNumber")),
        }
        if field == "cash" and not provenance.get("official"):
            metric["label"] = "현금 관련 자산 (공급자 정의)"
        if field == "totalDebt":
            metric["label"] = "차입금·사채 (공급자 집계)"
        growth_field = {"revenue": "revenueGrowthPct", "operatingIncome": "operatingIncomeGrowthPct", "netIncome": "netIncomeGrowthPct"}.get(field)
        comparison = mapping(mapping(row.get("comparisonEvidence")).get(growth_field))
        comparison_source = mapping(comparison.get("source"))
        previous_source = mapping(comparison.get("previousSource"))
        same_basis = all(
            text(comparison_source.get(key)) == text(provenance.get(key))
            and text(previous_source.get(key)) == text(provenance.get(key))
            and text(provenance.get(key))
            for key in ("provider", "currency", "scope", "durationBasis")
        )
        if (comparison.get("status") == "verified-comparable"
                and comparison.get("basis") == "year-over-year"
                and number(comparison.get("currentValue")) == value
                and date_label(comparison.get("currentPeriod")) == period
                and same_basis):
            metric["comparison"] = {key: comparison.get(key) for key in ("basis", "currentPeriod", "previousPeriod", "currentValue", "previousValue", "changePct", "source", "previousSource")}
            metric["comparisonLabel"] = comparison_label(comparison)
        metrics.append(metric)
    return {"period": assessment["period"], "frequency": assessment["frequency"], "observationId": assessment["observationId"], "metrics": metrics} if metrics else {}


def _documents(symbol, signals):
    ir = mapping(mapping(signals.get("issuerIrDocuments")).get(symbol))
    dart = mapping(mapping(signals.get("dartDisclosures")).get(symbol))
    selected, seen = [], set()
    for kind, items in (("기업 IR", rows(ir.get("items"))), ("공시", rows(dart.get("items")))):
        for item in items:
            url = safe_url(item.get("resolvedUrl") or item.get("url"))
            title = text(item.get("title") or item.get("reportName"))
            identity = text(item.get("documentId") or item.get("receiptNo") or url)
            if not title or not identity or identity in seen:
                continue
            seen.add(identity)
            verified = item.get("documentVerified") is True or item.get("documentState") == "document-verified"
            body = text(item.get("officialDocumentText") or item.get("documentText")) if verified else ""
            # Short, attributed excerpts retain the company's own wording.
            excerpt = body[:400].rsplit(" ", 1)[0] if len(body) > 400 else body
            selected.append({
                "documentId": identity, "kind": kind,
                "title": title, "publishedAt": date_label(item.get("publishedAt") or item.get("receiptDate")),
                "url": url, "bodyVerified": verified, "excerpt": excerpt,
                "sourceRevision": text(item.get("documentHash")) or text((rows(item.get("sourceReferences")) or [{}])[0].get("revisionId")),
                "useLabel": ("회사 발표 · 본문 확인" if kind == "기업 IR" else "공시 문서 · 본문 확인") if verified else "문서 목록 확인 · 본문 미검증",
            })
    return sorted(selected, key=lambda row: row["publishedAt"], reverse=True)[:6]


def build_company_report_evidence(symbol, external_signals):
    """Select existing collected evidence; make no external calls or judgements."""
    signals = mapping(external_signals)
    symbol = text(symbol).upper()
    company = mapping(mapping(signals.get("companyKnowledge")).get(symbol))
    profile = mapping(company.get("profile"))
    yf = mapping(mapping(signals.get("yfinanceData")).get(symbol))
    info = mapping(yf.get("info"))
    financials = mapping(company.get("financials"))
    annual_by_period = {}
    candidates = rows(financials.get("annual")) + rows(company.get("valuationFinancialCandidates"))
    for row in candidates:
        normalized = _financial_row(row, company)
        if not normalized or normalized["frequency"] != "annual":
            continue
        # Prefer attributable primary statements over an aggregate summary
        # for the same period, without merging metrics across alternatives.
        score = (
            sum(bool(item["official"] and item["sourceUrl"]) for item in normalized["metrics"]),
            sum(item["official"] for item in normalized["metrics"]),
            len(normalized["metrics"]),
        )
        old = annual_by_period.get(normalized["period"])
        if not old or score > old[0]:
            annual_by_period[normalized["period"]] = (score, normalized)
    annual = [annual_by_period[period][1] for period in sorted(annual_by_period, reverse=True)[:3]]
    recent = []
    for frequency in ("interim", "quarterly"):
        for row in sorted(rows(financials.get(frequency)), key=lambda row: date_label(row.get("periodEnd") or row.get("period")), reverse=True):
            normalized = _financial_row(row, company)
            if normalized:
                recent.append(normalized)
                break
    recent.sort(key=lambda item: item["period"], reverse=True)
    documents = _documents(symbol, signals)
    business = text(info.get("longBusinessSummary"))
    company_profile = {key: profile.get(key) for key in ("companyName", "ceoName", "industry", "sector", "website", "employeeCount", "auditFirm", "auditOpinion") if profile.get(key) not in (None, "")}
    coverage_state = (
        "sufficient" if len(annual) >= 3 and recent and documents
        else "partial" if annual or recent or documents or company_profile
        else "preparing"
    )
    coverage_labels = {"sufficient": "자료 충분", "partial": "부분 확보", "preparing": "준비 중"}
    coverage_gaps = []
    if len(annual) < 3:
        coverage_gaps.append("확인 가능한 연간 재무자료가 3개 기간보다 적습니다.")
    if not recent:
        coverage_gaps.append("최근 중간·분기 재무자료를 확인하지 못했습니다.")
    if not documents:
        coverage_gaps.append("본문 또는 문서 식별자를 확인한 최근 공시·IR이 없습니다.")
    return {
        "contractVersion": "company-report-evidence-v1", "symbol": symbol,
        "profile": company_profile,
        "businessDescription": (business[:1000].rsplit(" ", 1)[0] + "…") if len(business) > 1000 else business,
        "businessDescriptionSource": "yfinance" if business else "",
        "annualFinancials": annual, "recentFinancials": recent, "documents": documents,
        "coverage": {
            "state": coverage_state, "label": coverage_labels[coverage_state],
            "annualPeriods": len(annual), "recentPeriods": len(recent),
            "documents": len(documents), "verifiedDocuments": sum(item["bodyVerified"] for item in documents),
            "gaps": coverage_gaps,
        },
        "limitations": [
            "사업부·제품별 매출 비중과 수주잔고는 이 보고서에 구조화된 근거가 없습니다.",
            "기업 IR의 전망과 설명은 회사 주장으로 표시하며 독립적인 검증 결과가 아닙니다.",
        ] + ([] if business else ["사업 설명 본문이 수집되지 않았습니다."]),
    }


def evidence_material(evidence):
    """Exclude polling clocks and provider cache IDs from change detection."""
    evidence = mapping(evidence)
    return {
        "profile": evidence.get("profile"), "businessDescription": evidence.get("businessDescription"),
        "financials": [{"period": report.get("period"), "frequency": report.get("frequency"),
                        "metrics": [{key: metric.get(key) for key in ("key", "value", "currency", "period", "durationBasis", "scope", "provider", "sourceDocumentId")}
                                    for metric in rows(report.get("metrics"))]}
                       for report in rows(evidence.get("annualFinancials")) + rows(evidence.get("recentFinancials"))],
        "documents": [{key: document.get(key) for key in ("documentId", "title", "publishedAt", "bodyVerified", "sourceRevision")}
                      for document in rows(evidence.get("documents"))],
    }


def financial_reading(evidence):
    """Arithmetic observations only, with explicit comparable source bases."""
    evidence = mapping(evidence)
    annual = rows(evidence.get("annualFinancials"))
    recent = rows(evidence.get("recentFinancials"))
    observations = []
    for report in (annual[:1] + recent[:1]):
        metrics = {item["key"]: item for item in rows(report.get("metrics"))}
        revenue, operating = mapping(metrics.get("revenue")), mapping(metrics.get("operatingIncome"))
        comparison = mapping(revenue.get("comparison"))
        previous = number(comparison.get("previousValue"))
        growth = number(comparison.get("changePct"))
        if previous is not None and previous > 0 and growth is not None:
            observations.append(
                revenue["basisLabel"] + " 매출액은 " + amount(previous, revenue["currency"])
                + " → " + amount(revenue["value"], revenue["currency"])
                + f" (전년 동기 대비 {growth:+.2f}%). 출처: " + revenue["provider"]
            )
        comparable = revenue and operating and all(
            revenue.get(key) and revenue.get(key) == operating.get(key)
            for key in ("provider", "currency", "period", "durationBasis", "scope")
        )
        if comparable and revenue["value"] > 0:
            margin = operating["value"] / revenue["value"] * 100
            observations.append(
                operating["basisLabel"] + " 영업이익률은 " + f"{margin:.2f}%"
                + "입니다. 영업이익 ÷ 매출액으로 계산했으며 출처는 " + revenue["provider"] + "입니다."
            )
        if report is (recent[0] if recent else None):
            for key in ("operatingCashFlow", "freeCashFlow"):
                metric = mapping(metrics.get(key))
                if metric:
                    observations.append(metric["basisLabel"] + " " + metric["label"] + " " + amount(metric["value"], metric["currency"]) + " · " + metric["provider"])
            net = mapping(metrics.get("netIncome"))
            if operating and net and operating.get("durationBasis") != net.get("durationBasis"):
                observations.append("최근 영업이익과 순이익의 집계 기간이 다릅니다. 두 금액을 직접 비교하지 않습니다.")
            elif comparable and net and all(net.get(key) == operating.get(key) for key in ("period", "durationBasis", "scope", "provider", "currency")) and net["value"] > operating["value"]:
                observations.append("최근 순이익이 영업이익보다 큽니다. 차이의 원인을 설명하려면 영업외손익·법인세 항목 확인이 필요합니다.")
    return observations


def company_evidence_sections(evidence):
    evidence = mapping(evidence)
    profile = mapping(evidence.get("profile"))
    profile_labels = {"companyName": "법인명", "ceoName": "대표이사", "industry": "산업", "sector": "업종", "employeeCount": "임직원 수", "auditFirm": "감사인", "auditOpinion": "감사의견"}
    profile_rows = [label + ": " + profile_value(profile[key]) for key, label in profile_labels.items() if profile.get(key) not in (None, "")]
    business = text(evidence.get("businessDescription"))
    coverage = mapping(evidence.get("coverage"))
    coverage_rows = [
        "자료 확보 수준: " + text(coverage.get("label") or "준비 중"),
        "연간 재무 " + str(coverage.get("annualPeriods", 0)) + "개 기간 · 최근 재무 "
        + str(coverage.get("recentPeriods", 0)) + "개 보고자료 · 공시·IR "
        + str(coverage.get("documents", 0)) + "건",
        *[text(item) for item in coverage.get("gaps") or [] if text(item)],
    ]
    sections = [{"key": "coverage", "title": "자료 확보 상태", "rows": coverage_rows,
                 "paragraphs": ["자료 확보 수준은 보고서 작성 범위를 뜻하며 기업의 투자 매력이나 위험도를 평가하지 않습니다."]},
                {"key": "business", "title": "사업과 기업 개요", "rows": profile_rows,
                 "sourceExcerpt": business, "sourceExcerptLabel": "사업 설명 발췌 원문 · yfinance",
                 "paragraphs": ["기업 프로필은 수집된 소개 정보이며 항목별 공시 대조가 완료된 것은 아닙니다."]}]
    sections.append({"key": "financialReading", "title": "실적과 현금흐름 읽기", "rows": financial_reading(evidence),
                     "paragraphs": ["같은 출처·기간·회계 기준으로 확인한 수치의 계산과 비교입니다. 실적이 바뀐 원인을 추정하지 않습니다."]})
    for key, title in (("annualFinancials", "연간 실적과 현금흐름"), ("recentFinancials", "최근 보고기간 실적")):
        reports = rows(evidence.get(key))
        sections.append({"key": key, "title": title, "financialReports": reports,
                         "paragraphs": ["항목별 기간·연결 기준·출처를 확인하세요. 단일 분기와 누적 금액을 합산하지 않습니다."] + (["이 연간 자료에는 현금흐름 항목이 없습니다."] if key == "annualFinancials" and not any(metric.get("key") == "operatingCashFlow" for report in reports for metric in rows(report.get("metrics"))) else []) if reports else ["출처와 보고기간을 확인할 수 있는 재무 자료가 없습니다."]})
    documents = rows(evidence.get("documents"))
    sections.append({"key": "documents", "title": "최근 공시와 회사 발표", "documents": documents,
                     "paragraphs": ["발행일 순으로 정리한 수집 문서입니다. 최근 주가 변화의 원인으로 단정하지 않습니다."] if documents else ["이 종목에 연결된 회사 발표 문서가 없습니다."]})
    return sections


__all__ = ["build_company_report_evidence", "evidence_material", "company_evidence_sections"]
