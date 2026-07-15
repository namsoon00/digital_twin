import re
from typing import Dict, List

from ..domain.notification_ai_gate_text import _number


def _context_path_value(context: Dict[str, object], path: str):
    current = context or {}
    for part in [item for item in str(path or "").split(".") if item]:
        if isinstance(current, dict) and part in current:
            current = current.get(part)
            continue
        return None
    return current


def _first_number_from_paths(context: Dict[str, object], paths: List[str]):
    for path in paths:
        value = _context_path_value(context, path)
        if value not in (None, ""):
            return _number(value)
    return None


def _signed_decimal_text(value: float) -> str:
    magnitude = ("%.1f" % abs(float(value or 0))).rstrip("0").rstrip(".")
    if value > 0:
        return "+" + magnitude
    if value < 0:
        return "-" + magnitude
    return magnitude


def _signed_point_change_text(delta: float) -> str:
    if delta is None:
        return ""
    if abs(delta) < 0.05:
        return "이전 알림 대비 0.0%p 변화 없음"
    magnitude = ("%.1f" % abs(delta)).rstrip("0").rstrip(".")
    direction = "개선" if delta > 0 else "악화"
    return "이전 알림 대비 " + magnitude + "%p " + direction


def _profit_loss_band_label(rate: float) -> str:
    if rate <= -20:
        return "큰 손실"
    if rate <= -8:
        return "손실 관리"
    if rate < -2:
        return "손실 주의"
    if rate <= 2:
        return "거의 보합"
    if rate < 8:
        return "작은 수익"
    if rate < 20:
        return "수익 구간"
    return "큰 수익"


def _profit_loss_band_text(rate: float) -> str:
    return _profit_loss_band_label(rate) + "(" + _signed_decimal_text(rate) + "%)"


def _profit_loss_pair_from_reason(reason: str):
    match = re.search(
        r"([+-]?\d+(?:\.\d+)?)\s*%\s*(?:->|→)\s*([+-]?\d+(?:\.\d+)?)\s*%",
        str(reason or ""),
    )
    if not match:
        return None
    return (_number(match.group(1)), _number(match.group(2)))


def _profit_loss_rate_from_text(value: object):
    text = str(value or "")
    if not text.strip():
        return None
    match = re.search(r"(?:수익률|손익률|손익)\s*(?:[:：]|은|이|약)?\s*([+-]?\d+(?:\.\d+)?)\s*%", text)
    if not match:
        return None
    return _number(match.group(1))


def _profit_loss_current_rate(context: Dict[str, object], reason: str = ""):
    pair = _profit_loss_pair_from_reason(reason)
    if pair:
        return pair[1]
    current = _first_number_from_paths(context, [
        "profitLossRate",
        "profit_loss_rate",
        "pnlRate",
        "pnl_rate",
        "facts.profitLossRate",
        "ontologyInsight.facts.profitLossRate",
        "ontologyInsight.legacyModel.profitLossRate",
        "ontologyInsight.sourceFacts.profitLossRate",
        "ontologyInsight.executionPlan.sourceFacts.profitLossRate",
        "activeInvestmentOpinion.facts.profitLossRate",
        "activeInvestmentOpinion.legacyModel.profitLossRate",
        "activeInvestmentOpinion.sourceFacts.profitLossRate",
        "activeInvestmentOpinion.executionPlan.sourceFacts.profitLossRate",
        "ontologyRelationContext.facts.profitLossRate",
        "relationContext.facts.profitLossRate",
    ])
    if current is not None:
        return current
    for path in ["rawLines", "body", "summary", "currentStatus", "currentSituation"]:
        value = _context_path_value(context, path)
        rate = _profit_loss_rate_from_text(value)
        if rate is not None:
            return rate
    return None


def _profit_loss_delta(context: Dict[str, object], reason: str = ""):
    pair = _profit_loss_pair_from_reason(reason)
    if pair:
        previous, current = pair
        return current - previous
    delta = _first_number_from_paths(context, [
        "profitLossRateDeltaPct",
        "profitLossDeltaPct",
        "pnlDeltaPct",
        "pnlDelta",
        "facts.profitLossRateDeltaPct",
        "ontologyInsight.facts.profitLossRateDeltaPct",
        "activeInvestmentOpinion.facts.profitLossRateDeltaPct",
        "ontologyRelationContext.facts.profitLossRateDeltaPct",
        "relationContext.facts.profitLossRateDeltaPct",
    ])
    if delta is not None:
        return delta
    previous = _first_number_from_paths(context, [
        "previousProfitLossRate",
        "previous_profit_loss_rate",
        "facts.previousProfitLossRate",
        "ontologyInsight.facts.previousProfitLossRate",
        "activeInvestmentOpinion.facts.previousProfitLossRate",
        "ontologyRelationContext.facts.previousProfitLossRate",
        "relationContext.facts.previousProfitLossRate",
    ])
    current = _profit_loss_current_rate(context, reason)
    if previous is None or current is None:
        return None
    return current - previous


def _profit_loss_reason_present(reason: str) -> bool:
    return any(term in str(reason or "") for term in ["손익", "손실률", "수익률", "필수 발송 구간"])


def _profit_loss_change_summary(context: Dict[str, object], reason: str = "") -> str:
    current = _profit_loss_current_rate(context, reason)
    delta = _profit_loss_delta(context, reason)
    delta_text = _signed_point_change_text(delta) if delta is not None else ""
    if current is None and not delta_text:
        return ""
    if current is None and delta_text:
        return "손익 구간: " + delta_text
    if not delta_text and not _profit_loss_reason_present(reason):
        return ""
    parts = [_profit_loss_band_text(current)]
    if delta_text:
        parts.append(delta_text)
    return "손익 구간: " + " · ".join(parts)
