# Ontology Reasoning Bottleneck Proof

Use the proof command before changing TypeDB timeouts, parallelism, rule count,
or retention policy:

```bash
npm run python:ontology-reasoning:profile -- --symbols 005930 --repeats 2
```

The command combines two independent evidence sources:

1. Recent completed production projections from MySQL, including top-level
   ABox/native timings, native sub-stage timings, and per-rule query traces.
2. A live read-only replay against the currently active RuleBox and ABox.
   It executes direct TypeQL reads, matched-evidence reads, and in-memory
   InferenceBox graph construction. It does not sync functions, write an ABox
   or InferenceBox, activate a generation, or run retention.

Each replay reads the active ABox identity before and after execution and
verifies the RuleBox hash again after the final sample. A sample is rejected
when the RuleBox, Manifest, active pointer, material fingerprint, or scoped
generation map changes, or when the core native evaluation is incomplete. Two
valid samples from the same generation are required before a read boundary can
be reported as `confirmed`. The native TypeDB read
boundary combines rule evaluation and matched-evidence graph hydration because
they are consecutive, non-overlapping reads against the same active ABox.
`productionDominantReadSubstage` still identifies which part of that boundary
is slower.

Verdicts:

- `confirmed`: production telemetry and the unchanged-generation replay identify
  the same dominant read boundary.
- `supported`: production telemetry points to a stage, but a no-write replay
  cannot independently reproduce it or the replay sample is insufficient.
- `inconclusive`: the audit history is absent, a replay failed, or the active
  ABox changed while measuring.

`inferencebox-write-dominant` and `abox-persistence-dominant` remain `supported`
because the diagnostic never performs a synthetic operational write. A write
benchmark requires an explicitly isolated capacity test and is outside this
command's contract.

Useful options:

```bash
npm run python:ontology-reasoning:profile -- \
  --account-id main \
  --world-id portfolio:local:main \
  --symbols 005930,035420 \
  --production-runs 10 \
  --repeats 2 \
  --rule-id graph.example.v1
```

The JSON result always declares `readOnly`, `mutatedOperationalState`,
`writeMethodsInvoked`, excluded operations, generation fingerprints, fixed
proof thresholds, production slow rules, and replay slow rules. Treat a
`supported` or `inconclusive` result as a reason to gather more evidence, not
as permission to tune a different subsystem.

The default output keeps the complete verdict and the eight slowest rules per
sample while omitting bulky raw generation and query diagnostics. Add `--full`
when those diagnostics are needed.

## Root-Cause Decision Order

Do not treat a long end-to-end duration as proof that TypeQL rule evaluation is
the bottleneck. Diagnose the stages in this order:

1. Compare the source-event arrival interval with completed-run service time.
   The worker cannot drain when service time is equal to or longer than the
   arrival interval, regardless of queue coalescing.
2. Compare `aboxPersistenceMs`, the native read boundary, InferenceBox writes,
   and candidate construction. Tune only the dominant measured boundary.
3. Replay the same active generation read-only. A fast unchanged-generation
   replay next to a slow production run identifies projection/write
   amplification rather than an intrinsically slow rule set.
4. Inspect `currentStateDeltaPlan`. Repeated runs should reuse most unchanged
   nodes and relations. `reused=0` on a polling-only update is a correctness
   defect in the materiality boundary, not a capacity problem.

The 2026-08-31 production proof for `000660` measured 108,298 ms at the
projection boundary: ABox persistence used 48,266 ms and native inference used
42,073 ms. Two read-only replays of the unchanged generation completed in
3,347 ms and 2,398 ms. This establishes ABox write amplification as the first
boundary to remove. Increasing timeout, worker count, or rule parallelism does
not address that cause.

## Fundamental Latency Boundary

TypeDB is the semantic current-state and inference authority. It must not be a
high-frequency copy of every polling lifecycle update.

- QuestDB/MySQL retain raw observations, timestamps, and replay provenance.
- The ABox current-state slots retain rule-visible business values and
  categorical freshness/data states.
