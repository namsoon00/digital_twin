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
