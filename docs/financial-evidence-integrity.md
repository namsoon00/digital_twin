# Financial Evidence Integrity

## Failure Chain

Reporting timestamps were sorted using their last eight digits. This ranked
March 31 ahead of June 30 and selected the wrong current financial ABox state.
The OpenDART full-statement endpoint does not supply the date fields expected
by the old parser. The collector also discarded interim comparison columns
and truncated statements after 180 rows. Finally, prompt compression removed
the values underlying some financial rule labels.

These are input and evidence-lineage defects, not a reason to loosen action
authorization or empirically qualify an untested hypothesis.

A subsequent production audit found another compression path: research-only
reviews omitted `companyEvidence` and kept just four contextual citations.
The stored brief and rendered financial table were complete while the model's
actual prompt was not. Helper-only tests did not detect that mismatch. The
v28 prompt path retains financial packets and citation rows through final
rendering, with an equality check at the fitting boundary.

## Contracts

- Parse reporting dates before timestamp offsets; select the latest actual
  period independently of provider row order and history length.
- Keep zero and missing distinct. Growth needs a nonzero comparison value,
  chronological adjacent periods, source lineage and compatible units/scope.
- Quarterly yfinance growth is quarter-over-quarter. OpenDART interim income
  is a three-month amount compared with the prior-year matching quarter.
  Interim cash flow is cumulative. Do not mix quarterly income and cumulative
  cash flow in a conversion ratio.
- Preserve OpenDART `thstrm_add_amount`, `frmtrm_q_amount`, and
  `frmtrm_add_amount`, along with every financial statement row.
- Issued, outstanding, treasury and weighted-average share counts are not
  substitutes. A secondary-vendor share-count discontinuity of 25% or more
  remains visible as a data-quality exception, not an actionable dilution
  metric. This threshold checks vendor consistency, not investment merit.
- Choose official current observations where comparable, but never combine an
  official current value and a secondary prior value to manufacture growth.
  Each metric/comparison retains its provider, reporting period, basis,
  source URL, receipt and exclusions. Official coverage of a company does not
  make its secondary-vendor valuation multiples official.
- Preserve a valid same-period ratio computed from one provider when an
  official observation replaces only its numerator or denominator. Retain
  both original inputs and the formula; never compute a cross-provider ratio
  or discard valid positive evidence merely because an official field arrived.
- The stock's current financial ABox state and AI `financialEvidence` packet
  share this contract. Compression must retain the comparison packet; numeric
  evidence IDs allow AI to cite it instead of citing only a rule title.
- Research-only, normal and minimum-budget prompts retain the same complete
  financial packet, including excluded comparisons and paired-ratio inputs.
  Reserve the comparison/ratio citation IDs before limiting the evidence
  ledger. Final fitting checks packet equality and citation value, period and
  source equality; insufficient space is a budget error, never missing data.
  The queue can then use its existing larger-budget retry path.
- Keep historical comparison-quality issues in storage for audit. The current
  AI context includes current-period exclusions and provider parsing errors,
  not historical anomalies mislabeled as current financial weakness.
- A financial evidence revision can trigger one fresh insight, even when its
  directional label is unchanged. Reused evidence is background, not a newly
  published filing. Invalid AI output cannot use this novelty path to publish.
- Both TypeDB and AI customer documents show the reporting period, comparison,
  source and available original links when the evidence is first observed,
  revised or moves to a new period. Compact web details retain these rows.
- Every normalized financial period carries a `financial-report-observation-v1`
  contract. It preserves period start/end, annual/quarterly/YTD basis,
  consolidated/separate scope, currency, fiscal year, accounting standard,
  publication time, filing ID and the exact immutable external-data revision.
  Unknown publication or correction state remains unknown; collection time is
  never presented as a filing time. Exact lineage remains in fact/audit
  revisions, while a lineage-only vendor payload change does not create a new
  material company-fact reasoning turn when normalized statement values and
  report semantics are unchanged.
