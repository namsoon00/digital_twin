# Development Methodology

This project uses a local-first, DDD-oriented, event-driven architecture. Future development sessions should use this file as the operating guide before changing code.

## Core Rules

- Keep business concepts in `domain/`.
- Keep use-case orchestration in `application/`.
- Keep database, files, HTTP APIs, external vendors, process management, and runtime composition in `infrastructure/`.
- Use domain events as contracts between feature slices.
- Organize business implementations under the twelve packages in
  `digital_twin/modules/`; see [Business Module Architecture](module-architecture.md).
  Call another module through its explicit `public.py` or `contracts.py` only.
  Keep immediate reads and transactional edits synchronous; use durable events
  and existing jobs for slow, independently retryable follow-up work. Module
  boundaries do not imply asynchronous APIs or separate worker processes.
- Root `digital_twin/domain` and `digital_twin/application` are removed. Business
  definitions and integration events belong to their module; cross-owner
  consumers use explicit contracts. Keep the shared kernel business-independent
  and operational maintenance in `platform/application`. Do not recreate a
  global business domain, fallback import alias, or shared service locator.
- Give every executable RuleBox rule exactly one persisted `RuleClaimContract`.
  Predictive rules own a falsifiable `MarketHypothesisClaim` with an authored
  outcome contract. Policy, execution, data-quality and context rules own
  typed non-predictive claims and must never be promoted into competing market
  hypotheses. A RuleBox release with an orphan, duplicate or incomplete claim
  contract must fail semantic validation before TypeDB persistence.
- Keep catalog admission separate from empirical qualification. Durable
  `DecisionEpisode` and `ObservedOutcome` records deterministically derive
  shadow, observed, limited-active, active or quarantined qualification using
  the versioned policy stored with the claim. Never infer qualification from
  lifecycle persistence, notification counts or a Python-only score.
- Keep time-series database products behind `modules/market_data/domain/time_series_storage.py`.
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
- Persist a content-addressed release seed artifact when a reasoning release is
  registered. It must contain the exact TBox metadata, RuleBox rows, language
  governance graph, static relations, semantic-storage contract, and release
  bundle needed to reconstruct that deployment. Blue-green TypeDB rotation
  must restore protected databases only from this artifact; using the current
  source catalog to recreate an older deployment is a semantic mutation and
  must fail closed before an expensive candidate rebuild starts.
- Keep the authored RuleBox artifact fingerprint separate from the executable
  TypeDB readback fingerprint. TypeDB normalizes persisted rule rows, so those
  hashes may legitimately differ. Reconstruction verifies the authored hash
  against the static seed manifest and the executable hash against the frozen
  deployment runtime identity; never compare one kind to the other.
- A new reasoning-engine version must consume durable source events through
  its own leased queue and implement the version-neutral
  `InvestmentReasoningEngine` contract. It must not call the preceding
  version, wait for its completion, or construct its orchestration runner.
  Shared domain ports and approved TBox/RuleBox releases may be reused; input
  assembly, TypeDB execution, decision-candidate construction, health, and
  delivery authorization remain explicit replaceable stages. Stable V1/V2/V3
  deployment IDs are separate from the mutable active/delivery/candidate
  control pointers.
- Treat `reasoning_engine_job_sources` as the durable V2 ingress lineage, not
  only `reasoning_engine_jobs.source_event_id`. Queue coalescing replaces a
  job's primary source ID while carrying every predecessor into the lineage
  table. Ingress repair must check both locations or it will recreate already
  represented work forever. A newly registered deployment repairs only events
  at or after its `createdAt`; its current-state ABox bootstrap owns older
  state, while intentional historical validation belongs to the replay lane.
- Between TypeDB hypotheses and AI judgement, persist one deterministic
  `DecisionSynthesis` per account/subject/generation. It must contain only
  graph-authored candidate, allowed and blocked actions, eligible and
  reference hypotheses, evidence IDs, counter-evidence and invalidation
  conditions. Python may normalize this contract but must not score, rank, or
  invent an investment action. AI publication must reject reference-only
  hypotheses and actions outside the TypeDB action envelope.
- Treat a batched `ReasoningCase` as execution audit only. Create one immutable
  `SubjectDecisionCase` and `CandidateSetSnapshot` per account, symbol, ABox
  snapshot, inference generation, and synthesis before AI is queued. AI must
  review exactly that candidate fingerprint; hypotheses from another subject,
  account, or generation are a contract failure. Only a validated subject case
  may create one canonical `DecisionPublication` and one `DecisionEpisode`.
  Observations, suppressions, AI failures, and incomplete comparisons persist
  abstention or review-only outcomes and must never manufacture `HOLD`,
  `WATCH`, or `NO_ACTION` decision history.
- Keep AI insight generation independent from notification transport. Persist
  the `SubjectDecisionCase` and publish its inference-completed event before
  creating an `AIInsightHandoff`; this handoff may enter the AI queue without
  any notification outbox row. Persist the validated result as an
  `AIInsightEpisode`, reconcile it against the current candidate fingerprint
  and delivery policy, and create a notification job only when that final
  reconciliation is admitted. A failed, stale, duplicate, or web-only AI
  attempt must remain auditable without manufacturing a customer notification.
  A graph-proven `REVIEW_ONLY` or `OBSERVATION` subject may request AI narrative
  interpretation, but that request must retain `context-narrative` mode and may
  never originate an investment action.
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
- Keep old top-level Python modules only as compatibility re-export modules.
  New business code imports its own layer or another module's public contract.
