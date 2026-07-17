import re
from typing import Dict, List

from .instrument_profiles import instrument_profile_for_position
from .market_data import clamp, number
from .portfolio import Position
from .security_lines import SecurityLine, security_lines_for_symbol


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
        "approvalStatus": "ai_applied_pending_review",
        "activeStatus": "active",
        "requiresUserApproval": True,
        "autoApplied": True,
        "aiGenerated": True,
    }


def pct_distance(current: float, reference: float) -> float:
    return ((current / reference) - 1.0) * 100.0 if current and reference else 0.0


def crypto_market(external_signals: Dict[str, object], coin_id: str = "bitcoin") -> Dict[str, object]:
    markets = external_signals.get("cryptoMarkets") if isinstance(external_signals, dict) and isinstance(external_signals.get("cryptoMarkets"), dict) else {}
    direct = markets.get(coin_id)
    if isinstance(direct, dict):
        return direct
    for item in markets.values():
        if isinstance(item, dict) and str(item.get("symbol") or "").upper() == "BTC":
            return item
    return {}


def review_overrides(settings: Dict[str, object]) -> Dict[str, Dict[str, str]]:
    rows: Dict[str, Dict[str, str]] = {}
    text = str((settings or {}).get("valuationReviewOverrides") or "")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line and "," not in line:
            symbol, status = [part.strip() for part in line.split("=", 1)]
            note = ""
        else:
            parts = [part.strip() for part in line.split(",")]
            symbol = parts[0] if parts else ""
            status = parts[1] if len(parts) > 1 else ""
            note = parts[2] if len(parts) > 2 else ""
        symbol = symbol.upper()
        if symbol and status:
            rows[symbol] = {"status": status, "note": note}
    return rows


def apply_review_override(row: Dict[str, object], settings: Dict[str, object]) -> Dict[str, object]:
    symbol = str(row.get("symbol") or "").upper().strip()
    override = review_overrides(settings).get(symbol)
    if not override:
        return row
    status = str(override.get("status") or "").strip()
    note = str(override.get("note") or "").strip()
    row = dict(row)
    row["approvalStatus"] = status
    row["reviewStatus"] = status
    row["userReviewNote"] = note
    if status in {"user_approved", "approved"}:
        row["approvalStatus"] = "user_approved"
        row["activeStatus"] = "active"
        row["requiresUserApproval"] = False
        row["reliabilityScore"] = max(number(row.get("reliabilityScore")), 72.0)
    elif status in {"user_modified", "modified"}:
        row["approvalStatus"] = "user_modified"
        row["activeStatus"] = "active"
        row["requiresUserApproval"] = False
        row["reliabilityScore"] = max(number(row.get("reliabilityScore")), 78.0)
    elif status in {"user_rejected", "rejected"}:
        row["approvalStatus"] = "user_rejected"
        row["activeStatus"] = "rejected"
        row["requiresUserApproval"] = False
    return row


def usdkrw_rate_for_position(position: Position, external_signals: Dict[str, object]) -> float:
    rate = number(getattr(position, "exchange_rate", 0.0))
    if rate:
        return rate
    fx_rates = external_signals.get("fxRates") if isinstance(external_signals, dict) and isinstance(external_signals.get("fxRates"), dict) else {}
    for key in ["USDKRW", "USD/KRW", "USD"]:
        item = fx_rates.get(key) if isinstance(fx_rates.get(key), dict) else {}
        rate = number(item.get("rate")) or number(item.get("value"))
        if rate:
            return rate
    return 0.0


def adr_security_line_for_position(position: Position, settings: Dict[str, object] = None) -> SecurityLine:
    symbol = str(position.symbol or "").upper().strip()
    for line in security_lines_for_symbol(symbol, settings or {}):
        if line.symbol == symbol and line.is_adr and line.local_symbol:
            return line
    return None


