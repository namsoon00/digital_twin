"""Present measured price paths and fixed-position effects without advice."""
from .display_time import kst_timestamp


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
    result = ["시세 시각: " + kst_timestamp(movement["observedAt"]),
              "현재가: " + format(movement["currentPrice"], ",.2f") + " " + currency]
    if session:
        result.append("거래 구간: " + session)
    for row in movement.get("windows", []):
        minutes = format(row["elapsedSeconds"] / 60, ".1f").rstrip("0").rstrip(".")
        result.append("최근 " + minutes + "분: " + format(row["changePct"], "+.2f") + "% (" +
                      kst_timestamp(row["startAt"], "%H:%M:%S") + " → " + kst_timestamp(row["endAt"], "%H:%M:%S KST") + ")")
    result.append("이번 변동 시작 대비: " + format(movement["changePct"], "+.2f") + "%")
    if movement["stage"] == "recovery":
        result.append("관측된 최대 " + format(movement["peakMagnitude"], ".2f") + "% 변동 후 절반 이상 되돌린 상태가 2분 이상 관측됐습니다.")
    if effect:
        result.append("보유분 가격 효과: " + format(effect["nativeAmount"], "+,.2f") + " " + currency)
        if "accountImpactPct" in effect:
            result.append("현재 계좌 평가액 대비: " + format(effect["accountImpactPct"], "+.2f") + "% · 현재 보유 비중 " + format(effect["weightPct"], ".1f") + "%")
        result.append("현재 수량·환율 고정 기준이며 실제 계좌 손익과 다를 수 있습니다.")
    elif movement.get("role") == "watchlist":
        result.append("관심 종목의 가격 변화입니다. 사업·가치 조건 충족 여부는 별도 검토합니다.")
    result.extend(["시세 출처: " + source, "가격 변동 사실이며 원인이나 매수·매도 판단을 뜻하지 않습니다."])
    return result
