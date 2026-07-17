from typing import Dict, Iterable, List, Tuple

from .alert_formatting import compact_number
from .market_data import number
from .ontology_contracts import PortfolioOntology
from .ontology_schema import add_entity, add_relation
from .portfolio import Position
from .portfolio_ontology_market_concepts import symbol_key
from .portfolio_ontology_runtime_concepts import valuation_assumption_rows
from .security_lines import security_lines_for_symbol
from .valuation_ai_proposals import ai_valuation_proposal_rows


VALUATION_NUMERIC_KEYS = {
    "currentPrice": ("currentPrice", "price", "현재가"),
    "fairValue": ("fairValue", "fairValuePrice", "적정가"),
    "fairValuePrice": ("fairValue", "fairValuePrice", "적정가"),
    "targetPrice": ("fairValue", "fairValuePrice", "목표가"),
    "analystTargetPrice": ("fairValue", "fairValuePrice", "애널리스트 목표가"),
    "expectedEPS": ("expectedEPS", "expectedEPS", "예상 EPS"),
    "expectedEps": ("expectedEPS", "expectedEPS", "예상 EPS"),
    "eps": ("expectedEPS", "expectedEPS", "예상 EPS"),
    "reportedEPS": ("reportedEPS", "reportedEPS", "발표 EPS"),
    "estimatedEPS": ("estimatedEPS", "estimatedEPS", "예상 EPS"),
    "targetPER": ("targetPER", "targetPER", "목표 PER"),
    "targetPer": ("targetPER", "targetPER", "목표 PER"),
    "targetPE": ("targetPER", "targetPER", "목표 PER"),
    "peRatio": ("peRatio", "peRatio", "PER"),
    "forwardPE": ("forwardPE", "forwardPE", "선행 PER"),
    "pegRatio": ("pegRatio", "pegRatio", "PEG"),
    "beta": ("beta", "beta", "베타"),
    "dividendYield": ("dividendYield", "dividendYield", "배당수익률"),
    "annualDividend": ("annualDividend", "annualDividend", "연간 배당"),
    "annualDividendPerShare": ("annualDividend", "annualDividend", "연간 배당"),
    "requiredYieldPct": ("requiredYieldPct", "requiredYieldPct", "요구수익률"),
    "requiredYield": ("requiredYieldPct", "requiredYieldPct", "요구수익률"),
    "couponPct": ("couponPct", "couponPct", "표면 배당률"),
    "parValue": ("parValue", "parValue", "액면 기준가"),
    "marginOfSafetyPct": ("marginOfSafetyPct", "marginOfSafetyPct", "안전마진"),
    "minimumMarginOfSafetyPct": ("minimumMarginOfSafetyPct", "minimumMarginOfSafetyPct", "요구 안전마진"),
    "peerPER": ("peerPER", "peerPER", "피어 PER"),
    "historicalMedianPER": ("historicalMedianPER", "historicalMedianPER", "과거 중앙 PER"),
}


def normalize_assumption_row(row: Dict[str, object]) -> Dict[str, object]:
    payload = dict(row or {})
    nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    payload.update(nested)
    has_structured_formula_inputs = any(payload.get(key) not in (None, "") for key in ("expectedEPS", "expectedEps", "eps", "targetPER", "targetPer", "targetPE"))
    for item in payload.get("values") or []:
        text = str(item or "").strip()
        if not text:
            continue
        if "=" in text:
            key, value = text.split("=", 1)
            payload[key.strip()] = value.strip()
        elif not has_structured_formula_inputs and "fairValue" not in payload and "fairValuePrice" not in payload and number(text):
            payload["fairValue"] = number(text)
    return payload


def normalized_symbol_set(row: Dict[str, object]) -> List[str]:
    values = [
        row.get("symbol"),
        row.get("ticker"),
        row.get("code"),
        row.get("assumptionKey"),
    ]
    symbols = []
    for value in values:
        text = str(value or "").upper().strip()
        if text and text not in {"PORTFOLIO", "ALL"} and text not in symbols:
            symbols.append(text)
    return symbols


def position_runtime_valuation_rows(runtime_context: Dict[str, object], symbol: str) -> List[Dict[str, object]]:
    settings = runtime_context.get("settings") if isinstance(runtime_context, dict) else {}
    rows = valuation_assumption_rows(settings.get("valuationAssumptions") if isinstance(settings, dict) else "")
    normalized_symbol = str(symbol or "").upper().strip()
    result = []
    for raw_row in rows:
        row = normalize_assumption_row(raw_row)
        symbols = normalized_symbol_set(row)
        if normalized_symbol in symbols:
            row.setdefault("provider", "RuntimeSettings")
            row.setdefault("source", "runtime-settings")
            result.append(row)
    return result


