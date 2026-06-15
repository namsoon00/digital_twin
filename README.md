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

새 모바일 앱은 `mobile/`에 있습니다. 한국/미국 시장 흐름, 테마 확산, 관심 종목, 감 기록을 보는 Flutter MVP입니다.

```bash
cd mobile
flutter run
flutter analyze
flutter test
```

현재 모바일 앱은 mock 데이터 repository로 동작하며, 이후 사용자 인증과 실제 시세/뉴스 API를 연결할 수 있도록 화면과 데이터 계층을 분리했습니다.

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
