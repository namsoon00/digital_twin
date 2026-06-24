# MarketFlow

MarketFlow는 한국/미국 주식과 경제 피드, 세계 자금 흐름을 테마, 종목, 투자 전 체크리스트, 감 기록으로 추적하는 Flutter 앱입니다.

앱은 기본적으로 다크 모드로 실행되며, `설정` 탭에서 시스템/라이트/다크 테마를 바꿀 수 있습니다.

## 실행

```bash
flutter run
```

실제 기기에 설치하려면 `mobile` 폴더에서 iOS 또는 Android 디바이스를 선택해 실행하세요.

시세 API를 연결하려면 Alpha Vantage API key를 빌드 변수로 전달하세요.

```bash
flutter run --dart-define=ALPHA_VANTAGE_API_KEY=<your-key>
```

앱 실행 후 `설정` 탭에서는 웹 클라이언트에서 직접 호출 가능한 데이터 API를 중심으로 연결 상태를 확인합니다. 현재 우선 대상은 CoinGecko와 DefiLlama이며, GitHub Pages 배포판은 토스증권 secret이나 계좌 토큰을 브라우저에 저장하지 않습니다.

## iOS 빌드 준비

```bash
cd mobile
flutter doctor -v
flutter pub get
cd ios && pod install && cd ..
flutter build ios --debug --no-codesign
```

기기에 설치하거나 배포용 archive를 만들 때는 `ios/Runner.xcworkspace`를 Xcode에서 열고 Apple Developer Team과 signing 설정을 지정하세요.

## 검증

```bash
flutter analyze
flutter test
```

## GitHub Pages 빌드

```bash
flutter build web --release --base-href /digital_twin/ --dart-define=ALPHA_VANTAGE_API_KEY=<your-key>
```

`main`에 푸시하면 `.github/workflows/pages.yml`이 같은 빌드를 실행하고 `gh-pages` 브랜치에 정적 파일을 배포합니다.
배포 환경에서는 GitHub repository secret `ALPHA_VANTAGE_API_KEY`를 사용합니다.

## 토스증권 연동

토스증권 Open API는 Flutter Web에서 직접 호출하지 않고 로컬 BFF를 통해 연결합니다.

- 공식 API: OAuth 2.0 Client Credentials, `https://openapi.tossinvest.com`
- 브라우저 정책: `client_secret`, access token, `X-Tossinvest-Account` 헤더를 브라우저에 저장하지 않음
- 1단계 산출물: `docs/toss-api-contract.md`
- mock fixture: `docs/fixtures/toss/`
- 주문 기능: read-only 연결 검증 이후에도 서버 플래그가 켜질 때까지 잠금 상태

## 세계 자금 흐름

앱의 `자금` 탭은 세계 돈의 이동을 자산군 단위로 보여줍니다.

- 필요 API 맵: CoinGecko, DefiLlama, 이후 BFF 기반 토스증권 Open API
- 종합 그래프: 캔들형 종합 흐름 지수, 유동성 막대, AI/코인/금/KOSPI/리스크 라인을 하나의 그래프에 표시
- 보기 단위: 일별, 주별, 월별 전환
- 데이터 출처: 실제 API 데이터와 mock 데이터를 배지로 구분
- 코인 마켓: CoinGecko API로 BTC, ETH, SOL, XRP, BNB, USDC, USDT, DOGE 가격/시총/거래량/1h/24h/7d 변화율 표시
- 데이터 UI: 축, 현재값 마커, 핵심 지표 요약을 포함한 금융 대시보드형 차트
- 기간 조정: 1M, 2M, 3M, ALL 선택과 세부 구간 슬라이더
- 자산군: 주가지수, 섹터, 코인, 금/원자재, 채권, 통화, 대체자산
- 표시 항목: 흐름 점수, 모멘텀, 유동성, 위험도, 목적지, 핵심 촉발 요인
- 새 흐름 후보: 미국 개인의 KOSPI 접근성 확대처럼 아직 초기 단계인 자금 이동 시나리오

## 경제 피드

앱의 `피드` 탭은 경제가 어떤 축으로 움직이는지 한 화면에서 볼 수 있게 구성한 읽기 피드입니다.

- 요약: 시장 펄스, 자금 흐름, 강세 테마, API 상태를 한 번에 표시
- 필터: 전체, 매크로, 유동성, 정책, 자금, 실적, 리스크
- 피드: 영향도, 출처, 업데이트 시간, 핵심 태그를 카드로 표시

## 투자 전 체크

앱의 `체크` 탭은 오늘 투자하기 전 확인할 항목을 날짜별로 관리합니다.

- 기본 항목: 글로벌 지수/환율, 자금 흐름, 매매 근거, 손절/목표, 포지션 크기, 이벤트, 감정 상태
- 데이터 확인: 체크 화면 상단에서 API 상태, 시장 펄스, 자금 흐름, 강세 테마, 관심 종목 신호 확인
- 캘린더: 월간 캘린더에서 날짜별 완료 상태와 진행률 확인
- 관리: 선택일 체크 토글, 사용자 항목 추가/삭제, 날짜별 메모 저장, 선택일 초기화
- 저장: 체크 상태와 메모는 기기 로컬 설정에 저장

## 사용 API

- Provider: Alpha Vantage
- Endpoint: `GLOBAL_QUOTE`
- Key: `ALPHA_VANTAGE_API_KEY`
- 화면 표시: 대시보드의 API 카드에 provider, endpoint, 연결 상태, 업데이트 시각이 표시됩니다.

## 구조

- `lib/src/models`: 시장, 테마, 종목, 사용자, 기록 모델
- `lib/src/data`: 나중에 API/서버 저장소로 교체 가능한 repository 계층
- `lib/src/screens`: 하단 탭 기반 앱 화면
- `lib/src/widgets`: 카드, 차트, 시장 전환 등 재사용 UI
- `lib/src/theme`: 앱 색상과 Material 3 테마

## 현재 범위

현재 앱은 mock 데이터로 동작합니다. 다음 단계에서 실시간/지연 시세 API, 사용자 인증, 서버 저장소, 유료 플랜, 알림을 붙일 수 있도록 데이터 접근을 repository로 분리했습니다.
