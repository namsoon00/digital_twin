# Reasoning Input Latency

## Yield delivery priority while retaining prepared input (2026-10-26)

The v4 turn reservation removed repeated candidate source reads but also blocked
delivery during those MySQL reads. Independent reasoning now fully releases a
candidate's writer and turn reservation before source preparation. If delivery
owns the writer when preparation finishes, the suspended engine call retains
exactly one prepared batch and waits without rebuilding it or opening a native
transaction. Delivery preserves its existing priority during its own source
reads. The server still has exactly one native writer.

The original attempt heartbeat remains active during this wait. The exact claim
token and current control binding are checked periodically and again after
writer acquisition; execution readiness is rechecked before TypeDB. Lease loss,
uncertain ownership, control changes, shutdown and server cooldown retain their
normal fail-closed behavior. Resident waiting is bounded by one configured lease
interval (600 seconds by default). Timeout or restart retries the durable
original source, without substituting a newer snapshot or refreshing its clock.
No global prepared-input cache or shared result between engines is introduced.

`preparedInputWriterWaitMs` records this wait separately from `inputAssemblyMs`.
It remains included in the complete execution duration and all existing
promotion limits; moving the wait outside a writer lease does not make it free.
This change only releases the source-read boundary. Already-running native
projection, persistence and inference still finish under their writer fence.
An unusable source returns its original preflight reason without waiting for a
writer, because it has no native work to execute.
Candidate bootstrap cost, initial queue admission, temporal comparison parity
and the 10-second complete-path target remain separate unresolved work.

Regressions in `test_rule_delivery_latency` prove that delivery can acquire the
actual OS writer during candidate preparation, that the exact prepared snapshot
is consumed once afterward, and that lease/control/readiness/shutdown changes
cannot enter native inference. Bounded timeout and cooldown cases also preserve
the original retry and single-writer contracts.

## Granted turns and reproducible native witnesses (2026-10-26)

The candidate could acquire the TypeDB writer, release it for immutable source
preparation, then lose its turn to delivery before inference. Its prepared input
was discarded and prepared again on the next retry. Writer contract v4 retains
an OS-backed background turn reservation across that preparation. A waiting
delivery worker reserves the next turn; newly arriving background workers still
yield to delivery. Normal completion, input/lease failure and process death
release the reservation. This removes repeated preparation after admission; it
does not remove the initial wait or make TypeDB transactions concurrent.

One failed comparison had identical logical ABox scope fingerprints but selected
different witnesses for `graph.security_line.coverage_gap.v1`: a stale ADR gap
versus a fresh ETF gap. Grounding ordered matches by physical assertion hashes,
which change across worlds/snapshots. Native engine v20 selects a usable witness
among the exact TypeDB matches, then orders ties by logical endpoint identity.
All-stale, missing-clock, future and explicitly unusable witnesses remain
unusable. Native predicates and source clocks are unchanged. A successor release
records the current native engine contract instead of inheriting its predecessor's
interpreter tag; historical failed comparisons remain immutable.

Replay packet serialization no longer deep-copies the complete history before
encoding or after decoding into a newly owned JSON tree. Canonical bytes,
compression, hashes, secret filtering, size/account checks and mutation isolation
are preserved. A fixed ETH source/context replay with graph caches disabled and
profiling enabled measured assembly at 3.450s before and 2.243s after; the logical
graph digest and all 51 entities/55 relations were identical. This is a limited
input-preparation measurement, not a production end-to-end latency claim or a
stock trading test. Live queue, TypeDB execution and notification latency still
need separate observation.

An unprofiled alternating three-run benchmark over the same 3,660,069-byte
context measured packet encode/decode medians of 0.4489s and 0.1846s. The full
packet, including compressed bytes and source clocks, was identical. The native
witness smoke passed both physical identity orders; all 96 existing current-flow
TypeQL checks also passed, with zero delivery and no operational graph writes.
V20 compiler snapshots differ only in the diagnostic engine-version profile;
query predicates, schema and static restoration fingerprints remain unchanged.

Regressions: `test_graph_writer_guard`, `test_rule_delivery_latency`,
`test_native_evidence_witness`, `test_runtime_packet_integrity`, and
`test_reasoning_engine_versions`. `native_evidence_witness_smoke.py` exercises
the actual TypeQL witness set in an isolated temporary database; the existing
`native_current_flow_smoke.py` checks source/clock/session/linked-phase gates.
The curated runner now acquires the existing test-schema lock before loading
fixtures. Direct AI storage fixtures use that same lock and the configured
managed test schema, including nested recovery checks. Concurrent local test
sessions must not delete one another's rows while validating a release.

## Indexed execution preparation and the acceptance boundary (2026-10-26)

