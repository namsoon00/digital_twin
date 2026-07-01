# Exit Lens

토스증권 Open API로 확인 가능한 계좌, 잔고, 보유 종목, 관심 종목, 주문 가능 금액을 기준으로 매매 점검 우선순위를 정리하는 로컬 우선 대시보드입니다. 핵심 화면은 체결강도, 거래량, 매수/매도 체결량, 호가 불균형, 밸류에이션을 조합해 보유/관심 종목을 점검하는 수급 판단판입니다.

## 실행

```bash
cp .env.example .env.local
npm start
```

브라우저에서 `http://127.0.0.1:3000`을 여세요.

GitHub Pages mock preview는 아래 URL에서 확인합니다.

```text
https://namsoon00.github.io/digital_twin/?mock=1
```

토스 실계좌 조회를 켜려면 `.env.local`에 아래 값을 넣습니다. 값이 없으면 앱은 데모 포트폴리오로 동작합니다.

```bash
TOSS_CLIENT_ID=...
TOSS_CLIENT_SECRET=...
TOSS_ACCOUNT_SEQ=... # 선택
WATCHLIST_SYMBOLS=NVDA,TSLA,000660 # 선택
```

토스 개발자 콘솔에서 허용 IP를 관리하는 경우, 브라우저 IP가 아니라 이 로컬 서버가 외부로 나가는 공인 IP를 등록해야 합니다. GitHub Pages 같은 정적 웹 페이지에서 브라우저가 직접 토스 API를 호출하는 구조는 `client_secret` 노출과 사용자별 유동 IP 문제 때문에 사용하지 않습니다.

## Mock 확인

웹 상단의 `Mock` 전환 버튼을 누르면 토스 계좌/잔고/관심 종목을 고정 mock 데이터로 표시합니다. 실환경 변수가 설정되어 있어도 mock 모드에서는 외부 API를 호출하지 않습니다.
GitHub Pages에서는 서버 API가 없으므로 mock 스냅샷을 브라우저에서 바로 렌더링합니다.

API만 확인할 때는 아래처럼 호출합니다.

```bash
curl http://127.0.0.1:3000/api/flow-lens?mock=1
```

시스템 실험용 시계열 mock 데이터도 제공합니다. GitHub Pages에서도 바로 읽을 수 있도록 정적 JSON을 `public/mock-data/market/`에 커밋합니다. 이 데이터는 실제 주문/계좌와 분리된 합성 OHLCV 데이터이며, 최근 1년 가격 레벨을 기준으로 한 기본 국면과 역사적 특징 국면을 선택해 받을 수 있습니다.

```bash
curl "http://127.0.0.1:3000/mock-data/market/scenarios.json"
curl "http://127.0.0.1:3000/mock-data/market/semiconductor-boom.json"
```

지원 시나리오:

- `recent-one-year`: 최근 1년 기준의 완만한 상승/순환 조정
- `covid-crash`: 코로나 급락과 V자 회복
- `financial-crisis`: 금융위기형 장기 하락과 느린 회복
- `semiconductor-boom`: 반도체 호황과 AI 설비 투자 국면
- `rate-shock`: 금리 충격과 성장주 밸류에이션 압축

정적 JSON 응답에는 종목별 `candles`, 최신 `signals`, 시나리오 이벤트, 기준 기간이 포함됩니다. 데이터 구성을 바꾸고 싶으면 아래 명령으로 같은 스키마의 정적 파일을 다시 생성합니다.

```bash
npm run generate:mock-market
```

로컬 서버에서는 같은 generator를 API 형태로도 확인할 수 있습니다.

```bash
curl "http://127.0.0.1:3000/api/mock-market/scenarios"
curl "http://127.0.0.1:3000/api/mock-market/candles?scenario=semiconductor-boom&symbols=NVDA,005930&seed=demo"
```

API 방식은 `seed` 값을 바꾸면 같은 시나리오 안에서도 다른 mock 흐름을 만들 수 있습니다.

## 화면 구성

웹은 `판단`, `실험실`, `모델`, `보유`, `피드`, `관심` 하단 탭으로 구성됩니다. `실험실`에서는 매수/매도 실험 기록을 버전으로 저장하고, `모델`에서는 나만의 매수·매도 공식, 가중치, 판단 기준, 모델 버전을 관리합니다. `관심` 탭에서는 관심 종목을 추가, 수정, 삭제할 수 있습니다.

상단 설정 버튼에서는 관심 종목, Toss API, 텔레그램 알림 설정을 로컬 서버 DB(`data/settings.json`)에 저장합니다. `client_secret`과 bot token은 서버가 사용하는 로컬 파일에만 저장하고, API 응답과 화면에는 원문을 다시 표시하지 않습니다. GitHub Pages 정적 미리보기에서는 서버 DB가 없으므로 민감 설정 저장을 사용하지 않습니다.

## 앱 구조

