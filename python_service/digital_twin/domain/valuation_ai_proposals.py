import re
from typing import Dict, List

from .instrument_profiles import instrument_profile_for_position
from .market_data import clamp, number
from .portfolio import Position


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "no", "off", "n", "미사용"}


def coupon_pct_from_position(position: Position) -> float:
    text = " ".join([str(position.name or ""), str(position.symbol or "")])
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*%", text)
    if not matches:
        return 0.0
    values = [number(item) for item in matches if number(item)]
    return max(values) if values else 0.0


def macro_dgs10(external_signals: Dict[str, object]) -> float:
    macro = external_signals.get("macro") if isinstance(external_signals, dict) and isinstance(external_signals.get("macro"), dict) else {}
    series = macro.get("series") if isinstance(macro.get("series"), dict) else {}
    dgs10 = series.get("DGS10") if isinstance(series.get("DGS10"), dict) else {}
    return number(dgs10.get("value")) or number(macro.get("dgs10")) or number(macro.get("DGS10"))


def _base_row(position: Position, method: str, formula: str, reliability: float) -> Dict[str, object]:
    symbol = str(position.symbol or "").upper().strip()
    return {
        "assumptionKey": symbol + ":ai-valuation-proposal",
        "symbol": symbol,
        "label": (position.name or symbol) + " AI 밸류에이션 제안",
        "provider": "Orbit Alpha AI",
        "source": "ai-valuation-proposal",
        "valuationMethod": method,
        "formula": formula,
        "reliabilityScore": reliability,
        "approvalStatus": "suggested",
        "requiresUserApproval": True,
        "aiGenerated": True,
    }


def preferred_income_ai_valuation_row(
    position: Position,
    external_signals: Dict[str, object],
    settings: Dict[str, object],
) -> Dict[str, object]:
    coupon = coupon_pct_from_position(position)
    current = number(position.current_price)
    if not coupon or not current:
        return {}
    profile = instrument_profile_for_position(position, settings)
    archetypes = set(profile.archetypes or [])
    par_value = number(settings.get("aiValuationPreferredParValue")) or 100.0
    risk_spread = number(settings.get("aiValuationPreferredRiskSpreadPct"))
    if not risk_spread:
        risk_spread = 5.0 if "BitcoinSensitiveIncome" in archetypes else 4.0
    base_rate = macro_dgs10(external_signals) or max(4.0, coupon - risk_spread + 0.5)
    required_yield = number(settings.get("aiValuationPreferredRequiredYieldPct"))
    if not required_yield:
        required_yield = max(coupon + 0.5, base_rate + risk_spread)
    required_yield = clamp(required_yield, 4.0, 20.0)
    annual_dividend = par_value * coupon / 100.0
    fair_value = annual_dividend / (required_yield / 100.0) if required_yield else 0.0
    if not fair_value:
        return {}
    row = _base_row(
        position,
        "ai-preferred-income-yield",
        "AI 제안 적정가 = 연간 배당 / 요구수익률",
        58.0,
    )
    row.update({
        "currentPrice": current,
        "fairValue": round(fair_value, 4),
        "annualDividend": round(annual_dividend, 4),
        "couponPct": round(coupon, 4),
        "parValue": round(par_value, 4),
        "requiredYieldPct": round(required_yield, 4),
        "minimumMarginOfSafetyPct": number(settings.get("aiValuationPreferredMinimumMarginPct")) or 8.0,
        "sourceReason": "우선주/인컴형은 보통주 PER보다 배당수익률 기준 적정가가 더 적합합니다.",
    })
    return row


def external_fundamental_ai_valuation_row(
    position: Position,
    external_signals: Dict[str, object],
) -> Dict[str, object]:
    symbol = str(position.symbol or "").upper().strip()
    overviews = external_signals.get("companyOverviews") if isinstance(external_signals, dict) and isinstance(external_signals.get("companyOverviews"), dict) else {}
    earnings = external_signals.get("earningsReports") if isinstance(external_signals, dict) and isinstance(external_signals.get("earningsReports"), dict) else {}
    overview = overviews.get(symbol) if isinstance(overviews.get(symbol), dict) else {}
    report = earnings.get(symbol) if isinstance(earnings.get(symbol), dict) else {}
    latest = report.get("latestQuarter") if isinstance(report.get("latestQuarter"), dict) else {}
    expected_eps = number(latest.get("estimatedEPS")) or number(latest.get("reportedEPS"))
    target_per = number(overview.get("forwardPE")) or number(overview.get("peRatio"))
    if not expected_eps or not target_per:
        return {}
    target_per = clamp(target_per, 3.0, 80.0)
    row = _base_row(
        position,
        "ai-eps-per-from-external-fundamentals",
        "AI 제안 적정가 = 외부 EPS x 외부 PER",
        62.0,
    )
    row.update({
        "currentPrice": number(position.current_price),
        "expectedEPS": round(expected_eps, 4),
        "targetPER": round(target_per, 4),
        "minimumMarginOfSafetyPct": 15.0,
        "sourceReason": "외부 기업개요와 최근 실적 EPS를 조합한 AI 초안입니다.",
    })
    return row


def current_price_anchor_ai_valuation_row(position: Position, settings: Dict[str, object]) -> Dict[str, object]:
    current = number(position.current_price)
    if not current:
        return {}
    row = _base_row(
        position,
        "ai-current-price-anchor",
        "AI 초기 기준가 = 현재가",
        35.0,
    )
    row.update({
        "currentPrice": current,
        "fairValue": current,
        "minimumMarginOfSafetyPct": number(settings.get("aiValuationBaselineMinimumMarginPct")) or 15.0,
        "sourceReason": "펀더멘털 입력이 없어 현재가를 임시 기준가로 둔 낮은 신뢰도 초안입니다.",
    })
    return row


def ai_valuation_proposal_rows(
    position: Position,
    external_signals: Dict[str, object],
    settings: Dict[str, object],
) -> List[Dict[str, object]]:
    settings = settings if isinstance(settings, dict) else {}
    if not truthy(settings.get("aiValuationAutoProposalEnabled"), True):
        return []
    profile = instrument_profile_for_position(position, settings)
    archetypes = set(profile.archetypes or [])
    rows: List[Dict[str, object]] = []
    if "PreferredIncome" in archetypes or "BitcoinSensitiveIncome" in archetypes:
        row = preferred_income_ai_valuation_row(position, external_signals or {}, settings)
        if row:
            rows.append(row)
    row = external_fundamental_ai_valuation_row(position, external_signals or {})
    if row:
        rows.append(row)
    if not rows and truthy(settings.get("aiValuationCurrentPriceAnchorEnabled"), True):
        row = current_price_anchor_ai_valuation_row(position, settings)
        if row:
            rows.append(row)
    return rows
