# Predictive Model Rule Conversion

## Decision Boundary

Orbit Alpha uses one governed rule catalog with two execution owners:

- The statistical-model control plane evaluates falsifiable market hypotheses.
- TypeDB evaluates semantic, account-policy, data-quality, execution, and
  delivery contracts and owns final relation and action-envelope materialization.

The model control plane cannot emit `BUY`, `ADD`, `HOLD`, `TRIM`, `SELL`, or
`AVOID`. It emits only exact hypothesis evidence. TypeDB must join that evidence
with the current account and policy world before an investment action can exist.

## Converted Inventory

The production catalog contains 118 rules and 225 executable conditions.

| Inventory | Count |
| --- | ---: |
| Predictive hypothesis contracts | 75 |
| Enabled predictive contracts | 74 |
| Deliberately disabled duplicate | 1 |
| Non-predictive TypeDB contracts | 43 |
| Exact model-evidence conditions | 75 |
| Retained private account conditions | 67 |

The governed source catalog remains available for audit and model input. It has
360 raw conditions, including 277 predictive conditions. Production conversion
reduces predictive TypeDB conditions from 277 to 142 and total TypeDB conditions
from 360 to 225. The disabled duplicate is
`graph.holding.trend_transition.risk.v1` and stays disabled after conversion.

## Model Families

Every predictive rule maps to exactly one release and one signal family:

1. `price-path`: path, drawdown, rebound, velocity, and moving-average state.
2. `investor-flow`: foreign, institution, trade-strength, order-book, and volume flow.
3. `cross-asset`: macro, FX, crypto, index, sector, and related-instrument context.
4. `valuation`: company fundamentals, profitability, multiples, and value evidence.
5. `event-response`: disclosure, news, research, corporate action, and market response.
6. `authored-thesis`: approved company thesis, invalidation, and research evidence.

Releases are independently versioned. A release must be production-enabled,
deterministically validated or calibrated, and decision-eligible before its
evidence can enter an investment hypothesis.

## Runtime Flow

```text
source stores and time series
        |
        v
immutable factual market ABox
        |
        v
one indexed model pass in SharedPremiseWorld
        |
        v
ModelHypothesisEvidence
(exact hypothesisContractId + release + source evidence)
        |
        v
TypeDB shared premise
        |
        +-------------------- account position / policy / quality / execution
        |                                           |
        v                                           v
                 TypeDB account resolver and InferenceBox
                                      |
                                      v
                         competing hypotheses and guardrails
                                      |
                                      v
                               AI decision synthesis
```

`PortfolioWorld` consumes the compact shared premise and does not run the model
scorer again. This is important for multi-account scaling: a market change for
one symbol is scored once, then reused by affected accounts.

## Exact-Evidence Contract

A predictive TypeDB resolver contains:

- zero or more account-owned conditions retained from the governed rule; and
- exactly one `HAS_MODEL_SIGNAL` condition targeting
  `statistical-model-hypothesis-evidence` with the original rule ID as
  `hypothesisContractId`.

A broad price or flow signal is useful for diagnostics but cannot satisfy a
resolver. Evidence produced for rule A cannot match rule B merely because both
belong to the same family.

Each converted rule also persists a `modelInputContract`. This is a routing
contract, not another executable TypeDB predicate. It retains the original
price, flow, company, macro, and event dependencies so a change to a source
fact wakes only the model contracts that consume it. The production rule's
`requiredContext` remains limited to the exact model evidence and private
account facts; `triggerDependencies` carries the source-side model inputs.
Both contracts survive a RuleBox graph-store round trip and are repaired once
when an older persisted catalog does not contain the routing metadata.

## Failure and Audit Behavior

- Model failure, missing family features, stale evidence, release mismatch, or
  missing exact contract identity blocks that predictive path.
- The old raw market predicate is not evaluated as a silent fallback.
- The model snapshot stores point-in-time cutoff, feature snapshot, source
  evidence IDs, scorer version, release ID, coverage, and eligibility.
- The TypeDB inference trace stores the exact model contract plus account and
  policy evidence used to form the final relation.
- AI receives the TypeDB-authorized hypotheses and cannot expand their action
  envelope.

## Performance and Complexity

The change reduces TypeDB predicate volume, direct-query complexity, and repeated
account work. The model pass builds one entity/relation index, compiles each
rule's market-owned conditions once, and evaluates all affected contracts over
those immutable structures. Shared-world scoring prevents one market event
from being recalculated for every account.

End-to-end latency is not guaranteed to disappear. TypeDB ABox writes, native
materialization, MySQL persistence, queueing, and AI response time remain
separate costs. Runtime branching becomes simpler, while control-plane
complexity increases because model releases, exact mappings, validation, and
lineage must be governed. Keeping six fixed families and one generated resolver
shape bounds that complexity.

## Change Procedure

1. Add or change the governed rule and assign one knowledge owner.
2. For a predictive rule, map exactly one signal type and one model release.
3. Verify that every non-model condition left in production is account-owned.
4. Replay the point-in-time contract and test missing, stale, and contradictory data.
5. Verify the exact evidence node, TypeDB resolver match, inference trace, action
   envelope, AI context, and blocked path.
6. Bump model, RuleBox, TBox, and native-engine contracts when their semantics change.
7. Measure shared scoring, TypeDB projection, native inference, AI, and total
   latency independently before release.
