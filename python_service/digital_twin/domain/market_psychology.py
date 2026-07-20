from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from .data_freshness import age_minutes, parse_datetime
from .investment_research import research_evidence_from_external_signals
from .investor_flow_psychology import investor_flow_psychology
from .market_data import number
from .ontology_decision_state import conflict_state_from_roles
from .portfolio import Position


PSYCHOLOGY_SHADOW_VERSION = "market-psychology-shadow-v2"
REVIEW_RANK = {"normal": 0, "observe": 1, "check": 2, "act": 3, "immediate": 4, "blocked": 5}


@dataclass(frozen=True)
class PsychologyPolicy:
    enabled: bool = True
    mode: str = "shadow"
    minimum_component_count: int = 2
    news_max_age_minutes: int = 1440

    def to_dict(self) -> Dict[str, object]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "minimumComponentCount": self.minimum_component_count,
            "newsMaxAgeMinutes": self.news_max_age_minutes,
            "decisionMode": "categorical-evidence-state",
        }


@dataclass(frozen=True)
class PsychologyComponent:
    key: str
    label: str
    state: str = "insufficient"
    state_label: str = "자료 부족"
    evidence_role: str = "blocking"
    review_level: str = "blocked"
    data_state: str = "unavailable"
    available: bool = False
    freshness_status: str = "unknown"
    source: str = ""
    source_as_of: str = ""
    reason: str = ""
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "key": payload["key"],
            "label": payload["label"],
            "state": payload["state"],
            "stateLabel": payload["state_label"],
            "evidenceRole": payload["evidence_role"],
            "reviewLevel": payload["review_level"],
            "dataState": payload["data_state"],
            "available": bool(payload["available"]),
            "freshnessStatus": payload["freshness_status"],
            "source": payload["source"],
            "sourceAsOf": payload["source_as_of"],
            "reason": payload["reason"],
            "evidence": list(payload["evidence"] or []),
        }


@dataclass(frozen=True)
class MarketPsychologySnapshot:
    symbol: str
    label: str
    state: str
    state_label: str
    review_level: str
    data_state: str
    conflict_state: str
    available_component_count: int
    components: List[PsychologyComponent]
    observed_at: str
    source_as_of: str
    freshness_status: str
    summary: str
    contradiction: str = ""
    shadow_only: bool = True
    version: str = PSYCHOLOGY_SHADOW_VERSION

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "symbol": self.symbol,
            "label": self.label,
            "state": self.state,
            "stateLabel": self.state_label,
            "reviewLevel": self.review_level,
            "dataState": self.data_state,
            "conflictState": self.conflict_state,
            "availableComponentCount": self.available_component_count,
            "components": [item.to_dict() for item in self.components],
            "observedAt": self.observed_at,
            "sourceAsOf": self.source_as_of,
            "freshnessStatus": self.freshness_status,
            "summary": self.summary,
            "contradiction": self.contradiction,
            "shadowOnly": self.shadow_only,
        }


