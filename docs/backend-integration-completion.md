# Backend Ownership Integration

This batch completes the remaining backend ownership work as one integrated
handoff. It does not redesign the web UI, migrate private data, change investment
semantics, or convert synchronous APIs into asynchronous jobs.

## Completion Checklist

- [x] Assign the shared repository contracts to the twelve business modules.
- [x] Assign single-owner MySQL adapters and inventory explicit shared transaction
  coordinators. Preserve SQL, transaction boundaries, queue lineage and leases.
- [x] Separate TypeDB graph writes and RuleBox administration from the facade.
- [x] Separate projection publication, recovery, audit and shared-world work.
- [x] Freeze source behavior and test imports, ownership and failure paths.
- [x] Run the full regression suite and isolated native persistence checks.

Handoff requires commit, push, managed-worker restart, local/shared HTTP checks,
and the project notifier. Those deployment receipts are reported after execution
in the final response and notifier, not pre-checked in the commit being deployed.

## Boundary Rules

Repository contracts belong to their feature's domain package and are exported
through `contracts.py`. Store implementations belong to infrastructure, not the
contract surface. Shared connection/schema/retention facilities remain platform
infrastructure. Operations that atomically modify several owners remain explicit
transaction coordinators until their participants have compatible connection-bound
ports; splitting the transaction into event callbacks is not an ownership fix.

Inference publication must preserve the last usable generation on failure.
Recovery, RuleBox fingerprints, source clocks, account/world isolation and queue
acknowledgements retain their existing contracts. Durable source events precede
independently retryable work. User reads and atomic account changes stay synchronous.

## Verification Limits

Source parity guards complement, but do not replace, runtime regression and
failure-injection tests. Temporary TypeDB tests cover only their stated storage
boundaries. This batch must not claim whole-engine crash recovery, a database
permission security boundary, or removal of every large domain algorithm.

The integrated regression freezes 144 moved methods, with three documented
failure-path changes: RuleBox cache restoration, optional-argument negotiation
before calling a storage adapter, and removal of recovery's TypeError retry.
Storage declaration parity preserves SQL and transaction bodies across owner
moves. See `test_backend_integration.py` and its two source-revision fixtures.

## Validation Results

- `npm test`: syntax checks, web smoke and 1,020 core tests passed.
- `npm run python:test:full`: 1,225 tests passed.
- Isolated native static-manifest test: rollback after delete/before commit and
  one-row retry after commit acknowledgement loss passed.
- Isolated native source-packet test: uncommitted rollback, cold source replay
  equivalence and one-row retry passed.
- Managed databases were not interrupted for these native failure tests.
