# Ontology Strategy Model

투자전략의 기준 모델은 관계 규칙 구조다. 기존 익절/손절 공식과 매수/매도 점수는 최종 판단 주체가 아니며 `legacyModelRole=supporting-evidence`로 보조 근거에만 남긴다.

## Domain Vocabulary

TBox 정의는 `python_service/digital_twin/domain/ontology_tbox.py`에 둔다. 그래프 데이터 계약은 `domain/ontology_contracts.py`, TBox/ABox payload와 bounded-context 속성 부여는 `domain/ontology_schema.py`, reasoning card와 AI 입력 read model은 `domain/ontology_prompting.py`가 맡는다. `domain/ontology.py`는 이 조각들을 사용해 현재 계좌 스냅샷을 ABox 그래프로 조립한다.

바운디드 컨텍스트는 6개다.

- `investment-core`: `Account`, `Portfolio`, `Instrument`, `Stock`, `Position`, `Watchlist`, `Cash`, `Sector`, `Market`, `Currency`, `MarketExposure`.
- `observation-data`: `Observation`, `PriceObservation`, `TechnicalObservation`, `FlowObservation`, `ExternalSignal`, `DataSource`, `DataFreshness`, `Provenance`, `SignalHorizon`, `MissingData`.
- `strategy-thesis`: `Strategy`, `InvestmentThesis`, `EntryCondition`, `ExitCondition`, `RiskManagementRule`, `RebalancingRule`, `PositionSizingRule`, `ModelScore`, `LegacyScoreModel`, `RuntimeSetting`.
- `risk-exposure`: `Risk`, `MarketRisk`, `LiquidityRisk`, `ConcentrationRisk`, `CurrencyRisk`, `EventRisk`, `DataQualityRisk`, `ModelRisk`, `RegimeRisk`.
- `reasoning-insight`: `Signal`, `Evidence`, `Belief`, `Opinion`, `Opportunity`, `Contradiction`, `Insight`, `ReasoningCard`, `AIReview`.
- `operations-dispatch`: `DataPipeline`, `CollectionSchedule`, `CollectionPolicy`, `AnalysisJob`, `ReasoningCycle`, `NotificationDispatch`, `CooldownPolicy`, `NoveltyPolicy`, `SuppressionPolicy`, `MarketSession`.

## TBox And ABox

이 모델은 TBox와 ABox를 분리한다.

- `TBox`: 투자 관계 분석의 규칙 구조다. `Portfolio`, `Stock`, `Sector`, `Risk`, `Evidence`, `Belief`, `Opinion` 같은 클래스와 `HOLDS`, `EXPOSED_TO`, `CONTRADICTS`, `HAS_EVIDENCE` 같은 관계 타입, 그리고 판단 규칙을 정의한다.
- `ABox`: 현재 계좌 스냅샷에서 만들어진 실제 데이터 계층이다. 실제 보유 종목, 섹터 노출, 수급 근거, 추세 근거, 위험 판단 근거, 종목별 의견이 여기에 들어간다.

AI 프롬프트에는 TBox, `boundedContexts`, ABox, operational ontology, reasoning card를 함께 전달한다. AI는 TBox를 해석 규칙으로 읽고, ABox를 현재 투자 상태의 사실 집합으로 읽어야 한다. TypeDB 저장 시 엔티티와 관계에는 `ontologyBox` 속성을 붙여 `TBox`와 `ABox`를 구분하고, ABox 노드/관계에는 가능하면 `boundedContext`도 붙인다.

데이터 수집 주기 자체도 세계관의 일부다. `marketSnapshot`, `watchlistSnapshot`, `externalSignals`는 ABox의 `DataPipeline` 노드이며, 각 파이프라인은 `CollectionSchedule`, `DataFreshness`, `CollectionPolicy`, `ReasoningCycle`에 연결된다. 즉 3분/5분/30분 같은 값은 TBox 클래스가 아니라, `CollectionSchedule` 클래스의 현재 실행 인스턴스다.

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
- `CONTRADICTS`: 기존 점수, 추세, 수급, 집중도 사이에 충돌이 있다.
- `USES_EVIDENCE_FROM`: 기존 점수 모델을 보조 근거로 사용한다.
- `REQUESTS_OPINION_FROM`: AI 투자 의견 정보로 넘긴다.
- `HAS_EVIDENCE`, `HAS_BELIEF`, `HAS_OPINION`: TypeDB/그래프 저장소 저장용 세부 관계.

## Runtime Flow

