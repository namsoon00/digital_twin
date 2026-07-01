# Python Service Architecture

The Python service is the migration target for account-scale monitoring, scheduling, and strategy model development.

## Structure

- `python_service/digital_twin/domain/`: account, portfolio, alert domain objects and repository/provider ports
- `python_service/digital_twin/application/`: use cases that coordinate repositories, snapshot providers, monitors, and notification delivery
- `python_service/digital_twin/infrastructure/`: SQLite, local JSON state, Toss snapshot, and notification adapters
- `python_service/digital_twin/config.py`: compatibility re-export for old settings/account imports
- `python_service/digital_twin/providers.py`: compatibility re-export for Toss snapshot adapter
- `python_service/digital_twin/analytics.py`: compatibility re-export for domain analytics
- `python_service/digital_twin/monitor.py`: compatibility re-export for monitoring rules and JSON monitor state
- `python_service/digital_twin/scheduler.py`: compatibility factory for the composed monitor runner
- `python_service/digital_twin/notifiers.py`: compatibility re-export for notification adapters
- `python_service/digital_twin/cli.py`: account and monitor CLI

The development methodology is documented in `docs/development-methodology.md` and referenced from `AGENTS.md` so future local Codex sessions can load the same rules.

## DDD Boundaries

The Python service now follows a conservative DDD layout:

- Domain objects and domain services live in `domain/` and do not call SQLite, Toss, Telegram, files, or environment variables.
- Domain ports in `domain/repositories.py` define what the application needs from account storage, snapshot loading, monitor state, and notifications.
- Application services in `application/` own use cases such as saving account settings and running one monitoring cycle.
- Infrastructure adapters satisfy those ports with local implementations.
- Legacy import paths such as `digital_twin.models`, `digital_twin.config.AccountConfig`, and `digital_twin.scheduler.MonitorRunner` remain available as thin wrappers so the Node API and existing scripts keep working.

When adding a feature, put business vocabulary and state transitions in `domain/`, orchestration in `application/`, and vendor/file/database code in `infrastructure/` or an existing adapter. UI and API routes should call application services rather than reaching into repositories directly.

## Event-Driven Feature Slices

Use events as the contract between independently developed features:

- Shared event names and payload factories live in `python_service/digital_twin/domain/events.py`.
- Runtime dispatch lives in `python_service/digital_twin/infrastructure/event_bus.py`.
- Application services publish events after completing their own transaction or monitoring step.
- Event payloads must not include API keys, Telegram tokens, client secrets, or raw account credentials.
- Local events are stored in the `domain_events` table inside `data/service.db`.

Current event contracts:

- `account.saved`: emitted by account settings writes with masked account data.
- `account.removed`: emitted after a saved account is removed.
- `monitoring.snapshot_collected`: emitted once per collected account snapshot.
- `monitoring.alerts_detected`: emitted when a monitoring cycle finds alert events.
- `monitoring.cycle_completed`: emitted after each monitoring cycle with snapshot and alert counts.

For parallel work across multiple chat windows, keep each conversation inside one slice:

- Account management: `domain/accounts.py`, `application/account_service.py`, `infrastructure/sqlite_accounts.py`
- Monitoring and scheduling: `domain/portfolio.py`, `application/monitoring_service.py`, `monitor.py`, `scheduler.py`
- Notifications: `notifiers.py`, `infrastructure/notifications.py`, and handlers subscribed to monitoring events
- Providers/data collection: `providers.py`, `infrastructure/toss_snapshots.py`
- Model scoring: `analytics.py` and future model-lab modules

If a feature needs another feature's result, subscribe to or publish a domain event instead of importing the other feature's application service. This keeps separate development sessions from editing the same orchestration code.

## Local Commands

```bash
npm run python:accounts -- list
npm run python:accounts -- add --id main --label "Main" --client-id "$TOSS_CLIENT_ID" --client-secret "$TOSS_CLIENT_SECRET" --account-seq "$TOSS_ACCOUNT_SEQ" --watchlist "NVDA,005930"
npm run python:monitor:once -- --dry-run --force
npm run python:monitor:status
npm run python:monitor:watch
npm run python:model-review:once -- --dry-run
npm run python:model-review:status
npm run python:model-review:watch
npm run python:service:start
npm run python:service:status
npm run python:service:restart
npm run python:service:stop
```