def external_valuation_rows(external_signals: Dict[str, object], symbol: str) -> List[Dict[str, object]]:
    normalized_symbol = str(symbol or "").upper().strip()
    rows: List[Dict[str, object]] = []
    source_symbols = [normalized_symbol]
    for line in security_lines_for_symbol(normalized_symbol):
        if line.symbol == normalized_symbol and line.is_adr and line.local_symbol not in source_symbols:
            source_symbols.append(line.local_symbol)
    overviews = external_signals.get("companyOverviews") if isinstance(external_signals.get("companyOverviews"), dict) else {}
    for source_symbol in source_symbols:
        overview = overviews.get(source_symbol)
        if not isinstance(overview, dict):
            continue
        is_underlying = source_symbol != normalized_symbol
        rows.append({
            "assumptionKey": normalized_symbol + ":" + source_symbol + ":company-overview",
            "symbol": normalized_symbol,
            "sourceSymbol": source_symbol,
            "label": (("본주 " + source_symbol + " 기반 ") if is_underlying else "") + str(overview.get("name") or normalized_symbol) + " 기업개요 밸류에이션",
            "provider": str(overview.get("provider") or "External"),
            "source": "company-overview",
            "fairValue": number(overview.get("analystTargetPrice")),
            "peRatio": number(overview.get("peRatio")),
            "forwardPE": number(overview.get("forwardPE")),
            "pegRatio": number(overview.get("pegRatio")),
            "beta": number(overview.get("beta")),
            "dividendYield": number(overview.get("dividendYield")),
            "valuationMethod": "analyst-target-and-multiple",
            "formula": "애널리스트 목표가와 PER/베타를 참고",
        })
    earnings = external_signals.get("earningsReports") if isinstance(external_signals.get("earningsReports"), dict) else {}
    for source_symbol in source_symbols:
        report = earnings.get(source_symbol)
        latest = report.get("latestQuarter") if isinstance(report, dict) and isinstance(report.get("latestQuarter"), dict) else {}
        if not latest:
            continue
        is_underlying = source_symbol != normalized_symbol
        rows.append({
            "assumptionKey": normalized_symbol + ":" + source_symbol + ":earnings",
            "symbol": normalized_symbol,
            "sourceSymbol": source_symbol,
            "label": (("본주 " + source_symbol + " 기반 ") if is_underlying else "") + normalized_symbol + " 실적 EPS 밸류에이션",
            "provider": str(report.get("provider") or "External"),
            "source": "earnings-report",
            "reportedEPS": number(latest.get("reportedEPS")),
            "estimatedEPS": number(latest.get("estimatedEPS")),
            "valuationMethod": "earnings-context",
            "formula": "최근 실적 EPS 참고",
        })
    return rows


def value_for(row: Dict[str, object], *keys: str) -> float:
    for key in keys:
        value = row.get(key)
        if value not in (None, "") and number(value):
            return number(value)
    return 0.0