The execution profiler found 5.29 million structural dependency comparisons in
one frozen initial projection. This happened while selecting affected rules,
before TypeDB evaluated their predicates. The planner now indexes exact,
colon-delimited ancestor/descendant keys and uses an inverse consumer index for
the transitive rule closure. Direct triggers, invalidation consumers, cycles,
per-symbol boundaries and shared dependencies retain the same semantics. An
index belongs to one plan; it cannot retain an old observation or rule release.

Native evidence normalization now visits only fields present in each evidence
row, preserving nested overrides, the allowed vocabulary and the complete
fingerprint validation. Persistence preparation builds node rows once and
derives the insertable subset while retaining external relation endpoints.
These are reductions in actual preparation work. They do not omit conditions,
skip persistence verification, move required work into an untracked background
task, or add another investment-rule evaluator.

The acceptance goal for a future realtime path is **P95 receipt-to-durable-decision
within 10 seconds**, including preparation, writer/queue wait and publication.
This is separate from notification transport and provider observation age.
The existing deployment health/promotion limits do not certify this goal.
Do not add separate stage percentiles to claim a complete-path percentile.
Cold rebuilds and warm incremental executions must be reported separately;
unchanged-source replays cannot stand in for new market observations.

Frozen replay compares the same source, runtime context, model releases and
RuleBox in independently seeded temporary TypeDB databases. Graph caches are
disabled; release seeding is outside the projection timer. Full input/topology,
scope/impact plans, rule/subject matches, native completion and generation
alignment must agree. Frozen context omits live history/source reads, queueing,
decision publication and notification transport, so this replay alone cannot
establish the 10-second goal or trading accuracy. The fixture has no stock
entry/exit decision. Temporary databases are deleted and no customer message is
sent. `test_reasoning_critical_path` additionally checks exhaustive dependency
equivalence, downstream/cyclic invalidation, boundary/value changes and external
endpoint persistence; it is included in `npm test`.

The unprofiled before/after replay against `0c498627e` measured:

| Projection | Before | After |
| --- | ---: | ---: |
| Initial complete projection | 107.284s | 93.054s |
| Next subject from the same frozen source | 20.817s | 15.589s |

All 30 complete-payload comparisons passed: source/material identity, native
planner topology, logical scopes, impact plan, incoming/persisted comparison
scope, request context, RuleBox identity, executed/matched counts, exact native
matches and the resulting InferenceBox collections. Both generations aligned.
The second projection contains no new physical fact rows; it measures switching
subjects on an already persisted source, not a fresh-price update. These are one
paired local run under normal worker load, not a statistically established P95.

**Decision: retain the verified preparation reduction, but do not accept it as
a solution to the 10-second realtime goal.** Even the reduced second projection
takes 15.589s before live queue/source reads and decision publication. Its native
rule-query stage is 2.497s, while ABox persistence is 3.497s, including 2.218s of
manifest/control writes, and the complete native inference path is 6.299s,
including those queries. These nested measurements must not be added together.
Query tuning alone cannot remove the remaining preparation/publication cost.

The next structural experiment must consume a versioned, subject-bounded fact
packet and an immutable release dependency plan without re-preparing the complete
manifest for every request. It must prove the same source, downstream-rule and
counter-evidence closure, then use native TypeQL and an atomic durable result.
The complete graph/audit projection can follow only through a durable outbox
with replayable proofs. This requires a new projection contract and full-path
comparison; the current change does not implement or activate that replacement.

## Bound discarded cache serialization and repeated assertion encoding (2026-10-25)

The r13 candidate's slow paired runs spent 11–15 seconds constructing an
optional persistent cache payload that was then rejected as `skipped-oversized`.
The cache serialized both complete graphs before checking its 8 MiB limit.
Serialization now counts the encoded UTF-8 row bytes across both graphs and the
frozen context packet, stopping when that lower bound exceeds the limit. The
complete graph used for TypeDB remains intact. Small cache entries retain their
exact wire payload, version, key, TTL and copy isolation. A final exact size
check includes envelope fields and punctuation before any database write.

Scope preparation also repeatedly encoded the same assertions in overlapping
field and kind/field dependency groups. Each immutable scope calculation now
reuses each row's exact spaced sorting string and compact digest string. The
array digest, duplicate multiplicity and ordering remain unchanged. Field-family
classification is reused only within a single build; subsequent builds recheck
the policy and all observed values. No market freshness or judgment is cached.

Three runs per revision on the same preserved full graph measured median
oversized-cache work of **7.767s before / 1.107s after**. Scope identity preparation
measured **9.698s before / 8.746s after**. All six identity runs produced the same
48,109,872-byte complete graph/manifest payload and SHA-256 digest. This is a
component comparison, not an end-to-end latency or trading-accuracy claim.
Local workers were running during measurement; no production state or customer
notification was written by these benchmarks. The real paired worker comparison
and existing promotion gates remain required for activation.