def bool_setting(settings: Dict[str, object], key: str, fallback: bool) -> bool:
    value = (settings or {}).get(key)
    if value in (None, ""):
        return fallback
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def int_setting(settings: Dict[str, object], key: str, fallback: int, minimum: int, maximum: int) -> int:
    try:
        value = int(float(str((settings or {}).get(key) if (settings or {}).get(key) not in (None, "") else fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(minimum, min(maximum, value))


def psychology_policy_from_settings(settings: Optional[Dict[str, object]] = None) -> PsychologyPolicy:
    settings = settings or {}
    return PsychologyPolicy(
        enabled=bool_setting(settings, "psychologyShadowEnabled", True),
        mode="shadow",
        minimum_component_count=int_setting(settings, "psychologyMinimumComponentCount", 2, 1, 5),
        news_max_age_minutes=int_setting(settings, "psychologyNewsMaxAgeMinutes", 1440, 10, 43200),
    )


def component_available(profile: Dict[str, object]) -> bool:
    return bool(profile) and str(profile.get("freshnessStatus") or "") == "fresh" and profile.get("judgementEvidenceUsable") is not False


def component_state(role: str) -> tuple:
    return {
        "risk": ("cautious", "경계 우세"),
        "support": ("optimistic", "낙관 우세"),
        "counter": ("counter", "반대 근거"),
        "context": ("neutral", "방향 중립"),
        "blocking": ("insufficient", "자료 부족"),
    }.get(role, ("neutral", "방향 중립"))


def behavior_component(position: Position, observation_profiles: Dict[str, Dict[str, object]]) -> PsychologyComponent:
    quote_profile = dict((observation_profiles or {}).get("quote") or {})
    flow_profile = dict((observation_profiles or {}).get("flow") or {})
    if not component_available(quote_profile):
        return PsychologyComponent(
            "behavior",
            "가격·거래 행동",
            freshness_status=str(quote_profile.get("freshnessStatus") or "unknown"),
            source=str(quote_profile.get("observationSource") or position.quote_source or ""),
            source_as_of=str(quote_profile.get("sourceAsOf") or position.source_as_of or ""),
            reason=str(quote_profile.get("freshnessGateReason") or "신선한 시세가 없습니다."),
        )
    price_change = number(position.change_rate)
    ma20_distance = number(position.ma20_distance)
    evidence = ["가격 변화 " + signed_number(price_change) + "%", "20일 평균 대비 " + signed_number(ma20_distance) + "%"]
    risk = price_change <= -1.0 or ma20_distance <= -2.0
    support = price_change >= 1.0 and ma20_distance >= 0
    if component_available(flow_profile):
        trade_strength = number(position.trade_strength)
        imbalance = number(position.bid_ask_imbalance)
        risk = risk or (trade_strength and trade_strength <= 95) or imbalance <= -10
        support = support or trade_strength >= 105 or imbalance >= 10
        if trade_strength:
            evidence.append("체결강도 " + str(round(trade_strength, 1)))
        if imbalance:
            evidence.append("호가 대기 차이 " + signed_number(imbalance) + "%")
    else:
        evidence.append("수급 세부값은 신선도 기준 미충족")
    role = "counter" if risk and support else "risk" if risk else "support" if support else "context"
    state, label = component_state(role)
    review = "act" if role == "risk" and (price_change <= -3 or ma20_distance <= -5) else "check" if role in {"risk", "support", "counter"} else "observe"
    return PsychologyComponent(
        "behavior",
        "가격·거래 행동",
        state=state,
        state_label=label,
        evidence_role=role,
        review_level=review,
        data_state="sufficient" if component_available(flow_profile) else "partial",
        available=True,
        freshness_status="fresh",
        source=str(quote_profile.get("observationSource") or position.quote_source or "position"),
        source_as_of=str(quote_profile.get("sourceAsOf") or position.source_as_of or ""),
        reason="가격과 거래 흐름의 방향을 조건별로 확인했습니다.",
        evidence=evidence[:4],
    )


def investor_flow_component(position: Position) -> PsychologyComponent:
    payload = investor_flow_psychology(position)
    if not payload.get("available"):
        return PsychologyComponent(
            "investorFlow",
            "투자자별 수급 심리",
            freshness_status="unavailable",
            source="KIS investor flow",
            source_as_of=investor_source_as_of(position),
            reason="외국인·기관·개인 수급이 없거나 신선도 기준을 통과하지 못했습니다.",
        )
    role = str(payload.get("evidenceRole") or "context")
    state, label = component_state(role)
    return PsychologyComponent(
        "investorFlow",
        "투자자별 수급 심리",
        state=state,
        state_label=label,
        evidence_role=role,
        review_level=str(payload.get("reviewLevel") or "observe"),
        data_state=str(payload.get("dataState") or "partial"),
        available=True,
        freshness_status="fresh",
        source="KIS investor flow",
        source_as_of=investor_source_as_of(position),
        reason=str(payload.get("sentimentLabel") or "투자자별 순매수 방향을 확인했습니다."),
        evidence=[
            "외국인 " + signed_number(payload.get("foreignNetVolume")) + "주",
            "기관 " + signed_number(payload.get("institutionNetVolume")) + "주",
            "개인 " + signed_number(payload.get("individualNetVolume")) + "주",
        ],
    )


def investor_source_as_of(position: Position) -> str:
    coverage = position.market_signal_coverage if isinstance(position.market_signal_coverage, dict) else {}
    investor = coverage.get("investor") if isinstance(coverage.get("investor"), dict) else {}
    return str(investor.get("sourceAsOf") or investor.get("fetchedAt") or position.source_as_of or "")


def positioning_component(symbol: str, external_signals: Dict[str, object]) -> PsychologyComponent:
    group = external_signals.get("yfinanceData") if isinstance((external_signals or {}).get("yfinanceData"), dict) else {}
    payload = group.get(symbol) if isinstance(group.get(symbol), dict) else {}
    chains = payload.get("optionChains") if isinstance(payload.get("optionChains"), list) else []
    summary = chains[0].get("summary") if chains and isinstance(chains[0], dict) and isinstance(chains[0].get("summary"), dict) else {}
    freshness = ((payload.get("moduleFreshness") or {}).get("optionChains") or {}) if isinstance(payload.get("moduleFreshness"), dict) else {}
    ratio = number(summary.get("putCallOpenInterestRatio"))
    if not summary or ratio <= 0 or str(freshness.get("status") or "unknown") != "fresh":
        return PsychologyComponent(
            "positioning",
            "옵션 포지셔닝",
            freshness_status=str(freshness.get("status") or "unavailable"),
            source="yfinance options",
            source_as_of=str(payload.get("collectedAt") or ""),
            reason="신선한 풋/콜 미결제약정 비율이 없습니다.",
        )
    role = "risk" if ratio >= 1.2 else "support" if ratio <= 0.8 else "context"
    state, label = component_state(role)
    return PsychologyComponent(
        "positioning",
        "옵션 포지셔닝",
        state=state,
        state_label=label,
        evidence_role=role,
        review_level="check" if role in {"risk", "support"} else "observe",
        data_state="sufficient",
        available=True,
        freshness_status="fresh",
        source="yfinance options",
        source_as_of=str(payload.get("collectedAt") or ""),
        reason="풋/콜 미결제약정 비율이 정한 구간에 있는지 확인했습니다.",
        evidence=["풋/콜 미결제약정 비율 " + str(round(ratio, 2))],
    )


def news_component(symbol: str, external_signals: Dict[str, object], now: object, max_age_minutes: int) -> PsychologyComponent:
    items = []
    roles = []
    for item in research_evidence_from_external_signals(symbol, external_signals or {}):
        if item.kind != "news":
            continue
        payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
        if str(payload.get("articleReadStatus") or "") != "body":
            continue
        if str(payload.get("relationScope") or "") not in {"direct", "direct-company", "direct-security", "issuer-direct"}:
            continue
        published_at = item.published_at or item.observed_at
        item_age = age_minutes(published_at, now=parse_datetime(now))
        if item_age is None or item_age > max_age_minutes:
            continue
        polarity = str(payload.get("stockImpactPolarity") or item.polarity or "context")
        role = "support" if polarity in {"support", "positive", "bullish"} else "risk" if polarity in {"risk", "negative", "bearish"} else "context"
        items.append(item)
        roles.append(role)
    if not items:
        return PsychologyComponent(
            "news",
            "검증된 뉴스 심리",
            freshness_status="unavailable",
            source="verified article bodies",
            reason="본문을 읽고 종목 직접성이 확인된 최신 기사가 없습니다.",
        )
    conflict = conflict_state_from_roles(roles)
    role = "counter" if conflict == "mixed" else "risk" if conflict == "risk-only" else "support" if conflict == "support-only" else "context"
    state, label = component_state(role)
    source_as_of = max((str(item.published_at or item.observed_at or "") for item in items), default="")
    return PsychologyComponent(
        "news",
        "검증된 뉴스 심리",
        state=state,
        state_label=label,
        evidence_role=role,
        review_level="check" if role in {"risk", "support", "counter"} else "observe",
        data_state="sufficient",
        available=True,
        freshness_status="fresh",
        source="verified article bodies",
        source_as_of=source_as_of,
        reason="본문·종목 직접성·발행시각을 통과한 기사의 방향을 비교했습니다.",
        evidence=[str(item.title or "")[:140] for item in items[:3]],
    )


def crowd_component() -> PsychologyComponent:
    return PsychologyComponent(
        "crowd",
        "커뮤니티 군중심리",
        freshness_status="unavailable",
        reason="신뢰도와 이용 약관을 검증한 커뮤니티 원천이 아직 연결되지 않았습니다.",
    )


def market_psychology_snapshot(
    position: Position,
    external_signals: Optional[Dict[str, object]] = None,
    observation_profiles: Optional[Dict[str, Dict[str, object]]] = None,
    settings: Optional[Dict[str, object]] = None,
    observed_at: str = "",
) -> MarketPsychologySnapshot:
    external_signals = external_signals or {}
    observation_profiles = observation_profiles or {}
    policy = psychology_policy_from_settings(settings)
    symbol = str(position.symbol or "").upper().strip()
    components = [
        behavior_component(position, observation_profiles),
        investor_flow_component(position),
        positioning_component(symbol, external_signals),
        news_component(symbol, external_signals, observed_at, policy.news_max_age_minutes),
        crowd_component(),
    ]
    available = [item for item in components if item.available]
    enough = policy.enabled and len(available) >= policy.minimum_component_count
    if not enough:
        state, state_label = "insufficient", "근거 부족"
        review_level, data_state, conflict_state = "blocked", "insufficient", "context-only"
    else:
        roles = [item.evidence_role for item in available]
        conflict_state = conflict_state_from_roles(roles)
        if conflict_state == "risk-only":
            state, state_label = "cautious", "경계 우세"
        elif conflict_state == "support-only":
            state, state_label = "optimistic", "낙관 우세"
        elif conflict_state == "mixed":
            state, state_label = "mixed", "우호·위험 혼재"
        else:
            state, state_label = "neutral", "방향 중립"
        review_level = max((item.review_level for item in available), key=lambda item: REVIEW_RANK.get(item, 0), default="observe")
        data_state = "partial" if any(item.data_state == "partial" for item in available) else "sufficient"
    contradiction = psychology_contradiction(position, state) if enough else ""
    source_timestamps = [item.source_as_of for item in available if item.source_as_of]
    source_as_of = min(source_timestamps) if source_timestamps else ""
    summary = psychology_summary(state_label, available, contradiction)
    return MarketPsychologySnapshot(
        symbol=symbol,
        label=position.name or symbol,
        state=state,
        state_label=state_label,
        review_level=review_level,
        data_state=data_state,
        conflict_state=conflict_state,
        available_component_count=len(available),
        components=components,
        observed_at=str(observed_at or ""),
        source_as_of=source_as_of,
        freshness_status="fresh" if enough else "insufficient",
        summary=summary,
        contradiction=contradiction,
    )


def psychology_contradiction(position: Position, state: str) -> str:
    price_change = number(position.change_rate)
    if price_change >= 1.5 and state == "cautious":
        return "가격은 오르지만 심리 근거는 경계 쪽이라 상승의 지속 여부를 확인해야 합니다."
    if price_change <= -1.5 and state == "optimistic":
        return "가격은 내리지만 심리 근거는 낙관 쪽이라 실제 매수 흡수인지 확인해야 합니다."
    return ""


def psychology_summary(state_label: str, available: List[PsychologyComponent], contradiction: str) -> str:
    if not available:
        return "심리 판단에 사용할 신선한 원천이 없습니다."
    drivers = [item.label + "은 " + item.state_label for item in available[:3]]
    text = state_label + ". 주요 근거: " + ", ".join(drivers) + "."
    return text + (" " + contradiction if contradiction else "")


def signed_number(value: object) -> str:
    parsed = round(number(value), 1)
    return ("+" if parsed > 0 else "") + str(parsed)
