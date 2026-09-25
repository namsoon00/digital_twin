# IN-06 EPS와 주식 수의 의미 검증

- 구현일: 2026-09-25, Asia/Seoul
- 시작 HEAD: `64e2e68ce`
- 범위: 회사 재무 관측의 EPS 구성요소, 평가 EPS 적격성, ADR/환율 변환
- 운영 변경: 외부 API 재수집, 과거 cache migration, 모델 release 승격, 알림 생성 없음

## 바뀐 계산 경계

기존 경로는 최신 annual `netIncome`을 같은 행 또는 현재 `capital.sharesOutstanding`으로
나누고 annual EPS처럼 사용할 수 있었다. 이 계산은 보통주 귀속 이익인지, 기간 가중평균
주식 수인지, basic/diluted 기준과 연결 범위가 맞는지 증명하지 못한다.

현재 주식 수를 사용한 계산은 삭제하지 않고 다음 reference-only 관측으로 낮췄다.

```text
calculationMethod = approximate-current-share-count
formula = netIncome / currentSharesOutstanding
positivePerEligible = false
excludedReasons += current-share-count-is-not-weighted-average
```

검증된 재구성은 같은 immutable annual report 안에서만 허용한다.

```text
diluted EPS = common-stockholder net income / diluted weighted-average shares
basic EPS   = common-stockholder net income / basic weighted-average shares
```

두 구성요소의 provider, report period, duration, scope, security line, split 상태와 ADR 비율이
일치해야 한다. 분자는 `common-stockholders` 귀속이 명시돼야 하고 분모는
`weighted-average-basic` 또는 `weighted-average-diluted`여야 한다. report contract의 exact
source revision도 필요하다. reported EPS가 함께 있으면 1%와 절대 오차 하한을 사용해
재구성 결과를 대조하고, 허용 오차를 넘으면 둘을 섞지 않고 재구성 값을 차단한다.

reported EPS도 basic/diluted basis와 exact revision이 확인될 때만 양수 PER 입력이 된다.
basis 없는 legacy `reportedEPS`는 값과 출처를 남기되 reference-only다. 음수 EPS는 검증된
관측으로 남아도 양수 PER 계산에는 들어가지 않는다.

## 원본 정규화

yfinance statement 정규화는 `netIncomeCommon`, `basicEPS`, `dilutedEPS`, basic/diluted
weighted-average shares를 별도 metric으로 보존한다. SEC company facts adapter도 대응 US-GAAP
tag와 unit을 별도로 수집한다. 현재 발행주식 수·issued shares·weighted-average shares는 서로
대체하지 않는다. 새 필드는 additive하며 이전 reader가 모르면 기존 필드만 읽는다.

## ADR·통화·분할

현재 지원하는 교차상장 변환은 한국 현지 보통주에서 USD ADR로 가는 한 방향이다.

```text
ADR value in USD = local-share value in KRW × adrRatio / USDKRW
```

변환 결과에는 source/target symbol·통화, ADR 비율, 환율 pair/rate, factor와 공식을 남긴다.
이미 security adjustment가 적용됐거나 EPS가 target security line에 있고, 통화 방향이 다르거나,
ADR 비율·환율이 0 이하이거나, split adjustment가 미해결이면 결과를 만들지 않는다.

| 입력 | 처리 |
| --- | --- |
| reported diluted/basic EPS + exact annual lineage | `verified-reported` |
| same-report common income / matching weighted shares | `verified-reconstruction` |
| annual net income / current shares | `reference-only` |
| 반기 이익 또는 다른 scope의 shares | 차단 |
| 서로 다른 ADR 비율·split 상태 | 차단 |
| KRW local → USD ADR, ratio 0.1, USDKRW 1000 | factor 0.0001로 한 번 변환 |
| 이미 ADR 조정된 EPS | 중복 변환 차단 |

## 검증과 현재 데이터 상태

합성 fixture에서 reported diluted EPS 1200과 `1,200,000 / 1,000` 재구성 값이 일치했고,
scope·ADR 비율·split 상태 불일치, reported mismatch, 음수 EPS, basis 없는 legacy reported EPS를
각각 차단했다. 원본 statement → company financial row → valuation evidence 경로와 ADR 순수
변환을 함께 검증했으며 외부 API를 호출하지 않았다.

운영 cache를 읽기 전용으로 확인한 결과 PLTR와 NVDA의 최신 annual row에는 `netIncome`과
`sharesOutstanding`은 있지만 common-stockholder income, basic/diluted weighted-average shares,
reported basic/diluted EPS가 없다. 따라서 기존 두 종목의 이 fallback은 즉시 reference-only가
되며 verified reconstruction은 생성되지 않는다. 두 종목의 현재 선택 평가 horizon은 별도의
FY1 consensus라서 이번 제한으로 FY2나 현재 주식 수 fallback이 대신 선택되지는 않는다.

새 SEC/yfinance 정규화 필드는 다음 정상 수집과 company cache 재구축부터 채워질 수 있다.
이 티켓은 없는 과거 구성요소를 보충하거나 전 종목 재수집을 강제하지 않는다.
