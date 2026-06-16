# MarketFlow

MarketFlow는 한국/미국 주식과 세계 자금 흐름을 테마, 종목, 투자 전 체크리스트, 감 기록으로 추적하는 Flutter 앱입니다.

## 실행

```bash
flutter run
```

실제 기기에 설치하려면 `mobile` 폴더에서 iOS 또는 Android 디바이스를 선택해 실행하세요.

시세 API를 연결하려면 Alpha Vantage API key를 빌드 변수로 전달하세요.

```bash
flutter run --dart-define=ALPHA_VANTAGE_API_KEY=<your-key>
```

앱 실행 후 `설정` 탭에서도 필요한 데이터 API key를 모두 입력할 수 있습니다. Alpha Vantage, 토스증권 Open API, FRED, CoinGecko, DefiLlama, ETF/Fund Flow API, 한국 투자자별 수급 API가 같은 화면에 표시되며, 공급자/커버리지/사용 화면/문서 URL은 읽기 전용 메타데이터로 보입니다. 앱은 기본 데이터 조회용 key 문자열만 기기 로컬 설정에 저장하고, Alpha Vantage key는 대시보드 시세 조회에 바로 사용합니다.

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

## 토스증권 설정

앱의 `설정` 탭에서 토스증권 Open API 직접 호출 옵션을 켤 수 있습니다.

- 저장 항목: 계정 별칭, 계좌 식별값, Open API 기본 URL, 연결 테스트 경로, 앱 키, 앱 시크릿, 액세스 토큰, 읽기 전용 여부
- 호출 방식: 앱에서 REST API로 직접 호출하며, `Authorization: Bearer <token>`, `appkey`, `appsecret` 헤더를 전송합니다.
- 주의 사항: 웹 배포에서는 키와 토큰이 브라우저 저장소와 네트워크 요청에 노출될 수 있습니다. 개인 기기 설치용으로 먼저 사용하세요.
- 주문 기능: 서버 검증 전까지 앱에서 잠금 상태

## 세계 자금 흐름

앱의 `자금` 탭은 세계 돈의 이동을 자산군 단위로 보여줍니다.

- 필요 API 맵: Alpha Vantage, 토스증권 Open API, FRED, CoinGecko, DefiLlama, ETF/Fund Flow API, 한국 투자자별 수급 API
- 종합 그래프: 캔들형 종합 흐름 지수, 유동성 막대, AI/코인/금/KOSPI/리스크 라인을 하나의 그래프에 표시
- 기간 조정: 1M, 2M, 3M, ALL 선택과 세부 구간 슬라이더
- 자산군: 주가지수, 섹터, 코인, 금/원자재, 채권, 통화, 대체자산
- 표시 항목: 흐름 점수, 모멘텀, 유동성, 위험도, 목적지, 핵심 촉발 요인
- 새 흐름 후보: 미국 개인의 KOSPI 접근성 확대처럼 아직 초기 단계인 자금 이동 시나리오

## 투자 전 체크

앱의 `체크` 탭은 오늘 투자하기 전 확인할 항목을 날짜별로 관리합니다.

- 기본 항목: 글로벌 지수/환율, 자금 흐름, 매매 근거, 손절/목표, 포지션 크기, 이벤트, 감정 상태
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
