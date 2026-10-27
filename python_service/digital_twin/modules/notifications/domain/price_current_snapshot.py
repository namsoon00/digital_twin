"""Frozen current-market display for a quote event, without investment inference."""

from copy import deepcopy

from .observation_market_snapshot import clock, decimal, market_snapshot_sections, numeric
from .price_movement_presentation import amount_text, price_context_lines, price_movement_headline


VERSION = "price-current-snapshot-v1"
FIELDS = {
    "current_price": "currentPrice", "trade_strength": "tradeStrength", "volume": "volume",
    "trading_value": "tradingValue", "volume_ratio": "volumeRatio",
    "buy_volume": "buyVolume", "sell_volume": "sellVolume",
    "orderbook_bid_volume": "orderbookBidVolume", "orderbook_ask_volume": "orderbookAskVolume",
    **{party + "_" + field: party + "".join(word.title() for word in field.split("_"))
       for party in ("foreign", "institution", "individual")
       for field in ("buy_volume", "sell_volume", "net_volume", "net_amount")},
}
COVERAGE_KEYS = ("status", "sourceAsOf", "sourceTimestampState", "provider", "freshnessStatus",
    "judgementEvidenceUsable", "observedFields", "fields", "participantStatus", "measurementType",
    "amountUnit", "tradeStrengthDecisionUsable", "tradeStrengthQualityState", "tradeStrengthQualityReason",
    "tradeStrengthSampleCount", "tradeStrengthSampleState")


def price_current_snapshot(item, *, account_id, role, captured_at, effect, volume_pace):
    """Capture allowlisted values from the very position used by the price detector."""
    facts = {target: numeric(item.get(source)) for source, target in FIELDS.items()
             if numeric(item.get(source)) is not None}
    facts.update(currency=item.get("currency"), market=item.get("market"),
                 freshnessStatus=item.get("freshness_status"))
    coverage = {name: {key: deepcopy(value[key]) for key in COVERAGE_KEYS if key in value}
                for name, value in (item.get("market_signal_coverage") or {}).items()
                if name in {"price", "ccnl", "investor", "orderbook", "volume"} and isinstance(value, dict)}
    # The selected quote owns its clock and daily return; another feed's
    # coverage cannot lend observation authority to a default zero.
    daily = numeric(item.get("change_rate")) if item.get("price_change_usable") is True else None
    if daily is not None:
        facts["priceChangeRate"] = daily
    coverage["price"] = {"sourceAsOf": item.get("source_as_of"),
                         "observedFields": ["currentPrice"] + (["priceChangeRate"] if daily is not None else [])}
    for days in (5, 20, 60):
        average = numeric(item.get("ma" + str(days)))
        price = numeric(item.get("current_price"))
        if average and average > 0 and price and price > 0:
            facts["ma" + str(days) + "Distance"] = (price / average - 1) * 100
    facts.update({key: value for key, value in volume_pace.items()
                  if key.startswith(("volumePace", "volumeRatio", "volumeMeasurement"))
                  or key in {"timeAdjustedVolumeRatio", "expectedVolumeRatioNow"}})
    if role == "holding" and (numeric(item.get("quantity")) or 0) > 0:
        facts["quantity"] = item["quantity"]
        for key, target in (("averagePrice", "averagePrice"), ("unrealizedReturnPct", "profitLossRate"),
                            ("unrealizedNativeAmount", "profitLoss"), ("weightPct", "positionAccountWeight")):
            if key in effect:
                facts[target] = effect[key]
    return {"version": VERSION, "accountId": account_id, "symbol": item.get("symbol"), "role": role,
            "capturedAt": captured_at, "observedAt": item.get("source_as_of") or "",
            "source": item.get("quote_source") or "", "market": item.get("market"),
            "indicatorAsOf": item.get("indicator_as_of") or "",
            "marketSignalCoverage": coverage, "facts": [{"id": key, "value": value} for key, value in facts.items()
                                                         if value is not None]}


