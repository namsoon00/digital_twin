# Ontology Strategy Model

투자전략의 기준 모델은 관계 규칙 구조다. 원시 가격·수익률·거래량 같은 숫자는 사실로 보관하지만, 최종 판단은 합산 점수 대신 TypeDB가 만든 범주형 상태와 근거 관계로 결정한다.

## Domain Vocabulary

TBox 정의는 `python_service/digital_twin/domain/ontology_tbox.py`에 둔다. 그래프 데이터 계약은 `domain/ontology_contracts.py`, TBox/ABox payload와 bounded-context 속성 부여는 `domain/ontology_schema.py`, reasoning card와 AI 입력 read model은 `domain/ontology_prompting.py`가 맡는다. `domain/ontology.py`는 이 조각들을 사용해 현재 계좌 스냅샷을 ABox 그래프로 조립한다.

바운디드 컨텍스트는 6개다.

- `investment-core`: `Account`, `Portfolio`, `Instrument`, `Stock`, `Position`, `Watchlist`, `Cash`, `Sector`, `Market`, `Currency`, `MarketExposure`.
- `observation-data`: `Observation`, `PriceObservation`, `TechnicalObservation`, `FlowObservation`, `ExternalSignal`, `DataSource`, `DataFreshness`, `Provenance`, `SignalHorizon`, `MissingData`.
- `strategy-thesis`: `Strategy`, `InvestmentThesis`, `EntryCondition`, `ExitCondition`, `RiskManagementRule`, `RebalancingRule`, `PositionSizingRule`, `DecisionState`, `ValidationState`, `RuntimeSetting`.
- `risk-exposure`: `Risk`, `MarketRisk`, `LiquidityRisk`, `ConcentrationRisk`, `CurrencyRisk`, `EventRisk`, `DataQualityRisk`, `ModelRisk`, `RegimeRisk`.
- `reasoning-insight`: `Signal`, `Evidence`, `Belief`, `Opinion`, `Opportunity`, `Contradiction`, `Insight`, `ReasoningCard`, `AIReview`.
- `operations-dispatch`: `DataPipeline`, `CollectionSchedule`, `CollectionPolicy`, `AnalysisJob`, `ReasoningCycle`, `NotificationDispatch`, `CooldownPolicy`, `NoveltyPolicy`, `SuppressionPolicy`, `MarketSession`.

## TBox And ABox

이 모델은 TBox와 ABox를 분리한다.

- `TBox`: 투자 관계 분석의 규칙 구조다. `Portfolio`, `Stock`, `Sector`, `Risk`, `Evidence`, `Belief`, `Opinion` 같은 클래스와 `HOLDS`, `EXPOSED_TO`, `CONTRADICTS`, `HAS_EVIDENCE` 같은 관계 타입, 그리고 판단 규칙을 정의한다.
- `ABox`: 현재 계좌 스냅샷에서 만들어진 실제 데이터 계층이다. 실제 보유 종목, 섹터 노출, 수급 근거, 추세 근거, 위험 판단 근거, 종목별 의견이 여기에 들어간다.

AI 프롬프트에는 TBox, `boundedContexts`, ABox, operational ontology, reasoning card를 함께 전달한다. AI는 TBox를 해석 규칙으로 읽고, ABox를 현재 투자 상태의 사실 집합으로 읽어야 한다. TypeDB 저장 시 엔티티와 관계에는 `ontologyBox` 속성을 붙여 `TBox`와 `ABox`를 구분하고, ABox 노드/관계에는 가능하면 `boundedContext`도 붙인다.

데이터 수집 주기 자체도 세계관의 일부다. `marketSnapshot`, `watchlistSnapshot`, `externalSignals`는 ABox의 `DataPipeline` 노드이며, 각 파이프라인은 `CollectionSchedule`, `DataFreshness`, `CollectionPolicy`, `ReasoningCycle`에 연결된다. 즉 3분/5분/30분 같은 값은 TBox 클래스가 아니라, `CollectionSchedule` 클래스의 현재 실행 인스턴스다.

### Multi-Account World Ownership

온톨로지의 개념 정의와 실제 사실의 소유권은 분리한다. TBox와 RuleBox는 모든 계정이 함께 쓰는 공통 투자 언어와 추론 정의이며, ABox와 InferenceBox는 반드시 하나의 명시적인 세계에 속한다.

```
TBox / RuleBox (shared definitions)
                 |
                 +--> MarketWorld (shared market facts per market)
                 |      market:shared:kr
                 |      market:shared:us
                 |
                 +--> PortfolioWorld (private facts and inference per tenant/account)
                        portfolio:<tenant>:<account>
                        ABox -> active RuleBox -> InferenceBox -> alert / AI packet
```

