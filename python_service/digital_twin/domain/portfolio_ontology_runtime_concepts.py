from typing import Dict, List

from .accounts import investment_strategy_profile, message_delivery_profile
from .market_data import number
from .ontology_contracts import PortfolioOntology, entity_id
from .ontology_schema import add_entity, add_relation
from .portfolio import Position
from .portfolio_ontology_catalog import (
    INSIGHT_TYPES,
    OPERATIONAL_PIPELINES,
    SENSITIVE_SETTING_TOKENS,
    SETTING_CONCEPT_TYPES,
)


def position_source(position: Position) -> str:
    return str(getattr(position, "source", "") or "holding").strip().lower() or "holding"


def is_watchlist_position(position: Position) -> bool:
    return position_source(position) == "watchlist"


def is_holding_position(position: Position) -> bool:
    return not is_watchlist_position(position) and (number(position.market_value) > 0 or number(position.quantity) > 0)


def safe_setting_value(key: str, value: object) -> object:
    lowered = str(key or "").replace("-", "").replace("_", "").lower()
    if any(token.replace("_", "") in lowered for token in SENSITIVE_SETTING_TOKENS):
        return "configured" if value not in (None, "", False) else ""
    text = str(value or "")
    return text[:1200] if len(text) > 1200 else value

def valuation_assumption_rows(value: object) -> List[Dict[str, object]]:
    if isinstance(value, list):
        rows = []
        for index, item in enumerate(value):
            if isinstance(item, dict):
                row = dict(item)
                row.setdefault("assumptionKey", str(row.get("symbol") or row.get("name") or index))
                rows.append(row)
            elif str(item or "").strip():
                rows.extend(valuation_assumption_rows(str(item)))
        return rows
    if isinstance(value, dict):
        rows = []
        for key, item in sorted(value.items()):
            row = dict(item) if isinstance(item, dict) else {"value": item}
            row.setdefault("assumptionKey", str(key))
            if not row.get("symbol") and str(key).upper() != "PORTFOLIO":
                row["symbol"] = str(key).upper()
            rows.append(row)
        return rows
    text = str(value or "").strip()
    if not text:
        return []
    rows: List[Dict[str, object]] = []
    normalized = text.replace("\r", "\n").replace(";", "\n")
    for index, line in enumerate([item.strip() for item in normalized.split("\n") if item.strip()]):
        parts = [item.strip() for item in line.replace("|", ",").replace("\t", ",").split(",")]
        symbol = str(parts[0] if parts else "").upper().strip()
        key = symbol or "line-" + str(index + 1)
        rows.append({
            "assumptionKey": key,
            "symbol": symbol,
            "rawLine": line,
            "values": parts[1:] if len(parts) > 1 else [],
        })
    return rows

def add_valuation_assumption_concepts(graph: PortfolioOntology, portfolio_node_id: str, value: object) -> None:
    for row in valuation_assumption_rows(value):
        key = str(row.get("assumptionKey") or row.get("symbol") or row.get("name") or "portfolio").strip()
        if not key:
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        label = str(row.get("label") or row.get("name") or (symbol + " 밸류에이션 가정" if symbol else "포트폴리오 밸류에이션 가정"))
        assumption_id = add_entity(graph, "valuation-assumption", key, label, {
            "tboxClass": "ValuationAssumption",
            "tboxClasses": ["ValuationAssumption", "StrategySignal"],
            "symbol": symbol,
            "assumptionKey": key,
            "label": label,
            "rawLine": row.get("rawLine"),
            "values": row.get("values") if isinstance(row.get("values"), list) else [],
            "payload": {k: v for k, v in row.items() if k not in {"assumptionKey", "symbol", "label", "name"}},
        })
        add_relation(graph, portfolio_node_id, assumption_id, "HAS_VALUATION", weight=1.0, properties={"source": "runtime-settings", "aiInfluenceLabel": label})

