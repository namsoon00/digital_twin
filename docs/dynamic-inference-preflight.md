# Dynamic Inference Preflight

The V2 reasoning engine must decide whether SharedPremiseWorld work is needed
before it assembles or writes a candidate ABox. This preflight is an
operational routing contract. It does not evaluate an investment rule and does
not create an action.

## Routes

- `REUSE_SHARED`: reuse the active SharedPremiseWorld generation.
- `RUN_SHARED`: assemble the affected source graph, project its scoped ABox,
  and run the affected TypeDB rules plus prior matches.
- `FULL_SAFE`: provenance is incomplete, so use the normal conservative path.

The account overlay still runs after a reused shared generation when private
position, policy, exposure, or decision facts may have changed.

## Reuse Proof

`REUSE_SHARED` is permitted only when all of the following hold:

1. The rule-result slot ledger covers the complete shared rule catalog for
   every requested subject.
2. The slots bind to the current deployment, graph database, native engine,
   TBox fingerprint, and RuleBox hash.
3. The slot source ABox, active ABox pointer, and published InferenceBox source
   generation are identical.
4. The event has an authoritative fact boundary and no shared rule can depend
   on its fact families, or the incoming revision vector exactly matches the
   revision vector already evaluated by the slots.

Before ABox assembly, the router uses fact-family separation only. Exact
dependency-key selection is intentionally deferred until after projection,
because a changed source field can create a derived ABox entity or relation
whose key was not present in the ingress event.

Missing or incoherent proof always falls through to `FULL_SAFE` or
`RUN_SHARED`.

## Context Retention

Rule routing never removes unchanged facts from the active ABox. A selected
TypeDB rule reads the complete active world. Previously matched rules are
included so a former relation can be invalidated, and unexecuted rule states
may be inherited only from one coherent full-catalog result-slot generation.

## Runtime Evidence

Each V2 result exposes `dynamicInferencePreflight` with the route, candidate
rule count, revision-match state, result-slot availability, and reason codes.
This evidence is operational telemetry and must not be rendered as investment
meaning.
