import json
from dataclasses import asdict, dataclass, field, replace
from typing import Dict, Iterable, List, Tuple


ONTOLOGY_THRESHOLD_POLICY_VERSION = "ontology-threshold-policy-v1"
ONTOLOGY_THRESHOLD_POLICY_SOURCE = "RuleBox threshold policy"


def snake_to_camel(value: str) -> str:
    parts = str(value or "").split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def parsed_mapping(value: object) -> Dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if value in (None, ""):
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def section_mapping(payload: Dict[str, object], *keys: str) -> Dict[str, object]:
    for key in keys:
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            data = dict(candidate)
            thresholds = data.get("thresholds")
            if isinstance(thresholds, dict):
                merged = dict(thresholds)
                merged.update({k: v for k, v in data.items() if k != "thresholds"})
                return merged
            return data
    return {}


def setting_value(payload: Dict[str, object], snake_key: str):
    camel_key = snake_to_camel(snake_key)
    if snake_key in payload:
        return payload.get(snake_key)
    return payload.get(camel_key)


def normalized_payload_from_context(context: Dict[str, object]) -> Dict[str, object]:
    context = context if isinstance(context, dict) else {}
    if isinstance(context.get("thresholdPolicy"), dict):
        return dict(context.get("thresholdPolicy") or {})
    if context.get("ontologyThresholdPolicy") not in (None, ""):
        return parsed_mapping(context.get("ontologyThresholdPolicy"))
    settings = context.get("settings") if isinstance(context.get("settings"), dict) else {}
    if isinstance(settings, dict) and settings.get("ontologyThresholdPolicy") not in (None, ""):
        return parsed_mapping(settings.get("ontologyThresholdPolicy"))
    return {}


def tuple_from_value(value: object, fallback: Tuple[object, ...]) -> Tuple[object, ...]:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    if value in (None, ""):
        return fallback
    return tuple(item.strip() for item in str(value).split(",") if item.strip()) or fallback


def policy_with_overrides(instance, payload: Dict[str, object]):
    if not payload:
        return instance
    values = asdict(instance)
    for key, fallback in list(values.items()):
        raw = setting_value(payload, key)
        if raw in (None, ""):
            continue
        if isinstance(fallback, bool):
            values[key] = str(raw).strip().lower() not in {"0", "false", "off", "no"}
        elif isinstance(fallback, int) and not isinstance(fallback, bool):
            values[key] = int(float(raw))
        elif isinstance(fallback, float):
            values[key] = float(raw)
        elif isinstance(fallback, tuple):
            values[key] = tuple_from_value(raw, fallback)
        else:
            values[key] = raw
    return replace(instance, **values)


def threshold_dict(instance) -> Dict[str, object]:
    excluded = {"policy_id", "label", "tbox_class", "tbox_classes", "source", "version"}
    return {snake_to_camel(key): value for key, value in asdict(instance).items() if key not in excluded}


@dataclass(frozen=True)
class WhyNowThresholdPolicy:
    policy_id: str = "threshold.why_now.v1"
    label: str = "WhyNow 변경 감지 정책"
    tbox_class: str = "InsightPolicy"
    tbox_classes: Tuple[str, ...] = ("InsightPolicy", "NoveltyPolicy")
    source: str = ONTOLOGY_THRESHOLD_POLICY_SOURCE
    version: str = ONTOLOGY_THRESHOLD_POLICY_VERSION
    profit_loss_delta_change_pct: float = 0.1
    price_change_driver_pct: float = 1.0
    novelty_driver_score: float = 55.0
    escalate_profit_loss_delta_pct: float = 0.5
    escalate_price_change_pct: float = 2.0
    escalate_pressure_score: float = 70.0
    escalate_stage_priority: float = 55.0
    max_change_drivers: int = 8
    max_changed_facts: int = 8
    max_rule_ids: int = 8