def add_runtime_setting_concepts(graph: PortfolioOntology, portfolio_node_id: str, runtime_context: Dict[str, object]) -> None:
    settings = runtime_context.get("settings") if isinstance(runtime_context, dict) else {}
    if not isinstance(settings, dict):
        return
    for key, value in sorted(settings.items()):
        if value in (None, "", False):
            continue
        concept = SETTING_CONCEPT_TYPES.get(str(key))
        tbox_class, relation_type = concept if concept else ("RuntimeSetting", "HAS_RUNTIME_SETTING")
        setting_id = add_entity(graph, "runtime-setting", key, str(key), {
            "tboxClass": tbox_class,
            "key": str(key),
            "value": safe_setting_value(str(key), value),
        })
        add_relation(graph, portfolio_node_id, setting_id, relation_type, weight=1.0, properties={"source": "runtime-settings", "aiInfluenceLabel": str(key)})
    add_valuation_assumption_concepts(graph, portfolio_node_id, settings.get("valuationAssumptions"))

def add_runtime_metadata_concepts(graph: PortfolioOntology, portfolio_node_id: str, runtime_context: Dict[str, object]) -> None:
    metadata = runtime_context.get("metadata") if isinstance(runtime_context, dict) else {}
    if not isinstance(metadata, dict):
        return
    for key, value in sorted(metadata.items()):
        if value in (None, "", False):
            continue
        metadata_id = add_entity(graph, "runtime-metadata", key, "metadata:" + str(key), {
            "tboxClass": "RuntimeSetting",
            "key": str(key),
            "value": safe_setting_value(str(key), value),
        })
        add_relation(graph, portfolio_node_id, metadata_id, "HAS_RUNTIME_SETTING", weight=1.0, properties={"source": "runtime-metadata", "aiInfluenceLabel": "metadata:" + str(key)})

def add_account_delivery_profile_concepts(
    graph: PortfolioOntology,
    account_node_id: str,
    portfolio_node_id: str,
    account_context: Dict[str, object],
) -> None:
    profile_payload = account_context.get("messageDeliveryProfile") if isinstance(account_context.get("messageDeliveryProfile"), dict) else {}
    level = profile_payload.get("level") or account_context.get("messageDeliveryLevel")
    profile = message_delivery_profile(level)
    profile_id = add_entity(graph, "message-delivery-profile", str(profile.get("level") or "absoluteBeginner"), str(profile.get("label") or "메시지 전달 수준"), {
        "tboxClass": "MessageDeliveryProfile",
        "level": profile.get("level"),
        "label": profile.get("label"),
        "detailLevel": profile.get("detailLevel"),
        "terminology": profile.get("terminology"),
        "scoreVisibility": profile.get("scoreVisibility"),
        "ruleVisibility": profile.get("ruleVisibility"),
        "description": profile.get("description"),
    })
    add_relation(graph, account_node_id, profile_id, "HAS_MESSAGE_DELIVERY_PROFILE", weight=1.0, properties={"source": "account-context"})
    add_relation(graph, portfolio_node_id, profile_id, "USES_MESSAGE_DELIVERY_PROFILE", weight=1.0, properties={"source": "account-context"})


def account_investment_strategy_profile(account_context: Dict[str, object]) -> Dict[str, object]:
    profile_payload = account_context.get("investmentStrategy") if isinstance(account_context.get("investmentStrategy"), dict) else {}
    profile_key = profile_payload.get("profile") or account_context.get("investmentStrategyProfile")
    return investment_strategy_profile(profile_key)


