# Testing

The project is in pre-release development. Optimize the feedback cycle by
running tests that match the change. Keep broad regression coverage available
for architecture changes and release readiness.

```bash
npm test
```

The quick gate runs syntax/build checks, tooling and frontend tests, the
isolated home/bootstrap HTTP smoke test, and Python modules with `tier: unit` in
`python_service/tests/suite_manifest.json`. The target is roughly one to two
minutes on the development machine. It does not certify DB/TypeDB integration
or complete feature behavior.

`npm run smoke:quick` checks server startup, the home page and bootstrap payload.
`npm run smoke` retains the extended API, fake-provider mapping and sharing/auth
scenarios; run it when those paths change. Core/full also include that extended
smoke. Both modes clean up their own server and temporary test database.

For simple wording, UI or documentation edits, use only the relevant syntax,
frontend or manual screen checks. Documentation-only changes do not require a
suite run. For behavior changes, use the quick gate plus any affected tests
outside its unit tier:

```bash
npm run test:focus -- test_observation_checkpoints.py test_ai_evidence_wake.py
npm run test:focus -- test_observation_checkpoints.py --list
```

Focus accepts exact manifest filenames, filename stems, or paths such as
`python_service/tests/test_ai_evidence_wake.py`. Duplicates run once; empty,
unknown, wildcard or out-of-scope selections fail instead of silently running
nothing or falling back to the entire suite. `--list` works for every Python
mode and previews the selection without starting Python or touching storage.
Focus runs only the selected Python tests; it does not repeat syntax, frontend
or HTTP checks that already passed.

DB, queue, evidence and notification changes must retain their relevant
integration checks for data preservation, account isolation, source lineage,
and duplicate-delivery prevention. Choose the files explicitly after tracing
the changed behavior; there is no automatic dependency-coverage guarantee.

## MySQL process isolation

Python runs, direct MySQL fixtures and HTTP smoke now use a separate MySQL
process, not merely a different schema on the application's server. The default
test process starts automatically on loopback port 33307 with its own socket,
128 MB buffer pool and `data/test-runtime/mysql-isolated` data directory.
Concurrent test starts share a startup lock; the existing fixture lock still
serializes resets of the reusable test schema. The small test server remains
available for the next run. Selection previews never start it.

Ordinary `MYSQL_*`, `MYSQL_URL` and `DATABASE_URL` application settings cannot
route tests to the app server. To use a disposable CI/test server explicitly,
set `MYSQL_TEST_HOST`, `MYSQL_TEST_PORT`, `MYSQL_TEST_USER`,
`MYSQL_TEST_PASSWORD` and optionally `MYSQL_TEST_UNIX_SOCKET`. The endpoint is
probed before fixture writes; the project app data directory is rejected.
An unavailable explicit server fails setup without falling back to the app.
CI declares its disposable service container through these variables; the
self-hosted issue runner uses the separate default process.

## Broader checks

```bash
npm run test:core
npm run test:full
```

`test:core` preserves the former default selection (`core: true`), including
broad contract, ownership and integration checks. `test:full` runs every
manifest module, including slower TypeDB, MySQL retention, service-manager and
web-process checks. Use the appropriate broader gate for cross-cutting changes
or release readiness. Do not run both without a specific coverage reason.

CI push and pull-request runs use the quick gate. A manual CI run can select
`suite: full` for release verification. There is no recurring full-test job.
Individual tiers remain available as `test:unit`, `test:contract`,
`test:integration`, and `test:system`; `python:test:*` runs Python alone.

Reuse existing regressions and add only meaningful missing coverage. Small
reversible edits do not need new tests. Re-run passed checks only for later
changes affecting them or unresolved failures. Report whether validation was
quick, focused, core or full, including any skipped or unavailable integration.

## Suite Governance

- Every `test_*.py` module must be declared in `suite_manifest.json` with one
  tier and an explicit `core` decision. Unclassified test files fail the run.
- Suite-size review remains in `test_suite_governance.py`, run by core/full or
  explicitly when changing suite ownership. Avoid implementation-specific or
  duplicate examples just to increase test counts.
- The runner forces MySQL and TypeDB test database names. A production database
  name fails before any test module is imported.
- Per-test timings are written to the ignored
  `data/test-results/<mode>-latest.json` file. Use the slowest entries to move
  misplaced tests to the correct tier or simplify expensive fixtures.
- Tests must be order-independent. Shared MySQL fixtures reuse only the managed
  `orbit_alpha_test` schema and reset it at contract boundaries.

`python_service/tests/legacy_python_service_regression.py` remains an archived
pre-TypeDB regression suite. It is outside discovery because it asserts retired
fallback reasoning. Restore a historical scenario only as a current domain,
TypeDB, queue, or notification contract.
