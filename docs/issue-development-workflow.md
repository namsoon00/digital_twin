# Issue Development Workflow

이 저장소는 GitHub Issue를 개발 요청의 단위로 사용합니다.

## 요청 남기기

1. GitHub에서 `Development Task` 또는 `Bug Report` 이슈를 작성합니다.
2. 목표, 요구사항, 완료 기준을 구체적으로 적습니다.
3. 비밀키, 계좌번호, 인증 정보, 실제 개인 식별 정보는 이슈에 남기지 않습니다.

## 로컬 작업 절차

이슈 번호를 기준으로 작업합니다. 기본 흐름은 `main`에서 작업하고 `origin/main`으로 푸시하는 방식입니다. 별도 브랜치나 PR이 필요하면 이슈 본문에 명시합니다.

## GitHub Actions 실행

`.github/workflows/issue-agent.yml`은 이슈 번호를 받아 self-hosted runner에서 작업을 실행합니다.

실행 방법은 두 가지입니다.

1. GitHub Actions에서 `Issue Agent` workflow를 수동 실행하고 `issue_number`를 입력합니다.
2. 이슈에 `run-agent` 라벨을 붙입니다.

이 workflow는 이슈 본문과 댓글을 `issue-context.md`로 export하고, 에이전트 명령에 stdin으로 전달합니다. 기본 명령은 아래와 같습니다.

```bash
codex -a never --sandbox workspace-write --cd "$GITHUB_WORKSPACE" exec --skip-git-repo-check -
```

workflow는 에이전트 실행 후 `npm test`, 커밋, `origin/main` 푸시, 이슈 완료 댓글까지 수행합니다. 실제 로컬 파일과 서버를 다루려면 GitHub hosted runner가 아니라 로컬 머신에 설치한 self-hosted runner가 필요합니다. self-hosted runner에 `codex`, Node, Git 인증이 준비되어 있지 않으면 workflow는 실패합니다.

핵심은 서버 실행이 아니라 로컬 머신의 self-hosted runner에서 `codex`가 이슈 내용을 읽고 저장소 파일을 수정하는 것입니다. 서버 실행은 이슈가 런타임 검증을 요구할 때만 별도로 수행합니다.

Git hook은 GitHub Issue 생성이나 수정 이벤트를 받지 못합니다. 이슈를 로컬 작업으로 연결하려면 로컬에서 watcher를 켜두거나 GitHub webhook 수신기를 따로 운영해야 합니다. 이 저장소는 기본값으로 로컬 watcher를 사용합니다.

```bash
npm run issue:watch
```

watcher는 `local-work` 라벨이 붙은 열린 이슈를 기본 60초마다 polling합니다. 간격과 저장소는 `.env.local`에서 바꿀 수 있습니다.

```bash
ISSUE_REPOSITORY=namsoon00/digital_twin
ISSUE_WORK_LABEL=local-work
ISSUE_WATCH_INTERVAL_MS=60000
GITHUB_TOKEN=
```

공개 저장소의 이슈 목록은 토큰 없이도 조회할 수 있습니다. 비공개 저장소 조회, 높은 API 한도, 이슈 댓글 작성에는 `GITHUB_TOKEN` 또는 인증된 `gh` CLI가 필요합니다. 토큰은 `.env.local`에만 저장하고 커밋하지 않습니다.

실제 작업은 이슈 번호를 지정해서 시작합니다.

1. `git status --short --branch`로 작업 디렉터리가 깨끗한지 확인합니다.
2. `npm run issue:claim -- <issue-number>`로 이슈 본문과 댓글을 확인하고 작업 시작 댓글을 남깁니다.
3. 필요한 파일만 수정합니다.
4. `npm test`로 검증합니다.
5. 커밋 메시지에 이슈 번호를 포함합니다. 예: `Implement stock filters for #12`
6. `git push origin main`으로 푸시합니다.
7. `npm run issue:done -- <issue-number> "변경 요약"`으로 이슈에 완료 댓글을 남깁니다.

열린 작업을 한 번만 확인하려면 아래 명령을 사용합니다.

```bash
npm run issue:list
```

## 완료 댓글 형식

```md
작업 완료했습니다.

- 커밋: <short-sha>
- 푸시: origin/main
- 검증: npm test 통과
- 실행 주체: 로컬 self-hosted runner / Codex

변경 요약:
- ...
```

검증을 실행하지 못했거나 Codex 실행이 실패한 경우, 댓글과 최종 응답에 이유를 함께 남깁니다.

## 도구 사용

GitHub Connector가 있으면 이슈 조회와 댓글 작성에 Connector를 우선 사용할 수 있습니다. 로컬 watcher와 헬퍼 스크립트는 GitHub REST API를 사용하고, 댓글 작성 인증이 없을 때는 인증된 `gh` CLI를 fallback으로 사용할 수 있습니다.

```bash
gh auth login
gh issue view <issue-number> --comments
gh issue comment <issue-number> --body-file <comment-file>
```

완전 자동으로 이슈 내용을 로컬에서 실행하고 푸시하는 방식은 기본으로 켜지 않습니다. GitHub 이슈 작성 권한이 곧 로컬 명령 실행 권한이 되는 구조라서, 작업 시작은 `issue:claim`처럼 명시적인 로컬 명령으로 둡니다.