1. Toss 계좌, 시장 데이터, 외부 API 데이터를 `Position`, `PortfolioSummary`, `externalSignals`로 정규화한다.
2. `domain/ontology_relation_reasoning.py`가 종목별 ABox fact, 부족 데이터, fallback 관계 추론 결과를 만든다. 운영 판단은 TypeDB RuleBox/InferenceBox 결과를 우선 사용한다.
3. `DecisionItem.decision`, `exit_pressure`, `decision_basis`는 관계 규칙 결과에서 나온다. `decision_basis`는 `ontologyRelationRules`다.
4. 기존 공식 기반 `profitTakePressure`, `lossCutPressure`, 매수/매도 점수는 보조 근거와 과거 비교용으로만 보관한다.
5. `domain/ontology.py`가 TBox/ABox 그래프와 `OntologyOpinion`을 만든다. 이때 `Strategy`, `InvestmentThesis`, `Observation`, `Risk`, `Insight`, `NotificationDispatch`까지 모두 ABox 노드로 만든다.
6. `DecisionItem.relation_rule_context`, `ai_prompt_context`, `ai_context`에 관계 규칙 결과와 프롬프트 입력 계약을 함께 붙인다.
7. 실시간 모니터링은 알림 metadata에 `ontologyRelationContext`, `ontologyPromptContext`, `ontologyReviewContext`를 포함한다.
8. 모델 리뷰 워커는 이 정보를 비동기 AI 프롬프트에 넣어 판단 변화 원인, 노이즈 가능성, 부족 데이터, 다음 규칙 개선안을 분석한다.
9. `infrastructure/ontology_projection.py`가 스냅샷을 온톨로지 read model로 투영한다. 런타임은 `infrastructure/ontology_graph_store.py`의 generic factory만 사용하고, 이 factory는 TypeDB repository 하나만 반환한다.

알림은 투자 이벤트 타입별 폴링으로 직접 발송하지 않는다. 기존 `modelBuy`, `holdingTiming`, `externalDartDisclosure` 같은 이벤트는 `investmentInsight.metadata.sourceAlertEvents`의 근거 신호로 남고, 최종 발송은 `Insight -> DISPATCHED_BY -> NotificationDispatch(investmentInsight)` 관계가 담당한다.

## Projection Boundary

온톨로지는 DDD aggregate의 저장소가 아니라 projection/read model이다. `Account`, `Monitoring`, `Research`, `Strategy`, `Notification` 같은 소유 컨텍스트가 사실과 이벤트를 만든 뒤, projection이 그 사실을 `TBox` 규칙에 맞는 `ABox` 노드와 관계로 변환한다. 이 경계 덕분에 계좌 저장, 알림 outbox, 모델 리뷰 큐의 트랜잭션은 각 context와 unit-of-work가 책임지고, TypeDB 저장이나 AI 프롬프트 생성 실패는 원본 업무 트랜잭션을 깨지 않는다.

Projection은 다음 용도로만 사용한다.

- TypeDB 그래프 조회와 시각화.
- reasoning card, AI inference packet, prompt payload 같은 읽기 모델 생성.
- 품질 샘플과 운영 콘솔용 진단 지표 생성.
- bounded context 사이의 의미 관계를 설명하는 audit trail.

새 투자 사실이 필요하면 projection에 직접 상태를 추가하지 말고, 먼저 소유 context의 aggregate/event/repository에 사실을 남긴 뒤 projection 변환을 확장한다.

## Data Quality And Coverage

외부 신호는 수집 결과에 `quality`, `freshness`, `provenance` 메타를 붙인다. 이 값은 `domain/external_signal_quality.py`에서 계산한다.

- `quality.score`: 심볼 커버리지, 공급자 상태, 에러 수를 합산한 외부 신호 품질 점수.
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

- 전체 점수: 데이터 커버리지, 바운디드 컨텍스트 커버리지, reasoning card 준비도, 관계 밀도.
- 데이터 공백: reasoning card가 표시한 부족 데이터.
- 고압력 종목: `ontology_pressure >= 55`인 종목.

이 샘플은 AI 의견 품질을 나중에 회귀 테스트하거나 운영 튜닝할 때 쓰는 로컬 히스토리다. 개인 계좌 데이터가 포함될 수 있으므로 git에 넣지 않는다.

## Ontology Lab

1차 실험 환경은 운영 RuleBox와 TypeDB를 직접 바꾸지 않는 로컬 샌드박스다. 후보 규칙을 `candidateRules`로 저장하고, 최근 모니터 스냅샷에서 만든 ABox facts-only 그래프에 현재 RuleBox와 후보 RuleBox를 각각 실행해 파생 관계, 추론 trace, 품질 점수 변화를 비교한다.

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