`test_projection_computation_parity` verifies overlapping/duplicate assertion
digests, changed policy/value isolation, bounded UTF-8 serialization, a shared
two-graph/context budget, the final envelope check, no writes or partial cache
entries on rejection, and exact small-cache persistence/readback.

## Reduce graph rebuild and quality-record computation (2026-10-25)

Profiling a frozen operational source found repeated JSON hashing, field-name
normalization and research-event parsing during complete graph construction.
Quality recording also rendered the complete RuleBox/InferenceBox/AI presentation
only to read its constant prompt version. Validation scanned every entity twice
for each relation endpoint.

Field-name normalization now uses bounded 4,096-entry vocabulary caches; policy
membership and observed values are always evaluated anew. Semantic groups that
contain the same immutable assertion objects share their digest only during one
scope calculation. The exact spaced-JSON sort, compact-JSON hash, duplicate
multiplicity and persisted scope identities remain unchanged. Overlapping temporal
windows parse each exact research source once per subject build. No market value,
outcome, judgement or parsed research result is cached between source builds.

Quality records read the existing prompt-version constant directly and retain
the complete validation report. Endpoint validation builds one entity lookup,
preserving first-match behavior even for duplicate IDs and all missing/wrong-type
endpoint errors. Quality recording remains synchronous and auditable; this does
not move required records into an unprotected background thread.

The complete-source repair remains mandatory when a retained relation's endpoint
disappears from a partial source. Avoiding that rebuild requires a separately
proven completeness/deletion contract. This change reduces the cost of that
rebuild without granting partial evidence deletion authority or bypassing native
rules, freshness, lease, generation or delivery checks. Shared-world preparation,
native writes and waiting for another execution remain separate latency sources.

`test_projection_computation_parity` covers exact persisted digests, local cache
lifetime, bounded vocabulary caches, live policy membership, quality output,
duplicate/missing endpoint validation and overlapping research windows. Existing
fact-slot, ABox lifecycle, preflight and logical-input regressions also apply.
Historical measurements use the same frozen source, runtime context and release
artifact against commit `361ff8206`, with graph caches disabled. They do not
replay current market data into old source clocks or send investment alerts.

The frozen source at `2026-10-24T08:05:20.480934Z` produced 4,887 entities and
9,939 relations. Six fresh-process constructions (three per revision) produced
exactly equal complete graph and persistence payloads. Local median construction
time fell from **24.121s to 19.489s** (19.2%). Six quality calculations per revision
produced exactly equal records, including sample IDs and validation issues;
median calculation fell from **6.398s to 0.197s**. These measurements exclude
native execution, database writes, queue waits and notification transport; they
are component measurements on one captured source, not an end-to-end SLO.

Both revisions also replayed that source through actual TypeDB in separate
temporary databases restored from the same release artifact. Both completed
native inference with aligned generations and the same source manifest, five
executed native rules and three portfolio rule/subject matches. All five subject
input/evidence maps and the complete quality records matched exactly; MarketWorld
and KnowledgeWorld material fingerprints also matched. Complete native match
payloads and both shared-world payloads were exactly equal. This includes shared-world
preparation/serialization and quality calculation, using recording sinks instead
of operational MySQL writes or notifications. Both temporary databases were
deleted. Native runs overlapped regression checks/local workers, so their elapsed
times are audit data, not a controlled latency comparison. This source has no
stock entry/exit match and establishes computational parity, not trading accuracy.

## Share execution for complete sibling source shards (2026-10-24)

The queue and runner can execute complete sibling shards from one verified
monitor event together. Previously, the compatibility key included the exact
symbol list, so two subjects from the same source repeated account preparation,
graph construction and the TypeDB publication pipeline. The second subject also
waited for the entire first execution. This change removes that repeated work
within one execution; it does not cache mutable performance or outcome history
across source snapshots.

Eligibility requires the same parent event, account, immutable snapshot and
source clock, a ready fact-change contract, complete captured source facts, and
matching remaining payload fields. Validation, reconciliation, coalesced,
non-fungible and governance/research requests keep their existing boundary.
Different source facts and per-symbol dependencies/revisions remain in the
combined request. Native symbol limits and the 1,200-fact request limit apply
before jobs are leased and again before execution. Duplicate symbols, shard
indices or fact identities are kept separate; facts are never truncated to make
a batch fit. Unknown payload differences also prevent sharing.

