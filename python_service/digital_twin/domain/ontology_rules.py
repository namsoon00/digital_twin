from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional

from .market_data import clamp, number
from .parsing import parse_assignments
from .portfolio import PortfolioSummary, Position, expects_kr_microstructure_signals


ONTOLOGY_RULE_ENGINE_VERSION = "ontology-relation-rules-v1"
AI_PROMPT_REGISTRY_VERSION = "ai-prompt-registry-v1"

BTC_SENSITIVE_SYMBOLS = {"MSTR", "STRC", "COIN", "MARA", "RIOT", "CLSK", "HUT", "BITF"}

DEFAULT_RELATION_THRESHOLDS = {
    "lossRateLow": -8.0,
    "lossRateBufferPct": 1.0,
    "lossGuardVolumeConfirmRatio": 0.8,
    "lossGuardMa60SupportPct": 0.0,
    "lossGuardWeakEvidencePenalty": 30.0,
    "profitRateHigh": 20.0,
    "sectorWeightHigh": 50.0,
    "positionWeightHigh": 30.0,
    "externalBitcoinChange24hPct": 3.0,
    "externalBitcoinChange7dPct": 4.0,
}


@dataclass
class RelationRuleDefinition:
    rule_id: str
    label: str
    version: str
    relation_type: str
    signal_type: str
    condition_summary: str
    prompt_hint: str
    required_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class OntologyRuleMatch:
    rule_id: str
    label: str
    version: str
    relation_type: str
    signal_type: str
    matched: bool
    strength_score: float
    strength_label: str
    confidence: float
    evidence: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    reference_only: bool = False
    prompt_hint: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["strengthScore"] = round(float(self.strength_score or 0), 1)
        payload["strengthLabel"] = self.strength_label
        return payload


@dataclass
class OntologyPromptTemplate:
    prompt_id: str
    label: str
    version: str
    purpose: str
    system_prompt: str
    user_prompt: str
    output_schema: Dict[str, object] = field(default_factory=dict)
    guardrails: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["promptId"] = payload.pop("prompt_id")
        payload["systemPrompt"] = payload.pop("system_prompt")
        payload["userPrompt"] = payload.pop("user_prompt")
        return payload


DEFAULT_RELATION_RULES = [
    RelationRuleDefinition(
        "holding.profit_take.trend_weakness.v1",
        "수익 보유 + 추세 약화 -> 익절 점검",
        "v1",
        "PROFIT_TAKE_REVIEW",
        "exit_timing",
        "손익률이 +10% 이상이고 20일선 아래이거나 60일선 대비 약해질 때",
        "익절을 서두르라는 결론보다 분할 매도, 추세 회복 조건, 유지 조건을 함께 비교합니다.",
        ["profitLossRate", "currentPrice", "ma20", "ma60"],
    ),
    RelationRuleDefinition(
        "holding.loss_guard.breakdown.v1",
        "손실 보유 + 기준선 이탈 -> 손실 관리",
        "v1",
        "LOSS_GUARD",
        "risk_control",
        "손익률이 손실 기준 이하이거나 20일선보다 5% 이상 낮고, 60일선·거래량·수급 확인 강도로 점수를 조정할 때",
        "손절 여부만 묻지 말고 손실 확대 요인, 60일선 유지, 거래량 동반 여부, 회복 조건, 분할 대응 기준을 분리합니다.",
        ["profitLossRate", "currentPrice", "ma20", "ma60", "volumeRatio", "sellableQuantity"],
    ),
    RelationRuleDefinition(
        "holding.concentration.rebalance.v1",
        "업종 집중 + 보유 비중 과대 -> 리밸런싱 점검",
        "v1",
        "CONCENTRATION_RISK",
        "portfolio_risk",
        "업종 비중 50% 이상 또는 단일 종목 비중 30% 이상일 때",
        "개별 종목 판단과 포트폴리오 리스크를 분리해서 설명합니다.",
        ["sectorRatio", "positionWeight"],
    ),
    RelationRuleDefinition(
        "holding.trend_flow.confirmation.v1",
        "추세와 수급 방향 일치 -> 판단 신뢰도 보강",
        "v1",
        "EVIDENCE_SUPPORT",
        "confirmation",
        "20일/60일 추세와 외국인·기관 순매수 방향이 같은 쪽으로 움직일 때",
        "같은 방향 증거와 반대 증거를 나눠 AI에게 검토시킵니다.",
        ["ma20Distance", "ma60Distance", "foreignNetVolume", "institutionNetVolume"],
    ),
    RelationRuleDefinition(
        "external.crypto.btc_sensitivity.v1",
        "비트코인 급변 + 민감 종목 -> 연동 점검",
        "v1",
        "EXTERNAL_SENSITIVITY",
        "cross_asset",
        "BTC가 24시간 또는 7일 기준을 넘고 MSTR/STRC 같은 민감 종목을 보유할 때",
        "BTC 가격 변화와 보유 종목 가격 변화의 시차, 과민반응 여부를 묻습니다.",
        ["btcChange24h", "btcChange7d"],
    ),
    RelationRuleDefinition(
        "disclosure.material_event.v1",
        "신규 공시 + 보유 종목 -> 이벤트 리스크 점검",
        "v1",
        "DISCLOSURE_EVENT",
        "event_risk",
        "OpenDART 신규 공시가 보유 종목에 연결될 때",
        "공시 제목만으로 결론 내리지 말고 공시 성격, 반복 여부, 가격 반응 확인점을 묻습니다.",
        ["dartDisclosure"],
    ),
    RelationRuleDefinition(
        "data.quality.guard.v1",
        "핵심 데이터 부족 -> 판단 보류",
        "v1",
        "DATA_QUALITY_GUARD",
        "data_quality",
        "현재가, 이동평균 또는 해당 시장에 적용 가능한 수급·체결 데이터가 빠졌을 때",
        "없는 데이터는 추정하지 말고 부족 데이터와 판단 영향만 설명합니다.",
        ["currentPrice", "ma20", "ma60", "tradeStrength", "investorFlow"],
    ),
]


