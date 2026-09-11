# Business Module Architecture

## Scope

Orbit Alpha is a modular monolith with selectively asynchronous jobs. Twelve
business packages do **not** mean twelve processes, twelve databases, or twelve
queues. Immediate reads and transactional edits remain synchronous. Collection,
TypeDB inference, AI execution, delivery, and outcome observation retain their
existing background job boundaries.

This migration physically moves the application services and their selected
owned domain/storage implementations into `python_service/digital_twin/modules`.
It does not rewrite investment rules, promote models, change delivery policy,
create new worker processes, migrate private account data, or add a message
broker. Existing API routes and UI navigation remain intact.

## Module Responsibilities

All paths below are relative to `python_service/digital_twin/modules/`.

| Module | Responsibility | Immediate operations | Background work |
| --- | --- | --- | --- |
| `accounts` | Account identity and brokerage settings | Validate, list, create, patch, remove | Publish recorded account changes |
| `instruments` | Instrument catalog and account watchlists | Search, suggest, atomic watchlist edits | Catalog refresh and affected-symbol refresh requests |
| `portfolio` | Holdings, ledger, mandate, exposure and execution lifecycle | Record and query user/account state | Reconciliation and portfolio observation |
| `market_data` | Quotes, candles, external facts, provider collection and temporal features | Cached reads and source configuration | Scheduled collection and existing time-series replication |
| `news_intelligence` | News, disclosure, verified evidence and bounded research | Evidence retrieval and verification results | Collection, claim verification and evidence-gap research |
| `investment_calendar` | Events, candidates and discovery | Register, edit, reject and query events | Official-calendar sync, discovery and extraction |
| `model_registry` | Model/rule releases, hypothesis proposals and governance | Catalog, version and approval queries | Model review, development and validation jobs |
| `reasoning` | Versioned fact assembly, model evidence and TypeDB inference | Exact stored trace/status queries | Existing leased inference jobs |
| `decisions` | Immutable subject cases, AI comparison, publication and continuity | Case/status queries and explicit commands | Existing AI inference and reconciliation jobs |
| `outcomes` | Follow-up observations, replay and calibration | Outcome and review queries | Scheduled observation, replay and hypothesis review |
| `notifications` | Admission, rendering, quiet hours, delivery and inbox state | Read/important changes and policy checks | Existing durable delivery queue and retries |
| `read_models` | Console, flow, timeline, case and valuation read projections | Bounded web queries | Projection refresh through existing runtime orchestration |

The table describes responsibility, not a new queue for every cell. An event
publication alone is not asynchronous execution.

## Package Contract

```text
digital_twin/
  modules/
    <business_module>/
      public.py             # Explicit, lazy use-case exports
      contracts.py          # Explicit owned data/event exports
      application/          # Use cases and narrow ports
      domain/               # Owned pure concepts, when already separated
      infrastructure/       # Owned adapters, when already separated
  domain/                   # Remaining shared contracts and ontology kernel
  application/              # Runtime coordination only
  infrastructure/
    composition/            # Responsibility-specific runtime builders
    service_factory.py      # Explicit, lazy builder exports
    account_transactions.py # Explicit cross-owner account transaction
                            # Remaining shared adapters also live here
```

- A business module imports another module only through `public` or `contracts`.
  Importing the other module's repository or application implementation is not
  an acceptable shortcut.
- Public exports are an explicit whitelist. Loading account/watchlist CRUD must
  not construct the service factory, TypeDB adapter, AI worker, or notification
  workflow.
- Public application dependencies must remain acyclic. Notification text
  rendering belongs to `notifications`; AI response validation belongs to
  `decisions`. The delivery module must not import AI execution to render a
  completed message.
- Domain implementations stay free of application/infrastructure imports.
- The root application package is limited to scheduling, checkpoints, pipeline
  health and storage maintenance. New business features belong to their owner.
- Shared runtime composition can wire implementations. That exception is not
  permission for business modules to bypass each other's contracts.

## Runtime Composition

`infrastructure/service_factory.py` is now an explicit lazy export catalog, not
a container that imports every business implementation. Builders live in
`infrastructure/composition/` by owner: accounts, instruments, portfolio,
market data, news, calendar, model registry, reasoning, decisions, outcomes,
notifications and read models. Reasoning release, projection, monitoring,
health and shadow wiring have separate files because their runtime lifecycles
differ. Events, operations and small runtime settings helpers are shared
composition concerns, not additional business modules or workers.