- Polling timestamps, session clocks, and provider fetch times do not change an
  ABox row content fingerprint by themselves.
- A changed node invalidates only relations adjacent to that node. Unrelated
  relations in the same scope remain reusable.
- Legacy rows without a semantic fingerprint are rewritten once, then become
  reusable. Inventory keeps bounded identity and fingerprint reads separate;
  TypeQL optional joins are not used here because their large-slot query plan
  regressed under the production graph cardinality.

This is the first safe implementation step because it changes persistence
materiality without moving investment judgement out of TypeDB. Exact source
provenance remains in the durable source snapshot and projection audit.

The first measured optimization kept the same authority boundary while
removing redundant database work inside one writer lease:

- Current-state inventory reads use a configurable batch size, defaulting to
  32 physical slots. Production verification rejected a 128-slot batch because
  the expanded TypeQL disjunction caused a transaction-close regression.
  Legacy rows without fingerprints still take the full, fail-closed inventory
  path and are rewritten once.
- After a delta write, verification reads only the exact inserted storage IDs.
  The verified pre-write inventory, explicit delete set, and exact inserted
  rows reconstruct the expected post-write inventory without rescanning every
  active slot.
- Native TypeQL still decides which rules match. Once those matches are known,
  the just-persisted projection may replace the second matched-evidence TypeDB
  read only when the active manifest proves that every expected source and
  relation storage ID exists in that projection. A missing ID fails closed to
  the durable TypeDB read.

The optimization therefore removes duplicate transport and hydration work; it
does not evaluate a rule, infer an action, or replace TypeDB as the semantic
authority. Production telemetry exposes `currentStatePostWriteVerificationMs`,
`matchedGraphSource`, `matchedGraphReuseStatus`, and
`matchedGraphReuseReason` so a real event can prove whether each fast path was
used.

### Stable fact ownership invariant

A logical ABox fact must have the same scope owner in a complete portfolio
graph and in a one-symbol graph. Scope ownership must never be inferred from
the set of neighbouring nodes because that set changes with projection shape.

- Symbol facts use a bounded symbol/family scope.
- Account activities, action candidates, risk snapshots, exposures, and
  rebalance facts use deterministic per-item account scopes.
- Shared factors and catalog facts use deterministic per-item reference
  scopes instead of one mutable `reference:global` aggregate.
- A selected semantic fact is a relation-rebind root. An endpoint scope added
  only to preserve physical integrity is not a new root and must not pull in
  every other relation owned by that endpoint.
- Every changed root rebinds all incident relation generations transitively.
  Before TypeDB inserts a relation, the exact physical storage IDs for both
  endpoints are verified. Missing endpoints fail the candidate write and the
  diagnostic records the logical node, relation type, relation scope, and
  generation.

This prevents a target update such as MSTR market data from moving a shared
factor between scopes or replacing a large mixed reference bucket. It also
keeps copy-on-write work proportional to the changed facts and their incident
relations.

Production evidence then showed that the dual-slot delta path still spent most
of its ABox time reading the inactive slot and deleting its previous rows. It
also reused almost no relations, so the comparison itself cost more than the
write it was intended to avoid. Current-state persistence therefore uses the
`current-state-copy-on-write-v2` physical mode with the
`current-abox-copy-on-write-v4` lifecycle contract:

- a changed entity scope invalidates every link scope that names it in
  `dependencyScopeIds`, including transitive link dependencies;
- the dependent links receive fresh physical generations, so their TypeDB
  endpoints always reference the active entity generation rather than a
  retired copy-on-write row;
- each freshly written physical generation belongs to the candidate Manifest,
  while reused generations retain the Manifest that first stored them, so
  candidate verification includes every rewritten relation scope;
- unrelated scopes keep their existing physical generations.

- A changed scope receives a retry-stable fresh physical generation derived
  from world, scope, logical generation, and durable projection run.