def valuation_values(row: Dict[str, object], position: Position) -> Dict[str, object]:
    current_price = value_for(row, "currentPrice", "price") or number(position.current_price)
    fair_value = value_for(row, "fairValue", "fairValuePrice", "targetPrice", "analystTargetPrice")
    expected_eps = value_for(row, "expectedEPS", "expectedEps", "eps", "estimatedEPS", "reportedEPS")
    target_per = value_for(row, "targetPER", "targetPer", "targetPE")
    annual_dividend = value_for(row, "annualDividend", "annualDividendPerShare")
    required_yield = value_for(row, "requiredYieldPct", "requiredYield")
    coupon_pct = value_for(row, "couponPct", "coupon")
    par_value = value_for(row, "parValue")
    if not fair_value and expected_eps and target_per:
        fair_value = expected_eps * target_per
    if not fair_value and annual_dividend and required_yield:
        fair_value = annual_dividend / (required_yield / 100.0)
    margin = value_for(row, "marginOfSafetyPct")
    if not margin and fair_value and current_price:
        margin = ((fair_value / current_price) - 1) * 100
    expensive_premium = ((current_price / fair_value) - 1) * 100 if current_price and fair_value else 0.0
    method = str(row.get("valuationMethod") or row.get("method") or "").strip()
    if not method:
        method = "eps-per" if expected_eps and target_per else "manual-fair-value" if fair_value else "valuation-context"
    formula = str(row.get("formula") or "").strip()
    if not formula and expected_eps and target_per:
        formula = "적정가 = 예상 EPS x 목표 PER"
    elif not formula and annual_dividend and required_yield:
        formula = "AI 제안 적정가 = 연간 배당 / 요구수익률"
    elif not formula and fair_value:
        formula = "적정가 = 사용자가 입력한 적정가"
    raw_missing = row.get("missingInputs")
    if isinstance(raw_missing, list):
        missing = [str(item).strip() for item in raw_missing if str(item or "").strip()]
    elif raw_missing:
        missing = [str(item).strip() for item in str(raw_missing).replace(";", ",").split(",") if str(item).strip()]
    else:
        missing = []
    if not current_price:
        missing.append("currentPrice")
    method_lower = method.casefold()
    if not fair_value and not (expected_eps and target_per) and not (annual_dividend and required_yield):
        if "preferred" in method_lower or "yield" in method_lower or annual_dividend or required_yield:
            missing.extend(["fairValue", "annualDividend", "requiredYieldPct"])
        else:
            missing.extend(["fairValue", "expectedEPS", "targetPER"])
    return {
        "currentPrice": round(current_price, 4) if current_price else 0.0,
        "fairValue": round(fair_value, 4) if fair_value else 0.0,
        "fairValuePrice": round(fair_value, 4) if fair_value else 0.0,
        "expectedEPS": round(expected_eps, 4) if expected_eps else 0.0,
        "targetPER": round(target_per, 4) if target_per else 0.0,
        "annualDividend": round(annual_dividend, 4) if annual_dividend else 0.0,
        "requiredYieldPct": round(required_yield, 4) if required_yield else 0.0,
        "couponPct": round(coupon_pct, 4) if coupon_pct else 0.0,
        "parValue": round(par_value, 4) if par_value else 0.0,
        "marginOfSafetyPct": round(margin, 2) if margin else 0.0,
        "expensivePremiumPct": round(expensive_premium, 2) if expensive_premium else 0.0,
        "minimumMarginOfSafetyPct": value_for(row, "minimumMarginOfSafetyPct") or 15.0,
        "valuationMethod": method,
        "formula": formula,
        "missingInputs": sorted(set(missing)),
    }


def metric_rows(row: Dict[str, object]) -> Iterable[Tuple[str, str, float]]:
    normalized = normalize_assumption_row(row)
    for source_key, canonical in VALUATION_NUMERIC_KEYS.items():
        if source_key not in normalized:
            continue
        _canonical_key, public_key, label = canonical
        value = number(normalized.get(source_key))
        if value:
            yield public_key, label, value


def valuation_relation_props(row: Dict[str, object], values: Dict[str, object], label: str) -> Dict[str, object]:
    return {
        "source": str(row.get("source") or row.get("provider") or "valuation"),
        "provider": str(row.get("provider") or ""),
        "polarity": "context",
        "aiInfluenceLabel": label,
        "valuationMethod": values.get("valuationMethod"),
        "formula": values.get("formula"),
        "marginOfSafetyPct": values.get("marginOfSafetyPct"),
        "fairValue": values.get("fairValue"),
        "approvalStatus": str(row.get("approvalStatus") or ""),
        "activeStatus": str(row.get("activeStatus") or ""),
        "requiresUserApproval": bool(row.get("requiresUserApproval")),
        "autoApplied": bool(row.get("autoApplied")),
    }


