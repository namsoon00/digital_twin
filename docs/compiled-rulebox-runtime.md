# Compiled RuleBox runtime

Owner-authorized change, 2026-11-13. TypeDB remains the governed authoring and
research store. The `compiled-rulebox-v1` backend executes immutable approved
RuleBox artifacts directly over the account's frozen source facts. AI is not
called. A TypeDB query, ABox write, graph writer lease, or TypeDB capacity wait
is not part of this backend's realtime execution path.

The flow is:

1. Collect and persist a source snapshot and its source-event lineage.
2. Assemble only the requested symbols' facts, model evidence and account context.
3. Execute every enabled rule in the selected immutable release, including
   negative conditions, independent N-of-M evidence groups, account thresholds,
   model evidence joins and authored management-release objectives.
4. Persist the exact input graph, execution coverage, source fingerprint and
   InferenceBox in the projection audit. A failed commit cannot authorize delivery.
5. Build the existing multi-rule synthesis and subject case. Existing evidence
   freshness, account isolation, delivery ownership, receipts and duplicate
   controls continue to govern notifications.

Native TypeDB completion flags remain false for compiled results. Separate
`compiledExecutionProof` and `ruleExecutionBackend` fields identify and validate
the new result. Unsupported rules fail admission rather than being skipped.
Execution budget exhaustion fails the whole projection, not just the slow rule.

## Approved RuleBox changes

Application RuleBox saves and version restores run compiler preflight before
writing authoring data. In compiled mode they then register a new immutable
release, warm its model/TBox contracts, replay preserved account inputs without
notification delivery, and atomically switch the active and delivery pointers.
Workers notice the pointer change and reload the artifact. An in-flight old
worker cannot acquire delivery authority for the successor.

Rule additions, removals, thresholds, conditions, response text and permitted
combinations are data in the artifact. They do not require an application edit.
Dependent derived relations execute in topological order. Recursive dependencies,
unknown condition/operator fields and unsupported semantics are rejected with a
reason. A new source measurement or model implementation still requires a
registered producer; changing a RuleBox cannot manufacture missing data.

The UI distinguishes authoring saved from runtime applied. Failed validation
keeps the previous runtime active and reports `saved-runtime-not-applied` with
`runtimeRelease` details. A concurrent candidate from another development session
blocks replacement. Failed candidates are retired and their candidate slot is
released without changing the delivery owner, so the next correction can be saved.
Direct database edits that bypass the governed save/restore
API do not automatically create a runtime release.

Admission checks every account and symbol and can take minutes on this machine;
the save screen keeps the operation visible instead of abandoning the response
at the general 30-second API timeout. This is release validation time, separate
from realtime symbol processing. Managed maintenance does not order compiled
releases by legacy numeric suffixes. TypeDB rotation preserves the artifact's
authoring database and never treats its compiled-program binding as a database.

## Initial migration and validation

Run the pure/compiler, projection/delivery and release-isolation tests:

```bash
npm run test:focus -- test_compiled_rulebox.py test_compiled_rulebox_projection.py test_compiled_rulebox_release.py
```

The opt-in `python_service/tests/native_compiled_rulebox_smoke.py` compares the
entire enabled catalog using identical frozen facts in a uniquely named temporary
TypeDB. It uses the native row mapper and TypeQL compiler, removes its own test
DB, and has no notification transport. Wide native OR queries use individually
queried native predicates and an independent evidence-group composition. The
oracle explicitly restores frozen attributes omitted by the legacy physical row
mapper; this is predicate equivalence on complete source facts, not a claim that
legacy storage omissions are preserved. An absent entire alternative group is
now a verified non-match in both compilers. Its input artifacts contain private data
and must remain outside git.

Initial activation requires its exact-program comparison report and a separate
successful end-to-end input/projection/synthesis replay:

```bash
python3 python_service/service.py reasoning-engine compiled-cutover \
  --deployment-id <new-immutable-deployment> \
  --native-parity-file <local-comparison-report.json>
```

The control switch and retirement of the previous execution owner are one MySQL
transaction. The authoring TypeDB database setting is retained. There is no
runtime fallback to native queries on an interpreter failure. Rollback must
select a retained immutable artifact as a new validated release; never rewrite
an active program or reuse its proof across releases.

These checks establish execution and evidence integrity. They do not establish
profitable trade timing or predictive accuracy. Measure source collection,
queue wait, input assembly, rule evaluation, durable audit and delivery separately;
fast rule execution alone is not proof that total alert latency has been removed.

