# Digiter Twin Development Notes

## Run Locally

```bash
cp .env.example .env.local
npm start
```

The app runs at `http://127.0.0.1:3000` by default. Use `HOST=0.0.0.0` only when you understand the network exposure.

## Validate Changes

```bash
npm test
```

This runs syntax checks and a local smoke test against the home page and `/api/bootstrap`.

## Development Methodology

Before changing Python service architecture or adding feature work, read and follow `docs/development-methodology.md`.

Key rules:

- Use DDD layers: `domain/` for business concepts, `application/` for use cases, `infrastructure/` for DB/files/API/vendors/runtime wiring.
- Use domain events for cross-feature contracts so multiple development sessions can work independently.
- Keep top-level Python modules such as `config.py`, `analytics.py`, `monitor.py`, `providers.py`, `notifiers.py`, and `scheduler.py` as compatibility wrappers only.
- Do not put API keys, Telegram tokens, client secrets, raw account credentials, `data/service.db`, legacy `data/store.json`, legacy `data/settings.json`, legacy `data/domain-events.jsonl`, or other private local data into git.

## Required Handoff

After making project changes:

1. Run the relevant validation, normally `npm test`.
2. Commit the completed work and push it to `origin/main` unless the user explicitly says not to.
3. Restart project-managed local runtime processes so running workers pick up the committed code:

```bash
npm run python:service:restart
npm run python:service:status
```

This restarts the realtime monitor, model review worker, and notification worker managed by `python_service/monitor_service.py`. Also restart any web, preview, share, or watcher process that the current Codex session started. Do not kill unrelated or user-started processes that cannot be safely identified; report any process that could not be restarted.
4. Send a work-complete notification through the project notifier so the owner and other local workers can see that the task finished:

```bash
npm run python:handoff:notify -- --summary "<short summary>" --commit "$(git rev-parse --short HEAD)" --validation "npm test 통과" --push "origin/main 성공"
```

Use `--dry-run` only when Telegram or the configured notifier is unavailable, and report that clearly.
The message uses the `workHandoff` type so it is distinguishable from realtime portfolio alerts.
5. Leave the final response with the commit hash, push result, restart result, handoff notification result, and any validation that could not be run.

For GitHub issue automation, the important handoff is that local Codex performed the work on a self-hosted runner, then validated, committed, pushed, and commented on the issue. Do not treat starting the app server as part of that automation unless the issue explicitly asks for runtime verification.

## Issue Development Workflow

When the user asks to work from a GitHub issue, use the issue as the source of truth for the development request.

GitHub Actions can run `.github/workflows/issue-agent.yml` from either manual `workflow_dispatch` with an issue number or by adding the `run-agent` label to an issue. That workflow is intentionally configured for a `self-hosted` runner so it can operate from the local development machine. If no self-hosted runner is online, the workflow will remain queued.

Git hooks do not receive GitHub Issue events. Use the local watcher instead when issue updates should be noticed from this machine:

```bash
npm run issue:watch
```

The watcher polls open issues with the `local-work` label. It can read public issues without a token, but private repositories and issue comments require `GITHUB_TOKEN` in `.env.local` or an authenticated `gh` CLI fallback.

For a specific issue:

1. Run `npm run issue:claim -- <issue-number>` to read the issue and post a local-started comment.
2. Work locally on `main` and push to `origin/main` unless the issue explicitly asks for a branch or PR.
3. Include the issue number in the commit message, for example `Implement stock filters for #12`.
4. Run `npm test`.
5. Push the commit.
6. Run `npm run issue:done -- <issue-number> "<short summary>"` or otherwise comment on the issue with the commit hash, validation result, local Codex execution context, and a short change summary.

Use GitHub Connector tools for issue comments when available. If Connector tools are unavailable and `gh` is authenticated, use `gh issue view <number> --comments` and `gh issue comment <number> --body-file <file>`.

The detailed workflow is documented in `docs/issue-development-workflow.md`.

## Shared Preview

The project owner can expose a temporary preview URL with:

```bash
npm run share
```

The share script starts the app on `127.0.0.1`, disables local Codex execution, creates a one-day share token, and opens a tunnel. Do not share `.env`, `.env.local`, `data/store.json`, API keys, or personal account data in issues or pull requests.

## Boundaries

- Keep the app dependency-light unless a feature clearly needs a package.
- Do not commit generated local data or secrets.
- Preserve the local-first design: private data stays on the machine unless the owner intentionally starts a tunnel.
- Treat `/api/chat` carefully because normal local mode may invoke Codex with repository context.