Each builder imports its implementations when invoked. The operational-store
factory and MySQL export catalog use the same lazy boundary. Resolving a
builder must not load the entire application graph or a database driver. A
builder can still construct its actual required collaborators; laziness does
not make a slow use case asynchronous.

Web handlers and CLI commands use these builders. Business modules cannot
import `service_factory`, `composition` or `account_transactions`; dependencies
are injected through ports. There is no implicit fallback export or runtime
service locator. Add each new builder to the explicit export catalog and its
isolation tests.

## TypeQL Query Boundary

`modules/reasoning/infrastructure/typeql/` owns direct TypeQL query generation
and execution planning. These are internal reasoning adapters, not additional
business modules or workers. The graph repository imports them explicitly;
they cannot import the repository, runtime composition, settings or application
services in return.

| Files | Responsibility |
| --- | --- |
| `constants`, `storage_schema` | Native engine identity, promoted attributes and physical schema capabilities |
| `literals`, `rule_shape`, `scope_clauses` | Value encoding, authored rule metadata, world and active-generation constraints |
| `condition_queries`, `match_queries`, `any_queries` | Individual predicates, complete queries and independent evidence groups |
| `indexed_queries`, `model_signal_queries` | Verified evidence-index bindings and governed model-signal batch queries |
| `preflight`, `planning`, `profiles` | Impossible-candidate rejection, bounded work plans and query capability reports |

The leaf dependency graph is acyclic. Shared domain contracts and the pure
`graph_store_payloads` conversions are allowed dependencies. Resolving every
compiler export must not load a TypeDB/MySQL driver or an application workflow.
`__init__.py` lists exports explicitly; it is not a fallback service locator.
Existing graph-adapter imports resolve to the same owned implementations.

These functions return query strings, plans and diagnostics, not an investment
verdict. Preflight rejection does not prove a matched rule. An unavailable
index remains explicit and may select the existing scoped **TypeDB** query;
it must never fall back to Python investment evaluation. Actual transactions
belong to the storage adapters described below; leases and retry policy remain
in the graph repository and existing runtime.

The extraction moved 85 definitions without changing their AST or the
remaining adapter's runtime AST. A golden contract captured from revision
`45f284c52` checks 1,023 deterministic query/plan/catalog cases, including
field/target-kind indexes, independent evidence groups and world ownership.
Inputs are synthetic and contain no account credentials or production records.
The native engine remains `typedb-direct-typeql-rule-engine-v6` because this is
a byte-equivalent relocation. Future semantic changes require the normal
versioned engine/replay review; do not simply regenerate the golden file to
silence a failure.

## Inference Publication Boundary

`modules/reasoning/infrastructure/inference_publication/` owns the storage
lifecycle of a TypeDB inference result, not the final AI investment opinion:

| File | Responsibility |
| --- | --- |
| `writer.py` | Persist candidate nodes, relations and marker in the existing bounded write batches |
| `validation.py` | Read counts/marker in one transaction and verify source-ABox alignment and completeness |
| `lifecycle.py` | Atomically switch the active marker and separately prune old world-scoped generations |
| `markers.py`, `values.py` | Marker payloads, generation deletion clauses and exact stored-value decoding |
| `ports.py` | Required graph I/O capabilities and injected clock, settings, timeout and error classification |

The shared graph repository retains five thin entry-point delegates and the
runtime wiring. Publication code depends on `PublicationStore`, not the whole
repository implementation. It cannot invoke rule evaluation, graph projection,
rule editing, account access or notification delivery. Driver acquisition,
retries, serialization and transaction options are supplied by the existing
adapter. A deterministic in-memory transaction recorder can exercise the
complete publication path without importing a database driver, settings,
application services or the graph repository.

Candidate writes are **not** one all-or-nothing transaction. Earlier batches
may remain staged after a failure, but they must not replace the active result.
The candidate marker/count/source checks precede activation; old marker removal
and new marker insertion remain in one write transaction. A successful empty
native evaluation can be published as `no-match`; an incomplete evaluation
cannot be treated as that result. Cleanup remains a separate, bounded,
world-scoped operation and a cleanup failure does not invalidate publication.

