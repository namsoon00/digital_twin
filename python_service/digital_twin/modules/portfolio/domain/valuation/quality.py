"""Categorical data-quality checks for valuation inputs and outputs."""

from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List

from digital_twin.modules.market_data.contracts import number


@dataclass(frozen=True)
class ValuationQualityIssue:
    code: str
    message: str
    blocking: bool = True

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedDividendYield:
    ratio: float = 0.0
    percent: float = 0.0
    source_unit: str = ""
    status: str = "unavailable"
    reason: str = ""


def normalize_dividend_yield(value: object, source_unit: object) -> NormalizedDividendYield:
    """Normalize a dividend yield while preserving explicit source units.

    A ratio is constrained to the economically meaningful 0..1 range. Values
    outside that range are rejected instead of silently entering valuation or
    TypeDB company-quality rules.
    """

    if value in (None, ""):
        return NormalizedDividendYield(reason="배당수익률 값이 없습니다.")
    raw = number(value)
    unit = str(source_unit or "").strip().lower()
    if unit not in {"ratio", "percent"}:
        return NormalizedDividendYield(
            source_unit=unit,
            status="invalid",
            reason="배당수익률 원본 단위가 ratio 또는 percent로 지정되지 않았습니다.",
        )
    ratio = raw / 100.0 if unit == "percent" else raw
    if ratio < 0.0 or ratio > 1.0:
        return NormalizedDividendYield(
            source_unit=unit,
            status="invalid",
            reason="배당수익률이 0%~100% 범위를 벗어나 단위 오류로 판단했습니다.",
        )
    return NormalizedDividendYield(
        ratio=round(ratio, 8),
        percent=round(ratio * 100.0, 4),
        source_unit=unit,
        status="valid",
    )


def valuation_quality_issues(row: Dict[str, object]) -> List[ValuationQualityIssue]:
    issues: List[ValuationQualityIssue] = []
    current = number(row.get("currentPrice"))
    low = number(row.get("fairValueLow"))
    base = number(row.get("fairValue") or row.get("fairValueBase"))
    high = number(row.get("fairValueHigh"))
    if base and not current:
        issues.append(ValuationQualityIssue("missing-current-price", "적정가와 비교할 현재가가 없습니다."))
    if any(value < 0 for value in (current, low, base, high)):
        issues.append(ValuationQualityIssue("negative-price", "현재가 또는 적정가에 음수 값이 있습니다."))
    if low and base and high and not low <= base <= high:
        issues.append(ValuationQualityIssue("invalid-scenario-order", "보수·기준·낙관 적정가의 순서가 올바르지 않습니다."))
    if bool(row.get("valuationDecisionEligible")):
        if str(row.get("valuationInputState") or "").strip().lower() != "sufficient":
            issues.append(ValuationQualityIssue("incomplete-inputs", "입력 자료가 충분하지 않은데 투자 판단 사용 가능으로 표시됐습니다."))
        if str(row.get("valuationFreshnessStatus") or "").strip().lower() in {"stale", "unknown", ""}:
            issues.append(ValuationQualityIssue("unusable-freshness", "기준일이 오래됐거나 확인되지 않은 적정가입니다."))
        if bool(row.get("aiGenerated")) and str(row.get("approvalStatus") or "").strip().lower() not in {
            "approved",
            "modified",
            "user_approved",
            "user_modified",
        }:
            issues.append(ValuationQualityIssue("unreviewed-ai-assumption", "검토되지 않은 AI 가정이 투자 판단에 사용되려 합니다."))
    return issues


def apply_valuation_quality_gate(row: Dict[str, object]) -> Dict[str, object]:
    result = dict(row or {})
    issues = valuation_quality_issues(result)
    blocking = [issue for issue in issues if issue.blocking]
    missing = [str(item) for item in result.get("missingInputs") or [] if str(item or "").strip()]
    if blocking:
        result["valuationDecisionEligible"] = False
    if blocking:
        status = "blocked"
    elif missing or str(result.get("valuationInputState") or "").strip().lower() in {"partial", "unavailable"}:
        status = "partial"
    else:
        status = "ready"
    result["valuationQualityStatus"] = status
    result["valuationQualityIssues"] = [issue.to_dict() for issue in issues]
    result["modelExclusionReasons"] = _unique(
        list(result.get("modelExclusionReasons") or []) + [issue.message for issue in blocking]
    )
    return result


def _unique(values: Iterable[object]) -> List[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))