Every source keeps its durable job, lineage, claim token and completion result.
A subject absent from native coverage cannot be completed by another subject's
result. Writer ownership, control/release checks, native generation activation,
per-subject decisions, freshness and notification eligibility stay authoritative.
There is still one TypeDB writer. The result's `execution_batch` records whether
source preparation was shared and how many jobs/subjects were included. This can
increase the first subject's wait within a pair, so acceptance must compare the
whole source-to-completion path as well as individual durations.

Sharding now also updates same-source `verifiedSourceSnapshots` aliases with
the shard's actual source-fact counts. Otherwise durable ingress could restore
the parent's count over the corrected primary boundary. Explicitly incomplete
aliases and other account/source identities are preserved, and an absent legacy
alias is not introduced.

`test_reasoning_sibling_batch` covers exact request lineage and dependencies,
boundary exclusions, fact/symbol limits, single preparation, isolated real-MySQL
claims, untouched overflow jobs, partial subject coverage and writer release.
Historical native replay uses task-owned temporary TypeDB databases and sends no
investment notifications. Runtime observations must distinguish queue removal
from native processing time and from a successful external transport receipt.

A historical same-source pair was replayed into two independent temporary
TypeDB databases, each restored from the same frozen release artifact. Serial
projection took 142.933s in total (122.368s initial construction + 20.565s second
subject); combined projection took 116.940s. Both modes completed native
execution and generation alignment. The same three portfolio rule/subject
matches were preserved, as were ten exact comparisons of the two subjects'
source identities, properties, relation kinds and evidence. This fixture has no
stock entry/exit match; `native_strategy_smoke.py` separately checks positive,
incomplete and stale stock strategy subjects together and individually. The
historical timing includes initial ABox construction but excludes release
seeding, live source/history reads and notification transport. It is one local
replay under concurrent worker load, not a steady-state or tail-latency SLO.

## Reuse transaction preparation and exact source packets (2026-10-24)

The monitor now serializes each final account state once for the current row,
historical row and canonical ingress comparison. Sorted-key normalization remains
mandatory, including corporate-action maps whose insertion order previously
created false deltas. The value is scoped to the current transaction and discarded
after the account's ingress comparison. The already prepared previous-state
projection in the immutable source packet is also reused for scoped input rows.
Current/history/source/input/mailbox writes retain their SQL, order and shared
commit/rollback boundary; mutable outcome and performance history is still read
independently by the reasoning worker.

Each source repository has a cache of serialized immutable packets, bounded to
four entries and 64 MiB of retained string storage. It rechecks the durable row's
existence and fingerprint on every hit. Deleted sources return missing, changed
fingerprints reload payload and fingerprint together, and DB failures do not
grant cached authority. Different repository instances never share this cache.
Each subject decodes its own object, avoiding another full deep copy after JSON
decoding. Rehydrated account metadata is already isolated and needs only a
top-level copy for replay labels. Source timestamps, settings, target selection,
freshness rules and release identities are unchanged; no cross-subject decision
or mutable outcome is cached here.

A frozen operational current/previous pair (15.02 MB current JSON, 10.55 MB source
packet) was compared against `ff4aaac36` four times in alternating order. All SQL
strings/parameters, normalized state and ingress payloads were exactly equal.
Local median preparation, including the unchanged ingress gate, fell from
9.193s to 7.744s. This used a recording DB port and excludes database writes.
A separate read-only real-MySQL comparison of the same immutable packet returned
identical data in four reads: median read/decoding/isolation time was 0.589s before
and 0.175s after. Full-payload reads decreased from four to one, with three fresh
fingerprint checks. These are component measurements on one historical pair,
not full native-inference replay, a trading-accuracy test, or an end-to-end SLO.
The complete rehydrated account snapshot, including replay metadata, also matched
the original reader exactly in a separate real-MySQL read-only comparison.
Native execution and waiting for the previous subject still need their own
operating measurements.

`test_reasoning_source_preparation` covers byte-for-byte persistence/delta parity,
previous/future/missing baselines, caller and repository isolation, source repair,
deletion, malformed data and DB failures, LRU/byte budgets, the actual live reader
constructor, and real MySQL rollback of source plus failed ingress. The existing
replay, writer, projection and runtime-wiring suites remain applicable. Benchmarks
use private temporary captures; no account data or fabricated investment alert is
committed or sent.

## Bounded delivery bursts and optional cache storage (2026-10-24)

The managed delivery/active V2 worker now immediately checks its next turn after
a completed, non-retryable job when the existing `next_lane` query finds work
whose source boundary and availability time permit a claim. At most three
successful turns run consecutively before the normal polling sleep. The writer
is released between every turn; candidate/configured workers, idle/failed turns,
writer backoff and an unavailable queue probe retain normal polling. This removes
the fixed five-second sleep between the first three ready delivery turns without
making candidate writers wait behind an unbounded drain. The advisory probe
cannot claim a job or bypass deployment, lease, source or native execution checks.