- Build investment-analysis features ontology-first. New investment facts, relationships, semantic rules, AI context, and notification triggers must enter the TBox/ABox/direct-TypeQL/InferenceBox flow before they influence user-facing investment judgement.
- Run `npm test` before handoff, then commit and push to `origin/main` unless explicitly told not to.
- After commit and push, restart project-managed local runtime processes with `npm run python:service:restart`, then confirm with `npm run python:service:status`. Also restart any web, preview, share, or watcher process that the current Codex session started. Do not kill unrelated or user-started processes that cannot be safely identified; report any process that could not be restarted.
- After commit and push, send a work-complete notification with `npm run python:handoff:notify -- --summary "<short summary>" --commit "$(git rev-parse --short HEAD)" --validation "npm test 통과" --push "origin/main 성공"` so other local workers can see the task is finished.
- Notification wording must keep categorical investment judgement separate from notification delivery priority. Follow `docs/notification-terminology.md` when changing alert messages, rule labels, or notification UI.
- User-facing investment language must follow `docs/investment-ubiquitous-language.md`. Internal TypeDB identifiers stay stable, while alerts, AI final text, and UI use the TBox-backed Korean domain labels.

## Ontology-First Development Rules

Investment-analysis code must treat the ontology as the shared world model, not as an optional UI artifact. Any feature that can affect buy, sell, hold, reduce, rebalance, watchlist-entry, risk, opportunity, or notification judgement must be designed as graph facts and graph-derived relationships first.

Required flow for new investment behavior:

1. Define the concept in the TBox.
   Add or reuse a class, relation type, bounded context, review level, data state, decision stage, and policy vocabulary before adding runtime behavior. TBox definitions belong in `modules/model_registry/domain/ontology_tbox.py`, `modules/model_registry/domain/ontology_relation_contracts.py`, `modules/model_registry/domain/ontology_relation_catalog.py`, `modules/model_registry/domain/ontology_relation_decisions.py`, or the closest existing ontology catalog module. Runtime decision conditions and their explicit decision stages belong in the TypeDB-backed rule catalog, not a Python fallback policy. Do not introduce a new investment meaning only as a string in an alert template.

2. Materialize real-world data as ABox facts.
   Every collected or derived investment fact should become an ABox entity or relation with `ontologyBox`, `tboxClass` or `tboxClasses`, `boundedContext` when applicable, provenance, freshness, and missing-data semantics. A quote, disclosure, news item, macro series, FX rate, liquidity observation, investor-flow value, valuation assumption, account exposure, data-source status, or collection schedule should be represented as facts before it is used for judgement.

3. Persist graph facts through the projection boundary.
   Owning bounded contexts still persist their transactional state in their own stores. The ontology projection translates that state into graph-store assertions through the TypeDB adapter. New feature code must publish or persist source facts first, then extend `portfolio_ontology_builder.py` or its concept-builder modules so the projection can create ABox nodes and relations. Do not make account, monitoring, notification, or provider aggregates depend directly on TypeDB or any graph driver.

4. Split predictive evidence from semantic decision assembly.
   A falsifiable market hypothesis must be registered as a versioned statistical-model contract and evaluated over an immutable point-in-time ABox. The scorer may emit only exact `ModelHypothesisEvidence` with a `hypothesisContractId`; it cannot emit buy, sell, hold, reduce, avoid, or an action envelope. TypeDB 3 direct TypeQL rules join that evidence with account, policy, quality, and execution facts and materialize the final semantic relation and InferenceBox trace. The RuleBox API/editor remains the governed contract surface, while runtime investment judgement must read direct-TypeQL-materialized InferenceBox output through `modules/reasoning/domain/ontology_inference_context.py`. Do not add a second general Python action evaluator or a fallback that bypasses TypeDB.

5. Keep action thresholds out of model scoring and application services.
   Python may parse data, normalize units, compute raw market metrics, evaluate governed market-hypothesis contracts, detect operational failures, and enforce delivery policies. Statistical scorers must be release-versioned, point-in-time reproducible, and emit exact evidence rather than an action. Python application services must not directly decide that a stock is a buy, sell, loss-cut, profit-take, risk-increase, opportunity, or sector-rotation candidate unless TypeDB has combined the evidence into a graph-store inference or the result is explicitly an operational/system alert.

6. Treat graph inference as mandatory for investment alerts.
   Legacy message types such as `modelBuy`, `modelSell`, `monitorPnlChange`, `monitorTrendChange`, `externalCryptoMove`, and `externalDartDisclosure` must not be generated, enabled by default, or registered as standalone investment dispatch inputs. `holdingTiming` and `watchlistOntologySignal` may exist only as graph-backed evidence signals inside an `investmentInsight`. New investment notifications must be `investmentInsight` events derived from graph-backed InferenceBox relation context from the active graph store.

7. Separate investment meaning from delivery priority.
   Ontology relations describe review level, data state, evidence role, change state, conflict state, validation state, and decision stage. Delivery priority only decides whether a message is sent after cooldown and similarity gates. Market-hours and freshness checks are advisory metadata and must not block delivery. Do not present delivery ordering as an investment judgement or as a probability.

8. Send AI the graph context, not loose facts only.
   AI investment opinions should receive the relevant TBox vocabulary, ABox facts, InferenceBox relations, matched TypeDB direct TypeQL rule traces, evidence subgraph, missing data, freshness, provenance, and guardrails. Prompt builders should not invent facts that are absent from the graph; missing data should be explicit.

9. Compare competing hypotheses before choosing an action.
   A single active relation or rule is a baseline candidate, not the final investment opinion. Create one current-situation hypothesis from each relevant exact model contract and TypeDB causal trace. Evidence sufficiency, counterfactual coverage, missing data, and policy limits are `DecisionGuardrail` records, not competing hypotheses and never selection targets. Do not maintain an ungoverned Python catalog of risk/recovery claims. Each hypothesis must carry an approved template ID, model release ID, exact hypothesis contract ID, graph evidence IDs, counter-evidence IDs, causal trace IDs, assumptions, invalidation conditions, horizon, verification status, and validation state. The selected hypothesis and unresolved questions must be part of the structured AI response; an incomplete comparison must persist `DecisionAbstention` with no selected hypothesis.

10. Make data quality part of the graph.
   Missing feeds, stale quotes, source errors, partial symbol coverage, unmatched news, and disabled vendors should become `DataQuality`, `DataFreshness`, `Provenance`, `DataSource`, `CoverageGap`, or equivalent ABox facts. They should affect data and validation states plus dispatch policy without being hidden as logs only.