- The live path never inventories or deletes the retired physical generation.
  It writes the complete changed scope with semantic fingerprints and verifies
  exact Manifest/scope/generation entity and relation counts.
- Only a fully verified candidate can move the active Manifest pointer. A
  failed or interrupted candidate remains unreachable and is safe to retry.
- Unchanged scopes retain their active physical generation. Retired and failed
  generations are reclaimed later by the bounded ABox maintenance turn owned
  by the same single TypeDB writer.
- `currentStateWriteStrategy=copy-on-write-fresh-generation-v4`,
  `currentStateInventoryReadMs=0`, and `currentStateDeleteMs=0` are the required
  production proof. The legacy dual-slot path remains readable only for
  progressive migration.

This is copy-on-write at the semantic scope boundary, not an unbounded history
policy. MySQL owns historical replay; TypeDB retains the active generation and
one rollback Manifest while maintenance drains older physical generations.

Live-account verification on 2026-09-01 KST recorded the former dual-slot
critical path at about 92 seconds for ABox persistence. Copy-on-write target
patches subsequently completed ABox persistence in 8.4 to 16.0 seconds; a
two-symbol target patch completed in 10.2 seconds with 0 ms inventory read,
0 ms retired-generation delete, and 0 delete queries. The same durable audit
record preserved `copy-on-write-fresh-generation-v4` as a categorical runtime
mode and completed native TypeDB inference in 8.0 seconds. These values are
operational evidence from the live local account, not a fixed latency promise.

The completed architecture should add a governed semantic-transition head in
front of projection. Raw value changes become events such as loss-band change,
moving-average crossing, flow-regime change, freshness-state change, new
article identity, or valuation-band change. Only those transitions enqueue an
affected TypeDB fact slice. A per-evaluation receipt may advance provenance
without duplicating an unchanged semantic result slot.

The transition gate must be fail-open until replay proves equivalence: an
unknown dependency, changed RuleBox/TBox release, missing prior head, or data
quality downgrade must execute TypeDB. It must never compute buy, sell, hold,
or reduce actions in Python.

## Acceptance Targets

- Steady-state worker utilization below 0.7 and processing capacity at least
  twice the normal source-event arrival rate.
- Polling-only repeats reuse unchanged rows and do not rewrite unrelated
  relations.
- ABox persistence p95 no longer dominates end-to-end p95.
- The same point-in-time source snapshot produces the same TypeDB inference and
  action envelope before and after transition gating.
- Unknown or changed semantic dependencies fail open to full TypeDB execution.

Adding more reasoning workers is not an acceptance strategy. The active graph
uses a single-world writer contract, so extra workers can increase transaction
contention while preserving the same write amplification.

## Storage-Recovery Closed Loop

The same 2026-08-31 incident exposed a second latency loop. Polling write
amplification grew the TypeDB physical store to 24,566 MB and its WAL to about
11,210 MB. At 75% of the configured 32 GB safety limit, ordinary graph writes
correctly stopped. The queued reasoning work then appeared as an inference
latency problem even though the immediate blocker was storage admission.

The original automatic rotation ran synchronously inside the service
supervisor. Candidate schema seeding took longer than the watchdog heartbeat
window, so the watchdog replaced the healthy-but-blocked supervisor. That left
the candidate server and two-hour maintenance fence orphaned. The resulting
loop was:

`unchanged writes -> WAL growth -> safety fence -> synchronous rotation -> supervisor replacement -> orphan fence -> queue growth`

The recovery contract is now:

- Dispatch blue-green rotation to a dedicated process. The supervisor keeps
  emitting heartbeats and monitoring all serving workers while the candidate
  is built.
- Track the maintenance worker PID separately from the fencing token. A
  dispatched or running rotation without a live owner is interrupted and its
  isolated candidate is removed after a 60-second startup grace period.
- Start candidate construction at the proactive 65% threshold. The 75% value
  remains the independent write-safety fence, so normal operation has time to
  prepare a replacement before writes must stop.
