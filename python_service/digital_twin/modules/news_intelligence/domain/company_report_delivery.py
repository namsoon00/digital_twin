"""Source-bound report changes and notification relevance, never investment scores."""

import hashlib
import json

from .company_report_evidence import mapping, number, rows, text
from .company_report_reading import _compact_money


DELIVERY_VERSION = "company-report-delivery-v1"
FIELDS = ("operatingCashFlow", "operatingIncome", "totalDebt", "revenue", "cash", "netIncome")
MEANINGS = {
    "operatingCashFlow": ("영업현금은 투자·상환에 쓸 자체 재원을 보여줍니다. 회계상 이익과 구분해 확인해야 합니다.", "현금 변화가 매출 회수·재고·매입대금 지급 중 어디에서 발생했는지 확인합니다."),
    "operatingIncome": ("영업손익은 매출에서 영업비용을 뺀 결과입니다. 반복 가능한 이익인지 일회성 항목을 확인해야 합니다.", "같은 기간 매출과 영업비용, 평가손익·일회성 항목을 대조합니다."),
    "totalDebt": ("차입금은 이자·상환 부담을 확인하는 출발점입니다. 만기와 현금 창출력도 함께 봐야 합니다.", "만기별 상환액과 보유현금, 차입금 사용처를 확인합니다."),
    "revenue": ("매출은 사업 규모를 보여줍니다. 이익과 영업현금이 뒷받침돼야 가치평가의 성장 가정을 검토할 수 있습니다.", "같은 기간 영업이익과 현금흐름이 매출 변화를 뒷받침하는지 확인합니다."),
    "cash": ("보유현금은 지급 여력의 한 요소입니다. 투자·상환·배당·자금조달 내역을 함께 확인해야 합니다.", "현금 증감 내역과 가까운 만기의 지급 의무를 확인합니다."),
    "netIncome": ("순손익은 세금까지 반영한 최종 결과입니다. 영업외손익과 일회성 항목을 구분해야 반복 가능한 이익인지 판단할 수 있습니다.", "순손익 변화와 영업손익의 차이, 법인세 및 일회성 항목을 확인합니다."),
}


def _metrics(evidence):
    result = {}
    for report in rows(evidence.get("recentFinancials")) + rows(evidence.get("annualFinancials")):
        for metric in rows(report.get("metrics")):
            if metric.get("key") not in FIELDS or number(metric.get("value")) is None:
                continue
            # A current debt component is not total debt, even in an official API.
            if metric["key"] == "totalDebt" and text(metric.get("sourceMetric")) in {
                "LongTermDebtCurrent", "LongTermDebtAndFinanceLeaseObligationsCurrent", "DebtCurrent", "ShortTermBorrowings",
            }:
                continue
            identity = tuple(text(metric.get(key)) for key in ("key", "period", "durationBasis", "scope", "provider", "currency", "sourceMetric"))
            result[identity] = metric
    return result


def has_delivery_reference(evidence):
    return bool(_metrics(mapping(evidence)))


def _change(current, previous, basis):
    key, value, old = current["key"], current["value"], previous["value"]
    direction = "증가" if value > old else "감소"
    label = current.get("label") or key
    if key in {"operatingIncome", "netIncome"}:
        label = "영업손익" if key == "operatingIncome" else "순손익"
        if old < 0 < value:
            direction = "적자에서 흑자로 전환"
        elif value < 0 < old:
            direction = "흑자에서 적자로 전환"
        elif old < 0 and value < 0:
            direction = "손실 축소" if value > old else "손실 확대"
    if key == "operatingCashFlow" and old > 0 > value:
        direction = "순유입에서 순유출로 전환"
    elif key == "operatingCashFlow" and old < 0 < value:
        direction = "순유출에서 순유입으로 전환"
    meaning, check = MEANINGS[key]
    if key == "operatingIncome":
        if value > 0 and old > 0:
            meaning = ("영업에서 남긴 이익이 늘었습니다." if value > old else "영업에서 남긴 이익이 줄었습니다.") + " 이익을 기준으로 한 가치평가 입력을 다시 검토할 변화입니다. 일회성 손익을 제외해도 지속되는지 확인해야 합니다."
        elif value < 0:
            meaning = "영업단계에서 손실이 남습니다. 흑자 전환에 필요한 매출·비용 조건을 확인해야 하며, 평가손실이 포함됐는지도 구분해야 합니다."
        elif value > 0 and old < 0:
            meaning = "영업적자에서 벗어났지만 한 번의 실적으로 수익성이 정착됐다고 볼 수 없습니다. 반복 가능한 이익과 현금 창출을 확인해야 합니다."
        elif value == 0:
            meaning = "영업손익이 손익분기 수준입니다. 투자·상환 재원을 만들 만큼 이익과 현금이 남는지는 별도 확인해야 합니다."
        else:
            meaning = "영업이익이 발생했습니다. 반복 가능한 이익인지, 영업현금도 뒷받침하는지 확인해야 합니다."
    if key == "operatingCashFlow" and value < 0:
        meaning = "영업활동에서 현금이 순유출됐습니다. 투자·상환에 쓸 자체 재원이 줄었는지, 일시적인 운전자본 지출인지 구분해야 합니다."
    elif key == "operatingCashFlow" and value > 0:
        meaning = "영업활동에서 현금이 순유입됐습니다. 투자·상환에 사용할 자체 재원이 생겼지만 설비투자와 지급 의무까지 감당하는지는 별도 확인해야 합니다."
    if key == "totalDebt":
        meaning = ("빌린 자금이 늘었습니다. 자금 사용처와 추가 이자·만기 부담을 확인해야 합니다." if value > old else "차입금이 줄었습니다. 상환 부담이 낮아질 수 있지만 현금 소진이나 증자로 갚았는지 함께 확인해야 합니다.")
    return {"key": key, "headline": label + " " + direction,
            "fact": current["basisLabel"].replace("official-filing", "공시 기준") + " · " + basis + " · " + label + " " + _compact_money(old, current["currency"]) + " → " + _compact_money(value, current["currency"]),
            "meaning": meaning, "nextCheck": check, "evidence": [previous, current], "basis": basis}


