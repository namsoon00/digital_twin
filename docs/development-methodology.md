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
- After commit and push, restart project-managed local runtime processes with `npm run python:service:restart`, then confirm with `npm run python:service:status`. Also restart any web, preview, share, or watcher process that the current Codex session started. Do not kill unrelated or user-started processes that cannot be safely identified; report any process that could not be restarted.
- After commit and push, send a work-complete notification with `npm run python:handoff:notify -- --summary "<short summary>" --commit "$(git rev-parse --short HEAD)" --validation "npm test 통과" --push "origin/main 성공"` so other local workers can see the task is finished.
- Notification wording must keep investment model scores separate from notification delivery priority. Follow `docs/notification-terminology.md` when changing alert messages, rule labels, or notification UI.

## Python Layer Map

Domain:

- `python_service/digital_twin/domain/accounts.py`: account entity/value data
- `python_service/digital_twin/domain/portfolio.py`: positions, portfolio summaries, decisions, alert events
- `python_service/digital_twin/domain/analytics.py`: compatibility facade for legacy analytics imports only
- `python_service/digital_twin/domain/market_data.py`: market-data normalization, symbol hints, moving-average helpers, and numeric coercion
- `python_service/digital_twin/domain/portfolio_calculations.py`: portfolio exposure, FX conversion, and summary calculations
- `python_service/digital_twin/domain/strategy.py`: scoring formulas, strategy feature variables, and position decision rules
- `python_service/digital_twin/domain/ontology_tbox.py`: bounded-context TBox vocabulary, relation definitions, and ontology reasoning rule catalog
- `python_service/digital_twin/domain/ontology_contracts.py`: ontology graph data contracts such as entities, relations, evidence, beliefs, opinions, and portfolio ontology snapshots
- `python_service/digital_twin/domain/ontology_schema.py`: TBox/ABox payloads, bounded-context property assignment, and basic ontology graph mutation helpers
- `python_service/digital_twin/domain/ontology_relation_contracts.py`: ontology relation-reasoning data contracts, prompt template contracts, score bands, decision stages, and threshold constants
- `python_service/digital_twin/domain/ontology_relation_catalog.py`: bootstrap ontology relation catalog, score-band catalog, and decision-stage catalog used to seed ontology/RuleBox views; new runtime logic should not be added here first
- `python_service/digital_twin/domain/ontology_prompt_registry.py`: default AI prompt registry text, prompt guardrails, and prompt policy defaults
- `python_service/digital_twin/domain/ontology_relation_facts.py`: position, temporal, liquidity, macro, research-evidence, and missing-data facts used by ontology relation evaluation
- `python_service/digital_twin/domain/portfolio_ontology_builder.py`: portfolio snapshot to ontology builder; Neo4j projection must call it in ABox-facts-only mode and leave opinions, insights, and inference to graph-store/AI stages
- `python_service/digital_twin/domain/portfolio_ontology_catalog.py`: portfolio ontology projection catalogs for metrics, runtime settings, operational pipelines, insight types, factors, and sectors
- `python_service/digital_twin/domain/portfolio_ontology_market_concepts.py`: market metric, trend, data-source, model-score, price-level, and liquidity ABox concept builders
- `python_service/digital_twin/domain/portfolio_ontology_runtime_concepts.py`: runtime settings, account delivery profile, operational pipeline, strategy world, and decision-item ABox concept builders
- `python_service/digital_twin/domain/ontology_prompting.py`: ontology read models for reasoning cards, AI inference packets, worldview summaries, and prompt payloads
- `python_service/digital_twin/domain/external_signal_quality.py`: external signal provenance, freshness, source-health, and symbol-coverage scoring
- `python_service/digital_twin/domain/ontology_quality.py`: AI opinion readiness and ontology graph quality sample metrics
- `python_service/digital_twin/domain/ontology_relation_reasoning.py`: Python fallback adapter that turns ABox facts into relation context, missing-data context, and AI prompt context; prefer Neo4j RuleBox/InferenceBox for new reasoning behavior
- `python_service/digital_twin/domain/ontology_inference_context.py`: Neo4j InferenceBox to relation-context adapter; runtime monitoring should prefer this graph-store result and use Python relation reasoning only as bootstrap fallback
- `python_service/digital_twin/domain/scoring.py`: reusable scoring signals and fallback vocabularies used by notification and strategy-adjacent scores
- `python_service/digital_twin/domain/message_types.py`: shared message-type catalog, labels, default alert rules, thresholds, and cadence
- `python_service/digital_twin/domain/alert_formatting.py`: money, percentage, and compact-number formatting used by alerts
- `python_service/digital_twin/domain/monitoring.py`: realtime monitoring orchestration rules and cadence filtering
- `python_service/digital_twin/domain/strategy_alerts.py`: strategy-score alert rules
- `python_service/digital_twin/domain/external_signal_alerts.py`: external market, crypto, macro, DART, and data-connection alert rules
- `python_service/digital_twin/domain/model_review.py`: model-change explanation, data validation, and improvement hints for alert messages
- `python_service/digital_twin/domain/events.py`: event names and event payload factories
- `python_service/digital_twin/domain/repositories.py`: application-facing ports
- `python_service/digital_twin/domain/parsing.py`: pure parsing helpers shared by domain rules