- Keep MySQL as the durable recovery source. TypeDB remains the semantic
  current-state and inference authority; a rotation does not move investment
  decisions into the supervisor or Python transition detector.

Never raise the safety limit or disable the storage guard to clear this state.
That only delays the same failure and risks an unrecoverable disk-full outage.

The supervisor also probes authenticated TypeDB service health after startup.
A matching PID is not sufficient: after two consecutive failed service probes,
the managed process is restarted without deleting its data directory. Startup
recovery remains exempt until the server has first finalized, so a legitimate
WAL replay is not mistaken for a runtime outage.

## Deployment-Binding Recovery Invariant

A physically healthy replacement store is not sufficient. Every deployment
selected by the durable reasoning control plane must find its immutable graph
database after cutover. The 2026-08-31 recovery initially rebuilt only
`typedbDatabase` because compatibility seeding was disabled, while the active
`ontology-v2-production-r88` deployment was bound to a different database.
The server was ready but the delivery worker correctly deferred every job
because its frozen release graph was absent.

Storage rotation now treats the MySQL reasoning deployment registry as the
authoritative inventory:

- Resolve active, delivery, and candidate deployment IDs immediately before
  candidate construction.
- Add each selected deployment's `graphStoreBinding` to the candidate even
  when optional legacy/shadow compatibility seeding is disabled.
- Fail closed when a selected deployment or binding cannot be resolved.
- Record protected, validated, and missing database lists in the rotation
  receipt.
- Refuse cutover if any protected database did not pass seed, frozen-release,
  native-inference, world-rebuild, and driver-readiness validation.

Runtime settings remain a bootstrap fallback, but they cannot remove a graph
selected by the durable deployment registry. This separates two independent
health checks: TypeDB process/storage readiness and reasoning-release
readiness. Both must pass before queued investment reasoning can resume.

## Immutable Release Reconstruction Invariant

Database inventory alone does not make an old reasoning release
reconstructable. A second 2026-08-31 rotation proved this boundary: the
candidate correctly included the database bound to
`ontology-v2-production-r88`, but the only available seed input was the newer
source TBox/RuleBox. The candidate fingerprints differed from the frozen r88
fingerprints, so cutover correctly failed. Replacing the old graph with the
current catalog would have changed investment meaning during storage
maintenance.

The durable release contract is therefore:

- Registering a V2 release stores one immutable, content-addressed seed
  artifact in MySQL before the candidate control pointer is changed.
- The artifact contains its release bundle, semantic-storage contract, exact
  TBox metadata, executable RuleBox rows, language-governance nodes, and the
  complete static relation graph.
- A duplicate save is accepted only when the full artifact fingerprint is
  identical. A changed payload under the same deployment ID is rejected.
- Artifact reads recompute the content fingerprint. Corrupt, missing, or
  release-bundle-mismatched artifacts fail closed.
- Blue-green rotation uses the artifact path for every protected deployment
  database and never runs the current-source seed there. Non-protected
  bootstrap databases may still use the normal current-source seed.
- Candidate readback must match both the frozen RuleBox and TBox fingerprints
  before world replay, native inference validation, or cutover.

RuleBox identity has two deliberately separate hashes:

- The **authored artifact fingerprint** identifies the exact governed rows and
  static graph saved at release registration. Restore compares this value with
  the TypeDB static seed manifest.
- The **runtime RuleBox fingerprint** identifies the normalized executable rows
  read back from TypeDB. Release identity, comparison cohorts, and execution
  receipts use this value.

TypeDB normalization can make these hashes differ without changing meaning.
Comparing the authored hash directly with the runtime hash incorrectly marks a
valid restored release as corrupt. Both must be non-empty and independently
verified at their own boundary.

The same incident exposed a second permanent-startup loop: 11 governed
statistical rules were intentionally disabled while waiting for a promoted
model scorer, but the release repair predicate treated them as executable
rules requiring migration. The migration path correctly left them unchanged,
so every candidate restart repeated the same rejection. Executable readiness
now ignores explicitly disabled audit/future-activation rules while retaining
them in the immutable artifact.