Only the managed worker serving the current delivery deployment omits the optional
durable graph-assembly cache port. It does not change projection settings, source
keys, frozen releases, the process-memory cache or authoritative TypeDB writes.
Candidate, explicitly configured/replay and other non-delivery builders retain
the existing durable cache. A cold live process rebuilds its graph from the same
authoritative sources instead of reading a saved assembly copy. The previous
eight completed jobs had no graph-cache hits and spent 0.87–2.83 seconds storing
these optional copies; this is a removable component, not an end-to-end guarantee.

`test_rule_delivery_latency` covers real local writer locks across seven bounded
turns, retirement, due-work/probe failures, retry/backoff isolation, managed role
wiring, exact graph/context equality with and without the optional port, memory
reuse, caller mutation, changed source clocks and cold replay from durable cache.
`test_independent_reasoning_engine`, `test_graph_writer_guard`,
`test_projection_input_ownership` and `test_runtime_composition` retain the wider
execution/replay contracts. No investment alert is fabricated to measure latency.

## Rule delivery preparation and wakeup (2026-10-24)

Six recent delivery jobs showed 0.7–77.4 seconds in the queue, 5.4–17.6
seconds in runtime-context preparation, 5.6–11.7 seconds in ABox persistence
and 3.5–7.6 seconds in native inference. These components overlap; they must
not be added to graph-build totals or presented as end-to-end delivery time.

Shadow performance history now limits the same ordered, account/symbol/cutoff
identities before reading large episode/outcome JSON. A read-only repeatable-read
comparison of 1,102 shadow episodes preserved every returned value and order:
the old query took 1.42/1.46 seconds and the revised query 1.22/1.24 seconds.
No historical observations, qualification criteria or source clocks are omitted.

Pure performance evaluation selects independent observations once per metric
slice. A bounded process-local cache reuses a report only after the full
authoritative episode read produces an identical key, including repairs,
qualification receipts, ordering, account, cutoff-visible contents and the
minimum-sample setting. Archive counts and coverage are still read anew. Cached
reports are copied, never exposed as shared mutable state. A frozen 1,761-episode
replay produced exactly equal reports: original calculation 1.81 seconds,
revised calculation 1.31 seconds, first cached evaluation 1.61 seconds and an
identical-input hit 0.32 seconds. These are local component benchmarks, not a
promise that later market snapshots will hit the cache.

The independent runner retains its existing writer fence when claiming jobs,
then releases the TypeDB writer only for immutable MySQL source assembly.
Delivery retains its existing priority reservation during this read, so candidate
or maintenance work cannot overtake it. A candidate's preparation can yield to
live delivery, and the normal turn exit releases both reservations and ownership.
Before graph preparation it reacquires the writer, verifies the exact durable
claim tokens, rechecks control deployment and execution readiness. Busy writers
defer the original source; lost or unverifiable claims cannot reach inference.
Source-read failures leave the writer released. Graph-dependent runtime context,
effectful outcome observation, ABox publication and native inference remain in
the writer boundary. This does not add parallel native writers or change frozen
release identity. Small scoped ABox patches now use the existing bounded union
delete, reducing round trips inside the same atomic activation transaction.

Direct notification enqueue emits a local wake hint only after its transaction
commits. The existing notification worker checks it at most one second apart,
including hints emitted during delivery. The hint contains no account or message
data and cannot authorize a send. Missing hints fall back to ordinary polling;
transaction-bound producers without a post-commit hook also retain polling.
Deduplication, leases, cadence, send-time freshness and successful-receipt rules
are unchanged. An already busy worker and external transport can still delay
delivery. No synthetic investment alert is sent to measure this change.

Regressions: `test_rule_delivery_latency`, `test_abox_persistence`,
`test_independent_reasoning_engine`, `test_graph_writer_guard` and
`test_rule_notification_repair`. Tests exercise cross-process wake hints,
rollback, real MySQL history selection and loss of writer/claim/control authority.
Steady-state latency still requires observation from source time through the
successful transport receipt; these changes alone do not establish a 60-second
delivery guarantee.

## Indexed outcome recovery reads (2026-10-23)

Recent delivery traces spent a median 12.6 seconds preparing runtime context
and 56.2 seconds in projection/inference. Outcome observation queried pending
targets twice per subject: baseline collection includes future targets, then
due-outcome collection applies the original observation cutoff. Both reads
repeatedly examined completed shadow outcomes to find legacy data gaps.

