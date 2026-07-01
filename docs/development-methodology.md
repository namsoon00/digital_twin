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
- After commit and push, send a work-complete notification with `npm run python:handoff:notify -- --summary "<short summary>" --commit "$(git rev-parse --short HEAD)" --validation "npm test 통과" --push "origin/main 성공"` so other local workers can see the task is finished.

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

- `python_service/digital_twin/infrastructure/settings.py`: env fallback and SQLite-backed runtime settings facade
- `python_service/digital_twin/infrastructure/sqlite_accounts.py`: SQLite account repository
- `python_service/digital_twin/infrastructure/sqlite_operational.py`: SQLite app store, runtime settings, snapshots, cadence, domain events, model-review jobs, and notification jobs
- `python_service/digital_twin/infrastructure/json_monitor_state.py`: legacy JSON monitor state compatibility only
- `python_service/digital_twin/infrastructure/toss_snapshots.py`: Toss adapter and demo snapshot fallback
- `python_service/digital_twin/application/notification_service.py`: queued notification delivery worker
- `python_service/digital_twin/infrastructure/notifications.py`: notification queue adapters plus console and Telegram delivery
- `python_service/digital_twin/infrastructure/event_bus.py`: synchronous event bus with SQLite event-log default
- `python_service/digital_twin/infrastructure/model_review_queue.py`: async model-review queue interface fed by decision-change events
- `python_service/digital_twin/infrastructure/model_reviewer.py`: Codex/LLM command adapter with local fallback
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

Events are persisted locally to the append-only `domain_events` table in `data/service.db` through `SQLiteEventLog`. Rebuild projections by replaying that event stream where practical instead of coupling features to mutable state tables. Event handlers must not break publishers by default. If one feature needs another feature's result, publish or subscribe to an event instead of importing the other feature's application service.

`monitoring.alerts_detected` also feeds asynchronous model-review jobs for `monitorDecisionChange` alerts. Realtime alerts must never wait for LLM/Codex output; deep analysis belongs in the model-review queue and worker. Notification producers should enqueue jobs in the notification outbox and leave external delivery to the notification worker. Jobs derived from a domain event should carry `source_event_id` and a stable `dedupe_key`.

## Parallel Development Slices

Use these slices when multiple chat windows work independently:

- Account management: `domain/accounts.py`, `application/account_service.py`, `infrastructure/sqlite_accounts.py`
- Monitoring and scheduling: `domain/monitoring.py`, `application/monitoring_service.py`, `application/scheduler.py`, `infrastructure/sqlite_operational.py`
- Notifications: `domain/notifications.py`, `application/notification_service.py`, `infrastructure/notifications.py`, and `infrastructure/sqlite_operational.py`
- Providers/data collection: `infrastructure/toss_snapshots.py`
- Model scoring: `domain/analytics.py` and future model-lab application services
- Model review and validation: `domain/model_review.py`, `application/model_review_service.py`, `infrastructure/sqlite_operational.py`, `infrastructure/model_review_queue.py`, `infrastructure/model_reviewer.py`
- Runtime/configuration: `infrastructure/settings.py`, `infrastructure/service_factory.py`, `service_manager.py`

When a change touches more than one slice, keep the cross-slice contract in `domain/events.py` or `domain/repositories.py` and keep each implementation inside its own layer.

## Testing Expectations

- Add unit tests around application services when a use case changes.
- Add tests around event contracts when adding or changing event payloads.
- Add tests around model-review text when alert explanations, validation checks, or improvement hints change.
- Add infrastructure tests only for repository/adapter behavior that can run without real credentials.
- Preserve local-first behavior: no test should require real Toss, Telegram, or private account data.

## Completion Notifications

Every development session that changes the project should finish with the same observable handoff:

```bash
npm run python:handoff:notify -- --summary "<short summary>" --commit "$(git rev-parse --short HEAD)" --validation "npm test 통과" --push "origin/main 성공"
```

The notification is sent through the configured local notifier, usually the account-level Telegram channel, and its message body must include `타입: workHandoff`. Do not include API keys, Telegram tokens, client secrets, raw account numbers, or private account data in the summary or details. If the notifier is unavailable, use `--dry-run`, keep the console output in the final response, and state that no external notification was delivered.
