# Ontology Reasoning Bottleneck Proof

Use the proof command before changing TypeDB timeouts, parallelism, rule count,
or retention policy:

```bash
npm run python:ontology-reasoning:profile -- --symbols 005930 --repeats 2
```

The command combines two independent evidence sources:

1. Recent completed production projections from MySQL, including top-level
   ABox/native timings, native sub-stage timings, and per-rule query traces.
2. A live read-only replay against the currently active RuleBox and ABox.
   It executes direct TypeQL reads, matched-evidence reads, and in-memory
   InferenceBox graph construction. It does not sync functions, write an ABox
   or InferenceBox, activate a generation, or run retention.

Each replay reads the active ABox identity before and after execution and
verifies the RuleBox hash again after the final sample. A sample is rejected
when the RuleBox, Manifest, active pointer, material fingerprint, or scoped
generation map changes, or when the core native evaluation is incomplete. Two
valid samples from the same generation are required before a read boundary can
be reported as `confirmed`. The native TypeDB read
boundary combines rule evaluation and matched-evidence graph hydration because
they are consecutive, non-overlapping reads against the same active ABox.
`productionDominantReadSubstage` still identifies which part of that boundary
is slower.

Verdicts:

- `confirmed`: production telemetry and the unchanged-generation replay identify
  the same dominant read boundary.
- `supported`: production telemetry points to a stage, but a no-write replay
  cannot independently reproduce it or the replay sample is insufficient.
- `inconclusive`: the audit history is absent, a replay failed, or the active
  ABox changed while measuring.

`inferencebox-write-dominant` and `abox-persistence-dominant` remain `supported`
because the diagnostic never performs a synthetic operational write. A write
benchmark requires an explicitly isolated capacity test and is outside this
command's contract.

Useful options:

```bash
npm run python:ontology-reasoning:profile -- \
  --account-id main \
  --world-id portfolio:local:main \
  --symbols 005930,035420 \
  --production-runs 10 \
  --repeats 2 \
  --rule-id graph.example.v1
```

The JSON result always declares `readOnly`, `mutatedOperationalState`,
`writeMethodsInvoked`, excluded operations, generation fingerprints, fixed
proof thresholds, production slow rules, and replay slow rules. Treat a
`supported` or `inconclusive` result as a reason to gather more evidence, not
as permission to tune a different subsystem.

The default output keeps the complete verdict and the eight slowest rules per
sample while omitting bulky raw generation and query diagnostics. Add `--full`
when those diagnostics are needed.

## Root-Cause Decision Order

Do not treat a long end-to-end duration as proof that TypeQL rule evaluation is
the bottleneck. Diagnose the stages in this order:

1. Compare the source-event arrival interval with completed-run service time.
   The worker cannot drain when service time is equal to or longer than the
   arrival interval, regardless of queue coalescing.
2. Compare `aboxPersistenceMs`, the native read boundary, InferenceBox writes,
   and candidate construction. Tune only the dominant measured boundary.
3. Replay the same active generation read-only. A fast unchanged-generation
   replay next to a slow production run identifies projection/write
   amplification rather than an intrinsically slow rule set.
4. Inspect `currentStateDeltaPlan`. Repeated runs should reuse most unchanged
   nodes and relations. `reused=0` on a polling-only update is a correctness
   defect in the materiality boundary, not a capacity problem.

The 2026-08-31 production proof for `000660` measured 108,298 ms at the
projection boundary: ABox persistence used 48,266 ms and native inference used
42,073 ms. Two read-only replays of the unchanged generation completed in
3,347 ms and 2,398 ms. This establishes ABox write amplification as the first
boundary to remove. Increasing timeout, worker count, or rule parallelism does
not address that cause.

## Fundamental Latency Boundary

TypeDB is the semantic current-state and inference authority. It must not be a
high-frequency copy of every polling lifecycle update.

- QuestDB/MySQL retain raw observations, timestamps, and replay provenance.
- The ABox current-state slots retain rule-visible business values and
  categorical freshness/data states.
- Polling timestamps, session clocks, and provider fetch times do not change an
  ABox row content fingerprint by themselves.
- A changed node invalidates only relations adjacent to that node. Unrelated
  relations in the same scope remain reusable.
- Legacy rows without a semantic fingerprint are rewritten once, then become
  reusable. Inventory reads fetch identity and optional fingerprint in one
  TypeQL query per row owner type.

This is the first safe implementation step because it changes persistence
materiality without moving investment judgement out of TypeDB. Exact source
provenance remains in the durable source snapshot and projection audit.

The completed architecture should add a governed semantic-transition head in
front of projection. Raw value changes become events such as loss-band change,
moving-average crossing, flow-regime change, freshness-state change, new
article identity, or valuation-band change. Only those transitions enqueue an
affected TypeDB fact slice. A per-evaluation receipt may advance provenance
without duplicating an unchanged semantic result slot.

The transition gate must be fail-open until replay proves equivalence: an
unknown dependency, changed RuleBox/TBox release, missing prior head, or data
quality downgrade must execute TypeDB. It must never compute buy, sell, hold,
or reduce actions in Python.

## Acceptance Targets

- Steady-state worker utilization below 0.7 and processing capacity at least
  twice the normal source-event arrival rate.
- Polling-only repeats reuse unchanged rows and do not rewrite unrelated
  relations.
- ABox persistence p95 no longer dominates end-to-end p95.
- The same point-in-time source snapshot produces the same TypeDB inference and
  action envelope before and after transition gating.
- Unknown or changed semantic dependencies fail open to full TypeDB execution.

Adding more reasoning workers is not an acceptance strategy. The active graph
uses a single-world writer contract, so extra workers can increase transaction
contention while preserving the same write amplification.