@dataclass(frozen=True)
class SignalConflictThresholdPolicy:
    policy_id: str = "threshold.signal_conflict.v1"
    label: str = "위험/지지 신호 충돌 정책"
    tbox_class: str = "InsightPolicy"
    tbox_classes: Tuple[str, ...] = ("InsightPolicy", "SignalConflict")
    source: str = ONTOLOGY_THRESHOLD_POLICY_SOURCE
    version: str = ONTOLOGY_THRESHOLD_POLICY_VERSION
    loss_risk_profit_loss_rate: float = -3.0
    weak_trade_strength: float = 95.0
    support_trade_strength: float = 105.0
    support_bid_ask_imbalance: float = 20.0
    minimum_risk_pressure: float = 25.0
    minimum_support_evidence: float = 18.0
    dominance_gap: float = 12.0


@dataclass(frozen=True)
class ScoreBreakdownThresholdPolicy:
    policy_id: str = "threshold.score_breakdown.v1"
    label: str = "관계 점수 해석 정책"
    tbox_class: str = "RulePriorityPolicy"
    tbox_classes: Tuple[str, ...] = ("RulePriorityPolicy", "InsightPolicy")
    source: str = ONTOLOGY_THRESHOLD_POLICY_SOURCE
    version: str = ONTOLOGY_THRESHOLD_POLICY_VERSION
    holding_loss_pressure_rate: float = -3.0
    price_change_pressure_pct: float = 1.0
    trend_dynamic_risk_baseline: float = 25.0
    default_rule_reliability: float = 55.0
    trace_floor_score: float = 55.0
    trace_actionability_score: float = 35.0
    trace_novelty_score: float = 25.0
    support_trade_strength: float = 105.0
    weak_trade_strength: float = 95.0
    loss_actionability_rate: float = -8.0
    profit_actionability_rate: float = 8.0
    watchlist_volume_ratio: float = 1.2
    recent_news_age_minutes: float = 360.0
    net_risk_bonus_threshold: float = 55.0
    data_confidence_penalty_threshold: float = 55.0
    minimum_final_strength: float = 55.0
    high_ontology_pressure_score: float = 55.0
    contradiction_materiality_score: float = 78.0
    max_drivers: int = 8


@dataclass(frozen=True)
class InferenceMaterializationThresholdPolicy:
    policy_id: str = "threshold.inference_materialization.v1"
    label: str = "InferenceBox 설명 materialization 정책"
    tbox_class: str = "RulePriorityPolicy"
    tbox_classes: Tuple[str, ...] = ("RulePriorityPolicy", "RuleDecisionPolicy")
    source: str = ONTOLOGY_THRESHOLD_POLICY_SOURCE
    version: str = ONTOLOGY_THRESHOLD_POLICY_VERSION
    escalate_stage_priority: float = 55.0
    escalate_risk_impact: float = 12.0
    escalate_support_impact: float = 12.0
    escalate_confidence: float = 0.86
    conflict_dominance_gap: float = 3.0


@dataclass(frozen=True)
class ActionSelectionThresholdPolicy:
    policy_id: str = "threshold.action_selection.v1"
    label: str = "투자 행동 후보 선택 정책"
    tbox_class: str = "ActionPolicy"
    tbox_classes: Tuple[str, ...] = ("ActionPolicy", "RuleDecisionPolicy")
    source: str = ONTOLOGY_THRESHOLD_POLICY_SOURCE
    version: str = ONTOLOGY_THRESHOLD_POLICY_VERSION
    watchlist_risk_margin: float = 8.0
    watchlist_entry_strong_relation_score: float = 70.0
    watchlist_entry_relation_score: float = 55.0
    watchlist_entry_support_margin: float = 8.0
    watchlist_entry_weak_support_margin: float = 16.0
    add_buy_relation_score: float = 70.0
    add_buy_support_margin: float = -4.0
    loss_control_sell_relation_score: float = 78.0
    loss_control_sell_risk_margin: float = 18.0
    urgent_trim_risk_margin: float = 28.0
    risk_hold_margin: float = 16.0
    unsupported_add_support_margin: float = 18.0
    unsupported_add_max_relation_score: float = 55.0


