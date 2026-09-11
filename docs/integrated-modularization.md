# Integrated Modularization

This work covers domain ownership, internal coordinators, HTTP routing, browser
modules and operational verification. Moving files alone is not completion.
Investment rules, frozen releases, persisted history and account policies are
not migration targets. Reads and atomic commands remain synchronous.

## Progress

- [x] Capture baseline and classify every remaining root domain file.
- [x] Move business implementations and events to explicit owner contracts.
- [x] Isolate platform application work and forbid legacy domain dependencies.
- [x] Decompose decision-history and manifest-repair coordination.
- [x] Separate HTTP routing, access policy and response boundaries.
- [x] Separate browser state, requests, navigation and feature rendering.
- [x] Verify browser continuity and bounded multi-account failure recovery.
- [x] Run final core validation and browser regression checks.
- [x] Run final full validation.

Commit/push, managed restart and notifier results are reported by the final
handoff with the resulting commit identity, not predeclared by this source
document.

## Acceptance

Every moved implementation has one owner. Shared kernel code cannot import
business modules. Cross-owner imports use explicit contracts or public APIs.
Atomic transactions, source timestamps, generation identity and last usable
results survive failures. No new broker or generic queue is required.

Browser checks include rapid tab changes, detail persistence, scroll continuity,
stale-response rejection and versioned assets. API checks include access policy,
response shape, bounded reads and local/shared access. Load rehearsals use
synthetic accounts and isolated storage, never real investment notifications.
Elapsed soak duration and tested workload must be reported; short fixtures do
not establish long-running production capacity.

Revision-specific results are recorded after validation, not assumed here.

## Ownership Verification

The baseline (`637b49023`) passed 1,045 core tests before editing. All 287 former
root domain/application files are inventoried in `domain-ownership.json`.
The generic envelope is independent of account/portfolio types. Event names
and bounded payload builders have separate owner files, so importing a constant
does not initialize an event producer in another module.

The migration fixture fingerprints 4,927 original definitions. Comparison
normalizes only import locations, equivalent named imports and docstrings;
executable rule/data values remain protected. Fresh-process contract loading
is tested in three orders. Ten ownership/completion tests and 80 focused
TypeQL/publication/runtime/seed boundary tests passed after the move. These
results are not a substitute for the integrated browser and load checks below.
The final event audit also imported all 414 owned domain/contract modules in a
fresh process. It caught and restored eight existing news event constants;
their exact names and event serialization now have a direct regression test.

## Integrated Checks

An initial complete curated Python run passed 1,325 tests after integration.
The news-event regression then increased the suite to 1,326 tests, including
the original coordinator SQL/transaction golden checks and 76 new focused
regressions. Final `npm test` passed 1,121 Python tests in 138.009 seconds plus
11 frontend tests, syntax/build checks and the HTTP smoke test. The sequential
final `npm run test:full` passed all 1,326 Python tests in 152.120 seconds and
repeated the 11 frontend tests, build checks and HTTP smoke test. The final
generated release also passed the independent Playwright browser run.

Native TypeDB rehearsals used separate temporary servers and databases. Both
passed: manifest rollback after deletion/before commit and ambiguous retry
produced one row; uncommitted source input rolled back and durable packet replay
remained equivalent. Neither run touched the managed graph store. These checks
do not execute every production inference rule or prove a complete deployment
rebuild.

The [frontend report](frontend-modularization.md) describes 177 source modules
and the generated `modules-7aa796120a6e3e92` release. Eleven direct unit tests
and the synthetic Playwright checks passed in bundle and native-module modes.
At 390 x 844, measured scroll offsets remained 4,605 px during list append,
1,400 px after a tab return and 900 px across case tabs. Existing rows retained
their DOM identities, and delayed detail responses did not reopen a closed
dialog. Desktop/light/dark/chart screenshots were inspected. This is isolated
UI evidence, not live data or service-worker upgrade verification.

HTTP dispatch, authentication, read-only roles, response/cache shape and source
mapping are documented in [Web Router Separation](web-router-separation.md).
Decision-history and manifest algorithm boundaries, faults and parity are
documented in [Coordinator Decomposition](internal-coordinator-decomposition.md).

The [load report](integrated-load-verification.md) records the completed
16-account, four-thread MySQL rehearsal: 97 waves, 1,552 account-wave cases and
904.162 seconds of workload. All expected rows and outcome links matched, with
zero account violations or remaining reasoning/AI/delivery backlog. The owned
schema was removed and verified absent. The harness retried 42 AI-claim
deadlocks using the bounded existing retry component; this is not a new retry
policy in the production AI consumer. No external request, actual model run or
notification transport was executed.

## Remaining Verification Limits

The legacy shared test schema cannot safely run two complete suites at once.
One concurrent full/core invocation hit MySQL error 1684 during table DDL.
Both final suites passed when run sequentially; future full/core invocations
must also be serialized. The new load harness uses its own exclusive schema
and does not adopt that shared schema.

Six additional tests outside the curated suite fail identically at pristine
baseline `637b49023` and after this migration (four assertion failures and two
errors). Both reproductions used isolated schemas, disabled TypeDB, and kept
the same test bodies apart from moved mock targets. Relevant web/policy
function ASTs and captured payloads also match. These are existing contract
disagreements, not silently counted as passing tests:

| Legacy test suffix | Existing disagreement |
| --- | --- |
| `notification_rule_payload_saves_similarity_bypass_conditions` | Expects a removed `holding_score_delta` condition |
| `notification_policy_payload_defaults_to_managed_types` | Expects six managed types, current policy has nine |
| `notification_template_test_send_queues_live_snapshot_message` | Repetitive content is suppressed instead of queued |
| `investment_insight_test_send_bypasses_policy_and_sends_directly` | Fixture has no matching event; returns 422 instead of 200 |
| `investment_insight_test_send_records_typedb_projection_before_type_check` | Same missing-event fixture mismatch |
| `realtime_status_payload_includes_monitoring_and_queue_state` | Sums metadata alongside numeric queue counters |

All six belong to `legacy_python_service_regression.PythonServiceTests` and
have the `test_` prefix. Local evidence is in
`/tmp/orbit-web-legacy-audit.FkrC01/{head,current,source-parity}.log`.
This migration does not change notification policy to make old expectations
pass. The frontend report separately records existing hardcoded `ACTUAL`
chart badges and remaining mutable feature dependencies. Neither the bounded
load rehearsal nor fixture UI checks establish 24-hour production stability.
