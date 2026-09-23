# Data Integration Acceptance

이 문서는 데이터 확장 작업의 완료 판정 기준이다. 수집 건수 증가만으로 완료하지
않으며, 동일한 정확한 원천이 저장·추론 입력·AI·화면에서 이어지는지를 검증한다.

## Baseline

- 착수 기준 revision: `bc1e73679`
- registry dataset: 26개
- 외부 호출과 LLM을 제외한 결정론적 fixture로 회귀를 비교한다.
- 운영 DB의 개인 계좌 값, API key, 원문 응답은 문서나 fixture에 복사하지 않는다.
- 활성 reasoning release와 운영 row count는 실행 시점 상태이므로 코드 기준선에서
  `runtime-unverified`로 남기고 재시작 후 status로 확인한다.

## Mandatory Invariants

1. 같은 의미의 payload는 `fetchedAt`만 달라져도 같은 canonical hash를 가진다.
2. 같은 provider revision에 내용이 수정되면 다른 내부 revision ID를 가진다.
3. 이벤트의 `sourceRef.revisionId`는 immutable revision을 정확히 조회하며 current로
   fallback하지 않는다.
4. payload의 120번째 이후 필드만 변해도 전체 변경을 감지한다.
5. 숫자 0, null/missing, unsupported, not-applicable을 서로 바꾸지 않는다.
6. 원천 저장과 domain event 및 공식 공시 delivery membership은 한 트랜잭션이다.
7. projection 실패나 프로세스 종료 후 lease 만료 시 같은 event를 다시 처리할 수 있다.
8. 동일 event의 재전달은 정규화 결과를 중복 생성하지 않는다.
9. 미국에 없는 국내식 수급은 매도 우위나 순매수 0으로 해석하지 않는다.
10. read model은 각 dataset/subject의 revision, hash, as-of, availability lineage를 보존한다.

## Representative Paths

| sample | source path | required assertion | current verification |
| --- | --- | --- | --- |
| KR disclosure | OpenDART document → revision → evidence | exact source revision, verified issuer, no current fallback | fixture verified |
| US filing | SEC document → revision → evidence | exact accession content and schema version | fixture verified |
| KR price | public daily/KIS time-series → feature snapshot | timezone/session/adjustment and coverage retained | existing contract tests; runtime sample required |
| US price | yfinance/Alpha → feature snapshot | unsupported flow remains unsupported | contract fixture; runtime sample required |
| crypto | CoinGecko → market context | source as-of and missing/failure separated | existing collector tests; runtime sample required |
| macro | official/FRED → market context | series vintage and release calendar remain distinct | catalog verified; runtime sample required |

## Test Mapping

- `test_external_data_platform.py`
  - category catalog and descriptor metadata
  - canonical source reference and provider correction
  - zero/missing/unsupported states
  - late-field material change detection
  - compatibility read-model lineage
- `test_external_official_evidence_projection.py`
  - exact revision projection and fail-closed missing revision
  - current backfill without alert replay
  - leased delivery completion and duplicate avoidance
- `test_investor_flow_contract.py`
  - investor class, period, unit, freshness and unsupported semantics
- `test_market_time_series_daily_candles.py`, `test_point_in_time_replay.py`
  - temporal coverage and point-in-time reconstruction
- `npm test`
  - repository-wide syntax, architecture and smoke regression

## Runtime Verification

구현 후 다음을 비밀정보 없이 기록한다.

1. `npm run python:external-data:status`에서 descriptor 26개가 모두 category와
   source schema version을 가지는지 확인한다.
2. 국내·미국 각 1개 subject에서 current fact의 `revisionId`가 revision table에서
   조회되는지 확인한다.
3. 공식 공시 delivery의 pending/processing/failed/completed 수와 oldest pending을
   확인하고 timestamp cursor가 아닌 `leased-membership`인지 확인한다.
4. 동일 저장 fixture를 30회 재생해 p50/p95, DB query 수, row 수, 입력 byte를
   측정한다. 외부 API와 LLM 시간은 별도 항목으로 기록한다.
5. reasoning source snapshot, AI request audit, web detail의 source revision과 값이
   같은지 기존 보호 ID로 대조한다.

## Release Gates

- 단위·집중 통합 테스트와 `npm test` 통과
- schema additive migration 성공
- 기존 current fact의 빈 revision ID가 값 변경 이벤트 없이 채택됨
- 공식 evidence backfill은 `alertEligibleCount=0`
- replay/backfill/candidate 경로의 투자 알림 0건
- runtime status에서 새로운 provider failure 또는 projection backlog 없음
- unsupported/missing 항목을 구현 완료로 보고하지 않음

## Deferred Work

아래는 이 계약 기반 위에서 순차 진행하며 현재 단계의 완료를 과장하지 않는다.

- 모든 카테고리의 field-level 표준 projection 전환
- point-in-time 컨센서스 history와 peer valuation coverage
- 실제 broker execution feed 연결
- 뉴스 사건의 정정·철회 및 다중 출처 cluster UI
- 모든 AI prompt와 notification detail의 field-level source link 표준화
- 공급자 교체를 이용한 full W09 확장성 검증

## 2026-09-23 Implementation Record

- 기본 registry 26개 모두 category, output contract, source schema version을 노출했다.
- 운영 current fact 186건을 이벤트·알림 없이 backfill했고 186건 모두 immutable
  revision의 ID와 payload hash가 정확히 일치했다.
- `005930`, `NVDA` read model을 같은 입력으로 30회 재생한 결과 output hash는 1개로
  동일했다. 측정값은 p50 18.52ms, p95 28.59ms, 최대 44.25ms, packet 277,158
  bytes였고 13개의 bounded lineage ref를 포함했다.
- 집중 계약 테스트와 전체 `npm test`의 1,571개 Python core test 및 44개
  frontend test가 통과했다.
- 실제 공급자 미지원 데이터와 point-in-time 컨센서스·실제 체결 feed는 위의
  deferred 상태를 유지한다.
