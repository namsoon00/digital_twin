# Ontology Rule Knowledge Governance

## Purpose

The versioned statistical-model control plane evaluates falsifiable market
hypothesis contracts against an immutable point-in-time ABox. TypeDB combines
the resulting exact-contract evidence with account, policy, quality, and
execution facts and materializes the final semantic relation. Knowledge
governance records why each contract exists, what judgement it may influence,
and whether the result is a competing investment hypothesis or a decision
guardrail. Neither the model scorer nor governance chooses a final investment
action directly.

## Rule Roles

| Role | Meaning | Creates a hypothesis | Decision use |
| --- | --- | --- | --- |
| `predictive-hypothesis` | A falsifiable market or company thesis | Yes | Conditional until outcome replay validates it |
| `policy-constraint` | Account mandate, risk budget, or portfolio limit | No | Guardrail only |
| `execution-gate` | Liquidity, order book, or executable-capacity limit | No | Guardrail only |
| `data-quality-gate` | Freshness, provenance, coverage, or conflict contract | No | Guardrail only |
| `context-observation` | Descriptive market or instrument context | No | Reference only |

This boundary prevents a freshness rule, cash-limit rule, or repeated time
window from being counted as another independent bullish or bearish thesis.

## Bounded-Context Ownership

Every production RuleBox ID is listed exactly once in
`domain/ontology_rule_ownership.py`. Production rules are never assigned from
tokens in a label or ID. A new or renamed production rule therefore fails the
catalog audit until its owner and contracts are reviewed.

| Owner | Owns | Allowed decision authority |
| --- | --- | --- |
| `statistical-model` | Empirical price, flow, event, valuation, and cross-asset hypotheses | Emits governed model signals; TypeDB maps only calibrated eligible signals to hypotheses |
| `market-observation` | Numeric thresholds and deterministic state transitions | Emits material market events; never proposes a trade |
| `ontology-semantic` | Stable meanings and relationships between verified facts | Adds semantic context; never invents an empirical threshold |
| `portfolio-policy` | Mandate, concentration, cash, exposure, and account limits | Constrains actions without forecasting price |
| `data-quality` | Freshness, provenance, coverage, and conflicts | Blocks or weakens evidence eligibility |
| `trade-execution` | Capacity, liquidity, order book, and slippage | Constrains executable actions |
| `notification-policy` | Delivery materiality and deduplication | Controls delivery only; never rewrites the investment opinion |

The ownership contract records `inputContract`, `outputContract`,
`decisionAuthority`, and `migrationDisposition`. Operator-authored candidate
rules receive a sandbox-only owner while being validated. They are not
production rules until an explicit catalog ID is approved.

## Statistical Model Contract

All 75 predictive-hypothesis rules use the production model contract path.
Seventy-four are enabled; one known duplicate remains disabled. The remaining
43 rules are semantic, account-policy, data-quality, execution, or delivery
contracts and intentionally remain TypeDB-owned.

1. An immutable point-in-time feature snapshot and factual market ABox are
   assembled from source stores.
2. One indexed pass evaluates the market-owned conditions of every enabled
   predictive contract across six model families: price path, investor flow,
   cross asset, valuation, event response, and authored thesis.
3. A matched contract emits `ModelHypothesisEvidence` carrying the exact
   `hypothesisContractId`, source evidence IDs, model release, feature snapshot,
   eligibility, and knowledge cutoff.
4. A broad family signal without an exact contract ID is diagnostic only and
   cannot satisfy another rule.
5. TypeDB joins exact model evidence with private account conditions and then
   materializes the relation, trace, hypothesis, guardrail, and action envelope.
6. A missing, failed, stale, or ineligible model result fails that predictive
   path closed; TypeDB does not fall back to the old raw market predicate.

The raw governed predictive catalog remains the versioned model input contract
and audit source. The executable RuleBox is generated from it and contains only
private/account predicates plus one exact model-evidence predicate per
predictive rule. See `docs/predictive-model-rule-conversion.md` for the runtime
boundary and release procedure.

Crypto thresholds illustrate the ownership split. The market-observation
module classifies the configured 24-hour and 7-day thresholds into a stable
event such as `crypto-market-24h-down-watch`. TypeDB consumes that event to
create semantic support or risk context. It no longer repeats the numeric
threshold comparison.

## Knowledge Basis Contract

Every executable rule carries `knowledgeBasis` with these audit fields:

- `theoryFamily` and `thesisFamily`: the economic mechanism and concrete thesis.
- `basisOrigin`: whether the metadata was explicitly authored or catalog-derived.
- `thresholdOrigin`: policy, observed context, or an authored heuristic.
- `validationStatus`: approved contract, reference only, or replay required.
- `decisionEligibility`: conditional, guardrail only, or reference only.
- `evidenceIndependenceKey`: the family used to avoid double-counting overlapping evidence.
- `plainLanguageBasis`, `applicability`, and `references`: operator-facing rationale and limits.
- `requiresHypothesis` and `outcomeValidationRequired`: the hypothesis and validation boundary.
- `owner`, `inputContract`, `outputContract`, `decisionAuthority`,
  `migrationDisposition`, and `ownershipContractVersion`: the bounded-context
  boundary and transition state.

Bootstrap classification is deliberately conservative. Predictive thresholds
remain `authored-heuristic` and `replay-required` until a reproducible replay
or prospective outcome sample supports promotion. A research citation never
validates a project-specific threshold by itself.

## Hypothesis and Guardrail Flow

1. A data event updates the affected immutable market ABox.
2. The dependency router selects relevant model contracts and the model control
   plane emits exact, eligible contract evidence once in `SharedPremiseWorld`.
3. TypeDB evaluates dependency-selected resolver, policy, execution, quality,
   and semantic functions in the shared and account worlds.
4. Matched inference rows retain the rule's `knowledgeBasis`, model release,
   source evidence, and TypeDB trace lineage.
5. `predictive-hypothesis` matches enter the competing hypothesis set.
6. Policy, execution, quality, and context matches become decision guardrails.
7. Hypothesis readiness counts independent evidence keys, not raw rule or time-window counts.
8. The AI compares only eligible hypotheses within the TypeDB action envelope.
9. Outcome observation and replay may support a later governance promotion; runtime performance never edits a rule automatically.

## Operator Review

The Ontology Catalog rule view exposes filters for rule role, theory family,
and validation state. Selecting a rule shows its explanation, threshold
origin, hypothesis eligibility, references, and the complete lineage from
TBox relations through inference, decision, and notification.

The rule audit adds runtime sample counts, match counts, latency, knowledge
classification, model release status, signal availability, and concrete
promotion blockers. Missing samples identify review work; they are not proof
that a rule is invalid or unused.

## Change Safety

Existing TypeDB catalogs are repaired once when `knowledgeBasis` is missing or
its ownership contract is stale. Metadata-only migration preserves conditions,
derivations, enable flags, and administrator-authored rationale. A known
runtime input-contract change, such as the crypto market-event migration,
replaces the incompatible rule shape while preserving the enable flag. The
migration result reports `knowledgeBasisUpdatedRuleIds`,
`ownershipContractUpdatedRuleIds`, and `rawAboxRuntimeUpdatedRuleIds` for
operational audit.