After these contracts were corrected, a real `ontology-v2-production-r89`
candidate processed one current market-data job in 43,049 ms. The projection
used 41,661 ms, including 22,198 ms for ABox persistence and 7,004 ms for native
TypeDB inference. This is below the 120-second source polling interval and
confirms that the serving-capacity fix works independently of release recovery.

This removes a control-plane feedback loop as well as a latency source. A
missing old artifact is detected immediately instead of spending many minutes
compiling a candidate that can never satisfy the release contract. Existing
legacy releases remain servable but are not silently made reconstructable from
new source; recovery requires registering and validating a new release with a
complete artifact.

## Ingress Repair Backlog Invariant

The remaining queue delay was not TypeDB compute time. V2 queue compaction
merges multiple latest-state events into one survivor and stores every original
event identity in `reasoning_engine_job_sources`. The recovery query checked
only the survivor's mutable `reasoning_engine_jobs.source_event_id`. Once a
newer event replaced that field, an already represented predecessor looked
unmaterialized and was inserted again on every worker turn.

A second amplification occurred on every new release. The generic six-hour
repair lookback predates the release, while release registration already
creates a current-state ABox bootstrap. The candidate therefore replayed old
polling observations before it could validate current traffic.

The permanent ingress boundary is now:

- A source event is materialized when either the primary job row or its durable
  source-lineage row represents that deployment/event pair.
- Recovery uses indexed `NOT EXISTS` checks against both tables.
- A deployment's live repair lower bound is the later of the bounded generic
  lookback and the immutable deployment `createdAt`.
- Events before release creation are handled by current-state bootstrap or the
  explicit historical replay pipeline, never by live repair.

On the affected r90 data, the legacy primary-row-only query reported 34 recent
events as missing even though 66 recent source identities were already present
in the lineage ledger. This false-negative boundary continuously regenerated
work and explains why query tuning alone could not drain the queue.

Candidate promotion must also distinguish historical recovery delay from the
live queue. A continuously collected market feed may never reach exactly zero
pending rows even after recovery. The explicit recovery override therefore
accepts an old queue-wait P95 only when the oldest currently pending row is
inside the configured queue-wait SLO; a currently stale row still blocks
promotion. Historical delay remains a visible warning and is never rewritten.

## Cold Schema Bootstrap Invariant

After ingress replay was bounded, a new isolated release still appeared to
stall before inference. The TypeDB adapter initially defined 64 schema
definitions as the cold-bootstrap batch, but runtime settings and both
service-manager fallbacks supplied 512. One oversized schema transaction could
therefore hold a socket for the full 900-second operation timeout. Restarting
the worker did not recover: fresh-candidate mode always planned from an empty
schema and attempted to redefine batches that the interrupted request had
already persisted.

The current v13 schema contains more than 1,800 definitions. A live isolated
release proved that even a 64-definition transaction can monopolize TypeDB's
schema compiler long enough for a one-second readiness probe to time out. A
supervisor reload then forgot that the unchanged serving TypeDB process had
already completed startup, treated the transient probe failure as unfinished
startup, and stopped every graph-dependent worker. The server continued the
abandoned schema transaction and held the schema lock, so the replacement
candidate blocked behind it. This formed a second control-plane loop:

`candidate schema compile -> transient probe timeout -> supervisor demotion -> worker termination -> abandoned schema lock -> candidate retry`

Cold provisioning now has one consistent contract:

- A rolling release first reuses the most recent isolated retired delivery
  database whose latest deployment health proves a complete, warmed ontology
  release. This preserves the compiled base schema and turns the normal
  blue-green path into release-artifact replacement plus current-state replay.
  A database is not reusable merely because an older deployment on the same
  binding was healthy: the latest deployment for that binding must itself be
  `retired`, `ready`, warmed, and have a non-empty runtime rule catalog.
