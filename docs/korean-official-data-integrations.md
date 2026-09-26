# 국내 공식 데이터 연동

Orbit Alpha는 국내 투자 판단에 필요한 공식 데이터를 세 가지 독립 데이터셋으로 수집한다. 인증키는 `.env.local` 또는 운영 설정 저장소에만 두며, API 응답·관리자 화면·수집 사실에는 원문을 노출하지 않는다.

| 데이터셋 | 공급원 | 수집 범위 | 판단 용도 |
| --- | --- | --- | --- |
| `ecos.macro` | 한국은행 ECOS | 기준금리, 국고채 3년·10년, 회사채 3년 AA-, 원/달러 환율 | 할인율, 신용 스프레드, 환율 민감도, 국내 거시 환경 |
| `kosis.indicators` | KOSIS | 전산업생산, 선행지수 순환변동치, 소매판매액 불변지수 | 경기·수요 환경 교차검증 |
| `krx.market-indices` | KRX OpenAPI | KOSPI·KOSDAQ 일별 공식 지수 | 시장 국면과 가격 데이터 교차검증 |

## 로컬 설정

`.env.local`에 다음 환경 변수를 설정한다.

```dotenv
ECOS_API_KEY=
KOSIS_API_KEY=
KRX_OPEN_API_KEY=
```

수집기는 기본적으로 활성화되며 다음 설정으로 호출 주기, 신선도 한도, 요청 제한 시간을 조정할 수 있다.

```dotenv
EXTERNAL_ECOS_ENABLED=1
EXTERNAL_ECOS_TIMEOUT_SECONDS=10
EXTERNAL_DATA_ECOS_CADENCE_SECONDS=21600
EXTERNAL_DATA_ECOS_FRESHNESS_SECONDS=172800

EXTERNAL_KOSIS_ENABLED=1
EXTERNAL_KOSIS_TIMEOUT_SECONDS=12
EXTERNAL_DATA_KOSIS_CADENCE_SECONDS=21600
EXTERNAL_DATA_KOSIS_FRESHNESS_SECONDS=3888000

EXTERNAL_KRX_ENABLED=1
EXTERNAL_KRX_TIMEOUT_SECONDS=12
EXTERNAL_DATA_KRX_CADENCE_SECONDS=21600
EXTERNAL_DATA_KRX_FRESHNESS_SECONDS=259200
```

## 정확성 경계

- ECOS와 KOSIS는 공급기관이 발표한 관측값과 발표 시점을 보존한다. 일부 계열이 누락되면 `coverageState=partial`과 `missingSeries`를 기록한다.
- KOSIS 월별 값은 관측 월을 별도로 보존하고, 신선도 계산용 시점은 해당 월 1일로 정규화한다. 이는 발표일을 추정하지 않기 위한 보수적인 기준이다.
- KRX 일별 지수는 실시간 가격으로 사용하지 않는다. `official-daily-reference`로 분류해 장중 추론을 직접 촉발하지 않는다.
- KRX와 공공데이터포털 지수가 겹치면 수집 순서와 관계없이 기준일이 최신인 관측값을 선택한다. 기준일이 같으면 KRX 직접 관측값을 우선한다.
- KRX 응답에서 `코스피` 또는 `코스닥` 대표지수를 정확히 찾지 못하면 첫 번째 행을 대신 사용하지 않고 해당 날짜를 사용할 수 없는 것으로 처리한다.
- KRX 인증키 발급과 각 API의 활용 승인은 별도 절차다. 승인되지 않은 지수 API가 401 또는 403을 반환하면 수집 실패를 명시하며 다른 데이터로 값을 꾸미지 않는다.
- 세 데이터셋은 적정가를 단독 산출하지 않는다. 기업 재무·공시·주가 데이터와 결합할 때 할인율, 경기 가정, 시장 교차검증 근거로 사용한다.

변경 후 `npm test`를 실행하고 프로젝트 관리 서비스를 재시작한다.
