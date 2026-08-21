# Ontology Rule Knowledge Governance

## Purpose

TypeDB decides whether an authored rule matches the active ABox. Knowledge
governance does not re-evaluate those conditions and does not choose an
investment action. It records why the rule exists, what judgement it may
influence, and whether a match is a competing investment hypothesis or a
decision guardrail.

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

## Statistical Transition

Predictive rules currently retain their raw-fact TypeDB authority until all
promotion gates pass. The migration is deliberately visible rather than
silent:

1. An immutable point-in-time feature snapshot is assembled from the time-series store.
2. Each model family produces and stores its own immutable signal snapshot.
3. A read-only signal bundle carries the independently versioned releases into the ABox.
4. `ModelSignalObservation`, `StatisticalModelRelease`, and
   `SignalEligibilityAssessment` preserve scorer, feature, validation, and
   source lineage.
5. Disabled candidate TypeDB rules consume only signals marked `calibrated`
   and `eligible`.
6. Point-in-time replay, minimum outcomes, calibration, economic utility,
   action-envelope parity, and latency must pass before a candidate replaces
   the raw-fact rule.

Price-path and investor-flow scorers are implemented as shadow releases.
Cross-asset, valuation, event-response, and authored-thesis releases remain
explicitly blocked until their point-in-time feature contracts and scorers
exist. Shadow scores are reference-only and never produce a probability or
expand the current action envelope.

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

1. A data event updates the active ABox.
2. TypeDB evaluates only dependency-selected RuleBox functions.
3. Matched inference rows retain the rule's `knowledgeBasis` and lineage IDs.
4. `predictive-hypothesis` matches enter the competing hypothesis set.
5. Policy, execution, quality, and context matches become decision guardrails.
6. Hypothesis readiness counts independent evidence keys, not raw rule or time-window counts.
7. The AI compares only eligible hypotheses within the TypeDB action envelope.
8. Outcome observation and replay may support a later governance promotion; runtime performance never edits a rule automatically.

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
