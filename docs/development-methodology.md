# Development Methodology

This project uses a local-first, DDD-oriented, event-driven architecture. Future development sessions should use this file as the operating guide before changing code.

## Core Rules

- Keep business concepts in `domain/`.
- Keep use-case orchestration in `application/`.
- Keep database, files, HTTP APIs, external vendors, process management, and runtime composition in `infrastructure/`.
- Use domain events as contracts between feature slices.
- Keep time-series database products behind `domain/time_series_storage.py`.
  Reasoning code consumes immutable `TemporalFeatureSnapshot` packets and must
  not import MySQL, QuestDB, or a future vendor driver. New backends are first
  registered as shadow targets, replayed from the durable outbox, compared at
  the feature boundary, and promoted through the control plane.
- Treat reasoning engines as immutable versioned deployments. TBox, RuleBox,
  prompt, feature-set, graph-store, and time-series bindings form one release
  bundle. Only the delivery deployment may emit notifications; shadow and
  candidate deployments must have a hard zero-delivery guarantee. Freeze the
  candidate RuleBox fingerprint after its first successful comparison and
  never combine comparison history from different release fingerprints under
  one deployment ID.
- A new reasoning-engine version must consume durable source events through
  its own leased queue and implement the version-neutral
  `InvestmentReasoningEngine` contract. It must not call the preceding
  version, wait for its completion, or construct its orchestration runner.
  Shared domain ports and approved TBox/RuleBox releases may be reused; input
  assembly, TypeDB execution, decision-candidate construction, health, and
  delivery authorization remain explicit replaceable stages. Stable V1/V2/V3
  deployment IDs are separate from the mutable active/delivery/candidate
  control pointers.
- Between TypeDB hypotheses and AI judgement, persist one deterministic
  `DecisionSynthesis` per account/subject/generation. It must contain only
  graph-authored candidate, allowed and blocked actions, eligible and
  reference hypotheses, evidence IDs, counter-evidence and invalidation
  conditions. Python may normalize this contract but must not score, rank, or
  invent an investment action. AI publication must reject reference-only
  hypotheses and actions outside the TypeDB action envelope.
- A reasoning request bound to `verifiedSourceSnapshot.generatedAt` must read
  that exact MySQL snapshot-history row. Never substitute a newer snapshot.
  If the point-in-time row is unavailable, defer or reject the request with an
  auditable reason before opening a TypeDB write transaction.
- Make replay inputs identical before comparing engine outputs. The active and
  shadow engines must consume the same secret-free ontology runtime context,
  original graph-input symbols, source observation clock, and temporal feature
  snapshot. A material fingerprint may omit polling provenance; a reusable
  graph cache key may not omit fields that affect freshness, session, flow, or
  data-quality facts.
- Historical decision replay must follow `docs/point-in-time-decision-replay.md`.
  Keep the market reference clock separate from the final persistence clock,
  read immutable decision facts separately from mutable outcomes/follow-ups,
  and reject every observation that was not known by the replay cutoff. Never
  rewrite a legacy decision with current engine metadata to make it appear
  exactly replayable.
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
   A single active relation or rule is a baseline candidate, not the final investment opinion. Create one current-situation hypothesis from each relevant active TypeDB rule and causal trace. Evidence sufficiency, counterfactual coverage, missing data, and policy limits are `DecisionGuardrail` records, not competing hypotheses and never selection targets. Do not maintain a fixed Python catalog of risk/recovery claims. Each hypothesis must carry an approved template ID, graph evidence IDs, counter-evidence IDs, causal trace IDs, assumptions, invalidation conditions, horizon, verification status, and validation state. The selected hypothesis and unresolved questions must be part of the structured AI response; an incomplete comparison must persist `DecisionAbstention` with no selected hypothesis.

10. Make data quality part of the graph.
   Missing feeds, stale quotes, source errors, partial symbol coverage, unmatched news, and disabled vendors should become `DataQuality`, `DataFreshness`, `Provenance`, `DataSource`, `CoverageGap`, or equivalent ABox facts. They should affect data and validation states plus dispatch policy without being hidden as logs only.

