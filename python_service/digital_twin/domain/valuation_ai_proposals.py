import re
from typing import Dict, List

from .instrument_profiles import instrument_profile_for_position
from .market_data import clamp, number
from .portfolio import Position
from .security_lines import SecurityLine, security_lines_for_symbol
from .valuation_contracts import (
    annual_eps_observation,
    fair_value_scenarios,
    period_is_annual_per_share,
    scenario_margins,
    unique_missing,
    valuation_confidence_label,
    valuation_decision_eligible,
    valuation_freshness_status,
    valuation_input_coverage,
    valuation_reliability_score,
)


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


def macro_dgs10_as_of(external_signals: Dict[str, object]) -> str:
    macro = external_signals.get("macro") if isinstance(external_signals, dict) and isinstance(external_signals.get("macro"), dict) else {}
    series = macro.get("series") if isinstance(macro.get("series"), dict) else {}
    dgs10 = series.get("DGS10") if isinstance(series.get("DGS10"), dict) else {}
    return str(dgs10.get("date") or dgs10.get("fetchedAt") or macro.get("fetchedAt") or "")


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
        "valuationConfidenceLabel": valuation_confidence_label(reliability),
        "valuationDecisionEligible": False,
        "valuationSourceType": "ai",
        "valuationCurrency": str(position.currency or ("KRW" if str(position.market or "").upper() == "KR" else "USD")),
        "perShare": True,
        "modelVersion": "valuation-scenarios-v2",
        "approvalStatus": "ai_applied_pending_review",
        "activeStatus": "active",
        "requiresUserApproval": True,
        "autoApplied": True,
        "aiGenerated": True,
        "perValuationStatus": "",
        "perValuationReason": "",
        "preferredValuationMetric": "",
        "fundamentalDataSourcePriority": "",
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
    row["valuationConfidenceLabel"] = valuation_confidence_label(row.get("reliabilityScore"))
    row["valuationDecisionEligible"] = valuation_decision_eligible(
        source_type=str(row.get("valuationSourceType") or "ai"),
        reliability_score=row.get("reliabilityScore"),
        approval_status=row.get("approvalStatus"),
        freshness_status=str(row.get("valuationFreshnessStatus") or "unknown"),
        period_compatible=bool(row.get("periodCompatible", True)),
        fair_value=row.get("fairValue"),
    )
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
    btc_price = number(btc.get("price"))
    btc_holdings = number(settings.get("aiValuationBitcoinHoldings"))
    diluted_shares = number(settings.get("aiValuationDilutedShares"))
    net_debt = number(settings.get("aiValuationNetDebt"))
    preferred_equity = number(settings.get("aiValuationPreferredEquity"))
    nav_premium_low = number(settings.get("aiValuationBitcoinNavPremiumLowPct")) or -10.0
    nav_premium_base = number(settings.get("aiValuationBitcoinNavPremiumBasePct")) or 10.0
    nav_premium_high = number(settings.get("aiValuationBitcoinNavPremiumHighPct")) or 35.0
    required = ["btcPrice", "btcHoldings", "dilutedShares"]
    available = [
        key
        for key, value in {
            "btcPrice": btc_price,
            "btcHoldings": btc_holdings,
            "dilutedShares": diluted_shares,
            "netDebt": net_debt,
            "preferredEquity": preferred_equity,
        }.items()
        if value or key in {"netDebt", "preferredEquity"}
    ]
    coverage = valuation_input_coverage(required, available)
    nav_per_share = (
        (btc_price * btc_holdings - net_debt - preferred_equity) / diluted_shares
        if btc_price and btc_holdings and diluted_shares
        else 0.0
    )
    scenarios = {}
    if nav_per_share > 0:
        scenarios = {
            "fairValueLow": round(nav_per_share * (1.0 + nav_premium_low / 100.0), 4),
            "fairValue": round(nav_per_share * (1.0 + nav_premium_base / 100.0), 4),
            "fairValueBase": round(nav_per_share * (1.0 + nav_premium_base / 100.0), 4),
            "fairValueHigh": round(nav_per_share * (1.0 + nav_premium_high / 100.0), 4),
        }
    as_of = btc.get("fetchedAt") or btc.get("updatedAt") or btc.get("lastUpdated")
    freshness = valuation_freshness_status(as_of, 2.0)
    reliability = valuation_reliability_score(
        "ai", coverage, freshness_status=freshness, scenario_complete=bool(scenarios)
    )
    row = _base_row(
        position,
        "ai-bitcoin-treasury-nav-scenarios",
        "적정가 범위 = (BTC 가격 x BTC 보유량 - 순부채 - 우선주 부담) / 희석주식수 x NAV 프리미엄 시나리오",
        reliability,
    )
    row.update({
        "currentPrice": current,
        **scenarios,
        "minimumMarginOfSafetyPct": number(settings.get("aiValuationBitcoinProxyMinimumMarginPct")) or 18.0,
        "btcPrice": btc_price,
        "btcChange24h": number(btc.get("change24h")),
        "btcChange7d": number(btc.get("change7d")),
        "btcHoldings": btc_holdings,
        "dilutedShares": diluted_shares,
        "netDebt": net_debt,
        "preferredEquity": preferred_equity,
        "navPerShare": round(nav_per_share, 4) if nav_per_share else 0.0,
        "valuationAsOf": str(as_of or ""),
        "valuationFreshnessStatus": freshness,
        "inputCoveragePct": coverage,
        "missingInputs": unique_missing([
            "BTC 보유량" if not btc_holdings else "",
            "희석주식수" if not diluted_shares else "",
            "BTC 현재가" if not btc_price else "",
        ]),
        "sourceReason": (
            "BTC 보유량, 희석주식수, 순부채와 우선주 부담을 반영한 NAV 시나리오입니다."
            if scenarios
            else "실제 BTC 보유량과 희석주식수가 없어 가격 추세를 적정가로 바꾸지 않고 계산을 보류했습니다."
        ),
        "perValuationStatus": "not_applicable",
        "perValuationReason": "비트코인 민감 종목은 일반 PER만으로 설명력이 낮아 비트코인 보유가치, 순부채, 희석주식수와 BTC 가격 민감도를 먼저 봅니다.",
        "preferredValuationMetric": "비트코인 보유가치/NAV",
        "fundamentalDataSourcePriority": "BTC 보유가치/NAV > 외부 PER",
        "periodCompatible": True,
        "valuationDecisionEligible": False,
    })
    row.update(scenario_margins(current, row.get("fairValueLow"), row.get("fairValue"), row.get("fairValueHigh")))
    return row


