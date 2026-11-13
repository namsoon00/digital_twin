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
