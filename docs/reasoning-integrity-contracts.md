# Reasoning Integrity Contracts

## Ownership

TypeDB owns rule matching, provenance, and action authorization. The hypothesis
manager freezes those results; it cannot invent an allowed action. AI interprets
eligible evidence through that same action contract. Outcome observation measures
later results and never changes an old investment decision or places an order.

The domain layer owns the contracts below. Application services orchestrate them.
The MySQL evidence adapter reads immutable, account-scoped historical data only.

## Action Authorization

`GraphActionAuthorization` is shared by decision synthesis and AI admission.
The first explicit action set wins, including an empty set. The graph's
`aiAllowedActions` takes precedence over its general execution-policy field;
blocked actions are always removed. Neither an empty set nor a missing action
becomes implicit permission. Synthesis v8 carries the resolved set downstream.

## ABox Lifecycle

Partial projections inspect all retained relation scopes referencing changed
shared endpoints, including scopes owned by another symbol/account overlay.
Missing shared endpoints request one complete assembly of the same immutable
source. The system still persists a bounded target patch, not a whole-world write.

Endpoint-only invariant failures in an incomplete source permit this repair.
Other invariant failures stay blocked. The repaired plan must pass the full
invariant check before activation. Failure receipts retain both attempts.

## Claim Calibration

Qualification matches account, symbol, claim ID, and a content fingerprint of
the entire claim, outcome criteria, qualification policy, and authored rule
fingerprint. Rule-claim v3 fingerprints include conditions, derivations, rule
version, and model routing. A family or template name cannot transfer performance
to another predictive claim. Unknown legacy fingerprints cannot qualify it.

Past episodes and decisions remain unchanged. Newly projected calibration facts
are derived from their frozen claims. Old v2 release definitions remain v2;
register a new immutable release to adopt v3 definitions. Existing price-only
contracts are explicitly prediction-performance-only, never causal validation.

## Outcome Observation

An episode freezes its financial reporting periods and technical baseline at
inference time. Prediction and premise results are stored separately:

- `predictionStatus`: observed price response, using market-relative returns for
  trend, event, and cross-asset contracts where a distinct benchmark exists.
- `thesisValidationStatus`: subsequent evidence supports or contradicts the
  authored premise, is unavailable, or was not measured by that contract.
- `causalAttribution`: always `not-established`. Matching observations alone
  do not establish that a news event or financial change caused a price move.

Recovery contracts measure the change in distance to the 20-day average. Flow
contracts require available investor-coverage metadata and observed foreign and
institutional volumes. Missing flow is not zero net buying.

Company premise contracts are rule-specific: revenue growth and cash-flow margin,
share dilution with deteriorating cash flow, operating-income deterioration, or
absent revenue growth. A valuation-only rule is not assigned an unrelated earnings
test. Financial criteria require a later period at the same reporting frequency,
with 90/180-day horizons; re-fetching the same report is not new evidence.

Financial evidence comes from the latest retained monitor snapshot at or before
the price observation, with a one-day lookup bound. Requests are batched. Current
mutable company data and external API backfills cannot leak into historical
evaluation. Missing historical evidence remains an explicit data gap rather
than fabricated success. The baseline survives normal snapshot retention.

## Engine Comparisons

Comparison input v2 requires identical source scope, snapshot ID/time, payload
hash, accounts, and symbols. Sharing an event ID is insufficient. SQL pairing and
the application boundary both enforce this contract.

Promotion statistics count independent physical execution pairs. Multiple rows
from one batch do not increase the sample count. Aggregation preserves coverage
and the worst failure/parity result in the batch. Delivery violations remain
visible even on legacy, rejected, or warmup rows.

Legacy comparisons remain auditable but do not count as v2 validation evidence.
Reconciliation appends comparisons under the new contract identity. It does not
rewrite old decisions, manufacture a passing cohort, or bypass release promotion.

## Validation and Rollout

Focused tests cover explicit grant/denial, shared endpoint replacement, exact
claim revisions, price-versus-premise results, point-in-time financial enrichment,
batch comparison deduplication, and immutable release round trips. The normal
`npm test` suite remains required. Smoke fixtures disable unmocked external
providers and supply their own currency/account settings.

Runtime fixes take effect after the managed workers restart. New v3 RuleBox
definitions are introduced through `reasoning-engine register-v2-release` into an
isolated candidate. Promotion must use independently paired validation evidence;
old same-event-only comparison counts are not sufficient. Financial outcome
quality still requires actual later reporting periods, not an immediate replay
of the same report.