The existing MySQL schema tuning now adds an indexed, virtual
`recovery_candidate` field to decision and shadow outcomes. It computes the
exact existing data-gap and automatic-retry-stop predicate from the authoritative
JSON; legacy rows and repairs update it atomically. No history backfill, extra
worker, TTL cache or qualification rule is introduced.

The reader selects pending and recoverable target identities in disjoint,
bounded branches before loading their payloads. Recovery starts at the new
outcome index and joins targets by outcome identity. Each branch retains its
earliest N, then the original order and limit choose the final N. The fifteen
minute retry age, `nextRetryAt`, account scope, future-target mode, missing
outcomes and legacy observed gaps retain their previous meaning. Decision
episode orphan exclusion occurs before either branch's limit.

A read-only, repeatable-read comparison at source cutoff
`2026-10-23T06:40:09.796740Z` ran the original and revised readers twice in
alternating order against the same stored data:

| Read | Original | Indexed | Exact returned rows |
| --- | ---: | ---: | ---: |
| Due outcome targets | 2.486–3.393 s | 0.016–0.030 s | 6, identical |
| Baseline collection targets | 2.221–2.229 s | 0.047–0.065 s | 200, identical |

These measurements cover two input queries, not total alert latency. Native
TypeDB persistence/inference, other history reads, queue debt and dispatch
revalidation retain their own costs. The frozen subject/snapshot batch boundary,
investment rules, source freshness and receipt cooldowns are unchanged.

`test_outcome_recovery_reads` exercises both outcome stores on isolated MySQL:
exact pre-change selection parity, bounded interleaving, source cutoffs, retry
backoff/stops, legacy gaps, other accounts, orphan episodes, generated-index
repair/rollback and native index plans. An attempted compact shadow-history
projection was slower in the measured workload and is not part of this change.

## Reuse exact performance observations within one calculation (2026-10-19)

The account performance calculation previously reconstructed the same outcome
observations for both action and insight reports, including claim parsing and
fingerprinting. It now builds those observations once per calculation and
computes each episode's claim identity once before visiting its outcomes.
There is no cross-request cache, changed cutoff, reduced history or altered
qualification policy. Outcome observation still commits before history and
performance reads; those dependent stages are not run concurrently.

A read-only replay of 1,728 frozen operational episodes returned exactly the same
complete report: calculation time was 2.748s before and 1.513s after. This measures
calculation only. Input assembly, TypeDB persistence/execution, queue waiting and
transport latency remain separate operating measurements.

## Current-state queue and conditional history reads (2026-10-19)

Unclaimed, supersedable price and portfolio deltas from a verified complete
monitor packet share one latest-state slot for the same account and symbols.
The existing coalescer retains both events' fact families, dependency keys and
durable source lineage while selecting the newest verified immutable snapshot.
Research, disclosures, non-fungible events, validation pairs and incomplete
source packets keep their existing separate lanes. Processing leases and
completed inputs are never rebound. Backlog migration retains the earliest
queue-entry clock instead of resetting measured waiting time.

The performance history reader obtains the large original episode payload
through a conditional second join only when a compact input is absent, stale,
wrong-version or missing an individual field. The original source and compact
version still share one SQL read view. This avoids off-page audit reads for
complete compact inputs without removing history or changing qualification.

A read-only comparison at `2026-10-19T04:31:21.810005Z` returned exactly the same
659 episodes from one consistent snapshot: the original reader took 2.547s
and the revised reader 0.728s. This is one component measurement, not a claim
that total queue latency meets its SLO. Native execution, persistence and
dispatch expiry continue to be measured and validated separately.

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

## Valuation Audit Churn and Comparison Inputs (2026-10-18)

Two actual immutable NVDA snapshots four minutes apart had identical DCF
values and release decisions after the existing clock/identity normalization.
Their only remaining delta was `modelRelease.audit.auditId`, regenerated as a
`driver-dcf-release-audit:` identity. V7 verified-monitor admission removes
only that recognized identity at that exact nested path from its comparison
projection. The full source and audit remain unchanged. Numerical inputs,
assumptions, approval decisions, release contents and other audit identities
still request native reasoning. This removes redundant work; it is not a new
market-hours gate, a notification quota or a change to investment thresholds.

Independent comparison input V5 additionally requires verified incoming and
persisted semantic ABox manifests. The durable compact projection retains the
original manifests, counts and fingerprints. A matching event/source/temporal
clock cannot stand in for equal assembled facts. Invalid, different or missing
manifests are recorded as input exclusions, never as successful comparisons.
Old V4 receipts remain in history; their missing proofs are not reconstructed
from today's graph or counted toward V5 promotion. Exclusion receipts use their
own contract identity so old incomparable pairs cannot starve later comparisons.
Release fingerprints, validation-start clocks and latency requirements remain
unchanged.