def add_account_investment_strategy_concepts(
    graph: PortfolioOntology,
    account_node_id: str,
    portfolio_node_id: str,
    account_context: Dict[str, object],
) -> Dict[str, object]:
    profile = account_investment_strategy_profile(account_context)
    profile_key = str(profile.get("profile") or "balanced")
    profile_id = add_entity(graph, "investment-strategy-profile", profile_key, str(profile.get("label") or "투자 전략 성향"), {
        "tboxClass": "InvestmentStrategyProfile",
        "tboxClasses": ["InvestmentStrategyProfile", "InvestorProfile", "StrategySignal"],
        "profile": profile_key,
        "label": profile.get("label"),
        "riskTolerance": profile.get("riskTolerance"),
        "timeHorizon": profile.get("timeHorizon"),
        "lossTolerancePct": number(profile.get("lossTolerancePct")),
        "profitProtectionPct": number(profile.get("profitProtectionPct")),
        "maxPositionWeightPct": number(profile.get("maxPositionWeightPct")),
        "maxSectorWeightPct": number(profile.get("maxSectorWeightPct")),
        "fxExposureReviewPct": number(profile.get("fxExposureReviewPct")),
        "addBuyPolicy": profile.get("addBuyPolicy"),
        "addBuyWatchSignalMin": number(profile.get("addBuyWatchSignalMin")),
        "addBuyReviewSignalMin": number(profile.get("addBuyReviewSignalMin")),
        "allowLossAddBuyReview": bool(profile.get("allowLossAddBuyReview")),
        "defaultHoldingRole": profile.get("defaultHoldingRole"),
        "watchlistActionPolicy": profile.get("watchlistActionPolicy"),
        "holdingActionPolicy": profile.get("holdingActionPolicy"),
        "description": profile.get("description"),
        "promptInstruction": profile.get("promptInstruction"),
    })
    risk_budget_id = add_entity(graph, "risk-budget", profile_key, str(profile.get("label") or "투자 전략") + " 손실 허용 기준", {
        "tboxClass": "RiskBudget",
        "profile": profile_key,
        "lossTolerancePct": number(profile.get("lossTolerancePct")),
        "maxPositionWeightPct": number(profile.get("maxPositionWeightPct")),
        "maxSectorWeightPct": number(profile.get("maxSectorWeightPct")),
        "fxExposureReviewPct": number(profile.get("fxExposureReviewPct")),
        "addBuyPolicy": profile.get("addBuyPolicy"),
        "addBuyWatchSignalMin": number(profile.get("addBuyWatchSignalMin")),
        "addBuyReviewSignalMin": number(profile.get("addBuyReviewSignalMin")),
        "allowLossAddBuyReview": bool(profile.get("allowLossAddBuyReview")),
    })
    profit_policy_id = add_entity(graph, "profit-policy", profile_key, str(profile.get("label") or "투자 전략") + " 수익 보호 기준", {
        "tboxClass": "ProfitPolicy",
        "profile": profile_key,
        "profitProtectionPct": number(profile.get("profitProtectionPct")),
        "addBuyPolicy": profile.get("addBuyPolicy"),
        "holdingActionPolicy": profile.get("holdingActionPolicy"),
    })
    add_relation(graph, account_node_id, profile_id, "HAS_INVESTOR_PROFILE", weight=1.0, properties={"source": "account-context"})
    add_relation(graph, portfolio_node_id, profile_id, "USES_INVESTMENT_STRATEGY_PROFILE", weight=1.0, properties={"source": "account-context"})
    add_relation(graph, profile_id, risk_budget_id, "HAS_RISK_BUDGET", weight=1.0, properties={"source": "account-context"})
    add_relation(graph, profile_id, profit_policy_id, "HAS_PROFIT_POLICY", weight=1.0, properties={"source": "account-context"})
    return {
        "profileId": profile_id,
        "riskBudgetId": risk_budget_id,
        "profitPolicyId": profit_policy_id,
        "profile": profile,
    }


def role_key_for_position(position: Position, strategy_profile: Dict[str, object]) -> str:
    if is_watchlist_position(position):
        return "watchlistEntry"
    role = str((strategy_profile or {}).get("defaultHoldingRole") or "coreSatellite").strip()
    return role or "coreSatellite"


def role_label(role_key: str) -> str:
    labels = {
        "core": "핵심 보유",
        "coreSatellite": "핵심·위성 보유",
        "growthCore": "성장 핵심 보유",
        "highConviction": "고확신 보유",
        "watchlistEntry": "관심 진입 후보",
    }
    return labels.get(str(role_key or ""), str(role_key or "포지션 역할"))