`activate`된 실험은 service manager의 `ontology-lab` worker가 계속 확인한다. 기본 주기는 `ontologyLabIntervalSeconds=300`이며, `npm run python:service:restart`를 실행하면 다른 Python worker와 함께 시작된다. 반복 실행은 `lastSnapshotKey`를 보고 같은 모니터 스냅샷에서는 건너뛰고, 새 계좌/관심종목 스냅샷이 들어오면 다시 샌드박스 리플레이를 수행한다. 각 실행 요약은 `runHistory`에 보관한다. 같은 워커는 `ontologyRuleCandidateAiEnabled=1`이면 `ontologyRuleCandidateAiIntervalMinutes` 주기로 AI RuleBox 후보를 자동 제안하고, 생성된 실험을 즉시 한 번 샌드박스 실행한 뒤 활성 상태로 둔다. 웹의 `AI 실험 제안` 액션도 같은 제안+실행+활성화 흐름을 수동으로 호출한다.

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

샌드박스 실행 결과의 `sandbox.mutatedOperationalRuleBox`와 `sandbox.mutatedTypeDB`는 항상 `false`여야 한다. 운영 반영은 완료된 샌드박스 결과, 그래프 실행 이력, `proposedOntologyChanges`가 있는 실험에서만 `apply` 단계로 수행한다. `apply`는 후보 RuleBox를 운영 RuleBox에 저장하고, 제안된 TBox class/relation/decision stage를 그래프 저장소에 반영한 뒤 RuleBox 추론을 다시 실행한다. 런타임 실험 기록은 `data/ontology-lab.json`에 저장되며 git에 넣지 않는다.

`promotionReadiness.status=promote-candidate`는 바로 `apply`할 수 있다. `needs-review` 결과는 운영 반영 전에 `reviewApproved`, `reviewedBy`, `reviewReason` 승인 payload가 필요하며, 웹 실험 탭은 확인창을 거쳐 이 승인 기록을 남긴다. `needs-data`나 실행되지 않은 AI 제안은 운영 반영할 수 없다.

## Runtime Settings

관계 규칙과 프롬프트는 런타임 설정으로 관리한다.

- `ontologyRelationRules`: `ruleId | label | condition | relationType | signalType | promptHint` 형식의 관계 규칙 목록.
- `aiPromptTemplates`: 투자 인사이트와 근거 신호별 AI 의견/질문 템플릿. 실제 투자 발송 타입은 `investmentInsight`이며, `modelBuy`, `holdingTiming`, `monitorTrendChange`, `externalDartDisclosure` 같은 타입은 인사이트 합성에 들어가는 근거 신호 템플릿으로 유지한다. 사용자가 일부만 수정해도 나머지는 기본 템플릿을 유지한다.
- `aiPromptPolicy`: 제공 데이터만 사용, 부족 데이터 표시, 투자 판단과 발송 우선도 분리 같은 공통 가드레일.

코드의 기본 matcher는 안전한 기본 규칙을 실행한다. 설정의 관계 규칙과 프롬프트는 UI, 메시지, AI 리뷰 정보의 운영 계약이며, 새 규칙 matcher를 추가할 때도 이 키를 함께 갱신해야 한다.

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
- 새 런타임 판단은 먼저 TypeDB RuleBox/InferenceBox와 온톨로지 relation catalog에 추가한다. Python `domain/ontology_relation_reasoning.py`는 그래프 저장소가 비었을 때의 bootstrap fallback만 보완한다.
- 새 AI 설명은 `aiPromptTemplates`와 `aiPromptPolicy`의 계약을 함께 갱신한다.
- 외부 뉴스, 공시, 매크로 데이터는 먼저 `ExternalSignal` 또는 구체 클래스(`NewsEvent`, `DisclosureEvent`, `MacroIndicator`)의 ABox 관측값으로 만들고, 필요하면 `Evidence`, `Belief`, `Insight`로 파생한다.
- 새 관계가 AI 의견을 바꿔야 하면 relation properties에 `polarity`, `opinionImpact`, `riskImpact`, `supportImpact`, `aiInfluenceLabel`을 명시한다.
- 외부 공급자 연동을 새로 추가하면 `domain/external_signal_quality.py`의 `SOURCE_KEYS`와 품질 계산도 함께 갱신한다.
- AI 의견 품질 지표를 바꾸면 `domain/ontology_quality.py`와 `ontology_ai_opinion_samples` 소비 화면/문서를 같이 갱신한다.
- 기존 공식 점수를 다시 최종 판단 주체로 올리지 않는다. 공식은 보조 근거로만 둔다.
- TypeDB 저장 실패가 실시간 알림, snapshot 저장, notification outbox를 막으면 안 된다.
