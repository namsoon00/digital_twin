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
