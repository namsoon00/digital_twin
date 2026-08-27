# Testing

The maintained Python suite is intentionally bounded. It protects domain and
integration contracts without retaining every implementation-specific example
that has existed in the project.

```bash
npm test
```

The normal development gate runs syntax checks, the HTTP smoke test, and the
tests marked `core` in `python_service/tests/suite_manifest.json`. Core includes
all unit and contract tests plus queue-loss and database-deadlock integration
contracts.

```bash
npm run test:full
```

The full release gate also runs the slower TypeDB, MySQL retention, service
manager, and web-process tests. CI uses this gate. Individual tiers are
available as `test:unit`, `test:contract`, `test:integration`, and `test:system`.

## Suite Governance

- Every `test_*.py` module must be declared in `suite_manifest.json` with one
  tier and an explicit `core` decision. Unclassified test files fail the run.
- The maintained suite must remain between 600 and 800 tests, with no module
  containing more than 50 tests. Add a focused contract and remove a weaker
  example when the upper bound would be exceeded.
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