def price_current_content(context, account_id, symbol, *, detail=False):
    """Render only the captured event; never enrich a queued alert with live data."""
    snapshot = context.get("priceCurrentSnapshot") or {}
    observation = context.get("marketObservation") or {}
    facts = {r["id"]: r.get("value") for r in snapshot.get("facts") or []}
    witness = observation.get("quoteWitness") or {}
    if (snapshot.get("version") != VERSION or snapshot.get("accountId") != account_id
            or snapshot.get("symbol") != symbol or not symbol
            or witness.get("symbol") != symbol
            or not snapshot.get("observedAt")
            or (numeric(facts.get("currentPrice")) or 0) <= 0
            or numeric(facts.get("currentPrice")) != numeric(observation.get("currentPrice"))
            or facts.get("currency") != witness.get("currency")
            or snapshot.get("observedAt") != witness.get("source_as_of")):
        return None
    currency = str(facts.get("currency") or "")
    movement = context.get("priceMovement") or {}
    change = numeric(observation.get("changePct"))
    baseline = numeric(observation.get("baselinePrice"))
    summary = price_movement_headline(movement) if movement else "기준 시세 대비 " + decimal(change, signed=True) + "% 변동"
    cause = []
    if baseline and baseline > 0:
        label = {"price-movement-start": "이번 변동 시작", "last-outbox-alert": "직전 알림 접수 기준",
                 "initial-observation": "초기 관측 기준", "last-reasoning-candidate": "직전 분석 요청 기준"}.get(
                     observation.get("baselineKind"), "직전 저장 기준")
        cause.append(label + " " + amount_text(baseline, currency) + " → "
                     + amount_text(facts["currentPrice"], currency) + " (" + decimal(change, signed=True) + "%)")
    effect = movement.get("positionEffect") or context.get("positionPriceEffect") or {}
    if numeric(effect.get("nativeAmount")) is not None:
        cause.append("이번 가격 변동분 " + amount_text(effect["nativeAmount"], currency, signed=True)
                     + " · 현재 수량 고정, 누적 손익과 구분")
    sections = [{"key": "price-cause", "title": "이번 알림이 온 이유", "lines": cause}]
    for key, title, rows in market_snapshot_sections(snapshot):
        if key == "current-price":
            title = "현재 시세"
            if "priceChangeRate" not in facts:
                rows.append("전일 대비 미확인")
            rows += price_context_lines(context.get("priceContext") or {}, currency, include_comparisons=detail)
            if snapshot.get("source"):
                rows.append("시세 출처 " + snapshot["source"])
        elif key == "market-activity":
            # Exact numerator/denominator remain in the frozen packet/detail.
            if not detail:
                rows = [row for row in rows if not row.startswith("계산 근거:")]
                rows = [row.split(" ≈ ", 1)[0] + (" · 정규장 " + row.split(" · 정규장 ", 1)[1]
                        if " · 정규장 " in row else "") if " ≈ " in row else row for row in rows]
            if not any("누적 거래량" in row or "누적 거래대금" in row for row in rows):
                rows.insert(0, "누적 거래량·거래대금 미확인")
        elif key == "execution-flow" and not any("대기" in row for row in rows):
            rows.append("매수·매도 대기 물량 미확인")
        elif key == "holding":
            rows = [" · ".join(rows)] if rows else [
                "보유 수량·매입가 미확인" if snapshot.get("role") == "holding" else "보유 없음 · 관심 종목"]
            if snapshot.get("role") == "holding":
                rows.append("현재 시세 기준 평가 · 수수료·세금 제외")
        elif key == "price-trend":
            rows = rows + ["가격 흐름 자료 " + clock(snapshot.get("indicatorAsOf"))] if rows else ["평균 가격 자료 미확인"]
        sections.append({"key": key, "title": title, "lines": rows})
    order = {key: index for index, key in enumerate(("price-cause", "current-price", "holding", "investor-flow",
                                                    "execution-flow", "market-activity", "price-trend"))}
    sections.sort(key=lambda row: order[row["key"]])
    sections.append({"key": "scope", "title": "읽는 기준", "lines": [
        "각 자료는 표시된 시각의 관측값입니다. 규칙에 따른 판단 변화는 관계 변화 알림에서 안내합니다."]})
    return {"kind": "price-change", "version": VERSION, "summary": summary, "sections": sections}
