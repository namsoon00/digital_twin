import re
from typing import Dict, List

from .instrument_profiles import instrument_profile_for_position
from .market_data import clamp, number
from .portfolio import Position
from .security_lines import SecurityLine, security_lines_for_symbol
from .valuation_contracts import (
    period_is_annual_per_share,
    scenario_margins,
    unique_missing,
    valuation_decision_eligible,
    valuation_freshness_status,
    valuation_input_state,
    valuation_reliability_label,
    valuation_reliability_state,
)
from .valuation_model_evidence import (
    FUNDAMENTAL_MODEL_VERSION,
    collect_earnings_observations,
    collect_multiple_observations,
    earnings_scenario,
    fair_value_from_evidence,
    multiple_evidence_band,
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


def _base_row(position: Position, method: str, formula: str, reliability_state: str) -> Dict[str, object]:
    symbol = str(position.symbol or "").upper().strip()
    return {
        "assumptionKey": symbol + ":ai-valuation-proposal",
        "symbol": symbol,
        "label": (position.name or symbol) + " AI 밸류에이션 제안",
        "provider": "Orbit Alpha AI",
        "source": "ai-valuation-proposal",
        "valuationMethod": method,
        "formula": formula,
        "valuationDataState": reliability_state,
        "valuationReliabilityState": reliability_state,
        "valuationDataStateLabel": valuation_reliability_label(reliability_state),
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
        if str(row.get("valuationInputState") or "") == "sufficient":
            row["valuationDataState"] = "sufficient"
            row["valuationReliabilityState"] = "sufficient"
    elif status in {"user_modified", "modified"}:
        row["approvalStatus"] = "user_modified"
        row["activeStatus"] = "active"
        row["requiresUserApproval"] = False
        if str(row.get("valuationInputState") or "") == "sufficient":
            row["valuationDataState"] = "sufficient"
            row["valuationReliabilityState"] = "sufficient"
    elif status in {"user_rejected", "rejected"}:
        row["approvalStatus"] = "user_rejected"
        row["activeStatus"] = "rejected"
        row["requiresUserApproval"] = False
    row["valuationDataStateLabel"] = valuation_reliability_label(row.get("valuationReliabilityState"))
    row["valuationDecisionEligible"] = valuation_decision_eligible(
        source_type=str(row.get("valuationSourceType") or "ai"),
        reliability_state=row.get("valuationReliabilityState"),
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
    input_state = valuation_input_state(required, available)
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
    reliability_state = valuation_reliability_state(
        "ai", input_state, freshness_status=freshness, scenario_complete=bool(scenarios)
    )
    row = _base_row(
        position,
        "ai-bitcoin-treasury-nav-scenarios",
        "적정가 범위 = (BTC 가격 x BTC 보유량 - 순부채 - 우선주 부담) / 희석주식수 x NAV 프리미엄 시나리오",
        reliability_state,
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
        "valuationInputState": input_state,
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


def _fundamental_context(
    position: Position,
    external_signals: Dict[str, object],
    settings: Dict[str, object],
) -> Dict[str, object]:
    symbol = str(position.symbol or "").upper().strip()
    overviews = external_signals.get("companyOverviews") if isinstance(external_signals.get("companyOverviews"), dict) else {}
    earnings = external_signals.get("earningsReports") if isinstance(external_signals.get("earningsReports"), dict) else {}
    companies = external_signals.get("companyKnowledge") if isinstance(external_signals.get("companyKnowledge"), dict) else {}
    source_symbol = symbol
    adr_line = None
    overview = overviews.get(source_symbol) if isinstance(overviews.get(source_symbol), dict) else {}
    report = earnings.get(source_symbol) if isinstance(earnings.get(source_symbol), dict) else {}
    company = companies.get(source_symbol) if isinstance(companies.get(source_symbol), dict) else {}
    if not overview and not report:
        adr_line = adr_security_line_for_position(position, settings)
        if adr_line:
            source_symbol = adr_line.local_symbol
            overview = overviews.get(source_symbol) if isinstance(overviews.get(source_symbol), dict) else {}
            report = earnings.get(source_symbol) if isinstance(earnings.get(source_symbol), dict) else {}
            company = companies.get(source_symbol) if isinstance(companies.get(source_symbol), dict) else {}
    eps_observations = collect_earnings_observations(overview, report, company)
    eps = earnings_scenario(eps_observations)
    multiple_observations = collect_multiple_observations(overview, report, company)
    provider = str(overview.get("provider") or report.get("provider") or "")
    return {
        "symbol": symbol,
        "sourceSymbol": source_symbol,
        "overview": overview,
        "report": report,
        "company": company,
        "eps": eps,
        "epsObservations": eps_observations,
        "multipleObservations": multiple_observations,
        "provider": provider,
        "sourceType": _source_type(provider),
        "adrLine": adr_line,
    }


def _family_evidence(context: Dict[str, object], model_family: str) -> List[Dict[str, object]]:
    overview = context.get("overview") if isinstance(context.get("overview"), dict) else {}
    report = context.get("report") if isinstance(context.get("report"), dict) else {}
    company = context.get("company") if isinstance(context.get("company"), dict) else {}
    result: List[Dict[str, object]] = []
    raw_key = "cycleData" if model_family == "semiconductor" else "growthData"
    for owner, source in ((overview, "company-overview"), (report, "earnings-report")):
        values = owner.get(raw_key)
        rows = values if isinstance(values, list) else [values] if isinstance(values, dict) else []
        for index, item in enumerate(rows):
            if not isinstance(item, dict):
                continue
            numeric = {
                str(key): round(number(value), 4)
                for key, value in item.items()
                if isinstance(value, (int, float)) and number(value) == number(value)
            }
            if numeric:
                result.append({
                    "evidenceId": raw_key + ":" + str(index),
                    "source": source,
                    "provider": str(item.get("provider") or owner.get("provider") or ""),
                    "asOf": str(item.get("asOf") or owner.get("fetchedAt") or ""),
                    **numeric,
                })

    financials = company.get("financials") if isinstance(company.get("financials"), dict) else {}
    periods = []
    for frequency in ("annual", "interim"):
        rows = financials.get(frequency) if isinstance(financials.get(frequency), list) else []
        periods.extend([dict(item) for item in rows[:3] if isinstance(item, dict)])
    for index, item in enumerate(periods):
        fields = (
            ("revenueGrowthPct", "operatingIncomeGrowthPct", "operatingMarginPct", "netIncomeGrowthPct")
            if model_family == "semiconductor"
            else ("revenueGrowthPct", "operatingIncomeGrowthPct", "operatingMarginPct", "freeCashFlowGrowthPct")
        )
        numeric = {field: round(number(item.get(field)), 4) for field in fields if item.get(field) not in (None, "")}
        if numeric:
            result.append({
                "evidenceId": "company-financial:" + str(item.get("period") or index),
                "source": "company-knowledge.financials",
                "provider": "+".join(
                    str(entry.get("provider") or "")
                    for entry in company.get("provenance") or []
                    if isinstance(entry, dict) and entry.get("provider")
                ),
                "asOf": str(item.get("period") or ""),
                **numeric,
            })
    return result


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
    eps_value = number(eps.get("base"))
    eps_period = str(eps.get("period") or "")
    as_of = eps.get("asOf")
    freshness = valuation_freshness_status(as_of)
    multiple_band = multiple_evidence_band(context.get("multipleObservations") or [], archetypes)
    multiples = [number(multiple_band.get(key)) for key in ("low", "base", "high")]
    scenarios = fair_value_from_evidence(eps, multiple_band)
    family_evidence = _family_evidence(context, model_family)
    source_symbol = str(context.get("sourceSymbol") or position.symbol).upper()
    adr_line = context.get("adrLine")
    valuation_eps = dict(eps)
    adr_ratio = 0.0
    fx_rate = 0.0
    missing = []
    if not eps_value:
        missing.append("연간 또는 TTM EPS")
    if model_family == "semiconductor":
        if not family_evidence:
            missing.append("메모리 업황 또는 실적 사이클 지표")
    else:
        if not family_evidence:
            missing.append("매출 성장률·영업이익률 전망")
    if not bool(multiple_band.get("evidenceBacked")):
        missing.append("피어 또는 과거 PER 표본 3개 이상")
    if source_symbol != str(position.symbol or "").upper() and scenarios:
        adr_ratio = number(getattr(adr_line, "adr_ratio", 0.0)) if adr_line else 0.0
        fx_rate = usdkrw_rate_for_position(position, external_signals)
        if adr_ratio and fx_rate:
            for field in ("fairValueLow", "fairValue", "fairValueBase", "fairValueHigh"):
                scenarios[field] = round(number(scenarios.get(field)) * adr_ratio / fx_rate, 4)
            for field in ("low", "base", "high"):
                valuation_eps[field] = round(number(valuation_eps.get(field)) * adr_ratio / fx_rate, 6)
            valuation_eps["sourceCurrency"] = "KRW"
            valuation_eps["valuationCurrency"] = str(position.currency or "USD")
            valuation_eps["adrRatio"] = round(adr_ratio, 6)
            valuation_eps["fxRate"] = round(fx_rate, 6)
        else:
            scenarios = {}
            if not adr_ratio:
                missing.append("ADR 비율")
            if not fx_rate:
                missing.append("USD/KRW 환율")
    required = ["annualEPS", "targetMultipleBand"]
    available = ["annualEPS"] if eps_value else []
    if bool(multiple_band.get("evidenceBacked")):
        available.append("targetMultipleBand")
    family_input = "cycleData" if model_family == "semiconductor" else "growthData"
    if family_evidence:
        available.append(family_input)
    input_state = valuation_input_state(required + [family_input], available)
    confidence_rank = {"insufficient": 0, "low": 1, "medium": 2, "high": 3}
    confidence_values = [
        str(eps.get("confidence") or "insufficient"),
        str(multiple_band.get("confidence") or "insufficient"),
    ]
    valuation_confidence = min(
        confidence_values,
        key=lambda value: confidence_rank.get(value, 0),
    )
    eps_observation_ids = {str(item) for item in eps.get("observationIds") or [] if str(item or "")}
    multiple_observation_ids = (
        {str(item) for item in multiple_band.get("observationIds") or [] if str(item or "")}
        if bool(multiple_band.get("evidenceBacked"))
        else set()
    )
    all_observations = list(context.get("epsObservations") or []) + list(context.get("multipleObservations") or [])
    input_observations = [
        dict(item)
        for item in all_observations
        if isinstance(item, dict)
        and str(item.get("observationId") or "") in (eps_observation_ids | multiple_observation_ids)
    ]
    excluded_observations = []
    for item in all_observations:
        if not isinstance(item, dict):
            continue
        observation_id = str(item.get("observationId") or "")
        if observation_id in (eps_observation_ids | multiple_observation_ids):
            continue
        excluded_observations.append({
            "observationId": observation_id,
            "reason": (
                "lower-priority-earnings-horizon"
                if str(item.get("metric") or "") == "earnings-per-share"
                else "target-multiple-basis-not-eligible"
                if str(item.get("basis") or "") not in {"historical", "peer"}
                else "target-multiple-sample-count-below-minimum"
            ),
        })
    reliability_state = valuation_reliability_state(
        str(context.get("sourceType") or "external"),
        input_state,
        eps_period=eps_period,
        freshness_status=freshness,
        scenario_complete=bool(scenarios),
    )
    method = "ai-semiconductor-eps-per-scenarios" if model_family == "semiconductor" else "ai-growth-eps-per-scenarios"
    formula = "적정가 범위 = 출처가 확인된 연간 EPS 시나리오 x 과거·피어 PER 사분위 밴드"
    row = _base_row(position, method, formula, reliability_state)
    row.update({
        "currentPrice": current,
        **scenarios,
        "expectedEPS": round(number(valuation_eps.get("base")), 4) if eps_value else 0.0,
        "expectedEPSLow": round(number(valuation_eps.get("low")), 4) if eps_value else 0.0,
        "expectedEPSHigh": round(number(valuation_eps.get("high")), 4) if eps_value else 0.0,
        "targetPERLow": multiples[0] if len(multiples) >= 1 else 0.0,
        "targetPER": multiples[1] if len(multiples) >= 2 else 0.0,
        "targetPERHigh": multiples[2] if len(multiples) >= 3 else 0.0,
        "epsPeriod": eps_period,
        "multiplePeriod": "annual-compatible",
        "periodCompatible": period_is_annual_per_share(eps_period),
        "valuationAsOf": str(as_of or ""),
        "valuationFreshnessStatus": freshness,
        "valuationInputState": input_state,
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
            "연간 EPS와 실제 과거·피어 PER 표본으로 계산했습니다. 이동평균과 금리는 적정가 산식에 넣지 않았습니다."
            if bool(multiple_band.get("evidenceBacked"))
            else "연간 EPS는 확인했지만 과거·피어 PER 표본이 부족해 유형별 초기 밴드는 참고값으로만 표시하며 투자 판단에서는 제외합니다."
        ),
        "perValuationStatus": "available" if scenarios and multiple_band.get("evidenceBacked") else "provisional" if scenarios else "missing",
        "perValuationReason": "연간 EPS와 과거·피어 PER 표본의 기간·출처를 함께 확인했습니다." if multiple_band.get("evidenceBacked") else "PER 표본이 부족해 유형별 초기 밴드를 계산 참고값으로만 사용했습니다.",
        "preferredValuationMetric": "연간 EPS 시나리오 x 과거·피어 PER 사분위",
        "fundamentalDataSourcePriority": "KIS/yfinance 예상 EPS > OpenDART/SEC·TTM EPS > 과거·피어 PER 표본 > 유형별 초기 밴드(참고만)",
        "modelVersion": FUNDAMENTAL_MODEL_VERSION,
        "valuationConfidence": valuation_confidence,
        "epsScenario": dict(valuation_eps),
        "multipleBand": dict(multiple_band),
        "familyEvidence": family_evidence,
        "inputObservations": input_observations,
        "formulaTrace": {
            "modelVersion": FUNDAMENTAL_MODEL_VERSION,
            "formula": formula,
            "sourceEarningsScenario": dict(eps),
            "earningsScenario": dict(valuation_eps),
            "multipleBand": dict(multiple_band),
            "usedObservationIds": sorted(eps_observation_ids | multiple_observation_ids),
            "excludedObservations": excluded_observations,
            "adrConversionApplied": bool(
                source_symbol != str(position.symbol or "").upper() and adr_ratio and fx_rate
            ),
            "adrRatio": round(adr_ratio, 6),
            "fxRate": round(fx_rate, 6),
            "sourceSymbol": source_symbol,
        },
        "modelExclusionReasons": unique_missing(missing),
        "historicalMedianPER": number(multiple_band.get("base")) if "historical" in str(multiple_band.get("basis") or "") else 0.0,
        "peerPER": number(multiple_band.get("base")) if "peer" in str(multiple_band.get("basis") or "") else 0.0,
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
    input_state = valuation_input_state(
        ["coupon", "parValue", "requiredYield"],
        ["coupon", "parValue", "requiredYield"],
    )
    reliability_state = valuation_reliability_state(
        "ai", input_state, freshness_status=freshness, scenario_complete=True
    )
    row = _base_row(
        position,
        "ai-preferred-income-yield-scenarios",
        "적정가 범위 = 연간 배당 / 요구수익률 시나리오",
        reliability_state,
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
        "valuationInputState": input_state,
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
        "partial",
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
        "valuationInputState": "partial",
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
