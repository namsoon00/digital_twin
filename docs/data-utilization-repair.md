# Source data utilization repair

This repair preserves already collected facts through projection and AI input.
It does not introduce a new market hypothesis, action rule, provider subscription,
or a claim that incomplete valuations are ready for investment decisions.

## Repaired boundaries

- Projection input v3 retains 1/5/20-day rate changes, comparison dates, the
  yield-spread observation date, and Korean macro units, changes and year-over-year
  comparisons. Source history remains bounded; raw vendor archives are not copied
  into the graph. Unknown numeric rate facts are omitted; measured zero survives.
- Each external observation owns its source clock. A recent account-wide fetch
  or another provider's freshness no longer supplies an observation timestamp.
  The yield spread uses its own observation date, not the merged macro root's
  clock. Source fetch time is retained separately as transport metadata.
- AI decision routing v13 carries the rate windows and observation dates. Macro
  entities retain the measured units and comparisons as graph properties. No new
  TypeQL investment rule or delivery authorization is added.
- The yfinance adapter retains period-end and estimate currency from the cached
  earnings-trend response already loaded by its public estimate accessors. Those
  fields were previously lost by the DataFrame conversion. Missing vendor cache
  metadata remains explicitly missing, with no extra unbudgeted vendor request.
  EPS observation identity includes period and currency. Quote/listing currency
  does not supply an EPS or revenue-consensus currency. Revenue estimates may use
  the provider's explicit financial currency when no estimate-specific unit is
  supplied; they never fall back to quote/history currency.
- Consensus rows expose missing period, currency and publication-clock fields;
  collection freshness alone does not imply full input completeness. Collection
  time and insider transaction dates never date an analyst consensus publication.
- Source lineage exposes `revisionPersistence`: `retained`, `current-only`, or
  `unverified`. Current-only price/option/crypto observations are content identities,
  not promises of immutable history availability. Exact revision lookup still
  fails closed. Older missing history is not fabricated from a newer source
  response. The existing retention policy remains a separate operating limit.

## Data still required

The local audit found secondary financial statements already collected for the
Korean subjects with incomplete official DCF rows. Some secondary statements lack
stock-based compensation, and reporting periods differ. Arbitrarily mixing these
with a partial official annual report would change the reporting basis. They
remain separate source-bound candidates; missing values are not replaced with
zero. Negative pretax income and anomalous consensus growth retain their existing
model exclusions.

Yahoo may not provide the consensus publication clock or all estimate currency
metadata. Relative horizons alone do not prove a precise fiscal period. New
collection preserves whatever the provider actually supplies; historical missing
metadata remains unknown. Full point-in-time consensus history still requires a
source with such history and cannot be recovered by repeatedly fetching today's
estimate.

BLS release HTML is denied with HTTP 403 in the local environment; the official
BLS statistics API remains a distinct functioning source. GDELT's collection
deadline expires and its existing circuit breaker delays retries. Neither limit
is addressed by bypassing access restrictions, resetting the circuit, increasing
request volume or reporting a failed collection as successful. Existing alternate
news/statistics sources remain independent. Additional API purchase is not a
prerequisite for these repairs.

## Verification

`test_data_utilization_contract.py` replays synthetic source facts into projection,
ABox and AI decision facts, and checks nonzero/zero/missing differences, source
clock isolation, source-only revision markers, estimate period identity and unit
handling. Existing financial and external-data tests cover the adjacent contracts.

Run `python3 scripts/verify-data-utilization.py` against local retained data. It
opens a MySQL read-only transaction, emits counts and quality gaps without account
values or credentials, and performs no vendor request, LLM call, TypeDB write or
notification send. The follow-up audit below expands the original macro replay
to financial inputs, account ABox facts, dataset routes and retained native/AI
executions. `npm test` is the repository regression gate. Historical decisions
and frozen inputs are not rewritten by this repair.

The local retained-data replay verified 10 macro series and no current/retained
revision hash mismatches. A bounded `yfinance.analyst` refresh for two subjects
completed successfully through the normal collection runner. Its four returned
EPS estimates all preserved period end and currency; all four still lacked a
vendor publication clock. Successful collection therefore remains partial input
quality, not evidence of a complete point-in-time consensus.

## 후속 전수 점검: 2026-10-01

점검 범위는 등록된 외부 데이터셋 전체, 현재 보존된 원본과 모니터 스냅샷,
그 스냅샷에서 생성하는 ABox, 보존된 TypeDB 실행 기록과 AI 결과다.
원시 응답의 모든 필드를 AI에 넣는 것이 올바른 활용은 아니다. 아래처럼
판단 입력, 정규화 원본, 일정 참고, 연구용 원문, 미활용 보관 데이터를 구분했다.
외부 원본 자체의 진실성이나 미래 투자 성과를 보증하는 검사는 아니다.

