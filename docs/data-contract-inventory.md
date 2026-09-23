# Data Contract Inventory

이 문서는 외부 데이터를 공급자 이름이 아니라 소유 의미, 표준 출력, 사용 목적,
정확한 원천 버전으로 관리하기 위한 기준선이다. API 키의 존재나 수집 성공은
투자 판단 적격성을 뜻하지 않는다.

## Contract Boundary

- 도메인 계약: `modules/market_data/domain/external_data_contracts.py`
- 데이터셋 의미 카탈로그: `modules/market_data/domain/external_dataset_catalog.py`
- 전송·주기 등록: `modules/market_data/application/external_data/registry.py`
- 원천 저장: `external_fact_current`, `external_fact_revision`
- 원천 이벤트: `external_data.observation_recorded`, `external_data.fact_changed`
- 호환 읽기 모델: `ExternalSignalsReadModelService`
- 목적별 적격성: `external_data_fitness.py`

`SourceReference`는 `datasetId`, `providerId`, `subjectKey`, 내부 `revisionId`,
공급자 revision, canonical payload hash, schema version, source-as-of, availability를
고정한다. `fetchedAt`처럼 재조회마다 바뀌는 전송 시각은 내용 hash에서 제외한다.

## Data Categories

| category | 소유 의미 | 대표 출력 |
| --- | --- | --- |
| `identity` | 기업, 증권, 상장, 공급자 별칭 | security master, company profile |
| `price_trade` | 가격, OHLCV, 거래·파생 관측 | quote, daily price, option chain |
| `investor_flow` | 투자자 구분별 매수·매도·순매수 | KIS investor flow |
| `financial` | 기간·통화·기준이 있는 재무 보고 | company facts, statements |
| `expectation_valuation` | 컨센서스·평가배수 입력 | analyst consensus, fundamentals |
| `company_event` | 뉴스, 공시, 기업행동 | filing, disclosure, corporate action |
| `calendar` | 효력·발표 일정 | release calendar, shareholder schedule |
| `market_context` | 거시·지수·FX·크립토 환경 | FRED, BLS, index, crypto market |
| `account_portfolio` | 계좌, 포지션, 원장, 노출 | portfolio-owned contracts |

`account_portfolio`는 외부 데이터 registry가 아니라 portfolio/account 모듈이
소유한다. `investor_flow`의 실시간 KIS 자료도 KIS 시장 신호 경계가 소유하며,
이 표의 외부 registry 항목과 동일한 가용성 언어를 사용해야 한다.

## Registered Datasets

| dataset | category | normalized output | decision purpose | market | empty result |
| --- | --- | --- | --- | --- | --- |
| `coingecko.market` | price_trade, market_context | crypto-market-observation-v1 | crypto-market | GLOBAL | missing |
| `fred.macro` | market_context | macro-series-observation-v1 | macro-regime | GLOBAL | missing |
| `official.bls-release` | calendar, market_context | official-release-calendar-v1 | reference | GLOBAL | missing |
| `official.fomc-release` | calendar, market_context | official-release-calendar-v1 | reference | GLOBAL | missing |
| `official.bok-release` | calendar, market_context | official-release-calendar-v1 | reference | GLOBAL | missing |
| `official.bls-statistics` | market_context | official-statistical-vintage-v1 | macro-regime | GLOBAL | missing |
| `opendart.disclosures` | company_event | official-disclosure-index-v1 | disclosure | KR | missing |
| `opendart.document` | company_event, financial | official-disclosure-document-v1 | disclosure, valuation | KR | missing |
| `opendart.company_facts` | identity, financial | company-financial-facts-v1 | identity, valuation | KR | missing |
| `public-data.kr-stock-daily` | price_trade | daily-price-observation-v1 | market-price | KR | missing |
| `public-data.kr-security-master` | identity | security-master-v1 | identity | KR | missing |
| `public-data.kr-market-index-daily` | price_trade, market_context | market-index-observation-v1 | market-price | KR | missing |
| `public-data.kr-company-profile` | identity | company-profile-v1 | identity | KR | missing |
| `public-data.kr-company-financials` | financial | company-financial-facts-v1 | valuation | KR | missing |
| `public-data.kr-dividends` | company_event, calendar | corporate-action-v1 | disclosure | KR | missing |
| `public-data.kr-capital-events` | company_event, calendar | corporate-action-v1 | disclosure | KR | missing |
| `public-data.kr-shareholder-rights` | company_event, calendar | shareholder-rights-event-v1 | disclosure | KR | missing |
| `sec.submissions` | company_event, identity | official-filing-index-v1 | disclosure, identity | US | missing |
| `sec.document` | company_event, financial | official-filing-document-v1 | disclosure, valuation | US | missing |
| `sec.company_facts` | financial | company-financial-facts-v1 | valuation | US | missing |
| `yfinance.price` | price_trade | secondary-market-observation-v1 | market-price | KR, US | missing |
| `yfinance.options` | price_trade, market_context | derivatives-observation-v1 | derivatives | US | unsupported |
| `yfinance.news` | company_event | news-metadata-v1 | news | KR, US | unsupported |
| `yfinance.analyst` | expectation_valuation | analyst-consensus-v1 | analyst-consensus | KR, US | unsupported |
| `yfinance.fundamental` | financial, expectation_valuation | secondary-company-facts-v1 | valuation | KR, US | missing |
| `alpha.quote` | price_trade | secondary-market-observation-v1 | market-price | US | missing |