## Realtime work on the existing machine

Compiled projections prepare their source context once during assembly. They do
not run the native writer's earlier prewarm, or hash/deep-copy the complete source
through native runtime/graph caches. Revision-checked input preparation inside
the owning modules still applies. Freshness checks and the enabled RuleBox
coverage are unchanged.

The realtime cooldown read supplies the longest applicable notification interval
as an inclusive delivery-receipt cutoff. SQL excludes older receipts before
aggregating large proof payloads. Enqueue time, failed attempts and other accounts
cannot substitute for the successful receipt clock. Historical callers without a
cutoff retain their full-history contract; final dispatch checks are unchanged.

The compiled runner can combine up to eight compatible source jobs by default
(`reasoningEngineCompiledBatchSize`, bounded to 1–16). Existing account, source,
lane, ordering and payload-budget compatibility checks still govern every batch;
this does not drop old transitions or limit the enabled rules. Native releases
retain their smaller writer limits.

The exact source graph, complete execution proof, rule evaluations and a detail
outbox entry commit in one MySQL transaction. There is no intermediate native
`projecting` commit. A failed transaction cannot make the result delivery-eligible.
The existing inference-detail worker later normalizes the diagnostic stage/rule
tables from that immutable receipt, one bounded job per turn, while the live
reasoning queue is idle. It neither queries TypeDB nor reruns rules or delivery.
Source audit retention protects pending and failed detail jobs. After eight
failed attempts a job remains visible as failed; use
`ontology-inference-detail retry-failed` after correcting the cause. The original
evidence is available before normalized detail tables finish.

Managed research admission also checks a host-local activity lock held during
monitor collection and live inference. New research turns wait until a 20-second
quiet interval, and pause at warning or critical memory pressure with a 30-second
recovery interval. An already-running turn finishes normally. This is cooperative
admission on the same PC, not physical isolation or a guarantee against resource
contention from other applications. News collection and maintenance retain their
own existing scheduling policies. AI remains controlled by its separate off and
token-budget settings.

Validation includes atomic-write rollback, restart recovery, retention protection,
account/source isolation, compatible batching and notification deduplication.
An in-memory replay of preserved inputs proves result parity and isolates input
assembly/rule work; it does not include collection, SQL durability, queue wait or
notification transport. Live source-to-completion and delivery timings must be
reported separately before claiming an end-to-end latency target.
# Snapshot storage and maintenance admission (2026-11-13)

The realtime producer retains one immutable source packet per account/source
clock. While the durable active engine is V2 it no longer refreshes the legacy
mutable per-symbol input cache; existing entries for that account are removed
in the same transaction. V1 activation restores ordinary cache writes, and its
read adapter can recover from the current monitor row during the transition.

Large company/research evidence values use the self-contained
`__orbit_snapshot_zlib_v1__` storage envelope. Account, position, quote, symbol
paths and source clocks remain directly queryable. The snapshot readers and
as-of company evidence reader restore the original values and verify byte count
and SHA-256 before any domain use or source fingerprint check. Old plain rows
remain readable. Corrupt encoded evidence fails the read. Archive copies remain
self-contained, with no external blob retention dependency. This changes storage
representation only, not RuleBox identity, claim semantics or delivery gates.

An overdue cleanup turn admits one batch to preserve cleanup progress, then
yields its host workload lease between committed retention
policies and repair phases when a monitor is waiting. It reacquires the lease
before resuming. It never releases a live SQL transaction, cancels another
process, or drops pending work. An individual slow SQL statement can still delay
the next boundary; this is not a hard deadline guarantee. `guardWaitMs` now
separates that wait from collection and commit time in monitor timing logs.

Historical multiple enrichment declares the five payload fields it consumes.
Cold reads select those JSON fields and exact original revision metadata in the
existing bounded read transaction. Missing paths and explicit JSON null remain
distinct. A disposable local field cache (4 MiB per namespace, at most five
files) survives worker restarts; it is keyed by database/principal, field
contract, revision ID, fetched clock and payload hash. Every call re-reads the
retained IDs and hashes from MySQL before using cached fields. Deleted/corrected
revisions cannot acquire authority from the cache. A corrupt/unavailable cache
requires another source read; DB failures still propagate. No extra database
instance or physical split is introduced. Original provider payloads and
historical source references remain unchanged.