11. Persist decisions and evaluate outcomes.
    Save every final AI investment judgement as a `DecisionEpisode` with its `InvestmentQuestion`, `HypothesisSet`, selected hypothesis, inference generation, evidence IDs, and facts at decision time. Evaluate later ontology observations at configured horizons and project `ObservedOutcome` facts back into the ABox. Do not count repeated observations of one decision as multiple independent decisions.

12. Carry decision continuity into the next judgement.
    Every subsequent AI judgement for the same account and symbol must receive one bounded `DecisionContinuityPacket` containing the prior decision, selected hypothesis, observable follow-up transitions, observed outcomes, account quantity changes, execution feedback, and lifecycle reviews. Capture it once before enqueueing the immutable AI request and reuse it in the worker. A missing quantity change is not an intentional `HOLD`, and a detected quantity change is not proof that the user followed the notification. Do not reload the full portfolio lifecycle or re-run TypeDB to assemble this packet.

13. Keep learning proposals under governance.
    Repeatedly contradicted decisions may create a `LearningProposal`. AI research may also create a `NovelHypothesisProposal` when approved active TypeDB templates cannot explain the verified evidence. Neither proposal may edit TypeDB schema functions, RuleBox data, prompts, or collection policy automatically. Approval means that the proposal is eligible for rule design, not deployed. Promotion requires evidence review, historical replay, TypeDB rule preview, explicit review, and deployment audit. Runtime learning is proposal generation, not unsupervised production mutation.

14. Bound Graph RAG by the question.
    Store the complete graph and audit context, but send AI only the relevant subject, top active relations, evidence/counter-evidence subgraph, provenance, freshness, competing hypotheses, and research plan. Remove duplicated full snapshots and repeated rule payloads. Prompt-size limits are an architectural constraint; silently falling back because an unbounded graph exceeded an AI input limit is a defect.

15. Separate a matched inference from an eligible inference.
    A TypeDB schema function may remain matched for audit while its source observation has become stale, unavailable, or explicitly unusable for judgement. Every materialized match must receive an `InferenceEligibilityAssessment`. Only fresh, usable matches with complete decision metadata may enter `CoreInferenceSelection`, action envelopes, independent assessment scopes, AI action evidence, or delivery fingerprints. Ineligible matches remain visible as reference-only evidence and must not block a usable core match merely because they coexist in the same generation. If no eligible core inference remains, persist a blocked or abstained decision instead of selecting a stale rule.

16. Test the ontology contract.
    Tests for new investment behavior should verify both the source use case and the graph result: expected ABox classes, relation types, provenance/freshness fields, TypeDB schema function materialization or InferenceBox context, AI prompt payload, and final `investmentInsight` metadata. Tests should also verify the blocked path when graph inference is missing.

17. Research only when a hypothesis has a decision-changing evidence gap.
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
6. The investment brain instantiates current hypotheses from approved active TypeDB rules and causal traces, and records evidence-sufficiency or counterfactual limits as separate decision guardrails.
7. The research orchestrator reuses cached verified claims, performs bounded on-demand collection for decision-changing gaps, rejects stale/unresolved/low-quality evidence, and persists an auditable `ResearchRun`.
8. New verified evidence refreshes only the affected logical world and creates a new TypeDB InferenceBox generation for impacted subjects. Unchanged or rejected evidence does not create a false new fact, and an account projection must not copy the complete public market world.
9. AI compares support, counter-evidence, assumptions, invalidation conditions, provenance, freshness, research verification, and missing data before selecting a hypothesis and action.
10. The final opinion is stored as a `DecisionEpisode`; later observations become `ObservedOutcome` ABox facts and may create review-only learning or novel-hypothesis proposals.
11. Notification delivery applies cooldown, novelty, market-hours, and channel policy after investment meaning is already decided.

Implementation notes:

- V2 reasoning is world-partitioned. `SharedPremiseWorld` contains the account-free market, company, macro, disclosure, research, and other shared facts required by active TypeDB rules. TypeDB evaluates those rules once and emits compact shared-premise references. `PortfolioWorld` contains private position, P/L, exposure, policy, and execution facts plus only those premise references; it must not mirror raw quote, flow, news, macro, or company ABox rows.
- Rules remain one persisted semantic RuleBox contract. The runtime compiler classifies each condition by fact ownership. Shared-only rules execute in the shared-premise phase, account-only rules execute in the account-overlay phase, and mixed rules are split into a TypeDB shared-premise rule plus a TypeDB account resolver without changing the original semantic rule ID. Unknown ownership or cross-world cardinality that cannot be preserved must fail closed.
- A `SharedPremiseWorld` generation and its source ABox snapshot ID are immutable inputs to an account-overlay generation. Portfolio inference must not run until the matching shared generation is ready, and the resulting InferenceBox trace must retain both generation IDs for reverse audit.
- Changing the account-overlay projection contract requires one complete PortfolioWorld rebuild. That migration removes legacy market mirrors once; subsequent changes return to impacted-subject projection and must not reintroduce those rows.
- The current adapter stores semantic rule profiles in TypeDB-compatible RuleBox graph rows, compiles the required and negative clauses of every active rule into TypeDB 3 schema functions, calls those functions from TypeQL, and materializes matched results back into TypeDB as InferenceBox output. An `any`/`optional` N-of-M group is evaluated only after a base source match, through one source-bounded TypeQL `reduce count` query over distinct RuleBox condition entities. TypeDB, not Python, decides that group cardinality; the split keeps schema compilation and realtime CPU bounded. A bounded ABox preflight may exclude only a rule whose required RuleBox condition is already provably impossible in the active graph; it never asserts a match or replaces TypeDB schema-function judgement.
- Schema function sync is keyed by the active rule fingerprint. A process may reuse the last successful sync when the RuleBox has not changed, but `forceSchemaFunctionSync` must still force a full TypeDB schema refresh for operational repair.
- Complete per-symbol native rule-result slots may survive an application release when the RuleBox hash, TBox fingerprint, graph database, deployment, and `TYPEDB_NATIVE_RULE_ENGINE_VERSION` are unchanged. Queue, UI, prompt, or collection-only commits must not force a full RuleBox bootstrap. Any change to TypeQL generation, native rule evaluation, match semantics, or InferenceBox result interpretation must bump `TYPEDB_NATIVE_RULE_ENGINE_VERSION`; otherwise old slot coverage could be reused across incompatible executable semantics.
- Python code may compute raw observations such as moving averages, P/L, volume ratios, investor-flow deltas, freshness, materiality, and data-quality flags. Python must not independently decide final buy/sell/hold/reduce/avoid judgement for investment alerts.
- Temporal ABox builders may compute arithmetic path facts such as peak drawdown, trough rebound, recent-versus-prior velocity, crossing counts, and distinct observation counts. They must not emit preclassified trend episodes, price/flow pattern labels, evidence polarity, or action strength; TypeDB schema functions own those derivations.
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
- `python_service/digital_twin/domain/account_identity.py`: brokerage account identity, credential references, watchlist universe, and delivery-profile separation
- `python_service/digital_twin/domain/investment_mandate.py`: versioned investment policy, loss/cash/exposure limits, and allowed actions
- `python_service/digital_twin/domain/portfolio_ledger.py`: immutable ledger entries, FIFO lots, cash, cost basis, and idempotent position reconstruction
- `python_service/digital_twin/domain/portfolio_analytics.py`: stored-history portfolio return, volatility, drawdown, correlation, benchmark beta, and policy-delta calculations
- `python_service/digital_twin/domain/risk_exposure.py`: raw exposure snapshots and policy deltas consumed by TypeDB
- `python_service/digital_twin/domain/portfolio_rebalancing.py`: allocation bands, drift, and review-only rebalance proposals
- `python_service/digital_twin/domain/trade_execution.py`: action envelopes, action plans, order intents, fills, and execution episodes
- `python_service/digital_twin/domain/investment_outcomes.py`: performance attribution and decision review contracts
- `python_service/digital_twin/domain/portfolio.py`: positions, portfolio summaries, decisions, alert events
- `python_service/digital_twin/domain/investment_brain.py`: investment questions, research plans, competing hypotheses, decision episodes, observed outcomes, and governed learning proposals
- `python_service/digital_twin/domain/decision_continuity.py`: bounded prior-decision, follow-up, account-action, execution, and outcome memory contract
- `python_service/digital_twin/domain/investment_evidence_governance.py`: evidence claims, entity resolution, freshness/source quality verification, and research-run audit contracts
- `python_service/digital_twin/domain/analytics.py`: compatibility facade for legacy analytics imports only
- `python_service/digital_twin/domain/market_data.py`: market-data normalization, symbol hints, moving-average helpers, and numeric coercion
- `python_service/digital_twin/domain/portfolio_calculations.py`: portfolio exposure, FX conversion, and summary calculations
- `python_service/digital_twin/domain/strategy.py`: TypeDB inference-backed strategy compatibility facade, raw market facts, and categorical position decision state
- `python_service/digital_twin/domain/ontology_tbox.py`: bounded-context TBox vocabulary, relation definitions, and ontology reasoning rule catalog
- `python_service/digital_twin/domain/ontology_domain_tbox.py`: canonical account-to-outcome domain modules layered over the compatibility TBox
- `python_service/digital_twin/domain/ontology_rule_manifest.py`: question, fact-family, policy, world, freshness, cost, and outcome routing metadata for every rule
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
- `python_service/digital_twin/application/investment_domain_service.py`: mandate, ledger, rebalance, action-plan, execution, and outcome lifecycle use cases
- `python_service/digital_twin/application/flow_lens_service.py`: flow-lens snapshot use case with injected account, snapshot, settings, FX, and symbol dependencies
- `python_service/digital_twin/application/monitoring_service.py`: one monitoring cycle use case
- `python_service/digital_twin/application/notification/`: version-neutral notification ingress, admission, dispatch eligibility, rendering, channel dispatch, quality policy, lifecycle trace query, and queue workflow
- `python_service/digital_twin/application/notification_service.py`: compatibility facade for legacy notification-worker imports only
- `python_service/digital_twin/application/scheduler.py`: long-running scheduling loop around a runner
- `python_service/digital_twin/application/investment_research_orchestration_service.py`: cache-first bounded hypothesis research, verified-evidence persistence, and re-reasoning request orchestration
- `python_service/digital_twin/domain/hypothesis_development.py`: novel-hypothesis development lifecycle, lineage, validation gates, decision-impact classification, and deployment state
- `python_service/digital_twin/application/hypothesis_proposal_service.py`: evidence-bound novel hypothesis proposals that automatically enter the governed development pipeline
- `python_service/digital_twin/application/hypothesis_development_service.py`: automatic causal screening, disabled RuleBox candidate compilation, TypeDB preview, historical and post-proposal validation, and explicit deployment approval orchestration