def _source_type(provider: object) -> str:
    text = str(provider or "").casefold()
    if "kis" in text:
        return "broker"
    if "sec" in text or "dart" in text:
        return "official"
    return "external"


def _multiple_band(archetypes: set, dgs10: float = 0.0) -> List[float]:
    if "AIGrowth" in archetypes:
        band = [24.0, 34.0, 44.0]
    elif "MegaCapQuality" in archetypes and "SemiconductorCyclical" not in archetypes:
        band = [20.0, 28.0, 34.0]
    elif "PlatformGrowth" in archetypes:
        band = [18.0, 26.0, 34.0]
    elif "SemiconductorHBM" in archetypes:
        band = [8.0, 12.0, 16.0]
    elif "SemiconductorCyclical" in archetypes:
        band = [7.0, 10.0, 13.0]
    elif "HighVolatilityGrowth" in archetypes:
        band = [10.0, 18.0, 28.0]
    else:
        band = [8.0, 12.0, 18.0]
    rate_factor = clamp(1.0 - max(0.0, dgs10 - 4.0) * 0.05, 0.75, 1.0) if dgs10 else 1.0
    return [round(item * rate_factor, 2) for item in band]


def _fundamental_context(
    position: Position,
    external_signals: Dict[str, object],
    settings: Dict[str, object],
) -> Dict[str, object]:
    symbol = str(position.symbol or "").upper().strip()
    overviews = external_signals.get("companyOverviews") if isinstance(external_signals.get("companyOverviews"), dict) else {}
    earnings = external_signals.get("earningsReports") if isinstance(external_signals.get("earningsReports"), dict) else {}
    source_symbol = symbol
    adr_line = None
    overview = overviews.get(source_symbol) if isinstance(overviews.get(source_symbol), dict) else {}
    report = earnings.get(source_symbol) if isinstance(earnings.get(source_symbol), dict) else {}
    if not overview and not report:
        adr_line = adr_security_line_for_position(position, settings)
        if adr_line:
            source_symbol = adr_line.local_symbol
            overview = overviews.get(source_symbol) if isinstance(overviews.get(source_symbol), dict) else {}
            report = earnings.get(source_symbol) if isinstance(earnings.get(source_symbol), dict) else {}
    eps = annual_eps_observation(overview, report)
    provider = str(overview.get("provider") or report.get("provider") or "")
    return {
        "symbol": symbol,
        "sourceSymbol": source_symbol,
        "overview": overview,
        "report": report,
        "eps": eps,
        "provider": provider,
        "sourceType": _source_type(provider),
        "adrLine": adr_line,
    }


