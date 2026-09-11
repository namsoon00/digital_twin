# Passive Runtime Continuity Verification

`python_service/tests/verify_runtime_continuity.py` observes an **already running
local runtime**. Unlike `verify_integrated_load.py`, it creates no synthetic
work and does not execute a model, graph query, repair, delivery or recovery.
An HTTP 200, fresh supervisor heartbeat, or completed queue row alone is **not
live AI end-to-end evidence**.

## Run

Coordinate with the runtime owner before starting. The caller explicitly
supplies local `MYSQL_DATABASE` and `MYSQL_USER`, plus `MYSQL_HOST`,
`MYSQL_PORT`, `MYSQL_PASSWORD` or `MYSQL_UNIX_SOCKET` as needed. Do not print
these values, put them in command arguments, or attach environment files to
reports. No dotenv file, application settings, store constructor or service
composition is loaded. The existing `pymysql` dependency is required only for
live reads; fixture tests need no database or credentials.

```bash
PYTHONPATH=python_service/tests python3 -m unittest -v test_runtime_continuity
PYTHONPATH=python_service/tests python3 -m unittest -v test_runtime_continuity_mysql

# One observation to check schema/connection compatibility, not a continuity pass.
python3 python_service/tests/verify_runtime_continuity.py \
  --allow-live-read-only --duration-seconds 0 \
  --output /tmp/runtime-continuity-smoke.json

python3 python_service/tests/verify_runtime_continuity.py \
  --allow-live-read-only --duration-seconds 900 --interval-seconds 60 \
  --output /tmp/runtime-continuity-900s.json
```

The equivalent npm entry point is `npm run python:verify:runtime --` followed
by the same explicit opt-in and output arguments. The 22 fixture tests run in
both the curated core and full suites; neither suite starts live observation.

Use a new absolute output path outside the repository. Existing files are not
overwritten. The final JSON and sibling `.progress.jsonl` have mode `0600`.
Progress records are observations, not final verdicts. The final report records
the verifier source fingerprint, requested duration, actual elapsed time,
sampled first-to-last span, expected/observed counts and skipped schedule slots.
SIGINT/SIGTERM finalizes an incomplete report; a forced kill may leave only
progress, which must not be counted as completion. No catch-up polling burst is
performed after sleep/host suspension or late scheduling.

Exit codes: `0` requires passing sampled infrastructure, observed source-time
progress and new linked AI evidence; `1` means degraded evidence; `2` means
inconclusive/incomplete or a preflight failure. Missing new AI work is a normal
possible result of passive observation, not a reason to invoke the model.

## Read Surface

- Fixed HTTP GET allowlist: `/api/version` and `/api/operations/performance`,
  default origin `http://127.0.0.1:3000`. Use `--base-url` for another loopback
  port. Redirects, remote hosts, credentials, query strings and arbitrary routes
  are rejected. Proxy environment variables are not used. HTTP telemetry
  naturally records these GETs in memory; no application data is written.
- The verifier deliberately excludes `/api/bootstrap`, `/api/realtime/status`,
  `/api/operations/health` and platform endpoints. Their composed readers may
  instantiate stores, initialize platform state or reach expensive work.
- The only local runtime file read is the supervisor heartbeat (default
  `data/python-supervisor-heartbeat.json`, override `--heartbeat-file`). It
  must be a regular, bounded file; symlinks are rejected. No process manager,
  process probe, log reader, notification transport or external API is invoked.
- Raw PyMySQL uses a separate connection per observation with session
  `TRANSACTION READ ONLY`, `MAX_EXECUTION_TIME`, a one-second metadata-lock
  timeout, and `START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY`.
  Every exit rolls back and closes; there is no commit or fallback if the
  read-only setup/schema is unsupported. Prefer a SELECT-only database user.
- SELECTs read `reasoning_engine_control`, pointed deployments' stored health,
  recent verified source snapshot metadata, reasoning/AI/notification queues,
  and stored lineage. Queue retries, error presence, oldest ages, lease expiry,
  ownership and heartbeat ages are read from actual queue columns. Failure
  samples are limited to the lookback window; active backlog includes old work.
  No raw error text or JSON payload/message/account dump is selected.

## Limits

| Control | Default | Accepted Bound |
| --- | --- | --- |
| Observation duration | 900 seconds | 0 (one smoke sample), or interval through 86,400 seconds |
| Poll interval | 60 seconds | 30-900 seconds |
| HTTP/MySQL socket timeout | 3 seconds | 1-5 seconds |
| MySQL SELECT execution limit | 1,000 ms | 100-3,000 ms |
| Rows per SELECT | 100 | 1-200, plus one truncation sentinel |
| Historical lookback | 3,600 seconds | 60-86,400 seconds |
| Source/heartbeat stale threshold | 300 seconds | 30-86,400 seconds |
| Backlog age threshold | 1,800 seconds | 60-86,400 seconds |
| Slow HTTP threshold | 2,000 ms | 100-30,000 ms |

Each observation makes two serial HTTP calls and normally 18 SELECTs for one
pointed deployment (25 for two, 32 for three), with a hard SELECT-count guard of 40. HTTP
bodies are capped at 256 KiB, heartbeat files at 32 KiB, and telemetry routes at
the row limit. A 20-second observation alarm stops further reads; rollback can
consume one additional socket timeout. The final observation may end that far
past the requested duration. Report filesystem I/O and host suspension are not
a real-time scheduling guarantee. There are no retries within an observation.