@dataclass(frozen=True)
class WatchlistDispatchThresholdPolicy:
    policy_id: str = "threshold.watchlist_dispatch.v1"
    label: str = "관심종목 온톨로지 신호 디스패치 정책"
    tbox_class: str = "NotificationPolicy"
    tbox_classes: Tuple[str, ...] = ("NotificationPolicy", "ImportanceGate")
    source: str = ONTOLOGY_THRESHOLD_POLICY_SOURCE
    version: str = ONTOLOGY_THRESHOLD_POLICY_VERSION
    minimum_relation_score: float = 55.0
    risk_alert_relation_score: float = 75.0


@dataclass(frozen=True)
class ProfitLossDeliveryThresholdPolicy:
    policy_id: str = "threshold.profit_loss_delivery.v1"
    label: str = "손익 구간 필수 발송 정책"
    tbox_class: str = "NotificationPolicy"
    tbox_classes: Tuple[str, ...] = ("NotificationPolicy", "ImportanceGate", "CooldownPolicy")
    source: str = ONTOLOGY_THRESHOLD_POLICY_SOURCE
    version: str = ONTOLOGY_THRESHOLD_POLICY_VERSION
    message_types: Tuple[str, ...] = ("investmentInsight", "holdingTiming")
    loss_rate_threshold: float = -15.0
    profit_rate_threshold: float = 20.0
    loss_bands: Tuple[float, ...] = (-15.0, -20.0, -30.0)
    profit_bands: Tuple[float, ...] = (20.0, 30.0, 50.0)


@dataclass(frozen=True)
class DataQualityThresholdPolicy:
    policy_id: str = "threshold.data_quality.v1"
    label: str = "데이터 품질 교차검증 정책"
    tbox_class: str = "DataQuality"
    tbox_classes: Tuple[str, ...] = ("DataQuality", "ObservationConfidence")
    source: str = ONTOLOGY_THRESHOLD_POLICY_SOURCE
    version: str = ONTOLOGY_THRESHOLD_POLICY_VERSION
    trading_value_mismatch_pct: float = 35.0
    volume_pace_strong_ratio: float = 1.5
    volume_pace_normal_ratio: float = 0.8


@dataclass(frozen=True)
class MarketProxyThresholdPolicy:
    policy_id: str = "threshold.market_proxy.v1"
    label: str = "시장 프록시/노출 판단 정책"
    tbox_class: str = "RuleDecisionPolicy"
    tbox_classes: Tuple[str, ...] = ("RuleDecisionPolicy", "InsightPolicy")
    source: str = ONTOLOGY_THRESHOLD_POLICY_SOURCE
    version: str = ONTOLOGY_THRESHOLD_POLICY_VERSION
    volume_confirmation_ratio: float = 1.2
    directional_margin_score: float = 1.0
    minimum_directional_score: float = 3.0
    currency_exposure_min_pct: float = 10.0
    sector_exposure_min_pct: float = 35.0
    sector_position_min_count: int = 2


@dataclass(frozen=True)
class NewsImpactThresholdPolicy:
    policy_id: str = "threshold.news_impact.v1"
    label: str = "뉴스 영향 분류 정책"
    tbox_class: str = "InsightPolicy"
    tbox_classes: Tuple[str, ...] = ("InsightPolicy", "NewsImpactConfirmation")
    source: str = ONTOLOGY_THRESHOLD_POLICY_SOURCE
    version: str = ONTOLOGY_THRESHOLD_POLICY_VERSION
    risk_keyword_weight: float = 18.0
    support_keyword_weight: float = 15.0
    concern_bonus: float = 22.0
    plunge_bonus: float = 18.0
    contrast_bonus: float = 15.0
    dominance_gap: float = 8.0
    social_source_reliability: float = 0.25
    low_source_reliability: float = 0.42
    high_source_reliability: float = 0.82
    medium_source_reliability: float = 0.68
    aggregator_source_reliability: float = 0.58
    default_source_reliability: float = 0.58
    excluded_confidence: float = 0.25
    confidence_floor: float = 0.25
    confidence_cap: float = 0.92
    risk_impact_base: float = 6.0
    risk_impact_per_hit: float = 4.0
    risk_impact_cap: float = 16.0
    support_impact_base: float = 5.0
    support_impact_per_hit: float = 3.5
    support_impact_cap: float = 14.0
    context_impact_score: float = 2.0
    materiality_watch_score: float = 65.0