### 추가로 확인하고 수정한 손실

| 경계 | 수정 전 | 수정 후 |
| --- | --- | --- |
| 원본 이력 → 추론 입력 | 전체 202개 중 정렬상 앞선 86개만 보존 | 종목 범위로 필터링하고 202개 모두 보존 |
| EPS → 평가 모델 | overview 30개·report 6개 추정치 제거 | 정규화 관측치와 원본 참조를 그대로 보존; 두 그룹 간 중복을 독립 표본으로 세지 않음 |
| 과거 배수·성장/업황 자료 → 평가 | 정규화 입력 제거 | 기존 모델이 읽는 관측치·근거 보존 |
| DCF → ABox | 6개 계산 입력 묶음 제거 | 현금흐름 연도, 출처, 승인 상태를 보존하고 평가 ABox 생성 |
| 컨센서스 → EPS 정규화 | 수집시각을 발표시각으로 대체; 기존 출처를 최신 참조로 교체 | 발표시각 미제공 유지, 기존 정확한 참조 우선, 회계기간·통화별 관측 구분 |
| 변경 감지 → 추론 요청 | DCF와 overview의 평가 입력 변경 누락 | 실제 평가 입력 변경만 `ValuationObservation`으로 전달; 일반 시세 갱신 경로 유지 |
| 결산 보고서 선택 → DCF | `20251231`이 같은 날짜의 `2025-12-31`보다 최신으로 정렬되어 불완전한 보고서를 선택 | 실제 날짜로 비교하고 같은 결산일에는 검증된 공식 항목과 항목 완전성으로 선택; 보고서를 혼합하지 않음 |

입력 계약은 v4, EPS 모델과 DCF 입력 정규화 계약은 v5로 갱신했다. 승인되지 않은
DCF는 계속 참고·검토용이며, 정보 보존이 투자 실행 승인으로 바뀌지 않는다.
원시 시세 이력·문서 전체를 복제하지 않고 이미 정규화된 평가 계약을 보존한다.

### 데이터셋별 소비 경로

등록 31종 중 30종에 현재 원본이 있으며, `official.bls-release`는 접근 거부로
원본이 없다. 실행 스크립트는 데이터셋·최상위 출력 그룹 전부를 열거하고,
분류되지 않은 데이터셋이나 출력 그룹이 생기면 실패한다. 이 경로 점검은
개별 벤더의 문서화되지 않은 모든 원시 필드에 소비자가 있다는 뜻은 아니다.

| 데이터셋 | 실제 소비 경로 |
| --- | --- |
| `alpha.quote`, `yfinance.price` | 시세 정규화·가격 ABox; 보존 시세 이력은 배수·베타 산출에도 사용 |
| `coingecko.market` | 크립토 관측·노출 ABox |
| `fred.macro`, `ecos.macro`, `kosis.indicators` | 금리·거시·환율 ABox와 AI 문맥; 금리는 DCF에도 사용 |
| `krx.market-indices`, `public-data.kr-market-index-daily` | 공식 지수 ABox; 완료 거래일 자료로 구분 |
| `public-data.kr-stock-daily`, `public-data.kr-security-master` | 완료 거래일 가격·증권 식별 ABox |
| `opendart.company_facts`, `opendart.xbrl_facts`, `sec.company_facts`, `public-data.kr-company-financials` | 출처별 재무 정규화 → CompanyKnowledge → 재무·평가 ABox |
| `public-data.kr-company-profile` | 기업 식별·프로필 정규화 → CompanyKnowledge |
| `opendart.disclosures`, `opendart.document`, `sec.submissions`, `sec.document` | 공시·문서 검증 → 기업 이벤트·재무·연구 근거 |
| `public-data.kr-dividends`, `public-data.kr-capital-events`, `public-data.kr-shareholder-rights` | 출처가 연결된 기업행동 ABox와 이벤트 수명 관리 |
| `issuer.ir_documents` | 검증된 문서 본문을 보고서·연구 근거로 변환; 원문은 그래프에 직접 복제하지 않음 |
| `yfinance.fundamental` | 재무 정규화, 기업 프로필, 배수·DCF 입력 |
| `yfinance.analyst` | EPS·매출 추정·수정 이력 정규화; 일부 부가 원시 모듈은 아래처럼 미활용 |
| `yfinance.options` | 옵션 만기와 요약 통계; 개별 체인 전체는 보관용 |
| `yfinance.news` | 원시 발견 메타데이터 보관; 현재 응답의 기사 목록에는 직접 추론 소비자가 없음 |
| `official.bls-release`, `official.fomc-release`, `official.bok-release` | 경제 일정의 발표 결과 검증; 투자 판단 권한 없음 |
| `official.bls-statistics` | 경제 일정의 최신 통계 참고; 최초 발표값이나 발표 전 컨센서스로 대체하지 않음 |