- `MarketWorld`: 종목, 시장, 뉴스, 공시, 거시, 환율처럼 계정에 독립적인 관측 사실을 시장별로 공유한다. 보유 수량, 평균매입가, 위험 한도, 알림 설정, 의사결정, 가설, 실행 계획은 절대 포함하지 않는다. 수집 시각을 기준으로 보존 기간과 종목 수 상한을 적용해 무한히 커지지 않게 관리한다.
- `PortfolioWorld`: 테넌트와 계정 하나에만 속한다. 보유·관심종목, 포지션, 계정 위험 예산, 전략, 가설, 의사결정, 알림 이력과 그 계정의 ABox/InferenceBox generation을 보관한다. 활성 ABox pointer와 InferenceBox pointer도 `worldId`로 조회하므로 다른 계정의 최신 generation을 선택할 수 없다.
- `MarketHypothesis`는 `MarketWorld`에 쓰는 새로운 투자 판단이 아니다. TypeDB가 성립시킨 경로 중 시장 공통 입력만 가진 경우에만 `marketHypothesisId`를 만들고, 각 `PortfolioWorld`의 판단 에피소드가 그 공통 식별자를 참조한다. 따라서 같은 시장 인과 설명은 계정마다 같은 식별자를 쓰되, 공유 세계에는 계정 판단이나 행동이 저장되지 않는다.
- `AccountHypothesisOverlay`는 보유 여부, 손익, 비중, 위험 한도, 투자 성향, 허용/차단 행동처럼 계정 전용 입력을 별도 ABox 개체로 남긴다. 조건 구조가 시장 입력과 계정 입력을 섞었거나 소유권을 판별할 수 없으면 공통 시장 가설을 만들지 않고 `mixed` 또는 `unverified`로 보수적으로 남긴다.
- `RuleBox`: 규칙 정의는 전역으로 한 번만 배포하지만, TypeDB schema function 실행은 반드시 `PortfolioWorld.worldId`를 인자로 받는다. 계정이나 세계가 지정되지 않은 RuleBox 실행은 차단한다. 공유 `MarketWorld`는 직접 투자 판단을 수행하는 대상이 아니므로 그 세계에서 InferenceBox를 만들 수 없다.
- 현재 native RuleBox는 포트폴리오 사실과 시장 사실을 한 ABox generation 안에서 결합한다. 따라서 `PortfolioWorld`에는 실행을 위한 시장 사실 read mirror가 존재하고, `MarketWorld`는 공유·보존되는 기준 원본이다. 이는 계정 사실을 공유하지 않는 경계를 유지한다. 두 세계를 하나의 native query에서 직접 조합하는 최적화는 이후 RuleBox schema 계약을 확장할 때 별도 이행한다.

운영 흐름은 `계정별 원본 스냅샷 -> PortfolioWorld ABox 투영 -> 같은 관측의 계정 독립 slice를 MarketWorld에 병합 -> 해당 PortfolioWorld만 TypeDB RuleBox materialization -> 해당 계정 AI/알림`이다. 새 공통 규칙을 배포한 직후에는 대상 계정의 다음 투영이 해당 규칙을 적용한다. 전 계정에 즉시 실행이 필요할 때도 무범위 전역 실행 대신, 계정 목록을 대상으로 한 명시적 world fan-out 작업만 허용한다.

RuleBox 조건은 선택적으로 `hypothesisScope`를 `market`, `account`, `mixed`, `unverified`로 명시할 수 있다. 이 값은 TypeDB의 조건 평가를 바꾸지 않으며, 사용자에게 노출되는 가설의 소유권을 감사하기 위한 메타데이터다. 구조적으로 계정 필드(예: 손익률·보유비중)와 충돌하는 `market` 표시는 신뢰하지 않고 `unverified`로 처리한다.

## Relation Types

- `HOLDS`: 포트폴리오가 종목을 보유한다.
- `HOLDS_CASH`: 포트폴리오가 현금을 보유한다.
- `BELONGS_TO`: 종목이 섹터에 속한다.
- `TRADED_IN`: 종목이 시장에 상장되어 있다.
- `DENOMINATED_IN`: 종목 평가 통화.
- `EXPOSED_TO`: 포트폴리오 또는 종목이 리스크/섹터에 노출되어 있다.
- `HAS_OBSERVATION`: 종목 또는 포트폴리오가 가격, 기술 지표, 수급, 외부 신호 관측값을 가진다.
- `USES_STRATEGY`: 포트폴리오가 적용 중인 투자전략.
- `BASED_ON_THESIS`: 종목 또는 전략이 투자 가설에 의해 평가된다.
- `SUPPORTS_THESIS`, `WEAKENS_THESIS`, `INVALIDATES_THESIS`: 근거, 기회, 리스크, 모순이 투자 가설에 미치는 방향.
- `HAS_TIME_HORIZON`, `APPLIES_TO_HORIZON`: 보유/관심 판단의 유효 기간과 관찰 범위.
- `SUPPORTED_BY`: 종목 보유 이유를 뒷받침하는 기회 관계.
- `CONTRADICTS`: 추세, 수급, 보유 비중, 뉴스·공시 근거 사이에 충돌이 있다.
- `USES_EVIDENCE_FROM`: 관측 사실과 검증된 외부 근거를 판단에 연결한다.
- `REQUESTS_OPINION_FROM`: AI 투자 의견 정보로 넘긴다.
- `HAS_EVIDENCE`, `HAS_BELIEF`, `HAS_OPINION`: TypeDB/그래프 저장소 저장용 세부 관계.

## Runtime Flow

