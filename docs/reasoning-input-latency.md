# Reasoning Input Latency

## Measured Bottlenecks

The realtime path was repeatedly extracting hypothesis contracts from large
decision audit documents before TypeDB ran. In a local account profile,
`performance_episodes` took 10.149 seconds while evaluating the returned
performance metrics took only 0.204 seconds. Shadow history took 0.087 seconds;
the decision-history SQL alone took 8.480 seconds. Moving the metric calculation
to another worker would not directly address that expensive read.

A separate cold-start trace rebuilt its source twice: a partial assembly took
26.416 seconds and the subsequent required full assembly took 55.055 seconds.
The cold request took 178.740 seconds overall. These are observations, not a
latency guarantee or a benchmark of every account and market condition.

## Compact Calibration Inputs

`outcomes` owns the disposable `investment_decision_calibration_inputs` read
model. It has one row per decision episode:

- `episode_id`: original decision identity and primary key.
- `source_updated_at`: exact original row version.
- `format_version`: projection contract version.
- `hypotheses_json`: ordered hypothesis IDs and their original claim contracts.

The original decision, outcome, evidence and audit documents remain authoritative.
No article bodies, credentials, full decision facts or generated narrative are
copied into this table. Hypothesis order and duplicate-ID first-match behavior
are retained.

Decision creation, outcome updates and legacy qualification repair update the
compact row in the same transaction as the original episode. A write failure
rolls back both. The performance reader uses the compact row only when its source
version and format match. Missing or stale rows fall back to the original JSON,
so deployment and repair do not hide historical observations or reset a model's
qualification. There is no TTL cache, delayed aggregate or relaxed as-of cutoff.

The existing low-priority MySQL maintenance worker repairs at most 25 rows per
pass by default, with an explicit maximum of 100. Its candidate scan does not
lock the whole history. It locks only each selected primary key with
`FOR UPDATE SKIP LOCKED`, re-reads the source under that lock, and skips busy
decisions. Bounded orphan cleanup removes only compact rows whose originals no
longer exist. The table is reconstructible and does not accumulate generations.

## Cold Source Preflight

After acquiring the existing projection lease, source assembly reads the active
manifest once. A missing, incomplete, old-version or migration-requiring manifest
selects full input immediately. A ready manifest can still use targeted input.
The later detailed patch checks and full-source fallback remain in place;
preflight does not bypass publication validation or expand investment authority.

TypeDB remains the sole investment-action authority. Account/world/release
identity, frozen input timestamps, source lineage, notification admission,
cooldown and shadow delivery isolation are unchanged.

## Verification

On 2026-09-14, a repeatable-read transaction compared the previous reader with
the compact reader at the same cutoff (`2026-09-14T00:00:00Z`):

| Measurement | Previous | Compact |
| --- | ---: | ---: |
| History read | 8.065 seconds | 0.370 seconds |
| Returned episodes | 751 | 751 |
| Exact episode equality | Yes | Yes |
| Exact computed performance equality | Yes | Yes |

This is about a 95% reduction in that history-read component, not in total
TypeDB, AI or notification latency. The existing 3,266 decision rows were
backfilled in bounded batches without deleting source history or sending an
investment alert. New worker receipts are verified separately during handoff.

Regression tests cover exact SQL/raw fallback parity, source and format changes,
future/account exclusion, same-transaction rollback, locked-row repair and orphan
cleanup (`test_decision_calibration_inputs`). Source preflight tests cover cold,
warm, explicit fresh, incomplete and incompatible manifests, full target sets and
the final safety fallback (`test_projection_input_preflight`). Structural
governance records exact reviewed implementation hashes rather than disabling
baseline checks.

Remaining latency must be measured by stage: source assembly, TypeDB persistence,
native inference, AI processing and delivery are distinct. Further optimization
must preserve exact evidence and outcome qualification, rather than removing
waiting controls or generating messages without investment evidence.

## Writer Contention and Current-State Queue Maintenance

The independent worker maintains ingress, expired observations, comparisons and
dead leases before acquiring the physical TypeDB writer. These MySQL operations
continue during writer contention, at most once per existing heartbeat interval.
Job claims and graph execution still require the single server-wide writer lock;
the control binding is rechecked immediately before claiming. Shared-world
projection and maintenance take a separate lease after the inference turn.
Every completed watch turn also yields for its configured polling interval.
Previously a successful delivery turn could immediately reclaim the writer while
the candidate slept, starving candidate validation even with no execution errors.

Replaceable observation expiry uses both queue age and the current immutable
source timestamp. A mailbox that has waited a long time but now contains fresh
coalesced facts is retained. Shards use the existing per-symbol work-class
contract, so an unrelated company's research cannot protect an obsolete quote.
Mixed evidence, disclosures and research remain durable. Queue health exposes
`oldestPendingSourceAt`, `oldestPendingSourceAgeSeconds` and missing source clocks
separately from the original queue debt; no timestamps or latency gates are reset.

Health queries exclude request packets and successful terminal-result bodies.
Completed execution metrics and failed result diagnostics remain exact, preserving
promotion counts, latency percentiles, failure resolution and source lineage.
These operational changes do not promote a candidate or authorize its delivery.

A read-only comparison on 2026-10-16 used one transaction and the same clock:
all returned health metrics matched. The active deployment's returned row bytes
fell from 98,054,187 to 7,919,754, and the candidate's from 7,956,191 to 1,434,251.
The measured active read took 0.879 versus 0.320 seconds. This reduces health-read
cost and memory; it is not evidence that total inference latency meets its SLO.

Receipt cooldown lookup was another live bottleneck: candidate construction
repeatedly took about 30 seconds, while the original JSON-scanning SELECT was
still executing on MySQL after 52 seconds. The V2 cadence reader now passes
the actual event account/symbol pairs to the receipt store. The store uses the
existing subject indexes, joins delivered receipts before inspecting JSON and
keeps the opaque cadence-key comparison plus maximum receipt completion clock.
The server execution deadline matches the existing MySQL connection timeout,
so a timed-out client does not leave an indefinitely running read behind.
Legacy readers and unscoped events retain their compatible lookup path.

A same-transaction, read-only probe of 19 delivered jobs (90,249,873 source
payload bytes) returned the exact original receipt clock in 4.782 seconds.
This measures the scoped receipt query, not the full candidate-build stage.
Regression coverage includes account IDs and opaque keys containing colons,
failed receipts, other-account/subject traffic and legacy reader compatibility.
