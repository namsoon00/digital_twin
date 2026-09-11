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
  infrastructure/           # Composition and remaining shared adapters
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

## Remaining Shared Boundaries

This is application-layer modularization with selected ownership fixes, not a
claim that the entire persistence/domain migration is complete:

- The broad `AccountConfig` DTO and several repository ports remain in the
  shared domain package. Other large ontology and decision contracts also
  remain there.
- `service_factory.py` still assembles most runtime dependencies. Injected
  cross-module collaborators are not all discoverable from Python imports.
- `typedb_ontology.py` and `ontology_projection.py` remain large shared adapters.
  Their query, generation and transaction semantics were not changed here.
- The MySQL schema and operational store facade remain shared. Owner helpers
  restrict the changed write paths, but do not enforce table ownership for
  every legacy writer.
- `public/app.js` and the Python web router were not split into frontend/BFF
  modules. Only account payload construction changed in this pass.
- There is no new generic per-consumer acknowledgement/outbox framework.
  Existing job-specific recovery remains authoritative.

Next work should isolate composition by module, then move store ports and table
writes one owner at a time. Separate TypeDB query planning, generation
publication and storage adapters only with immutable replay and failure-path
tests. Convert a synchronous follow-up to a durable consumer only when measured
latency, retries or failure isolation justify it. Do not migrate all modules to
asynchronous APIs by default.

## Verification

- `test_module_boundaries.py`: twelve real implementations, explicit exports,
  private import bans, acyclic public dependencies, pure domains, lazy
  synchronous account APIs and record-before-dispatch behavior.
- `test_module_account_mutations.py`: real isolated MySQL transactions,
  credential/watchlist preservation, concurrent additions, idempotence, empty
  lists, account isolation and event-write rollback.
- The web smoke test checks changed-field payloads and existing pages.
- `npm test` is the fast required gate; `npm run python:test:full` checks the
  complete curated regression suite. Tests use the isolated test database, not
  the owner's production account data.
