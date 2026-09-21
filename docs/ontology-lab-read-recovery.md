# Ontology Lab Read Recovery

## Failure Mechanism

The September 21 lab timeout occurred while assembling rule-authoring context,
not while executing a new native investment rule. Selecting the published
InferenceBox generation first loaded every historical node and relation JSON
payload in the portfolio world. A live reproduction read 7,479 node rows before
the 20-second read deadline expired. Immediate driver retries repeated that work.
The scheduler also retried failed suggestions on each lab cycle because only
successful attempts advanced its interval.

The queue probe could omit the actual candidate deployment when its ID differed
from the configured V2 deployment. Candidate inference was therefore competing
with lab reads without appearing in lab backpressure.

## Read and Retry Contract

- Online generation selection reads only published generation markers. An absent
  publication is `missing-generation`, not permission to scan historical facts.
  Native inference must publish a valid generation before authoring continues.
- Retention inventory reads scalar generation/time/count aggregates, not proof
  JSON. Pending candidate markers and the active generation remain protected;
  each retention call prunes at most two obsolete generations.
- Authoring pins the portfolio world, published inference generation and source
  ABox snapshot. Only requested symbols evaluated in that generation enter the
  inference context. `requestedSymbols` and `notEvaluatedSymbols` retain the
  omitted scope. No evaluated requested symbols means defer, not empty evidence.
- Generation proof readers retain their bounded node/relation queries and
  existing alignment validation. Partial or unavailable snapshots cannot invoke
  the general rule proposal advisor.
- Read deadlines and interrupted transactions are not immediately retried.
  Transient connection failures retain the existing bounded driver retry policy.
- Failed automatic suggestions back off for 5, 15, 45 and then 60 minutes.
  Deferred attempts recheck at the lab cadence (at least 60 seconds); successful
  attempts use the configured suggestion interval. This retry schedule is
  process-local and resets when the lab worker restarts.
- Active/delivery deployments and a processing candidate contribute to queue
  pressure. Dormant rollback candidates remain excluded. With normal lab
  deferral enabled, a failed queue probe means defer, not idle.
- Native inference, immutable release identity, AI action envelopes and customer
  notification permissions are unchanged.

## Verification and Diagnosis

`test_ontology_lab_read_path` covers marker-only selection, aggregate inventory,
missing publication, timeout/transport retry differences, scheduler backoff,
candidate pressure, failed probes and pinned authoring scope. Existing replay,
publication, TypeDB and ownership tests cover cross-path compatibility.

Read metrics distinguish `typedb.inference-published-markers`,
`typedb.inference-node-inventory`, `typedb.inference-relation-inventory`,
`typedb.inference-generation-nodes` and `typedb.inference-generation-relations`.
Lab read errors include the failed label, row count, elapsed time and query hash,
not the raw query or account credentials.

Live read-only measurements during this repair were approximately 0.32 seconds
for publication selection, 2.01 seconds for 171-generation inventory and 1.89
seconds for the scoped snapshot. These are observations under one server load,
not latency guarantees or proof of an entire AI-to-notification cycle.

For recurrence, identify the failed query label first, inspect live processing
counts for all control-plane deployments, and check the published world's
generation/source/target coverage. Do not raise deadlines or interpret a missing
publication as an investment HOLD. A successful bounded read does not prove
native computation, AI evaluation or external notification delivery succeeded;
those require their own completion receipts.
