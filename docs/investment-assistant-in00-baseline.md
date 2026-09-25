# IN-00 투자 비서 기준선과 데이터 준비도

- 조사 시각: 2026-09-25 10:30~10:45 KST
- 조사 시작 HEAD: `14376cd126eb8fc17ad5c6c701a00d6f58f82e57`
- 범위: PLTR, NVDA를 **출시 종목이 아닌 검증 후보**로 비교
- 방식: 운영 MySQL의 `READ ONLY` transaction과 현재 코드의 순수 read model/valuation 계산만 사용
- 금지 작업: 외부 수집, cache 갱신, `maintenance --apply`, release 변경, 알림 생성·발송

이 보고서는 “원천 데이터가 있다”와 “투자 판단에 쓸 수 있다”를 구분한다. 조사 코드가 만든 운영 쓰기·수집·발송은 0이다. 기존 worker는 조사 중에도 독립적으로 실행 중이었으므로 시스템 전체 행 증가량을 이 조사 결과로 주장하지 않는다. 계좌 식별자, 보유 수량, 메시지 본문, 설정 및 credential은 읽거나 기록하지 않았다.

## 1. 런타임 기준선

| 항목 | 확인 결과 |
| --- | --- |
| 활성 reasoning deployment | `ontology-v2-mysql-20260922`, family `ontology-investment-brain`, engine `v2`, status `active` |
| 전달 deployment | 활성 deployment와 동일 |
| 후보 deployment | 없음 |
| control version | 187, 2026-09-23T13:31:19.904918Z |
| 최근 24시간 shadow job/comparison | 0 / 0 |
| 회사 cache | `company_knowledge`, 2026-09-16T02:18:55.563438Z |
| 최근 monitor snapshot | 2026-09-25T01:38:21.323029Z |

현재 `external_fact_current`의 실제 상태 열은 `availability`와 `quality_json`이다. 계획 초안에서 조사 후보로 적은 `freshness_state` 열은 없다. 이후 감사와 migration은 실제 schema를 기준으로 해야 한다.

## 2. 후보별 원천 revision

아래 prefix는 같은 입력을 다시 찾기 위한 감사 표식이며 원문 전체 revision은 운영 저장소에 남아 있다.

| 후보 | 가격 | SEC company facts | 분석가 추정 | fundamental |
| --- | --- | --- | --- | --- |
| PLTR | `yfinance.price:3f8d565ae29eee9c`, 2026-09-25T01:16:15Z | `09293197998fc9f6`, source 2026-09-17 | `c3685a07a866d4f0`, 2026-09-21T21:29:17Z | `9c7ef3c3cd2938f3`, source 2026-08-06 |
| NVDA | `yfinance.price:e6bbc14cd592e240`, 2026-09-25T01:40:00Z | `580bad153defc596`, source 2026-09-23 | `17a33e11e5ac886a`, 2026-09-24T04:45:35Z | `3cefe24929282533`, source 2026-09-10 |

두 후보 모두 SEC document/submissions, yfinance news/options도 `availability=observed`로 존재한다. 이 사실은 기사와 가격 사이의 인과관계가 검증됐다는 뜻이 아니다.

## 3. 데이터 준비도

상태 단계는 `미확인 → 없음 → 원본 있음 → 정규화됨 → 검증됨 → 해당 모델에 적격`이다. 모델 승인과 행동 권한은 별도다.

| 입력/산출물 | PLTR | NVDA | 부족 이유와 다음 작업 |
| --- | --- | --- | --- |
| 현재 가격 관측 | 원본 있음 | 원본 있음 | point-in-time cutoff와 기업행동 조정 검증은 평가 bundle에서 고정해야 함 |
| 공식 재무 원천 | 원본 있음 | 원본 있음 | SEC company facts revision 확인 |
| 현재 코드로 재구성한 재무 기간 | 검증됨 | 검증됨 | 두 후보 모두 annual 4, interim 1, quarterly 4이며 새 재구성 결과에는 report contract가 있음 |
| 저장된 회사 cache 재무 기간 | 부적격 | 부적격 | cache의 모든 기간에 report contract가 없는데도 `checked/v2`로 표시됨. IN-01 격리 대상 |
| FY1/FY2 분석가 원천 | 원본 있음 | 원본 있음 | 원천에는 FY1/FY2와 reported range가 있으나 read-model 병합 뒤 estimate list/range가 사라짐. IN-05에서 병합 계약 수정 필요 |
| EPS×PER 입력 | 정규화 불완전 | 정규화 불완전 | 계산은 되지만 multiple이 관측 peer/history가 아닌 bootstrap prior이고 analyst sample state가 전달되지 않음 |
| 현재 valuation 계산 | reference-only | reference-only | 두 후보 모두 `calculated/partial`, fair value는 생성되지만 `valuationDecisionEligible=false` |
| 모델 승인 | 미승인 | 미승인 | scenario completeness와 multiple evidence 부족 |
| DCF 입력 | 미확인 | 미확인 | 이번 조사에서 FCF driver, WACC, terminal growth의 동일 cutoff bundle을 입증하지 않음 |
| 사건→가치 driver→가격 설명 | 미확인 | 미확인 | 뉴스 존재만 확인. 사건 동일성, 발표 선후, 반증 가설은 IN-09/10 대상 |

따라서 최초 R1 출시 종목은 아직 확정하지 않는다. PLTR과 NVDA는 원천 및 재무 정규화 경로를 검증할 수 있는 후보지만, 현재 평가 결과는 투자 판단에 적격하지 않다. 둘을 억지로 “데이터 준비 완료”로 올리지 않고 IN-05~08 통과 뒤 다시 선정한다.