11. Persist decisions and evaluate outcomes.
    Save every final AI investment judgement as a `DecisionEpisode` with its `InvestmentQuestion`, `HypothesisSet`, selected hypothesis, inference generation, evidence IDs, and facts at decision time. Evaluate later ontology observations at configured horizons and project `ObservedOutcome` facts back into the ABox. Do not count repeated observations of one decision as multiple independent decisions.

12. Carry decision continuity into the next judgement.
    Every subsequent AI judgement for the same account and symbol must receive one bounded `DecisionContinuityPacket` containing the prior decision, selected hypothesis, observable follow-up transitions, observed outcomes, account quantity changes, execution feedback, and lifecycle reviews. Capture it once before enqueueing the immutable AI request and reuse it in the worker. A missing quantity change is not an intentional `HOLD`, and a detected quantity change is not proof that the user followed the notification. Do not reload the full portfolio lifecycle or re-run TypeDB to assemble this packet.
    Preserve outcome eligibility during compression: missing, delayed, and excluded observations are not successful or failed predictions. Repair data gaps against the original observation and contract, never a newer quote. Keep small point-in-time baselines independently of raw-data retention. See [the closed-loop implementation](hypothesis-closed-loop.md) for retry, storage, UI, and validation boundaries.

13. Keep learning proposals under governance.
    Repeatedly contradicted decisions may create a `LearningProposal`. AI research may also create a `NovelHypothesisProposal` when approved active TypeDB templates cannot explain the verified evidence. Neither proposal may edit TypeDB direct TypeQL rules, RuleBox data, prompts, or collection policy automatically. Approval means that the proposal is eligible for rule design, not deployed. Promotion requires evidence review, historical replay, TypeDB rule preview, explicit review, and deployment audit. Runtime learning is proposal generation, not unsupervised production mutation.

14. Bound Graph RAG by the question.
    Store the complete graph and audit context, but send AI only the relevant subject, top active relations, evidence/counter-evidence subgraph, provenance, freshness, competing hypotheses, and research plan. Remove duplicated full snapshots and repeated rule payloads. Prompt-size limits are an architectural constraint; silently falling back because an unbounded graph exceeded an AI input limit is a defect.

15. Separate a matched inference from an eligible inference.
    A TypeDB direct TypeQL rule may remain matched for audit while its source observation has become stale, unavailable, or explicitly unusable for judgement. Every materialized match must receive an `InferenceEligibilityAssessment`. Only fresh, usable matches with complete decision metadata may enter `CoreInferenceSelection`, action envelopes, independent assessment scopes, AI action evidence, or delivery fingerprints. Ineligible matches remain visible as reference-only evidence and must not block a usable core match merely because they coexist in the same generation. If no eligible core inference remains, persist a blocked or abstained decision instead of selecting a stale rule.

16. Test the ontology contract.
    Tests for new investment behavior should verify both the source use case and the graph result: expected ABox classes, relation types, provenance/freshness fields, TypeDB direct TypeQL rule materialization or InferenceBox context, AI prompt payload, and final `investmentInsight` metadata. Tests should also verify the blocked path when graph inference is missing.

17. Research only when a hypothesis has a decision-changing evidence gap.
    Reuse verified cached evidence first. When the active hypotheses conflict or require missing evidence, create bounded `ResearchTask` records and collect only the source types required by those hypotheses. Resolve the target entity, enforce source reliability and freshness, and separate verified and rejected claims. Only verified claims may enter the investment ABox. If verified evidence changes, rebuild the complete account snapshot, project it through the graph repository, run TypeDB direct TypeQL rules, and ask the AI judge only after the new InferenceBox generation is available. Research failures must preserve the last usable generation and remain visible in the audit record.

Acceptable non-ontology code:

- Operational alerts such as process heartbeat, API connection failure, worker status, handoff notifications, and data-ingestion errors.
- Data adapters, normalization, schema migrations, runtime wiring, and repository implementations.
- Notification delivery gates such as cooldown and similarity suppression, advisory market-hours and freshness checks, and Telegram/console transport.
- Backward-compatible wrappers and test/sample helpers, as long as they do not become the primary investment-decision path.

## TypeDB Direct TypeQL Rule Contract

Runtime investment reasoning has one primary path:

1. Source contexts collect or persist facts in their own stores.
2. `portfolio_ontology_builder.py` and concept builders project those facts into ABox entities and relations.
3. The model control plane evaluates affected predictive contracts once per exact subject revision and projects exact `ModelHypothesisEvidence` into a bounded fact slice.
4. `typedb_ontology.py` stores the changed subject fact slices, exact model evidence, and the private account overlay as one TypeDB current-state generation. A second shared-world generation is not part of the realtime critical path.
5. TypeDB direct TypeQL rules combine model evidence with semantic, account, policy, quality, and execution facts and materialize generation-scoped InferenceBox entities, relations, and traces.
6. `ontology_inference_context.py` reads the active InferenceBox context for monitoring, AI prompts, diagnostics, and notification metadata.
7. The investment brain instantiates current hypotheses from approved exact model contracts and TypeDB causal traces, and records evidence-sufficiency or counterfactual limits as separate decision guardrails.
8. The research orchestrator reuses cached verified claims, performs bounded on-demand collection for decision-changing gaps, rejects stale/unresolved/low-quality evidence, and persists an auditable `ResearchRun`.
9. New verified evidence refreshes only the affected logical world and creates a new TypeDB InferenceBox generation for impacted subjects. Unchanged or rejected evidence does not create a false new fact, and an account projection must not copy the complete public market world.
10. AI compares support, counter-evidence, assumptions, invalidation conditions, provenance, freshness, research verification, and missing data before selecting a hypothesis and action.
11. The final opinion is stored as a `DecisionEpisode`; later observations become `ObservedOutcome` ABox facts and may create review-only learning or novel-hypothesis proposals.
12. Notification delivery applies cooldown, novelty, and channel policy after investment meaning is already decided. Market-hours and freshness checks remain visible advisories and never defer delivery.