- A failed or incomplete candidate therefore poisons only its own binding for
  automatic reuse. The next release chooses another verified warm standby or
  allocates an immutable isolated database. Fixed shadow database names are
  never trusted without this deployment-health proof.
- The adapter, settings loader, managed-process specification, and subprocess
  environment all default to 16 definitions per schema transaction and a
  60-second per-transaction deadline. The complete release seed keeps its
  independent end-to-end provisioning budget.
- A database created in the current process is known empty and avoids the
  expensive initial schema listing.
- A candidate database found after restart is treated as partially durable.
  Its schema is read once under the same bounded deadline and the bootstrap
  plan contains only missing definitions.
- Failure to inspect an existing candidate fails closed. It never falls back
  to a blank plan that can produce duplicate definitions and another long
  timeout.
- Serving-process startup readiness is persisted against a hash of PID,
  process start time, command, address, and storage path. A supervisor reload
  restores readiness only for that exact process generation, so a transient
  workload-probe timeout cannot demote or kill its graph workers. A real TypeDB
  replacement or stop invalidates the marker.

This removes the provisioning head-of-line block. It is distinct from native
inference latency: a release cannot enqueue or execute useful inference until
its immutable schema and static artifact have become ready.

The same live preflight exposed a missing import for TBox metadata
normalization. That defect was invisible to syntax compilation because the
name is resolved only while an immutable release manifest is built. Release
bootstrap regression coverage now executes manifest construction as well as
schema synchronization, so an unresolved release-contract dependency fails in
the curated suite instead of every candidate worker at runtime.

## Current-State Read And Evidence Reuse Optimization

The next production profile separated the remaining latency into two concrete
read paths. A two-symbol execution took about 110 seconds: ABox persistence was
28.8 seconds and native inference was 49.1 seconds, including an 11.0-second
matched-evidence reread. A later one-symbol execution took 64.1 seconds, with
32.4 seconds in ABox persistence and 13.2 seconds in native inference. These
measurements show that the queue can make progress, but the 30-second ABox
budget is not yet consistently met.

The current-state inventory originally read node and relation branches
serially. They are independent read-only queries, so the adapter now runs
exactly those two branches concurrently for both pre-write inventory and exact
post-write verification. TypeDB writes remain serialized under the existing
projection coordinator. The bounded storage-id batch remains 32; a live test
with 128 identities caused a TypeDB concurrent-transaction-close error and was
rejected rather than retained as an apparent throughput improvement.

The projection worker also used to read the matched evidence graph from TypeDB
after direct TypeQL had already evaluated the rules, even when it still held
the just-persisted graph under the same stable write lease. The optimized path
reuses that graph only after comparing every matched source and required
relation to the active Manifest storage-id index. A partial routed projection
is acceptable only when this exact post-match proof succeeds. Any missing row,
Manifest mismatch, or planner-topology mismatch automatically uses the durable
TypeDB read. Runtime metadata records the selected source and rejection reason.

The first live reuse attempt exposed an identity-boundary defect: the
projection layer passed logical scope generations while current-state
persistence had mapped them to bounded physical slot generations. The graph
therefore described the same facts but could never satisfy an exact physical
storage-id proof. The recorder now rebuilds the preflight persistence view from
the committed physical scope plan before native execution. It does not alter
facts or evaluate rules; it makes the already validated in-memory rows use the
same identities TypeDB committed.

The same two-symbol profile spent 54.1 seconds inside the changed-scope write,
while the measured insert queries used only 9.4 seconds. Exact stale-row
deletion was committing once per 64 storage identities. Relation deletes still
run before node deletes, but their bounded queries now share the configured
transaction query budget. Runtime stages expose delete duration, query count,
and transaction count independently, so future tuning can distinguish row
replacement cost from inserts and inventory reads.

