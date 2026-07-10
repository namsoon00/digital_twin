from dataclasses import asdict, dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class TBoxBoundedContext:
    key: str
    label: str
    description: str


@dataclass(frozen=True)
class TBoxClassDef:
    name: str
    bounded_context: str
    label: str = ""
    parent: str = ""
    description: str = ""


@dataclass(frozen=True)
class TBoxRelationDef:
    name: str
    bounded_context: str
    source_context: str = ""
    target_context: str = ""
    description: str = ""


@dataclass(frozen=True)
class TBoxRuleDef:
    text: str
    bounded_context: str
    description: str = ""


BOUNDED_CONTEXTS: List[TBoxBoundedContext] = [
    TBoxBoundedContext(
        "investment-core",
        "투자 핵심",
        "계좌, 포트폴리오, 투자 대상, 보유/관심 상태, 현금과 노출을 정의합니다.",
    ),
    TBoxBoundedContext(
        "observation-data",
        "관측 데이터",
        "가격, 수급, 기술 지표, 외부 이벤트, 데이터 출처와 신선도를 정의합니다.",
    ),
    TBoxBoundedContext(
        "strategy-thesis",
        "전략 가설",
        "투자 가설, 진입/청산/리밸런싱/비중 규칙과 기존 모델 점수의 역할을 정의합니다.",
    ),
    TBoxBoundedContext(
        "risk-exposure",
        "리스크 노출",
        "시장, 유동성, 집중, 통화, 이벤트, 데이터 품질, 모델, 실행 리스크를 분류합니다.",
    ),
    TBoxBoundedContext(
        "reasoning-insight",
        "추론 인사이트",
        "신호, 근거, 판단 근거, 의견, 모순, 기회, 인사이트와 AI 리뷰를 정의합니다.",
    ),
    TBoxBoundedContext(
        "operations-dispatch",
        "운영과 알림",
        "데이터 수집, 분석 실행, 추론 주기, 알림 디스패치와 억제 정책을 정의합니다.",
    ),
]