Application:

- `python_service/digital_twin/application/account_service.py`: account-management use cases
- `python_service/digital_twin/application/flow_lens_service.py`: flow-lens snapshot use case with injected account, snapshot, settings, FX, and symbol dependencies
- `python_service/digital_twin/application/monitoring_service.py`: one monitoring cycle use case
- `python_service/digital_twin/application/scheduler.py`: long-running scheduling loop around a runner

Infrastructure:

- `python_service/digital_twin/infrastructure/settings.py`: env fallback and operational runtime settings facade
- `python_service/digital_twin/infrastructure/operational_store.py`: runtime selector for the MySQL operational stores, with an explicit legacy SQLite test fixture path
- `python_service/digital_twin/infrastructure/operational_common.py`: shared row conversion and notification helper functions used by operational store adapters
- `python_service/digital_twin/infrastructure/mysql_operational.py`: MySQL account, runtime, event, monitoring, notification, model-review, symbol, quote, evidence, and quality-sample stores
- `python_service/digital_twin/infrastructure/sqlite_*.py`: legacy local fixture adapters kept for compatibility tests and old data migration only
- `python_service/digital_twin/infrastructure/json_monitor_state.py`: legacy JSON monitor state compatibility only
- `python_service/digital_twin/infrastructure/toss_snapshots.py`: Toss adapter and demo snapshot fallback
- `python_service/digital_twin/application/notification_service.py`: queued notification delivery worker
- `python_service/digital_twin/infrastructure/notifications.py`: notification queue adapters plus console and Telegram delivery
- `python_service/digital_twin/infrastructure/event_bus.py`: synchronous event bus with operational event-log default
- `python_service/digital_twin/infrastructure/model_review_queue.py`: async model-review queue interface fed by decision-change events
- `python_service/digital_twin/infrastructure/model_reviewer.py`: Codex/LLM command adapter with local fallback
- `python_service/digital_twin/infrastructure/ontology_projection.py`: snapshot-to-ontology projection recorder that saves Neo4j graphs and quality samples without making monitoring application services own graph persistence details
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

Events are persisted locally to the append-only `domain_events` table through the configured operational event-log adapter. Rebuild projections by replaying that event stream where practical instead of coupling features to mutable state tables. Event handlers must not break publishers by default. If one feature needs another feature's result, publish or subscribe to an event instead of importing the other feature's application service.