def bitcoin_proxy_ai_valuation_row(
    position: Position,
    external_signals: Dict[str, object],
    settings: Dict[str, object],
) -> Dict[str, object]:
    current = number(position.current_price)
    if not current:
        return {}
    btc = crypto_market(external_signals or {}, "bitcoin")
    btc24 = number(btc.get("change24h"))
    btc7 = number(btc.get("change7d"))
    ma20_distance = pct_distance(current, number(position.ma20))
    ma60_distance = pct_distance(current, number(position.ma60))
    adjustment_pct = clamp(
        btc24 * 0.20 + btc7 * 0.35 + ma20_distance * 0.18 + ma60_distance * 0.06,
        -22.0,
        28.0,
    )
    fair_value = current * (1.0 + adjustment_pct / 100.0)
    row = _base_row(
        position,
        "ai-bitcoin-proxy-nav-draft",
        "AI 제안 기준가 = 현재가 x (1 + BTC/추세 보정)",
        48.0 if btc else 42.0,
    )
    row.update({
        "currentPrice": current,
        "fairValue": round(max(0.01, fair_value), 4),
        "minimumMarginOfSafetyPct": number(settings.get("aiValuationBitcoinProxyMinimumMarginPct")) or 18.0,
        "btcPrice": number(btc.get("price")),
        "btcChange24h": btc24,
        "btcChange7d": btc7,
        "missingInputs": ["BTC 보유량", "희석주식수", "순부채/우선주 부담"],
        "sourceReason": "비트코인 보유가치/NAV 모델이 적합하지만 최신 BTC 보유량, 희석주식수, 순부채가 없어 BTC 가격 변화와 종목 추세로 만든 자동 적용 초안입니다.",
    })
    return row


def semiconductor_cycle_ai_valuation_row(position: Position, settings: Dict[str, object]) -> Dict[str, object]:
    current = number(position.current_price)
    if not current:
        return {}
    ma5_distance = pct_distance(current, number(getattr(position, "ma5", 0)))
    ma20_distance = pct_distance(current, number(position.ma20))
    ma60_distance = pct_distance(current, number(position.ma60))
    volume_ratio = number(position.volume_ratio)
    volume_adjustment = clamp((volume_ratio - 1.0) * 2.5, -5.0, 6.0) if volume_ratio else 0.0
    adjustment_pct = clamp(ma5_distance * 0.12 + ma20_distance * 0.14 + ma60_distance * 0.08 + volume_adjustment, -20.0, 22.0)
    row = _base_row(
        position,
        "ai-semiconductor-cycle-draft",
        "AI 제안 기준가 = 현재가 x (1 + 반도체 사이클/추세 보정)",
        46.0,
    )
    row.update({
        "currentPrice": current,
        "fairValue": round(max(0.01, current * (1.0 + adjustment_pct / 100.0)), 4),
        "minimumMarginOfSafetyPct": number(settings.get("aiValuationSemiconductorMinimumMarginPct")) or 18.0,
        "missingInputs": ["예상 EPS", "목표 PER", "메모리 가격/업황 지표"],
        "sourceReason": "반도체는 EPS/PER와 업황 사이클이 핵심이지만 입력이 부족해 5일·20일·60일 평균과 거래량으로 만든 자동 적용 초안입니다.",
    })
    return row