def _fundamental_scenario_row(
    position: Position,
    external_signals: Dict[str, object],
    settings: Dict[str, object],
    model_family: str,
) -> Dict[str, object]:
    current = number(position.current_price)
    if not current:
        return {}
    profile = instrument_profile_for_position(position, settings)
    archetypes = set(profile.archetypes or [])
    context = _fundamental_context(position, external_signals, settings)
    eps = context.get("eps") if isinstance(context.get("eps"), dict) else {}
    eps_value = number(eps.get("value"))
    eps_period = str(eps.get("period") or "")
    as_of = eps.get("asOf")
    freshness = valuation_freshness_status(as_of)
    multiples = _multiple_band(archetypes, macro_dgs10(external_signals))
    scenarios = fair_value_scenarios(eps_value, eps_period, multiples)
    source_symbol = str(context.get("sourceSymbol") or position.symbol).upper()
    adr_line = context.get("adrLine")
    missing = []
    if not eps_value:
        missing.append("연간 또는 TTM EPS")
    if model_family == "semiconductor":
        missing.extend(["메모리 가격/업황 지표", "피어 또는 과거 PER 범위"])
    else:
        missing.extend(["매출 성장률", "영업이익률 전망", "피어 또는 과거 PER 범위"])
    if source_symbol != str(position.symbol or "").upper() and scenarios:
        adr_ratio = number(getattr(adr_line, "adr_ratio", 0.0)) if adr_line else 0.0
        fx_rate = usdkrw_rate_for_position(position, external_signals)
        if adr_ratio and fx_rate:
            for field in ("fairValueLow", "fairValue", "fairValueBase", "fairValueHigh"):
                scenarios[field] = round(number(scenarios.get(field)) * adr_ratio / fx_rate, 4)
        else:
            scenarios = {}
            if not adr_ratio:
                missing.append("ADR 비율")
            if not fx_rate:
                missing.append("USD/KRW 환율")
    required = ["annualEPS", "targetMultipleBand"]
    available = ["annualEPS"] if eps_value else []
    if multiples:
        available.append("targetMultipleBand")
    coverage = valuation_input_coverage(required + (["cycleData"] if model_family == "semiconductor" else ["growthData"]), available)
    reliability = valuation_reliability_score(
        str(context.get("sourceType") or "external"),
        coverage,
        eps_period=eps_period,
        freshness_status=freshness,
        scenario_complete=bool(scenarios),
    )
    method = "ai-semiconductor-eps-per-scenarios" if model_family == "semiconductor" else "ai-growth-eps-per-scenarios"
    row = _base_row(position, method, "적정가 범위 = 연간/TTM EPS x 종목 유형별 PER 시나리오", reliability)
    row.update({
        "currentPrice": current,
        **scenarios,
        "expectedEPS": round(eps_value, 4) if eps_value else 0.0,
        "targetPERLow": multiples[0] if len(multiples) >= 1 else 0.0,
        "targetPER": multiples[1] if len(multiples) >= 2 else 0.0,
        "targetPERHigh": multiples[2] if len(multiples) >= 3 else 0.0,
        "epsPeriod": eps_period,
        "multiplePeriod": "annual-compatible",
        "periodCompatible": period_is_annual_per_share(eps_period),
        "valuationAsOf": str(as_of or ""),
        "valuationFreshnessStatus": freshness,
        "inputCoveragePct": coverage,
        "sourceProvider": str(context.get("provider") or ""),
        "sourceSymbol": source_symbol,
        "underlyingSymbol": source_symbol if source_symbol != str(position.symbol or "").upper() else "",
        "adrRatio": round(number(getattr(adr_line, "adr_ratio", 0.0)), 4) if adr_line else 0.0,
        "peRatio": number(context.get("overview", {}).get("peRatio")),
        "forwardPE": number(context.get("overview", {}).get("forwardPE")),
        "pbr": number(context.get("overview", {}).get("pbr")),
        "bps": number(context.get("overview", {}).get("bps")),
        "minimumMarginOfSafetyPct": number(settings.get("aiValuationSemiconductorMinimumMarginPct" if model_family == "semiconductor" else "aiValuationGrowthMinimumMarginPct")) or (18.0 if model_family == "semiconductor" else 15.0),
        "missingInputs": unique_missing(missing),
        "sourceReason": (
            "연간/TTM EPS와 반도체 유형별 보수·기준·낙관 PER 범위를 사용했습니다. 이동평균은 적정가가 아니라 별도의 가격 흐름 근거로만 사용합니다."
            if model_family == "semiconductor"
            else "연간/TTM EPS와 성장주 유형별 보수·기준·낙관 PER 범위를 사용하고 금리 부담을 배수에 반영했습니다. 이동평균은 적정가 계산에서 제외합니다."
        ),
        "perValuationStatus": "available" if scenarios else "missing",
        "perValuationReason": "연간/TTM EPS와 같은 기간 기준의 PER 시나리오를 사용했습니다." if scenarios else "연간/TTM EPS 또는 ADR 환산 입력이 부족해 적정가 계산을 보류했습니다.",
        "preferredValuationMetric": "연간/TTM EPS x 유형별 PER 범위",
        "fundamentalDataSourcePriority": "KIS/공식 연간 EPS > yfinance/Alpha Vantage TTM·선행 EPS > 유형별 PER 범위",
        "valuationDecisionEligible": False,
    })
    row.update(scenario_margins(current, row.get("fairValueLow"), row.get("fairValue"), row.get("fairValueHigh")))
    return row


