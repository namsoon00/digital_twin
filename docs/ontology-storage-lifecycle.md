# Ontology Storage Lifecycle

This document describes the operational boundary between MySQL history and the
TypeDB materialized ontology. It is a storage policy, not an investment rule.

## Ownership

- MySQL is the durable source for market observations, account snapshots,
  projection outboxes, reasoning audit summaries, and delivery state.
- TypeDB stores the active TBox, governed RuleBox, current ABox world
  generations, and current InferenceBox results used by investment reasoning.
- Old TypeDB generations are rebuildable read-model history. They are not the
  long-term source of record.

## Retention Defaults

- Terminal notification payloads: 30 days; compact delivery identities: 365 days.
- Completed shared-world projection jobs: 6 hours; completed inference detail: 7 days.
- Failed shared-world payloads: compact after 2 days; delete the failed row
  after 30 days.
- Temporal feature snapshots: 3 days; statistical signal snapshots: 365 days.
- Investment reasoning cases and engine comparisons: 90 days.
- Intraday market observations: 3-minute data for 7 days, 15-minute data for
  30 days, hourly data for 365 days, and daily data for 1,825 days.
- Inactive TypeDB ABox manifests: keep one rollback generation per world.

Retention never removes active snapshots, pending or processing jobs, current
world manifests, current InferenceBox output, credentials, or delivery state.

## ABox Maintenance

The delivery reasoning process owns physical generation cleanup when the
single TypeDB writer topology is enabled. It runs the same bounded
`ontology-maintenance` task between reasoning turns; the standalone worker is
used only by the legacy multi-writer topology. This avoids concurrent TypeDB
writers while keeping retention outside investment latency. It follows these
rules:

1. An active inference transaction always finishes.
2. Strict queue priority defaults off. With background fairness enabled,
   maintenance deferred for 120 seconds may attempt one bounded turn when the
   queue proves there is no active inference lease, even if jobs remain queued.
   The single-writer delivery loop supplies this opportunity after inference.
   Unknown active-lease counts defer the attempt. Explicit yield requests remain
   disabled for this topology; no running inference is interrupted.
3. Normal maintenance uses two delete batches and a 45-second cooperative
   budget, with 300 seconds between successful fairness turns. A transaction
   already in progress can exceed that budget. The adaptive budget is also
   capped by the available time; it does not make cleanup unbounded.
   After three recent successful physical-delete turns in the same world,
   the time estimate uses twice the slowest observed per-batch duration,
   including read overhead, with a five-second floor. This lets the existing
   adaptive batch limit take effect when cleanup is consistently fast. Samples
   expire after 30 minutes and must use the same row batch size. Errors,
   exhausted time budgets and turns without completed physical generation
   deletion reset the sample window. Until it is verified, and during capacity
   emergencies, the configured conservative estimate remains in effect. The
   writer lease, rollback protection, configured adaptive maximum and 45-second
   cooperative deadline remain unchanged.
4. A turn never exceeds the configured manifest and batch budget.
5. The worker records per-world inventory and progress in MySQL. Status reads
   this durable state and does not scan TypeDB.

Persisted runtime settings override environment values and defaults. Existing
deployments must explicitly save `ontologyAboxMaintenanceStrictReasoningPriority`
as `0` and retain `ontologyBackgroundWorkFairnessEnabled=1` to adopt this policy.
Keep `ontologyAboxMaintenanceDeferWhenReasoningPending=1`; disabling that check
would bypass the age and active-lease admission rules. Confirm a successful
maintenance result, retained rollback count, actual deletion progress and the
next live inference completion after restart. A deferred or missing inventory
is not proof that no old generations exist. Monitor queue age and maintenance
duration together; the policy trades a bounded delay of subsequent inference
for preventing indefinite cleanup starvation.

Current-state ABox updates use copy-on-write. A changed scope is written to a
fresh retry-stable generation and verified before the active Manifest moves.
The hot path does not read or delete the retired generation. Unchanged scopes
keep their physical generation, and maintenance removes generations no longer
referenced by the active or retained rollback Manifest.

Topology `granular-v15-episode-catalog-items` gives each hypothesis template,
hypothesis family definition and portfolio decision cycle its own physical
scope. Previously these facts shared one account-wide episode inventory. A
one-symbol input contained only its own templates, so changing the cycle could
force complete-source reconstruction to preserve another symbol's endpoints.
Item scopes preserve those endpoints without replacing the whole inventory.
The regression reproduces the previous incomplete-endpoint rejection and
checks that the new patch retains the other subject's exact generation.