1. Toss 계좌, 시장 데이터, 외부 API 데이터를 `Position`, `PortfolioSummary`, `externalSignals`로 정규화한다.
2. `infrastructure/ontology_projection.py`가 종목별 ABox fact와 부족 데이터를 TypeDB에 저장하고, TypeDB schema function rule materialization 결과를 InferenceBox로 읽어 운영 판단에 사용한다. `domain/ontology_relation_reasoning.py`는 프롬프트 조립과 read model formatting helper로만 사용하며 추론을 실행하지 않는다.
3. `DecisionItem.decision`, `reviewLevel`, `dataState`, `changeState`, `conflictState`, `validationState`, `decisionBasis`는 관계 규칙 결과에서 나온다. `decisionBasis`는 `ontologyRelationRules`다.
4. 과거 점수 기반 데이터가 남아 있으면 읽기 경계에서 범주형 상태로만 변환하며, 새 판단과 메시지에는 다시 저장하거나 사용하지 않는다.
5. `domain/ontology.py`가 TBox/ABox 그래프와 `OntologyOpinion`을 만든다. 이때 `Strategy`, `InvestmentThesis`, `Observation`, `Risk`, `Insight`, `NotificationDispatch`까지 모두 ABox 노드로 만든다.
6. `DecisionItem.relation_rule_context`, `ai_prompt_context`, `ai_context`에 관계 규칙 결과와 프롬프트 입력 계약을 함께 붙인다.
7. 실시간 모니터링은 알림 metadata에 `ontologyRelationContext`, `ontologyPromptContext`, `ontologyReviewContext`를 포함한다.
8. 모델 리뷰 워커는 이 정보를 비동기 AI 프롬프트에 넣어 판단 변화 원인, 노이즈 가능성, 부족 데이터, 다음 규칙 개선안을 분석한다.
9. `infrastructure/ontology_projection.py`가 스냅샷을 온톨로지 read model로 투영한다. 런타임은 `infrastructure/ontology_graph_store.py`의 generic factory만 사용하고, 이 factory는 TypeDB repository 하나만 반환한다.

알림은 투자 이벤트 타입별 폴링으로 직접 발송하지 않는다. 기존 `modelBuy`, `holdingTiming`, `externalDartDisclosure` 같은 이벤트는 `investmentInsight.metadata.sourceAlertEvents`의 근거 신호로 남고, 최종 발송은 `Insight -> DISPATCHED_BY -> NotificationDispatch(investmentInsight)` 관계가 담당한다.

## TypeDB Schema Function Rule And InferenceBox

운영 판단의 기준은 TypeDB에 저장된 ABox와 TypeDB schema function materialization 결과다. TypeDB 3에서는 예전 TypeDB 2의 `define rule` 대신 schema `fun`이 rule-equivalent 추론 단위다. Python 공식, 템플릿 조건, 알림 임계값은 투자 의미를 직접 만들지 않는다.

운영 경로는 다음 경계를 강제한다. Python의 ABox projection은 TypeDB에 활성화된 규칙이 참조하는 관계 타입만 전달하며 조건값이나 임계값을 미리 판정하지 않는다. 모든 파생 관계는 TypeDB RuleBox 정의에 `decisionStage`를 명시해야 하고, 이 값이 없으면 해당 관계는 설명 자료로만 남아 투자 판단을 차단한다. Python action label fallback과 별도 Psychology Shadow 판단 경로는 사용하지 않는다.

실행 흐름은 다음과 같다.

1. `portfolio_ontology_builder.py`가 계좌, 보유/관심 종목, 가격, 이동평균, 수급, 투자자별 매수·매도, 뉴스, 공시, 거시, 투자 성향, 데이터 품질을 ABox fact로 만든다.
2. `typedb_ontology.py`가 ABox를 TypeDB에 저장한다.
3. RuleBox semantic profile은 TypeDB schema function으로 컴파일된다. 각 function은 TypeDB ABox를 직접 조회하며, 필수 조건, 후보 조건 중 N개 이상, 부정 조건을 TypeQL 안에서 처리한다.
4. 성립한 규칙은 InferenceBox 노드와 관계로 저장된다. 각 결과에는 `sourceRuleId`, `nativeRuleId`, `semanticRuleId`, `reasoningMode`, `materializationSource`, `matchedConditions`, `reviewLevel`, `dataState`, `evidenceRole`, `conflictState`, `validationState`, `sourceEvidenceIds`가 남는다.
5. `ontology_inference_context.py`가 최신 generation의 InferenceBox만 읽어 투자 판단 후보, 근거, 반대 근거, 부족 데이터, AI 질문을 만든다.
6. AI는 이 컨텍스트를 받아 최종 의견을 쓰고, 시스템은 없는 데이터 생성 여부와 규칙 충돌 여부를 검증한 뒤 알림 메시지에 넣는다.

### Temporal Observation And Episode Inference

기간 추론에서도 ABox와 InferenceBox의 경계를 유지한다. Python은 MySQL 시계열에서 산술 관측값만 계산한다. 여기에는 기간 시작·현재·고점·저점 가격, 전체·이전·최근 변화율, 고점 대비 하락폭, 저점 대비 회복폭, 최근 연속 상승·하락 횟수, 이동평균 상향·하향 통과 횟수, 서로 다른 수급 관측 수, 이벤트 수, 유효·오래된 관측 비율이 포함된다.

ABox의 기간 경로에는 `HAS_TEMPORAL_WINDOW`, `WINDOW_CONTAINS_OBSERVATION`, `PRECEDES` 관계를 투영하고, 자료가 부족하면 별도로 `HAS_COVERAGE_GAP`을 남긴다. 시작·중간·최신 관측점은 원시 가격·수급·시각·출처·품질을 보존한다. 실제 수급 관측이 없거나 오래된 경우에는 수급값을 `0`으로 만들지 않고 `smartMoneyDataState`를 `unavailable` 또는 `partial`로 둔다. `PersistentDecline`, `FailedRecovery`, `DeclineDeceleration`, `RecoveryAttempt`, `AccumulationDuringWeakness`, `DistributionDuringBounce` 같은 이름과 위험·지지 방향은 Python이 붙이지 않는다.