`yfinanceData`의 `recommendations`, `recommendationsSummary`,
`upgradesDowngrades`, `institutionalHolders`, `mutualfundHolders`, `majorHolders`,
`insiderTransactions`, `insiderPurchases`, `insiderRosterHolders`, `news`는
현재 정규화 판단 경로에 직접 연결되지 않은 보관 필드다. 이는 기존 정보의
활용 여지가 있다는 뜻이며, 데이터가 없어서 새 API가 필요한 상황은 아니다.
별도 뉴스 수집기는 기사를 수집·검증하지만 이 캐시를 소비하는 것은 아니다.
이 필드들을 새 투자 신호로 사용하려면 출처·관측기간·단위·검증 계약과
가설 검증이 필요하므로 이번 무결성 수정에서 임의로 투자 근거로 승격하지 않았다.

### 운영 근거와 미해결 외부 제약

- 보존된 실제 입력에서 금리·거시 10개, 이력 참조 202개, EPS 그룹 행 36개,
  DCF 6개를 재생성해 보존 여부를 검증했다. DCF 계산 결과는 압축 전후
  동일하며, 참고용 평가가 실행 가능 상태로 승격되지 않는지도 확인했다.
  보유·관심 종목 14개의 가격·수량·평가값·통화·이동평균도 ABox와 대조했다.
- 저장된 AI 결과와 실행 감사 기록 58쌍을 대조했다. 프롬프트가 있는 54건의
  해시가 일치하며 서술 주장 345개는 당시 근거 목록으로 재검증해 통과했다.
  4건은 프롬프트 없는 TypeDB 대체 결과로, AI 생성 성공 건수에 포함하지 않았다.
- 이전 수정 커밋 `75b580163` 실행 기록에서 TypeDB 원시 추론 완료와 세대 일치를
  확인했다. 실제 v13 AI 입력에도 금리 변화량과 관측일 18개 필드가 남아 있었다.
  이번 평가 입력 수정 자체는 실제 보존 원본의 ABox 재생성·계약 테스트로
  검증한다. 보존된 과거 AI 결과를 이번 새 코드로 생성한 결과처럼 취급하지 않는다.
- BLS 발표문을 정상 수집 경로로 재확인했으나 HTTP 403이었다. 접근 거부를
  재시도 유예로 기록했고, 정상 작동하는 BLS 통계 API와 분리했다.
  GDELT는 기존 시간초과·회로 차단 상태를 유지했다. 제한을 초기화하거나
  우회하지 않았으며, 전체 뉴스 데이터가 없다고 판정하지 않았다.
- 재무자료는 원본에도 일부 값이 없고, 출처별 보고기간이 다르다. 누락값을
  0으로 채우거나 오래된 다른 보고서와 혼합하지 않는다. 발표시각이 있는
  과거 컨센서스와 추가 재무 항목이 필요할 수 있지만, 유료 API 구매가
  이번 오류 수정의 선행조건은 아니다.
  다만 최초 점검에서 부족하다고 표시된 현금·부채·세전이익·세금·설비투자 중
  일부는 실제 원본 누락이 아니라 위의 보고서 선택 오류였다. 상세 DART/XBRL
  원본에 있는 항목은 새 API 없이 사용하며, 주식보상·희석 가중평균 주식수 등
  선택된 보고서에도 없는 항목은 계속 미제공으로 남긴다. 기존 스냅샷의
  `driverDcfReadiness`와 새 선택기의 미제공 항목을 감사 출력에서 구분한다.
  새 선택기로 실제 연간 보고서 14개를 확인했으며, 재무 입력 계약의 미제공
  항목은 주식보상 3개 보고서와 희석 가중평균 주식수 4개 보고서였다.

검증은 보존 기간 내 기록에 한정된다. 원본이 삭제된 과거 결과나 미래 AI
설명까지 전수 검증됐다고 주장하지 않는다. 재실행 시 수집·보존 주기에 따라
건수는 바뀔 수 있으며, 보고서는 계좌 값·개인 거래 내역·인증정보를 출력하지 않는다.

