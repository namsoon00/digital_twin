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
classification, and validation status. Missing samples identify review work;
they are not proof that a rule is invalid or unused.

## Change Safety

Existing TypeDB catalogs are repaired once when `knowledgeBasis` is missing.
The migration copies only the new governance metadata from the bootstrap
catalog. Conditions, derivations, enable flags, and administrator edits remain
unchanged. The migration result reports `knowledgeBasisUpdatedRuleIds` for
operational audit.
