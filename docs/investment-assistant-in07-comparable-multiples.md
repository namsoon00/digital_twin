# IN-07 비교 가능한 배수와 기본 평가 게이트

- 구현일: 2026-09-25, Asia/Seoul
- 시작 HEAD: `2234f855f`
- 범위: PER 표본 정규화·비교 가능성·분포 선택·EPS×PER 시나리오·품질 게이트
- 운영 변경: 외부 API 재수집, 과거 cache migration, 모델 release 승격, 알림 생성 없음

## 표본 계약

각 배수 관측은 다음 의미를 보존한다.

```text
issuer / securityLine / multipleMetric / basis(peer|historical)
earningsHorizon / accountingBasis / epsBasis / currency
priceAsOf / earningsAsOf / provider / upstreamOrigin
comparisonFactors / sourceReferences / freshnessState / comparabilityState
```

값이 0.5~150 범위라는 사실만으로 비교 표본이 되지 않는다. target EPS와 horizon,
EPS basis, accounting basis, security line, 통화가 맞아야 한다. issuer·기준 시각·provider·
upstream·통화가 없거나 기준일이 해석되지 않고, 비교 가능성·freshness가 검증되지 않은 행과
stale 행은 제외한다. 같은 issuer/security/
horizon/두 기준 시각/upstream 관측은 mirror provider가 달라도 한 표본이며, 같은 identity에 서로
다른 값이 들어오면 둘 다 conflict로 제외한다.

결과의 `selectionLedger`는 각 표본의 포함·제외 상태와 이유를 남긴다. 성장률·마진·사업 구조처럼
공급자가 제공한 비교 요인은 `comparisonFactors`에 보존하며, 없는 비교 요인을 만들어 내지 않는다.

## 분포와 정책

`multiple-comparability-v2`는 peer와 historical 값을 별도 분포로 계산한다. 각 분포의 25·50·75
백분위를 별도로 남기고, 최소 표본 수를 충족한 분포 중 versioned priority인 peer, historical
순서로 하나만 선택한다. 두 분포를 한 배열에 합치지 않는다. provider 수는 신뢰도 보조 정보이며
정확성 인증이 아니다.

과거 TTM PER를 FY1 EPS와 결합하거나 basic PER를 diluted EPS에 적용하는 행은 명시적으로
차단한다. 기존 KIS normalization은 한 원본 행의 issuer, period, upstream을 보존하도록 확장했지만
원본이 basic/diluted를 제공하지 않으면 `epsBasis=unknown`으로 남기므로 판단 표본이 되지 않는다.

`SemiconductorCyclical` 유형은 target EPS에 정상화 기간·마진이 검증됐다는 상태가 없으면 표본 수가
충분해도 배수 평가를 차단한다. 현재 티켓은 그 근거를 임의로 생성하지 않는다.

## bootstrap과 승인 경계

비교 가능한 단일 분포가 최소 표본 수를 채우지 못하면 기존 archetype bootstrap 수치는 계산
흔적으로 유지한다. 이 결과는 다음 상태를 갖는다.

```text
basis = bootstrap-prior
evidenceBacked = false
comparabilityState = insufficient-comparable-samples
referenceOnly = true
```

모델 입력 상태는 충분해지지 않으며 `valuationReferenceOnly=true`다. 품질 게이트도
`unverified-target-multiple`과 `reference-only-valuation`을 검사하므로 사람의 기존 모델 승인만으로
bootstrap 또는 비교 불가능한 배수를 판단 적격으로 바꿀 수 없다. 현재가 anchor도 이 부족분을
메우지 않는다.

## 시나리오 계산

양수 EPS와 PER의 bear/base/bull 가정은 의미를 유지한 채 같은 방향끼리 계산한다.

```text
bear = eps.low  × multiple.low
base = eps.base × multiple.base
bull = eps.high × multiple.high
```

각 조합은 `scenarioAssumptions`에 EPS, multiple, fair value와 함께 남는다. 계산 결과를 정렬해
서로 반대인 가정의 출처를 바꾸지 않는다. 이 계산 모듈은 BUY/SELL을 만들지 않는다.

## 검증과 운영 데이터 상태

합성 fixture에서 FY1 diluted·US-GAAP·동일 security line인 peer 4개와 historical 3개를 서로 다른
분포로 계산하고 peer 분포 9.5/11/13을 선택했다. horizon 불일치, stale, basic/diluted 불일치,
mirror 중복, 같은 identity의 값 충돌을 각각 제외했다. bootstrap 품질 게이트와 명시적 시나리오
조합도 검증했다.

운영 read model을 읽기 전용으로 확인한 결과 035720, 000660, PLTR, NVDA 모두 현재
`bootstrap-prior`, 비교 가능 표본 0, `evidenceBacked=false`, decision eligible false였다. yfinance
현재 fact에는 peRatio/forwardPE가 있지만 issuer별 peer/historical 비교 ledger가 없어서 이를 새
목표 배수 표본으로 간주하지 않았다. 이번 변경은 없는 비교 표본을 보충하거나 API 재수집을
강제하지 않으며, 기존 reference 상태를 정확히 설명한다.
