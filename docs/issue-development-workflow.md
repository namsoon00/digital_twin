# Issue Development Workflow

이 저장소는 GitHub Issue를 개발 요청의 단위로 사용합니다.

## 요청 남기기

1. GitHub에서 `Development Task` 또는 `Bug Report` 이슈를 작성합니다.
2. 목표, 요구사항, 완료 기준을 구체적으로 적습니다.
3. 비밀키, 계좌번호, 인증 정보, 실제 개인 식별 정보는 이슈에 남기지 않습니다.

## 로컬 작업 절차

이슈 번호를 기준으로 작업합니다. 기본 흐름은 `main`에서 작업하고 `origin/main`으로 푸시하는 방식입니다. 별도 브랜치나 PR이 필요하면 이슈 본문에 명시합니다.

1. `git status --short --branch`로 작업 디렉터리가 깨끗한지 확인합니다.
2. 이슈 본문과 댓글을 확인합니다.
3. 필요한 파일만 수정합니다.
4. `npm test`로 검증합니다.
5. 커밋 메시지에 이슈 번호를 포함합니다. 예: `Implement stock filters for #12`
6. `git push origin main`으로 푸시합니다.
7. 이슈에 완료 댓글을 남깁니다.
8. 로컬 서버를 최신 코드로 재시작합니다.

## 완료 댓글 형식

```md
작업 완료했습니다.

- 커밋: <short-sha>
- 푸시: origin/main
- 검증: npm test 통과
- 로컬 서버: http://127.0.0.1:3000

변경 요약:
- ...
```

검증을 실행하지 못했거나 서버를 띄우지 못한 경우, 댓글과 최종 응답에 이유를 함께 남깁니다.

## 도구 사용

GitHub Connector가 있으면 이슈 조회와 댓글 작성에 Connector를 우선 사용합니다. Connector가 없고 `gh` CLI가 인증되어 있으면 아래 명령을 사용할 수 있습니다.

```bash
gh issue view <issue-number> --comments
gh issue comment <issue-number> --body-file <comment-file>
```
