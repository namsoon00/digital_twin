from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from .market_data import clamp, number
from .investment_research import research_evidence_from_external_signals, research_evidence_from_facts
from .message_types import DEFAULT_RELATION_RULE_THRESHOLDS
from .parsing import parse_assignments
from .portfolio import PortfolioSummary, Position, expects_kr_microstructure_signals


ONTOLOGY_RULE_ENGINE_VERSION = "ontology-relation-rules-v1"
AI_PROMPT_REGISTRY_VERSION = "ai-prompt-registry-v1"

BTC_SENSITIVE_SYMBOLS = {"MSTR", "STRC", "COIN", "MARA", "RIOT", "CLSK", "HUT", "BITF"}

DEFAULT_RELATION_THRESHOLDS = {
    str(key): float(value)
    for key, value in DEFAULT_RELATION_RULE_THRESHOLDS.items()
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


@dataclass(frozen=True)
class ScoreBandDefinition:
    key: str
    label: str
    min_score: float
    action_level: str
    meaning: str
    next_stage_at: float = 0.0

    def contains(self, score: float) -> bool:
        return float(score or 0) >= self.min_score

    def to_dict(self) -> Dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "minScore": self.min_score,
            "actionLevel": self.action_level,
            "meaning": self.meaning,
            "nextStageAt": self.next_stage_at,
        }


@dataclass(frozen=True)
class DecisionStageDefinition:
    stage_key: str
    action_group: str
    action_level: str
    label: str
    tone: str
    min_score: float = 0.0
    next_stage_at: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return {
            "stageKey": self.stage_key,
            "actionGroup": self.action_group,
            "actionLevel": self.action_level,
            "label": self.label,
            "tone": self.tone,
            "minScore": self.min_score,
            "nextStageAt": self.next_stage_at,
        }


SCORE_BANDS = [
    ScoreBandDefinition("URGENT", "매우 강함", 85.0, "urgent", "즉시 재확인이 필요한 매우 강한 관계 신호", 0.0),
    ScoreBandDefinition("ACTION", "강함", 70.0, "action", "대응 기준을 확인할 만큼 강한 관계 신호", 85.0),
    ScoreBandDefinition("REVIEW", "주의", 55.0, "review", "관찰을 유지해야 하는 관계 신호", 70.0),
    ScoreBandDefinition("WATCH", "관찰", 35.0, "watch", "추적은 필요하지만 단독 판단 근거는 약한 관계 신호", 55.0),
    ScoreBandDefinition("LOW", "낮음", 0.0, "reference", "참고 수준의 약한 관계 신호", 35.0),
]


DECISION_STAGE_DEFINITIONS = {
    "RELATION_WATCH": DecisionStageDefinition("RELATION_WATCH", "holdWatch", "watch", "관계 규칙 관찰", "hold", 35.0, 55.0),
    "HOLD_KEEP": DecisionStageDefinition("HOLD_KEEP", "holdWatch", "watch", "보유 유지", "watch", 0.0, 35.0),
    "LOSS_WATCH": DecisionStageDefinition("LOSS_WATCH", "lossControl", "watch", "손실 방어 관망", "hold", 0.0, 55.0),
    "LOSS_REDUCE": DecisionStageDefinition("LOSS_REDUCE", "lossControl", "review", "손실 축소 권장", "caution", 55.0, 70.0),
    "LOSS_CUT": DecisionStageDefinition("LOSS_CUT", "lossControl", "action", "손절·분할축소 권장", "danger", 70.0, 85.0),
    "PROFIT_PARTIAL": DecisionStageDefinition("PROFIT_PARTIAL", "profitTake", "review", "일부 익절 권장", "caution", 55.0, 70.0),
    "PROFIT_SPLIT": DecisionStageDefinition("PROFIT_SPLIT", "profitTake", "action", "분할매도 권장", "caution", 70.0, 85.0),
    "REBALANCE_REVIEW": DecisionStageDefinition("REBALANCE_REVIEW", "rebalance", "review", "리밸런싱 점검", "caution", 55.0, 70.0),
    "REBALANCE_ACTION": DecisionStageDefinition("REBALANCE_ACTION", "rebalance", "action", "리밸런싱 권장", "caution", 70.0, 85.0),
    "BTC_REVIEW": DecisionStageDefinition("BTC_REVIEW", "cryptoSensitivity", "review", "비트코인 민감도 점검", "watch", 55.0, 70.0),
    "BTC_REDUCE": DecisionStageDefinition("BTC_REDUCE", "cryptoSensitivity", "action", "비트코인 민감도 축소 검토", "caution", 70.0, 85.0),
    "DISCLOSURE_REVIEW": DecisionStageDefinition("DISCLOSURE_REVIEW", "disclosure", "review", "공시 리스크 대응 검토", "caution", 55.0, 70.0),
    "NEWS_RISK": DecisionStageDefinition("NEWS_RISK", "eventRisk", "review", "뉴스 리스크 대응 검토", "caution", 55.0, 70.0),
    "NEWS_CONFIRMATION": DecisionStageDefinition("NEWS_CONFIRMATION", "eventConfirmation", "review", "뉴스·가격 동조 확인", "watch", 55.0, 70.0),
    "SECTOR_NEWS": DecisionStageDefinition("SECTOR_NEWS", "sectorContext", "watch", "섹터·피어 뉴스 영향 점검", "watch", 35.0, 55.0),
    "FLOW_WATCH": DecisionStageDefinition("FLOW_WATCH", "flowTrend", "watch", "수급·추세 관찰", "watch", 35.0, 55.0),
    "FLOW_DEFENSE": DecisionStageDefinition("FLOW_DEFENSE", "flowTrend", "review", "수급·추세 방어 권장", "caution", 55.0, 70.0),
    "SUPPORT_RETEST": DecisionStageDefinition("SUPPORT_RETEST", "trendReview", "review", "60일선 지지 재확인", "hold", 55.0, 70.0),
    "RECOVERY_CONFIRM": DecisionStageDefinition("RECOVERY_CONFIRM", "recovery", "review", "회복 시도 확인", "watch", 55.0, 70.0),
    "BREAKDOWN_ACCELERATION": DecisionStageDefinition("BREAKDOWN_ACCELERATION", "lossControl", "action", "하락 가속 대응 점검", "danger", 70.0, 85.0),
    "BREAKOUT_FAILURE": DecisionStageDefinition("BREAKOUT_FAILURE", "lossControl", "action", "돌파 실패 리스크 점검", "danger", 70.0, 85.0),
    "SUPPORT_RETEST_FAILED": DecisionStageDefinition("SUPPORT_RETEST_FAILED", "lossControl", "review", "지지선 실패 점검", "caution", 55.0, 70.0),
    "DISTRIBUTION_REVIEW": DecisionStageDefinition("DISTRIBUTION_REVIEW", "distributionRisk", "review", "분산매도 의심 점검", "caution", 55.0, 70.0),
    "LIQUIDITY_REVIEW": DecisionStageDefinition("LIQUIDITY_REVIEW", "executionRisk", "review", "유동성 실행 점검", "caution", 55.0, 70.0),
    "LIQUIDITY_ACTION": DecisionStageDefinition("LIQUIDITY_ACTION", "executionRisk", "action", "분할 실행 우선", "caution", 70.0, 85.0),
    "FACTOR_CROWDING": DecisionStageDefinition("FACTOR_CROWDING", "factorRisk", "review", "팩터 과밀 점검", "caution", 55.0, 70.0),
    "DATA_CONFLICT": DecisionStageDefinition("DATA_CONFLICT", "dataQuality", "review", "데이터 충돌 점검", "caution", 55.0, 70.0),
    "PROFIT_PROTECT": DecisionStageDefinition("PROFIT_PROTECT", "profitTake", "review", "수익 보호 점검", "caution", 55.0, 70.0),
    "MACRO_REGIME": DecisionStageDefinition("MACRO_REGIME", "macroRegime", "review", "거시 레짐 점검", "watch", 55.0, 70.0),
    "ENTRY_WATCH": DecisionStageDefinition("ENTRY_WATCH", "entry", "watch", "분할매수 관찰", "watch", 35.0, 55.0),
    "ENTRY_SPLIT_BUY": DecisionStageDefinition("ENTRY_SPLIT_BUY", "entry", "review", "분할매수 후보", "watch", 55.0, 70.0),
    "ENTRY_READY": DecisionStageDefinition("ENTRY_READY", "entry", "action", "소액 분할매수 검토", "caution", 70.0, 85.0),
    "ADD_BUY_BLOCKED": DecisionStageDefinition("ADD_BUY_BLOCKED", "entryRisk", "review", "추가매수 보류", "caution", 55.0, 70.0),
}