Capacity admission no longer recursively measures `history-archive` inside the
monitor/cleanup exclusion lock. The live process was spending roughly two
minutes traversing archive chunk files at each preflight/refresh. Fresh free
space and existing MySQL/TypeDB capacity checks remain; archive size is explicitly
unmeasured in this fast observation, never reported as zero. The independent
filesystem observer retains detailed archive measurements, and explicit
diagnostics can request `include_archive_size=True` outside the live guard.

# Independent quote source boundary (2026-11-14)

`marketSourceRefreshEnabled=1` connects the compiled delivery worker to the
existing durable Toss account quote cache and validated KIS market cache. Before
each reasoning turn it can prepare one account from the latest collected quotes,
without calling the account provider or external enrichment. Checks are spaced
by at least two seconds and accounts rotate fairly. Intermediate ticks may be
coalesced into the latest observation; this is not a tick-by-tick trading feed.

The source producer preserves holdings, cost, cash, broker valuations and their
original account clock. Quotes, indicators, execution, orderbook and investor
flow retain separate source clocks. Missing, older or future clocks cannot
replace a retained observation. Fresh market prices update marked valuations
without changing the configured portfolio valuation basis. Complete holdings and
cash must have been reconciled within twice the existing account interval
(bounded to 60–3600 seconds); repeated quote updates never renew that clock.
Fundamental and research evidence retain their original validity and provenance.

The existing atomic monitoring transaction writes the immutable source packet,
time series, domain event and V2 ingress. It locks and compares the prepared
account generation and checks delivery ownership again. A concurrent account or
release change rolls the source transaction back for a fresh read. No duplicate
raw-market inference queue or AI invocation is introduced. The original RuleBox
material-change, freshness, publication and receipt gates still apply. Quote
updates do not guarantee a customer notification.

The account monitor still reconciles holdings and long-lived context. A late
account collection merges newer committed quotes before its source write and
rebuilds affected price candidates. Collection and source preparation may overlap
under shared host workload leases; SQL account locks serialize their commits.
Cleanup retains an exclusive lease and admission priority. A busy cleanup turn
defers source preparation without blocking already committed reasoning jobs.

`snapshotEvidenceReferencesEnabled=1` stores unchanged evidence values of at
least 4 KiB once by SHA-256 in `data/snapshot-evidence`. Atomic file publication,
file/directory fsync, length and checksum verification precede the referencing
SQL write. Current state, historical state and immutable reasoning inputs share
these exact values. Every reader expands them before hashing or domain use;
quote/account SQL paths stay directly queryable. Old plain and inline-compressed
rows remain readable. Missing/corrupt evidence fails closed. A rolled-back SQL
write may leave a harmless unreferenced blob; automatic blob deletion is disabled.

**Backup and restore must include `data/snapshot-evidence`, MySQL and the history
archive together.** Referenced rows are no longer self-contained. Do not delete
this directory when disabling new reference writes; retained rows and archived
source packets still depend on it. This directory is private durable data, never
a disposable cache or git artifact.

On a preserved input, decoding and all 128 RuleBox evaluations (4 matched rules)
were identical with inline and referenced evidence. The serialized source fell
from 3,302,479 to 601,518 bytes; this is a transfer/storage representation measure,
not an end-to-end latency claim. `market-source-refresh` logs report independent
source commit duration and the retained account basis. Live quote-to-inference
and notification transport times must be checked separately.

Post-cutover profiling found repeated normalization of unchanged company reports
and repeated news alias compilation inside source commits. Company construction
and merging now reuse exact JSON inputs per company, with respectively 8 MiB
and 16 MiB of serialized results, at most 128 input keys each per process.
Identical result bytes share one allocation. This avoids caching duplicate
account-wide bundles that evict each other on a small machine. It preserves
input ordering and source clocks; non-JSON values bypass reuse. Returned objects
are independently decoded, so a consumer cannot mutate another input. No
freshness, materiality, RuleBox or delivery decisions are cached. Alias-pattern
reuse is bounded to 1,024 immutable compiled patterns. A process restart simply
recomputes these derived values. Frozen source metadata also carries the account
reconciliation clock and the independent market-source boundary explicitly.

A paired replay of the same live input in an isolated, rolled-back database
transaction took 9.659 seconds without reuse, 7.158 seconds cold and 5.356
seconds warm. All three source fingerprints matched. A separate frozen replay
also preserved all 128 rule evaluations, four matches and the material
fingerprint. These are preparation/write measurements, not customer delivery
latency; provider polling and the subsequent inference/transport remain separate.