@dataclass(frozen=True)
class StrategyFallbackThresholdPolicy:
    policy_id: str = "threshold.strategy_fallback.v1"
    label: str = "보조 전략 점수 정책"
    tbox_class: str = "RuleDecisionPolicy"
    tbox_classes: Tuple[str, ...] = ("RuleDecisionPolicy", "ActionPolicy")
    source: str = ONTOLOGY_THRESHOLD_POLICY_SOURCE
    version: str = ONTOLOGY_THRESHOLD_POLICY_VERSION
    profit_take_high_pnl: float = 20.0
    profit_take_mid_pnl: float = 10.0
    profit_take_low_pnl: float = 5.0
    loss_cut_deep_pnl: float = -15.0
    loss_cut_mid_pnl: float = -8.0
    loss_cut_low_pnl: float = -3.0
    loss_guard_ma20_break_pct: float = -5.0
    loss_guard_sell_flow_share: float = 56.0
    loss_guard_investor_flow_score: float = -15.0
    loss_guard_ma20_slope: float = -1.0
    loss_guard_ma60_slope: float = -0.5
    pressure_urgent_score: float = 72.0
    pressure_action_score: float = 55.0
    pressure_review_score: float = 38.0
    pressure_loss_label_pnl: float = -8.0
    strong_flow_share: float = 62.0
    moderate_flow_share: float = 56.0
    flow_volume_ratio: float = 1.2
    smart_money_strong_ratio: float = 0.35
    smart_money_moderate_ratio: float = 0.15
    weak_trade_strength: float = 85.0
    strong_trade_strength: float = 120.0
    ma20_deep_break_pct: float = -5.0
    ma20_weak_break_pct: float = -2.0
    ma20_overheat_pct: float = 8.0
    profit_overheat_pnl: float = 10.0
    ma60_break_pct: float = -4.0
    ma20_slope_recovery_pct: float = 0.5
    sector_concentration_high_pct: float = 50.0
    sector_concentration_review_pct: float = 35.0


@dataclass(frozen=True)
class TemporalPatternThresholdPolicy:
    policy_id: str = "threshold.temporal_pattern.v1"
    label: str = "가격 경로/추세 전이 정책"
    tbox_class: str = "RuleDecisionPolicy"
    tbox_classes: Tuple[str, ...] = ("RuleDecisionPolicy", "InsightPolicy")
    source: str = ONTOLOGY_THRESHOLD_POLICY_SOURCE
    version: str = ONTOLOGY_THRESHOLD_POLICY_VERSION
    sideways_total_delta_pct: float = 1.0
    sideways_last_delta_pct: float = 0.8
    sideways_ma20_distance_pct: float = 2.0
    falling_total_delta_pct: float = -2.0
    falling_ma20_distance_pct: float = -4.0
    falling_last_delta_pct: float = -1.2
    rising_total_delta_pct: float = 2.0
    rising_ma20_distance_pct: float = 3.0
    rising_last_delta_pct: float = 1.2
    recovery_curve_pct: float = 0.6
    recovery_ma20_slope_pct: float = 0.4
    weakening_curve_pct: float = -0.6
    weakening_ma20_slope_pct: float = -0.4
    phase_confidence_base: float = 0.45
    phase_confidence_per_point: float = 0.08
    phase_confidence_cap: float = 0.95
    recovery_confidence_multiplier: float = 0.9
    mixed_confidence_multiplier: float = 0.8
    rebound_last_delta_pct: float = 0.8
    rebound_ma20_delta_pct: float = 1.2
    rebound_volume_ratio: float = 1.2
    breakout_last_delta_pct: float = 1.0
    breakout_volume_ratio: float = 1.4
    breakdown_last_delta_pct: float = -1.0
    breakdown_ma20_delta_pct: float = -1.2
    distribution_last_delta_pct: float = -1.0
    distribution_ma20_delta_pct: float = -1.2
    falling_acceleration_last_delta_pct: float = -1.5
    falling_acceleration_ma20_delta_pct: float = -2.0
    path_risk_ma20_offset_pct: float = 4.0
    path_risk_ma60_offset_pct: float = 3.0
    path_risk_last_delta_offset_pct: float = 1.2
    path_risk_ma20_slope_offset_pct: float = 0.6
    path_risk_driver_score: float = 4.0
    path_risk_ma20_distance_pct: float = -5.0
    path_risk_ma60_distance_pct: float = -4.0
    path_support_last_delta_offset_pct: float = 0.8
    path_support_ma20_delta_offset_pct: float = 0.8
    path_support_ma20_distance_offset_pct: float = 2.0
    path_support_ma20_slope_offset_pct: float = 0.3
    path_support_driver_score: float = 4.0
    path_support_last_delta_pct: float = 1.2
    path_support_volume_ratio: float = 1.1
    confirmation_volume_ratio: float = 1.2