Implementation notes:

- V2 realtime reasoning uses `incremental-current-state-one-pass-v1`. A `SemanticChangeSet` fixes observed time, ingestion time, changed families, dependency keys, and the per-subject revision vector. Only affected subject fact slices are assembled, then account-free model evidence and the private account overlay are written and inferred in one bounded `PortfolioWorld` generation. Exact verified subject results may be reused by another account when release, revision, market input, and account input fingerprints all match.
- Rules remain one governed semantic RuleBox contract. Predictive raw conditions are the model input contract; their executable TypeDB form contains retained account conditions plus one exact `HAS_MODEL_SIGNAL` condition keyed by `hypothesisContractId`. Non-predictive semantic, policy, quality, execution, and delivery rules remain native TypeDB contracts. Unknown ownership, missing exact IDs, or cross-world cardinality that cannot be preserved must fail closed.
- Exact model-signal contracts are projected as `ModelSignalInterpretationPolicy`, not duplicated statistical calculations. Policies with the same `stock`, `holding`, or `watchlist` source context share one `ModelSignalBridge` direct TypeQL rule; the call query must still verify every policy's exact signal ID, model release, validation state, account predicates, and derivation lineage in TypeDB. See `docs/model-signal-interpretation-bridges.md`.
- `SharedPremiseWorld` is an offline reconciliation and migration tool, not a prerequisite for realtime alerts. Realtime inference must retain the `SemanticChangeSet` fingerprint, fact-slice fingerprints, active ABox snapshot ID, InferenceBox generation ID, release fingerprint, and logical store route for reverse audit.
- Changing the account-overlay projection contract requires one complete PortfolioWorld rebuild. That migration removes legacy market mirrors once; subsequent changes return to impacted-subject projection and must not reintroduce those rows.
- The current adapter stores executable resolver, semantic, policy, quality, and execution profiles in TypeDB-compatible RuleBox rows. It selects the affected contracts from event fact families, batches compatible model-signal reads, and executes the remaining predicates as direct TypeQL against the immutable active Manifest. The model scorer evaluates market-owned `any`/`optional` groups as part of the exact predictive contract. TypeDB remains the sole owner of final relation and action-envelope materialization.
- There is no generated-function compiler, function receipt, prewarm worker, or compiler readiness gate. A RuleBox change takes effect through its new rule fingerprint and direct TypeQL query plan; TBox schema maintenance remains a separate database lifecycle.
- Complete per-symbol native rule-result slots may survive an application release when the RuleBox hash, TBox fingerprint, graph database, deployment, and `TYPEDB_NATIVE_RULE_ENGINE_VERSION` are unchanged. Queue, UI, prompt, or collection-only commits must not force a full RuleBox bootstrap. Any change to TypeQL generation, native rule evaluation, match semantics, or InferenceBox result interpretation must bump `TYPEDB_NATIVE_RULE_ENGINE_VERSION`; otherwise old slot coverage could be reused across incompatible executable semantics.
- Python code may compute raw observations such as moving averages, P/L, volume ratios, investor-flow deltas, freshness, materiality, and data-quality flags. A registered model scorer may evaluate a governed predictive contract and emit exact evidence, but Python must not independently decide final buy/sell/hold/reduce/avoid judgement for investment alerts.
- Temporal ABox builders may compute arithmetic path facts such as peak drawdown, trough rebound, recent-versus-prior velocity, crossing counts, and distinct observation counts. Versioned model releases may classify those facts into exact, auditable hypothesis evidence. Evidence polarity never grants action authority; TypeDB owns semantic relation and action-envelope derivation.
- Portfolio ontology projection must default to factual ABox plus registered model-evidence output. General local Python graph reasoning remains removed; every model-evidence candidate must still pass direct TypeQL evaluation and InferenceBox materialization before it can affect judgement.
- `modules/reasoning/domain/ontology_relation_reasoning.py` is a prompt/read-model helper only, and the old graph reasoner modules have been physically removed. Runtime investment judgement must not fall back to Python inference. If a direct TypeQL query fails, investment judgement is blocked and diagnostics must expose the TypeDB failure with `pythonCompatibilityReasonerUsed=false`.
- InferenceBox writes are generation-scoped. A failed materialization must not delete the last usable generation, and a successful materialization should prune old generations according to retention settings.
- ABox scope ownership must be graph-shape independent. Shared reference facts and dynamic account facts use deterministic per-item scopes; never infer their owner from whichever neighbours happen to be present in a target-scoped projection. Only semantically selected scopes are relation-rebind roots. Integrity-only endpoint companions may be staged but must not expand the patch into unrelated relations.
- Legacy names that include `RuleBox` may still appear in API routes, tests, or UI labels as a compatibility management surface for editing rule JSON. New development should document and describe the runtime concept as TypeDB direct TypeQL rules.
- A feature is not complete until tests verify the ABox facts, direct TypeQL query/materialization metadata, InferenceBox context, AI prompt payload, and blocked diagnostic path.
- A changed source field must map to the semantic fact families stored in TypeDB, not to a producer-specific pseudo field. Keep that mapping in the versioned fact-change contract and include replay migration tests for historical durable events.
- Treat an empty successful TypeDB inference separately from an execution failure. Empty success is a valid non-alert; a missing dependency scope, failed projection, or failed query is a repairable/terminal operational state and must not silently become `no signal`.
- Long-lived workers may hold the graph-writer lease only while executing one projection turn. Release it before sleeping or polling, and publish the released state in the worker heartbeat.
- Active and candidate TypeDB data paths are separate safety domains. Cleanup code must validate both the candidate role and the `-candidate` path suffix before stopping processes or removing data.

Anti-patterns to avoid:

- Adding a new investment alert by checking a price, moving average, PnL, volume, disclosure title, or news keyword directly in `monitoring.py` or `external_signal_alerts.py` without first creating ontology facts and graph rules.
- Creating a context named `ontologyRelationContext` in Python without `graphStoreUsed=True` and without active graph-store InferenceBox evidence, then presenting it as graph-derived reasoning.
- Storing a rule only as a Python `if` statement, formula string, or notification condition when it changes investment judgement.
- Letting AI see raw source data without the corresponding TBox/ABox/TypeDB direct TypeQL rule/InferenceBox explanation and missing-data boundaries.
- Treating graph-store projection failure as harmless for investment judgement. If graph inference is unavailable, investment decisions should be blocked, downgraded to operational diagnostics, or clearly marked as non-investment evidence.

## Python Layer Map

Domain:

- `python_service/digital_twin/modules/accounts/domain/accounts.py`: account entity/value data
- `python_service/digital_twin/modules/accounts/domain/account_identity.py`: brokerage account identity, credential references, watchlist universe, and delivery-profile separation
- `python_service/digital_twin/modules/portfolio/domain/investment_mandate.py`: versioned investment policy, loss/cash/exposure limits, and allowed actions
- `python_service/digital_twin/modules/portfolio/domain/portfolio_ledger.py`: immutable ledger entries, FIFO lots, cash, cost basis, and idempotent position reconstruction
- `python_service/digital_twin/modules/portfolio/domain/portfolio_analytics.py`: stored-history portfolio return, volatility, drawdown, correlation, benchmark beta, and policy-delta calculations
- `python_service/digital_twin/modules/portfolio/domain/risk_exposure.py`: raw exposure snapshots and policy deltas consumed by TypeDB
- `python_service/digital_twin/modules/portfolio/domain/portfolio_rebalancing.py`: allocation bands, drift, and review-only rebalance proposals
- `python_service/digital_twin/modules/portfolio/domain/trade_execution.py`: action envelopes, action plans, order intents, fills, and execution episodes
- `python_service/digital_twin/modules/outcomes/domain/investment_outcomes.py`: performance attribution and decision review contracts
- `python_service/digital_twin/modules/portfolio/domain/portfolio.py`: positions, portfolio summaries, decisions, alert events
- `python_service/digital_twin/modules/decisions/domain/investment_brain.py`: investment questions, research plans, competing hypotheses, decision episodes, observed outcomes, and governed learning proposals
- `python_service/digital_twin/modules/decisions/domain/decision_continuity.py`: bounded prior-decision, follow-up, account-action, execution, and outcome memory contract
- `python_service/digital_twin/modules/news_intelligence/domain/investment_evidence_governance.py`: evidence claims, entity resolution, freshness/source quality verification, and research-run audit contracts
- `python_service/digital_twin/modules/read_models/domain/analytics.py`: compatibility facade for legacy analytics imports only
- `python_service/digital_twin/modules/market_data/domain/market_data.py`: market-data normalization, symbol hints, moving-average helpers, and numeric coercion
- `python_service/digital_twin/modules/portfolio/domain/portfolio_calculations.py`: portfolio exposure, FX conversion, and summary calculations
- `python_service/digital_twin/modules/portfolio/domain/valuation/`: independent valuation bounded context for evidence, model registry, deterministic calculations, quality gates, and ABox projection; it never emits an investment action
- `python_service/digital_twin/modules/decisions/domain/strategy.py`: TypeDB inference-backed strategy compatibility facade, raw market facts, and categorical position decision state
- `python_service/digital_twin/modules/model_registry/domain/ontology_tbox.py`: bounded-context TBox vocabulary, relation definitions, and ontology reasoning rule catalog
- `python_service/digital_twin/modules/model_registry/domain/ontology_domain_tbox.py`: canonical account-to-outcome domain modules layered over the compatibility TBox
- `python_service/digital_twin/modules/model_registry/domain/ontology_rule_manifest.py`: question, fact-family, policy, world, freshness, cost, and outcome routing metadata for every rule
- `python_service/digital_twin/modules/reasoning/domain/ontology_contracts.py`: ontology graph data contracts such as entities, relations, evidence, beliefs, opinions, and portfolio ontology snapshots
- `python_service/digital_twin/modules/reasoning/domain/ontology_schema.py`: TBox/ABox payloads, bounded-context property assignment, and basic ontology graph mutation helpers
- `python_service/digital_twin/modules/model_registry/domain/ontology_relation_contracts.py`: ontology relation-reasoning data contracts, prompt template contracts, categorical review/data/change states, decision stages, and raw threshold constants
- `python_service/digital_twin/modules/model_registry/domain/ontology_relation_catalog.py`: bootstrap ontology relation catalog and decision-stage catalog used to seed ontology/native-rule management views; new runtime logic should not be added here first
- `python_service/digital_twin/modules/model_registry/domain/ontology_prompt_registry.py`: default AI prompt registry text, prompt guardrails, and prompt policy defaults
- `python_service/digital_twin/modules/reasoning/domain/ontology_relation_facts.py`: position, temporal, liquidity, macro, research-evidence, and missing-data facts used by ontology relation evaluation
- `python_service/digital_twin/modules/reasoning/domain/portfolio_ontology_builder.py`: portfolio snapshot to ontology builder; graph-store projection produces ABox facts only and leaves opinions, insights, and inference to TypeDB direct-TypeQL/AI stages
- `python_service/digital_twin/modules/reasoning/domain/portfolio_ontology_cognitive_concepts.py`: decision memory, hypotheses, assumptions, unresolved questions, and outcomes projected into the ABox
- `python_service/digital_twin/modules/reasoning/domain/portfolio_ontology_catalog.py`: portfolio ontology projection catalogs for metrics, runtime settings, operational pipelines, insight types, factors, and sectors
- `python_service/digital_twin/modules/reasoning/domain/portfolio_ontology_market_concepts.py`: market metric, trend, data-source, price-level, and liquidity ABox concept builders
- `python_service/digital_twin/modules/reasoning/domain/portfolio_ontology_runtime_concepts.py`: runtime settings, account delivery profile, operational pipeline, strategy world, and decision-item ABox concept builders
- `python_service/digital_twin/modules/reasoning/domain/ontology_prompting.py`: ontology read models for reasoning cards, AI inference packets, worldview summaries, and prompt payloads
- `python_service/digital_twin/modules/market_data/domain/external_signal_quality.py`: external signal provenance, freshness, source-health, and symbol-coverage state
- `python_service/digital_twin/modules/reasoning/domain/ontology_quality.py`: AI opinion readiness and ontology graph quality sample metrics
- `python_service/digital_twin/modules/reasoning/domain/ontology_relation_reasoning.py`: prompt/read-model helpers for relation-context formatting; it must not materialize InferenceBox output or run offline investment-rule comparisons
- `python_service/digital_twin/modules/reasoning/domain/ontology_inference_context.py`: active graph-store InferenceBox to relation-context adapter; runtime monitoring should require TypeDB-stored InferenceBox evidence for TypeDB-backed investment judgement
- `python_service/digital_twin/modules/reasoning/domain/ontology_decision_state.py`: categorical review, data, evidence, conflict, change, and validation states shared by reasoning, AI, and delivery
- `python_service/digital_twin/modules/notifications/domain/message_types.py`: shared message-type catalog, labels, default alert rules, thresholds, and cadence
- `python_service/digital_twin/modules/notifications/domain/alert_formatting.py`: money, percentage, and compact-number formatting used by alerts
- `python_service/digital_twin/modules/market_data/domain/monitoring.py`: realtime monitoring orchestration rules and cadence filtering
- `python_service/digital_twin/modules/notifications/domain/strategy_alerts.py`: compatibility alert helpers that must not create standalone investment judgement
- `python_service/digital_twin/modules/notifications/domain/external_signal_alerts.py`: external market, crypto, macro, DART, and data-connection alert rules
- `python_service/digital_twin/modules/model_registry/domain/model_review.py`: model-change explanation, data validation, and improvement hints for alert messages
- `python_service/digital_twin/shared_kernel/events.py`: generic event envelope and deterministic identity
- `python_service/digital_twin/modules/<owner>/domain/event_types.py`: owned event names; owner event factories and bounded payload builders live beside them
- `python_service/digital_twin/modules/<owner>/contracts.py`: explicitly exported application-facing domain ports and data contracts; there is no root repository facade
- `python_service/digital_twin/shared_kernel/parsing.py`: pure parsing helpers shared by domain rules

