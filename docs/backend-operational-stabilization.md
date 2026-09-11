# Backend Operational Stabilization

This follow-up keeps investment rules and delivery authority unchanged. It
addresses production AI claim recovery, outdated notification contracts and
bounded observation of the running system.

## Progress

- [x] Apply the existing bounded transaction retry policy to production AI claims.
- [x] Verify rollback, fresh claim leases, retry exhaustion and no model repetition.
- [x] Reconcile the six previously failing notification tests with current contracts.
- [x] Add read-only runtime verification and validate its SQL against local MySQL.
- [x] Complete integrated validation with the final source tree.
- [x] Observe the committed, restarted runtime for 900 seconds.
- [x] Publish validated code and restart the managed runtime.

## AI Claim Boundary

`MySQLAIInferenceQueueStore.claim` now uses
`transaction_with_deadlock_retry("ai-inference-claim", ...)`. Existing settings
control the budget: three retries by default, exponential backoff with jitter,
and the existing configured clamps. There is no new independent retry loop.
Numeric `0` and string `"0"` both disable retries; invalid or nonfinite budgets
use the default, and configured values remain bounded from zero to eight.

Only MySQL error **1213** retries the complete transaction. Every attempt owns a
fresh transaction, selected-row list and lease clock. A failed partial batch
does not leak claimed requests or increment persisted AI attempts. The caller
receives requests only after commit. Exhaustion propagates its operation and
attempt receipt so the existing scheduler can report and defer the cycle.

Connection loss, ambiguous commit acknowledgement, lock-wait timeout and SQL
errors are not immediately retried. No model execution, result publication or
notification transport is enclosed by this retry boundary. Previously
committed claims retain their existing lease/recovery behavior.

The store records `last_transaction_retry`; the runner captures
`last_claim_retry`, including exhausted attempts and a non-deadlock failure
after earlier retries. The latter retains its original exception and does not
start another transaction. Successful recovery emits a
bounded scheduler log with attempts, retry count and total backoff, even when
the retry finds no work. It contains no account or prompt payload.

Twelve focused tests cover these boundaries, including a committed claim whose
acknowledgement is lost: it stays leased, and no model call or immediate retry
occurs. A reconstruction check compares the
original claim body with its pre-change golden hash, preserving SQL strings,
row predicates, supersession handling, limits and claim serialization. Only
the retry wrapper and per-attempt ownership of temporary values change.

`verify_integrated_load.py` now calls the production claim method once and
counts its receipt. Its former harness-level retry was removed, preventing
multiplied retry budgets and making the measured behavior the production
adapter's behavior. Historical results in `integrated-load-verification.md`
still describe the older harness policy and must not be reinterpreted.

## Evidence And Limits

The initial 18 focused retry tests passed (12 new claim regressions and six
retry-component tests). All 18 backend integration/parity tests also
passed. The production-claim MySQL preflight completed 32 account-wave cases
across 16 accounts and four threads in 17.103 seconds, with no outer harness
retry. No deadlock occurred in that short run; the focused tests inject it
explicitly. Expected counts matched and the owned database cleanup was
verified. Local report: `/tmp/orbit-stability-production-claim-check.json`.

All six old notification failures now pass against current contracts, together
with 12 focused policy/boundary tests and two adjacent rejection tests: 20 tests
in 58.916 seconds. This changes tests, not TypeDB eligibility, AI authority,
delivery policy or investment semantics. See
[Legacy Notification Contracts](legacy-notification-contracts.md) for the exact
mapping and isolated reproduction commands.

## Production-Claim Load Result

The 900-second rehearsal passed: 16 synthetic accounts, four worker threads,
101 waves and 1,616 account-wave cases. Work plus recovery took 906.139 seconds;
the supervisor including setup and verified cleanup took 915.419 seconds.

- 45 recovered InnoDB 1213 deadlocks in 2,065 transaction attempts across 2,020
  measured claim calls. There was no harness-level retry.
- All persisted expected/actual counts matched; account violations were zero.
  Reasoning, AI and delivery backlogs were zero after the final wave.
- 101 expired claims were reclaimed, 15,360 late transitions were rejected,
  1,616 receipts were repaired and 101 publication rollbacks were verified.
  One terminal failure was deliberately injected and remained recorded.
- AI claim latency: p50 7.142 ms, p95 32.682 ms, p99 81.353 ms, max 186.096 ms.
  These timings include fixture verification/tracing, not model execution.
- Python retained allocations grew 1,581,662 bytes; process RSS high-water
  grew 8,204,288 bytes. This is not MySQL server memory or a long-term leak test.
