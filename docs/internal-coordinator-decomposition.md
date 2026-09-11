# Internal Coordinator Decomposition

This extraction changes ownership of synchronous implementation code, not
investment semantics, native engine versions, SQL, release identities or
transaction boundaries. Existing store methods and record-stage input/result
APIs remain available. No runtime, driver, queue or notification sender is
constructed by a new helper.

## Decision History

`python_service/digital_twin/infrastructure/transactions/decision_history.py`
retains the compatibility API, bound callback wiring, caller-owned transaction
option and per-store target-maintenance completion flags. Helpers receive only
the connection factory, settings mapping or named callbacks they actually use;
none receives the repository, a shared mutable context or a service locator.

New files under `infrastructure/transactions/decision_history_parts/`:

| File | Responsibility |
| --- | --- |
| `__init__.py` | Private package boundary, no runtime construction |
| `ports.py` | Execute-only connection context factory type |
| `decision_write.py` | Frozen prepared-decision packet and connection-bound owner writes |
| `episode_queries.py` | Bounded history, current heads and decision memory |
| `episode_hydration.py` | Row conversion and separate mutable follow-up/outcome joins |
| `replay_queries.py` | Original decision facts with separate later observations |
| `outcome_policy.py` | Existing frozen-contract, timestamp and horizon helpers |
| `outcome_schedule.py` | Connection-bound scheduling and follow-up supersession |
| `target_queries.py` | Frozen pending-target read request and target summaries |
| `target_repair.py` | Backfill and pending-only schedule migration |
| `follow_ups.py` | Observable-condition evaluation and pending-row compare-and-set |
| `observation_records.py` | Decision observation assembly and per-outcome persistence |
| `shadow_observations.py` | Research-only samples and shadow outcome persistence |
| `performance.py` | Outcome-led calibration reads and coverage summaries |
| `learning.py` | Review-only proposal persistence |
| `legacy_repair.py` | Audit-preserving legacy quarantine and pointer repairs |

One decision save still writes all owners and events on one transaction. An
external connection does not acquire or commit another transaction. Outcome
batches still commit each outcome independently; a later failure does not undo
earlier committed outcomes. Shadow sample batches and schedule repair retain
their original shared transactions. Pending guards and contract fingerprints
are unchanged. The event and performance-evaluator bindings remain injectable
through the compatibility facade.

## Manifest Repair

Under `modules/reasoning/infrastructure/projection_write/`, the existing
`patch_manifest.py` now coordinates these new private files:

| File | Responsibility |
| --- | --- |
| `patch_manifest_source.py` | One complete-source reassembly with original snapshot/release/world inputs and target-bounded selection |
| `patch_manifest_identity.py` | Verified active-topology reuse or merged-topology identity |
| `patch_manifest_diagnostics.py` | Applied integrity contract, blocked result and explicit full-input fallback |
| `patch_manifest_results.py` | Frozen phase inputs/results without runtime capabilities |

Packets follow the existing record-stage model: their fields are frozen, while
ownership of mutable graphs/episodes transfers synchronously without cloning.
Source repair receives only assembly and selection callbacks. Identity planning
has no I/O port. Diagnostics cannot publish. Blocked local patches persist their
failure audit in the coordinator, never activate a whole-world fallback.
Deferred relation scopes, semantic rebind roots, observation clocks, world IDs,
release artifacts, fingerprints and prior usable generations remain intact.

## Validation And Handoff

New test files are `python_service/tests/test_internal_decision_history.py` and
`python_service/tests/test_internal_patch_manifest.py`. They cover frozen
packets, failures before/after individual writes, caller rollback, ambiguous
commit acknowledgement, partial outcome batches, shared decision/shadow target
repair, stale pending-row updates, blocked manifests and audit/generation
preservation on either side of publication.

`python_service/tests/internal_coordinator_parity.py` reconstructs the original
declarations for the parent's existing golden tests. `decision_history_members()`
restores per-method capability names and packet stages; `patch_manifest_member()`
inlines source repair, merged identity and diagnostics. Only relocated docstring
indentation is normalized. SQL string contents are never normalized or rebuilt.
The reconstructed history class matches the unchanged HEAD baseline hash
`f02cd846317aacd488525b469afee2289333ecad7156ee4fae6dd3e9fe9d5bd1` after the
existing participant expansion. Full captured AST parity, seven manifest
result/callback/mutation traces and 40 exact decision/outcome SQL/fault traces
were also checked against the pre-extraction workspace copies.

Focused tests use in-memory transaction/graph recorders, not live MySQL or
TypeDB. They do not establish native locking, crash durability or production
performance. The parent owns broader validation, golden integration, suite
registration, commit/push and managed runtime handoff. This slice does not
commit, restart services or send notifications.