TypeDB schema function은 원시 기간 속성을 직접 비교해 `DERIVES_TREND_EPISODE`, `HAS_TREND_TRANSITION`, `HAS_INFERRED_RISK`, `HAS_INFERRED_SUPPORT`, `BLOCKS_VALIDATION_OF`를 InferenceBox에 만든다. 임계값은 TypeDB 규칙 JSON에만 있으며 웹 규칙 관리 화면에서 조회·수정할 수 있다. 오래된 관측이 하나라도 있거나 유효 관측 비율이 부족하면 방향 규칙은 성립하지 않고 별도 신선도 차단 관계가 만들어진다. 수급 변화 규칙은 값이 존재하는 것만으로 성립하지 않으며, 서로 다른 실제 수급 관측이 두 개 이상이어야 한다.

우선적으로 강화한 추론 관계 축은 다음 5개다.

- 종목 성격 관계: `HAS_INSTRUMENT_PROFILE`, `HAS_ARCHETYPE`, `HAS_POSITION_INTENT`로 종목을 성장주, 반도체 업황 민감주, 비트코인 가격에 민감한 주식, 배당 중심 우선주, 대형 우량주 등으로 분류한다. `MATCHES_INVESTOR_PROFILE`, `VIOLATES_STRATEGY_FIT` 추론은 이 성격이 현재 가격 흐름과 계정 성향에 맞는지 분리한다. 내부 TypeDB ID와 사용자 표현의 기준은 [투자 보편언어](investment-ubiquitous-language.md)를 따른다.
- 투자 성향 적합 관계: `HAS_RISK_BUDGET`, `HAS_PROFIT_POLICY`, `EVALUATED_UNDER_STRATEGY`를 통해 공격형/성장형/균형형/보수형의 손실 허용폭, 수익 보호 기준, 단일 종목 비중 한도를 적용한다. `VIOLATES_RISK_TOLERANCE`, `FITS_INVESTOR_RISK_PROFILE` 추론은 모든 계정에 같은 손절/추가매수 기준을 쓰지 않게 한다.
- 가격 회복 관계: `RECLAIMS_LEVEL`, `BREAKS_LEVEL`, `HAS_TREND_TRANSITION`과 5/20/60일 평균 가격 거리, 당일 등락, 거래량을 묶어 `CONFIRMS_RECOVERY`, `FAILS_RECOVERY`를 만든다. 단순 반등, 확인된 회복, 반등 실패 위험을 다른 관계로 남긴다.
- 수급 심리 관계: `HAS_TRADE_FLOW`, `HAS_INVESTOR_FLOW_SENTIMENT`로 체결강도, 호가 불균형, 외국인·기관·개인 순매수 심리를 표현한다. `CONFIRMS_WITH_FLOW`, `DIVERGES_FROM_FLOW`는 가격 변화와 큰 자금 흐름이 같은 방향인지 또는 어긋나는지를 추론한다.
- 뉴스·공시 영향 관계: `HAS_EXTERNAL_SIGNAL`, `NEWS_CONTEXT_FOR`, `NEWS_RISK_FOR`, `NEWS_SUPPORTS_ENTRY`, `HAS_DILUTION_RISK`, `CONFIRMS_EVENT_IMPACT`로 기사/공시 존재와 실제 가격·거래 반응을 분리한다. 새 뉴스나 공시가 있더라도 신선도, 관련성, 중요도, 원문 확보, 가격 반응이 약하면 실행 강도를 낮춘다.

TypeDB는 먼저 규칙 성립 여부와 InferenceBox 관계를 만들고, `ontology_inference_context.py`는 성립한 관계와 ABox 사실을 다음 범주형 상태로 정리한다. 원시 숫자는 사실 설명에만 남고 합산 투자 점수나 확률을 만들지 않는다.

- `reviewLevel`: `normal`, `observe`, `check`, `act`, `immediate`, `blocked` 중 현재 다시 확인해야 할 단계.
- `dataState`: `sufficient`, `partial`, `insufficient`, `unavailable` 중 판단에 쓸 자료의 상태.
- `evidenceRole`: `risk`, `support`, `counter`, `context`, `blocking` 중 근거가 판단에서 맡는 역할.
- `conflictState`: 위험 근거와 버티는 근거가 한쪽만 있는지, 함께 있는지, 참고 수준인지 나타낸다.
- `changeState`: 이전 알림과 비교해 새 조건, 개선, 악화, 방향 변경, 새 뉴스·공시가 있었는지 나타낸다.
- `validationState`: `ready`, `conditional`, `blocked` 중 해당 판단을 실제 안내에 사용할 수 있는지 나타낸다.

따라서 같은 TypeDB 규칙이 성립해도 손실률, 5일선·20일선·60일선 위치, 외국인·기관 흐름, 뉴스 원문 확보, 데이터 지연에 따라 확인 단계와 자료 상태가 달라진다. 이 상태는 가격 방향 예측 확률이 아니라 사용자가 무엇을 다시 확인해야 하는지 설명한다.

운영 상태를 해석하는 기준:

- `reasoningMode=typedb-native-rule-materialized`: 정상. TypeDB ABox에서 native rule match가 실행되고 InferenceBox가 저장됐다.
- `materializationSource=typedb-abox-native-rule`: 정상. TypeDB ABox 기반 materialization 결과다.
- `pythonCompatibilityReasonerUsed=false`: 정상 운영 경로다.
- `typedbSchemaFunctionUsed=true`: 정상. RuleBox profile이 TypeDB schema function으로 동기화되고 해당 function query가 실행됐다.
- `typedbNativeRuleSkippedCount=0`: 정상. 지원되지 않아 건너뛴 active rule이 없다.
- `pythonCompatibilityReasonerUsed=true`: 비정상. 운영 투자 판단 경로에서는 사용하면 안 된다. TypeDB schema function sync/query 실패는 투자 판단을 차단하고 진단 알림으로 다뤄야 한다.
- `relations=0`, `traces=0`: 보유/관심 데이터가 있는데도 이 값이면 TypeDB 저장, native rule profile, 조건 매칭, worker 실행 상태를 순서대로 확인한다.

