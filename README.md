# Exit Lens

토스증권 Open API로 확인 가능한 계좌, 잔고, 보유 종목, 관심 종목, 주문 가능 금액을 기준으로 매도 검토 우선순위를 정리하는 로컬 우선 대시보드입니다. 뉴스, X 포스팅, 군중 심리 같은 외부 신호는 판단에 섞지 않습니다.

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

## Mock 확인

웹 상단의 `Mock` 전환 버튼을 누르면 토스 계좌/잔고/관심 종목을 고정 mock 데이터로 표시합니다. 실환경 변수가 설정되어 있어도 mock 모드에서는 외부 API를 호출하지 않습니다.
GitHub Pages에서는 서버 API가 없으므로 mock 스냅샷을 브라우저에서 바로 렌더링합니다.

API만 확인할 때는 아래처럼 호출합니다.

```bash
curl http://127.0.0.1:3000/api/flow-lens?mock=1
```

## 설정 탭

웹은 `판단`, `가치`, `보유`, `관심`, `설정` 하단 탭으로 구성됩니다. `관심` 탭에서는 관심 종목을 추가, 수정, 삭제할 수 있고, `설정` 탭에서는 관심 종목, Toss API 설정, 밸류에이션 가정, secret 값을 입력하고 이 브라우저의 `localStorage`에 저장할 수 있습니다. GitHub Pages에서는 secret을 서버로 보내지 않으며, 브라우저 저장소가 막힌 환경에서는 현재 탭 메모리에서만 유지됩니다.

## 앱 구조

- `public/`: Exit Lens 웹 대시보드
- `GET /api/flow-lens`: 토스 계좌/보유자산, 주문 가능 금액, 관심 종목, 내 계좌 기준 오늘 먼저 점검할 종목 집계
- `GET /api/flow-lens?mock=1`: 웹 검증용 고정 mock 스냅샷
- `server.js`: 토스 OAuth 토큰 발급, 계좌/보유자산 조회, 관심 종목 파싱, 매도 검토 fallback 생성

토스 호출은 서버에서만 수행합니다. 브라우저에 `client_secret`, access token, `X-Tossinvest-Account` 값이 내려가지 않습니다. 토스증권 공개 Open API에는 토스 앱의 관심 종목 목록 조회 endpoint가 확인되지 않아, 관심 종목은 앱 내부 목록으로 관리합니다.

## 매도 판단 모델

앱은 주문을 실행하지 않고 읽기 전용 판단판만 제공합니다. 보유 종목은 수익률, 평가손익, 매도 가능 수량, 계좌 내 노출 비중으로 `분할 매도 기준 확인`, `일부 익절 기준 확인`, `조건부 보유`, `보유 유지`로 분류합니다. 관심 종목은 보유가 아니므로 매도 판단을 만들지 않고, 토스 시세 연결 후 현재가 기준을 비교하는 대기 상태로 둡니다.

## 밸류에이션

`가치` 탭은 투자 중인 보유 종목의 현재가가 비싼지 싼지 보기 위한 계산판입니다. 현재가는 토스 잔고/시세 값을 사용하고, EPS·목표 PER·안전마진은 사용자가 설정 탭에서 직접 입력합니다. 기본 계산식은 `적정가 = EPS × 목표 PER`이며, 현재가가 안전마진 가격 이하이면 `싸다`, 적정가 이하이면 `적정권`, 적정가를 넘으면 `비싼 편` 또는 `비싸다`로 표시합니다.

## 검증

```bash
npm test
```

프런트엔드 변경 후에는 로컬 서버를 띄워 브라우저에서도 확인합니다.

## 공유 미리보기

```bash
npm run share
```

공유 모드에서는 임시 토큰 URL을 사용합니다. `.env`, `.env.local`, `data/store.json`, API key, 토스 credentials, 개인 계좌 데이터는 이슈나 PR에 올리지 않습니다.

## 참고

토스증권 Open API 계약과 fixture는 `docs/toss-api-contract.md`와 `docs/fixtures/toss/`에 정리되어 있습니다. `mobile/`은 이전 실험 앱 코드이며, 현재 루트 웹 앱의 기준 구현은 Exit Lens입니다.