Application:

- `python_service/digital_twin/modules/accounts/application/account_service.py`: account-management use cases
- `python_service/digital_twin/modules/portfolio/application/investment_domain_service.py`: mandate, ledger, rebalance, action-plan, execution, and outcome lifecycle use cases
- `python_service/digital_twin/modules/read_models/application/flow_lens_service.py`: flow-lens snapshot use case with injected account, snapshot, settings, FX, and symbol dependencies
- `python_service/digital_twin/modules/market_data/application/monitoring_service.py`: one monitoring cycle use case
- `python_service/digital_twin/modules/notifications/application/notification/`: version-neutral notification ingress, admission, dispatch eligibility, rendering, channel dispatch, quality policy, lifecycle trace query, and queue workflow
- `python_service/digital_twin/modules/notifications/application/notification_service.py`: compatibility facade for legacy notification-worker imports only
- `python_service/digital_twin/platform/application/scheduler.py`: long-running scheduling loop around a runner
- `python_service/digital_twin/modules/news_intelligence/application/investment_research_orchestration_service.py`: cache-first bounded hypothesis research, verified-evidence persistence, and re-reasoning request orchestration
- `python_service/digital_twin/modules/model_registry/domain/hypothesis_development.py`: novel-hypothesis development lifecycle, lineage, validation gates, decision-impact classification, and deployment state
- `python_service/digital_twin/modules/model_registry/application/hypothesis_proposal_service.py`: evidence-bound novel hypothesis proposals that automatically enter the governed development pipeline
- `python_service/digital_twin/modules/model_registry/application/hypothesis_development_service.py`: automatic causal screening, disabled RuleBox candidate compilation, TypeDB preview, historical and post-proposal validation, and explicit deployment approval orchestration

Infrastructure:

- `python_service/digital_twin/infrastructure/settings.py`: env fallback and operational runtime settings facade
- `python_service/digital_twin/infrastructure/operational_store.py`: lazy runtime factories for MySQL operational stores and separate account-reader/watchlist-command capabilities
- `python_service/digital_twin/infrastructure/operational_common.py`: shared row conversion and notification helper functions used by operational store adapters
- `python_service/digital_twin/infrastructure/mysql_operational.py`: explicit lazy exports of MySQL adapters, without loading every store on import
- `python_service/digital_twin/modules/accounts/infrastructure/mysql_account_reader.py`: read-only account queries; no mutation capability
- `python_service/digital_twin/modules/instruments/infrastructure/mysql_account_watchlist.py`: watchlist-only mutations with an injected account reader
- `python_service/digital_twin/infrastructure/account_transactions.py`: cross-owner account create/patch/delete coordinator; owner helpers and the domain event commit in one MySQL transaction
- `python_service/digital_twin/infrastructure/mysql_investment_domain.py`: versioned mandate, append-only ledger, rebalance, action-plan, execution, fill, review, and lifecycle-trace persistence
- `python_service/digital_twin/infrastructure/json_monitor_state.py`: legacy JSON monitor state compatibility only
- `python_service/digital_twin/infrastructure/toss_snapshots.py`: Toss adapter and demo snapshot fallback
- `python_service/digital_twin/modules/reasoning/application/independent_reasoning_engine.py`: independent versioned reasoning input assembly, scoped graph execution, candidate construction, and leased job orchestration
- `python_service/digital_twin/modules/decisions/application/ai_inference_queue_service.py`: immutable notification AI request handoff, leased MAX inference, validation, and result publication
- `python_service/digital_twin/modules/decisions/application/decision_continuity_service.py`: indexed prior-decision continuity assembler used before AI queue capture
- `python_service/digital_twin/modules/notifications/infrastructure/notification/`: durable queue ingress adapters and concrete console/Telegram channel transports
- `python_service/digital_twin/infrastructure/notifications.py`: compatibility facade for legacy notification-infrastructure imports only
- `python_service/digital_twin/infrastructure/event_bus.py`: synchronous event bus with operational event-log default
- `python_service/digital_twin/modules/model_registry/infrastructure/model_review_queue.py`: async model-review queue interface fed by decision-change events
- `python_service/digital_twin/modules/model_registry/infrastructure/model_reviewer.py`: Codex/LLM command adapter with local fallback
- `python_service/digital_twin/infrastructure/mysql_ai_inference_queue.py`: subject single-flight AI request/result outbox with semantic coalescing, material-change replacement, leases, heartbeat, retries, and atomic notification release
- `python_service/digital_twin/infrastructure/investment_research_gateway.py`: hypothesis-scoped composite gateway over existing official/market APIs and full-text news research
- `python_service/digital_twin/infrastructure/ontology_projection.py`: snapshot-to-ontology projection recorder that saves graph-store projections and quality samples without making monitoring application services own graph persistence details
- `python_service/digital_twin/infrastructure/ontology_graph_store.py`: graph-store composition root; runtime code should import this factory instead of constructing the database adapter directly
- `python_service/digital_twin/infrastructure/typedb_ontology.py`: TypeDB graph-store adapter; production InferenceBox output is materialized from TypeDB ABox facts and TypeDB direct TypeQL rules into TypeDB InferenceBox, not from a non-TypeDB runtime fallback. InferenceBox writes must be generation-scoped so a failed materialization does not erase the last usable graph-backed judgement.
- `python_service/digital_twin/modules/reasoning/infrastructure/typeql/`: acyclic, DB-free direct TypeQL query builders, literal/scope clauses, evidence-index plans and execution preflight. These functions never execute transactions or grant investment actions. They may depend on shared domain contracts and pure graph payload conversions, but not on the graph repository, settings, application workflows or runtime composition. Structural extraction must preserve query/plan byte contracts; semantic changes still require a versioned engine and replay review. This is not a generated-function compiler or a new worker.
- `python_service/digital_twin/modules/reasoning/infrastructure/inference_publication/`: candidate result writes, aggregate/source validation, atomic active-marker switching and generation retention through an explicit `PublicationStore` port. Inject clock/settings/timeouts from the adapter, never import the graph repository or a business workflow. Preserve staged-write versus active-publication semantics, source/world ownership and the last usable result on failure. Recorded transaction contracts supplement, but do not replace, native TypeDB and immutable replay validation.
- `python_service/digital_twin/modules/reasoning/infrastructure/abox_persistence/`: scoped physical fact writes and separate Manifest activation/finalization through `ABoxRowStore` and `ABoxControlStore`. Keep physical batches incremental, but never split a control-pointer/journal swap across commits. Reject an oversized control patch before its first write with explicit limit diagnostics. Candidate planning, projection leases, readback verification and recovery remain authoritative; an activation readback error must retain its journal rather than falsely claim rollback.
- `python_service/digital_twin/modules/reasoning/infrastructure/typedb_runtime/`: connection, transaction, readiness and schema lifecycle through explicit capability ports and injected runtime callbacks. Keep driver/cache/lock identities at the composition facade until their ownership is separately migrated. Preserve retry deadlines, partial schema resumption and optional-driver behavior; schema readiness does not grant investment action authority.
- `python_service/digital_twin/modules/reasoning/infrastructure/abox_candidates/`: pure scope/physical-image plans, semantic-versus-rebind selection, exact row closure and identity readback, plus bounded pending-activation recovery. Mapping and validation cannot publish; recovery uses only journal/active-pointer/native-marker reads and existing control operations under the facade's projection coordinator. Preserve deferred published facts, generation/world ownership and the journal on unproven account results. Never load a full historical graph during recovery or retry a semantic candidate mismatch inline. Save/Manifest orchestration and native execution remain separate; structural moves must preserve golden contracts and do not create new asynchronous boundaries.
- `python_service/digital_twin/infrastructure/service_factory.py`: explicit lazy export catalog for runtime builders
- `python_service/digital_twin/infrastructure/composition/`: owner/lifecycle-specific runtime composition of use cases and adapters, imported at builder invocation

Versioned reasoning engines must own separate durable queue, graph-database,
release, and delivery-authorization boundaries. Promotion must switch the
active deployment and read-side graph binding together. A switched-out engine
must fail closed before consuming another request even if its old process is
still shutting down.

Fresh TypeDB release databases must bootstrap the base schema in bounded
16-definition batches with a 60-second transaction deadline. A database created
by the current process skips schema
inspection; an existing candidate database must inspect its partial schema and
resume only missing definitions. If that inspection fails, provisioning fails
closed instead of retrying from an empty schema. Runtime settings and service
manager fallbacks must use the same batch-size default as the TypeDB adapter.
Once the serving TypeDB process completes startup seeding, persist readiness
against its PID, process start time, command, storage path, and address. A
supervisor reload must trust that exact process generation instead of demoting
it when a workload probe briefly times out during schema compilation. Starting
or stopping a different TypeDB generation invalidates the persisted readiness.

Compatibility modules:

- `config.py`, `analytics.py`, `models.py`, `monitor.py`, `providers.py`, `notifiers.py`, and `scheduler.py` should remain thin re-export/factory modules only.
- Do not add new business logic to compatibility modules.

## Event-Driven Rules

The generic event envelope lives in `shared_kernel/events.py`. Event names,
typed payload shaping and factories belong to their producing module's domain
and are exposed through that module's explicit `contracts.py`. Common event
serialization belongs to the platform, not to the generic envelope.

Current events:

- `account.saved`
- `account.removed`
- `account.watchlist_changed` (owned by `modules/instruments/contracts.py`)
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

Events are persisted locally to the append-only `domain_events` table through
the configured operational event-log adapter. The default synchronous bus
records before dispatch: event persistence failure must propagate before any
consumer runs. Handler failures remain isolated by default. A transaction that
has already recorded its event uses `dispatch_recorded` after commit.

