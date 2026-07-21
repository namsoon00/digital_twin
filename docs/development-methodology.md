# Development Methodology

This project uses a local-first, DDD-oriented, event-driven architecture. Future development sessions should use this file as the operating guide before changing code.

## Core Rules

- Keep business concepts in `domain/`.
- Keep use-case orchestration in `application/`.
- Keep database, files, HTTP APIs, external vendors, process management, and runtime composition in `infrastructure/`.
- Use domain events as contracts between feature slices.
- Do not pass API keys, Telegram tokens, client secrets, or raw account credentials through events, docs, tests, or git-tracked files.
- Keep old top-level Python modules only as compatibility re-export modules. New code should import from the layer package directly.
- Build investment-analysis features ontology-first. New investment facts, relationships, semantic rules, AI context, and notification triggers must enter the TBox/ABox/TypeDB schema function rule/InferenceBox flow before they influence user-facing investment judgement.
- Run `npm test` before handoff, then commit and push to `origin/main` unless explicitly told not to.
- After commit and push, restart project-managed local runtime processes with `npm run python:service:restart`, then confirm with `npm run python:service:status`. Also restart any web, preview, share, or watcher process that the current Codex session started. Do not kill unrelated or user-started processes that cannot be safely identified; report any process that could not be restarted.
- After commit and push, send a work-complete notification with `npm run python:handoff:notify -- --summary "<short summary>" --commit "$(git rev-parse --short HEAD)" --validation "npm test 통과" --push "origin/main 성공"` so other local workers can see the task is finished.
- Notification wording must keep categorical investment judgement separate from notification delivery priority. Follow `docs/notification-terminology.md` when changing alert messages, rule labels, or notification UI.
- User-facing investment language must follow `docs/investment-ubiquitous-language.md`. Internal TypeDB identifiers stay stable, while alerts, AI final text, and UI use the TBox-backed Korean domain labels.

## Ontology-First Development Rules

Investment-analysis code must treat the ontology as the shared world model, not as an optional UI artifact. Any feature that can affect buy, sell, hold, reduce, rebalance, watchlist-entry, risk, opportunity, or notification judgement must be designed as graph facts and graph-derived relationships first.

Required flow for new investment behavior:

1. Define the concept in the TBox.
   Add or reuse a class, relation type, bounded context, review level, data state, decision stage, and policy vocabulary before adding runtime behavior. TBox definitions belong in `domain/ontology_tbox.py`, `domain/ontology_relation_contracts.py`, `domain/ontology_relation_catalog.py`, `domain/ontology_relation_decisions.py`, or the closest existing ontology catalog module. Runtime decision conditions and their explicit decision stages belong in the TypeDB-backed rule catalog, not a Python fallback policy. Do not introduce a new investment meaning only as a string in an alert template.

2. Materialize real-world data as ABox facts.
   Every collected or derived investment fact should become an ABox entity or relation with `ontologyBox`, `tboxClass` or `tboxClasses`, `boundedContext` when applicable, provenance, freshness, and missing-data semantics. A quote, disclosure, news item, macro series, FX rate, liquidity observation, investor-flow value, valuation assumption, account exposure, data-source status, or collection schedule should be represented as facts before it is used for judgement.

3. Persist graph facts through the projection boundary.
   Owning bounded contexts still persist their transactional state in their own stores. The ontology projection translates that state into graph-store assertions through the TypeDB adapter. New feature code must publish or persist source facts first, then extend `portfolio_ontology_builder.py` or its concept-builder modules so the projection can create ABox nodes and relations. Do not make account, monitoring, notification, or provider aggregates depend directly on TypeDB or any graph driver.

4. Put investment reasoning in TypeDB schema function rules and InferenceBox.
   New investment rules must be expressible as graph rules over ABox facts and should be persisted as TypeDB schema functions before they drive alerts or AI opinions. TypeDB 3 replaces the old TypeDB 2 `define rule` style with schema `fun`; in this project, “TypeDB native rule” means those schema functions plus generation-scoped InferenceBox materialization. The legacy RuleBox API/editor may remain as a compatibility management surface, but runtime investment judgement must read TypeDB-function-materialized InferenceBox output through `domain/ontology_inference_context.py`. Python may assemble facts and prompts, but it must not keep a parallel buy/sell/risk rule evaluator for user-facing investment decisions.

5. Keep Python thresholds out of primary investment judgement.
   Python may parse data, normalize units, compute raw market metrics, detect operational failures, and enforce delivery policies. It should not directly decide that a stock is a buy, sell, loss-cut, profit-take, risk-increase, opportunity, or sector-rotation candidate unless that decision is backed by graph-store inference or explicitly marked as an operational/system alert.