The extraction preserves the original five storage algorithms and four payload
helpers, with only receiver/runtime bindings and docstring indentation changed.
Twenty-two golden execution scenarios from revision `685821a19` compare query
and transaction order, markers, return values, failures, retries and retention.
The recorded driver models commit/rollback; it is not a replacement for native
TypeDB validation. Existing repository and replay tests remain required.

No worker, event/outbox contract, transaction retry policy, native engine
version or investment rule changed. Source validation and pointer activation
still rely on the existing projection coordinator/write lease; this extraction
does not introduce a new cross-store transaction or compare-and-swap protocol.

## Scoped ABox Persistence Boundary

`modules/reasoning/infrastructure/abox_persistence/` separates physical fact
writes from active-generation control. These are internal reasoning adapters,
not new business modules or asynchronous workers.

| File | Responsibility |
| --- | --- |
| `writer.py` | Reuse verified storage identities, write bounded node batches, verify relation endpoints and write relation batches |
| `controls.py` | Replace active Manifest/scope pointers and the pending journal together; clear only the journal after finalization |
| `lifecycle.py` | Admit a staged candidate, verify pointer/journal readback and finalize only after aligned inference proof |
| `ports.py` | Separate `ABoxRowStore` and `ABoxControlStore` capabilities plus injected clock, settings, timeout and error classification |
| `world_calls.py` | Preserve explicit world ownership and the existing empty-world callback contract |

The row writer cannot activate a generation through its port. It retains the
existing short-lived driver per bounded commit, storage reuse checks, endpoint
inventory, relation-plan fallback and telemetry. Physical batches are still
incremental, not a single transaction for the complete graph. Candidate plans,
copy-on-write/current-state selection and row-image verification now live in
`abox_candidates/`. Manifest construction, the save coordinator, database
readback queries and scoped writer leases remain in the shared adapter.
This extraction does not change those policies or add a new atomicity guarantee
for in-place physical fact updates.

The control port does not expose physical row writes, rule editing, native
execution or notification delivery. A fully staged candidate may move the
active pointer only through the existing admission path. Its recovery journal
stays durable until the exact source generation and target coverage are proven
by a completed native `matched` or `no-match` result. A failed readback after
a committed activation reports an error but retains the journal; it does not
claim that the previous pointer was restored. Retired-generation cleanup
remains deferred to the existing maintenance lane.

**Atomic control limit correction:** the former control writer split oversized
pointer updates across commits despite describing them as atomic. A failure in
a later batch could leave the active Manifest, scope pointers and pending
journal inconsistent. Control updates now either commit together or fail
before their first write. `typedbScopedControlWriteTransactionQueryCount`
remains the bounded query limit (default 256, clamped to 8-512), but it is no
longer a batch size for multi-commit activation. An oversized activation returns
`typedbAtomicControlPatchLimit`, the required/allowed query counts and
`preservedPreviousAbox=true`. Reduce the control patch or review that limit;
never retry it as separate commits. Larger patches can therefore remain blocked
instead of partially activating. No account data is migrated by this change.

Six repository entry points are thin delegates. Forty-three synthetic golden
execution scenarios from revision `966b3c9f0` preserve normal and failure-path
query order, return values, telemetry, retries and journal lifecycle. Separate
boundary tests cover exact limits, one-query overflow, rollback, other-world
isolation and retained scope pointers. The recorded transaction engine is not
a substitute for native TypeDB crash/ambiguous-commit testing. Existing replay
and repository regressions remain required. Native TypeQL match semantics,
rules and engine version are unchanged; the atomic control guard and its
diagnostics are the intentional storage behavior correction.

## TypeDB Connection and Schema Boundary

`modules/reasoning/infrastructure/typedb_runtime/` now owns the connection and
storage-schema lifecycle. Its nine implementation files are private adapters
inside the existing reasoning module, not nine additional business modules or
workers. Forty-six facade methods delegate or alias their implementation here.

