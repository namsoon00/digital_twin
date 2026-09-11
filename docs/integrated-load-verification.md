# Integrated Load Verification

This rehearsal exercises the **real MySQL operational schema, connection pool,
durable queues and transaction adapters**, using synthetic account identities.
It is not an in-memory queue benchmark or a production capacity certification.

## Run

From the repository root, with Python and the existing `pymysql` dependency:

```bash
PYTHONPATH=python_service:python_service/tests python3 -m unittest -v test_integrated_load

python3 python_service/tests/verify_integrated_load.py \
  --mode regression --accounts 4 --concurrency 2 --rounds 2 \
  --output /tmp/integrated-load-regression.json

python3 python_service/tests/verify_integrated_load.py \
  --mode soak --accounts 8 --concurrency 4 --duration-seconds 15 \
  --max-cases 256 --output /tmp/integrated-load-short-soak.json
```

The requested longer soak uses this bounded configuration:

```bash
python3 python_service/tests/verify_integrated_load.py \
  --mode soak --accounts 16 --concurrency 4 --duration-seconds 900 \
  --recovery-seconds 120 --max-cases 4096 \
  --output /tmp/integrated-load-900s.json
```

The configuration alone is **not a measured capacity claim**; see the executed
results below. A cap reached before the requested duration is a nonzero,
incomplete run, not a successful shorter soak. No fallback queue or silent
skip is used when MySQL is unavailable. The focused test suite includes one
three-account, two-thread, two-wave MySQL run; its safety unit tests do not
substitute for that integration run.

With `--output`, a sibling `<output>.progress.jsonl` records the owned schema,
supervisor/worker PIDs, completed waves/cases, elapsed workload time and drained
backlogs. The final JSON also records bootstrap and full supervisor elapsed
time including cleanup. A progress line is not a successful final report.

## Isolation

