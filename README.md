# Digiter Twin

주식, 여행 계획, 자산관리, 스케줄 관리를 함께 다루는 로컬 우선 개인 비서 MVP입니다. 현재 폴더의 Node가 오래된 버전이어도 실행되도록 외부 패키지 없이 만들었습니다.

## 실행

```bash
cp .env.example .env.local
npm start
```

브라우저에서 `http://localhost:3000`을 여세요.

OpenAI API를 쓰려면 `.env.local`에 `OPENAI_API_KEY`를 넣으세요. 키가 없으면 앱은 로컬 요약 응답으로 동작합니다.

## 로컬 Codex + RAG

채팅 질문은 먼저 로컬 `codex exec`에 전달됩니다. 서버가 프로필, 기억, 저장된 항목, 로컬 파일 스니펫으로 RAG 컨텍스트를 만든 뒤 읽기 전용 Codex 실행에 넘깁니다.

- 기본값: `LOCAL_CODEX_ENABLED=1`
- 비활성화: `.env.local`에 `LOCAL_CODEX_ENABLED=0`
- 제한시간: `CODEX_TIMEOUT_MS=90000`

서버는 기본적으로 `127.0.0.1`에만 바인딩합니다. 외부 접속을 열려면 `HOST=0.0.0.0`을 명시하세요.

## 외부 개발자용 임시 미리보기

로컬 서버를 외부에서 잠깐 확인하게 하려면 아래 명령을 사용하세요.

```bash
npm run share
```

이 명령은 로컬 서버를 `127.0.0.1`에서 실행하고, 임시 공유 토큰을 붙인 터널 URL을 출력합니다. 공유 모드에서는 안전을 위해 `LOCAL_CODEX_ENABLED=0`으로 실행됩니다. 작업이 끝나면 터미널에서 `Ctrl+C`로 터널과 서버를 끄세요.

기본 터널은 `cloudflared`가 설치되어 있으면 Cloudflare Quick Tunnel을 사용하고, 없으면 `npx localtunnel`을 사용합니다. 특정 제공자를 고정하려면 `TUNNEL_PROVIDER=cloudflared npm run share` 또는 `TUNNEL_PROVIDER=localtunnel npm run share`처럼 실행하세요.

## 검증

```bash
npm test
```

GitHub Actions는 push, pull request, 수동 실행에서 같은 검증을 수행합니다.

## Flutter 모바일 앱

새 모바일 앱은 `mobile/`에 있습니다. 한국/미국 시장 흐름, 경제 피드, 세계 자금 흐름, 테마 확산, 관심 종목, 투자 전 체크리스트, 감 기록을 보는 Flutter MVP입니다. 기본 UI는 다크 모드 금융 대시보드 톤으로 실행되며, `설정` 탭에서 시스템/라이트/다크 테마를 바꿀 수 있습니다.

```bash
cd mobile
flutter run
flutter analyze
flutter test
```

현재 모바일 앱은 mock 데이터 repository로 동작하며, 이후 사용자 인증과 실제 시세/뉴스 API를 연결할 수 있도록 화면과 데이터 계층을 분리했습니다.

`피드` 탭은 매크로, 유동성, 정책, 자금 흐름, 실적, 리스크를 시간순/영향도 중심으로 보여주며 경제가 어떤 축으로 움직이는지 빠르게 확인할 수 있습니다.

`자금` 탭은 코인, 금, 채권, 특정 섹터, 국가별 주식시장으로 돈이 이동하는 흐름을 점수화해서 보여줍니다. “새 흐름 후보”에는 미국 개인의 KOSPI 접근성 확대처럼 아직 만들어지는 중인 글로벌 자금 이동 시나리오를 기록합니다.
같은 화면의 `필요 API 맵`에는 Alpha Vantage, 토스증권 Open API, FRED, CoinGecko, DefiLlama, ETF/Fund Flow API, 한국 투자자별 수급 API의 역할과 연결 상태를 노출합니다.
`코인 마켓` 카드에서는 CoinGecko API로 BTC, ETH, SOL, XRP, BNB, USDC, USDT, DOGE의 가격, 시총, 거래량, 1h/24h/7d 변화율을 확인할 수 있습니다.
`종합 플로우 캔들`은 캔들형 종합 흐름 지수, 유동성 막대, AI/코인/금/KOSPI/리스크 라인을 하나의 그래프에 겹쳐 보여주며 1M, 2M, 3M, ALL 기간과 세부 구간을 조정할 수 있습니다.

`체크` 탭은 투자하기 전 확인할 기본 체크리스트, 날짜별 메모, 사용자 추가 항목, 월간 캘린더 상태를 관리합니다. 체크 화면 상단에서는 API 상태, 시장 펄스, 자금 흐름, 강세 테마, 관심 종목 신호를 바로 확인할 수 있고, 체크 상태와 메모는 기기 로컬 설정에 저장되어 날짜별로 다시 볼 수 있습니다.