Infrastructure:

- `python_service/digital_twin/infrastructure/settings.py`: env fallback and operational runtime settings facade
- `python_service/digital_twin/infrastructure/operational_store.py`: runtime factory for the MySQL operational stores
- `python_service/digital_twin/infrastructure/operational_common.py`: shared row conversion and notification helper functions used by operational store adapters
- `python_service/digital_twin/infrastructure/mysql_operational.py`: MySQL account, runtime, event, monitoring, notification, model-review, symbol, quote, evidence, and quality-sample stores
- `python_service/digital_twin/infrastructure/mysql_investment_domain.py`: versioned mandate, append-only ledger, rebalance, action-plan, execution, fill, review, and lifecycle-trace persistence
- `python_service/digital_twin/infrastructure/json_monitor_state.py`: legacy JSON monitor state compatibility only
- `python_service/digital_twin/infrastructure/toss_snapshots.py`: Toss adapter and demo snapshot fallback
- `python_service/digital_twin/application/independent_reasoning_engine.py`: independent versioned reasoning input assembly, scoped graph execution, candidate construction, and leased job orchestration
- `python_service/digital_twin/application/ai_inference_queue_service.py`: immutable notification AI request handoff, leased MAX inference, validation, and result publication
- `python_service/digital_twin/application/decision_continuity_service.py`: indexed prior-decision continuity assembler used before AI queue capture
- `python_service/digital_twin/infrastructure/notification/`: durable queue ingress adapters and concrete console/Telegram channel transports
- `python_service/digital_twin/infrastructure/notifications.py`: compatibility facade for legacy notification-infrastructure imports only
- `python_service/digital_twin/infrastructure/event_bus.py`: synchronous event bus with operational event-log default
- `python_service/digital_twin/infrastructure/model_review_queue.py`: async model-review queue interface fed by decision-change events
- `python_service/digital_twin/infrastructure/model_reviewer.py`: Codex/LLM command adapter with local fallback
- `python_service/digital_twin/infrastructure/mysql_ai_inference_queue.py`: latest-wins AI request/result outbox with subject heads, leases, heartbeat, retries, and atomic notification release
- `python_service/digital_twin/infrastructure/investment_research_gateway.py`: hypothesis-scoped composite gateway over existing official/market APIs and full-text news research
- `python_service/digital_twin/infrastructure/ontology_projection.py`: snapshot-to-ontology projection recorder that saves graph-store projections and quality samples without making monitoring application services own graph persistence details
- `python_service/digital_twin/infrastructure/ontology_graph_store.py`: graph-store composition root; runtime code should import this factory instead of constructing the database adapter directly
- `python_service/digital_twin/infrastructure/typedb_ontology.py`: TypeDB graph-store adapter; production InferenceBox output is materialized from TypeDB ABox facts and TypeDB schema functions into TypeDB InferenceBox, not from a non-TypeDB runtime fallback. InferenceBox writes must be generation-scoped so a failed materialization does not erase the last usable graph-backed judgement.
- `python_service/digital_twin/infrastructure/service_factory.py`: runtime composition of use cases and adapters