## 후속 운영 검증: 저장된 평가 근거 연결

`cb1cb6fca` 재시작 이후의 TypeDB 실행과 AI 결과를 같은 추론 세대,
ABox, 배포·릴리스, 종목별 판단 사건으로 연결해 확인했다. 운영 스냅샷의
DCF 입력은 v5로 바뀌었고, 잘못 표시되던 현금·부채·세금·설비투자 등의
미제공 항목이 사라졌다. 선택된 원본에도 없는 주식보상·희석주식수,
컨센서스 단위·관측시각 등은 계속 별도 품질 제한으로 남는다.

실제 TypeDB를 읽어 보니 메모리 그래프에는 있는 EPS 시나리오가 저장된
그래프에는 없었다. `graph_for_graph_store_persistence`가 종목과 직접
연결된 평가 결과는 보존하지만, 그 결과에서 입력 묶음·EPS·배수·계산 추적·
검토 기록으로 이어지는 간접 연결을 제거하고 있었다. 이전의 입력/ABox
재생성 검사만으로는 발견되지 않는 저장 경계의 문제였다.

평가 결과와 가정에서 출발하는 근거 연결을 방향대로 보존하도록 수정했다.
공유 모델에서 다른 종목의 가정으로 역추적하지 않으며, 회귀 테스트는 이
격리 조건과 입력 payload·판단 적격성의 보존을 확인한다. 그래프 조립 캐시도
v19로 바꿔 이전에 근거가 제거된 캐시를 재사용하지 않도록 했다. 투자 규칙,
모델 승인 상태, 과거의 고정된 결과는 변경하지 않는다.

운영 검증은 다음 명령으로 재실행한다. `REVISION`에는 검증할 배포 커밋의
해시를 넣는다. 최근 성공한 추론 100건과 AI 실행 100건 중 해당 커밋의 기록을
대상으로 하며, 새 입력·완료된 AI·실제 저장된 DCF와 EPS 표본이 없으면 실패한다.

```bash
python3 scripts/verify-runtime-data-flow.py --revision REVISION
```

- 정확한 시점의 원본과 저장용 입력에서 EPS·DCF·출처·거시 값의 보존을 대조한다.
- AI가 참조한 추론 세대·ABox·릴리스·종목 사건, 실행 프롬프트 해시와 주장 근거를 검증한다.
- 활성 TypeDB의 DCF 평가와 EPS payload를 해당 생성 시점의 원본 재계산과 대조한다.
- 재시작 후 처리된 구버전 입력과 보존 기간이 지난 입력은 별도 집계한다.
  이를 새 입력의 성공 표본으로 계산하거나 최신 스냅샷으로 대체하지 않는다.
- MySQL 읽기 전용 트랜잭션과 TypeDB READ만 사용한다. 데이터베이스 생성,
  신규 추론·AI 요청·알림 발송은 수행하지 않으며 계좌 값이나 AI 원문도 출력하지 않는다.

기업 재무 근거가 모든 AI 설명에 포함돼야 하는 것은 아니다. 현재 라우터는
해당 규칙·가설·사용자 질문과 관련 있는 기업 근거만 포함한다. 따라서 AI
실행 계보 검증과 개별 EPS·DCF 근거가 실제 서술에 사용됐다는 주장은 구분한다.

### 운영 재생성과 최종 대조

`f7d82a135` 적용 후 기존 스냅샷을 사용해 14개 종목의 저장 범위를 재생성했다.
활성 릴리스의 TBox/RuleBox와 모델 승인 상태를 유지했으며, 유지보수 과정에서
새 AI 요청이나 투자 알림을 생성하지 않았다. 잠시 중지한 그래프 워커는 재개했다.

읽기 전용 대조에서 EPS 시나리오 9건, DCF 평가 6건, 입력 근거 묶음 14건과
그 묶음의 출처 참조 123개를 확인했다. 재계산에는 정확한 시점의 저장용 입력과
운영 capture 단계의 기업정보 조합을 사용한다. 원본 아카이브를 바로 계산하면
조합 전 회사정보의 제외 관측 식별자가 달라질 수 있으므로, 원본 보존 검증과
운영 입력 재계산을 구분한다.

같은 커밋의 AI 실행 1건은 필수 설명 항목이 부족해 게시 계약을 통과하지 못했다.
이는 데이터 보존 성공과 별개의 실패이며 전체 경로 성공으로 보고하지 않는다.
검증 도구는 이 경우에도 네이티브 추론·그래프 대조 결과를 출력하고,
`failed-ai-publication` 상태 및 종료 코드 1을 반환한다.
