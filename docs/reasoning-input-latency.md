# Reasoning Input Latency

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