DEFAULT_PROMPT_TEMPLATES = [
    OntologyPromptTemplate(
        "holdingTiming",
        "보유 타이밍 AI 분석",
        AI_PROMPT_REGISTRY_VERSION,
        "보유 종목의 매수, 보유, 분할 매도, 손실 관리 타이밍을 온톨로지 관계 규칙 기반으로 설명합니다.",
        "너는 투자 자문가가 아니라 포트폴리오 관제 분석가다. 제공된 데이터와 관계 규칙만 사용한다.",
        "대상 종목, 성립한 관계 규칙, 증거, 부족 데이터를 보고 왜 알림이 발생했는지 설명하고 다음 확인 질문 3개를 제시한다.",
        {
            "summary": "string",
            "whyTriggered": ["string"],
            "counterEvidence": ["string"],
            "missingDataImpact": ["string"],
            "nextChecks": ["string"],
        },
        [
            "제공되지 않은 값은 추정하지 않습니다.",
            "매수/매도 지시 대신 확인 기준과 시나리오를 제시합니다.",
            "공식 점수보다 관계 규칙, 증거, 부족 데이터를 우선합니다.",
        ],
    ),
    OntologyPromptTemplate(
        "monitorDecisionChange",
        "판단 변화 AI 분석",
        AI_PROMPT_REGISTRY_VERSION,
        "이전 판단과 현재 판단이 달라진 이유를 관계 규칙과 데이터 변화로 분해합니다.",
        "너는 실시간 모니터링 변화 원인을 설명하는 분석가다.",
        "이전 상태와 현재 상태의 차이를 비교해 판단 변화 원인, 노이즈 가능성, 재확인 조건을 설명한다.",
        {
            "changedBecause": ["string"],
            "noiseCheck": ["string"],
            "dataValidation": ["string"],
            "modelImprovement": ["string"],
        },
        [
            "반복 알림 여부와 임계값 근처 흔들림을 반드시 점검합니다.",
            "체결강도와 투자자별 수급은 국내장 등 적용 가능한 시장에서만 부족 데이터로 표시합니다.",
        ],
    ),
    OntologyPromptTemplate(
        "externalCryptoMove",
        "크립토 연동 AI 분석",
        AI_PROMPT_REGISTRY_VERSION,
        "BTC/ETH 급변이 보유 주식과 어떤 관계를 가질 수 있는지 분리해 설명합니다.",
        "너는 외부 시장 신호와 보유 종목의 연결 관계를 검토하는 분석가다.",
        "크립토 변화율, 거래액, 민감 종목 보유 여부를 근거로 확인할 연결 관계와 노이즈 가능성을 설명한다.",
        {
            "marketMove": "string",
            "linkedHoldings": ["string"],
            "riskQuestions": ["string"],
        },
        [
            "크립토 가격만으로 주식 매매 결론을 내리지 않습니다.",
            "민감 종목이 없으면 시장 참고 신호로만 표현합니다.",
        ],
    ),
    OntologyPromptTemplate(
        "modelReview",
        "모델 개선 AI 리뷰",
        AI_PROMPT_REGISTRY_VERSION,
        "알림 이후 비동기로 모델의 부족 데이터, 관계 규칙, 프롬프트 개선점을 검토합니다.",
        "너는 모델 개발 리뷰어다. 현재 규칙과 데이터 품질을 점검해 개선안을 제안한다.",
        "알림 원문, 관계 규칙, 부족 데이터, 최근 반복 여부를 보고 모델 개선 후보를 구조화한다.",
        {
            "dataValidation": ["string"],
            "ontologyRuleSuggestion": ["string"],
            "promptSuggestion": ["string"],
            "noiseReduction": ["string"],
        },
        [
            "새 규칙 제안은 어떤 관계 타입에 속하는지 명시합니다.",
            "발송 우선도와 투자 판단을 섞지 않습니다.",
        ],
    ),
]