The topology version change selects complete input once through the existing
migration preflight. Candidate validation and atomic activation still protect
the serving and rollback generations; subsequent requests return to scoped
updates. This does not change TBox/RuleBox meaning, bypass missing-endpoint
checks or delete stored evidence. It reduces unnecessary reconstruction and
write amplification; WAL allocation and long-running disk growth still require
separate observation and the existing guarded rotation policy.

Inspect it with:

```bash
python3 python_service/service.py ontology-maintenance status
```

The command reads the same durable maintenance state when cleanup is embedded
in the delivery worker.

## Capacity

`TYPEDB_DATA_MAX_SIZE_MB=16384` is a safety ceiling, not a target. Normal
operation should stay well below the 70% write-throttle threshold. Automatic
rotation starts at 80%, WAL rotation starts at 4,096 MB, and 90% is critical.
The active graph's age observation window is 72 hours, but age-only deletion
remains disabled. Shared disk reserve checks remain independent, so increasing
the TypeDB ceiling cannot consume the host's final free space.

The MySQL operational ceiling is 16,384 MB. Capacity reports separate physical
files, live data and indexes, and allocator pages reclaimable by explicit
compaction. Increasing retention does not treat deleted but unreclaimed pages
as live investment history.

MySQL physical compaction is explicit because `OPTIMIZE TABLE` can briefly
rebuild a table. The command selects only allow-listed tables, requires at
least 20% reclaimable space, and preserves the configured shared-disk reserve:

```bash
python3 python_service/service.py maintenance mysql-minimal-retention
python3 python_service/service.py maintenance mysql-minimal-retention --apply --drain
python3 python_service/service.py maintenance mysql-cleanup --optimize
```

### Sustained MySQL retention and snapshot compression

An admitted maintenance attempt is not a completed turn. Losing the realtime
monitor lock must preserve the original deferral clock. When minimal retention
is enabled in apply mode, the maximum deferral is capped by its configured
interval (normally 120 seconds). An overdue attempt reserves the next monitor
gap for up to 60 seconds, then runs a five-second cooperative retention pass
with a ten-second SQL connection timeout. An already-running SQL operation can
exceed the cooperative budget. Calibration repair and broad legacy scans stay
in idle maintenance turns. The next monitor waits; an active collection is never
interrupted to grant maintenance access.

`mysql_retention_progress` stores the next policy before each expensive action.
A timeout, exhausted budget or worker restart therefore resumes at the next
policy instead of repeatedly starving later tables. The failed policy is
revisited on the next complete round. The maintenance result includes attempted
policies, the next policy and remaining policies. Verify actual deleted rows
and queue age alongside these counters; admission alone is not cleanup proof.
Snapshot history preserves the newest rows and source boundaries referenced by
unresolved reasoning jobs or pending mailbox events. These references are
rechecked at deletion time. Large snapshot candidate reads are capped at eight
rows so measuring payload lengths does not scan hundreds of megabytes per turn.

Large immutable JSON tables use native InnoDB `ROW_FORMAT=COMPRESSED` with an
8 KiB key block on supported local servers. The allowlist is snapshot history,
verified reasoning sources, statistical signal snapshots and reasoning run
stages. JSON text, SQL readers, fingerprints and replay contracts stay the
same. New tables adopt this format during schema bootstrap; existing tables
require an explicit migration with all managed application writers paused:

```bash
python3 python_service/service.py maintenance mysql-snapshot-compression
python3 python_service/service.py maintenance mysql-snapshot-compression --apply
```

The apply command refuses running managed writers, unsupported page sizes and
insufficient shared-disk headroom. Use the supervisor maintenance handshake to
prevent workers from restarting during migration, retain MySQL and TypeDB, and
restart application workers afterward. For every changed table, the command
compares all-row hashes and row counts before and after rebuilding. Measure
actual file allocation, not `DATA_LENGTH`: off-page `LONGTEXT` can make allocator
estimates misleading. `OPTIMIZE TABLE` alone does not make live JSON smaller.