`monitoring.alerts_detected` now carries investment notifications as `investmentInsight` events. Legacy investment alert types such as `monitorDecisionChange`, `modelBuy`, and `externalCryptoMove` are evidence signals inside `metadata.sourceAlertEvents`, not direct investment dispatches. The model-review queue must read decision-change evidence from `investmentInsight.sourceAlertEvents` as well as the legacy direct shape for compatibility. Realtime alerts must never wait for LLM/Codex output; deep analysis belongs in the model-review queue and worker. Notification producers should enqueue jobs in the notification outbox and leave external delivery to the notification worker. Jobs derived from a domain event should carry `source_event_id` and a stable `dedupe_key`.

Ontology projection is a read-model boundary, not the source of truth. Aggregates and use cases own transactional state inside their bounded contexts; projection code can translate snapshots and domain events into TBox/ABox graph assertions for Neo4j, AI prompts, quality samples, and console views. Do not make domain aggregates depend on Neo4j, graph storage, or prompt rendering. If ontology needs more facts, publish or persist those facts in the owning context first, then extend the projection/read model.

## Parallel Development Slices

Use these slices when multiple chat windows work independently:

- Account management: `domain/accounts.py`, `application/account_service.py`, `infrastructure/operational_store.py`, and account store adapters
- Monitoring and scheduling: `domain/monitoring.py`, `domain/strategy_alerts.py`, `domain/external_signal_alerts.py`, `application/monitoring_service.py`, `application/scheduler.py`, `infrastructure/operational_store.py`, and monitor store adapters
- Notifications and messages: `domain/message_types.py`, `domain/notifications.py`, `domain/notification_rules.py`, `domain/notification_templates.py`, `domain/scoring.py`, `application/notification_service.py`, `infrastructure/notifications.py`, `infrastructure/operational_store.py`, and notification store adapters
- Symbol universe: `domain/symbol_universe.py`, `application/symbol_universe_service.py`, `infrastructure/symbol_sources.py`, `infrastructure/operational_store.py`, and symbol store adapters
- Providers/data collection: `infrastructure/toss_snapshots.py`
- Model scoring and strategy: `domain/market_data.py`, `domain/portfolio_calculations.py`, `domain/strategy.py`, `domain/scoring.py`, and future model-lab application services
- Model review and validation: `domain/model_review.py`, `application/model_review_service.py`, `infrastructure/operational_store.py`, `infrastructure/model_review_queue.py`, `infrastructure/model_reviewer.py`, and model-review store adapters
- Runtime/configuration: `infrastructure/settings.py`, `infrastructure/service_factory.py`, `service_manager.py`

When a change touches more than one slice, keep the cross-slice contract in `domain/events.py` or `domain/repositories.py` and keep each implementation inside its own layer. If one use case must update several context stores atomically, use an explicit recorder or unit-of-work implementation in `infrastructure/` instead of putting cross-context writes into a single context repository.

## Testing Expectations

- Add unit tests around application services when a use case changes.
- Add tests around event contracts when adding or changing event payloads.
- Add tests around model-review text when alert explanations, validation checks, or improvement hints change.
- Add infrastructure tests only for repository/adapter behavior that can run without real credentials.
- Preserve local-first behavior: no test should require real Toss, Telegram, or private account data.

## Completion Notifications

Every development session that changes the project should finish with the same observable handoff:

1. Run validation.
2. Commit and push to `origin/main`.
3. Restart the managed Python runtime processes:

```bash
npm run python:service:restart
npm run python:service:status
```

4. Send the work-complete notification:

```bash
npm run python:handoff:notify -- --summary "<short summary>" --commit "$(git rev-parse --short HEAD)" --validation "npm test 통과" --push "origin/main 성공"
```

The notification is sent through the configured local notifier, usually the account-level Telegram channel, and its message body must include `타입: workHandoff`. Do not include API keys, Telegram tokens, client secrets, raw account numbers, or private account data in the summary or details. If the notifier is unavailable, use `--dry-run`, keep the console output in the final response, and state that no external notification was delivered. The final response must include the validation, commit, push, restart, and handoff results.