def default_ontology_relation_rules_text() -> str:
    return "\n".join(
        " | ".join([
            item.rule_id,
            item.label,
            item.condition_summary,
            item.relation_type,
            item.signal_type,
            item.prompt_hint,
        ])
        for item in DEFAULT_RELATION_RULES
    )


def default_ai_prompt_templates_text() -> str:
    blocks = []
    for template in DEFAULT_PROMPT_TEMPLATES:
        blocks.append("\n".join([
            "[" + template.prompt_id + "]",
            "label=" + template.label,
            "version=" + template.version,
            "purpose=" + template.purpose,
            "system=" + template.system_prompt,
            "user=" + template.user_prompt,
            "guardrails=" + " / ".join(template.guardrails),
        ]))
    return "\n\n".join(blocks)


def default_ai_prompt_policy_text() -> str:
    return "\n".join([
        "providedDataOnly=1",
        "separateInvestmentJudgmentAndDelivery=1",
        "showMissingData=1",
        "askBeforeInventingNewData=1",
        "preferRelationRulesOverFormulaScores=1",
    ])


def parse_relation_rule_definitions_text(text: str) -> List[RelationRuleDefinition]:
    defaults = {item.rule_id: item for item in DEFAULT_RELATION_RULES}
    definitions: List[RelationRuleDefinition] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        rule_id = parts[0] if parts else ""
        if not rule_id:
            continue
        default = defaults.get(rule_id)
        label = parts[1] if len(parts) > 1 and parts[1] else (default.label if default else rule_id)
        condition = parts[2] if len(parts) > 2 and parts[2] else (default.condition_summary if default else "")
        relation_type = parts[3] if len(parts) > 3 and parts[3] else (default.relation_type if default else "CUSTOM_RELATION")
        signal_type = parts[4] if len(parts) > 4 and parts[4] else (default.signal_type if default else "custom")
        prompt_hint = " | ".join(parts[5:]).strip() if len(parts) > 5 else (default.prompt_hint if default else "")
        definitions.append(RelationRuleDefinition(
            rule_id=rule_id,
            label=label,
            version=default.version if default else "custom",
            relation_type=relation_type,
            signal_type=signal_type,
            condition_summary=condition,
            prompt_hint=prompt_hint,
            required_fields=list(default.required_fields if default else []),
        ))
    return definitions or list(DEFAULT_RELATION_RULES)


def relation_rule_definitions_from_settings(settings: Optional[Dict[str, object]] = None) -> List[RelationRuleDefinition]:
    settings = settings or {}
    text = str(settings.get("ontologyRelationRules") or "").strip()
    return parse_relation_rule_definitions_text(text) if text else list(DEFAULT_RELATION_RULES)


def parse_ai_prompt_templates_text(text: str) -> List[OntologyPromptTemplate]:
    defaults = {item.prompt_id: item for item in DEFAULT_PROMPT_TEMPLATES}
    templates: List[OntologyPromptTemplate] = []
    current: Dict[str, str] = {}

    def flush_current() -> None:
        prompt_id = str(current.get("prompt_id") or "").strip()
        if not prompt_id:
            return
        default = defaults.get(prompt_id, DEFAULT_PROMPT_TEMPLATES[0])
        guardrails_text = str(current.get("guardrails") or "").strip()
        guardrails = [
            item.strip()
            for item in guardrails_text.replace("\n", " / ").split(" / ")
            if item.strip()
        ] or list(default.guardrails)
        templates.append(OntologyPromptTemplate(
            prompt_id=prompt_id,
            label=str(current.get("label") or default.label or prompt_id).strip(),
            version=str(current.get("version") or default.version or AI_PROMPT_REGISTRY_VERSION).strip(),
            purpose=str(current.get("purpose") or default.purpose or "").strip(),
            system_prompt=str(current.get("system") or default.system_prompt or "").strip(),
            user_prompt=str(current.get("user") or default.user_prompt or "").strip(),
            output_schema=dict(default.output_schema or {}),
            guardrails=guardrails,
        ))

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            flush_current()
            current = {"prompt_id": line[1:-1].strip()}
            continue
        if "=" not in line or not current:
            continue
        key, value = line.split("=", 1)
        current[key.strip()] = value.strip()
    flush_current()
    return templates or list(DEFAULT_PROMPT_TEMPLATES)


def prompt_templates_from_settings(settings: Optional[Dict[str, object]] = None) -> List[OntologyPromptTemplate]:
    settings = settings or {}
    text = str(settings.get("aiPromptTemplates") or "").strip()
    return parse_ai_prompt_templates_text(text) if text else list(DEFAULT_PROMPT_TEMPLATES)


