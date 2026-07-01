from typing import Dict, List


def value(payload: Dict[str, object], key: str) -> float:
    try:
        return float(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def text(payload: Dict[str, object], key: str) -> str:
    return str(payload.get(key) or "").strip()


def first_sentence(items: List[str], fallback: str) -> str:
    return "; ".join(items[:2]) if items else fallback


def signed_pct(number: float, suffix: str = "%") -> str:
    rounded = round(float(number or 0), 1)
    return ("+" if rounded > 0 else "") + str(rounded) + suffix


def pct_delta(current: float, previous: float) -> float:
    base = float(previous or 0)
    if not base:
        return 0.0
    return ((float(current or 0) / base) - 1) * 100


def decision_change_review_lines(
    current_position: Dict[str, object],
    previous_position: Dict[str, object],
    current_decision: Dict[str, object],
    previous_decision: Dict[str, object],
    pressure_threshold: float,
) -> List[str]:
    pressure_delta = value(current_decision, "exit_pressure") - value(previous_decision, "exit_pressure")
    pnl_delta = value(current_position, "profit_loss_rate") - value(previous_position, "profit_loss_rate")
    market_value_delta = pct_delta(value(current_position, "market_value"), value(previous_position, "market_value"))
    decision_changed = text(current_decision, "decision") != text(previous_decision, "decision")

    reasons: List[str] = []
    if decision_changed:
        reasons.append("판단명이 " + (text(previous_decision, "decision") or "-") + "에서 " + (text(current_decision, "decision") or "-") + "로 바뀜")
    if abs(pressure_delta) >= float(pressure_threshold or 0):
        reasons.append("exit pressure가 " + signed_pct(pressure_delta, "점") + " 변해 기준 " + str(round(float(pressure_threshold or 0), 1)) + "점 이상")

    drivers: List[str] = []
    if abs(pnl_delta) >= 1:
        drivers.append("손익률 " + signed_pct(pnl_delta, "%p"))
    if abs(market_value_delta) >= 3:
        drivers.append("평가액 " + signed_pct(market_value_delta))
    if value(current_position, "quantity") != value(previous_position, "quantity"):
        drivers.append("수량 " + str(previous_position.get("quantity", 0)) + " -> " + str(current_position.get("quantity", 0)))

    validation = model_data_validation(current_position, previous_position, current_decision, previous_decision, pnl_delta)
    improvement = model_improvement_hint(current_position, current_decision, previous_decision, pressure_delta, pnl_delta, pressure_threshold)

    return [
        "Codex 답변: " + first_sentence(reasons, "판단 기준에 의미 있는 변화가 감지됨") + ". 주요 변화는 " + first_sentence(drivers, "점수 구성값 변화") + "입니다.",
        "데이터 검증: " + validation,
        "모델 보완: " + improvement,
    ]


def model_data_validation(
    current_position: Dict[str, object],
    previous_position: Dict[str, object],
    current_decision: Dict[str, object],
    previous_decision: Dict[str, object],
    pnl_delta: float,
) -> str:
    issues: List[str] = []
    if not text(current_position, "symbol"):
        issues.append("종목코드 누락")
    if value(current_position, "market_value") <= 0:
        issues.append("평가액 없음")
    if value(current_position, "quantity") <= 0:
        issues.append("보유수량 없음")
    if value(current_position, "current_price") <= 0 and value(current_position, "average_price") <= 0:
        issues.append("현재가/평단 없음")
    if not text(current_position, "sector"):
        issues.append("섹터 미분류")
    if not text(current_decision, "decision") or not text(previous_decision, "decision"):
        issues.append("판단 라벨 누락")
    if abs(pnl_delta) >= 12:
        issues.append("손익률 급변 원천 확인")
    if value(previous_position, "market_value") <= 0:
        issues.append("이전 평가액 기준 약함")
    if issues:
        return "확인 필요 - " + ", ".join(issues[:4])
    return "평가액, 수량, 손익률, 판단 라벨이 모두 비교 가능"


def model_improvement_hint(
    current_position: Dict[str, object],
    current_decision: Dict[str, object],
    previous_decision: Dict[str, object],
    pressure_delta: float,
    pnl_delta: float,
    pressure_threshold: float,
) -> str:
    if abs(pressure_delta) < float(pressure_threshold or 0) and text(current_decision, "decision") != text(previous_decision, "decision"):
        return "판단 경계값 근처 흔들림을 줄이도록 히스테리시스와 최소 유지 시간을 추가"
    if abs(pnl_delta) >= 5:
        return "체결강도, 거래량, 이동평균으로 손익률 급변이 추세인지 일시 변동인지 검증"
    if value(current_position, "current_price") <= 0:
        return "현재가 원천을 연결해 평가액 기반 점수의 신뢰도를 먼저 보강"
    if not text(current_position, "sector"):
        return "섹터 매핑을 보강해 집중도 기반 exit pressure의 오탐을 줄이기"
    return "체결강도, 거래량, 이동평균, 투자자별 수급을 feature로 추가해 판단 변화의 재현성을 검증"
