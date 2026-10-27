"""Present measured price paths and fixed-position effects without advice."""
from .display_time import kst_timestamp


def amount_text(value, currency, *, signed=False):
    code = str(currency or "").upper()
    precision = 0 if code == "KRW" else 2
    rounded = round(value, precision)
    text = format(abs(rounded), ",." + str(precision) + "f")
    if precision:
        text = text.rstrip("0").rstrip(".")
    prefix = "-" if rounded < 0 else "+" if signed and rounded > 0 else ""
    return prefix + text + ("원" if code == "KRW" else " " + code)


def position_price_lines(effect, currency, baseline):
    """Keep total unrealized P&L separate from a fixed-quantity quote delta."""
    if not effect:
        return []
    result = []
    if "unrealizedNativeAmount" in effect:
        result.append("매입가 대비 평가손익: " + amount_text(effect["unrealizedNativeAmount"], currency, signed=True)
                      + " (" + format(effect["unrealizedReturnPct"], "+.1f") + "% · 수수료·세금 제외)")
        result.append("평균매입가: " + amount_text(effect["averagePrice"], currency))
    else:
        result.append("매입가 대비 평가손익: 평균매입가 미확인")
    result.append("이번 가격 변동분: " + amount_text(effect["nativeAmount"], currency, signed=True)
                  + " (" + baseline + " · 현재 수량 고정)")
    return result


def price_movement_headline(movement):
    role = "보유" if movement.get("role") == "holding" else "관심"
    direction = "상승" if movement.get("direction") == "up" else "하락"
    stage = movement.get("stage")
    if stage == "reversed":
        return role + " · 시작 가격을 넘어 " + direction + " 전환"
    if stage == "recovery":
        return role + " · " + direction + "폭 축소 확인"
    if stage == "expanded":
        return role + " · " + direction + "폭 추가 확대"
    minutes = (movement.get("trigger") or {}).get("elapsedSeconds", 0) / 60
    return role + " · 최근 " + format(minutes, ".1f").rstrip("0").rstrip(".") + "분 " + direction


def price_movement_lines(movement, currency, source, session, effect):
    followup = movement["stage"] in {"expanded", "recovery"}
    windows = movement.get("windows", [])
    selected = next((row for row in windows if row["windowMinutes"] == movement["trigger"]["windowMinutes"]), None)
    summary = "이번 변동 시작 대비: " + format(movement["changePct"], "+.2f") + "%"
    if selected and not followup:
        summary = "최근 " + format(selected["elapsedSeconds"] / 60, ".1f").rstrip("0").rstrip(".") + "분: " + format(selected["changePct"], "+.2f") + "%"
    result = [summary, "현재가: " + amount_text(movement["currentPrice"], currency)]
    position_lines = position_price_lines(effect, currency, "변동 시작가 대비")
    if position_lines:
        result.append(position_lines[-1])
    result.append("시세 시각: " + kst_timestamp(movement["observedAt"]))
    if session:
        result.append("거래 구간: " + session)
    for row in ([] if followup else windows):
        if row is selected:
            continue
        minutes = format(row["elapsedSeconds"] / 60, ".1f").rstrip("0").rstrip(".")
        result.append("최근 " + minutes + "분: " + format(row["changePct"], "+.2f") + "% (" +
                      kst_timestamp(row["startAt"], "%H:%M:%S") + " → " + kst_timestamp(row["endAt"], "%H:%M:%S KST") + ")")
    result.append("변동 시작: " + kst_timestamp(movement["anchorAt"], "%H:%M:%S KST")
                  + " · " + amount_text(movement["anchorPrice"], currency))
    if not followup:
        result.append("이번 변동 시작 대비: " + format(movement["changePct"], "+.2f") + "%")
    measured = (selected or {}).get("observedRange") or {}
    if measured and measured["high"] != measured["low"]:
        result.append("최근 " + format(selected["elapsedSeconds"] / 60, ".1f").rstrip("0").rstrip(".")
                      + "분 수집 시세: 고가 " + amount_text(measured["high"], currency)
                      + " (" + kst_timestamp(measured["highAt"], "%H:%M:%S") + ") · 저가 "
                      + amount_text(measured["low"], currency) + " (" + kst_timestamp(measured["lowAt"], "%H:%M:%S KST") + ")")
        result.append("수집 사이의 가격과 고저점은 미확인입니다.")
    if movement["stage"] == "recovery":
        result.append("관측된 최대 " + format(movement["peakMagnitude"], ".2f") + "% 변동 후 절반 이상 되돌린 상태가 2분 이상 관측됐습니다.")
    if effect:
        if not followup:
            result.extend(position_lines[:-1])
        if "accountImpactPct" in effect and not followup:
            result.append("현재 계좌 평가액 대비: " + format(effect["accountImpactPct"], "+.2f") + "% · 현재 보유 비중 " + format(effect["weightPct"], ".1f") + "%")
        result.append("이번 가격 변동분은 누적 손익이 아니며, 계좌 영향은 현재 환율 고정 기준입니다.")
    elif movement.get("role") == "watchlist":
        result.append("관심 종목의 가격 변화입니다. 사업·가치 조건 충족 여부는 별도 검토합니다.")
    result.extend(["시세 출처: " + source, "가격 변동 사실이며 원인이나 매수·매도 판단을 뜻하지 않습니다."])
    return result


