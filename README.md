# Exit Lens

토스증권 Open API로 확인 가능한 계좌, 잔고, 보유 종목, 관심 종목, 주문 가능 금액을 기준으로 매매 점검 우선순위를 정리하는 로컬 우선 대시보드입니다. 핵심 화면은 체결강도, 거래량, 매수/매도 체결량, 외국인/기관 매수·매도량, 호가 불균형, 밸류에이션을 조합해 보유/관심 종목을 점검하는 수급 판단판입니다.

## 실행

```bash
cp .env.example .env.local
npm start
```

브라우저에서 `http://127.0.0.1:3000`을 여세요. 첫 화면은 Python 서비스 운영 콘솔이며, 계정 등록, 메시지 타입별 알림 활성화, 알림 주기, 모델링 설정을 관리합니다.

GitHub Pages 정적 미리보기는 아래 URL에서 확인합니다. 빌드 시점의 로컬 DB 계정/설정 요약은 마스킹된 값으로 채우고, 실제 SQLite DB 파일과 secret 원문은 포함하지 않습니다. 로컬 서버가 필요한 저장/조회 기능은 비활성화됩니다.

```text
https://namsoon00.github.io/digital_twin/
```

Python 서비스 어드민 구성 미리보기는 아래 URL에서 확인합니다. 계정, 알림 주기, 모델링 설정의 빌드 스냅샷을 secret 없이 보여주는 읽기 전용 정적 페이지입니다.

```text
https://namsoon00.github.io/digital_twin/admin/
```

토스 실계좌 조회를 켜려면 `.env.local`에 아래 값을 넣습니다. 값이 없으면 로컬 서버는 연결 상태와 빈 포트폴리오를 기준으로 화면을 구성합니다.

```bash
TOSS_CLIENT_ID=...
TOSS_CLIENT_SECRET=...
TOSS_ACCOUNT_SEQ=... # 선택
WATCHLIST_SYMBOLS=TSLA,AAPL,NVDA,000660 # 선택
```

미장 현재가/거래량, 크립토, 거시 금리, 국내 공시 알림을 켜려면 아래 외부 데이터 API 키를 `.env.local` 또는 상단 설정 화면에 저장합니다. 키 원문은 로컬 DB와 환경 파일에서만 사용하고, API 응답과 정적 미리보기에는 포함하지 않습니다.

```bash
ALPHA_VANTAGE_API_KEY=...
COINGECKO_API_KEY=...
FRED_API_KEY=...
OPENDART_API_KEY=...
EXTERNAL_FRED_SERIES=DGS10,DGS2,DFF
EXTERNAL_CRYPTO_IDS=bitcoin,ethereum
EXTERNAL_DART_CORP_CODES="005930=00126380;000660=00164779"
```

토스 개발자 콘솔에서 허용 IP를 관리하는 경우, 브라우저 IP가 아니라 이 로컬 서버가 외부로 나가는 공인 IP를 등록해야 합니다. GitHub Pages 같은 정적 웹 페이지에서 브라우저가 직접 토스 API를 호출하는 구조는 `client_secret` 노출과 사용자별 유동 IP 문제 때문에 사용하지 않습니다.

GitHub Pages에 올라가는 모든 정적 산출물은 아래 명령으로 함께 갱신합니다.

```bash
npm run generate:static
```

## 화면 구성

웹은 `홈`, `계정`, `관심종목`, `모니터링`, `알림`, `모델링`, `설정` 탭으로 구성됩니다. desktop에서는 왼쪽 고정 내비게이션, mobile에서는 하단 탭 내비게이션을 사용합니다. `계정` 탭은 로컬 SQLite DB에 저장된 계정 값을 폼에 채우고, secret 원문은 다시 표시하지 않습니다. `알림` 탭에서는 메시지 타입별 활성화, 주기, 임계값, 발송 채널, 마지막 발송 시각, 다음 발송 가능 시각, 타입별 메시지 템플릿을 관리하고, `모델링` 탭에서는 모델 공식과 판단 기준을 관리합니다.

`설정` 탭에서는 앱 테마, 외부 데이터 API, 텔레그램 알림 전달 설정을 로컬 SQLite DB(`data/service.db`)의 `runtime_settings` 테이블에 저장합니다. 계정별 Toss API 연결은 `계정` 탭에서, 관심 종목은 `관심종목` 탭에서, 모델 기준은 `모델링` 탭에서 관리합니다. `client_secret`, 외부 API key, bot token은 서버가 사용하는 로컬 DB에만 저장하고, API 응답과 화면에는 원문을 다시 표시하지 않습니다. GitHub Pages 정적 미리보기에서는 서버 DB가 없으므로 민감 설정 저장을 사용하지 않습니다.

전역 UI 정책과 새 화면 체크리스트는 `docs/design-system.md`에 정리되어 있습니다. 모든 웹 탭과 Python admin preview는 같은 색상, 간격, 버튼 위치, 내비게이션 기준을 따릅니다.

