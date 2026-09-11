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
    escalate_profit_loss_delta_pct: float = 0.5
    escalate_price_change_pct: float = 2.0
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


@dataclass(frozen=True)
class WatchlistDispatchThresholdPolicy:
    policy_id: str = "threshold.watchlist_dispatch.v1"
    label: str = "관심종목 온톨로지 신호 디스패치 정책"
    tbox_class: str = "NotificationPolicy"
    tbox_classes: Tuple[str, ...] = ("NotificationPolicy", "ImportanceGate")
    source: str = ONTOLOGY_THRESHOLD_POLICY_SOURCE
    version: str = ONTOLOGY_THRESHOLD_POLICY_VERSION
    minimum_review_level: str = "observe"
    usable_data_states: Tuple[str, ...] = ("sufficient", "partial")
    risk_alert_review_levels: Tuple[str, ...] = ("act", "immediate")


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
    tbox_classes: Tuple[str, ...] = ("DataQuality", "ObservationDataState")
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
    minimum_relevance_state: str = "related"
    minimum_source_trust_state: str = "standard"
    minimum_materiality_state: str = "notable"
    material_event_types: Tuple[str, ...] = ("earnings", "guidance", "regulation", "capital_policy")


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
    path_risk_ma20_distance_pct: float = -5.0
    path_risk_ma60_distance_pct: float = -4.0
    path_support_last_delta_offset_pct: float = 0.8
    path_support_ma20_delta_offset_pct: float = 0.8
    path_support_ma20_distance_offset_pct: float = 2.0
    path_support_ma20_slope_offset_pct: float = 0.3
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
    watchlist_dispatch: WatchlistDispatchThresholdPolicy = field(default_factory=WatchlistDispatchThresholdPolicy)
    profit_loss_delivery: ProfitLossDeliveryThresholdPolicy = field(default_factory=ProfitLossDeliveryThresholdPolicy)
    data_quality: DataQualityThresholdPolicy = field(default_factory=DataQualityThresholdPolicy)
    market_proxy: MarketProxyThresholdPolicy = field(default_factory=MarketProxyThresholdPolicy)
    news_impact: NewsImpactThresholdPolicy = field(default_factory=NewsImpactThresholdPolicy)
    temporal_pattern: TemporalPatternThresholdPolicy = field(default_factory=TemporalPatternThresholdPolicy)

    def to_dict(self) -> Dict[str, object]:
        return {
            "policyId": self.policy_id,
            "version": self.version,
            "source": self.source,
            "whyNow": threshold_dict(self.why_now),
            "signalConflict": threshold_dict(self.signal_conflict),
            "watchlistDispatch": threshold_dict(self.watchlist_dispatch),
            "profitLossDelivery": threshold_dict(self.profit_loss_delivery),
            "dataQuality": threshold_dict(self.data_quality),
            "marketProxy": threshold_dict(self.market_proxy),
            "newsImpact": threshold_dict(self.news_impact),
            "temporalPattern": threshold_dict(self.temporal_pattern),
        }

    def rulebox_policy_payloads(self) -> List[Dict[str, object]]:
        sections = [
            self.why_now,
            self.signal_conflict,
            self.watchlist_dispatch,
            self.profit_loss_delivery,
            self.data_quality,
            self.market_proxy,
            self.news_impact,
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
        temporal_pattern=policy_with_overrides(
            DEFAULT_ONTOLOGY_THRESHOLD_POLICY.temporal_pattern,
            section_mapping(payload, "temporalPattern", "temporal_pattern"),
        ),
    )


def rulebox_threshold_policy_payloads(policy: OntologyThresholdPolicy = None) -> List[Dict[str, object]]:
    return (policy or DEFAULT_ONTOLOGY_THRESHOLD_POLICY).rulebox_policy_payloads()


def policy_ids(payloads: Iterable[Dict[str, object]]) -> List[str]:
    return [str(item.get("policyId") or "") for item in payloads or [] if str(item.get("policyId") or "").strip()]