def semiconductor_cycle_ai_valuation_row(
    position: Position,
    external_signals: Dict[str, object],
    settings: Dict[str, object],
) -> Dict[str, object]:
    return _fundamental_scenario_row(position, external_signals, settings, "semiconductor")


def growth_quality_ai_valuation_row(position: Position, external_signals: Dict[str, object], settings: Dict[str, object]) -> Dict[str, object]:
    return _fundamental_scenario_row(position, external_signals, settings, "growth")


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
    bull_yield = max(4.0, required_yield - 1.0)
    bear_yield = min(20.0, required_yield + 1.5)
    fair_value_low = annual_dividend / (bear_yield / 100.0)
    fair_value = annual_dividend / (required_yield / 100.0)
    fair_value_high = annual_dividend / (bull_yield / 100.0)
    valuation_as_of = macro_dgs10_as_of(external_signals)
    freshness = valuation_freshness_status(valuation_as_of, 14.0)
    coverage = valuation_input_coverage(
        ["coupon", "parValue", "requiredYield"],
        ["coupon", "parValue", "requiredYield"],
    )
    reliability = valuation_reliability_score(
        "ai", coverage, freshness_status=freshness, scenario_complete=True
    )
    row = _base_row(
        position,
        "ai-preferred-income-yield-scenarios",
        "적정가 범위 = 연간 배당 / 요구수익률 시나리오",
        reliability,
    )
    row.update({
        "currentPrice": current,
        "fairValueLow": round(fair_value_low, 4),
        "fairValue": round(fair_value, 4),
        "fairValueBase": round(fair_value, 4),
        "fairValueHigh": round(fair_value_high, 4),
        "annualDividend": round(annual_dividend, 4),
        "couponPct": round(coupon, 4),
        "parValue": round(par_value, 4),
        "requiredYieldPct": round(required_yield, 4),
        "bearRequiredYieldPct": round(bear_yield, 4),
        "bullRequiredYieldPct": round(bull_yield, 4),
        "minimumMarginOfSafetyPct": number(settings.get("aiValuationPreferredMinimumMarginPct")) or 8.0,
        "sourceReason": "우선주/인컴형은 보통주 PER보다 배당수익률 기준 적정가가 더 적합합니다.",
        "perValuationStatus": "not_applicable",
        "perValuationReason": "우선주와 배당형 상품은 보통주 이익 배수보다 배당, 액면 기준가, 요구수익률이 가격 설명에 더 직접적입니다.",
        "preferredValuationMetric": "배당수익률/요구수익률",
        "fundamentalDataSourcePriority": "배당 조건 > 금리/요구수익률 > 외부 PER",
        "valuationFreshnessStatus": freshness,
        "valuationAsOf": valuation_as_of,
        "inputCoveragePct": coverage,
        "periodCompatible": True,
        "valuationDecisionEligible": False,
    })
    row.update(scenario_margins(current, fair_value_low, fair_value, fair_value_high))
    return row