CLASS_DEFS: List[TBoxClassDef] = [
    TBoxClassDef("Portfolio", "investment-core", "포트폴리오", description="투자 계좌의 전체 자산과 노출 집합입니다."),
    TBoxClassDef("Account", "investment-core", "계좌", description="데이터와 주문 권한이 연결되는 투자 계좌입니다."),
    TBoxClassDef("Instrument", "investment-core", "투자 대상", description="주식, ETF, 크립토, 현금성 자산 등 투자 가능한 대상입니다."),
    TBoxClassDef("Company", "investment-core", "회사", description="증권을 발행하고 뉴스·공시·실세계 노출의 주체가 되는 법인입니다."),
    TBoxClassDef("Security", "investment-core", "증권", parent="Instrument", description="회사가 발행한 거래 가능 증권입니다."),
    TBoxClassDef("Equity", "investment-core", "주식", parent="Instrument"),
    TBoxClassDef("ETF", "investment-core", "ETF", parent="Instrument"),
    TBoxClassDef("CryptoAsset", "investment-core", "크립토 자산", parent="Instrument"),
    TBoxClassDef("CashAsset", "investment-core", "현금성 자산", parent="Instrument"),
    TBoxClassDef("Derivative", "investment-core", "파생상품", parent="Instrument"),
    TBoxClassDef("Index", "investment-core", "지수", parent="Instrument"),
    TBoxClassDef("FXPair", "investment-core", "환율쌍", parent="Instrument"),
    TBoxClassDef("Stock", "investment-core", "종목", parent="Equity"),
    TBoxClassDef("Position", "investment-core", "포지션", description="보유 또는 관찰 중인 투자 대상의 계좌상 행입니다."),
    TBoxClassDef("Watchlist", "investment-core", "관심 목록"),
    TBoxClassDef("WatchlistCandidate", "investment-core", "관심 후보", parent="Position"),
    TBoxClassDef("Sector", "investment-core", "섹터"),
    TBoxClassDef("Industry", "investment-core", "산업"),
    TBoxClassDef("PeerGroup", "investment-core", "피어 그룹"),
    TBoxClassDef("Market", "investment-core", "시장"),
    TBoxClassDef("Currency", "investment-core", "통화"),
    TBoxClassDef("Cash", "investment-core", "대기 현금", parent="CashAsset"),
    TBoxClassDef("MarketExposure", "investment-core", "시장 노출"),
    TBoxClassDef("Observation", "observation-data", "관측값", description="특정 시점과 출처에서 들어온 사실입니다."),
    TBoxClassDef("FactChange", "observation-data", "사실 변경", parent="Observation"),
    TBoxClassDef("PriceObservation", "observation-data", "가격 관측", parent="Observation"),
    TBoxClassDef("VolumeObservation", "observation-data", "거래량 관측", parent="Observation"),
    TBoxClassDef("TechnicalObservation", "observation-data", "기술 지표 관측", parent="Observation"),
    TBoxClassDef("FlowObservation", "observation-data", "수급 관측", parent="Observation"),
    TBoxClassDef("FundamentalObservation", "observation-data", "펀더멘털 관측", parent="Observation"),
    TBoxClassDef("ExternalObservation", "observation-data", "외부 관측", parent="Observation"),
    TBoxClassDef("PriceMetric", "observation-data", "가격 지표", parent="PriceObservation"),
    TBoxClassDef("TechnicalIndicator", "observation-data", "기술 지표", parent="TechnicalObservation"),
    TBoxClassDef("TradeFlow", "observation-data", "거래/수급", parent="FlowObservation"),
    TBoxClassDef("DataQuality", "observation-data", "데이터 품질", parent="Observation"),
    TBoxClassDef("DataSource", "observation-data", "데이터 출처"),
    TBoxClassDef("DataFreshness", "observation-data", "데이터 신선도"),
    TBoxClassDef("MissingData", "observation-data", "부족 데이터"),
    TBoxClassDef("Provenance", "observation-data", "출처 이력"),
    TBoxClassDef("SourceReliability", "observation-data", "출처 신뢰도", parent="Provenance"),
    TBoxClassDef("ObservationConfidence", "observation-data", "관측 신뢰도", parent="DataQuality"),
    TBoxClassDef("TimeWindow", "observation-data", "시간 구간"),
    TBoxClassDef("ObservationTime", "observation-data", "관측 시점"),
    TBoxClassDef("SignalHorizon", "observation-data", "신호 기간"),
    TBoxClassDef("HoldingPeriod", "observation-data", "보유 기간"),
    TBoxClassDef("ValidityInterval", "observation-data", "유효 기간"),
    TBoxClassDef("Staleness", "observation-data", "노후화"),
    TBoxClassDef("ValidationRule", "observation-data", "검증 규칙"),
    TBoxClassDef("PriceBar", "observation-data", "가격 봉", parent="PriceObservation"),
    TBoxClassDef("PricePath", "observation-data", "가격 경로", parent="PriceObservation"),
    TBoxClassDef("VolumeProfile", "observation-data", "거래량 프로파일", parent="VolumeObservation"),
    TBoxClassDef("KeyLevel", "observation-data", "주요 가격대", parent="TechnicalObservation"),
    TBoxClassDef("SupportLevel", "observation-data", "지지선", parent="KeyLevel"),
    TBoxClassDef("ResistanceLevel", "observation-data", "저항선", parent="KeyLevel"),
    TBoxClassDef("Breakout", "observation-data", "돌파", parent="TechnicalObservation"),
    TBoxClassDef("FailedBreakout", "observation-data", "돌파 실패", parent="Breakout"),
    TBoxClassDef("Retest", "observation-data", "재시험", parent="TechnicalObservation"),
    TBoxClassDef("ResearchEvidence", "observation-data", "리서치 근거", parent="ExternalObservation"),
    TBoxClassDef("ExternalSignal", "observation-data", "외부 신호", parent="ExternalObservation"),
    TBoxClassDef("NewsEvent", "observation-data", "뉴스 이벤트", parent="ExternalSignal"),
    TBoxClassDef("NewsArticle", "observation-data", "뉴스 기사", parent="NewsEvent"),
    TBoxClassDef("NewsTopic", "observation-data", "뉴스 토픽", parent="NewsEvent"),
    TBoxClassDef("NewsEventType", "observation-data", "뉴스 이벤트 유형", parent="NewsEvent"),
    TBoxClassDef("PeerCompanyMention", "observation-data", "피어 회사 언급", parent="ExternalObservation"),
    TBoxClassDef("DisclosureEvent", "observation-data", "공시 이벤트", parent="ExternalSignal"),
    TBoxClassDef("DisclosureFiling", "observation-data", "공시 문서", parent="DisclosureEvent"),
    TBoxClassDef("MacroIndicator", "observation-data", "거시 지표", parent="ExternalSignal"),
    TBoxClassDef("MacroPrint", "observation-data", "거시 발표값", parent="MacroIndicator"),
    TBoxClassDef("RateSignal", "observation-data", "금리 신호", parent="ExternalSignal"),
    TBoxClassDef("InterestRate", "observation-data", "금리", parent="RateSignal"),
    TBoxClassDef("YieldCurve", "observation-data", "수익률곡선", parent="RateSignal"),
    TBoxClassDef("FXRateSignal", "observation-data", "환율 신호", parent="ExternalSignal"),
    TBoxClassDef("CreditSpreadSignal", "observation-data", "크레딧 스프레드 신호", parent="ExternalSignal"),
    TBoxClassDef("CryptoMarketSignal", "observation-data", "크립토 시장 신호", parent="ExternalSignal"),
    TBoxClassDef("EarningsEvent", "observation-data", "실적 이벤트", parent="ExternalSignal"),
    TBoxClassDef("EarningsCalendarEvent", "observation-data", "실적 일정 이벤트", parent="EarningsEvent"),
    TBoxClassDef("AnalystRevision", "observation-data", "애널리스트 추정 변경", parent="ExternalSignal"),
    TBoxClassDef("CorporateAction", "observation-data", "기업 액션", parent="ExternalSignal"),
    TBoxClassDef("RegulatoryEvent", "observation-data", "규제 이벤트", parent="ExternalSignal"),
    TBoxClassDef("ProductLine", "observation-data", "제품/서비스 라인", parent="ExternalObservation"),
    TBoxClassDef("SupplyChainExposure", "observation-data", "공급망 노출", parent="ExternalObservation"),
    TBoxClassDef("CustomerExposure", "observation-data", "고객 노출", parent="ExternalObservation"),
    TBoxClassDef("RevenueExposure", "observation-data", "매출 노출", parent="ExternalObservation"),
    TBoxClassDef("Factor", "strategy-thesis", "투자 팩터"),
    TBoxClassDef("FactorExposure", "strategy-thesis", "팩터 노출"),
    TBoxClassDef("BenchmarkIndex", "strategy-thesis", "벤치마크 지수"),
    TBoxClassDef("HedgeCandidate", "strategy-thesis", "헤지 후보"),
    TBoxClassDef("Strategy", "strategy-thesis", "투자전략"),
    TBoxClassDef("InvestmentThesis", "strategy-thesis", "투자 가설"),
    TBoxClassDef("EntryCondition", "strategy-thesis", "진입 조건"),
    TBoxClassDef("ExitCondition", "strategy-thesis", "청산 조건"),
    TBoxClassDef("RiskManagementRule", "strategy-thesis", "위험 관리 규칙"),
    TBoxClassDef("RebalancingRule", "strategy-thesis", "리밸런싱 규칙"),
    TBoxClassDef("PositionSizingRule", "strategy-thesis", "비중 규칙"),
    TBoxClassDef("Scenario", "strategy-thesis", "시나리오"),
    TBoxClassDef("StrategyExperiment", "strategy-thesis", "전략 실험"),
    TBoxClassDef("StrategySignal", "strategy-thesis", "전략 신호"),
    TBoxClassDef("ModelScore", "strategy-thesis", "모델 점수", parent="StrategySignal"),
    TBoxClassDef("Threshold", "strategy-thesis", "기준값"),
    TBoxClassDef("RuntimeSetting", "strategy-thesis", "런타임 설정"),
    TBoxClassDef("AlertRule", "strategy-thesis", "알림 규칙", parent="RuntimeSetting"),
    TBoxClassDef("PromptTemplate", "strategy-thesis", "프롬프트 템플릿", parent="RuntimeSetting"),
    TBoxClassDef("ValuationAssumption", "strategy-thesis", "밸류에이션 가정"),
    TBoxClassDef("LegacyScoreModel", "strategy-thesis", "관계 규칙 점수 모델"),
    TBoxClassDef("Risk", "risk-exposure", "리스크"),
    TBoxClassDef("MarketRisk", "risk-exposure", "시장 리스크", parent="Risk"),
    TBoxClassDef("LiquidityRisk", "risk-exposure", "유동성 리스크", parent="Risk"),
    TBoxClassDef("LiquidityProfile", "risk-exposure", "유동성 프로파일", parent="LiquidityRisk"),
    TBoxClassDef("ExitCapacity", "risk-exposure", "청산 가능 용량", parent="LiquidityRisk"),
    TBoxClassDef("SlippageEstimate", "risk-exposure", "슬리피지 추정", parent="ExecutionRisk"),
    TBoxClassDef("ConcentrationRisk", "risk-exposure", "집중 리스크", parent="Risk"),
    TBoxClassDef("CurrencyRisk", "risk-exposure", "통화 리스크", parent="Risk"),
    TBoxClassDef("VolatilityRisk", "risk-exposure", "변동성 리스크", parent="Risk"),
    TBoxClassDef("EventRisk", "risk-exposure", "이벤트 리스크", parent="Risk"),
    TBoxClassDef("DataQualityRisk", "risk-exposure", "데이터 품질 리스크", parent="Risk"),
    TBoxClassDef("ModelRisk", "risk-exposure", "모델 리스크", parent="Risk"),
    TBoxClassDef("ExecutionRisk", "risk-exposure", "실행 리스크", parent="Risk"),
    TBoxClassDef("CorrelationRisk", "risk-exposure", "상관 리스크", parent="Risk"),
    TBoxClassDef("RegimeRisk", "risk-exposure", "레짐 리스크", parent="Risk"),
    TBoxClassDef("Signal", "reasoning-insight", "신호"),
    TBoxClassDef("PriceSignal", "reasoning-insight", "가격 신호", parent="Signal"),
    TBoxClassDef("TrendSignal", "reasoning-insight", "추세 신호", parent="Signal"),
    TBoxClassDef("FlowSignal", "reasoning-insight", "수급 신호", parent="Signal"),
    TBoxClassDef("ValuationSignal", "reasoning-insight", "밸류에이션 신호", parent="Signal"),
    TBoxClassDef("MacroSignal", "reasoning-insight", "거시 신호", parent="Signal"),
    TBoxClassDef("DisclosureSignal", "reasoning-insight", "공시 신호", parent="Signal"),
    TBoxClassDef("CryptoSignal", "reasoning-insight", "크립토 신호", parent="Signal"),
    TBoxClassDef("DataQualitySignal", "reasoning-insight", "데이터 품질 신호", parent="Signal"),
    TBoxClassDef("Evidence", "reasoning-insight", "근거"),
    TBoxClassDef("Belief", "reasoning-insight", "판단 근거"),
    TBoxClassDef("RelationStateSnapshot", "reasoning-insight", "관계 상태 스냅샷"),
    TBoxClassDef("SignalTransition", "reasoning-insight", "신호 상태 전이"),
    TBoxClassDef("TrendPhase", "reasoning-insight", "추세 국면", parent="Signal"),
    TBoxClassDef("TrendTransition", "reasoning-insight", "추세 국면 전이", parent="SignalTransition"),
    TBoxClassDef("ReversalSignal", "reasoning-insight", "반전 신호", parent="TrendTransition"),
    TBoxClassDef("ConsolidationBreak", "reasoning-insight", "횡보 이탈", parent="TrendTransition"),
    TBoxClassDef("AccelerationSignal", "reasoning-insight", "가속 신호", parent="TrendTransition"),
    TBoxClassDef("DecelerationSignal", "reasoning-insight", "둔화 신호", parent="TrendTransition"),
    TBoxClassDef("MaterialityAssessment", "reasoning-insight", "중요 변경 평가"),
    TBoxClassDef("ThresholdCrossing", "reasoning-insight", "기준선 통과"),
    TBoxClassDef("ExposureAssessment", "reasoning-insight", "노출 영향 평가"),
    TBoxClassDef("ConfidenceAssessment", "reasoning-insight", "신뢰도 평가"),
    TBoxClassDef("ActionabilityAssessment", "reasoning-insight", "행동 가능성 평가"),
    TBoxClassDef("PreviousInsight", "reasoning-insight", "이전 인사이트", parent="Insight"),
    TBoxClassDef("Opinion", "reasoning-insight", "AI 의견"),
    TBoxClassDef("ActiveInvestmentOpinion", "reasoning-insight", "실행 투자 의견", parent="Opinion"),
    TBoxClassDef("ExecutionPlan", "reasoning-insight", "실행 계획"),
    TBoxClassDef("ActionCandidate", "reasoning-insight", "행동 후보"),
    TBoxClassDef("BlockedAction", "reasoning-insight", "보류 행동", parent="ActionCandidate"),
    TBoxClassDef("InvalidationCondition", "reasoning-insight", "무효화 조건"),
    TBoxClassDef("NextCheck", "reasoning-insight", "다음 확인"),
    TBoxClassDef("AIValidation", "reasoning-insight", "AI 판단 검증"),
    TBoxClassDef("ValidatedOpinion", "reasoning-insight", "AI 최종 의견", parent="Opinion"),
    TBoxClassDef("AIJudgmentAudit", "reasoning-insight", "AI 판단 감사 로그"),
    TBoxClassDef("AIContextPacket", "reasoning-insight", "AI 컨텍스트 패킷", parent="AIJudgmentAudit"),
    TBoxClassDef("EvidenceSubgraph", "reasoning-insight", "근거 서브그래프", parent="InferencePath"),
    TBoxClassDef("GraphNeighborhood", "reasoning-insight", "그래프 이웃 관계", parent="EvidenceSubgraph"),
    TBoxClassDef("Opportunity", "reasoning-insight", "기회"),
    TBoxClassDef("Contradiction", "reasoning-insight", "모순"),
    TBoxClassDef("Insight", "reasoning-insight", "인사이트"),
    TBoxClassDef("InsightType", "reasoning-insight", "인사이트 타입"),
    TBoxClassDef("InsightPolicy", "reasoning-insight", "인사이트 정책"),
    TBoxClassDef("MessageDeliveryProfile", "reasoning-insight", "메시지 전달 수준"),
    TBoxClassDef("ReasoningRule", "reasoning-insight", "추론 규칙"),
    TBoxClassDef("RuleRegistry", "reasoning-insight", "규칙 레지스트리"),
    TBoxClassDef("RelationRuleRegistry", "reasoning-insight", "투자 관계 규칙 레지스트리", parent="RuleRegistry"),
    TBoxClassDef("GraphInferenceRule", "reasoning-insight", "그래프 추론 규칙", parent="ReasoningRule"),
    TBoxClassDef("RelationReasoningRule", "reasoning-insight", "투자 관계 추론 규칙", parent="ReasoningRule"),
    TBoxClassDef("RuleCondition", "reasoning-insight", "규칙 조건", parent="ReasoningRule"),
    TBoxClassDef("RelationRuleCondition", "reasoning-insight", "투자 관계 규칙 조건", parent="RuleCondition"),
    TBoxClassDef("RelationTemplate", "reasoning-insight", "파생 관계 템플릿", parent="ReasoningRule"),
    TBoxClassDef("RelationRuleTemplate", "reasoning-insight", "투자 관계 파생 템플릿", parent="RelationTemplate"),
    TBoxClassDef("RuleDecisionPolicy", "reasoning-insight", "규칙 판단 단계 정책", parent="RelationTemplate"),
    TBoxClassDef("RulePriorityPolicy", "reasoning-insight", "규칙 우선순위 정책", parent="RelationTemplate"),
    TBoxClassDef("RuleBoxGovernance", "reasoning-insight", "RuleBox 운영 거버넌스"),
    TBoxClassDef("RuleBoxVersion", "reasoning-insight", "RuleBox 저장 버전", parent="RuleBoxGovernance"),
    TBoxClassDef("RuleChangeCandidate", "reasoning-insight", "규칙 변경 후보", parent="RuleBoxGovernance"),
    TBoxClassDef("DerivedAssertion", "reasoning-insight", "파생 어설션"),
    TBoxClassDef("InferenceTrace", "reasoning-insight", "추론 경로"),
    TBoxClassDef("InferencePath", "reasoning-insight", "추론 패스", parent="InferenceTrace"),
    TBoxClassDef("GraphReasoner", "reasoning-insight", "그래프 추론기"),
    TBoxClassDef("Neo4jNativeReasoner", "reasoning-insight", "Neo4j 네이티브 추론기", parent="GraphReasoner"),
    TBoxClassDef("ReasoningCard", "reasoning-insight", "추론 카드"),
    TBoxClassDef("ConfidenceScore", "reasoning-insight", "확신도"),
    TBoxClassDef("ReliabilityScore", "reasoning-insight", "신뢰도"),
    TBoxClassDef("AIReview", "reasoning-insight", "AI 리뷰"),
    TBoxClassDef("DataPipeline", "operations-dispatch", "데이터 파이프라인"),
    TBoxClassDef("CollectionSchedule", "operations-dispatch", "수집 주기"),
    TBoxClassDef("CollectionPolicy", "operations-dispatch", "수집 정책"),
    TBoxClassDef("MarketSnapshot", "operations-dispatch", "시장 스냅샷", parent="DataPipeline"),
    TBoxClassDef("WatchlistSnapshot", "operations-dispatch", "관심 스냅샷", parent="DataPipeline"),
    TBoxClassDef("ExternalSignalCollection", "operations-dispatch", "외부 신호 수집", parent="DataPipeline"),
    TBoxClassDef("AnalysisJob", "operations-dispatch", "분석 작업"),
    TBoxClassDef("ReasoningCycle", "operations-dispatch", "추론 주기"),
    TBoxClassDef("NotificationPolicy", "operations-dispatch", "알림 정책"),
    TBoxClassDef("NotificationDispatch", "operations-dispatch", "알림 디스패치"),
    TBoxClassDef("NotificationIntent", "operations-dispatch", "알림 의도"),
    TBoxClassDef("AlertCandidate", "operations-dispatch", "알림 후보", parent="NotificationIntent"),
    TBoxClassDef("ImportanceGate", "operations-dispatch", "중요도 게이트", parent="NotificationPolicy"),
    TBoxClassDef("CooldownPolicy", "operations-dispatch", "쿨다운 정책", parent="NotificationPolicy"),
    TBoxClassDef("NoveltyPolicy", "operations-dispatch", "신규성 정책", parent="NotificationPolicy"),
    TBoxClassDef("SuppressionPolicy", "operations-dispatch", "억제 정책", parent="NotificationPolicy"),
    TBoxClassDef("MarketSession", "operations-dispatch", "시장 세션"),
    TBoxClassDef("OperationalEvent", "operations-dispatch", "운영 이벤트"),
]