| File | Responsibility |
| --- | --- |
| `connection.py` | Lazy driver import, shared/dedicated channels, bounded retries, invalidation and database creation |
| `transactions.py` | Read/write/schema deadlines and optional older-driver compatibility |
| `readiness.py` | Address/database/TLS/fingerprint cache keys, shared readiness and monotonic expiry |
| `inspection.py` | Schema catalogue access and persisted schema-contract comparison |
| `schema_plan.py` | Pure dependency-ordered bootstrap planning and missing-definition/ownership resumption |
| `migrations.py` | Existing additive storage-identity, scope, fingerprint, world, promoted and semantic schema changes |
| `bootstrap.py` | Bounded native/HTTP schema commits, deadlines and batch telemetry |
| `http.py` | TypeDB HTTP transport and bounded response errors |
| `lifecycle.py` | Schema readiness checks, fresh/partial bootstrap and explicit schema-contract synchronization |

`ports.py` declares a separate capability set for each I/O role. Clock,
sleep, timeout and error classification callbacks are injected; the process
cache is passed explicitly with its existing dictionary and lock. Importing
and executing a pure plan or an injected lifecycle does not load the shared
repository, application workflows or database drivers. No schema port can
publish an ABox generation or execute an investment rule.

The composition facade deliberately retains per-repository driver state,
locks, local readiness and telemetry, plus the shared cache identity. Moving
those objects between owners is a separate migration; constructing a runtime
callback bundle does not create a connection or reset cache state. Shared
channels retain the longest declared operation deadline, while dedicated
native-rule reads keep their bounded channel lifetime. Failed operations
invalidate the cached channel before the existing bounded retry policy runs.

Process readiness still uses the original 300-second TTL, and local instance
readiness is unchanged. A database created by this process bootstraps without
a catalogue read. Existing fresh candidates must be inspected successfully;
an inspection or commit failure cannot mark them ready. Completed schema
batches remain committed, and the next attempt resumes the missing definitions.
Fresh candidates retain 16-definition batches and a 60-second deadline; normal
bootstrap retains its 64-definition default. HTTP selection is still explicit
configuration, not a new automatic transport failover. The legacy normal-mode
inspection/migration fallback is also unchanged: this extraction does not
claim universal fail-closed inspection or a single atomic schema transaction.

Thirty-nine synthetic golden scenarios captured from `ad395f3a7455` preserve
bootstrap plans, migration queries and readiness control flow. Eighteen tests
also cover concurrent connection creation, dedicated-channel ownership, retry
admission, TTL/database isolation, create races, optional drivers, HTTP errors,
partial-commit resumption and import/port boundaries. The transaction recorder
models commit/rollback only; it does not validate TypeQL or simulate native
server crashes. Existing native repository and replay suites remain required.
No investment rules, native engine version, event contract, asynchronous
boundary, deployment target or database schema definition changed.

## ABox Candidate and Recovery Boundary

`modules/reasoning/infrastructure/abox_candidates/` separates candidate planning
and row-image validation from physical writes, and interrupted-activation
recovery from the general graph repository. It is private to reasoning and
remains synchronous under the existing projection coordinator. This is not an
additional business module, worker, queue or general event framework.

| File | Responsibility |
| --- | --- |
| `scope_plan.py` | Normalize scope plans and choose physical generation identities |
| `selection.py` | Distinguish semantic changes, physical-only relation rebinds and reusable scope/index entries |
| `row_image.py` | Copy the graph into its physical generation and map selected scope rows |
| `rows.py` | Reconcile current and retained rows; validate candidate counts, generations and exact endpoint identities |
| `identity.py` | Pure world/box/generation storage identities and material-content fingerprints |
| `validation.py` | Dedupe rows, validate identity readback/reuse and report missing endpoints |
| `recovery.py` | Inspect the pending journal, active Manifest and bounded native-result marker; restore control or finalize through existing operations |
| `retry.py` | Delegate transport retries without replaying semantic candidate failures inline |
| `ports.py` | Separate mapping, identity readback, guarded recovery and retry capabilities |

Twenty-one facade methods delegate or alias these implementations; three
shared storage helpers are re-exported for existing callers. The facade keeps
the `pending-abox-recovery` coordinator decorator. A denied write lease must
return before reading the journal, and an exception must release the guard.
The recovery implementation is not a public unguarded entry point. Its port
exposes neither physical row deletion nor native rule execution, and cannot
request a complete historical graph or InferenceBox. Mapping/validation ports
cannot activate a generation. These are logical capability contracts, not a
runtime security sandbox.

