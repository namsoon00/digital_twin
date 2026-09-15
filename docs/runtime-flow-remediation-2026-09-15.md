# Runtime Flow Remediation

## Scope

This change repairs four verified failures without weakening investment,
cooldown, source-quality, or release-promotion gates.

### Account Policy In Native TypeQL

Nine authored conditions reference another subject field. Previously the compiler
resolved `{"field": "strategyLossTolerancePct", "default": -8}` to `-8`, ignoring
the account's actual policy. Loss, profit-protection and position-limit conditions
now bind both attributes in TypeDB. The default branch requires proven absence
of the referenced attribute; a failed comparison cannot fall through to it.

The three strategy attributes are included in the physical schema capability
contract and ABox writes. Subject strategy facts carry that contract version,
forcing a new immutable generation for older JSON-only subjects. TBox and
RuleBox release artifacts are not overwritten. Compiler V7 invalidates V6
result-slot reuse; market scorer V4 does not evaluate private field comparisons.

Native regression: a -8.2% return does not meet a -15% limit, does meet a -8%
limit, and uses -8% only when no account limit exists. Missing or unsupported
field mappings fail closed. The catalog itself is unchanged.

### Long-Lived AI Workers

Temporary structured-output files are not durable state. Every command launch
now recreates a missing or corrupt schema after acquiring execution capacity,
using independent temporary files and atomic replacement. Startup stderr is retained before JSON-event validation so
a missing file cannot be misdiagnosed as a model turn that never completed.

Tests use isolated runtime directories. Running tests must not repair, remove,
or otherwise affect the production worker's files. A fallback is still not
accepted as an AI-authored investment opinion.

### Outcome Data Recovery

ETF benchmarks previously had current quotes and daily candles, but lacked the
intraday history required by older outcome windows. The market-data collector
now recovers requested benchmark windows through the optional existing yfinance
provider: completed 1-minute bars, seven-day fetch bound, four symbols per pass,
15-minute provider retry interval. Only requested windows are persisted, under
the existing one-day raw retention policy. No substitute ETF or current quote
is used for a missing original observation.

Repair retains the original instrument price, source time, decision baseline,
horizon and contract fingerprint. Benchmark reads cannot extend beyond the
original outcome time. Available benchmark endpoints are retained in compact
outcome facts so raw retention cannot erase a partially recovered comparison.

Persisted recovery retries back off for 15, 30, 60, 120, 240, 360 and 360 minutes.
After eight unsuccessful evaluations, `evaluationRecovery.state=unavailable`
and `automaticRetryStopped=true` stop automatic retries. Calibration remains
excluded due to missing data, never a successful prediction or failed thesis.
Explicit repair can still supply missing original data through the validated
outcome writer; it cannot replace a completed observation.

Live recovery during this change retrieved 139 historical benchmark points and
re-evaluated five stalled shadow outcomes without changing their original
instrument observations. Two were directionally corroborated and three remained
inconclusive. This is evidence of recovered evaluation, not investment alpha or
automatic candidate adoption. Long-term quality and shadow promotion need their
own observation cohort and existing governance checks.

### Delivery Evidence

The passive runtime verifier now recognizes the actual delivery contract:
`channel=accountNotification`, `provider=Telegram`, `audience=account`, with a
verified provider receipt. A completed queue job, operator notification or
unverified attempt is not proof of account delivery. Existing lineage checks
still require the exact source snapshot, ABox generation and AI/publication.

## Validation And Operation

- `npm test` covers syntax, frontend, smoke and the curated Python suite.
- Focused tests cover missing/corrupt schema recovery, concurrent file creation,
  source-time benchmark bounds, retry exhaustion, transaction immutability,
  native account field compilation and real delivery-channel fixtures.
- V6 extraction fixtures are retained. V7 compiler/runtime and V2 schema/input
  fixtures document the deliberate changes; unchanged publication, ABox
  persistence and recovery still match their V6 transaction fixtures.
- Restoring only the two previous strategy fact builders reproduces all nine
  original projection-input scenarios, isolating the new input identities to
  the explicit account-policy fact version.
- After restart, check native promoted policy attributes on current subjects,
  then observe real AI completion and Telegram receipts. Do not force delivery
  or loosen cooldowns merely to make a health check pass.
