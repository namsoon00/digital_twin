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