def add_valuation_row_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    position: Position,
    row: Dict[str, object],
) -> None:
    symbol = symbol_key(position)
    row = normalize_assumption_row(row)
    values = valuation_values(row, position)
    key = str(row.get("assumptionKey") or row.get("symbol") or symbol).strip()
    label = str(row.get("label") or row.get("name") or (position.name or symbol) + " 밸류에이션").strip()
    is_ai_proposal = str(row.get("source") or "").casefold() == "ai-valuation-proposal" or bool(row.get("aiGenerated"))
    is_active = bool(values.get("fairValue")) and str(row.get("activeStatus") or "active").casefold() != "rejected"
    tbox_classes = ["ValuationAssumption", "StrategySignal", "ValuationSignal"]
    if is_ai_proposal:
        tbox_classes.append("AIValuationProposal")
    base_props = {
        "symbol": symbol,
        "provider": str(row.get("provider") or ""),
        "source": str(row.get("source") or row.get("provider") or "valuation"),
        "assumptionKey": key,
        "label": label,
        "payload": {k: v for k, v in row.items() if k not in {"payload"}},
        "approvalStatus": str(row.get("approvalStatus") or ""),
        "activeStatus": str(row.get("activeStatus") or ""),
        "requiresUserApproval": bool(row.get("requiresUserApproval")),
        "autoApplied": bool(row.get("autoApplied")),
        "reviewStatus": str(row.get("reviewStatus") or row.get("approvalStatus") or ""),
        "userReviewNote": str(row.get("userReviewNote") or ""),
        **values,
    }
    model_label = str(row.get("valuationMethod") or values.get("valuationMethod") or "valuation-context")
    model_id = add_entity(graph, "valuation-model", symbol + ":" + model_label, label + " 모델", {
        "tboxClass": "ValuationModel",
        "tboxClasses": ["ValuationModel", "StrategySignal", "ValuationSignal"],
        "symbol": symbol,
        "valuationMethod": model_label,
        "formula": values.get("formula"),
        "source": str(row.get("source") or row.get("provider") or "valuation"),
        "provider": str(row.get("provider") or ""),
    })
    assumption_id = add_entity(graph, "valuation-assumption", symbol + ":" + key, label, {
        "tboxClass": "AIValuationProposal" if is_ai_proposal else "ValuationAssumption",
        "tboxClasses": tbox_classes,
        **base_props,
    })
    props = valuation_relation_props(row, values, label)
    add_relation(graph, stock_id, assumption_id, "HAS_VALUATION", weight=0.88, properties=props)
    add_relation(graph, stock_id, model_id, "USES_VALUATION_MODEL", weight=0.84, properties=props)
    add_relation(graph, assumption_id, model_id, "USES_VALUATION_MODEL", weight=0.84, properties=props)
    if is_ai_proposal:
        add_relation(graph, stock_id, assumption_id, "HAS_AI_VALUATION_PROPOSAL", weight=0.86, properties={
            **props,
            "polarity": "context",
            "aiInfluenceLabel": "AI 밸류에이션 제안: 자동 적용, 사용자 검토 전",
        })
    if is_active:
        active_classes = ["ActiveValuation", "ValuationAssumption", "ValuationSignal"]
        if is_ai_proposal:
            active_classes.append("AIValuationProposal")
        active_id = add_entity(graph, "active-valuation", symbol + ":" + key, label + " 활성 밸류에이션", {
            "tboxClass": "ActiveValuation",
            "tboxClasses": active_classes,
            **base_props,
        })
        add_relation(graph, stock_id, active_id, "HAS_ACTIVE_VALUATION", weight=0.9, properties=props)
        add_relation(graph, active_id, assumption_id, "DERIVED_FROM_AI_VALUATION_PROPOSAL" if is_ai_proposal else "DERIVED_FROM_VALUATION_ASSUMPTION", weight=0.88, properties=props)
        if is_ai_proposal and bool(row.get("requiresUserApproval")):
            review_id = add_entity(graph, "valuation-review", symbol + ":" + key, label + " 사용자 검토 대기", {
                "tboxClass": "UserValuationReview",
                "tboxClasses": ["UserValuationReview", "ValuationAssumption", "ValuationSignal"],
                "symbol": symbol,
                "approvalStatus": str(row.get("approvalStatus") or "ai_applied_pending_review"),
                "reviewStatus": str(row.get("reviewStatus") or row.get("approvalStatus") or "ai_applied_pending_review"),
                "userReviewNote": str(row.get("userReviewNote") or ""),
                "requiresUserApproval": True,
                "autoApplied": True,
                "source": "valuation-review",
            })
            add_relation(graph, active_id, review_id, "AWAITS_USER_REVIEW", weight=0.86, properties={
                **props,
                "polarity": "context",
                "aiInfluenceLabel": "사용자 검토 전까지 AI 초안으로 자동 적용",
            })
    for field, metric_label, value in metric_rows(row):
        metric_id = add_entity(graph, "valuation-metric", symbol + ":" + key + ":" + field, metric_label + " " + compact_number(value), {
            "tboxClass": "ValuationMetric",
            "tboxClasses": ["Observation", "FundamentalObservation", "ValuationMetric", "ValuationSignal"],
            "symbol": symbol,
            "field": field,
            "value": round(value, 4),
            "valueNumber": round(value, 4),
            **{field: round(value, 4)},
            "provider": str(row.get("provider") or ""),
            "source": str(row.get("source") or row.get("provider") or "valuation"),
        })
        metric_props = {**props, "field": field, "aiInfluenceLabel": metric_label}
        add_relation(graph, stock_id, metric_id, "HAS_OBSERVATION", weight=0.82, properties=metric_props)
        add_relation(graph, stock_id, metric_id, "HAS_VALUATION_METRIC", weight=0.82, properties=metric_props)
    if number(values.get("fairValue")):
        estimate_id = add_entity(graph, "fair-value-estimate", symbol + ":" + key, (position.name or symbol) + " 적정가 " + compact_number(values.get("fairValue")), {
            "tboxClass": "FairValueEstimate",
            "tboxClasses": ["ValuationAssumption", "FairValueEstimate", "ValuationSignal"],
            **base_props,
        })
        add_relation(graph, stock_id, estimate_id, "HAS_FAIR_VALUE_ESTIMATE", weight=0.86, properties=props)
        add_relation(graph, stock_id, estimate_id, "HAS_VALUATION", weight=0.86, properties=props)
    if number(values.get("marginOfSafetyPct")):
        margin = number(values.get("marginOfSafetyPct"))
        margin_id = add_entity(graph, "margin-of-safety", symbol + ":" + key, (position.name or symbol) + " 안전마진 " + str(round(margin, 1)) + "%", {
            "tboxClass": "MarginOfSafety",
            "tboxClasses": ["ValuationAssumption", "MarginOfSafety", "ValuationSignal"],
            **base_props,
        })
        margin_props = {
            **props,
            "polarity": "support" if margin >= number(values.get("minimumMarginOfSafetyPct")) else "risk" if margin <= -10 else "context",
            "supportImpact": min(12.0, max(0.0, margin / 2)) if margin > 0 else 0.0,
            "riskImpact": min(12.0, abs(margin) / 2) if margin < 0 else 0.0,
            "aiInfluenceLabel": "안전마진 " + str(round(margin, 1)) + "%",
        }
        add_relation(graph, stock_id, margin_id, "HAS_MARGIN_OF_SAFETY", weight=0.9, properties=margin_props)
        add_relation(graph, stock_id, margin_id, "HAS_VALUATION", weight=0.9, properties=margin_props)
    if value_for(row, "peRatio", "forwardPE", "pegRatio", "peerPER", "historicalMedianPER"):
        relative_id = add_entity(graph, "relative-valuation", symbol + ":" + key, (position.name or symbol) + " 상대 밸류에이션", {
            "tboxClass": "RelativeValuation",
            "tboxClasses": ["ValuationAssumption", "RelativeValuation", "ValuationSignal"],
            **base_props,
            "peRatio": value_for(row, "peRatio"),
            "forwardPE": value_for(row, "forwardPE"),
            "pegRatio": value_for(row, "pegRatio"),
            "peerPER": value_for(row, "peerPER"),
            "historicalMedianPER": value_for(row, "historicalMedianPER"),
        })
        add_relation(graph, stock_id, relative_id, "COMPARES_WITH_PEER_MULTIPLE", weight=0.78, properties=props)
        add_relation(graph, stock_id, relative_id, "HAS_VALUATION", weight=0.78, properties=props)
    if values.get("missingInputs"):
        missing_id = add_entity(graph, "missing-data", symbol + ":valuation:" + key, (position.name or symbol) + " 밸류에이션 부족 데이터", {
            "tboxClass": "MissingData",
            "tboxClasses": ["Observation", "DataQuality", "MissingData", "CoverageGap", "DataQualitySignal"],
            "symbol": symbol,
            "field": "valuationInputs",
            "missingInputs": values.get("missingInputs"),
            "dataScope": "valuation",
            "source": "valuation-gate",
        })
        add_relation(graph, stock_id, missing_id, "HAS_DATA_QUALITY", weight=0.72, properties={
            "source": "valuation-gate",
            "polarity": "risk",
            "riskImpact": 5.0,
            "dataScope": "valuation",
            "aiInfluenceLabel": "밸류에이션 부족 데이터: " + ", ".join(values.get("missingInputs") or []),
        })


def add_position_valuation_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    position: Position,
    external_signals: Dict[str, object],
    runtime_context: Dict[str, object],
) -> None:
    symbol = symbol_key(position)
    rows = position_runtime_valuation_rows(runtime_context or {}, symbol)
    rows.extend(external_valuation_rows(external_signals or {}, symbol))
    settings = runtime_context.get("settings") if isinstance(runtime_context, dict) and isinstance(runtime_context.get("settings"), dict) else {}
    if not any(number(valuation_values(normalize_assumption_row(row), position).get("fairValue")) for row in rows):
        rows.extend(ai_valuation_proposal_rows(position, external_signals or {}, settings))
    for row in rows:
        add_valuation_row_concepts(graph, stock_id, position, row)