Planning preserves the distinction between a new fact and an existing relation
whose endpoint now has another storage identity. Only semantic selections seed
relation expansion; integrity-only companions do not expand the patch into
unrelated assertions. Deferred scopes retain their published image, and
physical-only rebinds retain active semantic assertions. The existing bounded
current-image fallback is allowed only when obsolete endpoints can be replaced
by a complete, count-aligned current relation scope. Invalid endpoint,
generation or row-count results return diagnostics without writable rows.

Recovery preserves the existing world-specific policies. Aligned, completed
native `matched` or `no-match` proof with requested target coverage can finalize
a pending candidate. An unproven account candidate retains its journal and
requires a bounded native retry. Shared-premise recovery and oversized-batch
recovery may restore the verified predecessor through the existing control
adapter; they do not delete candidate facts here. Unreadable control state
cannot authorize a new judgement. The initial active generation with no target
symbols retains its special control-only journal-clear path; this does not
create a native result or investment opinion. Recovery does not add an atomic
commit across physical facts and native results.

Seventy-five synthetic golden scenarios captured from `f6ef96d9dec2` cover
selection, physical images, row identities and recovery state/callback order.
Eighteen tests additionally verify endpoint closure, input immutability,
generation/world isolation, narrow imports/ports, transport-only retries,
idempotence, failed control writes and coordinator refusal/release. They model
interruption states with injected stores, not a real TypeDB server crash.
Native query/rule semantics, engine version and persisted formats are unchanged.
The larger reconciliation and recovery algorithms remain intact inside their
new owners; splitting those algorithms is separate from this ownership move.

## Synchronous and Asynchronous Boundaries

Use a synchronous public interface when the caller needs an immediate result
and one transaction owns the operation: account edits, watchlist changes,
calendar registration, inbox state and bounded cached queries. Inject the
required port; do not create a worker solely because another module is involved.

Use a recorded event and a durable job for slow or independently retryable
follow-up work. The existing conceptual flow remains:

```text
source state committed
  -> source change recorded
  -> leased reasoning request with immutable source identity
  -> TypeDB result and immutable subject candidate set
  -> leased AI comparison
  -> validated decision publication
  -> notification admission and leased delivery
  -> later outcome observation and review
```

The synchronous `EventBus` records through its configured recorder **before**
calling subscribers. Persistence failure propagates; consumers must not act on
an unrecorded event. Subscriber failure is still isolated by default.

When an account/watchlist mutation has already stored its event in the same
MySQL transaction, the caller uses `dispatch_recorded`. It must not append that
event a second time. Only use this method after a successful commit.

This bus is not a durable per-consumer inbox. A crash between commit and local
dispatch still requires the existing event-log/job recovery path. New reliable
async consumers must explicitly define deduplication identity, account/subject
scope, source revision, leases, retry limits and recovery cursor. Do not rely on
an in-memory callback as guaranteed delivery.

## Scoped Account Writes

The former account-wide update path made it easy for a stale settings form to
overwrite watchlist or channel changes. Existing-account payload saves now:

1. Normalize only explicitly supplied keys, including supported snake-case keys.
2. Preserve omitted fields and secrets and skip an unchanged mutation.
3. Route identity, notification preferences and watchlist writes to their owner
   helpers using one explicit shared transaction recorder.
4. Persist state and the secret-free event atomically, then dispatch it.

The web form captures its original normalized values and sends only changed
fields. A quiet-hours edit does not resend a stale watchlist or credentials.
Account creation still submits a complete account. Full typed-account `save`
remains an explicit full replacement for creation/import callers; ordinary
settings changes must use the patch use case.

Watchlist add/remove locks the persisted account row and applies the mutation
to the current list, preserving concurrent additions. Unchanged items retain
their original timestamps. Duplicate additions are no-ops, an empty list stays
empty, and mutation plus `account.watchlist_changed` commit together. Unsaved
fallback accounts must first be explicitly saved through account settings.

The patch contract prevents unrelated-field overwrites. It does not provide
compare-and-swap protection for two users intentionally editing the same field.