DECISION_LABEL_ALIASES = {
    "조건부 보유": "HOLD_KEEP",
    "손절 기준 확인": "LOSS_CUT",
    "손실 관리 기준 확인": "LOSS_REDUCE",
    "손실 기준 근접 관찰": "LOSS_REDUCE",
    "분할 매도 기준 확인": "PROFIT_SPLIT",
    "익절 점검": "PROFIT_PARTIAL",
    "리밸런싱 점검": "REBALANCE_REVIEW",
    "60일선 지지 재확인": "SUPPORT_RETEST",
    "회복 시도 확인": "RECOVERY_CONFIRM",
    "하락 가속 대응 점검": "BREAKDOWN_ACCELERATION",
}


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
        "같은 방향 근거와 반대 근거를 나눠 AI에게 검토시킵니다.",
        ["ma20Distance", "ma60Distance", "foreignNetVolume", "institutionNetVolume"],
    ),
    RelationRuleDefinition(
        "trend.support_retest.v1",
        "단기선 이탈 + 60일선 지지 -> 지지선 재확인",
        "v1",
        "SUPPORT_RETEST",
        "trend_context",
        "현재가가 20일선 아래로 크게 밀렸지만 60일선 부근 또는 위에서 버틸 때",
        "손절 결론을 바로 내리지 말고 60일선 지지 유지, 반등 시도, 거래량 동반 여부를 함께 검토합니다.",
        ["currentPrice", "ma20", "ma60", "ma20Distance", "ma60Distance", "priceChangeRate"],
    ),
    RelationRuleDefinition(
        "trend.recovery_attempt.v1",
        "손실/눌림 + 반등 커브 -> 회복 확인",
        "v1",
        "RECOVERY_ATTEMPT",
        "trend_recovery",
        "20일선 아래 또는 손실 구간에서 가격 모멘텀·단기 기울기·커브가 회복 쪽으로 돌아설 때",
        "회복 시도와 단순 낙폭 과대를 분리하고, 추가매수보다 확인 조건을 먼저 제시합니다.",
        ["profitLossRate", "priceChangeRate", "ma20Slope", "ma60Slope", "trendCurve"],
    ),
    RelationRuleDefinition(
        "trend.breakdown_acceleration.v1",
        "추세 훼손 + 하락 가속 -> 리스크 강화",
        "v1",
        "BREAKDOWN_ACCELERATION",
        "trend_acceleration",
        "20일선 이탈 상태에서 가격 하락, 단기 기울기 하락, 하락 커브 확대 또는 중기선 동반 약화가 겹칠 때",
        "현재 위치만 보지 말고 하락 속도와 커브 확대를 AI가 별도 위험 요인으로 검토합니다.",
        ["currentPrice", "ma20", "ma60", "priceChangeRate", "ma20Slope", "ma60Slope", "trendCurve"],
    ),
    RelationRuleDefinition(
        "breakout.failure.v1",
        "회복/돌파 후 재이탈 -> 실패 리스크",
        "v1",
        "FAILED_BREAKOUT",
        "trend_failure",
        "이전에는 20일선 위 또는 회복 상태였으나 현재 20일선을 재이탈하고 하락/거래량/매도 압력이 붙을 때",
        "단순 하락보다 회복 실패를 더 강한 경고로 보고, 재진입/추가매수보다 무효화 조건을 먼저 검토합니다.",
        ["previousMa20Distance", "ma20Distance", "priceChangeRate", "volumeRatio", "sellShare"],
    ),
    RelationRuleDefinition(
        "support.retest.failed.v1",
        "60일선 재시험 실패 -> 지지 실패",
        "v1",
        "SUPPORT_FAILURE",
        "trend_failure",
        "60일선 부근 재시험 후 현재 60일선 아래로 밀리거나 하락 커브가 확대될 때",
        "60일선 방어가 깨진 상태인지, 일시 이탈인지, 다음 확인 가격대를 분리해서 판단합니다.",
        ["previousMa60Distance", "ma60Distance", "trendCurve", "volumeRatio"],
    ),
    RelationRuleDefinition(
        "support.retest.confirmed.v1",
        "지지 재확인 + 매수 압력 -> 방어 확인",
        "v1",
        "SUPPORT_CONFIRMED",
        "confirmation",
        "60일선 부근에서 버티고 가격/체결/호가/수급 중 2개 이상이 회복 쪽일 때",
        "방어 확인은 매수 지시가 아니라 보유/관심 유지 근거이며, 다음 재확인 조건을 함께 제시합니다.",
        ["ma60Distance", "priceChangeRate", "tradeStrength", "bidAskImbalance", "investorFlow"],
    ),
    RelationRuleDefinition(
        "entry.pullback.supported.v1",
        "눌림목 + 지지 수급 -> 분할매수 후보",
        "v1",
        "ENTRY_OPPORTUNITY",
        "entry_timing",
        "20일선보다 낮아졌지만 60일선 지지가 유지되고 거래량·체결·투자자 수급이 회복 쪽일 때",
        "싸졌다는 이유만으로 매수하지 말고 지지선, 수급 회복, 보유 비중, 뉴스·공시 리스크를 함께 비교합니다.",
        ["currentPrice", "ma20", "ma60", "volumeRatio", "tradeStrength", "investorFlow"],
    ),
    RelationRuleDefinition(
        "entry.add_buy.blocked.v1",
        "보유 종목 + 추세 훼손 -> 추가매수 보류",
        "v1",
        "ENTRY_RISK",
        "entry_risk",
        "보유 중인 종목이 20일선·60일선 아래에 있거나 공시/뉴스 리스크와 손실 상태가 겹칠 때",
        "추가매수보다 손실 기준, 회복 조건, 비중 한도를 먼저 확인하도록 AI에게 요청합니다.",
        ["profitLossRate", "currentPrice", "ma20", "ma60", "dartDisclosure", "newsHeadlines"],
    ),
    RelationRuleDefinition(
        "averaging_down.block.v1",
        "손실 + 20일선 하회 + 확인 부족 -> 물타기 차단",
        "v1",
        "AVERAGING_DOWN_BLOCK",
        "entry_risk",
        "손실 상태에서 단기 추세 아래이고 수급/거래량/체결 확인이 부족할 때",
        "평균단가 낮추기보다 보유 이유와 손절 기준을 먼저 확인하도록 AI에게 요청합니다.",
        ["profitLossRate", "ma20Distance", "volumeRatio", "tradeStrength", "investorFlow"],
    ),
    RelationRuleDefinition(
        "distribution.detected.v1",
        "가격 버팀 + 매도 수급/거래량 -> 분산매도 의심",
        "v1",
        "DISTRIBUTION_RISK",
        "flow_risk",
        "가격은 크게 무너지지 않았지만 거래량 증가, 외국인·기관 순매도, 매도 호가/체결 우위가 겹칠 때",
        "가격만 보고 안심하지 말고 공급 우위와 다음 이탈 조건을 분리합니다.",
        ["priceChangeRate", "volumeRatio", "investorFlow", "sellShare", "bidAskImbalance"],
    ),
    RelationRuleDefinition(
        "profit.protection.volatility.v1",
        "수익 + 변동성 확대 + 둔화 -> 수익 보호",
        "v1",
        "PROFIT_PROTECTION",
        "exit_timing",
        "수익 구간에서 거래량/변동성이 커지고 단기 기울기 또는 커브가 둔화될 때",
        "전량 매도보다 분할 익절, 추세 회복 유지 조건, 재진입 조건을 함께 제시합니다.",
        ["profitLossRate", "volumeRatio", "ma20Slope", "trendCurve"],
    ),
    RelationRuleDefinition(
        "liquidity.exit_capacity.v1",
        "포지션 규모 + 거래대금 제약 -> 분할 실행",
        "v1",
        "LIQUIDITY_CONSTRAINT",
        "execution_risk",
        "포지션 평가액이 거래대금 대비 크거나 매도 가능 수량/호가가 실행을 제한할 때",
        "투자 판단과 실제 실행 가능성을 분리하고, 분할 실행·시장가 회피 기준을 제시합니다.",
        ["marketValue", "tradingValue", "sellableQuantity", "bidAskImbalance"],
    ),
    RelationRuleDefinition(
        "factor.crowding.v1",
        "섹터/팩터 집중 + 동방향 리스크 -> 포트폴리오 과밀",
        "v1",
        "FACTOR_CROWDING",
        "portfolio_risk",
        "섹터 비중이 높고 해당 종목도 같은 팩터에 노출되어 개별 리스크가 포트폴리오 리스크로 전파될 때",
        "개별 종목 신호와 포트폴리오 팩터 리스크를 분리해 설명합니다.",
        ["sectorRatio", "positionWeight", "market", "currency"],
    ),
    RelationRuleDefinition(
        "data.conflict.v1",
        "데이터 출처 충돌/신선도 저하 -> 확신도 상한",
        "v1",
        "DATA_CONFLICT",
        "data_quality",
        "핵심 가격/수급 출처가 비었거나 외부 신호 품질이 낮아 관계 결론의 확신도를 제한해야 할 때",
        "없는 데이터를 추정하지 말고 판단 강도 상한과 복구해야 할 데이터만 말합니다.",
        ["dataQualityScore", "externalSignalQuality", "missingData"],
    ),
    RelationRuleDefinition(
        "macro.regime.shift.v1",
        "금리/크립토/환율 레짐 변화 -> 팩터 민감도 점검",
        "v1",
        "MACRO_REGIME_SHIFT",
        "macro_risk",
        "금리, 스프레드, 크립토 유동성 또는 환율 변화가 성장주/반도체/디지털자산 민감 종목에 영향을 줄 때",
        "개별 가격 신호와 레짐 신호를 섞지 말고 민감도와 후속 확인 지표를 분리합니다.",
        ["macro", "btcChange24h", "btcChange7d", "currency"],
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
        "news.direct_risk.price_confirmed.v1",
        "직접 부정 뉴스 + 가격·수급 확인 -> 이벤트 리스크 점검",
        "v1",
        "NEWS_RISK_EVENT",
        "event_risk",
        "대상 종목 직접 뉴스가 부정 성격이고 가격 하락, 거래량 증가, 추세 훼손 중 하나 이상으로 확인될 때",
        "뉴스 제목만으로 결론 내리지 말고 직접 관련성, 출처 신뢰도, 가격·수급 동조 여부, 반대 신호를 함께 검토합니다.",
        ["researchEvidence", "directRiskNewsCount", "priceChangeRate", "volumeRatio", "ma20Distance"],
    ),
    RelationRuleDefinition(
        "news.direct_support.price_confirmed.v1",
        "직접 우호 뉴스 + 가격·수급 확인 -> 우호 이벤트 확인",
        "v1",
        "NEWS_SUPPORT_EVENT",
        "event_confirmation",
        "대상 종목 직접 뉴스가 우호 성격이고 가격 상승, 거래량 증가, 체결강도 개선 중 하나 이상으로 확인될 때",
        "우호 뉴스도 매수 지시가 아니라 가격·수급 확인과 무효화 조건을 분리해 검토합니다.",
        ["researchEvidence", "directSupportNewsCount", "priceChangeRate", "volumeRatio", "tradeStrength"],
    ),
    RelationRuleDefinition(
        "news.sector_peer_context.v1",
        "섹터·피어 뉴스 + 대상 종목 노출 -> 맥락 영향 점검",
        "v1",
        "SECTOR_NEWS_CONTEXT",
        "sector_context",
        "직접 뉴스는 아니지만 피어, 섹터, 시장 뉴스가 대상 종목의 팩터·섹터 노출과 연결될 때",
        "직접 기사와 섹터 맥락을 구분하고, 대상 가격·수급이 동조하는 경우에만 판단 강도를 높입니다.",
        ["researchEvidence", "sectorNewsCount", "peerNewsCount", "marketNewsCount"],
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


PROMPT_OUTPUT_SCHEMA = {
    "summary": "string",
    "opinion": "string",
    "nextChecks": ["string"],
    "missingDataImpact": ["string"],
}

COMMON_PROMPT_GUARDRAILS = [
    "제공되지 않은 값은 추정하지 않습니다.",
    "BUY, ADD, HOLD, TRIM, SELL, AVOID 중 하나의 투자 의견을 분명히 고르되 자동 주문 지시로 쓰지 않습니다.",
    "알림 발송 우선도와 투자 판단을 섞지 않습니다.",
    "뉴스·공시 데이터가 있으면 가격·수급·추세 판단과 연결해 적극적으로 해석하고 반대 근거와 무효화 조건을 함께 제시합니다.",
]


def _prompt(
    prompt_id: str,
    label: str,
    purpose: str,
    user_prompt: str,
    system_prompt: str = "너는 투자 자문가가 아니라 포트폴리오 관제 분석가다. 제공된 데이터와 관계 규칙만 사용한다.",
    guardrails: List[str] = None,
    output_schema: Dict[str, object] = None,
) -> OntologyPromptTemplate:
    return OntologyPromptTemplate(
        prompt_id,
        label,
        AI_PROMPT_REGISTRY_VERSION,
        purpose,
        system_prompt,
        user_prompt,
        dict(output_schema or PROMPT_OUTPUT_SCHEMA),
        list(guardrails or COMMON_PROMPT_GUARDRAILS),
    )


DEFAULT_PROMPT_TEMPLATES = [
    _prompt(
        "default",
        "기본 알림 AI 의견",
        "알림 데이터, 발송 기준, 부족 데이터를 읽고 사용자가 바로 확인할 핵심을 짧게 정리합니다.",
        "알림 원문과 기준을 보고 해석, 의견, 다음 확인 항목을 1~2줄씩 제시한다.",
    ),
    _prompt(
        "investmentInsight",
        "온톨로지 투자 인사이트 AI 의견",
        "보유, 관심종목, 외부 신호, 기존 모델 신호가 만든 관계 조합을 하나의 투자 인사이트로 해석합니다.",
        "인사이트 유형, 핵심 결론, 근거 신호, 원본 신호, 부족 데이터, 뉴스·공시를 보고 BUY, ADD, HOLD, TRIM, SELL, AVOID 중 하나의 투자 의견과 보류/무효화 조건을 제시한다.",
        system_prompt="너는 자동 주문 지시자가 아니라 온톨로지 관계 그래프를 해석하는 포트폴리오 관제 분석가다. 소극적 요약보다 현재 투자 의견과 우선순위를 분명히 말한다.",
        guardrails=[
            "개별 점수 하나가 아니라 관계 규칙, 뉴스·공시, 포트폴리오 노출을 종합해 하나의 투자 의견을 고릅니다.",
            "sourceSignalTypes와 ontologyInsight를 우선 근거로 사용합니다.",
            "상충 신호, 데이터 신뢰도, 포트폴리오 노출을 분리해 설명합니다.",
            "새 관계가 다음 데이터 업데이트에서도 유지되는지 확인 기준을 제시합니다.",
            "뉴스·공시가 있으면 핵심 영향, 반대 가능성, 원문 확인 항목, 무효화 조건을 나눠 말합니다.",
        ],
    ),
    _prompt(
        "modelBuy",
        "매수 후보 AI 의견",
        "모델 매수 후보 알림의 근거와 첫 진입 전 확인할 리스크를 설명합니다.",
        "매수 후보 점수, 현재가, 수급, 추세, 적정가 정보를 보고 분할매수 가능성과 보류 조건을 나눠 설명한다.",
    ),
    _prompt(
        "modelSell",
        "매도 후보 AI 의견",
        "모델 매도 후보 알림의 매도 압력과 분할 대응 기준을 설명합니다.",
        "매도 점수, 손익률, 수급, 추세를 보고 분할매도, 손절, 보유 유지 중 어떤 확인 기준이 우선인지 설명한다.",
    ),
    _prompt(
        "watchlistBuyCandidate",
        "관심종목 매수 후보 AI 의견",
        "보유 전 관심종목의 매수 후보 신호를 진입 조건과 보류 조건으로 분리합니다.",
        "관심종목의 가격, 거래량, 추세, 매수 후보 점수를 보고 첫 진입 전 확인할 조건을 제시한다.",
    ),
    _prompt(
        "watchlistQuote",
        "관심종목 시세 AI 의견",
        "관심종목 가격 변화가 매수 후보 검토로 이어질지 판단할 확인 기준을 설명합니다.",
        "관심종목의 현재가 변화, 직전 가격, 수급, 추세를 보고 추적 강화 또는 관망 기준을 제시한다.",
    ),
    _prompt(
        "watchlistQuotePending",
        "관심종목 시세 대기 AI 의견",
        "시세 미수집 상태가 데이터 품질에 주는 영향을 설명합니다.",
        "현재가가 없는 이유와 확인해야 할 연결, 종목 코드, 데이터 수집 상태를 정리한다.",
    ),
    _prompt(
        "watchlistOntologySignal",
        "관심종목 관계 신호 AI 의견",
        "관심종목의 온톨로지 관계 규칙이 만든 진입, 회복, 리스크 신호를 해석합니다.",
        "관심종목의 관계 규칙, 추세 동역학, 현재가, 수급, 부족 데이터를 보고 첫 진입, 관망, 회피 중 어떤 확인 기준이 우선인지 설명한다.",
    ),
    _prompt(
        "holdingTiming",
        "보유 타이밍 AI 분석",
        "보유 종목의 현재 가격, 수급, 추세, 공시, 뉴스 헤드라인을 관계 규칙과 함께 종합해 대응 우선순위를 설명합니다.",
        "대상 종목, 성립한 관계 규칙, 가격·수급·추세, OpenDART 공시, 뉴스 헤드라인, 부족 데이터를 보고 BUY, ADD, HOLD, TRIM, SELL, AVOID 중 하나의 투자 의견과 현재 우선순위를 제시한다.",
        guardrails=[
            "제공되지 않은 값은 추정하지 않습니다.",
            "투자 의견은 분명히 말하되 자동 주문 지시로 표현하지 않습니다.",
            "뉴스나 공시가 없으면 있다고 가정하지 않습니다.",
            "공식 점수보다 관계 규칙, 근거, 부족 데이터를 우선합니다.",
            "공시와 뉴스가 있으면 제목, 접수일, 출처, 가격·수급 반응, 반대 근거를 연결해 적극적으로 해석합니다.",
        ],
    ),
    _prompt(
        "monitorHeartbeat",
        "모니터링 상태 AI 의견",
        "실시간 모니터링이 정상 작동 중인지와 투자 판단 신호가 아닌지 구분합니다.",
        "모니터링 상태, 보유 수, 평가 정보를 보고 시스템 상태와 매매 판단 여부를 분리해 설명한다.",
    ),
    _prompt(
        "monitorConnection",
        "연결 상태 AI 의견",
        "토스 또는 외부 연결 실패가 데이터 신뢰도에 미치는 영향을 설명합니다.",
        "연결 모드, 실패 단계, 재시도 상태를 보고 일시 오류와 지속 오류를 구분해 다음 점검을 제시한다.",
    ),
    _prompt(
        "monitorPositionChange",
        "보유 수량 변화 AI 의견",
        "보유 수량 변화가 포지션 관리에 주는 의미를 설명합니다.",
        "이전 수량, 현재 수량, 현재가, 평균매입가, 수익률을 보고 의도한 매매 반영 여부와 비중 변화를 점검한다.",
    ),
    _prompt(
        "monitorPnlChange",
        "손익률 변화 AI 의견",
        "손익률 급변의 방향과 대응 기준을 설명합니다.",
        "이전 손익률, 현재 손익률, 변화폭, 현재가와 평균매입가를 보고 손실 관리 또는 수익 보호 기준을 제시한다.",
    ),
    _prompt(
        "monitorValueChange",
        "평가액 변화 AI 의견",
        "평가액 변화가 가격 변화인지 수량 변화인지 분리해 설명합니다.",
        "이전 평가액, 현재 평가액, 변화율, 현재가, 수익률을 보고 포트폴리오 영향과 확인 기준을 제시한다.",
    ),
    _prompt(
        "monitorTrendChange",
        "이동평균·추세 AI 의견",
        "현재가와 20일/60일선 관계가 매매 타이밍에 주는 의미를 설명합니다.",
        "이동평균 돌파, 이탈, 크로스, 수급 동반 여부를 보고 추세 회복 또는 약화 기준을 제시한다.",
    ),
    _prompt(
        "monitorCashChange",
        "현금 비중 AI 의견",
        "현금 비중 변화가 리스크 관리와 매수 여력에 주는 의미를 설명합니다.",
        "시장별 현금 비중의 이전/현재/변화를 보고 매수 여력, 방어력, 리밸런싱 관점을 분리한다.",
    ),
    _prompt(
        "monitorDecisionChange",
        "판단 변화 AI 분석",
        "이전 판단과 현재 판단이 달라진 이유를 관계 규칙과 데이터 변화로 분해합니다.",
        "이전 상태와 현재 상태의 차이를 비교해 판단 변화 원인, 노이즈 가능성, 재확인 조건을 설명한다.",
        system_prompt="너는 실시간 모니터링 변화 원인을 설명하는 분석가다.",
        guardrails=[
            "반복 알림 여부와 임계값 근처 흔들림을 반드시 점검합니다.",
            "체결강도와 투자자별 수급은 국내장 등 적용 가능한 시장에서만 부족 데이터로 표시합니다.",
            "점수가 같아도 선택 규칙과 성립 규칙 조합 변화를 분리합니다.",
        ],
    ),
    _prompt(
        "externalEquityMove",
        "미국 주식 변동 AI 의견",
        "미국 주식 가격/거래량 변화가 보유 종목 판단에 주는 의미를 설명합니다.",
        "Alpha Vantage 가격 변화, 거래량, 보유 수익률을 보고 단기 변동과 포지션 대응 기준을 제시한다.",
    ),
    _prompt(
        "externalCryptoMove",
        "크립토 연동 AI 분석",
        "BTC/ETH 급변이 보유 주식과 어떤 관계를 가질 수 있는지 분리해 설명합니다.",
        "크립토 변화율, 거래액, 민감 종목 보유 여부를 근거로 확인할 연결 관계와 노이즈 가능성을 설명한다.",
        system_prompt="너는 외부 시장 신호와 보유 종목의 연결 관계를 검토하는 분석가다.",
        guardrails=[
            "크립토 가격만으로 주식 매매 결론을 내리지 않습니다.",
            "민감 종목이 없으면 시장 참고 신호로만 표현합니다.",
            "BTC 민감 종목과 일반 시장 위험 선호 신호를 구분합니다.",
        ],
    ),
    _prompt(
        "externalMacroShift",
        "매크로 변화 AI 의견",
        "금리와 스프레드 변화가 성장주, 현금 비중, 리스크 선호에 주는 의미를 설명합니다.",
        "FRED 지표 변화와 기준값을 보고 성장주 할인율, 위험 선호, 포트폴리오 점검 기준을 제시한다.",
    ),
    _prompt(
        "externalDartDisclosure",
        "국내 공시 AI 의견",
        "OpenDART 신규 공시의 성격과 원문 확인 포인트를 설명합니다.",
        "공시 제목, 접수일, 보유 수익률, 공시 해석 결과를 보고 영향 가능성과 원문 확인 항목을 제시한다.",
    ),
    _prompt(
        "externalDataConnection",
        "외부 데이터 연결 AI 의견",
        "외부 API 연결 오류가 알림 신뢰도에 주는 영향을 설명합니다.",
        "실패한 데이터 소스, 오류 메시지, 재시도 필요 여부를 보고 투자 판단 전 데이터 복구 우선순위를 제시한다.",
    ),
    _prompt(
        "modelReview",
        "모델 개선 AI 리뷰",
        "알림 이후 비동기로 모델의 부족 데이터, 관계 규칙, 프롬프트 개선점을 검토합니다.",
        "알림 원문, 관계 규칙, 부족 데이터, 최근 반복 여부를 보고 모델 개선 후보를 구조화한다.",
        system_prompt="너는 모델 개발 리뷰어다. 현재 규칙과 데이터 품질을 점검해 개선안을 제안한다.",
        output_schema={
            "dataValidation": ["string"],
            "ontologyRuleSuggestion": ["string"],
            "promptSuggestion": ["string"],
            "noiseReduction": ["string"],
        },
        guardrails=[
            "새 규칙 제안은 어떤 관계 타입에 속하는지 명시합니다.",
            "발송 우선도와 투자 판단을 섞지 않습니다.",
            "이미 생성된 AI 리뷰를 다시 요약하지 않습니다.",
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
    if not text:
        return list(DEFAULT_PROMPT_TEMPLATES)
    configured = parse_ai_prompt_templates_text(text)
    merged: Dict[str, OntologyPromptTemplate] = {item.prompt_id: item for item in DEFAULT_PROMPT_TEMPLATES}
    extra_order: List[str] = []
    for item in configured:
        if item.prompt_id not in merged:
            extra_order.append(item.prompt_id)
        merged[item.prompt_id] = item
    return [merged[item.prompt_id] for item in DEFAULT_PROMPT_TEMPLATES if item.prompt_id in merged] + [merged[key] for key in extra_order]


def prompt_template(prompt_id: str, settings: Optional[Dict[str, object]] = None) -> OntologyPromptTemplate:
    templates = prompt_templates_from_settings(settings)
    requested = str(prompt_id or "").strip()
    for item in templates:
        if item.prompt_id == requested:
            return item
    for item in templates:
        if item.prompt_id == "default":
            return item
    return templates[0]


def prompt_template_for_message_type(message_type: str, settings: Optional[Dict[str, object]] = None) -> OntologyPromptTemplate:
    return prompt_template(str(message_type or "").strip() or "default", settings)


def score_band(score: float) -> ScoreBandDefinition:
    value = float(score or 0)
    for band in SCORE_BANDS:
        if band.contains(value):
            return band
    return SCORE_BANDS[-1]


def strength_label(score: float) -> str:
    return score_band(score).label


def relation_score_meaning(score: float) -> str:
    return score_band(score).meaning


def decision_stage_by_key(stage_key: str) -> DecisionStageDefinition:
    return DECISION_STAGE_DEFINITIONS.get(stage_key, DECISION_STAGE_DEFINITIONS["HOLD_KEEP"])


def decision_action_group_for_label(label: object) -> str:
    text = str(label or "").strip()
    if not text:
        return ""
    stage_key = DECISION_LABEL_ALIASES.get(text)
    if not stage_key:
        for key, stage in DECISION_STAGE_DEFINITIONS.items():
            if text == stage.label:
                stage_key = key
                break
    if stage_key:
        return decision_stage_by_key(stage_key).action_group
    if "비트코인" in text or "크립토" in text or "민감도" in text:
        return "cryptoSensitivity"
    if any(term in text for term in ["손절", "손실", "분할축소"]):
        return "lossControl"
    if any(term in text for term in ["분할매도", "익절", "수익"]):
        return "profitTake"
    if "리밸런싱" in text:
        return "rebalance"
    if any(term in text for term in ["매수", "진입"]):
        return "entryRisk" if "보류" in text else "entry"
    if "공시" in text:
        return "disclosure"
    if "뉴스" in text and any(term in text for term in ["리스크", "대응", "부정"]):
        return "eventRisk"
    if "뉴스" in text and any(term in text for term in ["동조", "확인", "우호"]):
        return "eventConfirmation"
    if any(term in text for term in ["섹터", "피어"]):
        return "sectorContext"
    if any(term in text for term in ["보유", "관망", "관찰", "유지"]):
        return "holdWatch"
    return text


def _stage_for_score(review_stage: str, action_stage: str, score: float) -> DecisionStageDefinition:
    return decision_stage_by_key(action_stage if float(score or 0) >= 70 else review_stage)


def resolve_decision_stage(rule_id: str, score: float, facts: Dict[str, object]) -> DecisionStageDefinition:
    value = float(score or 0)
    pnl = float(facts.get("profitLossRate") or 0)
    loss_threshold = float(facts.get("lossThreshold") or DEFAULT_RELATION_THRESHOLDS["lossRateLow"])
    if rule_id == "trend.breakdown_acceleration.v1":
        return decision_stage_by_key("BREAKDOWN_ACCELERATION" if value >= 70 else "LOSS_REDUCE")
    if rule_id == "breakout.failure.v1":
        return decision_stage_by_key("BREAKOUT_FAILURE" if value >= 70 else "LOSS_REDUCE")
    if rule_id == "support.retest.failed.v1":
        return decision_stage_by_key("SUPPORT_RETEST_FAILED")
    if rule_id == "support.retest.confirmed.v1":
        return decision_stage_by_key("SUPPORT_RETEST")
    if rule_id == "trend.support_retest.v1":
        return decision_stage_by_key("SUPPORT_RETEST")
    if rule_id == "trend.recovery_attempt.v1":
        return decision_stage_by_key("RECOVERY_CONFIRM")
    if rule_id == "holding.loss_guard.breakdown.v1":
        if value >= 70 and pnl <= loss_threshold:
            return decision_stage_by_key("LOSS_CUT")
        if value >= 55:
            return decision_stage_by_key("LOSS_REDUCE")
        return decision_stage_by_key("LOSS_WATCH")
    if rule_id == "holding.profit_take.trend_weakness.v1":
        return _stage_for_score("PROFIT_PARTIAL", "PROFIT_SPLIT", value)
    if rule_id == "holding.concentration.rebalance.v1":
        return _stage_for_score("REBALANCE_REVIEW", "REBALANCE_ACTION", value)
    if rule_id == "factor.crowding.v1":
        return decision_stage_by_key("FACTOR_CROWDING")
    if rule_id == "liquidity.exit_capacity.v1":
        return _stage_for_score("LIQUIDITY_REVIEW", "LIQUIDITY_ACTION", value)
    if rule_id == "distribution.detected.v1":
        return decision_stage_by_key("DISTRIBUTION_REVIEW")
    if rule_id == "profit.protection.volatility.v1":
        return decision_stage_by_key("PROFIT_PROTECT")
    if rule_id == "data.conflict.v1":
        return decision_stage_by_key("DATA_CONFLICT")
    if rule_id == "macro.regime.shift.v1":
        return decision_stage_by_key("MACRO_REGIME")
    if rule_id == "external.crypto.btc_sensitivity.v1":
        return _stage_for_score("BTC_REVIEW", "BTC_REDUCE", value)
    if rule_id == "disclosure.material_event.v1":
        return decision_stage_by_key("DISCLOSURE_REVIEW")
    if rule_id == "news.direct_risk.price_confirmed.v1":
        return decision_stage_by_key("NEWS_RISK")
    if rule_id == "news.direct_support.price_confirmed.v1":
        return decision_stage_by_key("NEWS_CONFIRMATION")
    if rule_id == "news.sector_peer_context.v1":
        return decision_stage_by_key("SECTOR_NEWS")
    if rule_id == "holding.trend_flow.confirmation.v1":
        return decision_stage_by_key("FLOW_DEFENSE" if value >= 55 else "FLOW_WATCH")
    if rule_id == "entry.pullback.supported.v1":
        if value >= 70:
            return decision_stage_by_key("ENTRY_READY")
        if value >= 55:
            return decision_stage_by_key("ENTRY_SPLIT_BUY")
        return decision_stage_by_key("ENTRY_WATCH")
    if rule_id == "entry.add_buy.blocked.v1":
        return decision_stage_by_key("ADD_BUY_BLOCKED")
    if rule_id == "averaging_down.block.v1":
        return decision_stage_by_key("ADD_BUY_BLOCKED")
    return decision_stage_by_key("HOLD_KEEP")


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


def _trend_facts(position: Position) -> Dict[str, object]:
    current = number(position.current_price)
    ma20 = number(position.ma20)
    ma60 = number(position.ma60)
    ma20_distance = number(position.ma20_distance) or (((current / ma20) - 1) * 100.0 if current and ma20 else 0.0)
    ma60_distance = number(position.ma60_distance) or (((current / ma60) - 1) * 100.0 if current and ma60 else 0.0)
    ma20_slope = number(position.ma20_slope)
    ma60_slope = number(position.ma60_slope)
    price_change = number(position.change_rate)
    trend_curve = ma20_slope - ma60_slope
    short_term_breakdown = bool(ma20) and ma20_distance <= -5.0
    medium_term_support = bool(ma60) and ma60_distance >= 0.0
    support_retest = short_term_breakdown and bool(ma60) and ma60_distance >= -1.0
    recovery_attempt = (
        bool(current)
        and (ma20_distance < 0 or number(position.profit_loss_rate) < 0)
        and bool(ma60)
        and ma60_distance >= -1.0
        and (price_change >= 1.0 or ma20_slope >= 0.3 or trend_curve >= 0.5)
    )
    breakdown_acceleration = (
        short_term_breakdown
        and (
            price_change <= -2.0
            or ma20_slope <= -1.0
            or trend_curve <= -1.0
            or (ma60_distance <= -5.0 and ma20_slope < 0 and ma60_slope < 0)
        )
    )
    dynamic_risk = trend_dynamic_risk_score(
        ma20_distance,
        ma60_distance,
        price_change,
        ma20_slope,
        trend_curve,
        support_retest,
        recovery_attempt,
    )
    state = trend_state_label(ma20_distance, ma60_distance, support_retest, recovery_attempt, breakdown_acceleration)
    curve_label = trend_curve_label(trend_curve)
    slope_label = trend_slope_label(ma20_slope, ma60_slope)
    price_momentum_label = direction_label(price_change, "상승", "하락")
    score = clamp(
        ma20_distance * 0.45
        + ma60_distance * 0.25
        + ma20_slope * 3.0
        + ma60_slope * 2.0
        + price_change * 0.4,
        -35.0,
        35.0,
    )
    return {
        "currentPrice": current,
        "ma20": ma20,
        "ma60": ma60,
        "ma20Distance": ma20_distance,
        "ma60Distance": ma60_distance,
        "ma20Slope": ma20_slope,
        "ma60Slope": ma60_slope,
        "priceChangeRate": price_change,
        "priceMomentumLabel": price_momentum_label,
        "trendSlopeLabel": slope_label,
        "trendCurve": trend_curve,
        "trendCurveLabel": curve_label,
        "trendState": state,
        "shortTermBreakdown": short_term_breakdown,
        "mediumTermSupport": medium_term_support,
        "supportRetest": support_retest,
        "recoveryAttempt": recovery_attempt,
        "breakdownAcceleration": breakdown_acceleration,
        "trendDynamicRiskScore": dynamic_risk,
        "trendDynamics": {
            "state": state,
            "priceMomentum": price_momentum_label,
            "priceChangeRate": round(price_change, 2),
            "slope": slope_label,
            "ma20Slope": round(ma20_slope, 2),
            "ma60Slope": round(ma60_slope, 2),
            "curve": curve_label,
            "trendCurve": round(trend_curve, 2),
            "ma20Distance": round(ma20_distance, 2),
            "ma60Distance": round(ma60_distance, 2),
            "shortTermBreakdown": short_term_breakdown,
            "mediumTermSupport": medium_term_support,
            "supportRetest": support_retest,
            "recoveryAttempt": recovery_attempt,
            "breakdownAcceleration": breakdown_acceleration,
            "dynamicRiskScore": round(dynamic_risk, 1),
        },
        "trendScore": score,
    }


def state_number(state: Dict[str, object], *keys: str) -> float:
    if not isinstance(state, dict):
        return 0.0
    for key in keys:
        if key in state and state.get(key) not in (None, ""):
            return number(state.get(key))
    return 0.0


def _temporal_facts(position: Position, previous_state: Dict[str, object] = None, previous_decision: Dict[str, object] = None) -> Dict[str, object]:
    previous_state = previous_state if isinstance(previous_state, dict) else {}
    previous_decision = previous_decision if isinstance(previous_decision, dict) else {}
    current_price = number(position.current_price)
    previous_price = state_number(previous_state, "currentPrice", "current_price", "price")
    current_pnl = number(position.profit_loss_rate)
    previous_pnl = state_number(previous_state, "profitLossRate", "profit_loss_rate")
    previous_ma20_distance = state_number(previous_state, "ma20Distance", "ma20_distance")
    previous_ma60_distance = state_number(previous_state, "ma60Distance", "ma60_distance")
    current_ma20_distance = number(position.ma20_distance)
    current_ma60_distance = number(position.ma60_distance)
    price_delta = ((current_price / previous_price) - 1) * 100 if current_price and previous_price else 0.0
    pnl_delta = current_pnl - previous_pnl if previous_state else 0.0
    ma20_distance_delta = current_ma20_distance - previous_ma20_distance if previous_state else 0.0
    ma60_distance_delta = current_ma60_distance - previous_ma60_distance if previous_state else 0.0
    previous_rule = str(previous_decision.get("selectedRuleId") or previous_decision.get("selected_rule_id") or "")
    previous_label = str(previous_decision.get("decision") or previous_decision.get("label") or "")
    return {
        "hasPreviousState": bool(previous_state or previous_decision),
        "previousPrice": previous_price,
        "previousProfitLossRate": previous_pnl,
        "previousMa20Distance": previous_ma20_distance,
        "previousMa60Distance": previous_ma60_distance,
        "previousRelationScore": state_number(previous_decision, "exitPressure", "exit_pressure", "score"),
        "previousSelectedRuleId": previous_rule,
        "previousDecisionLabel": previous_label,
        "priceDeltaFromPreviousPct": price_delta,
        "profitLossRateDeltaPct": pnl_delta,
        "ma20DistanceDeltaPct": ma20_distance_delta,
        "ma60DistanceDeltaPct": ma60_distance_delta,
        "reclaimedMa20Previously": previous_ma20_distance >= 0 and current_ma20_distance < 0,
        "lostMa60SincePrevious": previous_ma60_distance >= -1.0 and current_ma60_distance < -1.0,
    }


def _liquidity_facts(position: Position) -> Dict[str, object]:
    market_value = number(position.market_value)
    trading_value = number(position.trading_value)
    volume_ratio = number(position.volume_ratio)
    bid_ask_imbalance = number(position.bid_ask_imbalance)
    quantity = number(position.quantity)
    sellable_quantity = number(position.sellable_quantity)
    position_to_trading_value = (market_value / trading_value) * 100 if market_value and trading_value else 0.0
    exit_days = market_value / max(1.0, trading_value * 0.1) if market_value and trading_value else 0.0
    sellable_blocked = bool(quantity and sellable_quantity <= 0)
    liquidity_risk = clamp(
        position_to_trading_value * 2.0
        + max(0.0, 1.0 - volume_ratio) * 18.0
        + max(0.0, -bid_ask_imbalance) * 0.25
        + (25.0 if sellable_blocked else 0.0),
        0.0,
        100.0,
    )
    return {
        "positionToTradingValuePct": position_to_trading_value,
        "exitDaysAtTenPctADV": exit_days,
        "sellableBlocked": sellable_blocked,
        "liquidityRiskScore": liquidity_risk,
    }


def _external_quality_facts(external_signals: Dict[str, object]) -> Dict[str, object]:
    quality = external_signals.get("quality") if isinstance(external_signals.get("quality"), dict) else {}
    freshness = external_signals.get("freshness") if isinstance(external_signals.get("freshness"), dict) else {}
    statuses = external_signals.get("statuses") if isinstance(external_signals.get("statuses"), list) else []
    error_count = len([item for item in statuses if isinstance(item, dict) and not item.get("ok")])
    return {
        "externalSignalQualityScore": number(quality.get("score")) if quality else 0.0,
        "externalSignalCoverageScore": number(quality.get("coverageScore")) if quality else 0.0,
        "externalSignalSourceHealthScore": number(quality.get("sourceHealthScore")) if quality else 0.0,
        "externalSignalAgeMinutes": number(freshness.get("ageMinutes")) if freshness else 0.0,
        "externalSignalFreshnessStatus": str(freshness.get("status") or ""),
        "externalSignalErrorCount": error_count,
    }


def _macro_regime_facts(external_signals: Dict[str, object]) -> Dict[str, object]:
    macro = external_signals.get("macro") if isinstance(external_signals, dict) and isinstance(external_signals.get("macro"), dict) else {}
    series = macro.get("series") if isinstance(macro.get("series"), dict) else {}
    dgs10 = number((series.get("DGS10") or {}).get("value")) if isinstance(series.get("DGS10"), dict) else 0.0
    dgs2 = number((series.get("DGS2") or {}).get("value")) if isinstance(series.get("DGS2"), dict) else 0.0
    dff = number((series.get("DFF") or {}).get("value")) if isinstance(series.get("DFF"), dict) else 0.0
    spread = number(macro.get("yieldSpread10y2y"))
    rate_regime = "high_rate" if dgs10 >= 4.5 else "low_rate" if dgs10 and dgs10 <= 3.0 else "neutral_rate"
    curve_regime = "inverted_curve" if spread < 0 else "positive_curve" if spread > 0 else "flat_or_unknown_curve"
    return {
        "macroYieldSpread10y2y": spread,
        "macroDgs10": dgs10,
        "macroDgs2": dgs2,
        "macroDff": dff,
        "rateRegime": rate_regime,
        "yieldCurveRegime": curve_regime,
        "hasInterestRateSignals": bool(dgs10 or dgs2 or dff or macro.get("yieldSpread10y2y") not in (None, "")),
        "hasMacroSignals": bool(series or macro.get("yieldSpread10y2y") not in (None, "")),
    }


def _fx_rate_for_currency(external_signals: Dict[str, object], currency: str, quote: str = "KRW") -> Dict[str, object]:
    rates = external_signals.get("fxRates") if isinstance(external_signals, dict) else {}
    if not isinstance(rates, dict):
        return {}
    base = str(currency or "").upper().strip()
    quote_currency = str(quote or "KRW").upper().strip()
    if not base or base == quote_currency:
        return {}
    direct_key = base + quote_currency
    reverse_key = quote_currency + base
    for key in [direct_key, base, reverse_key]:
        item = rates.get(key)
        if isinstance(item, dict):
            item_base = str(item.get("base") or item.get("baseCurrency") or base).upper().strip()
            item_quote = str(item.get("quote") or item.get("quoteCurrency") or quote_currency).upper().strip()
            rate = number(item.get("rate") if item.get("rate") not in (None, "") else item.get("value"))
            if item_base == base and item_quote == quote_currency and rate:
                return {**item, "pair": item_base + item_quote, "base": item_base, "quote": item_quote, "rate": rate}
            if item_base == quote_currency and item_quote == base and rate:
                return {**item, "pair": base + quote_currency, "base": base, "quote": quote_currency, "rate": 1 / rate if rate else 0.0}
        elif item not in (None, ""):
            rate = number(item)
            if rate:
                return {"pair": direct_key, "base": base, "quote": quote_currency, "rate": rate, "provider": "externalSignals"}
    return {}


def _fx_regime_facts(position: Position, portfolio: PortfolioSummary, external_signals: Dict[str, object]) -> Dict[str, object]:
    currency = str(position.currency or "").upper().strip()
    rate = _fx_rate_for_currency(external_signals, currency)
    rate_value = number(rate.get("rate")) if rate else 0.0
    exposure_ratio = _position_weight(position, portfolio) if currency and currency != "KRW" else 0.0
    if currency == "USD" and rate_value >= 1450:
        regime = "krw_weakening"
    elif currency == "USD" and rate_value and rate_value <= 1300:
        regime = "krw_strengthening"
    elif currency and currency != "KRW" and rate_value:
        regime = "fx_observed"
    else:
        regime = "base_currency_or_unknown"
    return {
        "fxRatePair": str(rate.get("pair") or ""),
        "fxBaseCurrency": str(rate.get("base") or currency),
        "fxQuoteCurrency": str(rate.get("quote") or "KRW"),
        "fxRateToKrw": rate_value,
        "usdKrwRate": rate_value if currency == "USD" else 0.0,
        "fxProvider": str(rate.get("provider") or ""),
        "fxExposureRatio": exposure_ratio,
        "fxRegime": regime,
        "hasFxRateSignal": bool(rate_value),
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


def _missing(key: str, label: str, effect: str, status: str = "missing", source: str = "") -> Dict[str, str]:
    item = {"key": key, "label": label, "effect": effect, "status": status}
    if source:
        item["source"] = source
    return item


def _coverage_status(
    coverage: Dict[str, object],
    stage: str,
    keys: Iterable[str],
    value: float = 0.0,
    quote_status: str = "",
    quote_hint: str = "",
) -> str:
    if isinstance(coverage, dict) and coverage:
        item = coverage.get(stage) if isinstance(coverage.get(stage), dict) else {}
        fields = set(str(field) for field in (item.get("fields") or []))
        non_zero_fields = set(str(field) for field in (item.get("nonZeroFields") or []))
        if any(key in fields for key in keys):
            return "available" if any(key in non_zero_fields for key in keys) or value else "zero"
        status = str(item.get("status") or "").strip()
        if status in {"empty", "missing"}:
            return status
    if value:
        return "available"
    if quote_hint and quote_hint in str(quote_status or ""):
        return "zero"
    return "missing"


def _availability(status: str, source: str = "") -> Dict[str, str]:
    item = {"status": str(status or "missing")}
    if source:
        item["source"] = source
    return item


def _missing_penalty(item: Dict[str, object]) -> float:
    status = str(item.get("status") or "missing")
    if status in {"zero", "proxy", "empty"}:
        return 6.0
    return 12.0


def moving_average_distance_text(label: str, distance: float) -> str:
    value = abs(round(float(distance or 0), 1))
    value_text = str(int(value)) if value.is_integer() else str(value)
    if distance > 0:
        return label + "보다 " + value_text + "% 높음"
    if distance < 0:
        return label + "보다 " + value_text + "% 낮음"
    return label + "과 같음"


def direction_label(value: float, positive: str, negative: str, flat: str = "보합", threshold: float = 0.2) -> str:
    parsed = float(value or 0)
    if parsed >= threshold:
        return positive
    if parsed <= -threshold:
        return negative
    return flat


def trend_slope_label(ma20_slope: float, ma60_slope: float) -> str:
    short = float(ma20_slope or 0)
    medium = float(ma60_slope or 0)
    if short <= -0.3 and medium <= -0.2:
        return "단기·중기 하락"
    if short >= 0.3 and medium >= 0.2:
        return "단기·중기 상승"
    if short <= -0.3 and medium > 0:
        return "단기 둔화·중기 지지"
    if short >= 0.3 and medium <= 0:
        return "단기 회복·중기 둔화"
    return "혼조·완만"


def trend_curve_label(curve: float) -> str:
    value = float(curve or 0)
    if value <= -1.0:
        return "하락 커브 확대"
    if value <= -0.4:
        return "단기 둔화 커브"
    if value >= 1.0:
        return "회복 커브 확대"
    if value >= 0.4:
        return "단기 회복 커브"
    return "커브 완만"


def trend_state_label(
    ma20_distance: float,
    ma60_distance: float,
    support_retest: bool,
    recovery_attempt: bool,
    breakdown_acceleration: bool,
) -> str:
    if breakdown_acceleration:
        return "하락 가속"
    if support_retest and recovery_attempt:
        return "60일선 지지 반등 시도"
    if support_retest:
        return "60일선 지지 재확인"
    if recovery_attempt:
        return "회복 시도"
    if ma20_distance < 0 and ma60_distance < 0:
        return "추세 하방"
    if ma20_distance > 0 and ma60_distance > 0:
        return "추세 상방"
    return "추세 혼조"


def trend_dynamic_risk_score(
    ma20_distance: float,
    ma60_distance: float,
    price_change: float,
    ma20_slope: float,
    trend_curve: float,
    support_retest: bool,
    recovery_attempt: bool,
) -> float:
    risk = 0.0
    if ma20_distance <= -5:
        risk += min(30.0, abs(ma20_distance) * 2.0)
    if ma60_distance < 0:
        risk += min(20.0, abs(ma60_distance) * 3.0)
    if price_change < 0:
        risk += min(15.0, abs(price_change) * 4.0)
    if ma20_slope < 0:
        risk += min(15.0, abs(ma20_slope) * 8.0)
    if trend_curve < 0:
        risk += min(10.0, abs(trend_curve) * 6.0)
    if support_retest and ma60_distance >= 0:
        risk -= 5.0
    if recovery_attempt:
        risk -= 10.0
    return clamp(risk, 0.0, 100.0)


def position_signal_facts(
    position: Position,
    portfolio: PortfolioSummary,
    external_signals: Optional[Dict[str, object]] = None,
    previous_state: Optional[Dict[str, object]] = None,
    previous_decision: Optional[Dict[str, object]] = None,
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
    foreign_buy_volume = number(position.foreign_buy_volume)
    foreign_sell_volume = number(position.foreign_sell_volume)
    institution_buy_volume = number(position.institution_buy_volume)
    institution_sell_volume = number(position.institution_sell_volume)
    individual_buy_volume = number(position.individual_buy_volume)
    individual_sell_volume = number(position.individual_sell_volume)
    execution_direction_proxy = bool(position.trade_strength or bid_ask_imbalance or orderbook_bid_volume or orderbook_ask_volume)
    market_signal_coverage = dict(position.market_signal_coverage or {}) if isinstance(position.market_signal_coverage, dict) else {}
    quote_status = str(position.quote_status or "")
    btc = _btc_market(external_signals)
    disclosures = external_signals.get("dartDisclosures") if isinstance(external_signals, dict) else {}
    symbol = str(position.symbol or "").upper()
    disclosure = disclosures.get(symbol) if isinstance(disclosures, dict) else None
    news_headlines = external_signals.get("newsHeadlines") if isinstance(external_signals, dict) else {}
    news_context = news_headlines.get(symbol) if isinstance(news_headlines, dict) and isinstance(news_headlines.get(symbol), dict) else {}
    sec_filings = external_signals.get("secFilings") if isinstance(external_signals, dict) else {}
    sec_context = sec_filings.get(symbol) if isinstance(sec_filings, dict) and isinstance(sec_filings.get(symbol), dict) else {}
    facts: Dict[str, object] = {
        "symbol": symbol,
        "name": position.name,
        "market": position.market,
        "currency": position.currency,
        "sector": position.sector,
        "source": position.source,
        "quoteSource": position.quote_source,
        "quoteStatus": quote_status,
        "quoteMessage": position.quote_message,
        "dataQuality": position.data_quality,
        "updatedAt": position.updated_at,
        "marketSignalCoverage": market_signal_coverage,
        "isHolding": str(position.source or "holding") != "watchlist",
        "isWatchlist": str(position.source or "") == "watchlist",
        "profitLossRate": number(position.profit_loss_rate),
        "profitLoss": number(position.profit_loss),
        "marketValue": number(position.market_value),
        "quantity": number(position.quantity),
        "sellableQuantity": number(position.sellable_quantity),
        "averagePrice": number(position.average_price),
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
        "foreignBuyVolume": foreign_buy_volume,
        "foreignSellVolume": foreign_sell_volume,
        "institutionBuyVolume": institution_buy_volume,
        "institutionSellVolume": institution_sell_volume,
        "individualBuyVolume": individual_buy_volume,
        "individualSellVolume": individual_sell_volume,
        "executionDirectionProxy": execution_direction_proxy,
        "btcChange24h": number(btc.get("change24h")) if btc else 0.0,
        "btcChange7d": number(btc.get("change7d")) if btc else 0.0,
        "btcPrice": number(btc.get("price")) if btc else 0.0,
        "btcVolume24h": number(btc.get("volume24h")) if btc else 0.0,
        "isBtcSensitive": symbol in BTC_SENSITIVE_SYMBOLS,
        "dartDisclosure": dict(disclosure or {}) if isinstance(disclosure, dict) else {},
        "newsHeadlines": dict(news_context or {}) if isinstance(news_context, dict) else {},
        "secFiling": dict(sec_context or {}) if isinstance(sec_context, dict) else {},
        "expectsKrMicrostructureSignals": expects_kr_microstructure_signals(position.market, position.currency, symbol),
    }
    research_by_id = {}
    for item in research_evidence_from_facts(symbol, facts) + research_evidence_from_external_signals(symbol, external_signals):
        research_by_id[item.evidence_id] = item.to_dict()
    facts["researchEvidence"] = list(research_by_id.values())
    facts.update(research_evidence_facts(facts["researchEvidence"]))
    facts.update(trend)
    facts.update(flow)
    facts.update(_temporal_facts(position, previous_state, previous_decision))
    facts.update(_liquidity_facts(position))
    facts.update(_external_quality_facts(external_signals))
    facts.update(_macro_regime_facts(external_signals))
    facts.update(_fx_regime_facts(position, portfolio, external_signals))
    missing: List[Dict[str, str]] = []
    if not facts["currentPrice"]:
        missing.append(_missing("currentPrice", "현재가", "가격·이동평균 관계 판단 신뢰도가 낮아집니다."))
    if not facts["ma20"]:
        missing.append(_missing("ma20", "20일 이동평균", "단기 추세 이탈 여부를 확인할 수 없습니다."))
    if not facts["ma60"]:
        missing.append(_missing("ma60", "60일 이동평균", "중기 추세 위치를 확인할 수 없습니다."))
    expects_kr_signals = bool(facts.get("expectsKrMicrostructureSignals"))
    trade_strength_status = _coverage_status(
        market_signal_coverage,
        "ccnl",
        ["tradeStrength"],
        float(facts["tradeStrength"] or 0),
        quote_status,
        "체결강도",
    )
    execution_volume_status = _coverage_status(
        market_signal_coverage,
        "ccnl",
        ["buyVolume", "sellVolume"],
        float(total_execution_volume or 0),
        quote_status,
        "방향별 체결량",
    )
    investor_flow_status = _coverage_status(
        market_signal_coverage,
        "investor",
        [
            "foreignBuyVolume",
            "foreignSellVolume",
            "foreignNetVolume",
            "foreignNetAmount",
            "institutionBuyVolume",
            "institutionSellVolume",
            "institutionNetVolume",
            "institutionNetAmount",
            "individualBuyVolume",
            "individualSellVolume",
            "individualNetVolume",
            "individualNetAmount",
        ],
        float(facts["investorFlowBase"] or 0),
        quote_status,
        "투자자별 수급",
    )
    if execution_volume_status != "available" and execution_direction_proxy:
        execution_volume_status = "proxy"
    facts["dataAvailability"] = {
        "tradeStrength": _availability(trade_strength_status, "KIS ccnl"),
        "executionVolume": _availability(execution_volume_status, "KIS ccnl/orderbook"),
        "investorFlow": _availability(investor_flow_status, "KIS investor"),
    }
    if expects_kr_signals and trade_strength_status != "available":
        if trade_strength_status == "zero":
            effect = "체결강도 응답은 있었지만 0으로 들어와 체결 압력 근거로 쓰지 않습니다."
        elif trade_strength_status == "empty":
            effect = "KIS 체결 단계 응답이 비어 있어 수급 방향 판단을 가격·거래량 중심으로 봅니다."
        else:
            effect = "체결 압력 확인값이 없어 수급 방향 판단을 가격·거래량 중심으로 봅니다."
        missing.append(_missing("tradeStrength", "체결강도", effect, trade_strength_status, "KIS ccnl"))
    if expects_kr_signals and not total_execution_volume and not execution_direction_proxy:
        if execution_volume_status == "zero":
            effect = "방향별 체결량 응답은 있었지만 매수·매도 합계가 0으로 들어와 수급 방향 점수는 중립에 가깝게 처리합니다."
        elif execution_volume_status == "empty":
            effect = "KIS 체결 단계 응답이 비어 있어 매수·매도 방향별 체결 압력을 확인하지 못합니다."
        else:
            effect = "매수·매도 방향별 체결 압력을 확인하지 못해 수급 방향 점수는 중립에 가깝게 처리합니다."
        missing.append(_missing("executionVolume", "방향별 매수/매도 체결량", effect, execution_volume_status, "KIS ccnl"))
    if expects_kr_signals and not facts["investorFlowBase"]:
        if investor_flow_status == "zero":
            effect = "투자자별 수급 응답은 있었지만 외국인·기관·개인 순매수 합계가 0으로 들어와 방향성 근거로 쓰지 않습니다."
        elif investor_flow_status == "empty":
            effect = "KIS 투자자 단계 응답이 비어 있어 주체별 수급은 중립으로 처리합니다."
        else:
            effect = "외국인·기관·개인 순매수는 수집되지 않아 주체별 수급은 중립으로 처리합니다. 가격·거래량·체결강도 중심 판단입니다."
        missing.append(_missing("investorFlow", "투자자별 수급", effect, investor_flow_status, "KIS investor"))
    if facts["isBtcSensitive"] and not btc:
        missing.append(_missing("btcMarket", "비트코인 시장 데이터", "비트코인 민감 종목의 외부 연동 위험을 확인하지 못합니다."))
    data_quality = clamp(100.0 - sum(_missing_penalty(item) for item in missing), 35.0, 100.0)
    facts["missingData"] = missing
    facts["dataQualityScore"] = data_quality
    return facts


def _thresholds(settings: Optional[Dict[str, object]]) -> Dict[str, float]:
    settings = settings or {}
    legacy = parse_assignments(str(settings.get("alertThresholds") or ""), DEFAULT_RELATION_THRESHOLDS)
    configured = str(settings.get("relationRuleThresholds") or "").strip()
    if not configured:
        return legacy
    return parse_assignments(configured, legacy)


def relation_thresholds_from_settings(settings: Optional[Dict[str, object]] = None) -> Dict[str, float]:
    return _thresholds(settings)


def _evidence_payload(item: Dict[str, object]) -> Dict[str, object]:
    payload = item.get("payload") if isinstance(item, dict) else {}
    return payload if isinstance(payload, dict) else {}


def _evidence_scope(item: Dict[str, object]) -> str:
    payload = _evidence_payload(item)
    return str(item.get("relationScope") or payload.get("relationScope") or "").strip().lower()


def _evidence_relevance(item: Dict[str, object]) -> float:
    payload = _evidence_payload(item)
    return number(item.get("relevanceScore") or payload.get("relevanceScore"))


def _evidence_source_reliability(item: Dict[str, object]) -> float:
    payload = _evidence_payload(item)
    return number(item.get("sourceReliability") or payload.get("sourceReliability") or item.get("confidence"))


def _evidence_title(item: Dict[str, object]) -> str:
    return " ".join(str(item.get("title") or item.get("summary") or "").split())[:180]


def _evidence_minutes_since(item: Dict[str, object]) -> float:
    raw = str(item.get("publishedAt") or item.get("observedAt") or "").strip()
    if not raw:
        return 0.0
    for candidate in [raw, raw.replace("Z", "+00:00")]:
        try:
            parsed = datetime.fromisoformat(candidate)
            parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 60)
        except ValueError:
            pass
    for fmt in ["%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S", "%Y%m%d"]:
        try:
            parsed = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 60)
        except ValueError:
            pass
    return 0.0


def research_evidence_facts(evidence_items: Iterable[Dict[str, object]]) -> Dict[str, object]:
    news_items = [
        item
        for item in evidence_items or []
        if isinstance(item, dict) and str(item.get("kind") or "").lower() == "news"
    ]
    direct = [item for item in news_items if _evidence_scope(item) == "direct"]
    peer = [item for item in news_items if _evidence_scope(item) == "peer"]
    sector = [item for item in news_items if _evidence_scope(item) == "sector"]
    market = [item for item in news_items if _evidence_scope(item) == "market"]
    risk = [item for item in direct if str(item.get("polarity") or "").lower() in {"risk", "contradiction"}]
    support = [item for item in direct if str(item.get("polarity") or "").lower() == "support"]
    material = [
        item
        for item in news_items
        if _evidence_scope(item) == "direct" or _evidence_relevance(item) >= 55
    ]

    def titles(rows: List[Dict[str, object]], limit: int = 4) -> List[str]:
        result: List[str] = []
        for row in sorted(rows, key=lambda item: (_evidence_relevance(item), number(item.get("impactScore"))), reverse=True):
            title = _evidence_title(row)
            if title and title not in result:
                result.append(title)
            if len(result) >= limit:
                break
        return result

    relevance_values = [_evidence_relevance(item) for item in news_items if _evidence_relevance(item)]
    reliability_values = [_evidence_source_reliability(item) for item in news_items if _evidence_source_reliability(item)]
    risk_score = sum((number(item.get("impactScore")) or 2.0) * max(0.35, _evidence_relevance(item) / 100) for item in risk)
    support_score = sum((number(item.get("impactScore")) or 2.0) * max(0.35, _evidence_relevance(item) / 100) for item in support)
    latest_direct_age = min([_evidence_minutes_since(item) for item in direct if _evidence_minutes_since(item)], default=0.0)
    return {
        "researchEvidenceCount": len([item for item in evidence_items or [] if isinstance(item, dict)]),
        "newsEvidenceCount": len(news_items),
        "directNewsCount": len(direct),
        "directRiskNewsCount": len(risk),
        "directSupportNewsCount": len(support),
        "peerNewsCount": len(peer),
        "sectorNewsCount": len(sector),
        "marketNewsCount": len(market),
        "materialNewsCount": len(material),
        "averageNewsRelevanceScore": round(sum(relevance_values) / len(relevance_values), 1) if relevance_values else 0.0,
        "averageNewsSourceReliability": round(sum(reliability_values) / len(reliability_values), 2) if reliability_values else 0.0,
        "latestDirectNewsAgeMinutes": round(latest_direct_age, 1),
        "newsMomentumScore": round(support_score - risk_score, 1),
        "newsConflictScore": round(min(support_score, risk_score), 1) if risk_score and support_score else 0.0,
        "topNewsTitles": titles(material or news_items, 5),
        "directRiskNewsTitles": titles(risk, 4),
        "directSupportNewsTitles": titles(support, 4),
        "sectorNewsTitles": titles(peer + sector + market, 4),
    }


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
        stage = decision_stage_by_key("RELATION_WATCH")
        band = score_band(35.0)
        return {
            "label": stage.label,
            "tone": stage.tone,
            "score": 35.0,
            "basis": "ontologyRelationRules",
            "selectedRuleId": "",
            "decisionStage": stage.stage_key,
            "actionGroup": stage.action_group,
            "actionLevel": stage.action_level,
            "scoreBand": band.to_dict(),
            "nextStageAt": stage.next_stage_at,
        }
    priority = {
        "breakout.failure.v1": 48,
        "trend.breakdown_acceleration.v1": 45,
        "support.retest.failed.v1": 43,
        "holding.loss_guard.breakdown.v1": 40,
        "entry.pullback.supported.v1": 38,
        "averaging_down.block.v1": 37,
        "distribution.detected.v1": 36,
        "profit.protection.volatility.v1": 36,
        "holding.profit_take.trend_weakness.v1": 35,
        "liquidity.exit_capacity.v1": 34,
        "news.direct_risk.price_confirmed.v1": 32,
        "disclosure.material_event.v1": 30,
        "news.direct_support.price_confirmed.v1": 28,
        "factor.crowding.v1": 19,
        "macro.regime.shift.v1": 27,
        "external.crypto.btc_sensitivity.v1": 25,
        "news.sector_peer_context.v1": 24,
        "data.conflict.v1": 23,
        "holding.concentration.rebalance.v1": 20,
        "entry.add_buy.blocked.v1": 18,
        "support.retest.confirmed.v1": 17,
        "trend.support_retest.v1": 16,
        "trend.recovery_attempt.v1": 14,
    }
    selected = max(active, key=lambda item: (priority.get(item.rule_id, 10), item.strength_score, item.confidence))
    stage = resolve_decision_stage(selected.rule_id, selected.strength_score, facts)
    band = score_band(selected.strength_score)
    tone = stage.tone
    if selected.rule_id == "holding.profit_take.trend_weakness.v1" and selected.strength_score >= 80:
        tone = "danger"
    return {
        "label": stage.label,
        "tone": tone,
        "score": round(float(selected.strength_score or 0), 1),
        "basis": "ontologyRelationRules",
        "selectedRuleId": selected.rule_id,
        "decisionStage": stage.stage_key,
        "actionGroup": stage.action_group,
        "actionLevel": stage.action_level,
        "scoreBand": band.to_dict(),
        "nextStageAt": stage.next_stage_at,
    }


def _append_unique(rows: List[str], value: object) -> None:
    text = str(value or "").strip()
    if text and text not in rows:
        rows.append(text)


def _active_rule_labels(matches: List[OntologyRuleMatch]) -> List[str]:
    return [
        item.label
        for item in matches or []
        if item.matched and not item.reference_only and str(item.label or "").strip()
    ]


def execution_plan_from_relation_context(
    facts: Dict[str, object],
    decision: Dict[str, object],
    matches: List[OntologyRuleMatch],
) -> Dict[str, object]:
    facts = facts or {}
    decision = decision or {}
    action_group = str(decision.get("actionGroup") or "")
    action_level = str(decision.get("actionLevel") or "")
    label = str(decision.get("label") or "")
    pnl = float(facts.get("profitLossRate") or 0)
    ma20_distance = float(facts.get("ma20Distance") or 0)
    ma60_distance = float(facts.get("ma60Distance") or 0)
    volume_ratio = float(facts.get("volumeRatio") or 0)
    trade_strength = float(facts.get("tradeStrength") or 0)
    bid_ask_imbalance = float(facts.get("bidAskImbalance") or 0)
    primary_action = "HOLD"
    primary_label = "보유 유지, 다음 데이터 확인"
    blocked_actions: List[str] = []
    risk_signals: List[str] = []
    support_signals: List[str] = []
    counter_signals: List[str] = []
    strengthen_conditions: List[str] = []
    weaken_conditions: List[str] = []
    next_checks: List[str] = []

    if action_group == "lossControl":
        primary_action = "TRIM_OR_SELL_REVIEW" if action_level in {"action", "urgent"} else "LOSS_CONTROL_REVIEW"
        primary_label = "추가매수 보류, 분할축소/매도 기준 검토"
        blocked_actions.append("20일선 회복 전 추가매수")
        _append_unique(risk_signals, "수익률 " + ("%.1f" % pnl) + "%")
        if ma20_distance < 0:
            _append_unique(risk_signals, "20일선보다 " + ("%.1f" % abs(ma20_distance)) + "% 낮음")
        if ma60_distance < 0:
            _append_unique(risk_signals, "60일선보다 " + ("%.1f" % abs(ma60_distance)) + "% 낮음")
            _append_unique(strengthen_conditions, "60일선 아래 상태가 유지되면 손절·축소 강도를 높임")
        else:
            _append_unique(counter_signals, "60일선보다 " + ("%.1f" % abs(ma60_distance)) + "% 높아 중기 지지는 남아 있음")
            _append_unique(strengthen_conditions, "60일선 아래로 내려가면 손절·축소 강도 상향")
        if volume_ratio and volume_ratio < 1:
            _append_unique(counter_signals, "거래량 " + ("%.1f" % volume_ratio) + "x로 평균 이하라 투매 확정은 아님")
        elif volume_ratio >= 1:
            _append_unique(risk_signals, "거래량 " + ("%.1f" % volume_ratio) + "x로 하락 확인 강도 상승")
        if trade_strength and trade_strength < 100:
            _append_unique(risk_signals, "체결강도 " + ("%.1f" % trade_strength) + "로 매수 체결 우위 부족")
        elif trade_strength >= 100:
            _append_unique(counter_signals, "체결강도 " + ("%.1f" % trade_strength) + "로 단기 매수 체결은 확인됨")
        if bid_ask_imbalance > 0:
            _append_unique(counter_signals, "호가잔량 매수 우위 " + ("%.1f" % bid_ask_imbalance) + "%")
        elif bid_ask_imbalance < 0:
            _append_unique(risk_signals, "호가잔량 매도 우위 " + ("%.1f" % abs(bid_ask_imbalance)) + "%")
        _append_unique(weaken_conditions, "20일선 회복과 거래량 동반 반등이 확인되면 축소 강도 완화")
        _append_unique(next_checks, "매도 가능 수량과 손절/분할축소 기준 확인")
        _append_unique(next_checks, "다음 조회에서도 손실 관리 규칙이 유지되는지 확인")
    elif action_group == "profitTake":
        primary_action = "TRIM_REVIEW"
        primary_label = "분할매도/수익 보호 기준 검토"
        _append_unique(risk_signals, "수익 구간에서 추세 약화")
        _append_unique(blocked_actions, "목표·추세 확인 없는 일괄 매도")
        _append_unique(strengthen_conditions, "20일선 회복 실패와 거래량 증가가 이어지면 분할매도 강도 상향")
        _append_unique(weaken_conditions, "20일선 회복과 수급 개선이 확인되면 보유 유지로 완화")
        _append_unique(next_checks, "목표 수익률, 분할매도 수량, 재진입 조건 확인")
    elif action_group == "rebalance":
        primary_action = "REBALANCE_REVIEW"
        primary_label = "초과 비중 축소와 리밸런싱 검토"
        _append_unique(blocked_actions, "같은 섹터 추가매수")
        _append_unique(risk_signals, "포트폴리오 집중도 확대")
        _append_unique(next_checks, "섹터 비중과 단일 종목 비중 한도 확인")
    elif action_group == "entry":
        primary_action = "SPLIT_BUY_REVIEW"
        primary_label = "소액 분할매수 조건 검토"
        _append_unique(blocked_actions, "확인 없는 일괄 매수")
        _append_unique(support_signals, "눌림목과 지지 신호가 함께 성립")
        _append_unique(strengthen_conditions, "20일선 회복, 거래량 증가, 수급 개선이 함께 나오면 진입 강도 상향")
        _append_unique(weaken_conditions, "60일선 이탈 또는 부정 공시가 나오면 매수 후보 해제")
        _append_unique(next_checks, "첫 진입 가격, 손절 기준, 추가매수 조건 확인")
    elif action_group == "entryRisk":
        primary_action = "AVOID_OR_WAIT"
        primary_label = "추가매수 보류, 회복 조건 대기"
        _append_unique(blocked_actions, "추세 회복 전 추가매수")
        _append_unique(risk_signals, "보유 종목의 추세 훼손 또는 이벤트 리스크")
        _append_unique(weaken_conditions, "20일선 회복과 부정 이벤트 해소 시 보류 강도 완화")
        _append_unique(next_checks, "회복 조건과 비중 한도 확인")
    elif action_group == "disclosure":
        primary_action = "EVENT_RISK_REVIEW"
        primary_label = "공시 원문과 가격 반응 확인"
        _append_unique(blocked_actions, "공시 성격 확인 전 비중 확대")
        _append_unique(risk_signals, "신규 공시가 보유 판단에 연결됨")
        _append_unique(strengthen_conditions, "공시 이후 거래량 증가와 20일선 이탈이 겹치면 방어 강도 상향")
        _append_unique(weaken_conditions, "공시 영향이 제한적이고 가격·수급이 회복되면 경계 강도 완화")
        _append_unique(next_checks, "공시 원문, 접수번호, 후속 정정 여부 확인")
    elif action_group == "eventRisk":
        primary_action = "EVENT_RISK_REVIEW"
        primary_label = "뉴스 리스크와 가격·수급 동조 확인"
        _append_unique(blocked_actions, "뉴스 영향 확인 전 추가매수")
        _append_unique(risk_signals, "직접 부정 뉴스가 가격·수급 반응과 연결됨")
        _append_unique(strengthen_conditions, "후속 보도와 거래량 증가, 20일선 이탈이 이어지면 방어 강도 상향")
        _append_unique(weaken_conditions, "뉴스 반박 또는 가격·수급 회복이 확인되면 경계 강도 완화")
        _append_unique(next_checks, "기사 원문, 출처 신뢰도, 후속 보도, 가격·거래량 동조 여부 확인")
    elif action_group == "eventConfirmation":
        primary_action = "CONFIRMATION_REVIEW"
        primary_label = "우호 뉴스의 가격·수급 확인"
        _append_unique(blocked_actions, "확인 없는 추격 매수")
        _append_unique(support_signals, "직접 우호 뉴스와 가격·수급 확인 신호가 연결됨")
        _append_unique(counter_signals, "우호 뉴스도 추세 재이탈 시 무효화될 수 있음")
        _append_unique(strengthen_conditions, "거래량 증가와 20일선 회복이 유지되면 우호 강도 상향")
        _append_unique(weaken_conditions, "가격 반응이 사라지거나 부정 뉴스가 추가되면 우호 강도 완화")
        _append_unique(next_checks, "뉴스 원문, 거래량 지속성, 체결강도, 다음 지지선 유지 확인")
    elif action_group == "sectorContext":
        primary_action = "SECTOR_CONTEXT_REVIEW"
        primary_label = "섹터·피어 뉴스의 전파 가능성 확인"
        _append_unique(blocked_actions, "직접 뉴스처럼 과대 해석")
        _append_unique(counter_signals, "직접 종목 뉴스가 아니라 섹터·피어 맥락")
        _append_unique(next_checks, "피어/섹터 뉴스가 대상 종목 가격·수급에 전파되는지 확인")
    elif action_group == "cryptoSensitivity":
        primary_action = "EXPOSURE_REVIEW"
        primary_label = "비트코인 민감 비중 점검"
        _append_unique(blocked_actions, "크립토 변동 안정 전 민감 종목 비중 확대")
        _append_unique(next_checks, "BTC 변화와 보유 종목 가격 반응의 시차 확인")
    elif action_group == "distributionRisk":
        primary_action = "TRIM_REVIEW"
        primary_label = "분산매도 가능성 점검"
        _append_unique(blocked_actions, "수급 확인 없는 추가매수")
        _append_unique(risk_signals, "가격 버팀과 매도 수급이 충돌")
        _append_unique(strengthen_conditions, "거래량 증가와 20일선 이탈이 겹치면 축소 강도 상향")
        _append_unique(weaken_conditions, "외국인·기관 순매수와 20일선 회복이 확인되면 방어 강도 완화")
        _append_unique(next_checks, "거래량 증가가 매집인지 분산인지 다음 체결/호가에서 확인")
    elif action_group == "executionRisk":
        primary_action = "SPLIT_EXECUTION_REVIEW"
        primary_label = "시장가보다 분할 실행 기준 검토"
        _append_unique(blocked_actions, "유동성 확인 없는 일괄 매도/매수")
        _append_unique(risk_signals, "거래대금 대비 포지션 또는 매도 가능 수량 제약")
        _append_unique(next_checks, "거래대금, 호가잔량, 매도 가능 수량으로 분할 수량을 계산")
    elif action_group == "factorRisk":
        primary_action = "EXPOSURE_REVIEW"
        primary_label = "섹터·팩터 과밀 노출 점검"
        _append_unique(blocked_actions, "같은 팩터 종목 동시 추가매수")
        _append_unique(risk_signals, "개별 종목 리스크가 포트폴리오 팩터 리스크로 전파")
        _append_unique(next_checks, "동일 섹터/팩터 보유 종목의 동방향 신호를 함께 확인")
    elif action_group == "dataQuality":
        primary_action = "DATA_REPAIR_REVIEW"
        primary_label = "데이터 충돌 해소 전 판단 강도 제한"
        _append_unique(blocked_actions, "누락 데이터 추정 기반 매매 판단")
        _append_unique(risk_signals, "출처 품질 또는 신선도 부족")
        _append_unique(next_checks, "가격·수급·외부 피드의 출처와 최신성을 먼저 복구")
    elif action_group == "macroRegime":
        primary_action = "REGIME_EXPOSURE_REVIEW"
        primary_label = "거시 레짐 민감도 점검"
        _append_unique(blocked_actions, "레짐 악화 중 민감 팩터 비중 확대")
        _append_unique(risk_signals, "금리·크립토·통화 레짐이 민감 종목에 전파")
        _append_unique(next_checks, "금리, 환율, BTC, 지수 반응을 다음 데이터에서 함께 확인")

    for item in _active_rule_labels(matches):
        if any(token in item for token in ["손실", "리스크", "하락", "공시", "집중", "부정"]):
            _append_unique(risk_signals, item)
        elif any(token in item for token in ["지지", "회복", "수급", "매수", "우호", "동조"]):
            _append_unique(support_signals, item)

    missing_impact = []
    for item in facts.get("missingData") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("label") or item.get("key") or "").strip()
        effect = str(item.get("effect") or "").strip()
        if name:
            missing_impact.append(name + (": " + effect if effect else "는 판단 강도를 낮춥니다."))

    return {
        "engineVersion": "ontology-execution-plan-v1",
        "tboxClass": "ExecutionPlan",
        "subject": {
            "symbol": facts.get("symbol"),
            "name": facts.get("name"),
            "market": facts.get("market"),
            "source": facts.get("source"),
        },
        "decisionStage": decision.get("decisionStage"),
        "actionGroup": action_group,
        "actionLevel": action_level,
        "decisionLabel": label,
        "primaryAction": primary_action,
        "primaryActionLabel": primary_label,
        "blockedActions": blocked_actions[:5],
        "riskSignals": risk_signals[:7],
        "supportSignals": support_signals[:5],
        "counterSignals": counter_signals[:5],
        "strengthenConditions": strengthen_conditions[:5],
        "weakenConditions": weaken_conditions[:5],
        "nextChecks": next_checks[:5],
        "missingDataImpact": missing_impact[:5],
        "sourceFacts": {
            "currentPrice": facts.get("currentPrice"),
            "averagePrice": facts.get("averagePrice"),
            "profitLossRate": facts.get("profitLossRate"),
            "ma20Distance": round(float(facts.get("ma20Distance") or 0), 2),
            "ma60Distance": round(float(facts.get("ma60Distance") or 0), 2),
            "volumeRatio": facts.get("volumeRatio"),
            "tradeStrength": facts.get("tradeStrength"),
            "bidAskImbalance": facts.get("bidAskImbalance"),
            "sellableQuantity": facts.get("sellableQuantity"),
            "foreignBuyVolume": facts.get("foreignBuyVolume"),
            "foreignSellVolume": facts.get("foreignSellVolume"),
            "foreignNetVolume": facts.get("foreignNetVolume"),
            "institutionBuyVolume": facts.get("institutionBuyVolume"),
            "institutionSellVolume": facts.get("institutionSellVolume"),
            "institutionNetVolume": facts.get("institutionNetVolume"),
            "individualBuyVolume": facts.get("individualBuyVolume"),
            "individualSellVolume": facts.get("individualSellVolume"),
            "individualNetVolume": facts.get("individualNetVolume"),
            "positionToTradingValuePct": round(float(facts.get("positionToTradingValuePct") or 0), 2),
            "exitDaysAtTenPctADV": round(float(facts.get("exitDaysAtTenPctADV") or 0), 2),
            "liquidityRiskScore": round(float(facts.get("liquidityRiskScore") or 0), 1),
            "priceDeltaFromPreviousPct": round(float(facts.get("priceDeltaFromPreviousPct") or 0), 2),
            "profitLossRateDeltaPct": round(float(facts.get("profitLossRateDeltaPct") or 0), 2),
            "researchEvidenceCount": facts.get("researchEvidenceCount"),
            "directNewsCount": facts.get("directNewsCount"),
            "directRiskNewsCount": facts.get("directRiskNewsCount"),
            "directSupportNewsCount": facts.get("directSupportNewsCount"),
            "peerNewsCount": facts.get("peerNewsCount"),
            "sectorNewsCount": facts.get("sectorNewsCount"),
            "marketNewsCount": facts.get("marketNewsCount"),
            "averageNewsRelevanceScore": facts.get("averageNewsRelevanceScore"),
            "averageNewsSourceReliability": facts.get("averageNewsSourceReliability"),
            "topNewsTitles": list(facts.get("topNewsTitles") or [])[:5],
            "macroDgs10": facts.get("macroDgs10"),
            "macroDgs2": facts.get("macroDgs2"),
            "macroDff": facts.get("macroDff"),
            "macroYieldSpread10y2y": facts.get("macroYieldSpread10y2y"),
            "rateRegime": facts.get("rateRegime"),
            "yieldCurveRegime": facts.get("yieldCurveRegime"),
            "fxRatePair": facts.get("fxRatePair"),
            "fxRateToKrw": facts.get("fxRateToKrw"),
            "usdKrwRate": facts.get("usdKrwRate"),
            "fxExposureRatio": round(float(facts.get("fxExposureRatio") or 0), 2),
            "fxRegime": facts.get("fxRegime"),
        },
    }


def build_ai_prompt_context(
    prompt_id: str,
    facts: Dict[str, object],
    matches: List[OntologyRuleMatch],
    settings: Optional[Dict[str, object]] = None,
    execution_plan: Optional[Dict[str, object]] = None,
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
            "requiredBlocks": ["facts", "trendDynamics", "researchEvidence", "matchedRules", "missingData", "deliveryContext"],
            "forbidden": ["inventing_missing_market_data", "mixing_delivery_priority_with_investment_judgment"],
        },
        "outputSchema": {
            "activeInvestmentOpinion": {
                "action": "BUY|ADD|HOLD|TRIM|SELL|AVOID",
                "conviction": "number 0-100",
                "thesis": "string",
                "evidence": ["ResearchEvidence"],
                "counterEvidence": ["ResearchEvidence"],
                "invalidationCondition": "string",
                "sourceUrls": ["string"],
                "executionPlan": "ExecutionPlan",
            }
        },
        "facts": dict(facts or {}),
        "trendDynamics": dict(facts.get("trendDynamics") or {}),
        "executionPlan": dict(execution_plan or {}),
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
    previous_state: Optional[Dict[str, object]] = None,
    previous_decision: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    settings = settings or {}
    relation_definitions = relation_rule_definitions_from_settings(settings)
    thresholds = _thresholds(settings)
    facts = position_signal_facts(position, portfolio, external_signals, previous_state, previous_decision)
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
    source = str(facts.get("source") or "holding").strip()
    is_holding = bool(facts.get("isHolding"))
    volume_ratio = float(facts.get("volumeRatio") or 0)
    trade_strength = float(facts.get("tradeStrength") or 0)
    bid_ask_imbalance = float(facts.get("bidAskImbalance") or 0)
    ma20_slope = float(facts.get("ma20Slope") or 0)
    ma60_slope = float(facts.get("ma60Slope") or 0)
    price_change = float(facts.get("priceChangeRate") or 0)
    trend_curve = float(facts.get("trendCurve") or 0)
    trend_dynamic_risk = float(facts.get("trendDynamicRiskScore") or 0)
    support_retest = bool(facts.get("supportRetest"))
    recovery_attempt = bool(facts.get("recoveryAttempt"))
    breakdown_acceleration = bool(facts.get("breakdownAcceleration"))
    disclosure = facts.get("dartDisclosure")
    has_disclosure = isinstance(disclosure, dict) and bool(disclosure)
    news = facts.get("newsHeadlines")
    direct_news_count = int(number(facts.get("directNewsCount")))
    direct_risk_news_count = int(number(facts.get("directRiskNewsCount")))
    direct_support_news_count = int(number(facts.get("directSupportNewsCount")))
    peer_news_count = int(number(facts.get("peerNewsCount")))
    sector_news_count = int(number(facts.get("sectorNewsCount")))
    market_news_count = int(number(facts.get("marketNewsCount")))
    material_news_count = int(number(facts.get("materialNewsCount")))
    has_news = bool(
        (isinstance(news, dict) and bool(news.get("items") or news.get("count")))
        or material_news_count
        or direct_news_count
    )
    previous_ma20_distance = float(facts.get("previousMa20Distance") or 0)
    previous_ma60_distance = float(facts.get("previousMa60Distance") or 0)
    has_previous_state = bool(facts.get("hasPreviousState"))
    price_delta_previous = float(facts.get("priceDeltaFromPreviousPct") or 0)
    ma20_delta_previous = float(facts.get("ma20DistanceDeltaPct") or 0)
    ma60_delta_previous = float(facts.get("ma60DistanceDeltaPct") or 0)
    position_to_trading_value = float(facts.get("positionToTradingValuePct") or 0)
    liquidity_risk = float(facts.get("liquidityRiskScore") or 0)
    external_quality = float(facts.get("externalSignalQualityScore") or 0)
    external_errors = float(facts.get("externalSignalErrorCount") or 0)
    macro_spread = float(facts.get("macroYieldSpread10y2y") or 0)
    macro_dgs10 = float(facts.get("macroDgs10") or 0)

    if pnl >= 10 and (ma20_distance <= -2 or ma60_distance <= -5 or trend_score < -3):
        score = 55 + min(25, max(0, pnl - 10) * 1.2) + min(20, abs(min(ma20_distance, ma60_distance, trend_score)))
        matches.append(_match(
            "holding.profit_take.trend_weakness.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    news_relevance = float(facts.get("averageNewsRelevanceScore") or 0)
    news_reliability = float(facts.get("averageNewsSourceReliability") or 0)
    news_confidence = min(data_quality, 55 + min(40, news_reliability * 45)) if news_reliability else data_quality
    risk_news_confirmation_count = sum(
        1
        for value in [
            price_change <= -1.0,
            ma20_distance < 0,
            volume_ratio >= 1.2,
            trend_score < -4,
            flow_score <= -10 or bid_ask_imbalance <= -10,
        ]
        if value
    )
    if direct_risk_news_count and risk_news_confirmation_count >= 1:
        score = (
            54
            + min(14, direct_risk_news_count * 4)
            + min(12, news_relevance * 0.12)
            + min(12, risk_news_confirmation_count * 4)
            + (6 if price_change <= -2 else 0)
            + (5 if ma20_distance <= -5 else 0)
        )
        matches.append(_match(
            "news.direct_risk.price_confirmed.v1",
            score,
            news_confidence,
            [
                "직접 부정 뉴스 " + str(direct_risk_news_count) + "건",
                "평균 관련성 " + ("%.1f" % news_relevance) + "점",
                "최신 직접 뉴스 " + ("%.1f" % float(facts.get("latestDirectNewsAgeMinutes") or 0)) + "분 전" if facts.get("latestDirectNewsAgeMinutes") else "",
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x" if volume_ratio else "",
                moving_average_distance_text("20일선", ma20_distance),
                "뉴스 제목 " + " / ".join(str(item) for item in list(facts.get("directRiskNewsTitles") or [])[:2]),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    support_news_confirmation_count = sum(
        1
        for value in [
            price_change >= 1.0,
            volume_ratio >= 1.2,
            trade_strength >= 100,
            ma20_distance >= -2,
            flow_score >= 10 or bid_ask_imbalance >= 5,
        ]
        if value
    )
    if direct_support_news_count and support_news_confirmation_count >= 1:
        score = (
            50
            + min(14, direct_support_news_count * 4)
            + min(12, news_relevance * 0.12)
            + min(12, support_news_confirmation_count * 4)
            + (6 if price_change >= 2 else 0)
        )
        matches.append(_match(
            "news.direct_support.price_confirmed.v1",
            score,
            news_confidence,
            [
                "직접 우호 뉴스 " + str(direct_support_news_count) + "건",
                "평균 관련성 " + ("%.1f" % news_relevance) + "점",
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x" if volume_ratio else "",
                "체결강도 " + ("%.1f" % trade_strength) if trade_strength else "",
                "뉴스 제목 " + " / ".join(str(item) for item in list(facts.get("directSupportNewsTitles") or [])[:2]),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    sector_context_news_count = peer_news_count + sector_news_count + market_news_count
    sector_context_confirmed = sector_context_news_count > 0 and (
        abs(price_change) >= 1.5 or volume_ratio >= 1.2 or abs(trend_score) >= 4
    )
    if sector_context_news_count:
        score = (
            36
            + min(10, sector_context_news_count * 2.5)
            + min(10, news_relevance * 0.1)
            + (8 if sector_context_confirmed else 0)
        )
        matches.append(_match(
            "news.sector_peer_context.v1",
            score,
            news_confidence,
            [
                "피어 뉴스 " + str(peer_news_count) + "건",
                "섹터 뉴스 " + str(sector_news_count) + "건",
                "시장 뉴스 " + str(market_news_count) + "건",
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "뉴스 제목 " + " / ".join(str(item) for item in list(facts.get("sectorNewsTitles") or [])[:2]),
            ],
            missing_labels,
            reference_only=not sector_context_confirmed,
            definitions=relation_definitions,
        ))

    entry_ma20_below = float(thresholds.get("entryPullbackMa20BelowPct", -2.0) or -2.0)
    entry_ma20_deep = float(thresholds.get("entryPullbackMa20DeepPct", -8.0) or -8.0)
    entry_ma60_support = float(thresholds.get("entryMa60SupportPct", -1.0) or -1.0)
    entry_volume_min = float(thresholds.get("entryVolumeMinRatio", 0.6) or 0.0)
    entry_volume_max = float(thresholds.get("entryVolumeMaxRatio", 1.8) or 0.0)
    entry_smart_money_min = float(thresholds.get("entrySmartMoneyMin", 10.0) or 0.0)
    entry_trade_strength_min = float(thresholds.get("entryTradeStrengthMin", 100.0) or 0.0)
    entry_orderbook_min = float(thresholds.get("entryOrderbookImbalanceMin", 5.0) or 0.0)
    entry_position_max = float(thresholds.get("entryMaxPositionWeight", 20.0) or 0.0)
    entry_sector_max = float(thresholds.get("entryMaxSectorWeight", 45.0) or 0.0)
    pullback_zone = entry_ma20_deep <= ma20_distance <= entry_ma20_below
    ma60_supports_entry = bool(facts.get("ma60")) and ma60_distance >= entry_ma60_support
    volume_is_usable = bool(volume_ratio) and volume_ratio >= entry_volume_min and (not entry_volume_max or volume_ratio <= entry_volume_max)
    smart_money_supports = bool(flow_score) and flow_score >= entry_smart_money_min
    execution_supports = bool(trade_strength) and trade_strength >= entry_trade_strength_min
    orderbook_supports = bool(bid_ask_imbalance) and bid_ask_imbalance >= entry_orderbook_min
    allocation_room = (not position_weight or position_weight <= entry_position_max) and (not sector_ratio or sector_ratio <= entry_sector_max)
    entry_support_count = sum(
        1
        for value in [ma60_supports_entry, volume_is_usable, smart_money_supports, execution_supports, orderbook_supports]
        if value
    )
    facts["entryPullbackZone"] = pullback_zone
    facts["entrySupportCount"] = entry_support_count
    facts["entryAllocationRoom"] = allocation_room
    facts["entryExternalRiskBlocked"] = bool(has_disclosure or direct_risk_news_count)
    if (
        pullback_zone
        and ma60_supports_entry
        and allocation_room
        and entry_support_count >= 2
        and not has_disclosure
        and not direct_risk_news_count
        and (source == "watchlist" or pnl > -8)
    ):
        score = (
            48
            + min(14, entry_support_count * 4)
            + (8 if smart_money_supports else 0)
            + (6 if execution_supports or orderbook_supports else 0)
            + (6 if source == "watchlist" else 0)
        )
        matches.append(_match(
            "entry.pullback.supported.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x" if volume_ratio else "거래량 배율 미확인",
                "투자자 수급 점수 " + ("%.1f" % flow_score),
                "체결강도 " + ("%.1f" % trade_strength) if trade_strength else "",
                "호가 불균형 " + ("%.1f" % bid_ask_imbalance) + "%" if bid_ask_imbalance else "",
                "보유 비중 " + ("%.1f" % position_weight) + "%",
                "업종 비중 " + ("%.1f" % sector_ratio) + "%",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    add_buy_risk = (
        is_holding
        and (
            (pnl < 0 and ma20_distance <= entry_ma20_below)
            or ma60_distance < entry_ma60_support
            or has_disclosure
            or has_news and pnl < 0 and ma20_distance < 0
        )
    )
    if add_buy_risk:
        score = 52
        if pnl < 0:
            score += min(12, abs(pnl) * 1.2)
        if ma20_distance <= entry_ma20_deep:
            score += 12
        elif ma20_distance <= entry_ma20_below:
            score += 7
        if ma60_distance < entry_ma60_support:
            score += 8
        if has_disclosure:
            score += 10
        if has_news:
            score += 4
        matches.append(_match(
            "entry.add_buy.blocked.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "신규 공시 있음" if has_disclosure else "",
                "관련 뉴스 있음" if has_news else "",
                "추가매수보다 회복 조건 확인 우선",
            ],
            missing_labels,
                definitions=relation_definitions,
        ))

    averaging_support_count = sum(
        1
        for value in [
            volume_ratio >= 1.0,
            trade_strength >= 100,
            bid_ask_imbalance >= 5,
            flow_score >= 10,
            recovery_attempt,
        ]
        if value
    )
    avg_loss_threshold = float(thresholds.get("lossRateLow", -8.0) or -8.0)
    avg_loss_buffer = abs(float(thresholds.get("lossRateBufferPct", 1.0) or 0.0))
    weak_near_loss_for_averaging = (
        pnl <= avg_loss_threshold
        and avg_loss_threshold - pnl <= avg_loss_buffer
        and ma60_distance > 0
        and volume_ratio < float(thresholds.get("lossGuardVolumeConfirmRatio", 0.8) or 0.8)
        and flow_score > -15
    )
    if is_holding and pnl < 0 and ma20_distance < 0 and averaging_support_count < 2 and not weak_near_loss_for_averaging:
        score = 55 + min(18, abs(pnl) * 1.2) + min(12, abs(ma20_distance) * 1.1) + (8 if ma60_distance < 0 else 0)
        matches.append(_match(
            "averaging_down.block.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                moving_average_distance_text("20일선", ma20_distance),
                "확인 신호 " + str(averaging_support_count) + "/5",
                "추가매수보다 보유 이유 재확인 우선",
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
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "확인 신호 " + str(confirmation_count) + "/5",
                ("약한 확인 신호 감점 -" + ("%.1f" % weak_evidence_penalty) + "점") if weak_near_threshold else "",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if support_retest:
        score = (
            50
            + min(16, abs(ma20_distance) * 1.1)
            + (7 if ma60_distance >= 0 else 3)
            + (4 if price_change >= 0 else 0)
            - (8 if breakdown_acceleration else 0)
        )
        matches.append(_match(
            "trend.support_retest.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "가격 모멘텀 " + str(facts.get("priceMomentumLabel") or "-") + " (" + ("%.1f" % price_change) + "%)",
                "기울기 " + str(facts.get("trendSlopeLabel") or "-"),
                "추세 커브 " + str(facts.get("trendCurveLabel") or "-") + " (" + ("%.1f" % trend_curve) + ")",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    support_confirmation_count = sum(
        1
        for value in [
            support_retest,
            price_change >= 0,
            trade_strength >= 100,
            bid_ask_imbalance >= 5,
            flow_score >= 10,
        ]
        if value
    )
    if support_retest and support_confirmation_count >= 3 and not breakdown_acceleration:
        score = 50 + min(20, support_confirmation_count * 5) + (5 if ma60_distance >= 0 else 0)
        matches.append(_match(
            "support.retest.confirmed.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("60일선", ma60_distance),
                "확인 신호 " + str(support_confirmation_count) + "/5",
                "체결강도 " + ("%.1f" % trade_strength) if trade_strength else "",
                "호가 불균형 " + ("%.1f" % bid_ask_imbalance) + "%" if bid_ask_imbalance else "",
            ],
            missing_labels,
            reference_only=True,
            definitions=relation_definitions,
        ))
    if recovery_attempt:
        score = (
            48
            + (8 if price_change >= 1.0 else 0)
            + min(8, max(0.0, ma20_slope) * 8.0)
            + min(8, max(0.0, trend_curve) * 5.0)
            + (5 if ma60_distance >= 0 else 0)
        )
        matches.append(_match(
            "trend.recovery_attempt.v1",
            score,
            data_quality,
            [
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "20일선 기울기 " + ("%.1f" % ma20_slope) + "%",
                "60일선 기울기 " + ("%.1f" % ma60_slope) + "%",
                "추세 커브 " + ("%.1f" % trend_curve),
                moving_average_distance_text("60일선", ma60_distance),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if has_previous_state and previous_ma20_distance >= 0 and ma20_distance < 0 and (price_change <= -1.0 or volume_ratio >= 1.2 or float(facts.get("sellShare") or 0) >= 56):
        score = (
            60
            + min(16, abs(ma20_distance) * 1.5)
            + min(12, abs(min(0.0, price_change)) * 3.0)
            + (8 if volume_ratio >= 1.2 else 0)
            + (6 if float(facts.get("sellShare") or 0) >= 56 else 0)
        )
        matches.append(_match(
            "breakout.failure.v1",
            score,
            data_quality,
            [
                "이전 20일선 괴리 " + ("%.1f" % previous_ma20_distance) + "% -> 현재 " + ("%.1f" % ma20_distance) + "%",
                "가격 변화 " + ("%.1f" % price_delta_previous) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "매도 체결 비중 " + ("%.1f" % float(facts.get("sellShare") or 0)) + "%",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if has_previous_state and previous_ma60_distance >= -1.0 and ma60_distance < -1.0 and (price_change < 0 or trend_curve < 0 or ma60_delta_previous < -1.5):
        score = 56 + min(18, abs(ma60_distance) * 2.2) + min(12, abs(min(0.0, trend_curve)) * 4.0) + min(8, abs(min(0.0, ma60_delta_previous)) * 2.0)
        matches.append(_match(
            "support.retest.failed.v1",
            score,
            data_quality,
            [
                "이전 60일선 괴리 " + ("%.1f" % previous_ma60_distance) + "% -> 현재 " + ("%.1f" % ma60_distance) + "%",
                "60일선 괴리 변화 " + ("%.1f" % ma60_delta_previous) + "%p",
                "추세 커브 " + ("%.1f" % trend_curve),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if breakdown_acceleration:
        score = (
            60
            + min(20, trend_dynamic_risk * 0.35)
            + min(10, abs(min(0.0, price_change)) * 2.0)
            + min(10, abs(min(0.0, ma20_slope)) * 5.0)
            + (6 if ma60_distance < 0 else 0)
        )
        matches.append(_match(
            "trend.breakdown_acceleration.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "20일선 기울기 " + ("%.1f" % ma20_slope) + "%",
                "60일선 기울기 " + ("%.1f" % ma60_slope) + "%",
                "추세 커브 " + ("%.1f" % trend_curve),
                "추세 동역학 리스크 " + ("%.1f" % trend_dynamic_risk) + "점",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if volume_ratio >= 1.2 and price_change > -1.5 and (flow_score <= -15 or float(facts.get("sellShare") or 0) >= 56 or bid_ask_imbalance <= -10):
        score = 52 + min(14, max(0.0, volume_ratio - 1.0) * 10) + min(16, abs(min(0.0, flow_score)) * 0.35) + min(10, abs(min(0.0, bid_ask_imbalance)) * 0.35)
        matches.append(_match(
            "distribution.detected.v1",
            score,
            data_quality,
            [
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "투자자 수급 점수 " + ("%.1f" % flow_score),
                "매도 체결 비중 " + ("%.1f" % float(facts.get("sellShare") or 0)) + "%",
                "호가 불균형 " + ("%.1f" % bid_ask_imbalance) + "%",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if pnl >= 10 and (volume_ratio >= 1.5 or abs(price_change) >= 2.0) and (ma20_slope <= 0.2 or trend_curve <= -0.4 or ma20_delta_previous <= -2.0):
        score = 54 + min(18, max(0.0, pnl - 10) * 1.0) + min(12, max(0.0, volume_ratio - 1.0) * 8.0) + min(12, abs(min(0.0, trend_curve)) * 5.0)
        matches.append(_match(
            "profit.protection.volatility.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "20일선 기울기 " + ("%.1f" % ma20_slope) + "%",
                "추세 커브 " + ("%.1f" % trend_curve),
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
    if is_holding and (liquidity_risk >= 45 or position_to_trading_value >= 5 or facts.get("sellableBlocked")):
        score = 48 + min(25, liquidity_risk * 0.35) + min(18, position_to_trading_value * 1.2) + (8 if facts.get("sellableBlocked") else 0)
        matches.append(_match(
            "liquidity.exit_capacity.v1",
            score,
            data_quality,
            [
                "포지션/거래대금 " + ("%.1f" % position_to_trading_value) + "%",
                "10% 거래대금 기준 청산 일수 " + ("%.1f" % float(facts.get("exitDaysAtTenPctADV") or 0)),
                "유동성 리스크 " + ("%.1f" % liquidity_risk) + "점",
                "매도 가능 수량 제한" if facts.get("sellableBlocked") else "",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if sector_ratio >= 45 and (position_weight >= 10 or trend_score < -4 or pnl < 0):
        score = 48 + min(26, max(0.0, sector_ratio - 35) * 0.8) + min(16, max(0.0, position_weight - 8) * 0.9) + (8 if trend_score < -4 else 0)
        matches.append(_match(
            "factor.crowding.v1",
            score,
            data_quality,
            [
                "업종 비중 " + ("%.1f" % sector_ratio) + "%",
                "종목 비중 " + ("%.1f" % position_weight) + "%",
                "시장/통화 " + str(facts.get("market") or "-") + "/" + str(facts.get("currency") or "-"),
                "추세 점수 " + ("%.1f" % trend_score),
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
    data_conflict_active = (
        (external_quality and external_quality < 60)
        or external_errors >= 2
        or str(facts.get("externalSignalFreshnessStatus") or "") == "stale"
    )
    if data_conflict_active:
        score = 45 + min(22, max(0.0, 60 - external_quality) * 0.5 if external_quality else 8) + min(12, external_errors * 4) + min(12, len(missing_labels) * 3)
        matches.append(_match(
            "data.conflict.v1",
            score,
            data_quality,
            [
                "외부 신호 품질 " + ("%.1f" % external_quality) if external_quality else "외부 신호 품질 미확인",
                "외부 신호 오류 " + str(int(external_errors)) + "건",
                "신선도 " + str(facts.get("externalSignalFreshnessStatus") or "-"),
                "부족 데이터 " + ", ".join(missing_labels[:4]) if missing_labels else "",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    macro_sensitive = any(token in str(facts.get("sector") or "") for token in ["반도체", "AI", "플랫폼", "디지털자산"]) or str(facts.get("currency") or "").upper() == "USD" or facts.get("isBtcSensitive")
    macro_risk_active = macro_sensitive and (
        (macro_dgs10 and macro_dgs10 >= 4.5)
        or macro_spread < 0
        or abs(btc_change24h) >= float(thresholds.get("externalBitcoinChange24hPct", 3.0) or 3.0)
    )
    if macro_risk_active:
        score = 50 + (8 if macro_dgs10 >= 4.5 else 0) + (8 if macro_spread < 0 else 0) + min(18, abs(btc_change24h) * 2.0)
        matches.append(_match(
            "macro.regime.shift.v1",
            score,
            data_quality,
            [
                "10년 금리 " + ("%.2f" % macro_dgs10) if macro_dgs10 else "",
                "10Y-2Y 스프레드 " + ("%.2f" % macro_spread) if macro_spread else "",
                "BTC 24h " + ("%.1f" % btc_change24h) + "%" if btc_change24h else "",
                "민감 섹터/통화 " + str(facts.get("sector") or "-") + "/" + str(facts.get("currency") or "-"),
            ],
            missing_labels,
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
    execution_plan = execution_plan_from_relation_context(facts, decision, matches)
    prompt_context = build_ai_prompt_context(prompt_id, facts, matches, settings, execution_plan)
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
        "executionPlan": execution_plan,
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