## 앱 구조

- `public/`: Exit Lens 웹 대시보드
- `GET /api/flow-lens`: 토스 계좌/보유자산, 주문 가능 금액, 관심 종목, 내 계좌 기준 오늘 먼저 점검할 종목 집계
- `GET /api/symbol-universe`, `POST /api/symbol-universe/refresh`: 코스피·코스닥·나스닥 전체 종목 카탈로그 검색과 원천 목록 갱신
- `GET /api/bootstrap`, `GET/POST /api/memories`, `GET/POST /api/items`: 로컬 SQLite DB의 `app_store` 기반 앱 데이터 조회와 저장
- `GET/PUT /api/settings`: 로컬 SQLite DB의 `runtime_settings` 기반 Toss/알림 설정 조회와 저장. secret 원문은 GET 응답에 포함하지 않음
- `GET /api/notification-schedules`: 메시지 타입별 실제 마지막 발송, 다음 가능 시각, 최근 대상, 발송 조건 설명 조회
- `python_service/digital_twin/infrastructure/web_server.py`: 정적 웹 자산 서빙과 로컬 API 라우팅
- `python_service/digital_twin/application/flow_lens_service.py`: 토스 계좌/보유자산 스냅샷, 관심 종목 파싱, 매도 검토 fallback 생성

토스 호출은 서버에서만 수행합니다. 브라우저에 `client_secret`, access token, `X-Tossinvest-Account` 값이 내려가지 않습니다. 토스증권 공개 Open API에는 토스 앱의 관심 종목 목록 조회 endpoint가 확인되지 않아, 관심 종목은 앱 내부 목록으로 관리합니다.

## 전체 종목 카탈로그

모니터링 탭의 `전체 종목 카탈로그`는 코스피, 코스닥, 나스닥 종목을 `data/service.db`의 `symbol_universe`에 저장합니다. 코스피/코스닥은 KRX KIND 상장법인 목록, 나스닥은 Nasdaq Trader Symbol Directory를 사용하며, 소스별 마지막 성공 시각과 stale 여부를 함께 표시합니다. 원천 호출이 실패해도 마지막 성공 목록은 유지됩니다.

```bash
npm run python:symbols:refresh -- --markets KOSPI,KOSDAQ,NASDAQ
npm run python:symbols:search -- --query AAPL --market NASDAQ
npm run python:symbols:status
```

기본 신선도 기준은 `SYMBOL_UNIVERSE_MAX_AGE_HOURS=24`입니다.

## 매도 판단 모델

앱은 주문을 실행하지 않고 읽기 전용 판단판만 제공합니다. 보유 종목은 수익률, 평가손익, 매도 가능 수량, 계좌 내 노출 비중으로 `분할 매도 기준 확인`, `일부 익절 기준 확인`, `조건부 보유`, `보유 유지`로 분류합니다. 관심 종목은 보유가 아니므로 매도 판단을 만들지 않고, 토스 시세 연결 후 현재가 기준을 비교하는 대기 상태로 둡니다.

## 수급 신호

`수급` 탭은 종목별 `체결강도`, `거래량`, `거래량 배율`, `매수 체결량`, `매도 체결량`, `외국인/기관 매수·매도량`, `호가 불균형`, `가격 변화율`을 조합해 `매수 후보`, `추격 주의`, `분할매도 검토`, `리스크 축소 검토`, `관망` 같은 점검 라벨을 만듭니다. 라벨은 주문 지시가 아니라 사용자가 매수/매도 여부를 판단하기 전에 볼 데이터 조합입니다.

정적 GitHub Pages 미리보기에서는 서버 API를 호출하지 않으므로 수급 신호를 표시하지 않습니다. 로컬 라이브 연결에서는 토스 시장 데이터의 체결, 호가, 현재가, 캔들 응답을 같은 정규화 필드로 매핑합니다. 이동평균선은 Toss가 직접 내려주는 필드가 아니라 `/api/v1/candles` 일봉 종가로 앱이 계산합니다. 설정 입력 형식은 아래와 같습니다.

```text
SYMBOL, 체결강도, 거래량배율, 매수량, 매도량, 호가불균형%, 가격변화%, 20일선, 60일선, 외국인순매수, 기관순매수
005930,118,1.8,620000,480000,18,2.1,69000,66000,145000,82000
```

## 공식과 가중치

앱은 기본 추천 공식을 제공하지만, 사용자가 직접 적정가 공식과 매수/매도 점수 공식을 바꿀 수 있습니다. 공식은 브라우저 안에서 안전한 숫자 수식으로만 계산하며, 지원 범위는 숫자, 변수, `+ - * /`, 괄호, `min`, `max`, `abs`, `round`, `sqrt`, `pow`, `clamp`입니다.

기본 적정가 공식:

```text
eps * targetPer * growthWeight * qualityWeight * riskWeight
```

