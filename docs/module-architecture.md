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
it must never fall back to Python investment evaluation. Actual transactions,
leases, retry policy, candidate activation and InferenceBox publication remain
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

The account dependencies now expose three distinct capabilities:

| Capability | Implementation | Used by |
| --- | --- | --- |
| Account reads | `modules/accounts/infrastructure/mysql_account_reader.py` | Collection, reasoning, portfolio observation, notification and web read paths |
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
schema/credentials are still shared, and `AccountConfig` still carries several
owners' read data. The legacy full registry is available for explicit command
callers; new read paths must request `account_reader` instead.

## Remaining Shared Boundaries

This is application-layer modularization with selected ownership fixes, not a
claim that the entire persistence/domain migration is complete:

- The broad `AccountConfig` DTO and several repository ports remain in the
  shared domain package. Other large ontology and decision contracts also
  remain there.
- Runtime builders are physically separated and loaded lazily, but some
  reasoning builders still assemble large collaborator graphs. Those graphs
  are not fully described by module import checks alone.
- `typedb_ontology.py` still has roughly 27,000 lines after the TypeQL extraction,
  and `ontology_projection.py` remains a large shared adapter. Driver execution,
  generation publication and scoped Manifest persistence are not yet separated
  by ownership. Their transaction and investment semantics are unchanged.
- The MySQL schema and operational store facade remain shared. Owner helpers
  restrict the changed write paths, but do not enforce table ownership for
  every legacy writer.
- `public/app.js` and the Python web router were not split into frontend/BFF
  modules. Account payload handling and dependency wiring changed; navigation
  and rendering did not.
- There is no new generic per-consumer acknowledgement/outbox framework.
  Existing job-specific recovery remains authoritative.

Next work should move remaining store ports and table writes one owner at a
time, then simplify large builder dependency graphs. Following the TypeQL query
extraction, separate TypeDB generation publication and storage adapters only
with immutable replay and failure-path tests. Convert a synchronous follow-up
to a durable consumer only when measured
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
  isolation, bounded valuation construction and read-only account capabilities.
- `test_typeql_compiler.py`: original query/plan byte fingerprints, driver-free
  import isolation, explicit ownership, acyclic leaf dependencies and scoped
  unexecutable/fallback plans. Existing TypeDB/replay regressions still cover
  repository execution and failed-generation behavior.
- The web smoke test checks changed-field payloads and existing pages.
- `npm test` is the fast required gate; `npm run python:test:full` checks the
  complete curated regression suite. Tests use the isolated test database, not
  the owner's production account data.
