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

- Detailed reasoning stage and rule traces: 1 day.
- Completed shared-world projection jobs: 1 hour.
- Failed shared-world payloads: compact after 24 hours; delete the failed row
  after 7 days.
- Intraday market observations: 3-minute data for 2 days, 15-minute data for
  10 days, hourly data for 90 days, and daily data for 180 days.
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
3. A turn deletes at most the configured manifest and batch budget.
4. The worker records per-world inventory and progress in MySQL. Status reads
   this durable state and does not scan TypeDB.

Inspect it with:

```bash
python3 python_service/service.py ontology-maintenance status
```

## Capacity

`TYPEDB_DATA_MAX_SIZE_MB=16384` is a safety ceiling, not a target. Normal
operation should stay well below the 70% write-throttle threshold. Automatic
rotation starts at 80%, and 90% is critical. Shared disk reserve checks remain
independent, so increasing the TypeDB ceiling cannot consume the host's final
free space.

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
3. Read the latest completed shared-world packets from MySQL and project them
   into the candidate without requeueing or changing live outbox rows.
4. Validate authenticated TypeDB access.
5. Stop managed dependents, swap the candidate directory into the active path,
   and restart them.
6. If startup fails, restore the retained previous directory and restart.
7. Remove the retired directory after the rollback retention window.

Candidate preparation failure never stops the active TypeDB. The previous
store is retained for 30 minutes after a successful cutover by default.

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