The account dependencies now expose four distinct capabilities:

| Capability | Implementation | Used by |
| --- | --- | --- |
| Account reads | `modules/accounts/infrastructure/mysql_account_reader.py` | Collection, reasoning, portfolio observation, notification and web read paths |
| Watchlist account identity | `modules/accounts/infrastructure/mysql_watchlist_account_reader.py` | Instrument lists; selects identity, symbols and timestamps without joining credential tables |
| Watchlist edits | `modules/instruments/infrastructure/mysql_account_watchlist.py` | The watchlist use case; account reads are injected |
| Account commands | `infrastructure/account_transactions.py` | Explicit account create, patch, remove and full import/save operations |

The account reader has no account mutation or watchlist mutation methods. The
watchlist repository cannot replace credentials or notification preferences.
The command coordinator delegates identity, notification preferences, watchlist
and mandate writes to owner-specific helpers using the same MySQL connection.
Creation and deletion also keep the state change and event in one transaction;
an event-write or owner-write failure rolls everything back. Deletion preserves
historical investment records as before.

This is a logical capability boundary, not a security sandbox. The database
schema/credentials are still shared. `AccountConfig` remains a credential-aware
runtime configuration owned by `modules/accounts/domain/configuration.py`; it
must not be serialized into events. Its account read/atomic command ports live
in the same module. `domain/accounts.py` and the old account repository import
are compatibility exports, not duplicate implementations.

Quiet hours and message-level policies belong to `notifications` contracts;
investment strategy profiles belong to `portfolio` contracts. Values, defaults
and saved field names have not changed. Watchlist operations receive the much
smaller `WatchlistAccount` contract and return its identity-only account payload.
The full account settings endpoint remains authoritative for credentials and
preferences. Provider/notification workers still explicitly use the
credential-aware reader; this is not a complete secret-vault migration.

## Backend Execution Ownership

The reasoning infrastructure now has these additional private boundaries:

| Package | Responsibility |
| --- | --- |
| `manifest/` | Control graphs, exact evidence indexes, count plans, repair and staged save sequencing |
| `projection_lock/` | World leases, writer coordination, owner recovery and per-repository thread-local lease state |
| `graph_reads/` | Bounded reads, row decoding, metadata, inventories, inference snapshots and query metrics |
| `graph_maintenance/` | Retained-generation cleanup, orphan candidates and bounded deferred maintenance |
| `native_execution/` | Rule entry execution, target fanout, deadline recovery, evidence reads, validation and run sequencing |

These packages use explicit operation-specific Protocols and runtime callbacks.
They do not import the root TypeDB repository, runtime settings or business
application services. The facade preserves call signatures, coordinator guards,
driver ownership and activation order. Query-metric and lease state are owned
by their packages, with facade properties preserving lock/object identity.
Protocols document capabilities; they are not a runtime security sandbox.

The extraction preserves 153 method bodies and ten Manifest index helpers
against frozen pre-migration AST contracts, plus execution fingerprints for 22
save/lease scenarios. One intentional correction is recorded separately:
orphan cleanup previously referenced an undefined, unused `timing` variable
before opening its cleanup work. Removing that assignment permits the existing
bounded cleanup policy to run. Tests cover its generation limit and driver
release on failure; active/retained references are still excluded by inventory.

Native retry remains bounded by the original deadline. Invalid queries are not
made retryable, incomplete target-shard results are not treated as complete,
and partially persisted candidates do not replace the active generation.
No new background worker, broker, database format or investment rule was added.

## V2 Composition Phases

`composition/reasoning.py` now wires five explicit phases:

1. `reasoning_launch`: resolve the deployment and copy its runtime settings.
2. `reasoning_binding`: validate immutable release identity and seed artifacts.
3. `reasoning_warmup`: compile and warm the frozen rule catalog.
4. `reasoning_release_health`: record readiness without changing a frozen release.
5. `reasoning_delivery`: connect subject cases, AI handoff and delivery admission.

Typed result bundles connect the phases. Importing a phase does not initialize
the runtime. Platform initialization, store construction and phase execution
remain ordered synchronously; subsequent reasoning/AI/delivery jobs retain
their existing durable event and lease contracts. Frozen phase-body tests
protect the original release guards and wiring decisions.

