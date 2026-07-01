# Python Service Architecture

The Python service is the migration target for account-scale monitoring, scheduling, and strategy model development.

## Structure

- `python_service/digital_twin/domain/`: account, portfolio, alert domain objects and repository/provider ports
- `python_service/digital_twin/application/`: use cases that coordinate repositories, snapshot providers, monitors, and notification delivery
- `python_service/digital_twin/infrastructure/`: SQLite, local JSON state, Toss snapshot, and notification adapters
- `python_service/digital_twin/config.py`: local env, settings, and the current SQLite account registry implementation
- `python_service/digital_twin/providers.py`: Toss portfolio adapter and demo fallback
- `python_service/digital_twin/analytics.py`: portfolio summary, decision scoring, safe strategy formulas
- `python_service/digital_twin/monitor.py`: realtime anomaly detection and per-message cadence
- `python_service/digital_twin/scheduler.py`: long-running realtime scheduler
- `python_service/digital_twin/notifiers.py`: console and Telegram delivery
- `python_service/digital_twin/cli.py`: account and monitor CLI

## DDD Boundaries

The Python service now follows a conservative DDD layout:

- Domain objects are plain Python dataclasses in `domain/` and do not call SQLite, Toss, Telegram, files, or environment variables.
- Domain ports in `domain/repositories.py` define what the application needs from account storage, snapshot loading, monitor state, and notifications.
- Application services in `application/` own use cases such as saving account settings and running one monitoring cycle.
- Infrastructure adapters satisfy those ports with local implementations.
- Legacy import paths such as `digital_twin.models`, `digital_twin.config.AccountConfig`, and `digital_twin.scheduler.MonitorRunner` remain available so the Node API and existing scripts keep working.

When adding a feature, put business vocabulary and state transitions in `domain/`, orchestration in `application/`, and vendor/file/database code in `infrastructure/` or an existing adapter. UI and API routes should call application services rather than reaching into repositories directly.

## Local Commands

```bash
npm run python:accounts -- list
npm run python:accounts -- add --id main --label "Main" --client-id "$TOSS_CLIENT_ID" --client-secret "$TOSS_CLIENT_SECRET" --account-seq "$TOSS_ACCOUNT_SEQ" --watchlist "NVDA,005930"
npm run python:monitor:once -- --dry-run --force
npm run python:monitor:status
npm run python:monitor:watch
npm run python:service:start
npm run python:service:status
npm run python:service:restart
npm run python:service:stop
```

The account registry is stored in SQLite at `data/service.db` with `0600` permissions and is ignored by git.

If the SQLite database has no account rows, the service falls back to the existing single-account settings from `data/settings.json` or `.env.local`. If an old `data/accounts.json` exists, it is imported into SQLite on first use.

`python:monitor:watch` runs in the foreground. The `python:service:*` commands run the same scheduler in the background with `data/python-monitor.pid` and `data/python-monitor.log`.

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

Cadence is stored per account, rule, and symbol in `data/python-monitor-state.json`.

## Model Development

`analytics.py` includes a safe formula evaluator. It supports arithmetic, numeric variables, and these functions only:

- `min`
- `max`
- `abs`
- `round`

This lets user formulas evolve without allowing arbitrary Python execution.

The current service intentionally uses only Python standard library modules so the existing CI can run without installing packages. The next migration step can add `pandas`/`numpy` behind an optional model lab once the service boundary is stable.