An isolated MySQL pilot with representative source and history packets reduced
allocated files by about 86%, preserved every row hash and round-tripped new
writes exactly. This is a sample measurement, not a guaranteed ratio. Native
compression adds CPU and buffer work; monitor collection latency and resource
pressure after deployment, as described in the
[MySQL compression documentation](https://dev.mysql.com/doc/refman/9.7/en/innodb-compression-internals.html).
Logical retention is still required: physical compression reduces each write,
but does not replace age and reference policies.

Historical reasoning reads select the compact projection directly, falling
back to the full payload only when it is absent. Source assembly copies the
selected frozen facts without first deep-copying the entire provider archive.
Replay tests protect input immutability and independence from subsequent source
mutation. These changes reduce avoidable allocations; they do not establish
that every long-running process is free from memory leaks.

Notification claims now bound the sum of stored payload/body bytes to 8 MiB
per batch, in readiness order across pending and retry lanes. An oversized
first job is processed intact by itself, so this is not a message size limit
or an evidence truncation policy. Delivery history and exhausted-failure
reconciliation inventory IDs first and read each full packet separately;
they no longer fetch an entire history window of graph payloads into memory.
Receipt clocks, same-subject filtering and frozen comparison baselines remain
unchanged. JSON expansion and a single large graph can exceed the stored-byte
budget in RSS. Confirm long-running memory and disk trends independently.

## Blue/Green TypeDB Rotation

Automatic TypeDB rotation prepares an isolated candidate on a different port
and data directory while the active server continues to serve inference:

1. Start the candidate with configured credentials.
2. Seed TBox, language data, and governed RuleBox rows.
3. Compare the seeded RuleBox fingerprint with the frozen delivery deployment.
   A mismatch fails the rotation while the active store keeps serving.
4. Read the latest completed shared-world packets from MySQL and project them
   into the candidate without requeueing or changing live outbox rows.
5. Read each latest verified live account snapshot from MySQL and rebuild its
   current PortfolioWorld ABox plus the aligned native InferenceBox. This does
   not call providers, consume the reasoning mailbox, or enqueue alerts.
6. Validate authenticated TypeDB access and fail the candidate when any live
   PortfolioWorld cannot be rebuilt.
7. Stop managed dependents, swap the candidate directory into the active path,
   and restart them.
8. If startup fails, restore the retained previous directory and restart.
9. Remove the retired directory after the rollback retention window.

Scoped ABox retention first deduplicates generation IDs across removable
Manifests, deletes each retired physical generation once, and removes a
Manifest marker only after all of its unprotected generations are gone. The
maintenance status records planned and removed generation counts, duplicate
references avoided, and the remaining generation drain backlog per world.

Cleanup fails closed when the active, rollback or selected Manifest metadata
cannot be verified, or pending activation state is unknown. It reserves one
remaining write batch as soon as a Manifest is safe to retire, so later data
deletions cannot indefinitely postpone removal of completed Manifest markers.
Active and rollback references and external relation endpoints remain protected.

Routine maintenance persists a candidate cursor per world and scans retired
Manifests in bounded chronological windows. A fully inspected window advances
even when external relations protect every remaining node. This lets later
retired relation generations drain before the next sweep revisits their old
endpoints. The cursor contains the immutable timestamp and Manifest ID, so
removing its marker or restarting the worker does not reset selection. At the
end of the eligible inventory, selection wraps to the oldest retired Manifest.
Interrupted or write-budget-limited windows keep their cursor; unavailable
protection metadata never advances it. Every turn reloads active, rollback and
pending state under the existing writer leases. The cursor is scheduling state,
never evidence that a generation is safe to delete. Status exposes the selected
Manifest IDs and `candidateScanComplete`; rotation alone is not deletion progress.

Retired-generation presence checks share one read transaction for at most 64
exact IDs. Each indexed limit-one query includes both nodes and relations;
large disjunction/aggregation queries are deliberately avoided. Only generations
proved empty in this turn skip the per-generation deletion path. The proof is
never persisted or reused after a restart, rebuild or writer-lease release.
Read errors and deadlines stop cleanup, while nonempty generations still pass
the external-relation protection and bounded delete checks. Maintenance status
reports `generationPresenceProbeCount` as the number of these read transactions.

For a sustained backlog, the local deployment uses an adaptive maximum of eight
delete batches (`ontologyAboxMaintenanceAdaptiveDrainMaxDeleteBatchesPerRun=8`).
The default remains four. This is an upper limit, not a guaranteed eight-batch
turn: three recent successful timings must still justify the 45-second budget;
cold or failed timing evidence returns to the conservative two-batch limit.
The rollback count and per-batch row limit are unchanged. Verify actual cleanup
and subsequent native inference after changing this setting. Logical cleanup
does not immediately reclaim TypeDB WAL/checkpoint allocation.

`generationCleanupCounterVersion=physical-delete-v2` distinguishes generations
fully cleared after a delete batch in this turn from already-empty generations.
`clearedRetiredScopeGenerationCount` includes both for remaining-backlog
calculation; `removedRetiredScopeGenerationCount` counts only the former.
These are generation counts, not row or reclaimed-byte counts. Historical
totals included repeated empty confirmations; they are preserved separately as
`legacyGenerationClearConfirmationCountTotal` when a world's first new cleanup
result arrives. They cannot be used as unique deletion totals. Physical disk
recovery is still measured separately after blue/green retirement.

The projection recorder injects its pure RuleBox migration function into the
catalog adapter. The adapter must not resolve its own same-named persistence
wrapper as the migration function: that prevents existing RuleBox rows from
being inspected during a candidate PortfolioWorld rebuild. Recorder-boundary
tests cover migration, unchanged catalogs and failed persistence.

Candidate PortfolioWorld reconstruction receives the verified seed's RuleBox
and TBox fingerprints and uses the immutable release preflight to freeze its
recorder catalog. It must never migrate restored rules using current source
defaults. The manager re-reads the release contract after world reconstruction;
a post-rebuild mismatch invalidates candidate reuse and blocks cutover.

For capacity incidents, check the persisted reasoning `executionGuard` as well
as process health. `rotation-required` can stop new inference while TypeDB,
workers and collection all remain alive. Host free space, candidate staging
headroom and TypeDB usage thresholds are separate gates. Freeing host space
alone does not clear a TypeDB usage gate; a verified rebuild must also succeed.
Use the actual configured thresholds rather than the defaults above.

Inspect MySQL allocation before removing retained evidence. Tables can contain
substantial already-free allocator pages; explicit allow-listed physical
compaction can return them without deleting additional rows. Pause managed
writers first, reserve enough temporary space, and inspect every table result.
Do not manually delete TypeDB WAL/checkpoint files or MySQL `.ibd` files. A
successful cutover retains the previous graph for the configured rollback
window; record active-store reduction separately from host space recovered.

Candidate preparation failure never stops the active TypeDB. A fresh candidate
cannot inherit an active instance's seed skip flag: TBox and RuleBox seeding is
mandatory before any world replay. Consecutive failures retry after 5, 15, 30,
and then 60 minutes. The previous store is retained for 120 minutes after a
successful cutover by default.

## Test Isolation

The Python test runner pins TypeDB tests to port `1739`, HTTP port `8010`, the
`orbit_alpha_ontology_test` database, and `data/test-runtime/typedb-data`.
Infrastructure environment overrides are enabled only for that test process.
Tests must not read or write the production TypeDB endpoint.
The web smoke runner pins the same test graph endpoint and uses a unique MySQL
schema per fixture; it does not inherit a production MySQL URL from local env.

## Notification payloads and process memory

Notification storage uses `shared-metadata-v1` aliases only when a whitelisted
metadata object exactly equals its canonical top-level context. Reads restore
independent copies for existing consumers. Distinct values, full graph evidence,
rendered messages and receipts remain intact; older rows need no migration.
Canonical top-level context remains queryable by SQL. Existing rows compact on
their next ordinary state write; this change does not rewrite historical
deliveries or purge active evidence. Three production payloads replayed locally
on 2026-10-02 shrank by 46.84–47.15% with exact value equality after expansion.

Queue claims lock small IDs from each eligible lane, merge their readiness, and
load full context only for the selected jobs. Article duplicate checks read a
small captured identity summary for new rows. Compatibility reads load one old
payload at a time, including startup ledger backfill, instead of buffering up
to hundreds of complete graphs. The successful-delivery ledger and duplicate
policy remain in force.

Projection context and graph assembly caches retain at most an estimated 64 MiB
and 128 MiB of object data respectively, alongside their existing TTL/LRU and
entry limits. Oversized inputs execute normally without being cached; they are
never truncated. These are cache admission budgets, not an RSS or swap limit.
Long-duration observation still needs to establish steady-state process memory.

## Operational Verification

After a lifecycle change:

```bash
npm test
npm run python:service:restart
npm run python:service:status
npm run python:ontology-reasoning:status
```

`python:service:status` reports TypeDB as `unhealthy` when the managed PID
exists but the service port does not respond. The supervisor performs a full
authenticated probe every 30 seconds and restarts the process after two
consecutive runtime failures. It does not apply this restart rule while a new
process is still completing its initial WAL/index recovery.

Check that the reasoning queue is progressing, the projection circuit is
closed, the active runtime revision matches the deployed commit, TypeDB usage
is below capacity thresholds, and inactive manifest counts continue to fall.
