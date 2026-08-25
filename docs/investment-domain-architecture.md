# Investment Domain Architecture

## Purpose

Orbit Alpha separates transactional truth, semantic reasoning, execution, and delivery so each can scale and fail independently. MySQL-backed domain stores own durable business facts. TypeDB owns semantic relations and investment inference. AI compares graph-backed hypotheses but does not create positions, policy, fills, or source facts. Notification delivery never changes investment meaning.

## Bounded Contexts

| Context | Owns | Source of truth | Main outputs |
| --- | --- | --- | --- |
| Account Identity | Investor, brokerage account, provider credential reference, account universe | MySQL account configuration | `BrokerageAccount`, masked domain profile |
| Portfolio Ledger | Immutable trades, complete-balance checkpoints, inferred account activity, lots and cost basis | MySQL append-only ledger and snapshot CAS | Reconstructed positions, activity episodes and derived state |
| Investment Mandate | Risk tolerance, loss budget, cash floor, position/sector/currency limits, allowed actions | Versioned MySQL mandate | Policy version and TBox policy facts |
| Asset Knowledge | Company, security, listing line, market, sector, currency | Existing symbol/company stores | Stable identity graph |
| Market Observation | Quote, volume, flow, technical, macro, provenance and freshness | Existing time-series and source stores | Observation ABox facts |
| Statistical Signal | Point-in-time features, six versioned model families, exact hypothesis contracts, calibration and validation evidence | Immutable feature/signal snapshots and latest heads | `ModelSignalObservation` and exact `ModelHypothesisEvidence` ABox facts |
| Research Evidence | News, disclosure, claim, verification and counter-evidence | Existing research stores | Verified evidence ABox facts |
| Risk Exposure | Raw position, sector, currency and factor exposure | Derived immutable snapshot | Policy deltas for TypeDB rules |
| Allocation Rebalance | Target bands, drift and review-only rebalance proposals | MySQL proposal store | Bounded rebalance legs |
| Decision Intelligence | Question, hypotheses, inference generation and final decision | Decision episode store | `DecisionEpisode`, `ActionPlan` proposal |
| Trade Execution | Action envelope, order intent, broker fill and reconciliation | MySQL execution stores | Immutable execution episode |
| Outcome Learning | Observed outcome, attribution and decision review | MySQL review store | Governed learning evidence |
| Notification Delivery | Delivery intent, gate result and receipt | Notification outbox/ledger | Channel delivery only |
| Operations Audit | Pipeline health, leases, generations and failures | Operational stores and logs | Diagnostics and recovery signals |

Credentials are referenced by ID. Raw secrets are infrastructure configuration and must never enter domain events, ABox facts, prompts, logs, or git.

## Storage Roles

### MySQL

MySQL owns durable transactional facts:

- Account configuration and active investment mandate
- Append-only portfolio ledger entries
- Per-account complete-snapshot checkpoints, activity episodes, and derived portfolio state
- Previous-decision-to-observed-account-action correspondence records without a causality claim
- Rebalance proposals and action plans
- Decision episodes, execution episodes, fills, and decision reviews
- Event outbox, notification ledger, worker leases, and operational history

Writes use stable IDs and unique source references. Broker fills use provider execution IDs for idempotency. A position is reconstructed from ledger entries. A provider-declared complete live balance may append explicitly labelled inferred increase, decrease, exit, corporate-action, or cash-adjustment facts when it advances the account checkpoint. The checkpoint, inferred ledger rows, activity episode, derived state, source event, reasoning request, and factual notification outbox are committed with one version-checked MySQL transaction. Unknown orders, fees, taxes, and realised profit are never invented. Stale, same-time conflicting, account-fingerprint-changing, and unexplained all-empty snapshots are quarantined instead of mutating lots.

### TypeDB

TypeDB owns the semantic read model:

- TBox classes, relation types, bounded contexts, and governed RuleBox profiles
- Current compact ABox observations and exact model-contract evidence with provenance and freshness
- Generation-scoped InferenceBox relations and traces
- Links between mandate, exposure, decision, execution, and outcome concepts