TypeDB schema function rule은 TypeDB schema의 class/relation 정의와 다르다. TBox는 개념과 가능한 관계를 정의하고, ABox는 현재 사실을 담는다. RuleBox profile에서 생성된 schema function은 이 ABox 사실이 어떤 조합일 때 `손실 방어`, `회복 확인`, `추가매수 보류`, `조건부 추가매수 검토`, `뉴스 리스크 대응` 같은 InferenceBox 관계로 이어지는지 정의한다.

## Projection Boundary

온톨로지는 DDD aggregate의 저장소가 아니라 projection/read model이다. `Account`, `Monitoring`, `Research`, `Strategy`, `Notification` 같은 소유 컨텍스트가 사실과 이벤트를 만든 뒤, projection이 그 사실을 `TBox` 규칙에 맞는 `ABox` 노드와 관계로 변환한다. 이 경계 덕분에 계좌 저장, 알림 outbox, 모델 리뷰 큐의 트랜잭션은 각 context와 unit-of-work가 책임지고, TypeDB 저장이나 AI 프롬프트 생성 실패는 원본 업무 트랜잭션을 깨지 않는다.

Projection은 다음 용도로만 사용한다.

- TypeDB 그래프 조회와 시각화.
- reasoning card, AI inference packet, prompt payload 같은 읽기 모델 생성.
- 품질 샘플과 운영 콘솔용 진단 지표 생성.
- bounded context 사이의 의미 관계를 설명하는 audit trail.

새 투자 사실이 필요하면 projection에 직접 상태를 추가하지 말고, 먼저 소유 context의 aggregate/event/repository에 사실을 남긴 뒤 projection 변환을 확장한다.

### Projection Runtime Stability

실시간 투영은 현재 사실을 정확하게 반영하면서도 이전 세대 전체를 매번 다시 기록하거나 정리하지 않아야 한다. 이 원칙은 투자 판단 규칙이 아니라 `operations-dispatch` bounded context의 운영 계약이다.

1. ABox 변경 영향은 `directChangedScope`와 `dependencyAffectedScope`로 나눈다. 전자는 실제로 새로 들어온 가격, 수급, 뉴스, 공시, 거시 같은 사실이고, 후자는 그 사실을 입력으로 읽는 RuleBox 조건의 범위다.
2. 국소 변경일 때는 TypeDB가 실행할 RuleBox 후보를 의존 범위로 좁힐 수 있다. 다만 직전 활성 InferenceBox가 같은 ABox 세대와 동일 RuleBox 버전으로 정합한 경우에만, 변경 후보 규칙과 직전에 성립했던 비변경 규칙을 함께 실행한다. 정합 증명이 없거나 전역 영향이면 전체 활성 RuleBox를 실행한다. Python은 이 선택을 계획할 뿐 규칙 성립 여부를 판단하지 않는다.
3. 활성 ABox의 사실 소속은 `Worldview Manifest -> active scope pointer -> immutable scope generation`으로 판정한다. 재사용한 scope row의 `worldviewManifestId`는 최초 생성 provenance일 뿐 현재 소속 판정에 쓰지 않는다. ABox와 InferenceBox 포인터 전환은 추론 완료 뒤 즉시 끝낸다. 이전 세대, 실패 후보, 오래된 InferenceBox의 삭제는 실시간 경로에서 수행하지 않고, reasoning queue가 비어 있는 유휴 유지보수 작업으로 옮긴다. 유지보수는 동일한 TypeDB writer lease를 획득해야 하므로 활성 추론 세대를 삭제할 수 없다.
4. 직전 투영 시간이 긴 경우 다음 일반 투영 간격은 `max(기본 간격, 직전 시간 x backpressure factor)`로 늘린다. 연구 근거, 캘린더, 높은 검토 단계 이벤트는 이 동적 지연을 우회한다. 대기 한도를 넘긴 종목도 한 번에 하나씩 다음 실행으로 이어서 처리하며, TypeDB 동시 실행 수는 늘리지 않는다. 이것은 데이터 손실이 아니라 중복 관측을 합치는 scheduler 정책이다.
5. 종목 없는 거시/정책 이벤트는 첫 종목에서 완료 처리하지 않는다. 보유 종목, 관심종목 순으로 분할 투영하고 모든 대상이 완료된 후에만 이벤트를 완료 처리한다.
6. 각 투영 감사에는 ABox 저장, 영향 계획, 직전 InferenceBox 확인, native inference, 포인터 전환, 품질 기록의 단계별 시간과 실제 native 대상 종목 수를 남긴다. 계획 대상 수와 실제 실행 대상 수는 별도로 보관해 운영 지표가 실행 범위를 과장하지 않게 한다.

운영 설정은 `ontologyReasoningBackpressureEnabled`, `ontologyReasoningBackpressureFactor`, `ontologyReasoningBackpressureMaxSeconds`, `ontologyReasoningFairnessMaxWaitSeconds`, `ontologyReasoningFairnessDrainEnabled`, `ontologyReasoningMaintenanceEnabled`, `ontologyReasoningMaintenanceIntervalSeconds`, `typedbNativeRuleSelectionEnabled`로 관리한다. RuleBox 선택 최적화가 꺼져 있거나 안전 증명이 부족한 경우에도 전체 native RuleBox materialization이 항상 정답 경로다.