def report_delivery_changes(evidence, previous_evidence):
    """Ignore source enrichment; compare like periods or verified year-on-year facts.

    Ten percent is a notification noise filter, not an investment threshold.
    Zero/sign transitions always remain visible. New official reporting periods
    are disclosed even when no comparable prior-year amount is available.
    """
    current, previous = _metrics(mapping(evidence)), _metrics(mapping(previous_evidence))
    if not previous:
        return []
    changes = []
    for identity, metric in current.items():
        old = previous.get(identity)
        basis = "수치 정정"
        if old:
            if metric.get("periodStart") != old.get("periodStart"):
                continue
        else:
            peers = [item for prior_key, item in previous.items() if prior_key[0] == identity[0] and prior_key[2:] == identity[2:]]
            if not peers or metric.get("official") is not True or not metric.get("sourceDocumentId"):
                continue
            if text(metric.get("period")) <= max(text(item.get("period")) for item in peers):
                continue  # Historical backfill or source enrichment is not news.
            comparison = mapping(metric.get("comparison"))
            if comparison.get("basis") == "year-over-year" and number(comparison.get("previousValue")) is not None:
                old = {**mapping(comparison.get("previousSource")), "key": metric["key"], "value": comparison["previousValue"], "period": comparison.get("previousPeriod")}
                basis = "전년 동기 대비"
            else:
                meaning, check = MEANINGS[metric["key"]]
                changes.append({"key": metric["key"], "headline": "새 공시의 " + metric["label"] + " 확인",
                                "fact": metric["basisLabel"].replace("official-filing", "공시 기준") + " · " + metric["label"] + " " + _compact_money(metric["value"], metric["currency"]),
                                "meaning": meaning + " 비교 가능한 전년 동기 수치가 없어 개선·악화 방향은 보류합니다.",
                                "nextCheck": check, "evidence": [metric], "basis": "새 보고기간"})
                continue
        value, old_value = number(metric.get("value")), number(old.get("value"))
        if old_value is None:
            continue
        if value == old_value and basis == "전년 동기 대비":
            meaning, check = MEANINGS[metric["key"]]
            changes.append({"key": metric["key"], "headline": "새 공시의 " + metric["label"] + " 전년 동기 수준",
                            "fact": metric["basisLabel"].replace("official-filing", "공시 기준") + " · " + metric["label"] + " " + _compact_money(value, metric["currency"]) + " · 전년 동기와 동일",
                            "meaning": meaning, "nextCheck": check, "evidence": [old, metric], "basis": basis})
            continue
        if value == old_value:
            continue
        sign_change = (value > 0) != (old_value > 0) or (value < 0) != (old_value < 0)
        if basis == "수치 정정" and not sign_change and abs(value - old_value) < abs(old_value) * 0.10:
            continue
        changes.append(_change(metric, old, basis))
    # One latest observation per question, with cash generation before earnings.
    selected = {}
    for change in sorted(changes, key=lambda item: text(item["evidence"][-1].get("period")), reverse=True):
        selected.setdefault(change["key"], change)
    return [selected[key] for key in FIELDS if key in selected]


def attach_delivery_brief(report, previous):
    changes = report_delivery_changes(mapping(report.get("evidence")), mapping(previous.get("evidence")))
    eligible = report.get("reportKind") == "change" and bool(changes)
    report["deliveryEligible"] = eligible
    # Include all meaningful changes in the same message. No cash/debt reversal
    # disappears behind a generic operating-margin headline.
    report["deliveryPolicy"] = {"version": DELIVERY_VERSION, "eligible": eligible, "changes": changes,
                                "reason": "financial-change" if eligible else "reference-update-only"}
    if not eligible:
        return
    facts, implications, checks = [], [], []
    if any(change["basis"] == "수치 정정" for change in changes):
        implications.append("같은 보고기간의 수치 변경입니다. 새 기간의 실적 변화로 해석하기 전에 정정 공시·집계 기준을 확인해야 합니다.")
    for change in changes:
        facts.append(change["fact"])
        implications.append(change["meaning"])
        checks.append(change["nextCheck"])
    remeasurement = next((card for card in report.get("reading", {}).get("financial", []) if card.get("driver") == "digital-asset-remeasurement"), {})
    if remeasurement and any(change["key"] in {"operatingIncome", "netIncome"} for change in changes):
        implications.append(remeasurement["meaning"])
    report["summary"] = " · ".join(change["headline"] for change in changes)
    report["brief"] = {"kind": "company-change-report", "presentation": "company-report-brief-v1",
                       "subject": {"symbol": report.get("symbol"), "name": report.get("name")},
                       "summary": report["summary"], "sections": [
                           {"title": "달라진 점", "rows": facts},
                           {"title": "기업가치에 어떤 의미인가", "rows": list(dict.fromkeys(implications))},
                           {"title": "다음 확인", "rows": list(dict.fromkeys(checks))}]}
    # Exact meaning and numbers, excluding polling clocks and source cache IDs.
    semantic = {"symbol": report.get("symbol"), "facts": facts, "meaning": implications, "checks": checks}
    report["deliveryPolicy"]["fingerprint"] = hashlib.sha256(json.dumps(semantic, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