## 4. 알림 기준선

표본 창은 2026-09-24T01:39:50Z부터 24시간이다. 개인 메시지 본문을 git 산출물로 옮기지 않고 구조화 상태만 집계했다.

| 지표 | 결과 |
| --- | --- |
| investmentInsight job | done 17, suppressed 1 |
| AI request | completed 40 |
| AI 결과 | ready/AI authored 5, conditional/AI authored 3, conditional/TypeDB fallback 32 |
| decision case | observation 43, review-only 49, suppressed 42 |
| 전체 채널 delivery attempt | delivered 73 |

반복 메시지 문제를 판단할 때 `AI request 성공`과 `AI authored 계약 통과`를 같은 지표로 세면 안 된다. 이 창에서는 40개 요청 중 32개가 TypeDB fallback이었다. 다만 이 집계만으로 17개 투자 메시지 각각의 중복, 사실 오류, 보내지 말았어야 할 알림, 빠진 알림을 확정할 수 없다. 그 의미 라벨은 보존된 source case와 성공 receipt를 같은 cutoff로 replay하는 IN-03/04 평가표에서 작성한다. 현재 수치를 정확도나 유용성 점수로 사용하지 않는다.

기준선 metric 정의:

- `execution success`: AI request가 terminal `completed`에 도달한 수
- `contract pass`: 결과가 `publication_contract_passed=1`인 수
- `fallback`: AI 문안 대신 typed graph fallback이 사용된 수
- `suppression`: 판단 또는 알림 job이 명시적으로 suppressed가 된 수
- `delivery success`: transport attempt가 delivered receipt를 가진 수
- `semantic duplicate`: 같은 사건·근거·판단 material fingerprint인데 새 결론 없이 다시 전달된 경우. 이번 집계에서는 아직 라벨하지 않음
- `miss`: 같은 창에 전달 적격인 material transition이 있었지만 성공 receipt가 없는 경우. 이번 집계에서는 아직 라벨하지 않음

## 5. 합성 재현 입력

| fixture | 개인정보 없는 입력 | 현재 상태 |
| --- | --- | --- |
| F-02 | 유효한 2025-12-31 annual과 report contract 없는 2026-06-30 annual | IN-01 회귀 테스트로 고정. 후자가 현재 상태를 밀어내면 실패 |
| F-03 | KST 자정 전후 두 판단과 서로 다른 cutoff | 재현 명세만 유지. IN-03에서 desired test와 수정 코드를 함께 추가 |
| F-04 | EPS -1/0/양수, FY1/FY2, single point/reported range | 재현 명세만 유지. IN-05에서 잘못된 기존 동작을 정답으로 고정하지 않고 추가 |

보호할 비대상 입력은 `TEST` 합성 종목과 명시적으로 선택되지 않은 모든 실제 종목이다. IN-02 복구 manifest가 생기기 전에는 운영 cache를 일괄 변경하지 않는다.

## 6. Shadow와 운영 격리 경계

| 경계 | 현재 계약 | 남은 위험/검증 |
| --- | --- | --- |
| source input | 활성 실행의 immutable snapshot과 projection runtime context를 job에 고정 | snapshot 보존 기간을 넘긴 replay는 완전 재현으로 주장할 수 없음 |
| queue/comparison | 운영 MySQL의 전용 `reasoning_engine_shadow_jobs`와 `reasoning_engine_comparisons` | DB와 CPU는 공유하므로 active queue가 실행 중이거나 비정상이면 candidate가 양보 |
| TypeDB | 기본 `orbit_alpha_ontology_shadow_v2` 후보 DB | 실제 후보 release 등록 때 DB binding과 rulebox fingerprint 재검증 |
| time series | 기본 `questdb-shadow` | 활성 backend와 같은 cutoff 비교가 필요 |
| shared world | `ShadowWorldProjectionSink`가 write를 차단 | `saved=false` assertion 유지 |
| delivery | `ShadowNotificationSink`, transport 없음 | candidate outcome의 `shadowDeliveryCount`가 0이 아니면 comparison 실패 처리 |
| cache/계정 read | 운영 read store와 immutable 입력을 공유 | 후보가 cache를 갱신하지 않는지 integration test 유지 |
| AI/자원 예산 | 별도 완전 격리가 입증되지 않음 | 후보가 없으므로 현재 실행하지 않음. IN-15에서 한도와 비용 계측 필요 |

현재 active와 delivery ID는 같고 candidate ID는 비어 있다. 이번 작업은 후보 등록·승격·shadow 실행을 하지 않는다.

## 7. IN-01 인계 결론

오류의 직접 원인은 과거 cache 행이 `financialReportingVersion=financial-reporting-v2` 문자열만 가지면 검증된 현재 사실처럼 병합될 수 있었던 점이다. 원천에서 다시 만든 행은 `financial-report-observation-v1`에 period, frequency, duration basis, provider, observation ID와 source revision을 갖지만 저장 cache의 오래된 행에는 이 계약이 없다.

IN-01은 새 선택 의미를 `financial-period-selection-v1`, cache 의미를 `company-knowledge-cache-v4-contract-qualified-periods`로 올린다. 계약이 없는 행은 원본 저장소에서 삭제하지 않고 현재 `financials`에서 제외하며 `financialIntegrity.excludedPeriods`에 이유를 남긴다. 이 단계는 운영 cache 복구나 과거 판단 수정이 아니다. 현재 데이터 적용과 영향 reconciliation은 IN-02의 preview manifest 이후에만 수행한다.