## Data Quality And Coverage

외부 신호는 수집 결과에 `quality`, `freshness`, `provenance` 메타를 붙인다. 이 값은 `domain/external_signal_quality.py`에서 계산한다.

- `quality.dataState`: 심볼 커버리지, 공급자 상태, 에러 수를 바탕으로 한 자료 상태.
- `quality.symbolCoverage`: 현재 보유/관심 종목 중 외부 신호가 연결된 비율.
- `quality.sourceCoverage`: Alpha Vantage, CoinGecko, FRED, SEC EDGAR, OpenDART, GDELT News별 설정 여부, 수집 건수, 오류 메시지.
- `freshness`: 외부 신호의 마지막 수집 시각, 나이, stale 여부.
- `provenance`: 실제로 사용된 공급자와 현재 사용할 수 없는 공급자 목록.

이 메타는 ABox에서 `DataQuality`, `DataFreshness`, `Provenance` 노드로 들어간다. `secFilings`와 `dartDisclosures`는 종목별 `FundamentalObservation`, `DisclosureEvent`, `EarningsEvent`, `ValuationSignal`로도 연결한다. API 키나 공급자 설정이 없는 경우에는 가짜 데이터를 만들지 않고 품질/출처 메타에서 미커버 영역으로 남긴다.

포트폴리오 노출도 ABox에 확장된다.

- 외화 비중이 큰 경우 `FXPair`와 `CurrencyRisk`를 만든다.
- 섹터 비중 또는 같은 섹터 포지션이 커지면 `CorrelationRisk`와 `ConcentrationRisk`를 만든다.
- 이 노출은 AI가 투자 가설을 약화하거나 추가 확인할 수 있는 리스크 관계로 읽는다.

## Quality Samples

모니터링 사이클에서 온톨로지 그래프를 만들면 MySQL 운영 DB의 `ontology_ai_opinion_samples` 테이블에 품질 샘플을 남긴다.

- 전체 상태: 데이터 커버리지, 바운디드 컨텍스트 커버리지, reasoning card 준비도, 관계 연결 상태.
- 데이터 공백: reasoning card가 표시한 부족 데이터.
- 우선 점검 종목: `reviewLevel`이 `act` 또는 `immediate`인 종목.

이 샘플은 AI 의견 품질을 나중에 회귀 테스트하거나 운영 튜닝할 때 쓰는 로컬 히스토리다. 개인 계좌 데이터가 포함될 수 있으므로 git에 넣지 않는다.

## Ontology Lab

실험 환경은 운영 TypeDB schema function rule과 TypeDB를 직접 바꾸지 않는 후보 검증 단계다. 후보 규칙을 `candidateRules`로 저장하고, 최근 모니터 스냅샷에서 만든 ABox facts-only 그래프와 규칙 구조를 검증한다. 파생 관계, 추론 trace, 자료·검증 상태 변화는 후보가 승인되어 TypeDB schema function으로 동기화되고 `run_rulebox` materialization을 실행한 뒤에만 확인한다. API/화면 이름에 남아 있는 `RuleBox`는 호환 이름이며, 운영 의미는 TypeDB schema function rule이다.

CLI:

```bash
npm run python:ontology-lab -- list
npm run python:ontology-lab:status
npm run python:ontology-lab -- create --payload-file ./experiment.json
npm run python:ontology-lab -- suggest --symbols AAPL --activate --run
npm run python:ontology-lab -- auto-suggest
npm run python:ontology-lab -- activate --id <experiment-id>
npm run python:ontology-lab:once
npm run python:ontology-lab:watch
npm run python:ontology-lab -- run --id <experiment-id>
npm run python:ontology-lab -- apply --id <experiment-id> --approve-needs-review --reviewed-by local-user
npm run python:ontology-lab -- pause --id <experiment-id>
npm run python:ontology-lab -- report --id <experiment-id>
```

`activate`된 실험은 service manager의 `ontology-lab` worker가 계속 확인한다. 기본 주기는 `ontologyLabIntervalSeconds=300`이며, `npm run python:service:restart`를 실행하면 다른 Python worker와 함께 시작된다. 반복 실행은 `lastSnapshotKey`를 보고 같은 모니터 스냅샷에서는 건너뛰고, 새 계좌/관심종목 스냅샷이 들어오면 ABox snapshot과 candidate rule 구조를 다시 점검한다. 각 실행 요약은 `runHistory`에 보관하고, 실제 파생 관계 변화는 TypeDB materialization required 상태로 표시한다. 같은 워커는 `ontologyRuleCandidateAiEnabled=1`이면 `ontologyRuleCandidateAiIntervalMinutes` 주기로 AI native-rule 후보를 자동 제안하고, 생성된 실험을 즉시 한 번 검증한 뒤 활성 상태로 둔다. 웹의 `AI 실험 제안` 액션도 같은 제안+검증+활성화 흐름을 수동으로 호출한다.

API:

- `GET /api/ontology/experiments`
- `GET /api/ontology/experiments/status`
- `POST /api/ontology/experiments`
- `POST /api/ontology/experiments/once`
- `POST /api/ontology/experiments/suggest`
- `GET /api/ontology/experiments/{id}`
- `POST /api/ontology/experiments/{id}/run`
- `POST /api/ontology/experiments/{id}/apply`
- `POST /api/ontology/experiments/{id}/activate`
- `POST /api/ontology/experiments/{id}/pause`