6. Treat graph inference as mandatory for investment alerts.
   Legacy message types such as `modelBuy`, `modelSell`, `monitorPnlChange`, `monitorTrendChange`, `externalCryptoMove`, and `externalDartDisclosure` must not be generated, enabled by default, or registered as standalone investment dispatch inputs. `holdingTiming` and `watchlistOntologySignal` may exist only as graph-backed evidence signals inside an `investmentInsight`. New investment notifications must be `investmentInsight` events derived from graph-backed InferenceBox relation context from the active graph store.

7. Separate investment meaning from delivery priority.
   Ontology relations describe review level, data state, evidence role, change state, conflict state, validation state, and decision stage. Delivery priority only decides whether a message is sent after cooldown, similarity, market-hours, and freshness gates. Do not present delivery ordering as an investment judgement or as a probability.

8. Send AI the graph context, not loose facts only.
   AI investment opinions should receive the relevant TBox vocabulary, ABox facts, InferenceBox relations, matched TypeDB schema function traces, evidence subgraph, missing data, freshness, provenance, and guardrails. Prompt builders should not invent facts that are absent from the graph; missing data should be explicit.

9. Compare competing hypotheses before choosing an action.
   A single active relation or rule is a baseline candidate, not the final investment opinion. Create one current-situation hypothesis from each relevant active TypeDB rule and causal trace, then keep at least one evidence-sufficiency or counterfactual safety hypothesis when the graph does not provide enough independent alternatives. Do not maintain a fixed Python catalog of risk/recovery claims. Each hypothesis must carry an approved template ID, graph evidence IDs, counter-evidence IDs, causal trace IDs, assumptions, invalidation conditions, horizon, verification status, and validation state. The selected hypothesis and unresolved questions must be part of the structured AI response.

10. Make data quality part of the graph.
   Missing feeds, stale quotes, source errors, partial symbol coverage, unmatched news, and disabled vendors should become `DataQuality`, `DataFreshness`, `Provenance`, `DataSource`, `CoverageGap`, or equivalent ABox facts. They should affect data and validation states plus dispatch policy without being hidden as logs only.

11. Persist decisions and evaluate outcomes.
    Save every final AI investment judgement as a `DecisionEpisode` with its `InvestmentQuestion`, `HypothesisSet`, selected hypothesis, inference generation, evidence IDs, and facts at decision time. Evaluate later ontology observations at configured horizons and project `ObservedOutcome` facts back into the ABox. Do not count repeated observations of one decision as multiple independent decisions.

12. Keep learning proposals under governance.
    Repeatedly contradicted decisions may create a `LearningProposal`. AI research may also create a `NovelHypothesisProposal` when approved active TypeDB templates cannot explain the verified evidence. Neither proposal may edit TypeDB schema functions, RuleBox data, prompts, or collection policy automatically. Approval means that the proposal is eligible for rule design, not deployed. Promotion requires evidence review, historical replay, TypeDB rule preview, explicit review, and deployment audit. Runtime learning is proposal generation, not unsupervised production mutation.

13. Bound Graph RAG by the question.
    Store the complete graph and audit context, but send AI only the relevant subject, top active relations, evidence/counter-evidence subgraph, provenance, freshness, competing hypotheses, and research plan. Remove duplicated full snapshots and repeated rule payloads. Prompt-size limits are an architectural constraint; silently falling back because an unbounded graph exceeded an AI input limit is a defect.

14. Test the ontology contract.
    Tests for new investment behavior should verify both the source use case and the graph result: expected ABox classes, relation types, provenance/freshness fields, TypeDB schema function materialization or InferenceBox context, AI prompt payload, and final `investmentInsight` metadata. Tests should also verify the blocked path when graph inference is missing.

15. Research only when a hypothesis has a decision-changing evidence gap.
    Reuse verified cached evidence first. When the active hypotheses conflict or require missing evidence, create bounded `ResearchTask` records and collect only the source types required by those hypotheses. Resolve the target entity, enforce source reliability and freshness, and separate verified and rejected claims. Only verified claims may enter the investment ABox. If verified evidence changes, rebuild the complete account snapshot, project it through the graph repository, run TypeDB schema functions, and ask the AI judge only after the new InferenceBox generation is available. Research failures must preserve the last usable generation and remain visible in the audit record.

Acceptable non-ontology code:

- Operational alerts such as process heartbeat, API connection failure, worker status, handoff notifications, and data-ingestion errors.
- Data adapters, normalization, schema migrations, runtime wiring, and repository implementations.
- Notification delivery gates such as cooldown, similarity suppression, market-hours policy, and Telegram/console transport.
- Backward-compatible wrappers and test/sample helpers, as long as they do not become the primary investment-decision path.

## TypeDB Schema Function Rule Contract

Runtime investment reasoning has one primary path:

1. Source contexts collect or persist facts in their own stores.
2. `portfolio_ontology_builder.py` and concept builders project those facts into ABox entities and relations.
3. `typedb_ontology.py` stores the ABox in TypeDB.
4. TypeDB schema functions read TypeDB ABox facts and materialize generation-scoped InferenceBox entities, relations, and traces.
5. `ontology_inference_context.py` reads the active InferenceBox context for monitoring, AI prompts, diagnostics, and notification metadata.
6. The investment brain instantiates current hypotheses from approved active TypeDB rules and causal traces, and adds only the minimum safety hypotheses needed for evidence sufficiency or counterfactual comparison.
7. The research orchestrator reuses cached verified claims, performs bounded on-demand collection for decision-changing gaps, rejects stale/unresolved/low-quality evidence, and persists an auditable `ResearchRun`.
8. New verified evidence triggers a complete ABox refresh and a new TypeDB InferenceBox generation; unchanged or rejected evidence does not create a false new fact.
9. AI compares support, counter-evidence, assumptions, invalidation conditions, provenance, freshness, research verification, and missing data before selecting a hypothesis and action.
10. The final opinion is stored as a `DecisionEpisode`; later observations become `ObservedOutcome` ABox facts and may create review-only learning or novel-hypothesis proposals.
11. Notification delivery applies cooldown, novelty, market-hours, and channel policy after investment meaning is already decided.

Implementation notes:

- The current adapter stores semantic rule profiles in TypeDB-compatible RuleBox graph rows, compiles every active rule into TypeDB 3 schema functions, calls those functions from TypeQL, and materializes matched results back into TypeDB as InferenceBox output. Required, `any`/`optional`, and `not` conditions are represented in TypeDB function queries; complex `any` groups are split into helper functions so TypeDB, not Python, decides the matched source set.
- Schema function sync is keyed by the active rule fingerprint. A process may reuse the last successful sync when the RuleBox has not changed, but `forceSchemaFunctionSync` must still force a full TypeDB schema refresh for operational repair.
- Python code may compute raw observations such as moving averages, P/L, volume ratios, investor-flow deltas, freshness, materiality, and data-quality flags. Python must not independently decide final buy/sell/hold/reduce/avoid judgement for investment alerts.
- Portfolio ontology projection must default to ABox-only output. Local Python graph-reasoning output has been removed; candidate rules and experiments must be checked through TypeDB schema-function sync/materialization before they can affect judgement.
- `domain/ontology_relation_reasoning.py` is a prompt/read-model helper only, and the old graph reasoner modules have been physically removed. Runtime investment judgement must not fall back to Python inference. If TypeDB schema function sync or query fails, investment judgement is blocked and diagnostics must expose the TypeDB failure with `pythonCompatibilityReasonerUsed=false`.
- InferenceBox writes are generation-scoped. A failed materialization must not delete the last usable generation, and a successful materialization should prune old generations according to retention settings.
- Legacy names that include `RuleBox` may still appear in API routes, tests, or UI labels as a compatibility management surface for editing rule JSON. New development should document and describe the runtime concept as TypeDB schema function rules.
- A feature is not complete until tests verify the ABox facts, TypeDB schema function sync/query/materialization metadata, InferenceBox context, AI prompt payload, and blocked diagnostic path.

Anti-patterns to avoid:

- Adding a new investment alert by checking a price, moving average, PnL, volume, disclosure title, or news keyword directly in `monitoring.py` or `external_signal_alerts.py` without first creating ontology facts and graph rules.
- Creating a context named `ontologyRelationContext` in Python without `graphStoreUsed=True` and without active graph-store InferenceBox evidence, then presenting it as graph-derived reasoning.
- Storing a rule only as a Python `if` statement, formula string, or notification condition when it changes investment judgement.
- Letting AI see raw source data without the corresponding TBox/ABox/TypeDB schema function/InferenceBox explanation and missing-data boundaries.
- Treating graph-store projection failure as harmless for investment judgement. If graph inference is unavailable, investment decisions should be blocked, downgraded to operational diagnostics, or clearly marked as non-investment evidence.

## Python Layer Map

Domain:

- `python_service/digital_twin/domain/accounts.py`: account entity/value data
- `python_service/digital_twin/domain/portfolio.py`: positions, portfolio summaries, decisions, alert events
- `python_service/digital_twin/domain/investment_brain.py`: investment questions, research plans, competing hypotheses, decision episodes, observed outcomes, and governed learning proposals
- `python_service/digital_twin/domain/investment_evidence_governance.py`: evidence claims, entity resolution, freshness/source quality verification, and research-run audit contracts
- `python_service/digital_twin/domain/analytics.py`: compatibility facade for legacy analytics imports only
- `python_service/digital_twin/domain/market_data.py`: market-data normalization, symbol hints, moving-average helpers, and numeric coercion
- `python_service/digital_twin/domain/portfolio_calculations.py`: portfolio exposure, FX conversion, and summary calculations
- `python_service/digital_twin/domain/strategy.py`: TypeDB inference-backed strategy compatibility facade, raw market facts, and categorical position decision state
- `python_service/digital_twin/domain/ontology_tbox.py`: bounded-context TBox vocabulary, relation definitions, and ontology reasoning rule catalog
- `python_service/digital_twin/domain/ontology_contracts.py`: ontology graph data contracts such as entities, relations, evidence, beliefs, opinions, and portfolio ontology snapshots
- `python_service/digital_twin/domain/ontology_schema.py`: TBox/ABox payloads, bounded-context property assignment, and basic ontology graph mutation helpers
- `python_service/digital_twin/domain/ontology_relation_contracts.py`: ontology relation-reasoning data contracts, prompt template contracts, categorical review/data/change states, decision stages, and raw threshold constants
- `python_service/digital_twin/domain/ontology_relation_catalog.py`: bootstrap ontology relation catalog and decision-stage catalog used to seed ontology/native-rule management views; new runtime logic should not be added here first
- `python_service/digital_twin/domain/ontology_prompt_registry.py`: default AI prompt registry text, prompt guardrails, and prompt policy defaults
- `python_service/digital_twin/domain/ontology_relation_facts.py`: position, temporal, liquidity, macro, research-evidence, and missing-data facts used by ontology relation evaluation
- `python_service/digital_twin/domain/portfolio_ontology_builder.py`: portfolio snapshot to ontology builder; graph-store projection produces ABox facts only and leaves opinions, insights, and inference to TypeDB schema-function/AI stages
- `python_service/digital_twin/domain/portfolio_ontology_cognitive_concepts.py`: decision memory, hypotheses, assumptions, unresolved questions, and outcomes projected into the ABox
- `python_service/digital_twin/domain/portfolio_ontology_catalog.py`: portfolio ontology projection catalogs for metrics, runtime settings, operational pipelines, insight types, factors, and sectors
- `python_service/digital_twin/domain/portfolio_ontology_market_concepts.py`: market metric, trend, data-source, price-level, and liquidity ABox concept builders
- `python_service/digital_twin/domain/portfolio_ontology_runtime_concepts.py`: runtime settings, account delivery profile, operational pipeline, strategy world, and decision-item ABox concept builders
- `python_service/digital_twin/domain/ontology_prompting.py`: ontology read models for reasoning cards, AI inference packets, worldview summaries, and prompt payloads
- `python_service/digital_twin/domain/external_signal_quality.py`: external signal provenance, freshness, source-health, and symbol-coverage state
- `python_service/digital_twin/domain/ontology_quality.py`: AI opinion readiness and ontology graph quality sample metrics
- `python_service/digital_twin/domain/ontology_relation_reasoning.py`: prompt/read-model helpers for relation-context formatting; it must not materialize InferenceBox output or run offline investment-rule comparisons
- `python_service/digital_twin/domain/ontology_inference_context.py`: active graph-store InferenceBox to relation-context adapter; runtime monitoring should require TypeDB-stored InferenceBox evidence for TypeDB-backed investment judgement
- `python_service/digital_twin/domain/ontology_decision_state.py`: categorical review, data, evidence, conflict, change, and validation states shared by reasoning, AI, and delivery
- `python_service/digital_twin/domain/message_types.py`: shared message-type catalog, labels, default alert rules, thresholds, and cadence
- `python_service/digital_twin/domain/alert_formatting.py`: money, percentage, and compact-number formatting used by alerts
- `python_service/digital_twin/domain/monitoring.py`: realtime monitoring orchestration rules and cadence filtering
- `python_service/digital_twin/domain/strategy_alerts.py`: compatibility alert helpers that must not create standalone investment judgement
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
- `python_service/digital_twin/application/investment_research_orchestration_service.py`: cache-first bounded hypothesis research, verified-evidence persistence, and re-reasoning request orchestration
- `python_service/digital_twin/application/hypothesis_proposal_service.py`: evidence-bound novel hypothesis proposals with mandatory human governance