@dataclass(frozen=True)
class OntologyThresholdPolicy:
    policy_id: str = "ontology.threshold.policy.v1"
    version: str = ONTOLOGY_THRESHOLD_POLICY_VERSION
    source: str = ONTOLOGY_THRESHOLD_POLICY_SOURCE
    why_now: WhyNowThresholdPolicy = field(default_factory=WhyNowThresholdPolicy)
    signal_conflict: SignalConflictThresholdPolicy = field(default_factory=SignalConflictThresholdPolicy)
    score_breakdown: ScoreBreakdownThresholdPolicy = field(default_factory=ScoreBreakdownThresholdPolicy)
    inference_materialization: InferenceMaterializationThresholdPolicy = field(default_factory=InferenceMaterializationThresholdPolicy)
    action_selection: ActionSelectionThresholdPolicy = field(default_factory=ActionSelectionThresholdPolicy)
    watchlist_dispatch: WatchlistDispatchThresholdPolicy = field(default_factory=WatchlistDispatchThresholdPolicy)
    profit_loss_delivery: ProfitLossDeliveryThresholdPolicy = field(default_factory=ProfitLossDeliveryThresholdPolicy)
    data_quality: DataQualityThresholdPolicy = field(default_factory=DataQualityThresholdPolicy)
    market_proxy: MarketProxyThresholdPolicy = field(default_factory=MarketProxyThresholdPolicy)
    news_impact: NewsImpactThresholdPolicy = field(default_factory=NewsImpactThresholdPolicy)
    strategy_fallback: StrategyFallbackThresholdPolicy = field(default_factory=StrategyFallbackThresholdPolicy)
    temporal_pattern: TemporalPatternThresholdPolicy = field(default_factory=TemporalPatternThresholdPolicy)

    def to_dict(self) -> Dict[str, object]:
        return {
            "policyId": self.policy_id,
            "version": self.version,
            "source": self.source,
            "whyNow": threshold_dict(self.why_now),
            "signalConflict": threshold_dict(self.signal_conflict),
            "scoreBreakdown": threshold_dict(self.score_breakdown),
            "inferenceMaterialization": threshold_dict(self.inference_materialization),
            "actionSelection": threshold_dict(self.action_selection),
            "watchlistDispatch": threshold_dict(self.watchlist_dispatch),
            "profitLossDelivery": threshold_dict(self.profit_loss_delivery),
            "dataQuality": threshold_dict(self.data_quality),
            "marketProxy": threshold_dict(self.market_proxy),
            "newsImpact": threshold_dict(self.news_impact),
            "strategyFallback": threshold_dict(self.strategy_fallback),
            "temporalPattern": threshold_dict(self.temporal_pattern),
        }

    def rulebox_policy_payloads(self) -> List[Dict[str, object]]:
        sections = [
            self.why_now,
            self.signal_conflict,
            self.score_breakdown,
            self.inference_materialization,
            self.action_selection,
            self.watchlist_dispatch,
            self.profit_loss_delivery,
            self.data_quality,
            self.market_proxy,
            self.news_impact,
            self.strategy_fallback,
            self.temporal_pattern,
        ]
        return [
            {
                "policyId": section.policy_id,
                "label": section.label,
                "version": section.version,
                "source": section.source,
                "tboxClass": section.tbox_class,
                "tboxClasses": list(section.tbox_classes),
                "thresholds": threshold_dict(section),
                "thresholdCount": len(threshold_dict(section)),
            }
            for section in sections
        ]