- `public/`: Exit Lens 웹 대시보드
- `GET /api/flow-lens`: 토스 계좌/보유자산, 주문 가능 금액, 관심 종목, 내 계좌 기준 오늘 먼저 점검할 종목 집계
- `GET /api/flow-lens?mock=1`: 웹 검증용 고정 mock 스냅샷
- `GET/PUT /api/settings`: 로컬 서버 DB 기반 Toss/알림 설정 조회와 저장. secret 원문은 GET 응답에 포함하지 않음
- `public/mock-data/market/*.json`: GitHub Pages에서도 읽을 수 있는 정적 mock market 데이터
- `GET /api/mock-market/scenarios`: 학습/실험용 mock market 시나리오 목록
- `GET /api/mock-market/candles?scenario=recent-one-year&symbols=NVDA,AAPL`: 시나리오별 1년치 OHLCV, 수급 신호, 이벤트 반환
- `server.js`: 토스 OAuth 토큰 발급, 계좌/보유자산 조회, 관심 종목 파싱, 매도 검토 fallback 생성

토스 호출은 서버에서만 수행합니다. 브라우저에 `client_secret`, access token, `X-Tossinvest-Account` 값이 내려가지 않습니다. 토스증권 공개 Open API에는 토스 앱의 관심 종목 목록 조회 endpoint가 확인되지 않아, 관심 종목은 앱 내부 목록으로 관리합니다.

## 매도 판단 모델

앱은 주문을 실행하지 않고 읽기 전용 판단판만 제공합니다. 보유 종목은 수익률, 평가손익, 매도 가능 수량, 계좌 내 노출 비중으로 `분할 매도 기준 확인`, `일부 익절 기준 확인`, `조건부 보유`, `보유 유지`로 분류합니다. 관심 종목은 보유가 아니므로 매도 판단을 만들지 않고, 토스 시세 연결 후 현재가 기준을 비교하는 대기 상태로 둡니다.

## 수급 신호

`수급` 탭은 종목별 `체결강도`, `거래량 배율`, `매수 체결량`, `매도 체결량`, `호가 불균형`, `가격 변화율`을 조합해 `매수 후보`, `추격 주의`, `분할매도 검토`, `리스크 축소 검토`, `관망` 같은 점검 라벨을 만듭니다. 라벨은 주문 지시가 아니라 사용자가 매수/매도 여부를 판단하기 전에 볼 데이터 조합입니다.

현재 GitHub Pages와 mock 모드에서는 설정의 `수급 신호 입력` 값을 사용합니다. 입력 형식은 아래와 같습니다.

```text
SYMBOL, 체결강도, 거래량배율, 매수량, 매도량, 호가불균형%, 가격변화%
005930,118,1.8,620000,480000,18,2.1
```

라이브 연결에서는 토스 시장 데이터의 체결, 호가, 현재가, 캔들 응답을 같은 정규화 필드로 매핑하는 방식으로 확장합니다.

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

`main` 브랜치에 푸시하면 `.github/workflows/pages.yml`이 실행되어 GitHub Pages용 `gh-pages` 브랜치를 갱신합니다. 배포 직전에 `npm run generate:mock-market`를 실행하므로 서버의 mock market generator만 바뀌어도 정적 JSON이 함께 갱신됩니다.

## 공유 미리보기

```bash
npm run share
```

공유 모드에서는 임시 토큰 URL을 사용합니다. `.env`, `.env.local`, `data/store.json`, API key, 토스 credentials, 개인 계좌 데이터는 이슈나 PR에 올리지 않습니다.

## 로컬 알림

로컬 PC에서 토스 계좌를 조회한 뒤 점검 요약을 콘솔, 텔레그램, 또는 카카오톡 나에게 보내기로 발송할 수 있습니다.

```bash
npm run notify -- --dry-run
npm run notify
npm run notify:watch
```

`notify`는 한 번 실행하고, `notify:watch`는 기본 10분(`NOTIFY_INTERVAL_MINUTES`) 간격으로 반복 실행합니다. 알림은 토스 연결 실패, 장전/마감 요약, 각 보유 종목의 상태와 매도/추가매수 검토 타이밍, 섹터 집중, 현금 비중 부족을 점검합니다. 매수/매도 문구는 주문 지시가 아니라 사용자가 확인할 조건과 기준입니다.

텔레그램 발송을 쓰려면 봇에게 `/start`를 보낸 뒤 `.env.local`에 `NOTIFY_PROVIDER=telegram`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`를 넣습니다. `TELEGRAM_CHAT_ID`는 토큰을 넣은 상태에서 아래 명령으로 확인합니다.

```bash
npm run notify:telegram-chat
```

카카오 발송을 쓰려면 `NOTIFY_PROVIDER=kakao`와 `KAKAO_ACCESS_TOKEN`을 넣거나, 장기 실행용으로 `KAKAO_REST_API_KEY`와 `KAKAO_REFRESH_TOKEN`을 `.env.local`에 넣습니다. 토큰과 알림 중복 방지 상태는 `data/kakao-token.json`, `data/notification-state.json`에 로컬로 저장되며 커밋하지 않습니다.

## 참고

토스증권 Open API 계약과 fixture는 `docs/toss-api-contract.md`와 `docs/fixtures/toss/`에 정리되어 있습니다. `mobile/`은 이전 실험 앱 코드이며, 현재 루트 웹 앱의 기준 구현은 Exit Lens입니다.