def add_position_strategy_role_concepts(
    graph: PortfolioOntology,
    position_node_id: str,
    stock_node_id: str,
    strategy_context: Dict[str, object],
    position: Position,
) -> None:
    if not strategy_context:
        return
    profile = strategy_context.get("profile") if isinstance(strategy_context.get("profile"), dict) else {}
    profile_id = str(strategy_context.get("profileId") or "")
    risk_budget_id = str(strategy_context.get("riskBudgetId") or "")
    profit_policy_id = str(strategy_context.get("profitPolicyId") or "")
    profile_key = str(profile.get("profile") or "balanced")
    role_key = role_key_for_position(position, profile)
    strategy_relation_props = {
        "source": "account-strategy",
        "profile": profile_key,
        "role": role_key,
        "positionSource": position_source(position),
        "lossTolerancePct": number(profile.get("lossTolerancePct")),
        "profitProtectionPct": number(profile.get("profitProtectionPct")),
        "addBuyPolicy": profile.get("addBuyPolicy"),
        "addBuyWatchSignalMin": number(profile.get("addBuyWatchSignalMin")),
        "addBuyReviewSignalMin": number(profile.get("addBuyReviewSignalMin")),
        "allowLossAddBuyReview": bool(profile.get("allowLossAddBuyReview")),
        "watchlistActionPolicy": profile.get("watchlistActionPolicy"),
        "holdingActionPolicy": profile.get("holdingActionPolicy"),
    }
    role_id = add_entity(graph, "position-role", profile_key + ":" + role_key, role_label(role_key), {
        "tboxClass": "PositionRole",
        "profile": profile_key,
        "role": role_key,
        "source": position_source(position),
        "lossTolerancePct": number(profile.get("lossTolerancePct")),
        "profitProtectionPct": number(profile.get("profitProtectionPct")),
        "addBuyPolicy": profile.get("addBuyPolicy"),
        "addBuyWatchSignalMin": number(profile.get("addBuyWatchSignalMin")),
        "addBuyReviewSignalMin": number(profile.get("addBuyReviewSignalMin")),
        "allowLossAddBuyReview": bool(profile.get("allowLossAddBuyReview")),
        "watchlistActionPolicy": profile.get("watchlistActionPolicy"),
        "holdingActionPolicy": profile.get("holdingActionPolicy"),
    })
    for node_id in [position_node_id, stock_node_id]:
        if not node_id:
            continue
        add_relation(graph, node_id, role_id, "HAS_POSITION_ROLE", weight=1.0, properties=strategy_relation_props)
        if risk_budget_id:
            add_relation(graph, node_id, risk_budget_id, "HAS_RISK_BUDGET", weight=1.0, properties=strategy_relation_props)
        if profit_policy_id:
            add_relation(graph, node_id, profit_policy_id, "HAS_PROFIT_POLICY", weight=1.0, properties=strategy_relation_props)
        if profile_id:
            add_relation(graph, node_id, profile_id, "EVALUATED_UNDER_STRATEGY", weight=1.0, properties=strategy_relation_props)
    if profile_id:
        add_relation(graph, profile_id, role_id, "HAS_POSITION_ROLE", weight=1.0, properties={"source": "account-strategy", "profile": profile_key, "role": role_key})

def runtime_settings(runtime_context: Dict[str, object]) -> Dict[str, object]:
    settings = runtime_context.get("settings") if isinstance(runtime_context, dict) else {}
    return settings if isinstance(settings, dict) else {}

def configured_minutes(settings: Dict[str, object], primary_key: str, fallback: float, secondary_key: str = "") -> float:
    raw = settings.get(primary_key)
    if raw in (None, "") and secondary_key:
        raw = settings.get(secondary_key)
    value = number(raw)
    return value if value > 0 else number(fallback)