TypeDB is not the account, ledger, order, or delivery source of truth. Projection can be replayed from MySQL and source stores.

## Canonical Flow

1. A source context persists a fact and publishes a compact domain event.
2. Projection builds only the affected ABox fact families and records provenance, observation time, and policy version.
3. The statistical-model stage reads one immutable temporal and factual market ABox, evaluates all affected predictive contracts in one indexed shared-world pass, and emits versioned exact-contract evidence plus sample, freshness, validation, and decision-eligibility state.
4. Question routing selects model contracts and TypeDB resolvers by input fact family, dependency key, world scope, freshness requirement, decision stage, and cost hint.
5. Direct TypeQL rules join exact model evidence with semantic, private account, policy, quality, and execution facts and materialize one immutable InferenceBox generation.
6. The investment brain builds competing hypotheses from exact contracts, active TypeDB traces, and explicit counter-evidence.
7. The decision-continuity assembler loads the immediately prior decision plus its bounded follow-up, observed outcome, account-activity, execution, and review facts.
8. AI receives the bounded graph packet and `DecisionContinuityPacket` and selects a hypothesis and categorical action inside the action envelope.
9. A `DecisionEpisode` and review-only `ActionPlan` are persisted atomically.
10. Explicit user approval or a future governed executor may submit orders. Broker fills remain immutable.
11. Later observations create attribution and a `DecisionReview`; learning changes remain review-only proposals.
12. Notification delivery applies channel policy after the investment decision is complete.

Statistical signals have no implicit action authority. All predictive rules use
the production model-contract path, but only exact `hypothesisContractId`
evidence can satisfy its corresponding TypeDB resolver. Broad family signals
remain diagnostic. Semantic, policy, quality, execution, and delivery contracts
remain TypeDB-owned. A missing or failed model release blocks its predictive
path instead of restoring the former raw-fact predicate.

`DecisionContinuityPacket` is a read-only memory contract, not another inference engine. It distinguishes `observed`, `not-observed`, `pending`, and `not-applicable`; an unchanged balance never means the user deliberately chose `HOLD`. A quantity change is linked to the prior decision for comparison but explicitly carries `causalityClaimed=false`. The packet is captured once when an AI request enters the durable queue and the worker reuses the same packet, so later database changes cannot mutate an in-flight judgement.

For live-account balance changes, a factual `portfolioActivityObservation` can be delivered immediately from the same durable outbox. It states only the observed before/after balance and uncertainty. The separate `investmentInsight` remains blocked until the portfolio activity and state are projected into TypeDB, relevant rules materialize an InferenceBox generation, and the AI judge reviews that graph result. Suspicious complete-balance responses are stored in `portfolio_snapshot_quarantines`; they remain visible for audit but cannot replace the accepted comparison checkpoint.

The trace key chain is:

```text
sourceEventId
  -> aboxSnapshotId
  -> inferenceGenerationId
  -> decisionEpisodeId
  -> actionPlanId
  -> executionEpisodeId
  -> providerExecutionId
  -> decisionReviewId
  -> notificationJobId / deliveryReceiptId
```

## Investment Case Read Model

`DecisionEpisode` remains the durable judgement source of truth. The user-facing
read model projects the latest episode for each account and instrument into one
stable `InvestmentCaseSnapshot`:

```text
confirmed facts -> signals and relations -> competing investment cases -> current opinion -> outcome
```

The case ID is stable across new episodes for the same account and instrument.
List reads use the indexed `investment_flow_heads` projection and never query
TypeDB, hydrate full outcome history, or replay inference. Detail reads expose
the complete supporting and counter evidence, assumptions, invalidation
conditions, and guardrails. History is loaded separately from prior persisted
episodes. Evidence assurance is an overlay on the judgement: it may block or
qualify an opinion, but it is not another serial inference stage. Notification
delivery is a separate downstream branch and never lowers judgement readiness
when no notification is required. The source/evidence/relation/hypothesis/
inference/decision lineage is an operator-only trace loaded on demand.

The HTTP contracts are:

- `GET /api/investment-cases`
- `GET /api/investment-cases/{caseId}`
- `GET /api/investment-cases/{caseId}/history`
- `GET /api/investment-cases/{caseId}/trace`

`/api/investment-flow` remains a compatibility and operational API. New user
interfaces use the investment-case contract so implementation stages do not
leak into the investor's primary decision workflow.

## Investment Model Read Model

The active deployment registry, versioned RuleBox, ontology catalog, and
hypothesis experiment ledger remain authoritative. `GET /api/investment-model`
projects those sources into one read-only model identity:

```text
facts -> relations -> hypotheses -> inference -> current opinion
```

The projection exposes the active deployment and release fingerprints, model
thesis, graph and time-series bindings, rule/relation/hypothesis inventory, and
promotion state. It never copies rules into a second mutable store and never
returns credentials. A persistent stale-while-revalidate cache keeps the last
successful release visible while slow optional dependencies refresh. Explicit
refresh is an operator action; AI proposals cannot promote themselves.

## Policy Reasoning

Investment limits are source-backed facts, not constants embedded in rules. Python may compute raw metrics and policy deltas:

```text
policyDeltaRatio = observedExposureRatio - policyLimitRatio
```

TypeDB decides whether a breach relation exists by evaluating `policyDeltaRatio > 0`. The same concentration rule therefore works for every account profile without rule duplication or a hardcoded 35% threshold.

Every executable rule carries a generated domain manifest:

- Owning module and supported question types
- Input fact families and dependency keys
- Required freshness and provenance
- Policy keys and world scope
- Decision stages and effects
- Conflict group and outcome contract
- Failure policy and estimated cost

A rule with incomplete routing metadata fails contract validation before deployment.

## Performance And Scalability

- Source writes and execution writes are small transactions. No external API, TypeDB query, or AI call runs while a MySQL transaction is open.
- Domain events and work items carry compact IDs; workers load details only when processing.
- ABox projection remains target- and fact-family-scoped. Static TBox payloads are not rebuilt per quote.
- Policy changes invalidate exposure and decision families, not unrelated market observations.
- Rule routing occurs before TypeDB execution. Rules outside the question, changed fact families, world scope, or freshness window are not queried.
- InferenceBox writes are generation-scoped and bulk materialized. Failed generations preserve the last usable generation.
- AI and notification delivery are asynchronous. Realtime collection does not wait for either.
- Decision continuity reads one indexed prior episode and only its linked observations. It never hydrates the full account lifecycle or re-runs TypeDB, and the AI worker does not repeat the read after queue capture.
- Ledger and fill IDs make retries idempotent. At-least-once delivery cannot duplicate holdings or fills.
- Snapshot comparison uses a per-portfolio checkpoint CAS. Unchanged balances advance only the checkpoint, while changed balances write one episode and one state snapshot; concurrent workers retry the complete observation.
- Outcome evaluation is horizon-scheduled and does not block current inference.
- Statistical scoring is bounded CPU work over the already selected temporal windows. Changed signal heads are bulk-upserted; identical material performs no snapshot write, and history retention never deletes a snapshot referenced by a latest head.

## Compatibility And Migration

`AccountConfig` remains a compatibility facade. `domain_profile()` separates brokerage identity, watchlist universe, and delivery policy; `investment_mandate()` creates the versioned policy contract. Existing position snapshots remain available while the append-only ledger is populated. No automatic order submission is enabled.

TBox v6 layers the canonical domain modules over existing class and relation names. Existing rule IDs remain stable. New relation names are additive, and the web trace can show both historical episodes and new lifecycle records.

## Definition Of Done

A domain change is complete only when:

1. The source aggregate and repository own the fact.
2. A domain event contains stable IDs and no secrets.
3. TBox classes and relations pass referential validation.
4. ABox projection includes policy version, provenance, freshness, and missing-data state.
5. TypeDB rule manifests identify dependencies and decision stages.
6. Decision, plan, execution, outcome, and delivery IDs remain traceable.
7. Retries are idempotent and transactions do not include network calls.
8. Contract, regression, and smoke tests pass.