Infrastructure:

- `python_service/digital_twin/infrastructure/settings.py`: env fallback and operational runtime settings facade
- `python_service/digital_twin/infrastructure/operational_store.py`: runtime factory for the MySQL operational stores
- `python_service/digital_twin/infrastructure/operational_common.py`: shared row conversion and notification helper functions used by operational store adapters
- `python_service/digital_twin/infrastructure/mysql_operational.py`: MySQL account, runtime, event, monitoring, notification, model-review, symbol, quote, evidence, and quality-sample stores
- `python_service/digital_twin/infrastructure/json_monitor_state.py`: legacy JSON monitor state compatibility only
- `python_service/digital_twin/infrastructure/toss_snapshots.py`: Toss adapter and demo snapshot fallback
- `python_service/digital_twin/application/notification_service.py`: queued notification delivery worker
- `python_service/digital_twin/infrastructure/notifications.py`: notification queue adapters plus console and Telegram delivery
- `python_service/digital_twin/infrastructure/event_bus.py`: synchronous event bus with operational event-log default
- `python_service/digital_twin/infrastructure/model_review_queue.py`: async model-review queue interface fed by decision-change events
- `python_service/digital_twin/infrastructure/model_reviewer.py`: Codex/LLM command adapter with local fallback
- `python_service/digital_twin/infrastructure/investment_research_gateway.py`: hypothesis-scoped composite gateway over existing official/market APIs and full-text news research
- `python_service/digital_twin/infrastructure/ontology_projection.py`: snapshot-to-ontology projection recorder that saves graph-store projections and quality samples without making monitoring application services own graph persistence details
- `python_service/digital_twin/infrastructure/ontology_graph_store.py`: graph-store composition root; runtime code should import this factory instead of constructing the database adapter directly
- `python_service/digital_twin/infrastructure/typedb_ontology.py`: TypeDB graph-store adapter; production InferenceBox output is materialized from TypeDB ABox facts and TypeDB schema functions into TypeDB InferenceBox, not from a non-TypeDB runtime fallback. InferenceBox writes must be generation-scoped so a failed materialization does not erase the last usable graph-backed judgement.
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

`monitoring.alerts_detected` now carries investment notifications only as graph-backed `investmentInsight` events. Legacy investment alert types such as `monitorDecisionChange`, `modelBuy`, and `externalCryptoMove` are not valid realtime investment dispatch inputs. The model-review queue may read legacy-shaped historical jobs for compatibility, but new realtime investment judgement must originate from graph inference. Realtime alerts must never wait for LLM/Codex output; deep analysis belongs in the model-review queue and worker. Notification producers should enqueue jobs in the notification outbox and leave external delivery to the notification worker. Jobs derived from a domain event should carry `source_event_id` and a stable `dedupe_key`.

Ontology projection is a read-model boundary, not the source of truth. Aggregates and use cases own transactional state inside their bounded contexts; projection code can translate snapshots and domain events into TBox/ABox graph assertions for the active graph store, AI prompts, quality samples, and console views. Do not make domain aggregates depend on TypeDB, graph storage, or prompt rendering. If ontology needs more facts, publish or persist those facts in the owning context first, then extend the projection/read model.

## Parallel Development Slices

Use these slices when multiple chat windows work independently:

- Account management: `domain/accounts.py`, `application/account_service.py`, `infrastructure/operational_store.py`, and account store adapters
- Monitoring and scheduling: `domain/monitoring.py`, `domain/strategy_alerts.py`, `domain/external_signal_alerts.py`, `application/monitoring_service.py`, `application/scheduler.py`, `infrastructure/operational_store.py`, and monitor store adapters
- Notifications and messages: `domain/message_types.py`, `domain/notifications.py`, `domain/notification_rules.py`, `domain/notification_templates.py`, `domain/notification_signal_classification.py`, `application/notification_service.py`, `infrastructure/notifications.py`, `infrastructure/operational_store.py`, and notification store adapters
- Symbol universe: `domain/symbol_universe.py`, `application/symbol_universe_service.py`, `infrastructure/symbol_sources.py`, `infrastructure/operational_store.py`, and symbol store adapters
- Providers/data collection: `infrastructure/toss_snapshots.py`
- Market state and strategy: `domain/market_data.py`, `domain/portfolio_calculations.py`, `domain/strategy.py`, `domain/ontology_decision_state.py`, and future model-lab application services
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
