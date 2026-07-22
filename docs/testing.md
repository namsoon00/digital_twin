# Testing

Use the maintained test suite for development and handoff:

```bash
npm test
```

It runs JavaScript/Python syntax checks, the local smoke test, and every maintained `test_*.py` module. The Python runner provides both `python_service` and `python_service/tests` on `PYTHONPATH`, so each test module can run independently without depending on execution order.

```bash
npm run test:full
```

`test:full` runs the same maintained set. New maintained Python test files are discovered automatically.

`python_service/tests/legacy_python_service_regression.py` is an archived pre-TypeDB regression suite. It asserts removed Python fallback reasoning and retired standalone alert behavior, so it is intentionally outside automatic discovery and is not a release gate. Migrate a specific historical scenario into a current domain, ABox, TypeDB function, and InferenceBox contract test before restoring it to the maintained suite.