- Use an already running **local** MySQL server. Only `MYSQL_HOST` (loopback),
  `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, and an absolute
  `MYSQL_UNIX_SOCKET` are accepted from the caller. The local DB user needs
  CREATE/DROP privileges for the temporary schema. No dotenv file is loaded.
- The supervisor chooses `orbit_alpha_test_integrated_load_<24 hex digits>`.
  It uses exclusive CREATE, never adopts or resets an existing schema, and
  accepts no public database-name override. Other test workers' schemas,
  `orbit_alpha_test`, and application databases are not cleanup targets.
- Reuse `mysql_test_settings` and the source/episode builders from
  `stabilization_database.py`. Retention and partitioning are off, matching the
  existing transaction fixtures. Normal production schema initialization and
  connection pooling still run. This does not test production partitioning.
- The child has temporary data/settings paths, no inherited AI/Telegram/account
  credentials, no DB URL overrides and no inherited application `PYTHONPATH`.
  A fail-closed Python guard blocks foreign DB connections, external socket/DNS
  and datagram operations, and subprocess launches. Attempted blocked access
  fails the run even if an adapter catches the exception. This is a harness
  guard, not an OS sandbox for arbitrary native code.
- No account registry records are loaded or created: `service_accounts` must
  remain empty. Fixed, one-position fixture snapshots use synthetic account IDs
  and the same AAPL symbol to expose cross-account scope mistakes.
- The supervisor owns one child. On a hard timeout only that child is killed;
  the supervisor drops and verifies absence of its exact schema. It never
  starts, stops, restarts or kills managed services or the MySQL server.
  Normal fixture exit cleanup is also retained. Cleanup failure makes the run
  fail and reports only the owned schema name for manual follow-up.

## Workload

This is a repeatable **burst / drain / recovery** workload. Ingress workers run
concurrently with each other; reasoning consumers run concurrently with each
other; AI-storage consumers run concurrently with each other. Those phases do
not overlap. This deliberately does not claim sustained open-loop ingress,
multi-process contention, HTTP load or arbitrary portfolio-size coverage.
Configured concurrency is an upper bound, not guaranteed continuous worker
saturation; `peakConcurrentReasoningClaims` reports observed claim overlap.

Each wave performs these operations for every configured account:

1. Persist three distinct synthetic snapshot revisions through
   `MySQLMonitorStore`, including snapshot history and verified source inputs.
   Persist each source event through `MySQLEventLog`, which invokes the real
   durable reasoning ingress in its transaction. Replay each source twice and
   replay the older coalesced predecessors. Exactly three source/job rows and
   one surviving account-scoped job must remain, with all predecessor lineage.
2. Mark a real market-observation reasoning anchor pending. Concurrent workers
   use `MySQLReasoningEngineJobStore.claim` and its `FOR UPDATE SKIP LOCKED`
   path. Track duplicate active ownership, check exact source/account/revision
   identity, heartbeat, and bind a fixed synthetic release fingerprint.
3. Hold one account's claim without completing it. Another account executes the
   real retry transition, verifies its future backoff, then freezes its
   availability until the recovery phase. The other accounts complete normally.
   The durable interrupted backlog must be exactly two jobs.
4. Backdate only the held fixture row's lease, reject its expired heartbeat,
   reclaim with a fresh adapter and the **same worker ID**, and verify a new
   claim timestamp. Reject all eight late mutating transitions with the old
   token and with a foreign worker. Backdate only the retry row's availability
   and drain it without resetting its persisted attempt count.
5. Complete through the real receipt/anchor transaction using explicitly
   synthetic generation/ABox IDs. Reject all terminal rewrites. Reopen the
   fixture anchor through its real writer and repair from the completed job's
   persisted result. A second repair must be a no-op and the completed job row
   must remain byte-for-byte unchanged. An unrelated synthetic account's older
   pending anchor is a sentinel and must never change.
6. Enqueue/replay a notification-associated AI request through the real
   notification and AI outboxes. Supply a **synthetic storage result**, not an
   AI/model response. Complete the AI publication transaction with a decision
   episode saved on the same connection, plus audit and delivery release.
   Reject foreign-worker publication and repeated terminal publication.
7. Persist a clearly labeled synthetic delivery-attempt receipt, with no channel
   transport invoked. Persist/replay an `ObservedOutcome` and join its account,
   decision, request, source event and generation back to the reasoning receipt.
   Each account must have exactly one outcome per completed wave.

Fault probes also roll back a source write after its first owner write, and
once per wave interrupt AI publication after its owner/result/delivery writes
but before commit. Counts across affected owners and the outbox must not move.
The publication fault window is serial to make its count assertions exact.
One extra terminal-only job exhausts a one-attempt retry budget; it must stay
failed and cannot be repaired as a completed result.

Lease expiry and retry availability use explicit fixture-row clock edits.
The real minimum 60-second reasoning lease and initial 10-second retry sleep
are **not waited out**. Recovery latency starts at injected expiry/availability,
not at process death. No actual worker crash, MySQL crash or network outage is
simulated. Fresh adapters reread durable state in the same child process.

## Limits And Metrics

| Control | Default | Accepted bound |
| --- | --- | --- |
| Accounts | 4 | 2-64 synthetic IDs |
| Concurrency / MySQL pool size | 2 | 1-16, no more than accounts |
| Regression rounds | 2 | 1-1,000, within case cap |
| Soak duration | 30 seconds | 1-3,600 seconds |
| Recovery grace after admission deadline | 30 seconds | 5-120 seconds |
| Completed account-wave case cap | 2,000 | 2-10,000 |
| Pause between soak waves | 0.2 seconds | 0-5 seconds |

Regression has a 60-second workload admission budget. Soak admits waves until
its duration expires, then finishes the current wave within recovery grace.
Both modes have at most 1,000 waves. Schema/import setup has a 90-second budget;
the supervisor's child timeout is setup + workload + recovery + 15 seconds.
Admin cleanup is outside that child timeout, with five-second connection/read/
write timeouts. Runtime DB calls also use five-second driver timeouts; the
existing pool/deadlock retry contracts remain in use. AI claiming is wrapped
explicitly by this harness in the existing `run_mysql_deadlock_retry` component,
with at most three retries for InnoDB error 1213 only. The entire short claim
transaction is retried, never inference, publication, delivery or ambiguous
connection loss. `aiClaimTransactionAttempts` and `aiClaimDeadlockRetries`
report that policy separately; it is not a claim that the unwrapped production
AI consumer already has this retry policy. These are stall guards,
not service-level performance objectives.

For `C = accounts * completed waves`, the final checks require `3C + 1`
reasoning jobs (`C` completed, `2C` superseded, one deliberately failed),
`6C + 1` lineage rows, `3C` verified snapshot/history rows and `C` rows each for
AI requests/results/audits, notifications/delivery attempts, decisions,
follow-ups, outcomes and reasoning completion receipts. Account snapshots and
anchors are current-state rows; there is one additional untouched sentinel
anchor. Final reasoning, AI and notification backlogs must all be zero. The
sentinel's deliberately pending anchor is not queued work.

JSON reports contain exact nearest-rank p50/p95/p99, max and sample count for
snapshot/event commit and replay, claim, receipt completion/repair, retry,
publication, synthetic receipt/outcome persistence and recovery drains.
`sourceToOutcome` includes phase waiting and probes, starts when an account's
producer actually begins, and excludes executor admission wait and bootstrap.
Claim timing includes empty polls. `expiredClaimRecovery` includes fencing and
publication-handoff assertions; it is not a pure reclaim-SQL microbenchmark.
All successful-run samples are retained, capped at 100,000 per named metric;
there is no unreported sampling or fabricated percentile for an empty series.

Memory reports compare post-bootstrap and post-workload Python traced
allocations and **process RSS high-water**, not current RSS or MySQL server
memory. Full-process tracing and assertion/query overhead are included in
latencies. Measurement arrays, backlog history, warmed caches and live fixture
objects contribute to growth; a short delta is not a memory-leak proof.

## Measured Runs

Local Python 3.9.6 and MySQL 9.7.1; the existing local server and host were
shared with other integration work. No run here establishes 24-hour stability
or production capacity. Times include tracing and verification overhead.

| Run | Completed cases | Workload elapsed | Full supervisor elapsed | Result |
| --- | --- | --- | --- | --- |
| Focused tests, 3 accounts / 2 threads / 2 waves | 6 | Not separately retained | unittest: 13.332 s | 11 tests passed |
| 8 accounts / 4 threads / 15 s admission window | 32 in 4 waves | 17.945 s | 28.659 s | Passed, cleanup verified |
| Final harness preflight, 16 accounts / 4 threads / 2 waves | 32 | 17.613 s | 27.522 s | Passed, cleanup verified |
| Actual soak, 16 accounts / 4 threads / 900 s admission window | 1,552 in 97 waves | 904.162 s | 913.679 s | Passed, cleanup verified |

The 8-account report is `/tmp/integrated-load-short-diagnostic.json` and the
final 16-account preflight is `/tmp/integrated-load-16-account-check.json`.
The final preflight retained 97 reasoning jobs (32 completed, 64 superseded,
one intentionally failed), 193 lineage rows, 96 source snapshots and 32 linked
outcomes, with zero account mismatches or remaining queue work. It needed zero
AI-claim deadlock retries.

Final preflight latency (milliseconds; p50 / p95 / p99):

- Source-to-outcome, 32 samples: 5,856.152 / 9,000.941 / 9,061.134.
- Reasoning claim including empty polls, 53 samples: 8.030 / 33.121 / 88.702.
- AI publication transaction, 32 samples: 17.237 / 37.945 / 40.817.
- Python current-allocation delta: +752,625 bytes; process RSS high-water
  delta: +4,857,856 bytes. Bootstrap took 8.202 seconds.

An earlier 8-account attempt **failed** with an `OperationalError` after 24
cases, before final reconciliation. Its owned schema was cleaned up. That
initial report predated numeric-error diagnostics and cannot retrospectively
classify the exact error. It is retained at
`/tmp/integrated-load-short-soak.json`, not counted as a pass. The repeated
8-account run passed; the final harness subsequently added explicit counted
1213-only claim retries. Numeric DB error, SQL operation and stack diagnostics
are preserved for subsequent failures without credentials or account payloads.

### Actual 900-Second Soak

The exact 900-second command above completed with `status: passed`. The admission
window was 900 seconds, bootstrap took 7.484 seconds, workload including final
probes/reconciliation took **904.162 seconds**, and full supervisor time including
cleanup was **913.679 seconds**. It completed 97 waves / 1,552 account-wave cases
with observed reasoning claim concurrency 4. Wave 96 finished at 894.678 seconds
and wave 97 at 903.552 seconds; this was not a shorter cap-limited pass.

Limits were 16 synthetic accounts, 4 threads, 0.2 seconds between waves, a 4,096
case cap (at most 12,289 reasoning jobs and 24,577 lineage rows), 1,000 waves,
90-second setup budget, 120-second recovery grace and 1,125-second child hard
timeout. No case/wave cap or hard timeout was reached.

Evidence is retained locally, not committed:

- Full report: `/tmp/integrated-load-900s.json`.
- Raw progress log: `/tmp/integrated-load-900s.json.progress.jsonl`.
- Supervisor PID 70517 and worker PID 70520 both exited; exec session 51120
  closed with exit code 0, and the report records `workerExitCode: 0`.
  A subsequent `ps -p 70517,70520` returned no processes.
- Owned schema `orbit_alpha_test_integrated_load_96d420117420493f9d32828c`
  was dropped and its absence verified: `cleanupVerified: true`.

Final raw progress entries:

```jsonl
{"status": "running", "workerPid": 70520, "wavesCompleted": 97, "casesCompleted": 1552, "elapsedWorkSeconds": 903.552, "aiClaimDeadlockRetries": 42, "reasoningPending": 0, "aiPending": 0, "deliveryPending": 0}
{"status": "passed", "cleanupVerified": true, "supervisorElapsedSeconds": 913.679}
```

Count reconciliation matched every expected table count. Reasoning jobs totaled
4,657: 1,552 completed, 3,104 superseded and one deliberately failed exhaustion
probe. There were 9,313 lineage rows, 4,656 snapshot-history rows, 4,656 verified
source snapshots and 4,657 source events including that terminal probe. Each
AI request/result/audit, notification/delivery-attempt, decision/follow-up/outcome
and reasoning-receipt table held exactly 1,552 rows. All 1,552 outcome anchors
joined correctly, with exactly 97 outcomes per account. Current-state tables
held 16 snapshots and 17 anchors including the unchanged sentinel.

Exact zero invariants and recovery evidence:

- Account violations: **0**; expected-versus-actual count mismatches: **0**.
- Reasoning / AI / delivery pending work: **0 / 0 / 0** after every one of the
  97 waves and at final reconciliation. Sampled backlog per wave moved from
  16 reasoning jobs to 2 interrupted jobs, then 0 after recovery; AI and
  delivery queues each reached a sampled maximum of 16 before draining.
- Account registry rows (`service_accounts`): **0**. Blocked foreign-schema,
  socket and process access attempts: **0 / 0 / 0**. External requests: **0**;
  model, TypeDB, external delivery and managed-process flags were all false.
- Expired claims reclaimed: 97; durable retries scheduled/recovered: 97;
  persisted receipt repairs: 1,552; sentinel-isolation checks: 97.
- Late transitions rejected: 14,752; late release bindings rejected: 1,844.
  Source duplicate replays: 9,312; coalesced predecessor replays: 3,104;
  outcome duplicate replays: 1,552, with no duplicate durable outcomes.
- Publication rollbacks verified: 97; source rollback verified: 1;
  terminal retry exhaustion verified: 1. The intentionally failed job is
  expected, terminal and excluded from pending work, not an unreported failure.

There were **42 recovered InnoDB 1213 deadlocks** in 1,982 AI-claim transaction
attempts across 1,940 timed claim calls (including empty polls). These retries
used the explicitly harness-applied, existing bounded retry component described
above. This is neither a zero-deadlock result nor evidence that the unwrapped
production AI consumer already has that retry policy.

Actual soak latency, milliseconds; nearest-rank percentiles, with tracing and
verification overhead included:

| Metric | Samples | p50 | p95 | p99 | Max |
| --- | ---: | ---: | ---: | ---: | ---: |
| Source snapshot commit | 4,656 | 74.069 | 111.319 | 153.170 | 493.162 |
| Source event ingress | 4,656 | 99.548 | 144.470 | 169.948 | 408.415 |
| Source event replay | 4,656 | 163.583 | 220.288 | 277.413 | 449.274 |
| Coalesced source replay | 1,552 | 154.691 | 203.368 | 256.722 | 447.356 |
| Reasoning claim, including empty polls | 2,523 | 9.412 | 38.935 | 86.957 | 393.639 |
| Reasoning completion with receipt | 1,552 | 9.070 | 37.230 | 85.712 | 495.666 |
| Completed receipt repair | 1,552 | 13.733 | 46.199 | 80.499 | 198.276 |
| Reasoning retry | 97 | 2.992 | 23.437 | 112.163 | 112.163 |
| Expired claim recovery | 97 | 241.411 | 395.346 | 742.648 | 742.648 |
| Retry recovery | 97 | 157.726 | 247.951 | 827.076 | 827.076 |
| AI outbox enqueue | 1,552 | 59.363 | 140.595 | 233.844 | 394.109 |
| AI claim, including empty polls | 1,940 | 7.532 | 33.048 | 82.226 | 184.957 |
| AI publication transaction | 1,552 | 31.391 | 50.600 | 106.176 | 289.026 |
| Publication rollback | 97 | 17.272 | 45.275 | 168.507 | 168.507 |
| Synthetic delivery receipt | 1,552 | 10.210 | 37.321 | 93.065 | 282.986 |
| Outcome and replay | 1,552 | 13.483 | 37.279 | 86.377 | 238.771 |
| Source to outcome | 1,552 | 6,346.665 | 9,373.025 | 12,396.197 | 16,476.102 |
| Wave recovery drain | 97 | 1,729.884 | 2,395.534 | 5,207.808 | 5,207.808 |
| Whole wave | 97 | 8,735.857 | 11,146.212 | 16,487.717 | 16,487.717 |

Child Python current traced allocations grew from 17,082,527 to 18,633,692
bytes (**+1,551,165 bytes**). Process RSS high-water grew from 84,680,704 to
90,685,440 bytes (**+6,004,736 bytes**). The traced peak stayed at the bootstrap
peak of 23,512,217 bytes (delta 0). These are child-process measurements, not
MySQL server memory, a leak verdict or a 24-hour extrapolation.

This slice started no additional load or test runs after this soak. The focused
suite remains **11 tests**; the parent owns final sequential regression runs.

## Scope Exclusions

The fixture `HOLD` episode and graph/AI identifiers are persistence contracts,
not validated investment opinions. No TypeDB query, real graph publication,
model inference, candidate/action-envelope admission, live market ingestion,
Telegram/API transport, provider capacity, account authentication, web/internal
route, or production deployment promotion is exercised. The snapshot commit,
source-event transaction, reasoning receipt transaction, and AI/decision
publication are separate existing boundaries, not one distributed transaction.
These results do not prove live TypeDB/AI throughput or production capacity.

## Integration Handoff

The parent registered this manifest entry (not changed by this slice):

```json
{"file": "test_integrated_load.py", "tier": "integration", "core": true}
```

`verify_integrated_load.py` is an opt-in executable helper, not a manifest test
file. No package script is required.
The parent owns integrated `npm test`/full-suite validation,
commit/push, managed restarts and handoff notifications. This slice does none
of those process-management or publishing operations.