Versioned reasoning engines must own separate durable queue, graph-database,
release, and delivery-authorization boundaries. Promotion must switch the
active deployment and read-side graph binding together. A switched-out engine
must fail closed before consuming another request even if its old process is
still shutting down.

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
- `ai_inference.requested`
- `ai_inference.completed`
- `ai_inference.superseded`
- `investment.mandate_changed`
- `portfolio.ledger_recorded`
- `portfolio.risk_observed`
- `portfolio.rebalance_proposed`
- `investment.action_plan_proposed`
- `trade.execution_recorded`
- `investment.decision_reviewed`
- `investment.performance_attributed`

Events are persisted locally to the append-only `domain_events` table through the configured operational event-log adapter. Rebuild projections by replaying that event stream where practical instead of coupling features to mutable state tables. Event handlers must not break publishers by default. If one feature needs another feature's result, publish or subscribe to an event instead of importing the other feature's application service.

`monitoring.alerts_detected` now carries investment notifications only as graph-backed `investmentInsight` events. Legacy investment alert types such as `monitorDecisionChange`, `modelBuy`, and `externalCryptoMove` are not valid realtime investment dispatch inputs. The model-review queue may read legacy-shaped historical jobs for compatibility, but new realtime investment judgement must originate from graph inference. Realtime monitoring and notification delivery workers must never wait for LLM/Codex output. AI-gated investment notifications transition to `awaiting_ai`, run through the dedicated leased AI inference queue, and return to the delivery outbox only after the latest result passes the ontology/action-envelope validator. Notification producers should enqueue jobs in the notification outbox and leave external delivery to the notification worker. Jobs derived from a domain event should carry `source_event_id` and a stable `dedupe_key`.

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