def add_operational_world_concepts(
    graph: PortfolioOntology,
    portfolio_node_id: str,
    runtime_context: Dict[str, object],
    observed_positions: List[Position],
) -> None:
    settings = runtime_settings(runtime_context)
    collection_policy_id = add_entity(graph, "collection-policy", "adaptive-polling", "적응형 데이터 수집 정책", {
        "tboxClass": "CollectionPolicy",
        "mode": "adaptive",
        "description": "데이터는 성격별 목표 주기로 갱신하고, 알림은 의미 변화가 있을 때만 검토합니다.",
    })
    market_session_id = add_entity(graph, "market-session", "runtime-market-session", "현재 시장 세션", {
        "tboxClass": "MarketSession",
        "mode": str(runtime_context.get("mode") or ""),
        "positionCount": len([item for item in observed_positions if is_holding_position(item)]),
        "watchlistCount": len([item for item in observed_positions if is_watchlist_position(item)]),
    })
    reasoning_id = add_entity(graph, "reasoning-cycle", "ontologyReasoning", "ontologyReasoning", {
        "tboxClass": "ReasoningCycle",
        "trigger": "every-data-update",
        "description": "데이터 갱신 직후 관계 영향과 인사이트를 재계산합니다.",
    })
    strategy_analysis_id = add_entity(graph, "analysis-job", "strategyAnalysis", "전략 분석", {
        "tboxClass": "AnalysisJob",
        "role": "supporting-analysis",
        "description": "기존 모델 점수와 관계 규칙을 보조 분석으로 유지합니다.",
    })
    insight_policy_id = add_entity(graph, "insight-policy", "meaningful-change", "의미 변화 인사이트 정책", {
        "tboxClass": "InsightPolicy",
        "mode": "meaningful-change",
        "minimumNovelty": number(settings.get("notificationNoveltyThreshold")) or 0.65,
        "minimumConfidence": number(settings.get("notificationConfidenceThreshold")) or 0.55,
    })
    importance_gate_id = add_entity(graph, "importance-gate", "materiality-first", "중요 변경 게이트", {
        "tboxClass": "ImportanceGate",
        "mode": "materiality-first",
        "enabled": str(settings.get("materialityGateEnabled") or "1") not in {"0", "false", "False", "off"},
        "minimumScore": number(settings.get("materialityMinimumScore")) or 65,
        "marketMinimumScore": number(settings.get("marketMaterialityMinimumScore")) or 65,
        "newsMinimumScore": number(settings.get("newsMaterialityMinimumScore")) or 65,
        "priceChangePct": number(settings.get("marketMaterialityPriceChangePct")) or 0.6,
        "trendDistancePct": number(settings.get("marketMaterialityTrendDistancePct")) or 2.0,
        "volumeRatio": number(settings.get("marketMaterialityVolumeRatio")) or 1.5,
        "description": "데이터 변경이 투자 판단에 충분히 중요한 경우에만 추론과 알림 의도로 승격합니다.",
    })
    novelty_policy_id = add_entity(graph, "novelty-policy", "relation-novelty", "관계 신규성 정책", {
        "tboxClass": "NoveltyPolicy",
        "minimumNovelty": number(settings.get("notificationNoveltyThreshold")) or 0.65,
    })
    cooldown_policy_id = add_entity(graph, "cooldown-policy", "insight-cooldown", "인사이트 발송 쿨다운", {
        "tboxClass": "CooldownPolicy",
        "fallbackMinutes": number(settings.get("notificationCooldownMinutes")) or 10,
        "legacyAlertCadence": safe_setting_value("alertCadenceMinutes", settings.get("alertCadenceMinutes") or ""),
    })
    suppression_policy_id = add_entity(graph, "suppression-policy", "duplicate-insight", "중복 인사이트 억제 정책", {
        "tboxClass": "SuppressionPolicy",
        "basis": "same-subject-same-insight-type-without-material-relation-change",
    })
    dispatch_id = add_entity(graph, "notification-dispatch", "investmentInsight", "investmentInsight 디스패치", {
        "tboxClass": "NotificationDispatch",
        "mode": "insight-driven-only",
        "legacyAlertTypesRole": "presentation-and-compatibility",
        "description": "투자 알림은 알림 타입별 폴링이 아니라 온톨로지 인사이트를 전달합니다.",
    })
    add_relation(graph, portfolio_node_id, collection_policy_id, "USES_COLLECTION_POLICY", properties={"source": "operational-ontology"})
    add_relation(graph, portfolio_node_id, market_session_id, "OBSERVES_MARKET_SESSION", properties={"source": "operational-ontology"})
    add_relation(graph, portfolio_node_id, reasoning_id, "HAS_REASONING_CYCLE", properties={"source": "operational-ontology"})
    add_relation(graph, portfolio_node_id, dispatch_id, "HAS_NOTIFICATION_DISPATCH", properties={"source": "operational-ontology"})
    add_relation(graph, dispatch_id, insight_policy_id, "USES_INSIGHT_POLICY", properties={"source": "operational-ontology"})
    add_relation(graph, dispatch_id, importance_gate_id, "USES_IMPORTANCE_GATE", properties={"source": "operational-ontology"})
    add_relation(graph, dispatch_id, cooldown_policy_id, "HAS_COOLDOWN_POLICY", properties={"source": "operational-ontology"})
    add_relation(graph, dispatch_id, novelty_policy_id, "HAS_NOVELTY_POLICY", properties={"source": "operational-ontology"})
    add_relation(graph, dispatch_id, suppression_policy_id, "SUPPRESSED_BY_POLICY", properties={"source": "operational-ontology"})
    add_relation(graph, reasoning_id, strategy_analysis_id, "SCHEDULES_ANALYSIS", properties={"source": "operational-ontology"})
    add_relation(graph, reasoning_id, importance_gate_id, "USES_IMPORTANCE_GATE", properties={"source": "operational-ontology"})
    for key, label in INSIGHT_TYPES:
        add_entity(graph, "insight-type", key, label, {"tboxClass": "InsightType", "key": key})
    for pipeline in OPERATIONAL_PIPELINES:
        key = str(pipeline["key"])
        fallback_key = str(pipeline.get("fallbackSettingKey") or "")
        target_minutes = number(pipeline.get("defaultMinutes"))
        minutes = configured_minutes(settings, str(pipeline["scheduleKey"]), target_minutes, fallback_key)
        pipeline_id = add_entity(graph, "data-pipeline", key, str(pipeline["label"]), {
            "tboxClass": "DataPipeline",
            "tboxClasses": list(pipeline.get("tboxClasses") or ["DataPipeline"]),
            "key": key,
            "dataKinds": list(pipeline.get("dataKinds") or []),
            "targetMinutes": target_minutes,
            "configuredMinutes": minutes,
            "description": str(pipeline.get("description") or ""),
        })
        source_id = add_entity(graph, "data-source", str(pipeline["sourceKey"]), str(pipeline["sourceLabel"]), {
            "tboxClass": "DataSource",
            "dataKinds": list(pipeline.get("dataKinds") or []),
        })
        schedule_id = add_entity(graph, "collection-schedule", key + ":" + str(int(minutes)), str(pipeline["label"]) + " " + str(int(minutes)) + "분", {
            "tboxClass": "CollectionSchedule",
            "pipeline": key,
            "targetMinutes": target_minutes,
            "configuredMinutes": minutes,
            "settingKey": str(pipeline.get("scheduleKey") or ""),
            "fallbackSettingKey": fallback_key,
        })
        freshness_id = add_entity(graph, "data-freshness", key, str(pipeline["label"]) + " freshness", {
            "tboxClass": "DataFreshness",
            "targetMinutes": target_minutes,
            "configuredMinutes": minutes,
            "freshnessRole": "ai-confidence-input",
        })
        add_relation(graph, portfolio_node_id, pipeline_id, "HAS_PIPELINE", properties={"source": "operational-ontology"})
        add_relation(graph, pipeline_id, source_id, "COLLECTS_DATA_FROM", properties={"source": "operational-ontology"})
        add_relation(graph, pipeline_id, schedule_id, "RUNS_ON_SCHEDULE", properties={"source": "operational-ontology"})
        add_relation(graph, pipeline_id, freshness_id, "HAS_DATA_FRESHNESS", properties={"source": "operational-ontology"})
        add_relation(graph, pipeline_id, collection_policy_id, "USES_COLLECTION_POLICY", properties={"source": "operational-ontology"})
        add_relation(graph, pipeline_id, portfolio_node_id, "UPDATES_GRAPH", properties={"source": "operational-ontology"})
        add_relation(graph, pipeline_id, reasoning_id, "TRIGGERS_REASONING", properties={"source": "operational-ontology"})