샌드박스 실행 결과의 `sandbox.mutatedOperationalRuleBox`와 `sandbox.mutatedTypeDB`는 항상 `false`여야 한다. 운영 반영은 완료된 샌드박스 결과, 그래프 실행 이력, `proposedOntologyChanges`가 있는 실험에서만 `apply` 단계로 수행한다. `apply`는 후보 관계 규칙을 RuleBox semantic profile에 저장하고, 제안된 TBox class/relation/decision stage를 그래프 저장소에 반영한 뒤 TypeDB schema function sync와 InferenceBox materialization을 다시 실행한다. 런타임 실험 기록은 `data/ontology-lab.json`에 저장되며 git에 넣지 않는다.

`promotionReadiness.status=promote-candidate`는 바로 `apply`할 수 있다. `needs-review` 결과는 운영 반영 전에 `reviewApproved`, `reviewedBy`, `reviewReason` 승인 payload가 필요하며, 웹 실험 탭은 확인창을 거쳐 이 승인 기록을 남긴다. `needs-data`나 실행되지 않은 AI 제안은 운영 반영할 수 없다.

## Runtime Settings

관계 규칙과 프롬프트는 런타임 설정으로 관리한다.

- `ontologyRelationRules`: `ruleId | label | condition | relationType | signalType | promptHint` 형식의 관계 규칙 목록. 운영에서는 RuleBox semantic profile로 저장된 뒤 TypeDB schema function으로 컴파일되어 InferenceBox materialization에 쓰인다.
- `aiPromptTemplates`: 투자 인사이트와 근거 신호별 AI 의견/질문 템플릿. 실제 투자 발송 타입은 `investmentInsight`이며, `modelBuy`, `holdingTiming`, `monitorTrendChange`, `externalDartDisclosure` 같은 타입은 인사이트 합성에 들어가는 근거 신호 템플릿으로 유지한다. 사용자가 일부만 수정해도 나머지는 기본 템플릿을 유지한다.
- `aiPromptPolicy`: 제공 데이터만 사용, 부족 데이터 표시, 투자 판단과 발송 우선도 분리 같은 공통 가드레일.

설정의 관계 규칙과 프롬프트는 UI, 메시지, AI 리뷰 정보의 운영 계약이다. 새 투자 의미를 추가할 때는 TBox/ABox fact, RuleBox semantic profile, TypeDB schema function materialization, InferenceBox payload, AI prompt contract, 알림 문구를 함께 갱신해야 한다.

## AI Valuation Proposals

밸류에이션은 사용자 입력이 없어도 종목 타입별 AI 초안을 만들 수 있다. 초안은 `ActiveValuation`으로 저장해 화면과 AI 설명에는 사용할 수 있지만, `valuationDecisionEligible=false`로 저장되어 사용자 승인 전에는 TypeDB의 저평가 기회·고평가 위험 추론을 작동시키지 않는다. 항상 `AIValuationProposal`과 `UserValuationReview`를 함께 만들고 `ai_applied_pending_review` 상태를 드러낸다. 메시지는 이를 "AI 제안 자동 적용 · 사용자 검토 전"과 "참고만 사용 · 매수·매도 추론에서 제외"로 표시해야 한다.

현재 종목 타입별 초안 모델:

- `ai-bitcoin-treasury-nav-scenarios`: MSTR 같은 비트코인 프록시 종목. BTC 보유량·희석주식수·순부채·우선주 부담이 모두 있을 때만 NAV 범위를 계산한다. 입력이 없으면 가격 추세를 적정가로 바꾸지 않고 계산을 보류한다.
- `ai-preferred-income-yield-scenarios`: STRC 같은 우선주/인컴형. 연간 배당을 보수적·기준·낙관 요구수익률로 나눠 적정가 범위를 만든다.
- `ai-semiconductor-eps-per-scenarios`: 삼성전자, SK하이닉스 같은 반도체 종목. 연간·TTM·선행 12개월 EPS와 반도체 유형별 PER 범위를 사용한다. 5/20/60일 가격 흐름은 적정가 계산에 넣지 않고 별도 추세 근거로만 쓴다.
- `ai-growth-eps-per-scenarios`: Apple, NVIDIA, Tesla 같은 성장/플랫폼 종목. 연간 호환 EPS와 종목 유형별 PER 범위에 금리 부담을 적용한다. 성장률·마진·피어 배수가 없으면 부족 데이터와 `partial` 자료 상태를 남긴다.

모든 밸류에이션 ABox에는 `fairValueLow`, `fairValueBase`, `fairValueHigh`, 세 시나리오의 안전마진, `epsPeriod`, `multiplePeriod`, `valuationAsOf`, `valuationFreshnessStatus`, `valuationDataState`, `valuationReliabilityState`, `valuationDecisionEligible`을 저장한다. 분기 EPS와 연간 PER를 섞는 계산은 허용하지 않는다. TypeDB 저평가·고평가 규칙은 자료 상태가 `sufficient`이고, 검증 상태가 `ready`이며, 오래되지 않은 적정가만 사용한다. 저평가 후보는 기준 시나리오 안전마진 15% 이상이면서 보수적 시나리오 안전마진도 0% 이상이어야 한다.

검증 가능한 모델이 둘 이상이면 `ValuationConsensus` ABox가 기준 적정가 차이를 비교한다. 모델 간 차이가 합의 가격의 35%를 넘으면 `valuationConsensusStatus=conflict`로 저장하고 해당 사이클의 밸류에이션 매매 추론을 막는다. 모델별 결과와 부족 데이터는 그대로 남겨 사용자가 어느 가정이 충돌했는지 확인할 수 있게 한다.