def prompt_template(prompt_id: str, settings: Optional[Dict[str, object]] = None) -> OntologyPromptTemplate:
    templates = prompt_templates_from_settings(settings)
    for item in templates:
        if item.prompt_id == prompt_id:
            return item
    return templates[0]


def strength_label(score: float) -> str:
    value = float(score or 0)
    if value >= 85:
        return "매우 강함"
    if value >= 70:
        return "강함"
    if value >= 55:
        return "주의"
    if value >= 35:
        return "관찰"
    return "낮음"


def relation_score_meaning(score: float) -> str:
    value = float(score or 0)
    if value >= 85:
        return "즉시 재확인이 필요한 매우 강한 관계 압력"
    if value >= 70:
        return "대응 기준을 확인할 만큼 강한 관계 압력"
    if value >= 55:
        return "관찰을 유지해야 하는 관계 압력"
    if value >= 35:
        return "추적은 필요하지만 단독 판단 근거는 약한 관계 압력"
    return "참고 수준의 약한 관계 압력"


def relation_score_direction_meaning(delta: float) -> str:
    value = float(delta or 0)
    if abs(value) < 0.05:
        return "이전과 같은 수준의 대응 필요 강도"
    if value > 0:
        return "대응 필요 강도가 커졌다는 뜻이며, 가격 상승 예측 점수가 아닙니다"
    return "대응 필요 강도가 완화됐다는 뜻이며, 매수 신호는 아닙니다"


def _sector_ratio(position: Position, portfolio: PortfolioSummary) -> float:
    for item in portfolio.sectors or []:
        if str(item.get("sector") or item.get("label") or "") == str(position.sector or ""):
            return number(item.get("ratio"))
    return 0.0


def _position_weight(position: Position, portfolio: PortfolioSummary) -> float:
    invested = number(portfolio.invested)
    if invested <= 0:
        return 0.0
    return (number(position.market_value) / invested) * 100.0


def _investor_flow(position: Position) -> Dict[str, float]:
    foreign_volume = number(position.foreign_net_volume) or number(position.foreign_buy_volume) - number(position.foreign_sell_volume)
    institution_volume = number(position.institution_net_volume) or number(position.institution_buy_volume) - number(position.institution_sell_volume)
    individual_volume = number(position.individual_net_volume) or number(position.individual_buy_volume) - number(position.individual_sell_volume)
    foreign = foreign_volume or number(position.foreign_net_amount)
    institution = institution_volume or number(position.institution_net_amount)
    individual = individual_volume or number(position.individual_net_amount)
    base = abs(foreign) + abs(institution) + abs(individual)
    smart_money = foreign + institution
    score = clamp((smart_money - individual * 0.35) / base * 100.0, -100.0, 100.0) if base else 0.0
    return {
        "foreignNetVolume": foreign_volume,
        "institutionNetVolume": institution_volume,
        "individualNetVolume": individual_volume,
        "foreignNetAmount": number(position.foreign_net_amount),
        "institutionNetAmount": number(position.institution_net_amount),
        "individualNetAmount": number(position.individual_net_amount),
        "smartMoneyNetVolume": smart_money,
        "investorFlowBase": base,
        "investorFlowScore": score,
    }


def _trend_facts(position: Position) -> Dict[str, float]:
    current = number(position.current_price)
    ma20 = number(position.ma20)
    ma60 = number(position.ma60)
    ma20_distance = number(position.ma20_distance) or (((current / ma20) - 1) * 100.0 if current and ma20 else 0.0)
    ma60_distance = number(position.ma60_distance) or (((current / ma60) - 1) * 100.0 if current and ma60 else 0.0)
    ma20_slope = number(position.ma20_slope)
    ma60_slope = number(position.ma60_slope)
    score = clamp(ma20_distance * 0.45 + ma60_distance * 0.25 + ma20_slope * 3.0 + ma60_slope * 2.0, -35.0, 35.0)
    return {
        "currentPrice": current,
        "ma20": ma20,
        "ma60": ma60,
        "ma20Distance": ma20_distance,
        "ma60Distance": ma60_distance,
        "ma20Slope": ma20_slope,
        "ma60Slope": ma60_slope,
        "trendScore": score,
    }


def _btc_market(external_signals: Dict[str, object]) -> Dict[str, object]:
    markets = external_signals.get("cryptoMarkets") if isinstance(external_signals, dict) else {}
    if not isinstance(markets, dict):
        return {}
    for coin_id, item in markets.items():
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or coin_id or "").upper()
        name = str(item.get("name") or "").lower()
        if symbol == "BTC" or str(coin_id or "").lower() == "bitcoin" or name == "bitcoin":
            return dict(item)
    return {}


def _missing(key: str, label: str, effect: str) -> Dict[str, str]:
    return {"key": key, "label": label, "effect": effect}