def add_strategy_world_concepts(
    graph: PortfolioOntology,
    portfolio_node_id: str,
    runtime_context: Dict[str, object],
) -> str:
    settings = runtime_settings(runtime_context)
    strategy_id = add_entity(graph, "strategy", "ontology-first-investment-strategy", "온톨로지 투자전략", {
        "tboxClass": "Strategy",
        "mode": "ontology-first",
        "description": "최종 점수는 TBox/ABox 관계 규칙, 근거 충돌, 데이터 품질, 운영 정책으로만 계산합니다.",
    })
    thesis_id = add_entity(graph, "investment-thesis", "portfolio-relation-thesis", "포트폴리오 관계 투자 가설", {
        "tboxClass": "InvestmentThesis",
        "scope": "portfolio",
        "thesis": "실세계 관측값과 포트폴리오 노출이 투자 의견과 점수의 근거이며 개별 공식 점수는 최종 판단에 쓰지 않습니다.",
    })
    entry_id = add_entity(graph, "entry-condition", "evidence-confirmed-entry", "근거 확인 진입 조건", {
        "tboxClass": "EntryCondition",
        "requires": ["price-observation", "trend-signal", "flow-signal", "data-quality"],
    })
    exit_id = add_entity(graph, "exit-condition", "risk-invalidates-thesis", "가설 약화 청산 조건", {
        "tboxClass": "ExitCondition",
        "requires": ["risk-amplification", "contradiction", "position-sizing-check"],
    })
    risk_rule_id = add_entity(graph, "risk-management-rule", "relation-risk-first", "관계 리스크 우선 규칙", {
        "tboxClass": "RiskManagementRule",
        "minimumDataQuality": 60,
        "riskPressureThreshold": 55,
    })
    sizing_id = add_entity(graph, "position-sizing-rule", "exposure-aware-sizing", "노출 기반 비중 규칙", {
        "tboxClass": "PositionSizingRule",
        "uses": ["positionWeight", "sectorWeight", "cashRatio"],
    })
    rebalance_id = add_entity(graph, "rebalancing-rule", "meaningful-exposure-change", "의미 있는 노출 변화 리밸런싱", {
        "tboxClass": "RebalancingRule",
        "noveltyThreshold": number(settings.get("notificationNoveltyThreshold")) or 0.65,
    })
    add_relation(graph, portfolio_node_id, strategy_id, "USES_STRATEGY", properties={"source": "strategy-ontology"})
    add_relation(graph, strategy_id, thesis_id, "BASED_ON_THESIS", properties={"source": "strategy-ontology"})
    add_relation(graph, strategy_id, entry_id, "HAS_ENTRY_CONDITION", properties={"source": "strategy-ontology"})
    add_relation(graph, strategy_id, exit_id, "HAS_EXIT_CONDITION", properties={"source": "strategy-ontology"})
    add_relation(graph, strategy_id, risk_rule_id, "HAS_RISK_MANAGEMENT_RULE", properties={"source": "strategy-ontology"})
    add_relation(graph, strategy_id, sizing_id, "HAS_POSITION_SIZING_RULE", properties={"source": "strategy-ontology"})
    add_relation(graph, strategy_id, rebalance_id, "HAS_REBALANCING_RULE", properties={"source": "strategy-ontology"})
    return strategy_id

