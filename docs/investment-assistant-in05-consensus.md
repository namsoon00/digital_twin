# IN-05 컨센서스 horizon·범위·표본·revision 보존

- 구현일: 2026-09-25, Asia/Seoul
- 시작 HEAD: `ffbc9746d`
- 범위: yfinance analyst 정규화, 외부 fact read model 병합, EPS 평가 근거
- 운영 변경: 외부 API 재수집, 과거 revision 변경, 모델 release 승격, 알림 생성 없음

## 확인한 손실 경로

yfinance normalizer는 이미 FY1/FY2, low/base/high, 분석가 수와 30일 수정 정보를 만들었다.
손실은 그 다음 두 경계에 있었다.

1. `yfinance.analyst`와 `yfinance.fundamental` fact가 같은 `companyOverviews`와
   `earningsReports`를 배열 단위 last-write-wins로 병합했다. 빈 fundamental
   `earningsEstimates`가 유효 analyst 배열을 지울 수 있었다.
2. 평가 근거 변환은 양수이고 기존 annual period 집합에 속한 EPS만 보존했다. 그 결과
   FY1 음수·0과 FY2 관측이 사라졌고, 구조화 FY1과 legacy `forwardEPS` scalar가 서로 다른
   horizon으로 중복될 여지가 있었다.

## 구현한 계약과 경계

새 수집 결과는 `earnings-estimate-observation-v2`를 사용한다. provider period와 정규화
horizon, sourceAsOf와 fetchedAt, range 종류, 표본 상태, 통화와 EPS/accounting basis,
revision 전후 값과 수정 종류를 별도 필드로 보존한다. low/high와 분석가 수가 없으면 만들지
않는다. 음수에서 양수로 바뀐 수정은 백분율을 만들지 않고 `sign-transition-negative-to-positive`
로 기록한다.

read model은 analyst fact의 내부 immutable revision, provider revision과 payload hash를
각 estimate의 `sourceReferences`에 연결한다. revision이 없는 legacy 입력은
`legacy-unverified / missing-source-revision`으로 남는다. 현재 yfinance 응답은 일반적으로
목표 회계연도의 시작·종료일을 제공하지 않으므로 FY1/FY2 상대 horizon은 보존하되
`observed-partial-period`로 표시한다. 실제 회계연도를 추측하지 않는다.

병합은 구조화 estimate를 upstream·horizon·목표 기간별 완전한 revision 단위로 선택한다.
빈 배열이나 빈 summary 필드는 유효 값을 지우지 않는다. 구조화 FY1이 있으면 canonical
`forwardEPS`의 기간도 FY1으로 유지한다. 더 최신 revision에서 범위가 빠졌다면 오래된
low/high를 필드 단위로 섞어 되살리지 않는다. 동일 upstream을 포장한 두 API는 두 독립
컨센서스로 가중하지 않는다.

평가 근거에는 FY1/FY2와 음수·0을 모두 남긴다. `positivePerEligible`과
`excludedReasons`가 계산 적격성을 별도로 표현한다. FY1이 음수이고 FY2가 양수여도 FY2를
FY1 대신 EPS×PER에 넣지 않는다. 정확한 EPS basis와 재구성 규칙은 후속 IN-06에서 검증한다.

## 검증 결과

| 사례 | 결과 |
| --- | --- |
| analyst → fundamental 병합 | FY1 `1.8/2.0/2.2`, 12명과 FY2 `2.6` 보존 |
| fundamental → analyst 병합 | 위와 동일한 projection과 평가 scenario |
| 빈 fundamental estimate | analyst 배열과 exact revision을 지우지 않음 |
| low/high 또는 analystCount 결측 | 결측 유지, 합성 범위·표본 생성 없음 |
| FY1 -1, FY2 +2 | 두 관측 보존, 둘 다 현재 FY1 양수 PER scenario에는 부적격 |
| 음수 → 양수 revision | 부호 전환 기록, 부적절한 수정률 없음 |
| 같은 upstream의 API 2개 | observation/source count 1, 중복 가중 없음 |
| original revision 없는 legacy row | 검증 완료로 승격하지 않음 |

공급자 normalizer, fact read model, `collect_earnings_observations`, `earnings_scenario`를
한 fixture에서 통과시켰다. vendor 호출은 사용하지 않았다.

## 운영 읽기 전용 감사

PLTR와 NVDA의 현재 fact를 쓰기 없이 확인했다. 두 종목 모두
`yfinance.analyst`와 `yfinance.fundamental` 현재 revision이 있고 freshness는 `fresh`였다.
새 read model은 두 종목에서 FY1/FY2 배열을 보존하고 각 estimate에 exact fact revision을
연결했다. 양수 PER scenario는 FY1을 선택했으며 FY2는 관측으로만 남았다.

현재 저장된 payload는 이전 normalizer가 만든 legacy 형태다. read-side exact revision 연결과
병합 호환은 즉시 적용되지만 v2의 basis·rangeKind·revisionKind 필드는 다음 정상 수집부터
생긴다. 이 티켓은 전 종목 즉시 재수집이나 과거 payload migration을 요구하지 않는다.