def position_signal_facts(
    position: Position,
    portfolio: PortfolioSummary,
    external_signals: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    external_signals = external_signals or {}
    trend = _trend_facts(position)
    flow = _investor_flow(position)
    buy_volume = number(position.buy_volume)
    sell_volume = number(position.sell_volume)
    total_execution_volume = buy_volume + sell_volume
    buy_share = (buy_volume / total_execution_volume) * 100.0 if total_execution_volume else 0.0
    sell_share = 100.0 - buy_share if total_execution_volume else 0.0
    orderbook_bid_volume = number(position.orderbook_bid_volume)
    orderbook_ask_volume = number(position.orderbook_ask_volume)
    bid_ask_imbalance = number(position.bid_ask_imbalance)
    execution_direction_proxy = bool(position.trade_strength or bid_ask_imbalance or orderbook_bid_volume or orderbook_ask_volume)
    btc = _btc_market(external_signals)
    disclosures = external_signals.get("dartDisclosures") if isinstance(external_signals, dict) else {}
    symbol = str(position.symbol or "").upper()
    disclosure = disclosures.get(symbol) if isinstance(disclosures, dict) else None
    facts: Dict[str, object] = {
        "symbol": symbol,
        "name": position.name,
        "market": position.market,
        "currency": position.currency,
        "sector": position.sector,
        "profitLossRate": number(position.profit_loss_rate),
        "profitLoss": number(position.profit_loss),
        "marketValue": number(position.market_value),
        "quantity": number(position.quantity),
        "sellableQuantity": number(position.sellable_quantity),
        "sectorRatio": _sector_ratio(position, portfolio),
        "positionWeight": _position_weight(position, portfolio),
        "tradeStrength": number(position.trade_strength),
        "volume": number(position.volume),
        "volumeRatio": number(position.volume_ratio),
        "tradingValue": number(position.trading_value),
        "buyVolume": buy_volume,
        "sellVolume": sell_volume,
        "buyShare": buy_share,
        "sellShare": sell_share,
        "orderbookBidVolume": orderbook_bid_volume,
        "orderbookAskVolume": orderbook_ask_volume,
        "bidAskImbalance": bid_ask_imbalance,
        "executionDirectionProxy": execution_direction_proxy,
        "btcChange24h": number(btc.get("change24h")) if btc else 0.0,
        "btcChange7d": number(btc.get("change7d")) if btc else 0.0,
        "btcPrice": number(btc.get("price")) if btc else 0.0,
        "btcVolume24h": number(btc.get("volume24h")) if btc else 0.0,
        "isBtcSensitive": symbol in BTC_SENSITIVE_SYMBOLS,
        "dartDisclosure": dict(disclosure or {}) if isinstance(disclosure, dict) else {},
        "expectsKrMicrostructureSignals": expects_kr_microstructure_signals(position.market, position.currency, symbol),
    }
    facts.update(trend)
    facts.update(flow)
    missing: List[Dict[str, str]] = []
    if not facts["currentPrice"]:
        missing.append(_missing("currentPrice", "현재가", "가격·이동평균 관계 판단 신뢰도가 낮아집니다."))
    if not facts["ma20"]:
        missing.append(_missing("ma20", "20일 이동평균", "단기 추세 이탈 여부를 확인할 수 없습니다."))
    if not facts["ma60"]:
        missing.append(_missing("ma60", "60일 이동평균", "중기 추세 위치를 확인할 수 없습니다."))
    expects_kr_signals = bool(facts.get("expectsKrMicrostructureSignals"))
    if expects_kr_signals and not facts["tradeStrength"]:
        missing.append(_missing("tradeStrength", "체결강도", "체결 압력 확인값이 없어 수급 방향 판단을 가격·거래량 중심으로 봅니다."))
    if expects_kr_signals and not total_execution_volume and not execution_direction_proxy:
        missing.append(_missing("executionVolume", "방향별 매수/매도 체결량", "매수·매도 방향별 체결 압력을 확인하지 못해 수급 방향 점수는 중립에 가깝게 처리합니다."))
    if expects_kr_signals and not facts["investorFlowBase"]:
        missing.append(_missing("investorFlow", "투자자별 수급", "외국인·기관·개인 순매수는 반영하지 못해 주체별 수급은 중립으로 처리합니다. 가격·거래량·체결강도 중심 판단입니다."))
    if facts["isBtcSensitive"] and not btc:
        missing.append(_missing("btcMarket", "비트코인 시장 데이터", "비트코인 민감 종목의 외부 연동 위험을 확인하지 못합니다."))
    data_quality = clamp(100.0 - len(missing) * 12.0, 35.0, 100.0)
    facts["missingData"] = missing
    facts["dataQualityScore"] = data_quality
    return facts


def _thresholds(settings: Optional[Dict[str, object]]) -> Dict[str, float]:
    settings = settings or {}
    return parse_assignments(
        str(settings.get("alertThresholds") or ""),
        DEFAULT_RELATION_THRESHOLDS,
    )


def _rule(rule_id: str, definitions: Optional[List[RelationRuleDefinition]] = None) -> RelationRuleDefinition:
    for item in definitions or DEFAULT_RELATION_RULES:
        if item.rule_id == rule_id:
            return item
    for item in DEFAULT_RELATION_RULES:
        if item.rule_id == rule_id:
            return item
    return DEFAULT_RELATION_RULES[-1]


def _match(
    rule_id: str,
    score: float,
    confidence: float,
    evidence: Iterable[str],
    missing: Iterable[str] = (),
    matched: bool = True,
    reference_only: bool = False,
    definitions: Optional[List[RelationRuleDefinition]] = None,
) -> OntologyRuleMatch:
    definition = _rule(rule_id, definitions)
    return OntologyRuleMatch(
        definition.rule_id,
        definition.label,
        definition.version,
        definition.relation_type,
        definition.signal_type,
        matched,
        clamp(score, 0.0, 100.0),
        strength_label(score),
        clamp(confidence, 0.0, 100.0),
        [str(item) for item in evidence if str(item or "").strip()],
        [str(item) for item in missing if str(item or "").strip()],
        reference_only,
        definition.prompt_hint,
    )


def decision_from_matches(facts: Dict[str, object], matches: List[OntologyRuleMatch]) -> Dict[str, object]:
    active = [item for item in matches if item.matched and not item.reference_only]
    if not active:
        return {
            "label": "관계 규칙 관찰",
            "tone": "hold",
            "score": 35.0,
            "basis": "ontologyRelationRules",
            "selectedRuleId": "",
        }
    priority = {
        "holding.loss_guard.breakdown.v1": 40,
        "holding.profit_take.trend_weakness.v1": 35,
        "disclosure.material_event.v1": 30,
        "external.crypto.btc_sensitivity.v1": 25,
        "holding.concentration.rebalance.v1": 20,
    }
    selected = max(active, key=lambda item: (priority.get(item.rule_id, 10), item.strength_score, item.confidence))
    pnl = float(facts.get("profitLossRate") or 0)
    loss_threshold = float(facts.get("lossThreshold") or DEFAULT_RELATION_THRESHOLDS["lossRateLow"])
    if selected.rule_id == "holding.loss_guard.breakdown.v1":
        if selected.strength_score >= 70 and pnl <= loss_threshold:
            label = "손절 기준 확인"
        elif selected.strength_score >= 55:
            label = "손실 관리 기준 확인"
        else:
            label = "손실 기준 근접 관찰"
        tone = "danger" if selected.strength_score >= 70 else "caution" if selected.strength_score >= 55 else "hold"
    elif selected.rule_id == "holding.profit_take.trend_weakness.v1":
        label = "분할 매도 기준 확인" if selected.strength_score >= 70 else "익절 조건 점검"
        tone = "danger" if selected.strength_score >= 80 else "caution"
    elif selected.rule_id == "holding.concentration.rebalance.v1":
        label = "리밸런싱 기준 확인"
        tone = "caution"
    elif selected.rule_id == "external.crypto.btc_sensitivity.v1":
        label = "비트코인 민감도 점검"
        tone = "caution"
    elif selected.rule_id == "disclosure.material_event.v1":
        label = "공시 이벤트 점검"
        tone = "caution"
    elif selected.rule_id == "holding.trend_flow.confirmation.v1":
        label = "수급·추세 위험 점검" if selected.strength_score >= 55 else "수급·추세 확인"
        tone = "caution" if selected.strength_score >= 55 else "watch"
    else:
        label = "보유 조건 재확인"
        tone = "watch"
    return {
        "label": label,
        "tone": tone,
        "score": round(float(selected.strength_score or 0), 1),
        "basis": "ontologyRelationRules",
        "selectedRuleId": selected.rule_id,
    }


def build_ai_prompt_context(
    prompt_id: str,
    facts: Dict[str, object],
    matches: List[OntologyRuleMatch],
    settings: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    settings = settings or {}
    template = prompt_template(prompt_id, settings)
    policy = str(settings.get("aiPromptPolicy") or default_ai_prompt_policy_text()).strip()
    return {
        "promptVersion": template.version,
        "promptRegistryVersion": AI_PROMPT_REGISTRY_VERSION,
        "promptId": template.prompt_id,
        "promptTemplate": template.to_dict(),
        "promptPolicy": policy,
        "inputContract": {
            "subject": {
                "symbol": facts.get("symbol"),
                "name": facts.get("name"),
                "market": facts.get("market"),
                "sector": facts.get("sector"),
            },
            "requiredBlocks": ["facts", "matchedRules", "missingData", "deliveryContext"],
            "forbidden": ["inventing_missing_market_data", "mixing_delivery_priority_with_investment_judgment"],
        },
        "matchedRules": [item.to_dict() for item in matches if item.matched],
        "missingData": list(facts.get("missingData") or []),
        "guardrails": list(template.guardrails),
    }


def evaluate_position_relation_rules(
    position: Position,
    portfolio: PortfolioSummary,
    external_signals: Optional[Dict[str, object]] = None,
    settings: Optional[Dict[str, object]] = None,
    legacy_model: Optional[Dict[str, object]] = None,
    prompt_id: str = "holdingTiming",
) -> Dict[str, object]:
    settings = settings or {}
    relation_definitions = relation_rule_definitions_from_settings(settings)
    thresholds = _thresholds(settings)
    facts = position_signal_facts(position, portfolio, external_signals)
    missing_labels = [str(item.get("label") or item.get("key") or "") for item in facts.get("missingData") or []]
    matches: List[OntologyRuleMatch] = []
    data_quality = float(facts.get("dataQualityScore") or 0)
    pnl = float(facts.get("profitLossRate") or 0)
    ma20_distance = float(facts.get("ma20Distance") or 0)
    ma60_distance = float(facts.get("ma60Distance") or 0)
    sector_ratio = float(facts.get("sectorRatio") or 0)
    position_weight = float(facts.get("positionWeight") or 0)
    trend_score = float(facts.get("trendScore") or 0)
    flow_score = float(facts.get("investorFlowScore") or 0)
    btc_change24h = float(facts.get("btcChange24h") or 0)
    btc_change7d = float(facts.get("btcChange7d") or 0)

    if pnl >= 10 and (ma20_distance <= -2 or ma60_distance <= -5 or trend_score < -3):
        score = 55 + min(25, max(0, pnl - 10) * 1.2) + min(20, abs(min(ma20_distance, ma60_distance, trend_score)))
        matches.append(_match(
            "holding.profit_take.trend_weakness.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                "20일선 괴리 " + ("%.1f" % ma20_distance) + "%",
                "60일선 괴리 " + ("%.1f" % ma60_distance) + "%",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    loss_threshold = float(thresholds.get("lossRateLow", -8.0) or -8.0)
    loss_buffer = abs(float(thresholds.get("lossRateBufferPct", 1.0) or 0.0))
    volume_confirm_ratio = float(thresholds.get("lossGuardVolumeConfirmRatio", 0.8))
    ma60_support_threshold = float(thresholds.get("lossGuardMa60SupportPct", 0.0) or 0.0)
    weak_evidence_penalty = float(thresholds.get("lossGuardWeakEvidencePenalty", 30.0) or 0.0)
    facts["lossThreshold"] = loss_threshold
    facts["lossRateBufferPct"] = loss_buffer
    if pnl < 0 and (pnl <= loss_threshold or ma20_distance <= -5):
        volume_ratio = float(facts.get("volumeRatio") or 0)
        loss_depth = max(0.0, loss_threshold - pnl) if pnl <= loss_threshold else 0.0
        near_loss_threshold = pnl <= loss_threshold and loss_depth <= loss_buffer
        ma60_holds = bool(facts.get("ma60")) and ma60_distance > ma60_support_threshold
        volume_confirms = volume_ratio >= volume_confirm_ratio
        sell_flow_confirms = float(facts.get("sellShare") or 0) >= 56
        flow_confirms = flow_score <= -15
        slope_confirms = float(facts.get("ma20Slope") or 0) <= -1 or float(facts.get("ma60Slope") or 0) <= -0.5
        ma60_breaks = bool(facts.get("ma60")) and ma60_distance <= ma60_support_threshold
        confirmation_count = sum(1 for value in [ma60_breaks, volume_confirms, sell_flow_confirms, flow_confirms, slope_confirms] if value)
        weak_near_threshold = (
            near_loss_threshold
            and ma20_distance <= -5
            and ma60_holds
            and not volume_confirms
            and not sell_flow_confirms
            and not flow_confirms
        )
        score = 58 + min(24, abs(min(pnl, loss_threshold)) * 1.5) + (10 if ma20_distance <= -5 else 0)
        if weak_near_threshold:
            score -= weak_evidence_penalty
        matches.append(_match(
            "holding.loss_guard.breakdown.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                "손실 기준 " + ("%.1f" % loss_threshold) + "%",
                "손실 완충 " + ("%.1f" % loss_buffer) + "%p",
                "20일선 괴리 " + ("%.1f" % ma20_distance) + "%",
                "60일선 괴리 " + ("%.1f" % ma60_distance) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "확인 신호 " + str(confirmation_count) + "/5",
                ("약한 확인 신호 감점 -" + ("%.1f" % weak_evidence_penalty) + "점") if weak_near_threshold else "",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if sector_ratio >= float(thresholds.get("sectorWeightHigh", 50.0) or 50.0) or position_weight >= float(thresholds.get("positionWeightHigh", 30.0) or 30.0):
        score = 50 + min(25, max(0, sector_ratio - 35) * 0.9) + min(25, max(0, position_weight - 20) * 1.1)
        matches.append(_match(
            "holding.concentration.rebalance.v1",
            score,
            data_quality,
            [
                "업종 비중 " + ("%.1f" % sector_ratio) + "%",
                "종목 비중 " + ("%.1f" % position_weight) + "%",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if trend_score and flow_score and (trend_score > 4 and flow_score > 10 or trend_score < -4 and flow_score < -10):
        direction = "우호" if trend_score > 0 else "위험"
        score = 48 + min(24, abs(trend_score) * 1.2) + min(20, abs(flow_score) * 0.3)
        matches.append(_match(
            "holding.trend_flow.confirmation.v1",
            score,
            data_quality,
            [
                "추세 점수 " + ("%.1f" % trend_score),
                "투자자 수급 점수 " + ("%.1f" % flow_score),
                "공통 방향 " + direction,
            ],
            missing_labels,
            reference_only=trend_score > 0 and flow_score > 0,
            definitions=relation_definitions,
        ))
    btc_threshold24h = float(thresholds.get("externalBitcoinChange24hPct", 3.0) or 3.0)
    btc_threshold7d = float(thresholds.get("externalBitcoinChange7dPct", 4.0) or 4.0)
    if facts.get("isBtcSensitive") and (abs(btc_change24h) >= btc_threshold24h or abs(btc_change7d) >= btc_threshold7d):
        score = 50 + min(25, abs(btc_change24h) / max(1, btc_threshold24h) * 10) + min(25, abs(btc_change7d) / max(1, btc_threshold7d) * 10)
        matches.append(_match(
            "external.crypto.btc_sensitivity.v1",
            score,
            data_quality,
            [
                "BTC 24h " + ("%.1f" % btc_change24h) + "%",
                "BTC 7d " + ("%.1f" % btc_change7d) + "%",
                "민감 종목 " + str(facts.get("symbol") or ""),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    disclosure = facts.get("dartDisclosure")
    if isinstance(disclosure, dict) and disclosure:
        matches.append(_match(
            "disclosure.material_event.v1",
            62,
            data_quality,
            [
                "공시 " + str(disclosure.get("reportName") or "-"),
                "접수일 " + str(disclosure.get("receiptDate") or "-"),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if missing_labels:
        matches.append(_match(
            "data.quality.guard.v1",
            max(35, 100 - data_quality),
            data_quality,
            ["부족 데이터 " + ", ".join(missing_labels[:5])],
            missing_labels,
            reference_only=True,
            definitions=relation_definitions,
        ))

    decision = decision_from_matches(facts, matches)
    prompt_context = build_ai_prompt_context(prompt_id, facts, matches, settings)
    active_matches = [item for item in matches if item.matched and not item.reference_only]
    max_strength = max([item.strength_score for item in active_matches], default=decision["score"])
    return {
        "engineVersion": ONTOLOGY_RULE_ENGINE_VERSION,
        "subject": {
            "symbol": facts.get("symbol"),
            "name": facts.get("name"),
            "market": facts.get("market"),
            "sector": facts.get("sector"),
        },
        "facts": facts,
        "matchedRules": [item.to_dict() for item in matches if item.matched],
        "activeRules": [item.to_dict() for item in active_matches],
        "referenceRules": [item.to_dict() for item in matches if item.reference_only],
        "missingData": list(facts.get("missingData") or []),
        "dominantSignals": [item.label for item in active_matches[:3]],
        "signalStrength": round(float(max_strength or 0), 1),
        "signalStrengthLabel": strength_label(max_strength),
        "confidence": round(data_quality, 1),
        "decision": decision,
        "promptContext": prompt_context,
        "legacyModel": dict(legacy_model or {}),
    }


def relation_rule_context_summary_lines(context: Dict[str, object]) -> List[str]:
    if not isinstance(context, dict) or not context:
        return []
    lines: List[str] = []
    strength = context.get("signalStrength")
    strength_label_value = str(context.get("signalStrengthLabel") or "").strip()
    if strength not in (None, ""):
        lines.append("관계 신호 " + strength_label_value + " (" + ("%.1f" % float(strength)) + "점)")
    active_rules = context.get("activeRules") or context.get("matchedRules") or []
    names = []
    for item in active_rules:
        if not isinstance(item, dict) or item.get("referenceOnly") or item.get("reference_only"):
            continue
        label = str(item.get("label") or item.get("rule_id") or item.get("ruleId") or "").strip()
        if label:
            names.append(label)
    if names:
        lines.append("성립 규칙 " + " · ".join(names[:3]))
    missing = context.get("missingData") or []
    missing_names = []
    for item in missing:
        if isinstance(item, dict):
            text = str(item.get("label") or item.get("key") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            missing_names.append(text)
    if missing_names:
        lines.append("부족 데이터 " + ", ".join(missing_names[:5]))
    return lines
