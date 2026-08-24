# Ontology Storage Lifecycle

This document describes the operational boundary between MySQL history and the
TypeDB materialized ontology. It is a storage policy, not an investment rule.

## Ownership

- MySQL is the durable source for market observations, account snapshots,
  projection outboxes, reasoning audit summaries, and delivery state.
- TypeDB stores the active TBox, TypeDB schema functions, current ABox world
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

The dedicated `ontology-maintenance` worker owns physical generation cleanup.
It follows these rules:

1. An active inference transaction always finishes.
2. When inactive generations remain above the priority threshold for more than
   two minutes, maintenance receives one bounded writer turn before the next
   inference batch.
3. A normal hand-off stays at two delete batches. If the directly measured
   backlog is critical, the worker uses the larger timeout-derived safe budget
   so cleanup can catch up without crossing the isolated execution limit.
4. A turn never exceeds the configured manifest and batch budget.
5. The worker records per-world inventory and progress in MySQL. Status reads
   this durable state and does not scan TypeDB.

Inspect it with:

```bash
python3 python_service/service.py ontology-maintenance status
```

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
2. Seed TBox, language data, and TypeDB schema functions.
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

Check that the reasoning queue is progressing, the projection circuit is
closed, the active runtime revision matches the deployed commit, TypeDB usage
is below capacity thresholds, and inactive manifest counts continue to fall.
