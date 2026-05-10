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