`reference`는 일정 정보가 다른 판단을 보조하지만 단독 투자 판단 목적 계약은
아님을 뜻한다. `yfinance`와 Alpha Vantage는 공식 보고 원천이 아니므로
`estimated` evidence basis로 분류한다.

## Event and Consumer Matrix

| committed fact | emitted event | consumer | delivery |
| --- | --- | --- | --- |
| 새 원천 revision | `external_data.observation_recorded` | 감사·향후 정규화 투영 | exact `sourceRef` |
| 투자상 의미 있는 변경 | `external_data.fact_changed` | 공식 공시 evidence projector, 호환 경로 | exact `sourceRef` |
| OpenDART/SEC 공시 변경 | `external_data.fact_changed` | research evidence | leased persistent membership |
| current read | 없음 | `ExternalSignalsReadModelService` | compatibility model + `externalDataLineage` |

공식 공시 소비는 `external_fact_projection_deliveries`에 이벤트와 같은 트랜잭션으로
등록된다. 발생 시각 커서는 호환 fallback일 뿐 MySQL 운영 경로의 권위 있는 전달
상태가 아니다.

## End-to-End Samples

### Korean equity

`opendart.document` 관측 → immutable source revision → exact `sourceRef`가 있는
`fact_changed` → leased official-evidence projection → `ResearchEvidence` → 검증된
evidence만 reasoning snapshot/ABox 후보 → AI와 상세 화면의 provenance.

가격·수급은 Toss/KIS 실시간 경계와 market time-series가 소유한다. 미국에 없는
국내 투자자별 수급을 미국 종목에 0으로 합성하지 않는다.

### US equity

`sec.document`/`sec.company_facts` 관측 → immutable source revision → filing/financial
evidence → issuer/listing identity로 대상 확인 → reasoning source snapshot → AI와
상세 화면. `yfinance.price`는 가격 보조 원천이며 SEC 공식 재무의 대체물이 아니다.

## Known Gaps

- 실제 운영 데이터셋별 row count, 보존 기간, 최근 성공 시각은 로컬 DB 상태라 이
  git 문서에 고정하지 않는다. `/api` 상태와 외부 데이터 status 명령으로 확인한다.
- 미국 종목의 국내식 외국인·기관·개인 수급, 체결강도, 호가가 공급자에서
  지원되지 않으면 `unsupported`이며 0이 아니다.
- 컨센서스 표본 수, point-in-time 예상치, 실제 broker execution feed는 원천이
  없으면 `missing` 또는 `unsupported`로 남는다.
- 기업·증권·상장 연결은 기존 symbol universe와 `SecurityLine`이 소유한다. 이번
  계약은 문자열 티커를 전면 교체하지 않는다.
- 뉴스·공시·재무의 세부 표준 투영은 기존 소유 모듈 계약을 유지한다. 이 catalog는
  provider JSON을 새로운 전역 도메인 모델로 만들지 않는다.

## Adding a Dataset

1. provider adapter에 transport, cadence, rate-limit, empty handling을 구현한다.
2. `external_dataset_catalog.py`에 category, output contract, purpose, market,
   asset kind, evidence basis, schema/normalizer version을 등록한다.
3. 0, null, missing, unsupported와 correction fixture를 추가한다.
4. exact revision 재조회와 source reference가 유지되는지 검증한다.
5. 목적별 fitness와 권위 있는 소비자를 연결한다. provider payload를 reasoning이나
   notification에서 직접 읽는 분기를 추가하지 않는다.
6. replay/backfill은 알림 권한 없이 실행하고 authoritative reader가 하나인지 확인한다.
