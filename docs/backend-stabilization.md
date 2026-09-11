# Backend Stabilization

This batch follows ownership integration. It changes execution boundaries, not
investment rules, frozen releases, user notification policy or web navigation.

## Scope

- [x] Split projection orchestration into independently testable stages with
  explicit inputs/results and unchanged point-in-time and generation guards.
- [x] Delegate cross-owner transactional writes through connection-bound owner
  participants. Keep one commit and rollback boundary.
- [x] Fence existing reasoning job transitions against late workers and terminal
  state rewrites; retain coalesced source lineage and bounded recovery.
- [x] Exercise the integrated source/job/publication/outcome path in isolated
  fixtures, including failures and bounded performance measurements.
- [x] Run required and full validation.

Commit/push, managed restart, local/shared HTTP checks and owner notification are
revision-specific handoff steps. Their actual outcomes are reported in the task
handoff rather than asserted before this document is committed.

## Non-Negotiable Contracts

TypeDB remains the only investment-action authority. Exact source timestamps,
account/world/release identity, candidate fingerprints, last usable generations,
source-before-dispatch ordering and shared transaction atomicity are preserved.
Reads and atomic edits remain synchronous. Existing durable jobs own retries;
there is no additional message broker or generic asynchronous execution layer.

Tests never send investment messages or mutate the owner's account data. Native
failure rehearsals own their temporary servers. Managed databases are not crash
test targets. Fixture throughput is not a claim about production TypeDB or AI
latency; local/shared HTTP measurements are reported separately.

## Execution Boundaries

`reasoning/infrastructure/projection_write/record.py` coordinates eleven phases:
attempt preparation, source assembly, source selection, manifest repair,
validation, inference planning, exact reuse, audit creation, journal preparation,
candidate publication and follow-up scheduling. Each phase has a minimal port
and a frozen result envelope. Graphs are passed by ownership, not deep-copied.
The manifest repair algorithm remains the largest phase; this is not a claim
that every historical algorithm is now small.

Thirty-five connection-bound writes belong to portfolio, outcomes, decisions,
notifications, market data and reasoning. Shared transaction coordinators keep
ordering, commit and rollback; owner participants can only execute on the given
connection. No connection or queue is created per extracted function.

Reasoning transitions carry the worker ID and claim timestamp. An expired,
reclaimed or terminal attempt cannot update state, completion receipts or price
anchors. Heartbeats cannot revive expired attempts. Batch error recovery does
not retry already settled jobs. Existing bounded retry/backoff limits remain.

Receipt repair is explicitly separate: it locks a completed job, reads its
stored result and repairs matching receipts/anchors without changing the job
or repeating TypeDB/AI execution. A late AI worker also cannot supersede another
worker's active request.

## Verification Design

- Structural fixtures reassemble the original projection and owner SQL bodies;
  storage ownership is not validated only by happy-path tests. Queue ownership,
  explicit receipt repair and the AI ownership fix are the audited exceptions.
- Phase tests cover every early return, order, source identity, frozen envelopes,
  invalid candidates, audit failures and retaining audit identity on exceptions.
- Isolated MySQL tests cover coalesced lineage, same-worker reclaims, heartbeat
  expiry, terminal-state protection and rollback across the four coordinators.
- The source/job/AI publication/delivery receipt/decision follow-up test is a
  storage handoff rehearsal with explicit fixtures, not an end-to-end investment
model quality test. Native TypeDB recovery is tested separately.

## Results

- `npm test`: 1,045 tests passed, plus syntax and web smoke checks.
- `npm run test:full`: 1,250 tests passed, including 25 new focused regressions.
- Temporary native TypeDB: production static-manifest rollback after delete,
  rollback before commit and ambiguous-response retry passed; one row remained.
- Temporary native TypeDB restart: uncommitted rollback and equivalent exact
  source replay passed. Neither rehearsal touched the managed database process.
- Minimal queue completion fixture, ten sequential samples with no pending
  price anchors: five SQL statements, 225 response bytes, p50 2.80ms and p95
  5.16ms. This measures only the local durable handoff, not TypeDB/AI inference,
  large-account throughput or an improvement against a production baseline.
- Source coordinator is now 366 lines (previously 1,874). Scoped manifest repair
  is still 514 lines; the large transaction coordinators remain explicit shared
  boundaries, not services that should all become asynchronous.
