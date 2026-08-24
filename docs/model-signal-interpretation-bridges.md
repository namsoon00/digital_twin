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
enabled. They previously generated one TypeDB schema function per policy even
though their source-context predicates had only three forms:

- `stock`: any active stock subject
- `holding`: an active stock with `source == holding`
- `watchlist`: an active stock with `source == watchlist`

TypeDB now deploys one shared function for each source context. Runtime reads
also use those three contexts instead of invoking every simple policy one by
one. TypeDB returns the active `HAS_MODEL_SIGNAL` assertion and its immutable
contract fields; the dispatcher accepts only a registered, enabled, exact
`hypothesisContractId`. It also fails closed when release, validation,
eligibility, family, type, or strength metadata differs from the governed
policy.

The exact account predicates and derivations remain on the interpretation
policy. Fifteen policies with profit/loss, position role, risk budget, or other
account-specific predicates continue through their individual TypeDB query.
The 59 policies whose only residual predicate is the exact model signal use
three bridge reads. This reduces the active catalogue from 116 generated
definitions to 45 physical functions and reduces the model-signal runtime plan
from 74 policy reads to 18 reads at most: 3 shared reads plus 15 constrained
policy reads.

## Compatibility Contract

- Existing `ruleId`, `nativeRuleId`, derivations and inference trace lineage do
  not change.
- A bridge may be shared only when its TypeQL body is byte-identical.
- A shared deployment receipt records every covered rule ID.
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
- `ModelSignalBridge`: the reusable TypeDB source-context function.
- `DEFINES_MODEL_SIGNAL_BRIDGE`: registry to bridge.
- `DEFINES_SIGNAL_INTERPRETATION`: registry to policy.
- `APPLIES_SIGNAL_INTERPRETATION`: bridge to policy.
- `PRESERVES_RULE_LINEAGE`: policy to its existing derivation template.

## Operational Checks

The native reasoning profile and schema-function sync result expose both
logical-rule and physical-function counts. The expected production inventory is:

- enabled logical rules: 116
- enabled model-signal interpretation policies: 74
- shared model-signal bridge functions: 3
- physical TypeDB functions: 45
- eliminated duplicate definitions: 71
- simple runtime policies batched: 59
- constrained runtime policies retained: 15
- simple policy reads eliminated per subject: 56

Any mismatch blocks deployment readiness until the TypeDB function receipts
match the current v17 compiler body. Runtime diagnostics are exposed under
`modelSignalBridgeExecution`, including logical policy count, bridge read count,
constrained policy count, eliminated query count, and ignored unknown contract
IDs.
