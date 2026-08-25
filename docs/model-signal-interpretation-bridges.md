# Model Signal Interpretation Bridges

## Purpose

Statistical investment models and ontology inference have separate ownership:

1. A statistical model evaluates time-series and market features.
2. It writes an exact `HAS_MODEL_SIGNAL` evidence contract to ABox.
3. TypeDB verifies that evidence contract together with current account context.
4. The existing rule lineage materializes the authored ontology relations and inference trace.

The model does not create an investment action by itself. TypeDB remains the
authority that decides whether the model evidence is applicable to the current
world and account.

## Shared Bridge Design

The RuleBox has 75 model-signal interpretation policies, of which 74 are
enabled. Their source-context predicates have only three forms:

- `stock`: any active stock subject
- `holding`: an active stock with `source == holding`
- `watchlist`: an active stock with `source == watchlist`

Runtime planning groups compatible policies into one direct TypeQL read for
each source context instead of invoking every simple policy one by one. TypeDB
returns the active `HAS_MODEL_SIGNAL` assertion and its immutable
contract fields; the dispatcher accepts only a registered, enabled, exact
`hypothesisContractId`. It also fails closed when release, validation,
eligibility, family, type, or strength metadata differs from the governed
policy.

The exact account predicates and derivations remain on the interpretation
policy. Fifteen policies with profit/loss, position role, risk budget, or other
account-specific predicates continue through their individual TypeDB query.
The 59 policies whose only residual predicate is the exact model signal use
three bridge reads. This reduces the model-signal runtime plan from 74 policy
reads to 18 reads at most: 3 shared reads plus 15 constrained policy reads.

## Compatibility Contract

- Existing `ruleId`, `nativeRuleId`, derivations and inference trace lineage do
  not change.
- A bridge may be shared only when its source-context TypeQL shape is identical.
- Every batched result retains all covered rule IDs and exact contract IDs.
- Missing or unsupported residual conditions fail closed; Python never
  substitutes an investment decision.
- Python performs contract routing and integrity validation only. Thresholds,
  account predicates, and investment meaning remain TypeDB/RuleBox concerns.
- Unknown, disabled, missing, or metadata-inconsistent contracts never create
  a rule match.
- The verified active Manifest still scopes every source, evidence relation and
  account fact used by the interpretation query.

## TBox Concepts

- `ModelSignalInterpretationPolicy`: the durable meaning, exact signal contract,
  account context and derivation policy.
- `ModelSignalBridge`: the reusable direct-TypeQL source-context query group.
- `DEFINES_MODEL_SIGNAL_BRIDGE`: registry to bridge.
- `DEFINES_SIGNAL_INTERPRETATION`: registry to policy.
- `APPLIES_SIGNAL_INTERPRETATION`: bridge to policy.
- `PRESERVES_RULE_LINEAGE`: policy to its existing derivation template.

## Operational Checks

The native reasoning profile and runtime batch plan expose logical-rule and
direct-query counts. The expected production inventory is:

- enabled logical rules: 116
- enabled model-signal interpretation policies: 74
- shared model-signal bridge query groups: 3
- simple runtime policies batched: 59
- constrained runtime policies retained: 15
- simple policy reads eliminated per subject: 56

There is no generated-function deployment or receipt gate. Runtime diagnostics
are exposed under `modelSignalBridgeExecution`, including logical policy count,
bridge read count, constrained policy count, eliminated query count, and
ignored unknown contract IDs.