The account registry is stored in SQLite at `data/service.db` with `0600` permissions and is ignored by git.

If the SQLite database has no account rows, the service falls back to the existing single-account settings from `data/settings.json` or `.env.local`. If an old `data/accounts.json` exists, it is imported into SQLite on first use.

`python:monitor:watch` runs realtime monitoring in the foreground. `python:model-review:watch` runs the asynchronous model-review worker in the foreground.

The `python:service:*` commands run both background workers:

- realtime monitor: `data/python-monitor.pid`, `data/python-monitor.log`
- model review worker: `data/python-model-review.pid`, `data/python-model-review.log`

## Account Database

SQLite tables:

- `service_accounts`: account id, label, provider, enabled flag, watchlist
- `toss_credentials`: Toss base URL, client id, client secret, account sequence
- `telegram_configs`: per-account notify provider, bot token, chat id, link URL

CLI:

```bash
npm run python:accounts -- list --json
npm run python:accounts -- add --id main --label "Main" --client-id "$TOSS_CLIENT_ID" --client-secret "$TOSS_CLIENT_SECRET" --account-seq "$TOSS_ACCOUNT_SEQ" --notify-provider telegram --telegram-bot-token "$TELEGRAM_BOT_TOKEN" --telegram-chat-id "$TELEGRAM_CHAT_ID"
```

Local API:

- `GET /api/service-accounts`
- `POST /api/service-accounts`
- `PUT /api/service-accounts`
- `DELETE /api/service-accounts/{id}`

The API returns masked credentials only. Writes are disabled in shared preview mode.

## Monitoring Model

Each account produces an independent snapshot:

- connection status
- holdings
- portfolio cash/sector exposure
- per-position decision
- previous snapshot comparison

The monitor emits account-scoped events for:

- heartbeat
- connection changes
- new/removed holdings
- quantity changes
- P/L rate moves
- market value moves
- market cash ratio moves
- decision score changes
- holding timing checks

When a `monitorDecisionChange` alert is emitted, the message includes:

- why the decision changed or why exit pressure crossed the threshold
- short model data validation, such as missing price/quantity/sector fields or unusually large P/L moves
- a concise model-improvement hint for the next feature iteration

The same decision-change alert also queues a deeper asynchronous model review. The realtime alert path does not wait for LLM/Codex output.

Snapshots are stored in `monitor_snapshots`, and cadence is stored per account, rule, and symbol in `monitor_sent` inside `data/service.db`.

## Async Model Review

Decision-change alerts are queued in the `model_review_jobs` table inside `data/service.db`.

The model-review worker processes that queue separately and sends a second message with:

- decision-change cause
- data validation
- model-improvement suggestions
- next experiment ideas

Configuration:

- `MODEL_REVIEW_COMMAND`: optional command that receives the model-review prompt on stdin and returns the message on stdout.
- `MODEL_REVIEW_USE_CODEX`: defaults to `1`; when no explicit command is set and `codex` is available on PATH, the worker attempts a read-only Codex analysis command.
- `MODEL_REVIEW_TIMEOUT_SECONDS`: defaults to `180`.
- `MODEL_REVIEW_INTERVAL_SECONDS`: defaults to `300`.
- `MODEL_REVIEW_BATCH_SIZE`: defaults to `1`.

If the configured LLM command fails or is unavailable, the worker sends a local deterministic model review instead of blocking the queue.

## Model Development

`analytics.py` includes a safe formula evaluator. It supports arithmetic, numeric variables, and these functions only:

- `min`
- `max`
- `abs`
- `round`

This lets user formulas evolve without allowing arbitrary Python execution.

The current service intentionally uses only Python standard library modules so the existing CI can run without installing packages. The next migration step can add `pandas`/`numpy` behind an optional model lab once the service boundary is stable.