def growth_quality_ai_valuation_row(position: Position, external_signals: Dict[str, object], settings: Dict[str, object]) -> Dict[str, object]:
    current = number(position.current_price)
    if not current:
        return {}
    ma5_distance = pct_distance(current, number(getattr(position, "ma5", 0)))
    ma20_distance = pct_distance(current, number(position.ma20))
    ma60_distance = pct_distance(current, number(position.ma60))
    dgs10 = macro_dgs10(external_signals or {})
    rate_penalty = max(0.0, dgs10 - 4.0) * 2.0 if dgs10 else 0.0
    adjustment_pct = clamp(ma5_distance * 0.10 + ma20_distance * 0.16 + ma60_distance * 0.08 - rate_penalty, -18.0, 24.0)
    row = _base_row(
        position,
        "ai-growth-quality-draft",
        "AI 제안 기준가 = 현재가 x (1 + 성장주 추세/금리 보정)",
        44.0,
    )
    row.update({
        "currentPrice": current,
        "fairValue": round(max(0.01, current * (1.0 + adjustment_pct / 100.0)), 4),
        "minimumMarginOfSafetyPct": number(settings.get("aiValuationGrowthMinimumMarginPct")) or 15.0,
        "missingInputs": ["예상 EPS", "목표 PER", "매출 성장률", "마진 전망"],
        "sourceReason": "성장주는 실적 성장률과 마진 전망이 핵심이지만 입력이 부족해 가격 추세와 금리 부담으로 만든 자동 적용 초안입니다.",
    })
    return row


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
    settings: Dict[str, object] = None,
) -> Dict[str, object]:
    symbol = str(position.symbol or "").upper().strip()
    overviews = external_signals.get("companyOverviews") if isinstance(external_signals, dict) and isinstance(external_signals.get("companyOverviews"), dict) else {}
    earnings = external_signals.get("earningsReports") if isinstance(external_signals, dict) and isinstance(external_signals.get("earningsReports"), dict) else {}
    source_symbol = symbol
    source_label = ""
    adr_line = None
    overview = overviews.get(source_symbol) if isinstance(overviews.get(source_symbol), dict) else {}
    report = earnings.get(source_symbol) if isinstance(earnings.get(source_symbol), dict) else {}
    if not overview or not report:
        adr_line = adr_security_line_for_position(position, settings or {})
        if adr_line:
            source_symbol = adr_line.local_symbol
            source_label = "본주 " + source_symbol + " "
            overview = overviews.get(source_symbol) if isinstance(overviews.get(source_symbol), dict) else {}
            report = earnings.get(source_symbol) if isinstance(earnings.get(source_symbol), dict) else {}
    latest = report.get("latestQuarter") if isinstance(report.get("latestQuarter"), dict) else {}
    expected_eps = number(latest.get("estimatedEPS")) or number(latest.get("reportedEPS"))
    target_per = number(overview.get("forwardPE")) or number(overview.get("peRatio"))
    if not expected_eps or not target_per:
        return {}
    target_per = clamp(target_per, 3.0, 80.0)
    fair_value = expected_eps * target_per
    method = "ai-eps-per-from-external-fundamentals"
    formula = "AI 제안 적정가 = 외부 EPS x 외부 PER"
    reliability = 62.0
    missing_inputs = []
    source_reason = source_label + "외부 기업개요와 최근 실적 EPS를 조합한 AI 초안입니다."
    if source_symbol != symbol:
        adr_ratio = number(getattr(adr_line, "adr_ratio", 0.0)) if adr_line else 0.0
        fx_rate = usdkrw_rate_for_position(position, external_signals)
        if adr_ratio and fx_rate:
            fair_value = fair_value * adr_ratio / fx_rate
            method = "ai-underlying-eps-per-adr-conversion"
            formula = "AI 제안 적정가 = 본주 EPS x 본주 PER x ADR비율 / USDKRW"
            reliability = 54.0
            source_reason = (
                "본주 " + source_symbol + "의 KIS EPS/PER를 사용하고, "
                + symbol + " ADR 비율 " + str(round(adr_ratio, 4))
                + "과 USD/KRW " + str(round(fx_rate, 4))
                + "로 달러 기준 ADR 적정가를 환산한 AI 초안입니다."
            )
        else:
            fair_value = 0.0
            missing_inputs = ["ADR 비율" if not adr_ratio else "", "USD/KRW 환율" if not fx_rate else ""]
            missing_inputs = [item for item in missing_inputs if item]
            source_reason = (
                "본주 " + source_symbol + "의 EPS/PER는 확인했지만 "
                + symbol + " ADR 적정가로 환산할 ADR 비율이나 환율이 부족합니다."
            )
    row = _base_row(
        position,
        method,
        formula,
        reliability,
    )
    row.update({
        "currentPrice": number(position.current_price),
        "fairValue": round(fair_value, 4) if fair_value else 0.0,
        "expectedEPS": round(expected_eps, 4),
        "targetPER": round(target_per, 4),
        "peRatio": number(overview.get("peRatio")),
        "forwardPE": number(overview.get("forwardPE")),
        "pbr": number(overview.get("pbr")),
        "bps": number(overview.get("bps")),
        "sourceSymbol": source_symbol,
        "underlyingSymbol": source_symbol if source_symbol != symbol else "",
        "adrRatio": round(number(getattr(adr_line, "adr_ratio", 0.0)), 4) if adr_line else 0.0,
        "sourceProvider": str(overview.get("provider") or report.get("provider") or ""),
        "minimumMarginOfSafetyPct": 15.0,
        "sourceReason": source_reason,
        "missingInputs": missing_inputs,
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
    if not rows and "BitcoinProxy" in archetypes:
        row = bitcoin_proxy_ai_valuation_row(position, external_signals or {}, settings)
        if row:
            rows.append(row)
    row = external_fundamental_ai_valuation_row(position, external_signals or {}, settings)
    if row:
        rows.append(row)
    if not rows and ({"SemiconductorHBM", "SemiconductorCyclical"} & archetypes):
        row = semiconductor_cycle_ai_valuation_row(position, settings)
        if row:
            rows.append(row)
    if not rows and ({"PlatformGrowth", "MegaCapQuality", "AIGrowth", "HighVolatilityGrowth"} & archetypes):
        row = growth_quality_ai_valuation_row(position, external_signals or {}, settings)
        if row:
            rows.append(row)
    if not rows and truthy(settings.get("aiValuationCurrentPriceAnchorEnabled"), False):
        row = current_price_anchor_ai_valuation_row(position, settings)
        if row:
            rows.append(row)
    reviewed = [apply_review_override(row, settings) for row in rows]
    return [row for row in reviewed if str(row.get("activeStatus") or "").strip() != "rejected"]
