# Company Event Integrity

뉴스, 공시, 신고서와 기업행동은 서로 다른 문서이지만 같은 기업 사건을 설명할 수
있다. 문서가 새로 수집됐다는 사실과 투자 사건이 새로 발생했다는 사실을 동일하게
취급하지 않는다.

## Contract

모든 사건 자료는 `company-event-observation-v1`을 사용한다.

- `eventId`: 공급자가 제공한 사건·문서 식별자를 우선 사용하며, 없을 때만 종목,
  사건 유형, 정규화 제목, 효력일과 보고기간으로 결정론적 ID를 만든다.
- `kind`: `news`, `disclosure`, `filing`, `corporate-action`을 구분한다.
- `publishedAt`, `announcedAt`: 원천이 공개·발표한 시각이다. 수집 시각으로 채우지
  않는다.
- `effectiveFrom`, `effectiveTo`, `recordDate`, `reportingPeriod`: 발표 시각과 사건의
  실제 효력·대상 기간을 분리한다.
- `lifecycleState`: announced, upcoming, active, completed, cancelled, unknown이다.
- `revisionState`: original, corrected, restated, withdrawn, unknown이다.
- `sourceDocumentId`, `correctsSourceDocumentId`: 원문과 명시적인 정정 대상을
  구분한다. 공급자가 정정 대상을 주지 않으면 제목 유사성만으로 연결하지 않는다.
- `sourceReferences`: dataset, 내부 immutable revision, provider revision, payload hash,
  schema version과 source-as-of를 보존한다.
- `observationId`: 위 의미와 exact source reference를 포함한 content-addressed ID다.

수치·본문·제목의 투자 방향은 이 계약에서 결정하지 않는다. 사건 계약은 ABox 사실,
`ResearchEvidence`, AI 입력과 웹 상세가 같은 자료를 가리키게 하는 경계다.

## Delivery

OpenDART와 SEC 문서뿐 아니라 공식 배당, 주식 발행·보호예수, 주주 권리 일정도
`external_fact_projection_deliveries`의 leased membership을 사용한다. 기업행동은
직접 알림을 만들지 않고 canonical `ResearchEvidence`와 ABox를 갱신한다. 이후
TypeDB 규칙과 AI 판단이 실제 투자 의미를 결정한다.

현재 공식 projector 버전은 `official-evidence-projection-v5-company-events`다.
버전 변경 시 current fact backfill은 알림 권한 없이 실행되고, 새 fact change만 정상
추론 후보가 된다.

## ABox Provenance

기업행동 ABox의 `DataSource`는 더 이상 사건 유형만으로 식별하지 않는다. exact
dataset/revision을 식별자로 사용하고 payload hash와 source-as-of를 함께 보존한다.
따라서 동일한 배당이나 증자 사건이 정정돼도 어느 원천 revision을 사용했는지
추적할 수 있다.

## Acceptance Checks

- 정정 공시는 original로 분류되지 않는다.
- 정정 대상 ID가 없으면 유사한 과거 공시를 임의로 supersede하지 않는다.
- `YYYYMMDD` 날짜는 의미를 바꾸지 않고 ISO 날짜로 정규화한다.
- 수집 시각은 발표일이나 효력일을 대신하지 않는다.
- read model, `ResearchEvidence`, ontology projection에서 source revision과 payload
  hash가 같다.
- 기업행동 current backfill과 재처리는 직접 고객 알림을 만들지 않는다.
- 사건이 없는 대형 재무 payload에는 사건 계약 처리나 깊은 복사를 수행하지 않는다.