- No model, native TypeDB inference, external request, customer delivery or
  managed-process mutation was executed. The owned database was removed.

Report: `/tmp/orbit-stability-production-claim-900s.json`. The long child loaded
before the final mixed-error diagnostic-receipt refinement; no such terminal
mixed error occurred in the passing run. That refinement and committed-but-lost
acknowledgement behavior passed separate injected tests with the final code.

## Runtime Observation

The final pre-deployment read-only smoke passed its infrastructure checks:
25 bounded SELECTs, both allowlisted HTTP routes, supervisor heartbeat and
current source freshness. Three linked subjects were `OBSERVATION` without an
AI request, not failed AI executions. Two old retry jobs belonged solely to
the candidate deployment; current active/delivery backlog was zero. They stay
visible under `candidateOnlyHealth`, and were not deleted or reclassified in
the application database. No new linked AI or delivery was claimed as proven.

See [Passive Runtime Continuity Verification](runtime-continuity-verification.md)
for replay commands, evidence criteria and reporting limits. Deployed-runtime
observation follows the required commit/restart. Read-only runtime evidence
does not force an AI invocation, submit a trade or send a customer notification.
Missing or stale linked evidence remains inconclusive, not an E2E pass.

## Integrated Validation

- `npm test`: passed, including 1,169 Python tests before the additional
  literal-only SQL integration case, 11 frontend tests and the web smoke test.
- Final `npm run test:full`: passed all 1,375 Python tests in 164.153 seconds,
  plus frontend checks/tests and the web smoke test.
- The 18 retry regressions passed, including mixed failure receipts and an
  already-committed claim whose acknowledgement is lost.
- The runtime verifier's 22 unit tests plus one MySQL literal-only test passed.
  Its 26 SQL cases exercise explicit source/lineage scope disagreement, global
  source boundaries, missing/malformed evidence, plural/singular precedence,
  case-sensitive identifiers and mixed collations. No schema or table is used.
- Scoped syntax and whitespace checks passed. Full/core suites were serialized;
  the 900-second workload used a separate exclusively owned database.

The pre-deployment smoke report is
`/tmp/orbit-stability-runtime-smoke-4.json`. It reports sampled infrastructure
as passing and new live AI/delivery as inconclusive. It is not a duration test.

## Deployed Runtime Result

Application commit `3793cc3bb` was pushed to `origin/main` and the managed
runtime restarted. All 23 configured services were running. MySQL, TypeDB and
the existing Cloudflare tunnel were preserved. Local version, bootstrap,
accounts/watchlist, symbol suggestion, notifications, calendar and case reads
returned HTTP 200 without payload errors. The authenticated Cloudflare version,
bootstrap and case reads also returned HTTP 200 and matched that commit.

The passive run observed **2026-09-11 18:19:21 through 18:34:22 UTC**:

| Evidence | Result |
| --- | --- |
| Requested / observed duration | 900 / 900.114 seconds |
| Scheduled observations | 16 of 16; no skipped slots |
| First-to-last sample span | 900.008 seconds |
| Allowlisted HTTP reads | 32 of 32 HTTP 200; maximum 6.711 ms |
| Bounded, rollback-only SELECTs | 400; no read failures |
| Maximum observation duration | 0.470 seconds |
| Latest live-source timestamps | Nine distinct timestamps; advanced without regression |
| Maximum sampled source age | 127.386 seconds; configured threshold 300 seconds |
| Primary active/delivery/AI/notification backlog | Zero at first, last and peak sample |
| Candidate-only backlog | Two old retry jobs throughout; `aged-backlog` warning retained |
| Sampled infrastructure / source progress | `pass` / `pass` |
| New linked AI / recorded investment delivery | `inconclusive` / `inconclusive` |

Stored subjects observed were `OBSERVATION` or `REVIEW_ONLY`, with no new
qualifying AI completion. The verifier exited **2**, deliberately distinguishing
incomplete live-AI evidence from a passing infrastructure check. No model,
investment notification, trade, promotion or repair was forced to obtain a pass.
The candidate-only warning was not hidden and its rows were not deleted.

Private local report:
`/tmp/orbit-stability-runtime-3793cc3bb-900s.json`, with a sibling progress log.
Both have file mode `0600`; the report includes the verifier source fingerprint.
Raw reports and runtime data are not committed. This document-only follow-up
does not alter the application code tested by that observation.

Remaining limits: minute-spaced samples cannot prove availability between
polls or 24-hour stability. Live TypeDB execution, a new AI-to-delivery chain,
model quality, and every account/provider's freshness remain separate from
the verified storage/queue contracts and sampled infrastructure result.
