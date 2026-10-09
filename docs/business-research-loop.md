# Source-bound business research

The central observation lane now has a business research contract alongside its
price observations. This is a research memory, not a qualified predictive model
or a trading action. Existing TypeDB investment-action governance is unchanged.

## Source to analysis

`ObservationEvidenceSession.business_baseline()` reserves annual and quarterly
reports, verified documents, relationship assertions and company/valuation context
before discretionary AI reads. The reservation is bounded to 26 KB and four facts
per family. The complete inventory, omission counts and snapshot identities remain
in the evidence packet; unavailable data cannot masquerade as a completed review.
The observation evidence profile is v3. Historical v1/v2 packets remain readable.

The v15 author schema captures up to two business theses with the business
question, mechanism, prerequisite, alternative explanation, invalidation condition,
current evidence IDs, missing evidence and a 90–730 day horizon. Numeric
checkpoints bind exact source-referenced `reportedValues`; AI cannot choose a
numerical target. Annual and standalone quarterly observations use the following
year's same reporting period (330–400 days, duration difference at most eight days).
A checkpoint therefore requires at least a 365-day horizon. Cumulative reports,
changed currency/provider/accounting scope and missing report duration do not pass.
Earlier v14 and other frozen prompts/schemas retain their replay builders.

Business theses/reviews may omit price followups and price comparisons. Independent
notification review also reviews their mechanism, alternative and invalidation.
Notification hashes include the structured business content. The dashboard shows
these contracts and metric results, and notifications render the business check.

## Durable feedback

`ai_brain_cases` stores `business-thesis` contracts and their exact registration
baseline independently of whether a notification was sent. The original contract
and source evidence are retained; `ai_brain_case_events` stores registration and
reviews in the same transaction as task completion. At most two active contracts
per account/symbol/world enter mandatory memory. A replacement retires the original
and links a newly registered contract, rather than rewriting its prediction.

Before the next model input is frozen, deterministic code compares new reports to
the registered baseline. Both supporting and opposing metric directions are
retained. Same-period corrections, data fetched after the capture cutoff, newly
backfilled old reporting periods and expired observation windows are not outcomes.
The first observed result is immutable; a later restatement remains new source
context for review, never a rewritten successful test. A metric direction says
nothing by itself about causality, thesis-wide correctness or investment return.
Changed observations and weekly due reviews must be explicitly reviewed using
current evidence. Existing dated company research records also enter continuity.

## Connected companies

The existing source-research use case enriches verified issuer filings before
saving evidence and its durable projection events. Versioned extraction currently
supports explicit English/Korean issuer lists of suppliers, customers and
competitors. Unsupported language, mere co-mention, hypothetical statements and
negation create no relationship. This is deliberately partial extraction; it is
not a claim to cover every supply chain or to infer arbitrary causal connections.

An exact, unique full-name match in the instrument catalog binds a listed issuer.
No suffix stripping, parent/subsidiary merging, ticker guessing or fuzzy matching
is allowed. Unknown and ambiguous names remain visible research leads. A new
source revision or a resolved identity is a new assertion. The local MySQL table
`company_relationship_assertions` is append-only and preserves the first recorded
source excerpt, character offsets, document hash, publication/first-known clocks,
reporting period, direction and identity proof. Source changes and retractions
remove active projection eligibility while historical assertions remain auditable.

Projection adds source-backed `ExtractedClaim` ABox nodes. Resolved assertions
reuse `SUPPLIES_TO`, `SELLS_TO`, `COMPETES_WITH` and `Company` from the existing
TBox, with provenance in KnowledgeWorld. Unresolved names never get corporate
edges. The evidence reservation supplies these claims to central analysis and the
UI displays connected companies as additional research candidates. A dated
relationship does not prove that it still exists today; valid dates and economic
exposure remain unknown unless actually disclosed. No revenue share is invented.
No transitive relation or automatic watchlist entry is created.

Existing saved analyses retain their original input contract. New observation
runs use the new contract; graph assembly uses the v21 cache identity so a prior
cached graph cannot hide the new report lineage. Documentary relationship
enrichment runs when the research use case persists source documents. This
release does not silently backfill all previously cached documents.

The persistence projection also retains each selected stock's financial states,
relationship claims, source links and resolved corporate edges independently of
which relations the current RuleBox consumes. Connected companies do not become
additional rule subjects. Relationship-bearing documents retain exact bounded
proof during input assembly (up to twelve claims and 120,000 source characters);
only excerpts and provenance enter the ABox, not the document body.

## Validation and limits

Contract tests cover forced report inclusion, source-reference and report-basis
validation, misses and corrections, point-in-time exclusions, immutable prompt
replay, scoped review/replacement, source negation, entity ambiguity and the
ABox/provenance boundary. Existing task, agenda, publication and frontend checks
remain required. Storage tests use `orbit_alpha_test`, never production accounts.

The initial change supports one-hop, source-stated candidate discovery. It does
not yet provide unrestricted document extraction, automated cross-company
financial valuation, a multi-hop opportunity ranking, a quantified market
expectations model or a validated investment edge. Orders/backlog/unit economics
need source-specific metric contracts before becoming automatic checkpoints.
Empirical usefulness needs subsequent real filings and user review; tests and
narrative approval cannot supply future evidence.