def add_decision_item_concepts(graph: PortfolioOntology, runtime_context: Dict[str, object]) -> None:
    items = runtime_context.get("decisionItems") if isinstance(runtime_context, dict) else []
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        stock_id = entity_id("stock", symbol)
        signal_id = add_entity(graph, "strategy-signal", symbol + ":" + str(item.get("decision") or "decision"), str(item.get("decision") or "전략 판단"), {
            "tboxClass": "StrategySignal",
            "tboxClasses": ["Signal", "StrategySignal"],
            "source": str(item.get("source") or ""),
            "tone": str(item.get("tone") or ""),
            "priority": number(item.get("priority")),
            "exitPressure": number(item.get("exitPressure")),
            "reasons": list(item.get("reasons") or [])[:5],
            "triggers": list(item.get("triggers") or [])[:8],
        })
        properties = {"source": "decision-item", "aiInfluenceLabel": str(item.get("decision") or "전략 판단")}
        if number(item.get("exitPressure")) >= 55:
            properties.update({"polarity": "risk", "opinionImpact": min(16.0, (number(item.get("exitPressure")) - 45) * 0.3)})
        add_relation(graph, stock_id, signal_id, "DERIVES", weight=round(number(item.get("exitPressure")) / 100, 4), properties=properties)
        add_relation(graph, signal_id, stock_id, "USED_AS_EVIDENCE", weight=0.55, properties={"source": "decision-item"})