실시간/지연 시세는 Alpha Vantage `GLOBAL_QUOTE` API를 사용합니다. 앱 화면의 API 카드에서 사용 중인 provider, endpoint, 연결 상태, 업데이트 시각을 확인할 수 있습니다.

```bash
cd mobile
flutter run --dart-define=ALPHA_VANTAGE_API_KEY=<your-key>
```

API key가 없으면 앱은 mock 가격을 유지하고 화면에 `API key 필요` 상태를 표시합니다.

앱의 `설정` 탭에서는 Alpha Vantage, 토스증권 Open API, FRED, CoinGecko, DefiLlama, ETF/Fund Flow API, 한국 투자자별 수급 API의 key를 모두 입력할 수 있습니다. 각 API의 공급자, 커버리지, 사용 화면, key 발급 위치, 문서 URL은 읽기 전용으로 표시되고, 입력값은 기기 로컬 설정에 저장됩니다.

앱의 `설정` 탭에는 토스증권 Open API 직접 호출 옵션이 있습니다. Flutter 앱은 계정 별칭, 계좌 식별값, Open API 기본 URL, 연결 테스트 경로, 앱 키, 앱 시크릿, 액세스 토큰, 읽기 전용 여부를 기기 로컬 설정에 저장합니다. 백엔드 서버 없이 호출할 수 있지만, GitHub Pages 같은 웹 배포에서는 키가 브라우저 저장소와 네트워크 요청에 노출될 수 있습니다.

### GitHub Pages

모바일 앱의 웹 빌드는 GitHub Pages용으로 `gh-pages` 브랜치에 배포됩니다.

```bash
cd mobile
flutter build web --release --base-href /digital_twin/ --dart-define=ALPHA_VANTAGE_API_KEY=<your-key>
```

배포 URL은 `https://namsoon00.github.io/digital_twin/`입니다.

GitHub Pages가 아직 활성화되어 있지 않으면 저장소 Settings > Pages에서 source를 `Deploy from a branch`, branch를 `gh-pages`, folder를 `/`로 설정하세요.
GitHub Actions 배포에서 최신 시세를 켜려면 repository secret `ALPHA_VANTAGE_API_KEY`를 설정하세요.

## 이슈 기반 개발

개발 요청은 GitHub Issue의 `Development Task` 또는 `Bug Report` 템플릿으로 남길 수 있습니다. 로컬 작업자는 이슈 내용을 기준으로 구현하고, `npm test`를 통과시킨 뒤 `origin/main`에 푸시하고 이슈에 완료 댓글을 남깁니다.

GitHub Actions에서 `Issue Agent` workflow를 수동 실행하고 이슈 번호를 입력하면, 로컬 머신의 self-hosted runner가 이슈를 읽고 `codex`를 실행해 저장소 파일을 수정합니다. 이슈에 `run-agent` 라벨을 붙여도 같은 workflow가 실행됩니다. 이 방식은 로컬 머신에 GitHub self-hosted runner와 `codex` 명령이 준비되어 있어야 실제 코드 수정까지 진행됩니다.

Git hook은 GitHub Issue 이벤트를 받지 못하므로, 이슈 변경을 로컬에서 감지하려면 watcher를 켜둡니다.

```bash
npm run issue:watch
```

watcher는 `local-work` 라벨이 붙은 열린 이슈를 60초마다 확인합니다. 특정 이슈 작업은 `npm run issue:claim -- <issue-number>`로 시작하고, 완료 후 `npm run issue:done -- <issue-number> "변경 요약"`으로 댓글을 남깁니다.

공개 이슈 조회는 토큰 없이도 가능하지만, 비공개 저장소 조회나 댓글 작성에는 인증이 필요합니다. `.env.local`에 `GITHUB_TOKEN`을 넣거나 GitHub CLI(`gh`)를 인증해두세요.

자세한 절차는 `docs/issue-development-workflow.md`에 정리되어 있습니다.

## 주식 데이터

주식 탭은 단순 메모장이 아니라 관심 종목의 가격 요약과 최근 뉴스를 보여줍니다.

- 미국 등 해외 종목: Stooq 지연 시세
- 국내 6자리 종목코드: Naver Finance
- 기업 뉴스: Google News RSS 검색 결과

주식 탭의 셀렉트 박스에서 대표 한국/미국 종목을 고를 수 있고, 목록에 없으면 `직접 입력`을 선택해 넣을 수 있습니다.

예: `AAPL`, `TSLA`, `005930`

종목을 추가할 때 `보유 주식`과 `관심 주식`을 구분할 수 있습니다. 보유 주식은 보유 수량과 평균단가를 함께 기록하고, 관심 주식은 목표가와 관찰 메모를 중심으로 관리합니다.

표시 데이터는 상황 파악용이며 매수/매도 판단을 대신하지 않습니다.

## 데이터

데이터는 `data/store.json`에 저장합니다. 민감한 자산 정보는 실제 계좌번호나 인증 정보 없이 요약/메모 형태로만 입력하는 것을 권장합니다.