A production two-symbol run after both changes completed projection and native
inference in 98.9 seconds, down from 146.9 seconds for the previous comparable
run. Native inference fell from 46.9 seconds to 13.2 seconds. The verified
in-memory matched graph was used, and its TypeDB consistency read took 0.69
seconds. ABox persistence improved from 69.0 seconds to 60.7 seconds. Its
remaining cost is now explicit: current-state inventory took 17.5 seconds,
four ordered stale-row delete queries shared one transaction and took 21.3
seconds, and the changed-scope write stage took 47.1 seconds. The next
optimization boundary is therefore TypeDB current-state replacement and commit
cost, not native-rule evaluation or duplicate matched-graph retrieval.

## Exact Dependency Routing Across Fact Families

A live MSTR replay exposed a correctness defect in incremental rule selection.
The verified monitor snapshot reported an exact
`kind:stock:field:profitlossrate` change, but its broad source families were
`market`, `flow`, and `temporal`. The account-owned profit and loss notification
rules declare `position` and `profile` families. The router compared those
coarse families before checking the exact dependency key, so both rules were
deferred even though the changed field matched their contract exactly.

An authoritative dependency boundary now takes precedence over coarse fact
families. Exact dependency keys are compared across every eligible condition;
family matching remains the conservative fallback for incomplete boundaries,
conditions without exact keys, and conservative conditions. The native rule
engine compatibility version was advanced to
`typedb-direct-typeql-rule-engine-v2`, preventing prior result slots produced
under the lossy selection contract from being reused. Regression coverage
uses the production loss and profit policy rule IDs with a market-family MSTR
event and requires both rules to enter the candidate set.

## Runtime Health Grace Window

A live blue-green provisioning run also exposed an operational feedback loop.
The durable TypeDB server became ready after WAL recovery, but two 30-second
driver probes overlapped schema and planner pressure. The supervisor treated
those transient query failures as a dead server, restarted the 22 GB store,
and created another multi-minute alert gap. Process disappearance still causes
immediate recovery, while a live TypeDB process now receives ten consecutive
30-second service probes before restart. Both values can be overridden through
`TYPEDB_RUNTIME_HEALTH_PROBE_INTERVAL_SECONDS` and
`TYPEDB_RUNTIME_HEALTH_FAILURE_THRESHOLD`. The production TypeDB process also
defaults to normal scheduling priority because startup rebuilds the inherited
schema-capability cache; blue-green staging remains lower priority so it cannot
starve live delivery. The macOS supervisor LaunchAgent is also `Standard`:
using `Background` on the parent silently applies Darwin background QoS to
TypeDB even when its own `processNice` is zero. Individual collectors retain
their explicit background nice value.

Retired release stores can be quarantined or deleted after their rollback
window. The deployment health then records `graphStorePruned.status` as
`quarantined`, `deleted`, or `missing`. Candidate registration excludes those
bindings even when an older warmup result still says `ready`, preventing a
large retired store from silently re-entering the next release and every
subsequent TypeDB startup.

## Candidate ABox Integrity Boundary

A later live candidate exposed a stricter failure mode than schema readiness.
Its retired TypeDB database had a valid, warmed TBox, but an older copy-on-write
lifecycle had deleted endpoint nodes without rebinding every dependent
relation. The active manifest still described complete relation scopes even
though their physical assertions were missing. Reusing that store caused a new
release to inherit the corruption before it processed its first event.

Candidate registration now provisions a new isolated TypeDB database by
default. Retired-store reuse is available only through the explicit
`REASONING_ENGINE_REUSE_RETIRED_CANDIDATE_STORE_ENABLED=1` operational opt-in.
This makes a full projection, rather than historical physical state, the basis
of every normal candidate release.

The TypeDB adapter also verifies every physical source and target node after
node commits and before relation writes. TypeDB permits a `match ... insert`
whose match returns no rows without raising an error; previously the adapter
could therefore report a submitted relation that was never created. Missing
endpoints now fail the candidate before any relation batch is written, with
the missing physical identities recorded in projection telemetry. Exact
post-write scope verification remains the final promotion boundary.
