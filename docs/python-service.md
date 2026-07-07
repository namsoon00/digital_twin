# Python Service Architecture

The Python service is the migration target for account-scale monitoring, scheduling, and strategy model development.

## Structure

- `python_service/digital_twin/domain/`: account, portfolio, alert domain objects and repository/provider ports
- `python_service/digital_twin/application/`: use cases that coordinate repositories, snapshot providers, monitors, and notification delivery
- `python_service/digital_twin/infrastructure/`: SQLite, local JSON state, Toss snapshot, and notification adapters
- `python_service/digital_twin/config.py`: compatibility re-export for old settings/account imports
- `python_service/digital_twin/providers.py`: compatibility re-export for Toss snapshot adapter
- `python_service/digital_twin/analytics.py`: compatibility re-export for market data, portfolio calculations, and strategy helpers
- `python_service/digital_twin/monitor.py`: compatibility re-export for monitoring rules and JSON monitor state
- `python_service/digital_twin/scheduler.py`: compatibility factory for the composed monitor runner
- `python_service/digital_twin/notifiers.py`: compatibility re-export for notification adapters
- `python_service/digital_twin/cli.py`: account and monitor CLI
- `python_service/digital_twin/infrastructure/web_server.py`: local HTTP API and static web server used by `npm start`

The development methodology is documented in `docs/development-methodology.md` and referenced from `AGENTS.md` so future local Codex sessions can load the same rules.

## DDD Boundaries

The Python service now follows a conservative DDD layout:

- Domain objects and domain services live in `domain/` and do not call SQLite, Toss, Telegram, files, or environment variables.
- Domain ports in `domain/repositories.py` define what the application needs from account storage, snapshot loading, monitor state, symbol universe storage, symbol sources, and notifications.
- Application services in `application/` own use cases such as saving account settings and running one monitoring cycle.
- Infrastructure adapters satisfy those ports with local implementations.
- Message type ownership lives in `domain/message_types.py`; notification templates and rules read that catalog instead of importing monitoring internals.
- Strategy ownership lives in `domain/strategy.py`, market-data normalization in `domain/market_data.py`, and portfolio exposure math in `domain/portfolio_calculations.py`.
- Reusable scoring signals live in `domain/scoring.py`; notification templates should consume those signals instead of deriving scores from rendered message text.
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
- Monitoring and scheduling: `domain/monitoring.py`, `domain/strategy_alerts.py`, `domain/external_signal_alerts.py`, `application/monitoring_service.py`, `monitor.py`, `scheduler.py`
- Notifications and messages: `domain/message_types.py`, `domain/notifications.py`, `domain/notification_rules.py`, `domain/notification_templates.py`, `domain/scoring.py`, `infrastructure/notifications.py`, `application/notification_service.py`, and `infrastructure/sqlite_notifications.py`
- Symbol universe: `domain/symbol_universe.py`, `application/symbol_universe_service.py`, `infrastructure/symbol_sources.py`, and `infrastructure/sqlite_symbols.py`
- Providers/data collection: `providers.py`, `infrastructure/toss_snapshots.py`
- Model scoring and strategy: `domain/market_data.py`, `domain/portfolio_calculations.py`, `domain/strategy.py`, `domain/scoring.py`, and future model-lab modules

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
npm run python:notifications:once
npm run python:notifications:status
npm run python:notifications:watch
npm run python:templates -- list
npm start
npm run python:service:start
npm run python:service:status
npm run python:service:restart
npm run python:service:stop
```

The account registry is stored in SQLite at `data/service.db` with `0600` permissions and is ignored by git.

If the SQLite database has no account rows, the service falls back to single-account settings from the `runtime_settings` table or `.env.local`. Existing `data/store.json`, `data/settings.json`, and `data/accounts.json` files are imported into SQLite on first use as legacy compatibility.

`python:monitor:watch` runs realtime monitoring in the foreground. `python:model-review:watch` runs the asynchronous model-review worker in the foreground. `python:notifications:watch` runs the single notification delivery worker.

The `python:service:*` commands run all background workers:

- realtime monitor: `data/python-monitor.pid`, `data/python-monitor.log`
- model review worker: `data/python-model-review.pid`, `data/python-model-review.log`
- notification worker: `data/python-notifications.pid`, `data/python-notifications.log`

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

The monitor detects account-scoped operational alerts and investment evidence for:

- heartbeat
- connection changes
- new/removed holdings
- quantity changes
- P/L rate moves
- market value moves
- market cash ratio moves
- decision score changes
- holding timing checks
- Alpha Vantage US quote/volume moves
- CoinGecko crypto market moves
- FRED macro rate/spread shifts
- OpenDART disclosure changes for configured Korean holdings
- external data API connection errors

Investment signals are no longer dispatched as separate portfolio alerts. `modelBuy`, `holdingTiming`, `monitorDecisionChange`, `externalCryptoMove`, and the other investment signal types are evidence nodes. The realtime monitor merges enabled evidence signals into a single `investmentInsight` event with `metadata.ontologyInsight`, `metadata.sourceSignalTypes`, and `metadata.sourceAlertEvents`. `investmentInsight` is the actual investment notification type; `externalDataConnection`, `monitorConnection`, and `monitorHeartbeat` remain operational alerts.

When a decision-change evidence signal is included in an `investmentInsight`, its source payload includes:

- why the decision changed or why exit pressure crossed the threshold
- short model data validation, such as missing price/quantity/sector fields or unusually large P/L moves
- a concise model-improvement hint for the next feature iteration

The decision-change evidence signal also queues a deeper asynchronous model review through `investmentInsight.sourceAlertEvents`. The realtime alert path does not wait for LLM/Codex output.

The app store is stored in `app_store`, runtime settings are stored in `runtime_settings`, listed symbols are stored in `symbol_universe` with source freshness in `symbol_universe_sources`, recommendation-universe quote/trend snapshots are stored in `market_quote_cache` with account id `__market_data__`, snapshots are stored in `monitor_snapshots`, cadence is stored per account, rule, and symbol in `monitor_sent`, notification templates are stored in `notification_templates`, and outgoing notification jobs are stored in `notification_jobs` inside `data/service.db`.

The market data collector runs as a managed worker through `python_service/monitor_service.py`. It refreshes stale catalog rows when configured, then rotates through the symbol universe and stores Toss `/api/v1/prices` current prices plus a smaller `/api/v1/candles` trend batch for future recommendation scoring.

Configuration:

- `MARKET_DATA_COLLECTION_ENABLED`: `1` by default.
- `MARKET_DATA_COLLECTION_INTERVAL_SECONDS`: worker interval, default 3600 seconds.
- `MARKET_DATA_COLLECTION_MARKETS`: comma-separated markets, default `KOSPI,KOSDAQ,NASDAQ`.
- `MARKET_DATA_MAX_AGE_MINUTES`: quote freshness target before a symbol is picked again, default 240.
- `MARKET_DATA_PRICE_BATCH_SIZE`: Toss prices symbols per cycle, capped at 200 by the app.
- `MARKET_DATA_CANDLE_BATCH_SIZE`: daily-candle indicator symbols per cycle, default 25.
- `MARKET_DATA_REFRESH_UNIVERSE`: refresh stale symbol catalog before collection, default `1`.

External data signals are collected in `python_service/digital_twin/infrastructure/external_signals.py` and cached in `app_store` with the `external_signals` store id. The cache is separated by the holdings/settings combination so multiple accounts do not reuse the wrong symbol set, and it prevents each realtime monitoring tick from calling every vendor API. `AccountSnapshot.external_signals` carries normalized data into the domain monitor, and `RealtimeMonitor.external_signal_events()` decides whether to create evidence signals:

- `externalEquityMove`: Alpha Vantage `GLOBAL_QUOTE` for USD/US holdings.
- `externalCryptoMove`: CoinGecko market data for configured crypto ids.
- `externalMacroShift`: FRED series observations and the 10Y-2Y spread when `DGS10` and `DGS2` are configured.
- `externalDartDisclosure`: OpenDART recent disclosures for configured Korean ticker to corp-code mappings.
- `externalDataConnection`: provider errors such as missing/invalid response, API limit, or key problems. This one remains an operational alert instead of investment evidence.

Configuration:

- `ALPHA_VANTAGE_API_KEY`: Alpha Vantage key, used for US equity quote and volume alerts.
- `COINGECKO_API_KEY`: CoinGecko demo/pro key, optional for higher quota.
- `FRED_API_KEY`: FRED key, used for macro series alerts.
- `OPENDART_API_KEY`: OpenDART certificate key, used for Korean disclosure alerts.
- `EXTERNAL_API_FETCH_INTERVAL_MINUTES`: cache duration, minimum 10 minutes, default 60.
- `EXTERNAL_FRED_SERIES`: comma-separated FRED series ids, default `DGS10,DGS2,DFF`.
- `EXTERNAL_CRYPTO_IDS`: comma-separated CoinGecko ids, default `bitcoin,ethereum`.
- `EXTERNAL_ALPHA_MAX_SYMBOLS`: max US holdings queried per refresh, default 3.
- `EXTERNAL_DART_LOOKBACK_DAYS`: OpenDART disclosure lookback, default 14.
- `EXTERNAL_DART_CORP_CODES`: ticker-to-corp-code mappings, for example `005930=00126380;000660=00164779`.

## Event Sourcing and Outbox

Monitoring snapshots, alert detections, account changes, and cycle completions are appended to `domain_events`. `SQLiteEventLog.events()` replays that stream in event order so lightweight projections and audits can be rebuilt from the event log instead of trusting only the latest mutable state.

Monitoring alerts, model-review messages, and work-handoff messages enqueue `notification_jobs` instead of sending directly. This table is the notification outbox. Jobs derived from a domain event store `source_event_id`, `source_event_name`, and a `dedupe_key`, so replaying or retrying an event does not enqueue the same outbound message twice. The notification worker renders the current `notification_templates` row for the job's `message_type` at delivery time, then calls the configured notifier. That keeps Telegram/API delivery and message formatting out of realtime monitoring and model-review workers.

Template management:

- `npm run python:templates -- list`: list all templates and supported variables.
- `python3 python_service/service.py templates save < payload.json`: save one `{ "messageType": "...", "template": "..." }`.
- `python3 python_service/service.py templates reset --message-type monitorHeartbeat`: restore a type's default template.
- Available variables include `{readableMessage}`, `{title}`, `{dataLines}`, `{triggerSummary}`, `{messageTypeLabel}`, `{symbolLine}`, `{severityLine}`, `{lines}`, `{rawLines}`, `{body}`, `{messageType}`, `{symbol}`, `{severity}`, `{accountId}`, and `{accountLabel}`.
- `GET /api/notification-schedules`: returns each message type's enabled state, cadence, last real send time, next eligible send time, recent targets, and a plain-language trigger explanation based on `monitor_sent` and `alertCadenceMinutes`.

Default alert templates use `{readableMessage}`. It renders only fields that exist for that event: title, symbol, message type, severity, trigger condition, and non-empty data lines. Existing templates that still match the old default `{title}\n{lines}` are upgraded automatically; custom templates are left unchanged.

Configuration:

- `NOTIFICATION_QUEUE_INTERVAL_SECONDS`: defaults to `30`.
- `NOTIFICATION_QUEUE_BATCH_SIZE`: defaults to `10`.
- `NOTIFICATION_SEND_GAP_SECONDS`: defaults to `1`.

## Async Model Review

Decision-change alerts are queued in the `model_review_jobs` table inside `data/service.db`.

The model-review worker processes that queue separately and enqueues a second notification message with:

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

If the configured LLM command fails or is unavailable, the worker enqueues a local deterministic model review instead of blocking the queue.

## Model Development

`analytics.py` includes a safe formula evaluator. It supports arithmetic, numeric variables, and these functions only:

- `min`
- `max`
- `abs`
- `round`

This lets user formulas evolve without allowing arbitrary Python execution.

The current service intentionally uses only Python standard library modules so the existing CI can run without installing packages. The next migration step can add `pandas`/`numpy` behind an optional model lab once the service boundary is stable.