DEFAULT_ONTOLOGY_THRESHOLD_POLICY = OntologyThresholdPolicy()


def default_ontology_threshold_policy() -> OntologyThresholdPolicy:
    return DEFAULT_ONTOLOGY_THRESHOLD_POLICY


def ontology_threshold_policy_from_context(context: Dict[str, object] = None) -> OntologyThresholdPolicy:
    payload = normalized_payload_from_context(context or {})
    if not payload:
        return DEFAULT_ONTOLOGY_THRESHOLD_POLICY
    return OntologyThresholdPolicy(
        policy_id=str(payload.get("policyId") or payload.get("policy_id") or DEFAULT_ONTOLOGY_THRESHOLD_POLICY.policy_id),
        version=str(payload.get("version") or DEFAULT_ONTOLOGY_THRESHOLD_POLICY.version),
        source=str(payload.get("source") or DEFAULT_ONTOLOGY_THRESHOLD_POLICY.source),
        why_now=policy_with_overrides(
            DEFAULT_ONTOLOGY_THRESHOLD_POLICY.why_now,
            section_mapping(payload, "whyNow", "why_now"),
        ),
        signal_conflict=policy_with_overrides(
            DEFAULT_ONTOLOGY_THRESHOLD_POLICY.signal_conflict,
            section_mapping(payload, "signalConflict", "signal_conflict"),
        ),
        score_breakdown=policy_with_overrides(
            DEFAULT_ONTOLOGY_THRESHOLD_POLICY.score_breakdown,
            section_mapping(payload, "scoreBreakdown", "score_breakdown"),
        ),
        inference_materialization=policy_with_overrides(
            DEFAULT_ONTOLOGY_THRESHOLD_POLICY.inference_materialization,
            section_mapping(payload, "inferenceMaterialization", "inference_materialization"),
        ),
        action_selection=policy_with_overrides(
            DEFAULT_ONTOLOGY_THRESHOLD_POLICY.action_selection,
            section_mapping(payload, "actionSelection", "action_selection"),
        ),
        watchlist_dispatch=policy_with_overrides(
            DEFAULT_ONTOLOGY_THRESHOLD_POLICY.watchlist_dispatch,
            section_mapping(payload, "watchlistDispatch", "watchlist_dispatch"),
        ),
        profit_loss_delivery=policy_with_overrides(
            DEFAULT_ONTOLOGY_THRESHOLD_POLICY.profit_loss_delivery,
            section_mapping(payload, "profitLossDelivery", "profit_loss_delivery"),
        ),
        data_quality=policy_with_overrides(
            DEFAULT_ONTOLOGY_THRESHOLD_POLICY.data_quality,
            section_mapping(payload, "dataQuality", "data_quality"),
        ),
        market_proxy=policy_with_overrides(
            DEFAULT_ONTOLOGY_THRESHOLD_POLICY.market_proxy,
            section_mapping(payload, "marketProxy", "market_proxy"),
        ),
        news_impact=policy_with_overrides(
            DEFAULT_ONTOLOGY_THRESHOLD_POLICY.news_impact,
            section_mapping(payload, "newsImpact", "news_impact"),
        ),
        strategy_fallback=policy_with_overrides(
            DEFAULT_ONTOLOGY_THRESHOLD_POLICY.strategy_fallback,
            section_mapping(payload, "strategyFallback", "strategy_fallback"),
        ),
        temporal_pattern=policy_with_overrides(
            DEFAULT_ONTOLOGY_THRESHOLD_POLICY.temporal_pattern,
            section_mapping(payload, "temporalPattern", "temporal_pattern"),
        ),
    )


def rulebox_threshold_policy_payloads(policy: OntologyThresholdPolicy = None) -> List[Dict[str, object]]:
    return (policy or DEFAULT_ONTOLOGY_THRESHOLD_POLICY).rulebox_policy_payloads()


def policy_ids(payloads: Iterable[Dict[str, object]]) -> List[str]:
    return [str(item.get("policyId") or "") for item in payloads or [] if str(item.get("policyId") or "").strip()]