기본 매수/매도 공식은 `tradeStrength`, `volumeRatio`, `buyShare`, `bidAskImbalance`, `priceChangeRate`, `fairValueGap`, `undervalueBonus`, `expensivePenalty`, `flowWeight`, `valuationWeight`를 조합합니다. 사용자는 설정의 `추가 가중치`에서 `flowWeight=1.2`처럼 원하는 가중치를 바꾸고, `판단 기준값`에서 `buyCandidate=78`, `sellTrim=70` 같은 라벨 기준을 조정할 수 있습니다.

## 밸류에이션

`가치` 탭은 투자 중인 보유 종목의 현재가가 비싼지 싼지 보기 위한 계산판입니다. 현재가는 토스 잔고/시세 값을 사용하고, EPS·목표 PER·안전마진·적정가 공식은 사용자가 상단 설정에서 직접 입력합니다. 현재가가 안전마진 가격 이하이면 `싸다`, 적정가 이하이면 `적정권`, 적정가를 넘으면 `비싼 편` 또는 `비싸다`로 표시합니다.

## 검증

```bash
npm test
```

프런트엔드 변경 후에는 로컬 서버를 띄워 브라우저에서도 확인합니다.

## GitHub Pages 배포

`main` 브랜치에 푸시하면 `.github/workflows/pages.yml`이 실행되어 GitHub Pages용 `gh-pages` 브랜치를 갱신합니다. 로컬 DB 값을 반영해야 하므로 정적 웹 자산은 로컬에서 `npm run generate:admin-preview`로 갱신해 커밋하고, GitHub Actions는 커밋된 `public/` 산출물을 그대로 배포합니다.

## 공유 미리보기

```bash
npm run share
```

공유 모드에서는 임시 토큰 URL을 사용합니다. `.env`, `.env.local`, `data/service.db`, legacy `data/store.json`, API key, 토스 credentials, 개인 계좌 데이터는 이슈나 PR에 올리지 않습니다.

## Python 서비스

다중 계정, 실시간 모니터링, 스케줄링, 모델 리뷰 로직은 Python 서비스가 담당합니다. Python 서비스는 SQLite DB인 `data/service.db`의 여러 계정을 순회하고, 앱 store, 런타임 설정, 계정별 이전 스냅샷, 메시지 주기, 도메인 이벤트, 모델 리뷰 큐, 알림 발송 큐, 알림 템플릿을 DB 테이블에 저장합니다. 토스 credentials와 텔레그램 발송 정보도 계정별 DB row로 관리합니다.

```bash
npm run python:accounts -- list
npm run python:accounts -- add --id main --label "메인" --client-id "$TOSS_CLIENT_ID" --client-secret "$TOSS_CLIENT_SECRET" --account-seq "$TOSS_ACCOUNT_SEQ" --watchlist "NVDA,005930" --notify-provider telegram --telegram-bot-token "$TELEGRAM_BOT_TOKEN" --telegram-chat-id "$TELEGRAM_CHAT_ID"
npm run python:monitor:once -- --dry-run --force
npm run python:monitor:status
npm run python:monitor:watch
npm run python:model-review:once -- --dry-run
npm run python:model-review:status
npm run python:model-review:watch
npm run python:notifications:once
npm run python:notifications:status
npm run python:notifications:watch
npm run python:symbols:refresh -- --markets KOSPI,KOSDAQ,NASDAQ
npm run python:symbols:status
npm run python:templates -- list
npm run python:service:start
npm run python:service:status
npm run python:service:restart
npm run python:service:stop
```

`python:service:start`는 실시간 모니터, 비동기 모델 리뷰 worker, 알림 worker를 함께 시작합니다. Python 서비스는 계정별 연결 상태, 보유 종목 변화, 손익률/평가액 급변, 현금비중 급변, 판단 변화, 보유 타이밍을 감지합니다. 알림 메시지는 모니터링/모델 리뷰/핸드오프에서 즉시 외부 전송하지 않고 outbox인 `notification_jobs` 큐에 먼저 적재하며, 실제 텔레그램/콘솔 발송은 알림 worker 한 곳에서 순차 처리합니다. 발송 시점에는 `notification_templates`의 타입별 템플릿을 렌더링하므로 메시지 포맷 변경은 템플릿 수정만으로 반영됩니다. 모니터링 이벤트는 append-only `domain_events` 이벤트 스트림에 저장되고, 알림 outbox 작업은 source event id와 dedupe key를 함께 저장해 이벤트 재처리 시에도 중복 발송을 막습니다.

텔레그램 발송을 쓰려면 봇에게 `/start`를 보낸 뒤 계정 설정에 `notify-provider telegram`, `telegram-bot-token`, `telegram-chat-id`를 저장합니다. 자세한 구조는 `docs/python-service.md`에 정리되어 있습니다.

## 참고

토스증권 Open API 계약과 fixture는 `docs/toss-api-contract.md`와 `docs/fixtures/toss/`에 정리되어 있습니다. `mobile/`은 이전 실험 앱 코드이며, 현재 루트 웹 앱의 기준 구현은 Exit Lens입니다.
