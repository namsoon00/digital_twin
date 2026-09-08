# Valuation Bounded Context

## Purpose

Valuation is an independent domain module that answers one factual question:
"Given the available company, instrument, and market inputs, what fair-value
range does a named, versioned model calculate?"

It does not choose `buy`, `sell`, `hold`, or `reduce`. The calculated facts are
projected into TypeDB, where valuation can be combined with account policy,
price trend, flow, risk, and evidence quality. AI explains the resulting action
envelope but does not invent EPS, multiples, or fair value.

## Package Boundary

The implementation lives in `python_service/digital_twin/domain/valuation/`.

| Module | Ownership |
| --- | --- |
| `contracts.py` | Period, freshness, reliability, scenario, and eligibility contracts |
| `evidence.py` | Earnings and historical/peer multiple observations |
| `models.py` | Deterministic family-specific calculations |
| `registry.py` | Ordered instrument-family to model selection |
| `service.py` | Stable request/result API and calculation lineage |
| `quality.py` | Unit normalization and fail-closed output checks |
| `projection.py` | Valuation facts, traces, missing data, and quality facts for the ABox |

The former top-level valuation modules are compatibility exports only. New
production code must import the bounded context directly.

## Runtime Flow

1. Providers and company-knowledge builders collect raw fundamentals and retain
   provider, period, currency, unit, and observation time.
2. `ValuationModelService` obtains the instrument profile and asks the registry
   for the first applicable primary model.
3. The model calculates bear/base/bull values and a formula trace. A model may
   return missing inputs instead of a price.
4. The quality gate rejects invalid scenario order, impossible prices, stale
   decision inputs, incomplete eligible inputs, and unreviewed AI assumptions.
5. The projection writes model version, input observations, calculation trace,
   result, missing-data facts, and quality state into the ABox.
6. TypeDB rules alone decide whether an eligible valuation relation is relevant
   to an investment hypothesis and allowed action set.
7. Decision synthesis and AI receive only graph-backed eligible evidence plus
   explicit reference-only and blocked evidence.

## Web Read Model

The instrument workspace exposes a dedicated `기업가치` tab backed by
`GET /api/instruments/{symbol}/valuation`. The endpoint reads the latest
monitor snapshot and runs the same `ValuationModelService` used by ontology
projection. It does not call a market vendor, write TypeDB facts, or create an
investment action.

The response keeps three concerns separate:

- `marketMetrics`: observed PER, forward PER, EPS, PBR, PEG, and reporting basis;
- `valuation`: model identity, EPS scenario, evidence-backed or bootstrap PER
  band, fair-value range, safety margins, and quality gate state;
- `sources` and `missingData`: provider lineage, observation dates, and inputs
  that still prevent decision use.

Current provider facts are rebuilt and merged with cached `companyKnowledge`
before display. A positive KIS PER/PBR takes precedence over an older cached
zero, while a broker zero sentinel cannot hide a usable forward multiple from
another provider. Negative EPS remains meaningful and is displayed as
`적자 · PER 산출 불가` rather than being converted into a multiple.

## Model Registry

Primary models are selected by instrument archetype, in this order:

1. Preferred/income securities: dividend and required-yield scenarios.
2. Bitcoin treasury proxies: BTC treasury NAV after debt, preferred claims, and dilution.
3. Semiconductor cyclicals: annual or forward EPS with evidence-backed cycle multiples.
4. Growth and quality equities: annual or forward EPS with evidence-backed growth multiples.

The generic earnings model is a fallback. The current-price anchor is disabled
by default and is reference-only when explicitly enabled. Every row records
`valuationModelId`, `valuationModelFamily`, `valuationModelServiceVersion`, and
`calculationOwner=valuation-bounded-context`.

## Eligibility And Quality

`valuationDecisionEligible=true` requires all of the following:

- a positive fair value and current price;
- compatible annual, TTM, or forward periods for EPS/multiple calculations;
- sufficient inputs and usable freshness;
- an evidence-backed multiple where the model requires one;
- user approval for an AI-originated assumption;
- valid bear <= base <= bull scenario order;
- no blocking data-quality issue.

An analyst target is always reference-only because its internal assumptions are
not available. Bootstrap multiples may produce a visible draft range, but they
remain partial and cannot become action evidence.

Dividend yield has an explicit source-unit contract. Provider values are
accepted only as `ratio` or `percent`, normalized to both canonical forms, and
rejected when the resulting ratio is outside 0 through 1.

## Adding A Model

1. Define the required input observations, periods, currency, and formula trace.
2. Implement a pure calculator in `models.py`; it must not emit an investment action.
3. Add a `ValuationModelDefinition` to `registry.py` with a distinct family and priority.
4. Add tests for selection, complete calculation, missing inputs, stale data,
   malformed units, scenario order, and graph projection.
5. Add or update TBox vocabulary and TypeDB rules only when the new facts change
   investment semantics. A calculation being available is not itself an alert trigger.

## Current Limits

The boundary and safety contracts are production-ready, but fair-value quality
still depends on source coverage. Historical and peer multiple series, forward
consensus ranges, segment cash flows, net debt, dilution, and cycle indicators
must be collected before corresponding models become decision-eligible. The
module intentionally returns partial or unavailable results instead of filling
those gaps with current price or moving averages.

## Validation

Run the focused contract suite with:

```bash
python3 -m unittest python_service.tests.test_valuation_contracts
```

Run the repository handoff suite with:

```bash
npm test
```