def price_context_lines(context, currency, *, compact=False, window_minutes=None, include_comparisons=True):
    """Render only frozen facts, without reconstructing old alerts from live quotes."""
    if context.get("version") != "price-context-v1" or context.get("status") != "available":
        return []
    daily = context.get("daily") or {}
    provider = daily.get("basis") == "provider-session"
    label = "정규장" if provider else "금일 관측 구간"
    result = []
    if daily.get("newHigh") or daily.get("newLow"):
        sides = [name for key, name in (("newHigh", "고가"), ("newLow", "저가")) if daily.get(key)]
        result.append(label + " " + "·".join(sides) + " 갱신")
    if compact:
        return result
    if daily:
        result.append(label + " 고가 " + amount_text(daily["high"], currency) + " · 저가 " + amount_text(daily["low"], currency)
                      + " · 고가 대비 " + format(daily["fromHighPct"], "+.2f") + "%"
                      + (" · 저가 대비 " + format(daily["fromLowPct"], "+.2f") + "%" if "fromLowPct" in daily else ""))
        if not provider:
            result.append("관측 시작: " + kst_timestamp(daily["startAt"], "%H:%M:%S KST") + " · 관측 사이의 고저점은 미확인")
        if "sinceOpenPct" in daily:
            result.append("정규장 시가: " + amount_text(daily["open"], currency) + " · 시가 이후 " + format(daily["sinceOpenPct"], "+.2f") + "%")
        if "gapPct" in daily:
            result.append("전일 종가 대비 개장 갭 " + format(daily["gapPct"], "+.2f")
                          + "% · 현재 전일 대비 " + format(daily["sinceClosePct"], "+.2f") + "%")
    prior = context.get("priorRange") or {}
    if prior and prior.get("break") in {"above", "below"}:
        result.append("직전 관측 가격 범위 " + amount_text(prior["low"], currency) + "~" + amount_text(prior["high"], currency)
                      + (" 위로 이동" if prior["break"] == "above" else " 아래로 이동")
                      + " (" + kst_timestamp(prior["startAt"], "%H:%M") + "~" + kst_timestamp(prior["endAt"], "%H:%M KST") + ")")
    if not include_comparisons:
        return result
    comparisons = [row for row in context.get("comparisons") or []
                   if window_minutes is None or row["windowMinutes"] == window_minutes]
    available = [row for row in comparisons if row.get("status") == "available"]
    for row in available:
        text = ("현재 " + format(row["elapsedSeconds"] / 60, ".1f") + "분 변동 비교 (과거 "
                + format(row["historicalMinSeconds"] / 60, ".1f") + "~"
                + format(row["historicalMaxSeconds"] / 60, ".1f") + "분): 보관한 "
                + str(row["sessionDays"]) + "일·" + str(row["sampleCount"]) + "개 구간의 "
                + format(row["largerThanPct"], ".0f") + "%보다 큰 변동폭")
        if "medianMultiple" in row:
            text += " · 중앙값의 " + format(row["medianMultiple"], ".1f") + "배"
        if row.get("localTimeBand"):
            text += " · 시작 시각 " + row["localTimeBand"] + " (" + row["timeZone"] + ")"
        result.append(text)
    if not available:
        result.append("평소 변동 비교: 자료 축적 중 (같은 현지 시간대·길이 차이 10% 이내 20개 구간·3일 이상 필요)")
    return result
