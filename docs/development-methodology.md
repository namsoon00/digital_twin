# Development Methodology

This project uses a local-first, DDD-oriented, event-driven architecture. Future development sessions should use this file as the operating guide before changing code.

## Core Rules

- Keep business concepts in `domain/`.
- Keep use-case orchestration in `application/`.
- Keep database, files, HTTP APIs, external vendors, process management, and runtime composition in `infrastructure/`.
- Use domain events as contracts between feature slices.
- Do not pass API keys, Telegram tokens, client secrets, or raw account credentials through events, docs, tests, or git-tracked files.
- Keep old top-level Python modules only as compatibility re-export modules. New code should import from the layer package directly.
- Run `npm test` before handoff, then commit and push to `origin/main` unless explicitly told not to.

## Python Layer Map

Domain:

- `python_service/digital_twin/domain/accounts.py`: account entity/value data
- `python_service/digital_twin/domain/portfolio.py`: positions, portfolio summaries, decisions, alert events
- `python_service/digital_twin/domain/analytics.py`: scoring formulas, portfolio calculations, position normalization
- `python_service/digital_twin/domain/monitoring.py`: realtime monitoring rules and cadence filtering
- `python_service/digital_twin/domain/model_review.py`: model-change explanation, data validation, and improvement hints for alert messages
- `python_service/digital_twin/domain/events.py`: event names and event payload factories
- `python_service/digital_twin/domain/repositories.py`: application-facing ports
- `python_service/digital_twin/domain/parsing.py`: pure parsing helpers shared by domain rules

Application:

- `python_service/digital_twin/application/account_service.py`: account-management use cases
- `python_service/digital_twin/application/monitoring_service.py`: one monitoring cycle use case
- `python_service/digital_twin/application/scheduler.py`: long-running scheduling loop around a runner

Infrastructure:

- `python_service/digital_twin/infrastructure/settings.py`: env, local settings, private JSON helpers
- `python_service/digital_twin/infrastructure/sqlite_accounts.py`: SQLite account repository
- `python_service/digital_twin/infrastructure/json_monitor_state.py`: local JSON monitor state
- `python_service/digital_twin/infrastructure/toss_snapshots.py`: Toss adapter and demo snapshot fallback
- `python_service/digital_twin/infrastructure/notifications.py`: console and Telegram delivery
- `python_service/digital_twin/infrastructure/event_bus.py`: synchronous event bus and JSONL event log
- `python_service/digital_twin/infrastructure/service_factory.py`: runtime composition of use cases and adapters

Compatibility modules:

- `config.py`, `analytics.py`, `models.py`, `monitor.py`, `providers.py`, `notifiers.py`, and `scheduler.py` should remain thin re-export/factory modules only.
- Do not add new business logic to compatibility modules.

## Event-Driven Rules

Shared event contracts live in `domain/events.py`.

Current events:

- `account.saved`
- `account.removed`
- `monitoring.snapshot_collected`
- `monitoring.alerts_detected`
- `monitoring.cycle_completed`

Events are persisted locally to `data/domain-events.jsonl` through `JsonEventLog`; that file is ignored by git. Event handlers must not break publishers by default. If one feature needs another feature's result, publish or subscribe to an event instead of importing the other feature's application service.

## Parallel Development Slices

Use these slices when multiple chat windows work independently:

- Account management: `domain/accounts.py`, `application/account_service.py`, `infrastructure/sqlite_accounts.py`
- Monitoring and scheduling: `domain/monitoring.py`, `application/monitoring_service.py`, `application/scheduler.py`, `infrastructure/json_monitor_state.py`
- Notifications: `infrastructure/notifications.py` plus event handlers subscribed to monitoring events
- Providers/data collection: `infrastructure/toss_snapshots.py`
- Model scoring: `domain/analytics.py` and future model-lab application services
- Model review and validation: `domain/model_review.py` plus tests for decision-change explanations
- Runtime/configuration: `infrastructure/settings.py`, `infrastructure/service_factory.py`, `service_manager.py`

When a change touches more than one slice, keep the cross-slice contract in `domain/events.py` or `domain/repositories.py` and keep each implementation inside its own layer.

## Testing Expectations

- Add unit tests around application services when a use case changes.
- Add tests around event contracts when adding or changing event payloads.
- Add tests around model-review text when alert explanations, validation checks, or improvement hints change.
- Add infrastructure tests only for repository/adapter behavior that can run without real credentials.
- Preserve local-first behavior: no test should require real Toss, Telegram, or private account data.
