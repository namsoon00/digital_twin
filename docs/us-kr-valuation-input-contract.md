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
| 공식 재무 후보 | `sec.company_facts` | `opendart.company_facts`, `opendart.xbrl_facts`, `public-data.kr-company-financials` |
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
- FY1·FY2 매출 컨센서스와 공급자 재무통화, 각 기간의 분석가 수
- 통화에 맞는 10년 국채금리와 각 데이터 revision

집계 공급자의 재무로도 참고 계산은 가능하지만 `secondary-aggregator`로 표시한다. 필수 재무 12개가
공식 공시 revision과 지표별 provenance로 모두 확인돼야 `officialDecisionReady=true`가 된다. 공식
근거가 부족하면 계산 결과는 `reference-only`이며 투자 판단과 자동 행동에 쓰지 않는다.

컨센서스는 `0y`를 FY1, `+1y`를 FY2로 고정해 해석한다. 공급자의 `financialCurrency`가 공시 재무
통화와 같고, 각 기간에 한 명 이상의 분석가가 있으며, low/avg/high 범위가 모순되지 않아야 한다.
기준연도→FY1 또는 FY1→FY2 매출 변화가 +100%를 넘거나 -80%보다 작으면 단위 또는 기간 오류일
가능성이 있으므로 `*-revenue-consensus-growth-outlier`로 DCF 입력을 차단한다. 경고만 남긴 계산값은
릴리즈하지 않는다.

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
| `VALUATION_DRIVER_DCF_RELEASE_MODE` | `shadow` | `reference`일 때 검증된 참고용 release 활성화 |
| `VALUATION_DRIVER_DCF_RELEASE_ID` | 비어 있음 | 감사 가능한 참고용 release 식별자 |
| `VALUATION_DRIVER_DCF_RELEASE_AT` | 비어 있음 | release 승인 시각 |
| `VALUATION_DRIVER_DCF_RELEASE_SYMBOLS` | 비어 있음 | release 대상 종목 목록 |

기존 공통 ERP와 영구성장률 설정은 시장별 값이 없을 때의 호환 기본값으로 유지한다. 모든 기본 가정은
승인값이 아니라 검토 후보이다.

## 운영 검증 기준

미국과 한국 대표 종목을 한 번에 조회해 다음을 확인한다.

1. 미국 bundle은 USD, DGS10, `fred.macro`만 사용한다.
2. 한국 bundle은 KRW, KRGB10Y, `ecos.macro`만 사용한다.
3. 두 bundle 모두 계산 결과, 민감도, source revision과 가정 검토 계약을 만든다.
4. 공식 재무 coverage 또는 가정 승인이 부족하면 `valuationDecisionEligible=false`를 유지한다.
5. 누락·오래된 값·통화 불일치가 있으면 기존 값을 재사용하지 않고 명시적인 blocker를 남긴다.

## 공식 재무 수집과 후보 선택

SEC Company Facts는 지표별 최신값 한 건만 고르지 않는다. 연간 `10-K`의 270~430일 구간을 최대
4개년 보존하고, 같은 회계연도와 accession number에 속한 값만 하나의 DCF 재무행으로 묶는다. 총부채는
동일 보고서의 유동·비유동 부채를 합산하며, 운전자본 변화는 매출채권·재고·기타 영업자산·매입채무·기타
영업부채의 동일 accession 구성요소로 계산한다. 계산식과 구성 지표는 provenance에 남긴다.

OpenDART는 최신 분기·반기 보고서와 직전 사업연도 사업보고서를 함께 수집한다. 동일 날짜의 중간보고와
연간보고는 frequency를 포함한 별도 관측으로 보존하며, 유동·비유동 차입금과 공시된 리스부채를 합산해
총부채를 만든다. 사업보고서 접수번호로 원본 XBRL을 후속 수집해 이자비용, 감가상각·상각, 운전자본
조정, 주식보상, 희석가중평균주식 수 후보를 정규화한다. XBRL 값은 기간과 접수번호가 단일계정 재무행과
모두 일치할 때만 그 행의 누락값을 채운다. 태그, context, 단위, 구성값, 원본 ZIP hash와 instance hash를
provenance에 남기며 원본 ZIP 자체는 운영 fact에 저장하지 않는다. API가 반환하지 않는 지표를 0으로
채우거나 다른 접수번호·공급자의 값을 공식값처럼 합치지 않는다.

공식 행과 보조 행은 `valuationFinancialCandidates`에서 서로 다른 관측으로 유지한다. 완전한 공식 행이
최신 완전 행과 62일 이내이면 공식 행을 우선한다. 공식 행이 불완전하면 완전한 보조 행으로 shadow DCF는
계산하되, 공식 행의 coverage를 `officialAlternatives`에 표시하고 결과를 `reference-only`로 유지한다.

2026-09-27 전체 보유·관심 14종목 재계산 결과 중 공식 입력이 완전한 종목은 다음과 같다.

| 종목 | 계산 입력 | 공식 coverage | 상태 |
| --- | --- | ---: | --- |
| NVDA | SEC EDGAR 2026-01-25 연간보고 | 12/12 | `released-reference`, 판단·자동매매 제외 |
| 035720 | OpenDART 2025-12-31 사업보고서 | 12/12 | `released-reference`, 판단·자동매매 제외 |
| 000660 | OpenDART 2025-12-31 사업보고서 | 12/12 | FY1 컨센서스 증가율 이상으로 차단 |
| CPNG | SEC EDGAR 2025-12-31 연간보고 | 12/12 | 음수 DCF 결과와 불완전 민감도로 차단 |

2026-09-27 운영 검증에서 000660 사업보고서 접수번호 `20260317000635`의 단일계정 재무와 XBRL을
결합해 공식 12개 항목을 모두 확인했다. 이 상태는 재무 입력의 공식 출처가 완전하다는 뜻이며, DCF의
ERP·영구성장률·마진·재투자율 등 미래 가정까지 정확하다는 뜻은 아니다. 가정 검토와 판단 모델 승격 전에는
계속 reference-only로 유지한다. 다른 한국 종목도 각 회사의 XBRL 태그와 context가 허용 계약에 맞아야 같은
상태가 되며, 후보가 없거나 접수번호가 다르면 `reference-only`로 남는다.

## 참고용 release

`driver-dcf-reference-r1-20260927`은 보유·관심 14종목을 검증 대상으로 삼고, 검증을 통과한 종목의
적정가, reverse DCF와 3×3 민감도 데이터를 화면에서 비교하기 위한 참고용 release다. release audit는 공식 재무 12개, exact source revision,
양수 계산값과 9개 민감도 시나리오를 확인한다. 검증을 통과하지 못한 종목은 설정에 포함돼도 release되지
않는다. 통과한 종목도 `valuationDecisionEligible=false`, `automaticTradingAllowed=false`를 유지한다.

운영 검증에서 NVDA는 주당 121.20 USD, 035720은 주당 약 49,108원이었다. 이 값은 현재 가정으로
계산한 참고 범위다. 000660은 FY1 매출 컨센서스가 기준연도보다 100% 넘게 증가해
`fy1-revenue-consensus-growth-outlier`로 계산 전에 차단했다. 모든 종목에 장기 가정 검토와 기업별
환율·부채금리 노출 연결이 남아 있다.
