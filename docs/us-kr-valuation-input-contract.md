# 미국·한국 DCF 입력 계약

## 목적

미국과 한국 주식의 적정가 계산은 같은 DCF 계산기를 사용하되, 통화와 시장이 다른 관측값을 섞지 않는다.
계산 가능한 상태와 투자 판단에 사용할 수 있는 상태를 분리하며, 출처나 필수 항목이 빠지면 값을 추정해
채우지 않고 차단 사유를 반환한다.

## 시장별 입력

| 구분 | 미국 주식 | 한국 주식 |
| --- | --- | --- |
| 기준 통화 | USD | KRW |
| 무위험금리 | FRED `DGS10` | ECOS `KRGB10Y` |
| 무위험금리 데이터셋 | `fred.macro` | `ecos.macro` |
| 공식 재무 후보 | `sec.company_facts` | `opendart.company_facts`, `public-data.kr-company-financials` |
| 보조 재무·컨센서스 | `yfinance.fundamental`, `yfinance.analyst` | `yfinance.fundamental`, `yfinance.analyst` |

DCF 입력에는 무위험금리의 series ID, 데이터셋 ID, 관측일, 공급자와 revision을 함께 보존한다. USD
재무에는 FRED revision만, KRW 재무에는 ECOS revision만 연결한다. 지원하지 않는 통화이거나 해당 통화의
국채금리 값·revision이 없으면 `valuation-currency-not-supported`, `matching-risk-free-rate-missing`,
`macro-source-revision-missing` 중 해당 사유로 입력 생성을 차단한다.

## 계산과 판단의 경계

다음 항목이 모두 있으면 shadow DCF를 계산할 수 있다.

- 동일 연간 보고서의 매출, 영업이익, 세전이익, 법인세, 이자비용, 감가상각, 설비투자,
  운전자본 변화, 주식보상, 현금, 총부채, 희석주식 수
- 시가총액과 beta
- FY1·FY2 매출 컨센서스
- 통화에 맞는 10년 국채금리와 각 데이터 revision

집계 공급자의 재무로도 참고 계산은 가능하지만 `secondary-aggregator`로 표시한다. 필수 재무 12개가
공식 공시 revision과 지표별 provenance로 모두 확인돼야 `officialDecisionReady=true`가 된다. 공식
근거가 부족하면 계산 결과는 `reference-only`이며 투자 판단과 자동 행동에 쓰지 않는다.

ERP, 영구성장률, WACC, 3~5년 성장률 fade, 일정 마진·재투자율, 우선주와 비지배지분 0 가정은
관측 사실이 아니다. 미국과 한국 설정을 따로 보존하고 `candidate/pending`으로 표시한다. 정확한 입력
bundle과 가정 버전에 대한 사람의 검토와 모델 release 승인이 끝나기 전에는 DCF를 승격할 수 없다.

## 런타임 설정

| 설정 | 기본값 | 의미 |
| --- | ---: | --- |
| `VALUATION_DRIVER_DCF_PILOT_SYMBOLS` | `NVDA,000660` | shadow DCF 대상 |
| `VALUATION_DRIVER_DCF_US_EQUITY_RISK_PREMIUM_PCT` | `5` | 미국 ERP 후보 |
| `VALUATION_DRIVER_DCF_KR_EQUITY_RISK_PREMIUM_PCT` | `5` | 한국 ERP 후보 |
| `VALUATION_DRIVER_DCF_US_TERMINAL_GROWTH_PCT` | `2.5` | 미국 영구성장률 후보 |
| `VALUATION_DRIVER_DCF_KR_TERMINAL_GROWTH_PCT` | `2.5` | 한국 영구성장률 후보 |

기존 공통 ERP와 영구성장률 설정은 시장별 값이 없을 때의 호환 기본값으로 유지한다. 모든 기본 가정은
승인값이 아니라 검토 후보이다.

## 운영 검증 기준

미국과 한국 대표 종목을 한 번에 조회해 다음을 확인한다.

1. 미국 bundle은 USD, DGS10, `fred.macro`만 사용한다.
2. 한국 bundle은 KRW, KRGB10Y, `ecos.macro`만 사용한다.
3. 두 bundle 모두 계산 결과, 민감도, source revision과 가정 검토 계약을 만든다.
4. 공식 재무 coverage 또는 가정 승인이 부족하면 `valuationDecisionEligible=false`를 유지한다.
5. 누락·오래된 값·통화 불일치가 있으면 기존 값을 재사용하지 않고 명시적인 blocker를 남긴다.