Post-restart native runs retained valid ABox proofs, but two runs for the same
verified subject snapshot used different joint subject batches. Their complete
temporal and ABox footprints were not equal, so V5 rejected the comparison.
Paired validation still needs demonstrably identical assembled inputs; this
revision does not waive that requirement or promote the management candidate.

Decision comparison now includes native management-response codes, objectives,
bound numerical facts, release conditions and adjustment scenarios, while
excluding graph-local decision IDs. Rules used by the frozen judgment also
participate in comparison, including context rules that supplied its conditions.
Differences expose the exact changed decision fields. A declared management
upgrade is visible as a difference and still requires review; it is never
automatically accepted merely because both market actions are `NO_ACTION`.

Operational delivery diagnostics reuse existing status reads, adding no history
scan. CLI and the operations reasoning view distinguish processing delays,
source waiting, candidate rules not yet serving customers, the last scoped
analysis with no alert candidate, and recorded admission outcomes. Unknown
candidate counts remain unknown. Unavailable queue reads are never reported as
an empty queue. Queue health and admission do not prove a
successful customer receipt or an absence of market opportunities.

## Logical input proof and reproducible subject batches (2026-10-18)

A later exact single-subject pair had identical incoming ABox and temporal
inputs. Its persisted link hashes still differed because each isolated database
had independently rebound copy-on-write endpoint generations. V6 comparison
now requires `target-scope-logical-input-v2`: each original logical fragment's
complete `baseFingerprint`, its dependency IDs, and the transitive dependency
closure. Physical storage generations remain untouched and auditable. A changed
value, logical relation or dependency still changes the proof; missing fragments
or legacy receipts cannot establish completeness. Proofs are captured by each
new execution, never backfilled from today's graph.

Queue and runner batch compatibility now include the exact subject bundle.
The same verified snapshot can no longer combine NVDA with different unrelated
subjects depending on which worker reaches the mailbox first. Point-in-time
source and temporal equality checks remain mandatory. New release registration
binds the actual registering runtime revision and comparison contracts while
preserving the authored rules; existing releases and validation clocks are not
rewritten. This is required before independently validating and promoting the
management-rule upgrade.

Three actual immutable PLTR requests carried only `marketObservationFollowup`.
A source replay reproduced a raw-delivery candidate even though its quote was
identical to the pending reasoning anchor. Smart delivery deferred that raw
candidate, but the monitor still scheduled its TypeDB follow-up. Raw delivery
and reasoning now carry separate admission flags; new smart movement transitions
explicitly retain follow-up. Review also found that an inherited cycle-local
candidate list could survive an empty detection pass. Detection now replaces
that list, including its empty state. Durable pending anchors, queued source
events, completion receipts and meaningful new observations remain intact. No
price threshold, news/filing admission or investment rule is changed.

`python_service/service.py reasoning-engine validate-current --symbols NVDA --limit 1`
requests a bounded independent pair from an already durable source snapshot.
Its event and both queue jobs are recorded atomically against the selected
delivery/candidate pair. Repeating the same subject/snapshot/pair is idempotent.
The request is non-coalescible, retains the original observation clock, and uses
normal native execution and exact input proof checks. It does not manufacture
a new quote or bypass a missing source/temporal/ABox dependency.

Comparison-only sources are fenced from both transport and AI even on the
delivery deployment. They cannot advance pending production quote anchors,
including completion-repair fallback reads. The existing queues retain actual
wait/execution times; a requested pair is not a passing comparison. Request one
pair at a time so validation does not flood customer work or obscure queue SLOs.

The isolated real-MySQL load rehearsal also exposed error 1213 during an
outcome save. That complete idempotent transaction now uses the existing
bounded deadlock policy. A real rollback rehearsal injects the failure after
the outcome insert, proves the retry leaves exactly one observation, and keeps
the original observation clock. Other errors and ambiguous commit outcomes
are not retried. Historical execution failures are retained; a genuinely new
runtime release receives its own immutable validation cohort.

The first rollout exposed a separate startup defect when a new deployment reused
an already verified static graph. Its authored manifest matched, so restoration
was correctly skipped, but startup then compared the native rule hash with an
empty expected fingerprint. Initial binding now captures the existing executable
readback, and the unchanged immutable preflight verifies it along with the TBox.
Missing or empty readbacks still fail closed; active and already-frozen releases
keep their recorded expected hashes. Reuse does not rewrite the authored artifact
or reseed the database.

The candidate control pointer and the explicitly configured candidate worker are
separate controls. After registration, bind `reasoningEngineV2DeploymentId`,
`reasoningEngineCandidateDeploymentId` and `reasoningEngineCandidateReleaseId`
to that candidate through the runtime settings store and reload the managed
supervisor. Check the worker's actual frozen release and warmed status before
requesting comparisons. A registered deployment alone does not prove that a
worker is consuming its queue.