A duration exactly divisible by the interval includes an observation at both
ends: 900/60 requests 16 samples. Other durations wait out the remaining tail
without adding a too-close extra request. All health is **sampled**, not proof
that nothing failed between observations. A 15-minute run never proves 24-hour
stability; a longer opt-in duration does not prove it until actually observed.

## Evidence And Verdicts

`sampledInfrastructure` is separate from `sourceProgress`, `liveAiLineage` and
`notificationLineage`. Missing observations, unknown health and unavailable
reads remain explicit. Errors, stale clocks/data, failed jobs, aged backlog,
expired/unowned leases and runtime/control/release drift degrade the relevant
result. Stored control pointers label active, delivery and candidate roles.
Candidate-only errors and aged backlog appear separately in `candidateOnlyHealth`
and the all-deployment backlog, not as customer-runtime failures. This does not
assert the candidate is inactive: after promotion a candidate pointer may be a
rollback identity, while a configured candidate can still receive validation
work. Active or delivery roles keep primary severity. Reasoning backlog includes
`queued`, `retry`, `processing`, `awaiting_source` and `awaiting_world_projection`;
these are actual durable states, not inferred process names.

Snapshot progress compares the first and last **latest live generated
timestamp in the bounded snapshot sample**, counts distinct observed timestamps
and detects regressions. It does not imply every account/source progressed.
No timestamp progress is inconclusive even when HTTP and queues look healthy.

Source/queue/lineage `sampledCount`, `truncated` and `lineageSampledRows` are not
full database totals. Queues use oldest-first samples per status/deployment;
truncation prevents claiming complete queue coverage. Snapshot history is
newest-first, so truncating old history does not invalidate the observed latest
timestamp. Lineage selects recent completed jobs for the **delivery** deployment
and then bounded joined rows. Busy fan-out may exclude other subjects or older
jobs; an AI result whose reasoning receipt is outside the lookback may be missed.
Reports explicitly retain lineage gaps and truncation; absence is not proof of
no production activity. Counts across polls deduplicate AI results using one-run
hashes instead of summing overlapping windows.

The strong stored chain requires:

1. A persisted source event and live-mode verified snapshot, with the exact
   account/snapshot/generated-time tuple from the job's immutable
   `source_boundary_json`. `JSON_TABLE` selects the subject's boundary, not
   another account's newest primary snapshot in a global job. Extracted boundary
   comparison operands are cast to binary for exact identity across collations.
   Indexed join columns remain unwrapped; the proof predicate casts both sides
   so explicit collations cannot weaken case-sensitive identity. Database
   collations are not changed. Coalesced predecessors are joined via
   `reasoning_engine_job_sources`; mere representation is not proof that an
   older snapshot was executed. The producer's completion-scope helper reads
   plural `accountIds` only, so a blank lineage account is not a conflict by
   itself. Declared plural/singular event account scope must match; a genuinely
   global event requires the exact persisted subject boundary. Missing binding
   remains a gap, never a blanket wildcard match.
   A nonblank lineage account and the original source event's declared account
   scope both constrain identity; neither can override a mismatch in the other.
   The fixed `SOURCE_SCOPE_MATCH_SQL` predicate is also tested on real MySQL
   with synthetic literal/JSON_TABLE rows and deliberately different collations.
   That one integration test selects no database, touches no tables, and uses
   the same rollback-only reader; it never imports schema-owning test fixtures.
2. The completed job's explicit result case/request IDs, persisted subject
   case and candidate snapshot, matching account/symbol, ABox/generation and
   candidate fingerprint, and the persisted complete-trace flag.
3. The subject's completed AI request, AI-authored result with passed publication
   contract, and matching `AIInsightEpisode`. TypeDB fallback, failed AI,
   incomplete linkage and mock-mode input do not qualify.
4. Separately, a canonical publication. `FINAL_DECISION` additionally requires
   a matching decision episode; a valid AI narrative need not create one.
5. Separately, a non-mock actual-data notification linked to that publication
   with a recorded `delivered` Telegram attempt. `done`, console output or an
   outbox row alone does not establish delivery. A receipt is transport evidence,
   not recipient acknowledgement.

Historical complete chains are counted separately. `liveAiLineage` requires an
AI result created during this run; `entireSourceToAiInWindow` additionally requires
the source event during the run. No new qualifying AI completion is
**inconclusive**, never passed E2E. This verifies stored claims/identity and
chronology, not model correctness, trading quality, provider authenticity or a
fresh TypeDB readback. Legitimate abstention, review-only and suppressed delivery
must not be replaced by artificial decisions or notifications to make a pass.

Only allowlisted enums/numbers/normalized timestamps enter the report. IDs,
runtime identities and route names use HMAC-SHA256 with a random, unreported
per-run key. Hashes are stable only within one run. Credentials, accounts,
symbols, source text, message bodies, SQL and exception text are never emitted.

Coordinate live runs separately from deployment and core/full test execution.
The verifier never commits, pushes, restarts processes, promotes a graph release
or sends a handoff notification.