- Consensus snapshots retain their target horizon, exact observation time,
  sample state and immutable analyst-dataset revision. A missing analyst count,
  low/high estimate or 30-day revision is absent rather than zero. Negative EPS
  estimates remain valid observations but cannot enter a positive-PER valuation.
  A range reported by one provider is labelled `reported-consensus-range`;
  dispersion between independent point estimates is only an
  `observed-point-range` and is not presented as a provider-reported band.
- The v29 prompt carries `financialEvidenceUse` through final compression:
  reused, first-observed, revised and new-period are evidence-continuity
  states, never proof of a newly published filing. The captured delivered
  insight supplies the baseline, with prior analysis as an explicit fallback.
  Reused financials remain available to reasoning and audit, but customer alerts
  show one unchanged-premise row without repeating metrics, providers or source
  links. The verified market change appears before that premise. Full financial
  detail remains available through the web detail view. Customer documents
  separate the financial premise, verified market change and AI interpretation.
- Financial improvements and price recovery are co-observations, not proof
  that financial results caused a price move. Prompt instructions and bounded
  claim-repair checks reject unsupported factual attribution without turning
  a supported positive interpretation into a bearish or missing-data claim.
  The price/financial guard covers known wording regressions, not arbitrary
  natural-language causality proof. Source-backed event interpretation remains
  subject to the existing evidence/claim contract.
- An analysis-trigger watch cannot claim to strengthen or weaken the entire
  investment thesis. Such a watch requests neutral reassessment and retains
  the AI-authored purpose for audit. Registration receipts still govern every
  automatic-tracking promise.

OpenDART field definitions:
https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020

## Operational Repair

Preview (no vendor calls or writes):

```bash
python3 python_service/service.py maintenance financial-evidence --limit 3000
```

Apply during a controlled worker restart:

```bash
python3 python_service/service.py maintenance financial-evidence --limit 3000 --apply
```

The operation reconstructs current company facts from stored provider inputs.
It uses retained compressed AI execution artifacts to identify legacy
financial comparisons requiring source revalidation. It does not pretend to
replay missing original inputs or apply today's prices to past predictions.

In one transaction it supersedes in-flight legacy financial AI requests through
the existing queue owner and event contract, cancels affected pending AI watches, excludes affected
pending company-hypothesis targets and marks corresponding calibration
outcomes ineligible. Original decisions, AI text, observations and directional
outcome labels remain unchanged. Correction metadata preserves the prior
calibration eligibility. A separate durable audit record lists affected cases.
Concurrent source/cache updates abort the repair instead of being overwritten.
An official-data refresh is queued through the existing rate-limited collector.

The audit also reports 24-hour graph entry candidates, permitted BUY/ADD cases
and cases with execution-qualified hypotheses. These distinguish a broken
candidate path from an empirical-qualification wait. No rule, qualification
threshold, active engine release, or action permission is silently promoted.

## Acceptance Checks

- Q2 sorts ahead of Q1; non-December fiscal quarters remain chronological.
- Missing quarter/current amount and incompatible share bases exclude growth.
- Full-statement rows after 180 and interim comparative columns survive ingest.
- Old document extraction cannot overwrite newer company-statement ownership.
- Excluded financial comparisons remain auditable without becoming rule values.
- Current ABox facts, prompt compression and financial evidence IDs agree.
- Test the rendered `DecisionCore`, not just intermediate company helpers.
  Exercise crowded ledgers, research-only compression, minimum-budget
  compression, excluded comparisons, missing company data and failed retention.
  Replay retained execution briefs without rewriting old decisions or sending
  historical investment messages as new alerts.
- Evidence corrections notify once; repeated identical evidence does not.
- Financial wording remains idempotent and respects above/below-average facts.
- Repair preview is read-only; repeated current-state rebuilds are idempotent.
- Existing positive BUY/ADD authorization tests continue to pass; shadow
  hypotheses do not gain execution authority merely to increase alert volume.

Remaining observation work is not a software pass/fail result: long-horizon
hypotheses still need independent future outcomes, and excluded historical
comparisons require original point-in-time evidence before any reinstatement.