V4 comparison keeps raw management differences and records a separate
`management-rule-migration-review-v1` receipt. It requires a content-addressed
declaration in the candidate artifact naming the exact baseline artifact,
original/updated rule hashes and expected response codes. Only additive
`management_response` fields in non-predictive guardrail rules qualify. Existing
conditions, action envelopes, derivations, claim meaning and every other rule
must remain unchanged; legacy non-predictive observation defaults are normalized
explicitly. Native response objectives, observed/target values and release
conditions must bind to the frozen authored contract and its numerical facts.

Review rechecks captured V6 inputs and compares all non-management decisions
again. Missing input, delivery leakage, changed market judgments or unverified
bindings remain blocking. The original comparison status and differences are
retained; readiness counts only fingerprint-bound explained policy differences
separately. Altered receipts and duplicate execution representations cannot use
one review to clear another raw failure. Matching `NO_ACTION` labels alone never
qualify a migration, and historical failures or validation clocks are unchanged.

The first r5 paired run exposed a remaining causal-input mismatch. Its complete
incoming and temporal manifests were identical, but native persisted macro,
portfolio and subject exposure fragments still differed. Narrow event routing
had correctly retained each database's prior shared context; matching incoming
facts alone therefore could not establish equivalent native inputs. The actual
comparison was recorded as ineligible and is retained.

Paired validation now declares `paired-complete-dependencies-v1` in its immutable
source purpose. A verified complete snapshot prepares full factual dependencies
before the normal target/shared manifest merge. It does not claim the narrow
field ownership of a new market event. Decision memory, statistical computation
and native rule targets remain restricted to the requested subject. Regular
events keep their existing field ownership and partial-input path. Durable event
compaction and request/audit context retain this purpose; mixed/ordinary or
unverified requests cannot enable it. Captured incoming **and actual persisted**
V6 scope proofs remain mandatory. This is a new executable input contract, not a
backfill of missing proofs, a rewritten old receipt or a reset validation clock.

The first complete-dependency native pair had equal incoming, temporal and
persisted inputs. Its migration review then exposed an encoding mismatch:
the immutable registry hashes canonical ASCII-escaped JSON, whereas the reviewer
had hashed unescaped Korean labels. Artifact verification now uses the registry's
existing public fingerprint contract. Raw rule and comparison fingerprints keep
their own contracts. Unicode fixtures reject both the wrong encoding contract
and modified frozen content. Historical rejected receipts remain unchanged;
the corrected executable receives a new release and fresh independent evidence.

The complete raw source still has single-subject computed evidence. Two actual
consecutive subjects fell back to a whole-manifest rewrite because unrelated
computed scopes were absent. Paired validation now retains the ordinary
incremental manifest purpose: omission is not a deletion event. Supplied shared
and target dependencies are refreshed from the complete snapshot, while unrelated
computed results retain their verified generations. Explicit full reconciliation
and ordinary source-deletion contracts remain unchanged. Regression coverage
reproduces the removed shared-link fallback, preserves the unrelated model and
quote, and verifies refreshed account, macro and requested-price fragments.

### Portfolio cutoff and complete subject omission (2026-10-20)

The composition candidate comparison exposed two further input differences:
portfolio risk rows were selected at each worker's execution time, and the
active graph retained six old subject evidence/valuation scopes absent from the
candidate. Matching market snapshots and temporal windows did not prove matching
native inputs. The failed paired receipts remain in the audit.

`paired-frozen-portfolio-dependencies-v2` captures the portfolio lifecycle at
request time with the exact market cutoff. Portfolio reads bound both observation
and last-write clocks: a mutable current row updated later is omitted because
the store has no historical revision of its previous contents. The secret-free
compressed receipt binds account, source snapshot, cutoff and content hash.
Durable ingress, batching, request compaction and graph assembly preserve and
verify it. Different receipts cannot share a batch, and missing or altered
receipts fail before graph writes. Repeated requests with identical contents
remain idempotent; a changed portfolio receipt is a different input. Independent
time-series reads and model execution are retained.

A verified paired source now declares `complete-target-snapshot`. The complete
raw source and the requested subject's computed evidence give absent **target**
scopes deletion meaning. This is not authority to delete omitted shared or
unrelated subject computations. The existing dependency checks block retirement
while retained references remain; partial input cannot claim this mode. Ordinary
incremental market events retain their existing omission semantics. Incoming and
persisted logical scope fingerprints must still match across deployments.
Regression coverage: `test_paired_portfolio_input`, `test_reasoning_logical_inputs`
and `test_projection_input_preflight`, including real isolated MySQL cutoff reads.
