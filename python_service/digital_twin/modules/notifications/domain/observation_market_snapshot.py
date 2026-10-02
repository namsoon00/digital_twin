"""Readable, source-clocked measurements; never infer an investment action."""

from datetime import datetime
import math
from zoneinfo import ZoneInfo

from digital_twin.modules.notifications.domain.alert_formatting import price_money, trade_strength_label


def numeric(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def decimal(value, digits=2, signed=False):
    number = numeric(value)
    if number is None:
        return "미확인"
    if 0 < abs(number) < 10 ** -digits:
        digits = min(10, max(digits, math.ceil(-math.log10(abs(number))) + 1))
    text = format(number, ",." + str(digits) + "f").rstrip("0").rstrip(".") if digits else format(number, ",.0f")
    return ("+" if signed and number > 0 else "") + text


def clock(value):
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if at.tzinfo is None:
            return "기준 시각 미확인"
        return at.astimezone(ZoneInfo("Asia/Seoul")).strftime("%m/%d %H:%M KST")
    except (TypeError, ValueError):
        return "기준 시각 미확인"


def snapshot_facts(snapshot):
    return {row["id"]: row.get("value") for row in snapshot.get("facts", []) if isinstance(row, dict) and row.get("id")}


def stage(snapshot, name):
    value = (snapshot.get("marketSignalCoverage") or {}).get(name)
    return value if isinstance(value, dict) else {}


def observed(snapshot, field, stage_name):
    facts = snapshot_facts(snapshot)
    value = numeric(facts.get(field))
    if value is None:
        return None
    source = stage(snapshot, stage_name)
    fields = source.get("observedFields", source.get("fields"))
    if stage_name == "investor":
        fields = fields if fields is not None else snapshot.get("investorFlowObservedFields")
    if fields is not None:
        aliases = {"priceChangeRate": "changeRate"}
        return value if field in fields or aliases.get(field) in fields else None
    # Historical numeric defaults are not evidence that zero was measured.
    return value if value != 0 else None


def source_note(snapshot, name):
    source = stage(snapshot, name)
    facts = snapshot_facts(snapshot)
    at = source.get("sourceAsOf")
    if name == "investor":
        at = at or facts.get("investorFlowSourceAsOf")
    kind = source.get("measurementType") or (facts.get("investorFlowMeasurementType") if name == "investor" else "")
    basis = {"intraday-estimate": "장중 누적 추정", "daily-final": "장 마감 확정"}.get(kind, "")
    if name == "investor" and not basis:
        basis = "집계 방식 미확인"
    if source.get("status") in {"stale", "stale-at-dispatch", "expired"} or source.get("freshnessStatus") in {"stale", "expired"}:
        basis = (basis + " · " if basis else "") + "과거 값 참고"
    elif source.get("judgementEvidenceUsable") is False:
        basis = (basis + " · " if basis else "") + "참고용"
    return " · ".join(item for item in (basis, clock(at)) if item)


def investor_rows(snapshot):
    facts = snapshot_facts(snapshot)
    source = stage(snapshot, "investor")
    states = source.get("participantStatus") or snapshot.get("investorFlowParticipantStatus") or {}
    market = str(facts.get("market") or snapshot.get("market") or "").upper()
    if (source.get("status") in {"unsupported", "unsupported-market"}
            or all(states.get(party) == "unsupported" for party in ("foreign", "institution", "individual"))
            or (not source and not states and market in {"US", "CRYPTO"})):
        return ["외국인·기관·개인 구분 수급: 이 시장의 자료 미지원"]
    result = []
    currency = str(facts.get("currency") or "KRW")
    for party, label in (("foreign", "외국인"), ("institution", "기관"), ("individual", "개인")):
        state = states.get(party, "")
        net = observed(snapshot, party + "NetVolume", "investor")
        amount = observed(snapshot, party + "NetAmount", "investor")
        buy = observed(snapshot, party + "BuyVolume", "investor")
        sell = observed(snapshot, party + "SellVolume", "investor")
        if state in {"not-yet-published", "unsupported", "missing", "unavailable"}:
            result.append(label + ": " + {"not-yet-published": "아직 집계 전", "unsupported": "자료 미지원"}.get(state, "자료 미확인"))
            continue
        if net is None and buy is not None and sell is not None:
            net = buy - sell
        parts = []
        if net is not None:
            parts.append(("순매수 " if net > 0 else "순매도 " if net < 0 else "순매수·매도 차이 ") + decimal(abs(net), 0) + "주")
        if amount is not None:
            parts.append(("순매수 금액 " if amount >= 0 else "순매도 금액 ") + decimal(abs(amount), 0) + ("원" if currency == "KRW" else " " + currency))
        if buy is not None:
            parts.append("매수 " + decimal(buy, 0) + "주")
        if sell is not None:
            parts.append("매도 " + decimal(sell, 0) + "주")
        result.append(label + ": " + (" · ".join(parts) or "자료 미확인"))
    result.append(source_note(snapshot, "investor"))
    return result


def market_snapshot_sections(current, previous=None):
    """Keep essential groups visible regardless of the three-row evidence budget."""
    facts, before = snapshot_facts(current), snapshot_facts(previous or {})
    quantity_unit = "개" if (facts.get("market") or current.get("market")) == "CRYPTO" else "주"
    currency = str(facts.get("currency") or ("USD" if (facts.get("market") or current.get("market")) == "US" else "KRW"))
    price = numeric(facts.get("currentPrice"))
    quote = []
    if price is not None and price > 0:
        row = "가격 " + price_money(price, currency)
        change = observed(current, "priceChangeRate", "price")
        if change is not None:
            row += " · 전일 대비 " + decimal(change, signed=True) + "%"
        quote.append(row)
        old = numeric(before.get("currentPrice"))
        if old is not None and old > 0 and before.get("currency", currency) == currency:
            quote.append("이전 알림 " + price_money(old, currency) + " → " + price_money(price, currency)
                         + " (" + decimal((price / old - 1) * 100, signed=True) + "%)")
        observed_clock = clock(current.get("observedAt"))
        quote.append(("시세 " if observed_clock == "기준 시각 미확인" else "시세 기준 ") + observed_clock)
        if facts.get("freshnessStatus") in {"stale", "expired"}:
            quote.append("과거 시세 참고")
    else:
        quote.append("가격 미확인")
    trend = []
    for field, label in (("ma5Distance", "5일"), ("ma20Distance", "20일"), ("ma60Distance", "60일")):
        value = numeric(facts.get(field))
        if value is not None:
            old = numeric(before.get(field))
            text = decimal(abs(value)) + "% " + ("높음" if value > 0 else "낮음") if value != 0 else "같음"
            prior = " (이전 알림 " + decimal(old, signed=True) + "%)" if old is not None else ""
            trend.append(label + " 평균 가격보다 " + text + prior)
    activity = []
    for field, label, unit in (("volume", "누적 거래량", quantity_unit), ("tradingValue", "누적 거래대금", "원" if currency == "KRW" else " " + currency)):
        value = observed(current, field, "ccnl")
        if value is not None:
            suffix = " · 추정" if field == "tradingValue" and facts.get("tradingValueEstimated") else ""
            activity.append(label + " " + decimal(value, 0) + unit + suffix)
    ratios = []
    # Vendors use different denominators (including prior-day total). The
    # normalized ratio alone does not prove a daily-average comparison.
    for field, label in (("volumeRatio", "거래량 비율"), ("timeAdjustedVolumeRatio", "장중 시간 보정 추정")):
        value = numeric(facts.get(field))
        if value is not None and value > 0:
            ratios.append(label + " " + decimal(value) + "배")
    if ratios:
        activity.append(" · ".join(ratios))
    if activity:
        activity = [" · ".join(activity[:2]), *activity[2:]]
        activity[-1] += " · 거래 기준 " + source_note(current, "ccnl").replace("기준 시각", "시각")
    executions = []
    strength = observed(current, "tradeStrength", "ccnl")
    if strength is not None and strength > 0:
        executions.append("체결강도 " + decimal(strength) + " · " + trade_strength_label(strength))
    amounts = []
    for field, label in (("buyVolume", "매수 체결"), ("sellVolume", "매도 체결")):
        value = observed(current, field, "ccnl")
        if value is not None:
            amounts.append(label + " " + decimal(value, 0) + quantity_unit)
    if amounts:
        executions.append(" · ".join(amounts))
    if executions:
        executions[-1] += " · 체결 기준 " + source_note(current, "ccnl").replace("기준 시각", "시각")
    book = []
    for field, label in (("orderbookBidVolume", "매수 대기"), ("orderbookAskVolume", "매도 대기")):
        value = observed(current, field, "orderbook")
        if value is not None:
            book.append(label + " " + decimal(value, 0) + quantity_unit)
    if book:
        executions.append(" · ".join(book) + " · 호가 기준 " + source_note(current, "orderbook").replace("기준 시각", "시각"))
    holding = []
    if (numeric(facts.get("quantity")) or 0) > 0 or facts.get("isHolding") is True:
        for field, label, unit in (("quantity", "보유", quantity_unit), ("profitLossRate", "평가 수익률", "%"), ("positionWeight", "계좌 비중", "%")):
            value = numeric(facts.get(field))
            if value is not None:
                holding.append(label + " " + decimal(value, signed=field == "profitLossRate") + unit)
    return [
        ("current-price", "가격 · 이전 알림과 비교", quote),
        ("investor-flow", "외국인·기관·개인", investor_rows(current)),
        ("market-activity", "거래량·거래대금", activity or ["거래량·거래대금 자료 미확인"]),
        ("execution-flow", "체결과 대기 주문", executions or ["체결·호가 자료 미확인"]),
        ("price-trend", "가격 흐름", trend),
        ("holding", "내 보유 상황", holding),
    ]