`aiValuationCurrentPriceAnchorEnabled`는 기본 꺼짐이다. 이 값을 켜면 마지막 수단으로 `AI 초기 기준가 = 현재가`가 생성될 수 있지만, 알림에서는 `입력 부족 · 임시 기준`으로만 보여야 한다.

사용자 검토는 `valuationReviewOverrides` 설정으로 먼저 지원한다. 형식은 `MSTR,user_approved,메모` 또는 `MSTR=user_rejected`이며, 승인/수정 승인 시 AI 제안의 검증 상태를 높이고 거절 시 해당 AI 제안을 활성 밸류에이션에서 제외한다. 버튼형 검토 UI를 추가하더라도 이 설정과 같은 상태값(`user_approved`, `user_modified`, `user_rejected`)을 사용해야 한다.

## Graph Store Configuration

```bash
ONTOLOGY_TYPEDB_ENABLED=1
TYPEDB_ADDRESS=127.0.0.1:1729
TYPEDB_USER=admin
TYPEDB_PASSWORD=password
TYPEDB_DATABASE=orbit_alpha_ontology
TYPEDB_TLS_ENABLED=0
TYPEDB_TIMEOUT_SECONDS=20
```

TypeDB를 쓰려면 런타임에 `typedb-driver` Python package와 TypeDB 서버가 필요하다. 저장 실패는 모니터링 사이클을 막지 않고 snapshot metadata에 결과만 남긴다.

`ONTOLOGY_TYPEDB_ENABLED=1`이면 project service manager가 TypeDB 서버를 조건부 worker로 포함한다. 로컬 TypeDB 데이터는 `data/typedb-data/`에, TypeDB 자체 로그는 `data/typedb-logs/`에, service manager stdout/stderr는 `data/typedb.log`에 남긴다.

저장소는 그래프 저장 전에 다음 스키마 준비를 best effort로 실행한다.

- `OntologyEntity`, `OntologyEvidence`, `OntologyBelief`, `OntologyOpinion`, `OntologyReasoningCard`의 id 유니크 제약.
- `OntologyEntity(ontologyBox, kind)`, `OntologyEntity(updatedAt)`, `OntologyOpinion(symbol)`, `OntologyReasoningCard(symbol)` 인덱스.

그래프 저장소 스키마 준비가 실패해도 원본 수집 흐름은 막지 않으며, 결과에는 `schemaPrepared`와 `schemaReason`을 남긴다.

## AI Prompt Contract

AI에는 다음 데이터를 함께 전달한다.

- Subject: 종목, 시장, 섹터, 계좌 맥락.
- Facts: 손익률, 현재가, 이동평균, 거래량, 체결강도, 투자자별 수급, 외부 신호.
- Matched rules: 성립한 관계 규칙, 관계 타입, 신호 타입, 강도, 근거.
- Missing data: 없는 데이터와 판단 영향.
- Prompt policy: 없는 데이터 추정 금지, 투자 판단과 발송 우선도 분리.
- Relation graph: 필요할 때 규칙 구조, 현재 데이터, 근거, 판단 근거, AI 의견을 함께 전달한다.
- Bounded contexts: 각 노드/관계가 투자 핵심, 관측 데이터, 전략 가설, 리스크, 추론 인사이트, 운영/알림 중 어디에 속하는지 전달한다.

프롬프트는 매수/매도 명령을 확정하지 않고, 관계와 근거끼리의 충돌을 설명하는 투자 의견을 요구한다. API 키, 토큰, 계좌번호 같은 민감 정보는 전달하지 않는다.

## Extension Rules

- 새 TBox 클래스, 관계 타입, 바운디드 컨텍스트 규칙은 `domain/ontology_tbox.py`에 추가한다.
- 새 ABox 인스턴스 생성은 `domain/ontology.py`에 추가하고, `tboxClass` 또는 `tboxClasses`를 지정해 `boundedContext`가 자동 부여되게 한다.
- 새 런타임 판단은 먼저 RuleBox semantic profile, TypeDB schema function materialization, InferenceBox payload, 온톨로지 relation catalog에 추가한다. Python `domain/ontology_relation_reasoning.py`는 운영 fallback이나 실험 추론기가 아니라 프롬프트 조립과 read model formatting 보조 로직으로만 사용한다.
- 새 AI 설명은 `aiPromptTemplates`와 `aiPromptPolicy`의 계약을 함께 갱신한다.
- 외부 뉴스, 공시, 매크로 데이터는 먼저 `ExternalSignal` 또는 구체 클래스(`NewsEvent`, `DisclosureEvent`, `MacroIndicator`)의 ABox 관측값으로 만들고, 필요하면 `Evidence`, `Belief`, `Insight`로 파생한다.
- 새 관계가 AI 의견을 바꿔야 하면 relation properties에 `polarity`, `opinionImpact`, `riskImpact`, `supportImpact`, `aiInfluenceLabel`을 명시한다.
- 외부 공급자 연동을 새로 추가하면 `domain/external_signal_quality.py`의 `SOURCE_KEYS`와 품질 계산도 함께 갱신한다.
- AI 의견 품질 지표를 바꾸면 `domain/ontology_quality.py`와 `ontology_ai_opinion_samples` 소비 화면/문서를 같이 갱신한다.
- 합산 점수나 확률을 다시 최종 판단 주체로 만들지 않는다. 원시 수치와 공식은 사실 설명 또는 밸류에이션 계산에만 사용한다.
- TypeDB 저장 실패가 실시간 알림, snapshot 저장, notification outbox를 막으면 안 된다.