Rebuild projections by replaying recorded events where practical. Independent
follow-up work uses events and durable queues; an immediate result may use an
injected synchronous public interface. Do not force reads or small edits into
eventual consistency. An in-memory subscriber is not a durable consumer; its
recovery cursor, deduplication, retry and account/source identity must be
specified separately when reliable background execution is required.

`monitoring.alerts_detected` now carries investment notifications only as graph-backed `investmentInsight` events. Legacy investment alert types such as `monitorDecisionChange`, `modelBuy`, and `externalCryptoMove` are not valid realtime investment dispatch inputs. The model-review queue may read legacy-shaped historical jobs for compatibility, but new realtime investment judgement must originate from graph inference. Realtime monitoring and notification delivery workers must never wait for LLM/Codex output. AI-gated investment notifications transition to `awaiting_ai`, run through the dedicated leased AI inference queue, and return to the delivery outbox only after the latest result passes the ontology/action-envelope validator. Notification producers should enqueue jobs in the notification outbox and leave external delivery to the notification worker. Jobs derived from a domain event should carry `source_event_id` and a stable `dedupe_key`.

Ontology projection is a read-model boundary, not the source of truth. Aggregates and use cases own transactional state inside their bounded contexts; projection code can translate snapshots and domain events into TBox/ABox graph assertions for the active graph store, AI prompts, quality samples, and console views. Do not make domain aggregates depend on TypeDB, graph storage, or prompt rendering. If ontology needs more facts, publish or persist those facts in the owning context first, then extend the projection/read model.

## Parallel Development Slices

Use the twelve ownership packages and sync/async table in
[Business Module Architecture](module-architecture.md) when multiple sessions
work independently. Shared ontology contracts and runtime composition still
need coordination; moving an application service does not isolate every shared
table or adapter it uses.

Put owner-specific event definitions and ports inside the module and expose
the required contract explicitly. The old root repository export facade has
been removed; imports must name the actual owner's contract. If one
use case must update several stores atomically, use an explicit transaction
recorder in `infrastructure/`, delegating writes to owner-specific helpers.
Do not replace an entire account just to change a notification preference or
watchlist. Do not add cross-module private imports or circular public imports.

Business modules must not import the root service factory, composition package
or cross-owner account transaction coordinator. Inject the required capability:
read-only account data for collection/query workers, the watchlist-only store
for instrument edits, and the command coordinator for explicit account writes.
Runtime builders may wire private adapters, but must remain explicitly exported
and must not load unrelated business workflows merely by being imported.

Account configuration belongs to `modules/accounts/contracts.py`; delivery
time/message policies belong to `modules/notifications/contracts.py`, and
investment strategy profiles belong to `modules/portfolio/contracts.py`.
`modules/accounts/domain/accounts.py` is a compatibility export only. Credential-aware workers
may request `AccountReader`, but instrument/watchlist operations must use the
secret-free `WatchlistAccountReader` projection. Keep account commands and
their domain event in the existing shared transaction.

For reasoning persistence, keep Manifest/save planning, projection leases,
bounded graph reads, maintenance and native execution in their private
`modules/reasoning/infrastructure` packages. Pass explicit ports and callbacks;
do not import the repository facade or composition root back into these
packages. Preserve per-repository driver/lock/cache identities and coordinator
decorators. A cleanup or native-retry change requires failure-path and active
generation preservation tests, not just query snapshots.

Projection orchestration is phase-bound inside `projection_write`: prepare,
assemble, select, repair, validate, plan, reuse, audit, journal, publish and
follow-up. Keep explicit inputs, minimal ports and frozen result envelopes;
do not introduce a mutable global context or another asynchronous boundary.
Audit identity must survive exceptions after audit creation. Candidate
validation and durable source audit precede active generation publication.

Shared transaction coordinators delegate owner writes to each module's
`infrastructure/transaction_writes.py`. These functions receive a
`BoundWriteConnection`, cannot open connections or commit, and preserve exact
SQL/order/rollback behavior. Reasoning job mutations require the current worker
and claim timestamp, including heartbeats and release binding. Never turn a
completed job back into pending because a later batch health update failed.
Completed-job receipt repair reads the locked persisted result through its own
method; it must not rewrite completion state or repeat inference. See
`docs/backend-stabilization.md` for the isolated failure rehearsals.

Keep source input assembly in `modules/reasoning/application/projection_input`.
Inject source-reader, outcome-observer, scorer and cache capabilities separately;
never pass the TypeDB writer or a notification publisher into those stages.
The sequence is capture, cache lookup, factual graph, model evidence, lineage
verification, and cache completion. World/scoped identity is applied afterward.
Keep pure factual shaping and source-key computation in the reasoning domain,
and cache implementations in reasoning infrastructure. A cache-key change
requires replay/freshness review; source equality must include observation
clocks and frozen release identity. Optional enrichment is not permission to
replace an immutable source snapshot with current data. Preserve existing
effectful outcome observation explicitly instead of disguising it as a read.
Native crash rehearsals must create and stop only their own temporary server;
never stop the managed TypeDB process to test a failure path.

Keep TypeDB static schema, seed identity, bounded preflight, append-only writes
and immutable release restoration in `reasoning/infrastructure/static_seed`.
Connection/schema lifecycle remains in `typedb_runtime`; do not create a second
bootstrap owner. Reads and graph-shaping ports must not acquire graph-write or
delivery capabilities. Restore artifacts without consulting the current source
catalog. Publish the keyed static manifest only after static rows are saved,
and delete/insert that single pointer in one transaction. A failed replacement
must preserve the prior committed pointer; a lost commit acknowledgement must
remain retryable without duplicating it. This is not an atomic transaction for
the entire static graph or a change to release promotion policy.

V2 composition separates launch settings, immutable release binding, warmup,
release health and decision/delivery wiring. Keep this preparation synchronous
and preserve its phase order. Do not change frozen release identities or
promote shadow delivery while reorganizing its code. Do not reset existing
quiet-hours values or change account serialization during a DTO ownership move.

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
