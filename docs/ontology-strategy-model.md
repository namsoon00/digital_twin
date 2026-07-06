# Ontology Strategy Model

투자전략의 기준 모델은 온톨로지 우선 구조다. 기존 익절/손절 공식과 매수/매도 점수는 제거하지 않고 `legacyModelRole=supporting-evidence`로 격하시켜 보조 evidence로만 사용한다.

## Domain Vocabulary

- `Portfolio`: 계좌 단위 투자 세계관의 루트.
- `Stock`: 보유 종목.
- `Sector`, `Market`, `Currency`: 종목이 속한 노출 축.
- `Risk`: 집중도, 손실 thesis 훼손, 추세 약화, 수급 악화, 데이터 품질 저하.
- `Opportunity`: 추세, 수급, 분산 효과처럼 thesis를 지지하는 관계.
- `Evidence`: 기존 점수, 포트폴리오 노출, 추세, 수급, 데이터 품질.
- `Belief`: evidence에서 도출된 지지 또는 위험 판단.
- `Opinion`: 온톨로지 관계로 만든 종목별 투자 의견.

## TBox And ABox

이 모델은 TBox와 ABox를 분리한다.

- `TBox`: 투자 온톨로지의 스키마 계층이다. `Portfolio`, `Stock`, `Sector`, `Risk`, `Evidence`, `Belief`, `Opinion` 같은 클래스와 `HOLDS`, `EXPOSED_TO`, `CONTRADICTS`, `HAS_EVIDENCE` 같은 관계 타입, 그리고 추론 규칙을 정의한다.
- `ABox`: 현재 계좌 스냅샷에서 만들어진 실제 assertion 계층이다. 실제 보유 종목, 섹터 노출, 수급 evidence, 추세 evidence, 위험 belief, 종목별 opinion이 여기에 들어간다.

AI 프롬프트에는 TBox와 ABox를 함께 전달한다. AI는 TBox를 해석 규칙으로 읽고, ABox를 현재 투자 상태의 사실 집합으로 읽어야 한다. Neo4j 저장 시 노드와 관계에는 `ontologyBox` 속성을 붙여 `TBox`와 `ABox`를 구분한다.

## Relation Types

- `HOLDS`: 포트폴리오가 종목을 보유한다.
- `HOLDS_CASH`: 포트폴리오가 현금을 보유한다.
- `BELONGS_TO`: 종목이 섹터에 속한다.
- `TRADED_IN`: 종목이 시장에 상장되어 있다.
- `DENOMINATED_IN`: 종목 평가 통화.
- `EXPOSED_TO`: 포트폴리오 또는 종목이 리스크/섹터에 노출되어 있다.
- `SUPPORTED_BY`: 종목 thesis를 지지하는 기회 관계.
- `CONTRADICTS`: 기존 점수, 추세, 수급, 집중도 사이에 충돌이 있다.
- `USES_EVIDENCE_FROM`: 기존 점수 모델을 보조 evidence로 사용한다.
- `REQUESTS_OPINION_FROM`: AI 투자 의견 컨텍스트로 넘긴다.
- `HAS_EVIDENCE`, `HAS_BELIEF`, `HAS_OPINION`: Neo4j 저장용 세부 관계.

## Runtime Flow

1. Toss 계좌와 시장 데이터를 `Position`, `PortfolioSummary`로 정규화한다.
2. 기존 공식 기반 `exitPressure`, `profitTakePressure`, `lossCutPressure`를 계산한다.
3. `domain/ontology.py`가 포트폴리오 온톨로지 그래프를 만든다.
4. TBox 클래스/관계 정의와 ABox 인스턴스 assertion을 같은 그래프 payload에 담는다.
5. 종목별 `OntologyOpinion`을 생성해 `DecisionItem.ontology_opinion`과 `DecisionItem.ai_context`에 붙인다.
6. 실시간 모니터링은 결정 변화 알림 metadata에 `ontologyReviewContext`를 포함한다.
7. 모델 리뷰 워커는 이 컨텍스트를 AI 프롬프트에 넣어 세계관, 관계, 충돌, 데이터 검증, 다음 실험을 분석한다.
8. `NEO4J_URI`가 설정되어 있으면 `infrastructure/neo4j_ontology.py`가 동일 그래프를 Neo4j에 저장한다.

## Neo4j Configuration

```bash
ONTOLOGY_NEO4J_ENABLED=1
NEO4J_URI=http://127.0.0.1:7474
NEO4J_USER=neo4j
NEO4J_PASSWORD=...
NEO4J_DATABASE=neo4j
NEO4J_TIMEOUT_SECONDS=8
```

HTTP URI는 Neo4j transactional endpoint로 전송한다. `bolt://` 또는 `neo4j://` URI를 쓰려면 런타임에 `neo4j` Python driver가 설치되어 있어야 한다. 저장 실패는 모니터링 사이클을 막지 않고 snapshot metadata에 결과만 남긴다.

## AI Prompt Contract

AI에는 다음 데이터를 함께 전달한다.

- 포트폴리오 세계관: 지배 섹터, 현금, risk/support belief count, 충돌 수.
- TBox: 클래스, 관계 타입, 추론 규칙.
- ABox: 현재 포트폴리오 assertion count와 실제 instance 집합.
- 관계 그래프: 포트폴리오-종목-섹터-시장-통화-리스크-기회 관계.
- Evidence: 기존 점수, 노출, 추세, 수급, 데이터 품질.
- Belief: 지지/위험 판단.
- Opinion: 종목별 thesis, 충돌, 주요 리스크, 기회, 온톨로지 관계 압력.

프롬프트는 매수/매도 명령을 확정하지 않고, 관계와 evidence 충돌을 설명하는 투자 의견을 요구한다. API 키, 토큰, 계좌번호 같은 민감 정보는 전달하지 않는다.

## Extension Rules

- 새 리스크나 기회는 `domain/ontology.py`의 vocabulary와 relation으로 추가한다.
- 외부 뉴스, 공시, 매크로 데이터는 먼저 `Evidence`로 정규화한 뒤 belief를 만든다.
- 기존 공식 점수를 다시 최종 판단 주체로 올리지 않는다. 공식은 보조 evidence로만 둔다.
- Neo4j 저장 실패가 실시간 알림, snapshot 저장, notification outbox를 막으면 안 된다.
