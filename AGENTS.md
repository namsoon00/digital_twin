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

## Required Handoff

After making project changes:

1. Run the relevant validation, normally `npm test`.
2. Commit the completed work and push it to `origin/main` unless the user explicitly says not to.
3. Start or restart the local server so the latest pushed state is available at `http://127.0.0.1:3000`.
4. Leave the final response with the commit hash, push result, server URL, and any validation that could not be run.

## Issue Development Workflow

When the user asks to work from a GitHub issue, use the issue as the source of truth for the development request.

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
6. Run `npm run issue:done -- <issue-number> "<short summary>"` or otherwise comment on the issue with the commit hash, validation result, local server URL, and a short change summary.
7. Restart the local server at `http://127.0.0.1:3000`.

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
