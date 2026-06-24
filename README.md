# Flow Lens

토스증권 Open API와 뉴스 피드를 함께 읽어 오늘의 시장 흐름을 파악하는 로컬 우선 대시보드입니다. 브라우저는 `/api/flow-lens` 집계 결과만 받고, 토스 credentials는 서버 환경변수에서만 사용합니다.

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
```

## 앱 구조

- `public/`: Flow Lens 웹 대시보드
- `GET /api/flow-lens`: 토스 계좌/보유자산, 뉴스 테마, 흐름 점수, 점검 체크리스트 집계
- `server.js`: 토스 OAuth 토큰 발급, 계좌/보유자산 조회, 뉴스 피드 수집, fallback 생성

토스 호출은 서버에서만 수행합니다. 브라우저에 `client_secret`, access token, `X-Tossinvest-Account` 값이 내려가지 않습니다.

## 뉴스 피드

기본 뉴스 소스는 GDELT DOC API입니다. 네트워크 오류나 결과 없음 상태에서는 로컬 데모 뉴스로 fallback해 화면이 비지 않게 합니다.

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

토스증권 Open API 계약과 fixture는 `docs/toss-api-contract.md`와 `docs/fixtures/toss/`에 정리되어 있습니다. `mobile/`은 이전 실험 앱 코드이며, 현재 루트 웹 앱의 기준 구현은 Flow Lens입니다.