def external_fundamental_ai_valuation_row(
    position: Position,
    external_signals: Dict[str, object],
    settings: Dict[str, object] = None,
) -> Dict[str, object]:
    profile = instrument_profile_for_position(position, settings or {})
    archetypes = set(profile.archetypes or [])
    family = "semiconductor" if {"SemiconductorHBM", "SemiconductorCyclical"} & archetypes else "growth"
    return _fundamental_scenario_row(position, external_signals or {}, settings or {}, family)


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
        "perValuationStatus": "missing",
        "perValuationReason": "PER/EPS와 적정가 입력이 없어 현재가를 임시 기준가로만 사용했습니다.",
        "preferredValuationMetric": "임시 현재가 기준",
        "fundamentalDataSourcePriority": "사용자 적정가 또는 외부 PER/EPS 필요",
        "valuationFreshnessStatus": "unknown",
        "periodCompatible": False,
        "valuationDecisionEligible": False,
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
    if not rows and ({"SemiconductorHBM", "SemiconductorCyclical"} & archetypes):
        row = semiconductor_cycle_ai_valuation_row(position, external_signals or {}, settings)
        if row:
            rows.append(row)
    if not rows and ({"PlatformGrowth", "MegaCapQuality", "AIGrowth", "HighVolatilityGrowth"} & archetypes):
        row = growth_quality_ai_valuation_row(position, external_signals or {}, settings)
        if row:
            rows.append(row)
    if not rows:
        row = external_fundamental_ai_valuation_row(position, external_signals or {}, settings)
        if row and number(row.get("expectedEPS")):
            rows.append(row)
    if not rows and truthy(settings.get("aiValuationCurrentPriceAnchorEnabled"), False):
        row = current_price_anchor_ai_valuation_row(position, settings)
        if row:
            rows.append(row)
    reviewed = [apply_review_override(row, settings) for row in rows]
    return [row for row in reviewed if str(row.get("activeStatus") or "").strip() != "rejected"]