## Remaining Shared Boundaries

This is application-layer modularization with selected ownership fixes, not a
claim that the entire persistence/domain migration is complete:

- Account configuration has an owner, but still composes multiple policy
  contracts for provider/notification workers. Other large ontology, decision
  and portfolio repository contracts remain in the shared domain package.
- Runtime builders are lazy and V2 has tested phases, but some phases still
  assemble large collaborator graphs. Import checks alone cannot prove all
  runtime interactions safe.
- `typedb_ontology.py` is now roughly 9,200 lines, down from 22,738 at the start
  of this batch. Schema/seed administration, primitive writes, legacy helpers,
  facade methods and some driver/cache identities remain there.
  `ontology_projection.py` remains large. The extracted save and native-cycle
  algorithms are deliberately intact; ownership separation does not mean
  every algorithm is already small or that overall code volume decreased.
- The MySQL schema and operational store facade remain shared. Owner helpers
  restrict the changed write paths, but do not enforce table ownership for
  every legacy writer.
- `public/app.js` and the Python web router were not split into frontend/BFF
  modules. Account payload handling and dependency wiring changed; navigation
  and rendering did not.
- There is no new generic per-consumer acknowledgement/outbox framework.
  Existing job-specific recovery remains authoritative.

Further changes should move remaining store ports and table writes one owner
at a time, and simplify the larger save/native algorithms only with immutable
replay and failure-path tests. Schema/seed administration and projection input
assembly are separate remaining ownership areas, not part of this batch.
Convert a synchronous follow-up to a durable consumer only when measured
latency, retries or failure isolation justify it. Do not migrate all modules to
asynchronous APIs by default.

## Verification

- `test_module_boundaries.py`: twelve real implementations, explicit exports,
  private import bans, acyclic public dependencies, pure domains, lazy
  synchronous account APIs and record-before-dispatch behavior.
- `test_module_account_mutations.py`: real isolated MySQL transactions,
  credential/watchlist preservation, concurrent additions, idempotence, empty
  lists, account isolation, narrow runtime wiring and create/delete/event-write
  rollback across owner helpers.
- `test_runtime_composition.py`: explicit builder coverage, lightweight import
  isolation, bounded valuation construction, V2 frozen phase bodies, launch
  isolation, immutable catalog warmup and read-only account capabilities.
- `test_account_contracts.py`: one policy owner, serialization stability,
  credential-free watchlist query projection and empty/default list behavior.
- `test_typeql_compiler.py`: original query/plan byte fingerprints, driver-free
  import isolation, explicit ownership, acyclic leaf dependencies and scoped
  unexecutable/fallback plans. Existing TypeDB/replay regressions still cover
  repository execution and failed-generation behavior.
- `test_inference_publication.py`: original execution fingerprints, injected
  graph-I/O isolation, atomic marker switching, failure preservation, complete
  versus incomplete empty results, relation fallback and world-scoped cleanup.
- `test_abox_persistence.py`: original row/control execution fingerprints,
  independent injected execution, separate row/control ports, endpoint checks,
  atomic control limits, world/scope isolation and retained recovery journals.
- `test_typedb_runtime.py`: original schema-plan/migration/control-flow
  fingerprints, shared/dedicated driver ownership, retries and deadlines,
  readiness-cache scope, partial schema resumption, HTTP failures and narrow
  driver-free port execution.
- `test_abox_candidates.py`: original candidate/recovery fingerprints, exact
  row-image closure, deferred facts and relation rebinding, identity readback,
  bounded world-scoped recovery, idempotence and retained coordinator guards.
- `test_backend_ownership.py`: original save/native execution contracts, cold
  imports, narrow ports, lease/metric state isolation, orphan cleanup bounds,
  deadline exhaustion and rejection of incomplete native timeout recovery.
- The web smoke test checks changed-field payloads and existing pages.
- `npm test` is the fast required gate; `npm run python:test:full` checks the
  complete curated regression suite. Tests use the isolated test database, not
  the owner's production account data.
- Unit failure injection is not a native-server crash test. Deliberately killing
  TypeDB mid-commit and exhaustive mobile/network outage testing were not part
  of this backend ownership batch.
