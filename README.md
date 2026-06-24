# Exit Lens

토스증권 Open API, 뉴스 피드, X 같은 소셜 포스팅을 함께 읽어 보유/관심 종목의 매도 타이밍을 판단하는 로컬 우선 대시보드입니다. 브라우저는 `/api/flow-lens` 집계 결과만 받고, 토스/X credentials는 서버 환경변수에서만 사용합니다.

## 실행

```bash
cp .env.example .env.local
npm start
```

브라우저에서 `http://127.0.0.1:3000`을 여세요.

토스 실계좌 조회를 켜려면 `.env.local`에 아래 값을 넣습니다. 값이 없으면 앱은 데모 포트폴리오와 뉴스 fallback으로 동작합니다.

```bash
TOSS_CLIENT_ID=...
TOSS_CLIENT_SECRET=...
TOSS_ACCOUNT_SEQ=... # 선택
WATCHLIST_SYMBOLS=NVDA,TSLA,000660 # 선택
X_BEARER_TOKEN=... # 선택
X_SEARCH_QUERY=(market OR stocks OR semiconductor OR Fed OR KOSPI OR dollar OR AI) -is:retweet lang:en
```

## Mock 확인

웹 상단의 `Mock` 전환 버튼을 누르면 토스, 뉴스, X를 모두 고정 mock 데이터로 표시합니다. 실환경 변수가 설정되어 있어도 mock 모드에서는 외부 API를 호출하지 않습니다.

API만 확인할 때는 아래처럼 호출합니다.

```bash
curl http://127.0.0.1:3000/api/flow-lens?mock=1
```

## 앱 구조

- `public/`: Exit Lens 웹 대시보드
- `GET /api/flow-lens`: 토스 계좌/보유자산, 관심 종목, 뉴스/포스팅 테마, 종목별 매도 압력, 점검 체크리스트 집계
- `GET /api/flow-lens?mock=1`: 웹 검증용 고정 mock 스냅샷
- `server.js`: 토스 OAuth 토큰 발급, 계좌/보유자산 조회, 관심 종목 파싱, 뉴스 피드와 X recent search 수집, 매도 판단 fallback 생성

토스와 X 호출은 서버에서만 수행합니다. 브라우저에 `client_secret`, access token, `X-Tossinvest-Account`, `X_BEARER_TOKEN` 값이 내려가지 않습니다.

## 매도 판단 모델

앱은 주문을 실행하지 않고 읽기 전용 판단판만 제공합니다. 보유 종목은 수익률, 계좌 내 섹터 집중도, 뉴스/X 리스크 신호, 테마 강도를 함께 읽어 `매도 검토`, `부분 매도 검토`, `조건부 보유`, `보유 유지`로 분류합니다. 관심 종목은 보유 전 목표가, 손절가, 매도 사유를 먼저 정하도록 `진입 보류`, `기준가 대기`, `관심 유지`로 분류합니다.

## 뉴스 피드

기본 뉴스 소스는 GDELT DOC API입니다. X 포스팅은 X API v2 recent search를 사용합니다. 네트워크 오류, API 권한 부족, 결과 없음 상태에서는 로컬 데모 뉴스/포스팅으로 fallback해 화면이 비지 않게 합니다.

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