RELATION_DEFS: List[TBoxRelationDef] = [
    TBoxRelationDef("DEFINES_BOUNDED_CONTEXT", "operations-dispatch", "operations-dispatch", "operations-dispatch"),
    TBoxRelationDef("DEFINES_CLASS", "operations-dispatch", "operations-dispatch", "operations-dispatch"),
    TBoxRelationDef("DEFINES_RELATION", "operations-dispatch", "operations-dispatch", "operations-dispatch"),
    TBoxRelationDef("CONSTRAINS_ASSERTIONS", "operations-dispatch", "operations-dispatch", "operations-dispatch"),
    TBoxRelationDef("CONSTRAINS_RULES", "reasoning-insight", "operations-dispatch", "reasoning-insight"),
    TBoxRelationDef("DERIVES_ASSERTIONS", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("IS_A", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("HOLDS", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("WATCHES", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("HOLDS_CASH", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("MANAGES_PORTFOLIO", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("HAS_POSITION", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("HAS_WATCHLIST", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("ISSUES", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("COMPETES_WITH", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("SUPPLIES_TO", "observation-data", "investment-core", "investment-core"),
    TBoxRelationDef("SELLS_TO", "observation-data", "investment-core", "investment-core"),
    TBoxRelationDef("HAS_PRODUCT_EXPOSURE", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("HAS_SUPPLY_CHAIN_EXPOSURE", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("HAS_CUSTOMER_EXPOSURE", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("HAS_REVENUE_EXPOSURE", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("REPRESENTS_STOCK", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("REPRESENTS_INSTRUMENT", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("HAS_MARKET_EXPOSURE", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("BELONGS_TO", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("TRADED_IN", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("DENOMINATED_IN", "investment-core", "investment-core", "investment-core"),
    TBoxRelationDef("EXPOSED_TO", "risk-exposure", "investment-core", "risk-exposure"),
    TBoxRelationDef("HAS_FX_EXPOSURE", "risk-exposure", "investment-core", "risk-exposure"),
    TBoxRelationDef("HAS_RATE_SENSITIVITY", "risk-exposure", "investment-core", "risk-exposure"),
    TBoxRelationDef("HAS_OBSERVATION", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("OBSERVED_FROM", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("HAS_PRICE", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("HAS_PRICE_PATH", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("HAS_TECHNICAL_INDICATOR", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("HAS_TRADE_FLOW", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("HAS_DATA_QUALITY", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("HAS_EXTERNAL_SIGNAL", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("HAS_PROVENANCE", "observation-data", "observation-data", "observation-data"),
    TBoxRelationDef("HAS_SOURCE_RELIABILITY", "observation-data", "observation-data", "observation-data"),
    TBoxRelationDef("WEIGHTED_BY_CONFIDENCE", "observation-data", "observation-data", "reasoning-insight"),
    TBoxRelationDef("STALE_AFTER", "observation-data", "observation-data", "observation-data"),
    TBoxRelationDef("CONFLICTS_WITH_SOURCE", "observation-data", "observation-data", "observation-data"),
    TBoxRelationDef("PRODUCES_OBSERVATION", "observation-data", "operations-dispatch", "observation-data"),
    TBoxRelationDef("MEASURED_AT", "observation-data", "observation-data", "observation-data"),
    TBoxRelationDef("VALID_DURING", "observation-data", "observation-data", "observation-data"),
    TBoxRelationDef("PRECEDES", "observation-data", "observation-data", "observation-data"),
    TBoxRelationDef("CHANGED_FROM", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("CONFIRMED_OVER", "reasoning-insight", "observation-data", "reasoning-insight"),
    TBoxRelationDef("FAILED_AFTER", "reasoning-insight", "observation-data", "reasoning-insight"),
    TBoxRelationDef("HAS_TREND_PHASE", "reasoning-insight", "observation-data", "reasoning-insight"),
    TBoxRelationDef("HAS_TREND_TRANSITION", "reasoning-insight", "investment-core", "reasoning-insight"),
    TBoxRelationDef("INDICATES_REVERSAL", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("BREAKS_CONSOLIDATION", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("INDICATES_ACCELERATION", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("INDICATES_DECELERATION", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("RETESTS_LEVEL", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("BREAKS_LEVEL", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("RECLAIMS_LEVEL", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("MENTIONS_INSTRUMENT", "observation-data", "observation-data", "investment-core"),
    TBoxRelationDef("MENTIONS_PEER", "observation-data", "observation-data", "investment-core"),
    TBoxRelationDef("HAS_TOPIC", "observation-data", "observation-data", "observation-data"),
    TBoxRelationDef("HAS_EVENT_TYPE", "observation-data", "observation-data", "observation-data"),
    TBoxRelationDef("AFFECTS_SECTOR", "observation-data", "observation-data", "investment-core"),
    TBoxRelationDef("MATERIAL_TO", "observation-data", "observation-data", "strategy-thesis"),
    TBoxRelationDef("DECAYS_AFTER", "observation-data", "observation-data", "observation-data"),
    TBoxRelationDef("HAS_TIME_HORIZON", "observation-data", "investment-core", "observation-data"),
    TBoxRelationDef("APPLIES_TO_HORIZON", "observation-data", "strategy-thesis", "observation-data"),
    TBoxRelationDef("HAS_VALUATION", "strategy-thesis", "investment-core", "strategy-thesis"),
    TBoxRelationDef("HAS_MODEL_SCORE", "strategy-thesis", "investment-core", "strategy-thesis"),
    TBoxRelationDef("HAS_THRESHOLD", "strategy-thesis", "strategy-thesis", "strategy-thesis"),
    TBoxRelationDef("HAS_RUNTIME_SETTING", "strategy-thesis", "investment-core", "strategy-thesis"),
    TBoxRelationDef("HAS_ALERT_RULE", "strategy-thesis", "strategy-thesis", "strategy-thesis"),
    TBoxRelationDef("HAS_PROMPT_TEMPLATE", "strategy-thesis", "strategy-thesis", "strategy-thesis"),
    TBoxRelationDef("USES_STRATEGY", "strategy-thesis", "investment-core", "strategy-thesis"),
    TBoxRelationDef("BASED_ON_THESIS", "strategy-thesis", "investment-core", "strategy-thesis"),
    TBoxRelationDef("HAS_ENTRY_CONDITION", "strategy-thesis", "strategy-thesis", "strategy-thesis"),
    TBoxRelationDef("HAS_EXIT_CONDITION", "strategy-thesis", "strategy-thesis", "strategy-thesis"),
    TBoxRelationDef("HAS_RISK_MANAGEMENT_RULE", "strategy-thesis", "strategy-thesis", "strategy-thesis"),
    TBoxRelationDef("HAS_POSITION_SIZING_RULE", "strategy-thesis", "strategy-thesis", "strategy-thesis"),
    TBoxRelationDef("HAS_REBALANCING_RULE", "strategy-thesis", "strategy-thesis", "strategy-thesis"),
    TBoxRelationDef("HAS_FACTOR_EXPOSURE", "strategy-thesis", "investment-core", "strategy-thesis"),
    TBoxRelationDef("HAS_BETA_TO", "strategy-thesis", "investment-core", "strategy-thesis"),
    TBoxRelationDef("CORRELATED_WITH", "risk-exposure", "investment-core", "investment-core"),
    TBoxRelationDef("OFFSETS_RISK", "risk-exposure", "strategy-thesis", "risk-exposure"),
    TBoxRelationDef("SUPPORTS_THESIS", "strategy-thesis", "reasoning-insight", "strategy-thesis"),
    TBoxRelationDef("WEAKENS_THESIS", "strategy-thesis", "risk-exposure", "strategy-thesis"),
    TBoxRelationDef("INVALIDATES_THESIS", "strategy-thesis", "reasoning-insight", "strategy-thesis"),
    TBoxRelationDef("TRIGGERS_ENTRY", "strategy-thesis", "reasoning-insight", "strategy-thesis"),
    TBoxRelationDef("TRIGGERS_EXIT", "strategy-thesis", "risk-exposure", "strategy-thesis"),
    TBoxRelationDef("REQUIRES_CONFIRMATION", "strategy-thesis", "strategy-thesis", "observation-data"),
    TBoxRelationDef("CONFIGURES", "strategy-thesis", "investment-core", "strategy-thesis"),
    TBoxRelationDef("DERIVES_SIGNAL", "reasoning-insight", "observation-data", "reasoning-insight"),
    TBoxRelationDef("DEFINES_RULE", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("HAS_CONDITION", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("DERIVES_RELATION", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("HAS_RULEBOX_VERSION", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("HAS_RULE_CHANGE_CANDIDATE", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("TRIGGERED_INFERENCE", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("EXECUTES_RULEBOX", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("MATERIALIZES_INFERENCE", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("HAS_INFERENCE_TRACE", "reasoning-insight", "investment-core", "reasoning-insight"),
    TBoxRelationDef("EXPLAINED_BY_TRACE", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("HAS_INFERRED_RISK", "risk-exposure", "investment-core", "risk-exposure"),
    TBoxRelationDef("HAS_INFERRED_SUPPORT", "strategy-thesis", "investment-core", "reasoning-insight"),
    TBoxRelationDef("HAS_INFERRED_ENTRY_OPPORTUNITY", "strategy-thesis", "investment-core", "reasoning-insight"),
    TBoxRelationDef("HAS_ACTION_CANDIDATE", "reasoning-insight", "investment-core", "reasoning-insight"),
    TBoxRelationDef("USED_AS_EVIDENCE", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("CONFIRMS_SIGNAL", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("DIVERGES_FROM", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("DERIVES", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("AFFECTS", "reasoning-insight", "observation-data", "reasoning-insight"),
    TBoxRelationDef("IMPACTS_OPINION", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("CONTRIBUTES_TO", "reasoning-insight", "reasoning-insight", "strategy-thesis"),
    TBoxRelationDef("SUPPORTED_BY", "reasoning-insight", "investment-core", "reasoning-insight"),
    TBoxRelationDef("CONTRADICTS", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("USES_EVIDENCE_FROM", "reasoning-insight", "investment-core", "reasoning-insight"),
    TBoxRelationDef("REQUESTS_OPINION_FROM", "reasoning-insight", "investment-core", "reasoning-insight"),
    TBoxRelationDef("HAS_EVIDENCE", "reasoning-insight", "investment-core", "reasoning-insight"),
    TBoxRelationDef("HAS_BELIEF", "reasoning-insight", "investment-core", "reasoning-insight"),
    TBoxRelationDef("HAS_OPINION", "reasoning-insight", "investment-core", "reasoning-insight"),
    TBoxRelationDef("HAS_EXECUTION_PLAN", "reasoning-insight", "investment-core", "reasoning-insight"),
    TBoxRelationDef("HAS_MESSAGE_DELIVERY_PROFILE", "reasoning-insight", "investment-core", "reasoning-insight"),
    TBoxRelationDef("USES_MESSAGE_DELIVERY_PROFILE", "reasoning-insight", "investment-core", "reasoning-insight"),
    TBoxRelationDef("HAS_AI_CONTEXT_PACKET", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("HAS_EVIDENCE_SUBGRAPH", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("INCLUDES_EVIDENCE_RELATION", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("HAS_PRIMARY_ACTION", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("BLOCKS_ACTION", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("LIMITED_BY_LIQUIDITY", "risk-exposure", "reasoning-insight", "risk-exposure"),
    TBoxRelationDef("HAS_EXIT_CAPACITY", "risk-exposure", "reasoning-insight", "risk-exposure"),
    TBoxRelationDef("HAS_SLIPPAGE_RISK", "risk-exposure", "reasoning-insight", "risk-exposure"),
    TBoxRelationDef("STRENGTHENS_ACTION_IF", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("WEAKENS_ACTION_IF", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("REQUIRES_NEXT_CHECK", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("VALIDATES_OPINION", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("VALIDATES_DATA", "reasoning-insight", "reasoning-insight", "observation-data"),
    TBoxRelationDef("HAS_DECISION_AUDIT", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("PRODUCES_VALIDATED_MESSAGE", "operations-dispatch", "reasoning-insight", "operations-dispatch"),
    TBoxRelationDef("HAS_REASONING_CARD", "reasoning-insight", "investment-core", "reasoning-insight"),
    TBoxRelationDef("HAS_CONFIDENCE", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("CHANGES_FACT", "observation-data", "observation-data", "observation-data"),
    TBoxRelationDef("TRIGGERS_MATERIALITY_ASSESSMENT", "reasoning-insight", "observation-data", "reasoning-insight"),
    TBoxRelationDef("HAS_THRESHOLD_CROSSING", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("HAS_EXPOSURE_ASSESSMENT", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("HAS_CONFIDENCE_ASSESSMENT", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("HAS_ACTIONABILITY_ASSESSMENT", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("LOWERS_CONFIDENCE_OF", "reasoning-insight", "risk-exposure", "reasoning-insight"),
    TBoxRelationDef("AMPLIFIES_RISK", "risk-exposure", "reasoning-insight", "risk-exposure"),
    TBoxRelationDef("MITIGATES_RISK", "risk-exposure", "reasoning-insight", "risk-exposure"),
    TBoxRelationDef("HAS_PIPELINE", "operations-dispatch", "investment-core", "operations-dispatch"),
    TBoxRelationDef("COLLECTS_DATA_FROM", "operations-dispatch", "operations-dispatch", "observation-data"),
    TBoxRelationDef("RUNS_ON_SCHEDULE", "operations-dispatch", "operations-dispatch", "operations-dispatch"),
    TBoxRelationDef("RUNS_AFTER_EVENT", "operations-dispatch", "operations-dispatch", "operations-dispatch"),
    TBoxRelationDef("USES_COLLECTION_POLICY", "operations-dispatch", "operations-dispatch", "operations-dispatch"),
    TBoxRelationDef("HAS_DATA_FRESHNESS", "operations-dispatch", "operations-dispatch", "observation-data"),
    TBoxRelationDef("UPDATES_GRAPH", "operations-dispatch", "operations-dispatch", "investment-core"),
    TBoxRelationDef("TRIGGERS_REASONING", "operations-dispatch", "operations-dispatch", "operations-dispatch"),
    TBoxRelationDef("HAS_REASONING_CYCLE", "operations-dispatch", "investment-core", "operations-dispatch"),
    TBoxRelationDef("SCHEDULES_ANALYSIS", "operations-dispatch", "operations-dispatch", "operations-dispatch"),
    TBoxRelationDef("PRODUCES_INSIGHT", "operations-dispatch", "operations-dispatch", "reasoning-insight"),
    TBoxRelationDef("HAS_INSIGHT_TYPE", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("CREATED_FROM_RELATION", "reasoning-insight", "investment-core", "reasoning-insight"),
    TBoxRelationDef("EVALUATED_BY", "reasoning-insight", "reasoning-insight", "reasoning-insight"),
    TBoxRelationDef("USES_INSIGHT_POLICY", "operations-dispatch", "operations-dispatch", "reasoning-insight"),
    TBoxRelationDef("USES_IMPORTANCE_GATE", "operations-dispatch", "operations-dispatch", "operations-dispatch"),
    TBoxRelationDef("HAS_NOTIFICATION_POLICY", "operations-dispatch", "investment-core", "operations-dispatch"),
    TBoxRelationDef("HAS_NOTIFICATION_DISPATCH", "operations-dispatch", "investment-core", "operations-dispatch"),
    TBoxRelationDef("PASSES_IMPORTANCE_GATE", "operations-dispatch", "reasoning-insight", "operations-dispatch"),
    TBoxRelationDef("BLOCKED_BY_IMPORTANCE_GATE", "operations-dispatch", "reasoning-insight", "operations-dispatch"),
    TBoxRelationDef("CREATES_NOTIFICATION_INTENT", "operations-dispatch", "reasoning-insight", "operations-dispatch"),
    TBoxRelationDef("CREATES_ALERT_CANDIDATE", "operations-dispatch", "reasoning-insight", "operations-dispatch"),
    TBoxRelationDef("DISPATCHED_BY", "operations-dispatch", "reasoning-insight", "operations-dispatch"),
    TBoxRelationDef("SUPPRESSED_BY_POLICY", "operations-dispatch", "operations-dispatch", "operations-dispatch"),
    TBoxRelationDef("HAS_COOLDOWN_POLICY", "operations-dispatch", "operations-dispatch", "operations-dispatch"),
    TBoxRelationDef("HAS_NOVELTY_POLICY", "operations-dispatch", "operations-dispatch", "operations-dispatch"),
    TBoxRelationDef("OBSERVES_MARKET_SESSION", "operations-dispatch", "investment-core", "operations-dispatch"),
    TBoxRelationDef("TRIGGERS_ALERT", "operations-dispatch", "reasoning-insight", "operations-dispatch"),
]


RULE_DEFS: List[TBoxRuleDef] = [
    TBoxRuleDef("portfolio exposure, position weight, and sector concentration create risk beliefs", "risk-exposure"),
    TBoxRuleDef("price, trend, volume, and smart-money flow observations derive investable signals", "observation-data"),
    TBoxRuleDef("signals support, weaken, or invalidate an investment thesis before AI forms an opinion", "strategy-thesis"),
    TBoxRuleDef("legacy score model remains supporting evidence, not the primary decision model", "strategy-thesis"),
    TBoxRuleDef("legacy score disagreement with trend, flow, or risk creates contradiction beliefs", "reasoning-insight"),
    TBoxRuleDef("data quality, freshness, provenance, and missing data control AI opinion confidence", "observation-data"),
    TBoxRuleDef("watchlist candidates create observation assertions and entry checks, not sell decisions", "investment-core"),
    TBoxRuleDef("all runtime concepts become ABox nodes before AI receives an opinion packet", "operations-dispatch"),
    TBoxRuleDef("relations with opinionImpact, riskImpact, supportImpact, or polarity change AI opinion pressure", "reasoning-insight"),
    TBoxRuleDef("execution plans are ABox nodes derived from facts, active relation rules, counter signals, and missing data before notification rendering", "reasoning-insight"),
    TBoxRuleDef("AI validation validates an active investment opinion and execution plan before notification dispatch", "reasoning-insight"),
    TBoxRuleDef("data collection, analysis, reasoning, and notification dispatch are first-class ontology concepts", "operations-dispatch"),
    TBoxRuleDef("notification dispatch is driven by meaningful ontology insights, not by alert-type polling alone", "operations-dispatch"),
    TBoxRuleDef("fact changes must pass a materiality assessment before they trigger ontology reasoning or notification intent", "reasoning-insight"),
    TBoxRuleDef("materiality combines threshold crossings, novelty, exposure, confidence, and actionability before dispatch", "reasoning-insight"),
    TBoxRuleDef("collection schedules describe data freshness targets while cooldown, novelty, and suppression policies control delivery noise", "operations-dispatch"),
    TBoxRuleDef("price-bar sequence, key levels, and relation-state transitions distinguish confirmation, failure, and acceleration", "observation-data"),
    TBoxRuleDef("price path transitions such as falling-to-rebound, sideways breakout, sideways breakdown, and rising-to-distribution can change investment thesis confidence", "reasoning-insight"),
    TBoxRuleDef("individual news, disclosure, macro, earnings, and analyst events are ABox evidence objects that can support, weaken, or invalidate a thesis", "observation-data"),
    TBoxRuleDef("news relevance scope separates direct instrument evidence from peer, sector, market, and noise context before rules score it", "observation-data"),
    TBoxRuleDef("factor exposure, benchmark beta, and correlation clusters transmit market-regime risk across positions", "risk-exposure"),
    TBoxRuleDef("liquidity profile, exit capacity, and slippage risk constrain execution plans before alert dispatch", "risk-exposure"),
    TBoxRuleDef("source reliability and observation confidence weight evidence before AI receives the graph", "observation-data"),
    TBoxRuleDef("RuleBox stores executable graph inference rules as first-class ontology nodes linked to their conditions and derived relation templates", "reasoning-insight"),
    TBoxRuleDef("InferenceBox stores derived assertions, inference traces, matched conditions, and source relation ids so new ABox facts can immediately affect opinions", "reasoning-insight"),
    TBoxRuleDef("Neo4j projection preserves TBox, RuleBox, ABox, and InferenceBox as queryable ontology layers instead of flattening rule output into text", "operations-dispatch"),
    TBoxRuleDef("Neo4j native Cypher executes RuleBox conditions and materializes InferenceBox nodes, evidence, beliefs, and derived relations after graph persistence", "reasoning-insight"),
    TBoxRuleDef("companies issue securities, securities represent tradable instruments, and peer groups transmit sector and competitive context before AI forms an opinion", "investment-core"),
    TBoxRuleDef("market-data changes become FactChange and MaterialityAssessment ABox nodes before any alert candidate is created", "observation-data"),
    TBoxRuleDef("trend transitions remain ABox facts in Neo4j even when local Python opinions are disabled for production projection", "reasoning-insight"),
    TBoxRuleDef("AI receives a compact evidence subgraph built from InferenceBox relations, traces, current facts, missing data, and source freshness instead of an undifferentiated raw dump", "reasoning-insight"),
    TBoxRuleDef("RuleBox graph conditions can filter ABox fact values such as trade strength, volume ratio, orderbook imbalance, materiality score, event type, and relation scope before deriving opinions", "reasoning-insight"),
    TBoxRuleDef("material direct news, disclosures, trade-flow pressure, trend transitions, and fact-change gates can each independently create InferenceBox relations that alter AI investment opinions", "reasoning-insight"),
    TBoxRuleDef("RuleBox relation templates own decisionStage and stagePriority so graph-store policy, not rule-id code maps, controls AI decision routing", "reasoning-insight"),
    TBoxRuleDef("RuleBox changes must produce governance versions and reviewable rule-change candidates before candidate rules affect InferenceBox decisions", "reasoning-insight"),
]


TBOX_CLASSES = [item.name for item in CLASS_DEFS]
TBOX_RELATION_TYPES = [item.name for item in RELATION_DEFS]
TBOX_REASONING_RULES = [item.text for item in RULE_DEFS]

_CLASS_BY_NAME: Dict[str, TBoxClassDef] = {item.name: item for item in CLASS_DEFS}
_RELATION_BY_NAME: Dict[str, TBoxRelationDef] = {item.name: item for item in RELATION_DEFS}

_ABSTRACT_CLASS_NAMES = {
    "Instrument",
    "Security",
    "Equity",
    "Observation",
    "ExternalObservation",
    "ExternalSignal",
    "Risk",
    "Signal",
    "Evidence",
    "Belief",
    "Opinion",
    "ReasoningRule",
    "RuleRegistry",
    "RuleCondition",
    "RelationTemplate",
    "DataPipeline",
    "NotificationPolicy",
    "NotificationIntent",
}

_SOURCE_BACKED_CLASS_NAMES = {
    "Portfolio",
    "Account",
    "Company",
    "Stock",
    "ETF",
    "CryptoAsset",
    "CashAsset",
    "Derivative",
    "Index",
    "FXPair",
    "Position",
    "Watchlist",
    "WatchlistCandidate",
    "Sector",
    "Industry",
    "PeerGroup",
    "Market",
    "Currency",
    "Cash",
    "MarketExposure",
    "FactChange",
    "PriceObservation",
    "VolumeObservation",
    "TechnicalObservation",
    "FlowObservation",
    "FundamentalObservation",
    "PriceMetric",
    "TechnicalIndicator",
    "TradeFlow",
    "DataQuality",
    "DataSource",
    "DataFreshness",
    "MissingData",
    "Provenance",
    "SourceReliability",
    "ObservationConfidence",
    "TimeWindow",
    "ObservationTime",
    "SignalHorizon",
    "HoldingPeriod",
    "ValidityInterval",
    "Staleness",
    "ValidationRule",
    "PriceBar",
    "PricePath",
    "VolumeProfile",
    "KeyLevel",
    "SupportLevel",
    "ResistanceLevel",
    "Breakout",
    "FailedBreakout",
    "Retest",
    "ResearchEvidence",
    "NewsEvent",
    "NewsArticle",
    "NewsTopic",
    "NewsEventType",
    "PeerCompanyMention",
    "DisclosureEvent",
    "DisclosureFiling",
    "MacroIndicator",
    "MacroPrint",
    "RateSignal",
    "InterestRate",
    "YieldCurve",
    "FXRateSignal",
    "CreditSpreadSignal",
    "CryptoMarketSignal",
    "EarningsEvent",
    "EarningsCalendarEvent",
    "AnalystRevision",
    "CorporateAction",
    "RegulatoryEvent",
    "ProductLine",
    "SupplyChainExposure",
    "CustomerExposure",
    "RevenueExposure",
    "Factor",
    "FactorExposure",
    "BenchmarkIndex",
    "HedgeCandidate",
    "Strategy",
    "InvestmentThesis",
    "EntryCondition",
    "ExitCondition",
    "RiskManagementRule",
    "RebalancingRule",
    "PositionSizingRule",
    "Scenario",
    "StrategyExperiment",
    "StrategySignal",
    "ModelScore",
    "Threshold",
    "RuntimeSetting",
    "AlertRule",
    "PromptTemplate",
    "ValuationAssumption",
    "LegacyScoreModel",
    "MarketRisk",
    "LiquidityRisk",
    "LiquidityProfile",
    "ExitCapacity",
    "SlippageEstimate",
    "ConcentrationRisk",
    "CurrencyRisk",
    "VolatilityRisk",
    "EventRisk",
    "DataQualityRisk",
    "ModelRisk",
    "ExecutionRisk",
    "CorrelationRisk",
    "RegimeRisk",
}

_INFERRED_CLASS_NAMES = {
    "PriceSignal",
    "TrendSignal",
    "FlowSignal",
    "ValuationSignal",
    "MacroSignal",
    "DisclosureSignal",
    "CryptoSignal",
    "DataQualitySignal",
    "RelationStateSnapshot",
    "SignalTransition",
    "TrendPhase",
    "TrendTransition",
    "ReversalSignal",
    "ConsolidationBreak",
    "AccelerationSignal",
    "DecelerationSignal",
    "MaterialityAssessment",
    "ThresholdCrossing",
    "ExposureAssessment",
    "ConfidenceAssessment",
    "ActionabilityAssessment",
    "PreviousInsight",
    "ActiveInvestmentOpinion",
    "ExecutionPlan",
    "ActionCandidate",
    "BlockedAction",
    "InvalidationCondition",
    "NextCheck",
    "AIValidation",
    "ValidatedOpinion",
    "AIJudgmentAudit",
    "AIContextPacket",
    "EvidenceSubgraph",
    "GraphNeighborhood",
    "Opportunity",
    "Contradiction",
    "Insight",
    "InsightType",
    "InsightPolicy",
    "MessageDeliveryProfile",
    "DerivedAssertion",
    "InferenceTrace",
    "InferencePath",
    "GraphReasoner",
    "Neo4jNativeReasoner",
    "ReasoningCard",
    "ConfidenceScore",
    "ReliabilityScore",
    "AIReview",
}

_RULEBOX_CLASS_NAMES = {
    "RelationRuleRegistry",
    "GraphInferenceRule",
    "RelationReasoningRule",
    "RelationRuleCondition",
    "RelationRuleTemplate",
    "RuleDecisionPolicy",
    "RulePriorityPolicy",
}

_GOVERNANCE_CLASS_NAMES = {
    "RuleBoxGovernance",
    "RuleBoxVersion",
    "RuleChangeCandidate",
}

_OPERATIONAL_CLASS_NAMES = {
    "CollectionSchedule",
    "CollectionPolicy",
    "MarketSnapshot",
    "WatchlistSnapshot",
    "ExternalSignalCollection",
    "AnalysisJob",
    "ReasoningCycle",
    "NotificationDispatch",
    "AlertCandidate",
    "ImportanceGate",
    "CooldownPolicy",
    "NoveltyPolicy",
    "SuppressionPolicy",
    "MarketSession",
    "OperationalEvent",
}

_SCHEMA_RELATION_NAMES = {
    "DEFINES_BOUNDED_CONTEXT",
    "DEFINES_CLASS",
    "DEFINES_RELATION",
    "CONSTRAINS_ASSERTIONS",
    "CONSTRAINS_RULES",
    "DERIVES_ASSERTIONS",
    "IS_A",
}

_RULEBOX_RELATION_NAMES = {
    "HAS_RULE_CONDITION",
    "USES_RULE_TEMPLATE",
    "MATCHES_CONDITION",
    "PRODUCES_DERIVED_ASSERTION",
    "DERIVES_RELATION",
    "GOVERNED_BY",
    "PROPOSES_RULE_CHANGE",
    "SUPERSEDES_RULE_VERSION",
}


def tbox_class_def(name: str) -> Optional[TBoxClassDef]:
    return _CLASS_BY_NAME.get(str(name or ""))


def tbox_relation_def(name: str) -> Optional[TBoxRelationDef]:
    return _RELATION_BY_NAME.get(str(name or "").upper())


def tbox_class_materialization_policy(name: str) -> str:
    value = str(name or "")
    if value in _GOVERNANCE_CLASS_NAMES:
        return "governance"
    if value in _RULEBOX_CLASS_NAMES:
        return "rulebox"
    if value in _INFERRED_CLASS_NAMES:
        return "inferred"
    if value in _OPERATIONAL_CLASS_NAMES:
        return "operational"
    if value in _SOURCE_BACKED_CLASS_NAMES:
        return "source-backed"
    if value in _ABSTRACT_CLASS_NAMES:
        return "abstract"
    return "source-backed"


def tbox_materialization_box(policy: str) -> str:
    value = str(policy or "")
    if value in {"abstract", "schema"}:
        return "TBox"
    if value == "rulebox":
        return "RuleBox"
    if value == "governance":
        return "RuleBoxGovernance"
    if value == "inferred":
        return "InferenceBox"
    return "ABox"


def tbox_relation_materialization_policy(name: str) -> str:
    value = str(name or "").upper()
    if value in _SCHEMA_RELATION_NAMES:
        return "schema"
    if value in _RULEBOX_RELATION_NAMES:
        return "rulebox"
    definition = tbox_relation_def(value)
    if definition and definition.bounded_context in {"reasoning-insight", "operations-dispatch"}:
        return "inferred-or-operational"
    return "source-backed"


def bounded_contexts_payload() -> List[Dict[str, object]]:
    return [asdict(item) for item in BOUNDED_CONTEXTS]


def class_definitions_payload() -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for item in CLASS_DEFS:
        payload = asdict(item)
        policy = tbox_class_materialization_policy(item.name)
        payload["materializationPolicy"] = policy
        payload["materializationBox"] = tbox_materialization_box(policy)
        rows.append(payload)
    return rows


def relation_definitions_payload() -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for item in RELATION_DEFS:
        payload = asdict(item)
        policy = tbox_relation_materialization_policy(item.name)
        payload["materializationPolicy"] = policy
        payload["materializationBox"] = tbox_materialization_box(policy)
        rows.append(payload)
    return rows


def rule_definitions_payload() -> List[Dict[str, object]]:
    return [asdict(item) for item in RULE_DEFS]
