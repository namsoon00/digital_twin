# Company evidence reports

Company reports are source-bound reading documents, separate from graph-backed
`investmentInsight` decisions. Their v3 contract includes a collected company
profile, up to three annual periods, the latest interim and quarterly source
reports, simple comparable financial observations, up to six filings/IR
documents, market context, valuation assumptions and missing evidence.

The default v3 view and new notification messages show a compact captured brief:
a short conclusion, the economic meaning with its numerical basis, one
valuation implication and one next check. Generic collection-change messages
do not replace the interpretation. Financial explanations take priority over
an existing validated AI interpretation, which may discuss price action even
when it captures the same financial revision. The complete AI interpretation
remains in the detail with counter-evidence and invalidation; when no accounting
reading is available it supplies the brief without losing those qualifications.
Tables, sources and calculation
assumptions remain in a closed full-report disclosure. Historical v3 reports
without a captured brief use their own saved reading for the compact web view;
already delivered message bodies are not rewritten. Shortening presentation does
not change material fingerprints or resend unchanged reports. Qualified AI prose
is never cut mid-claim to fit the brief. New automatic messages use the exact
captured change brief, rather than regenerating a generic margin summary.
They name the changed financial items, before/after amounts and comparison
basis, explain their accounting significance and retain concrete next checks.
Cash-flow reversals and borrowing changes cannot disappear behind an unchanged
operating-profit headline. All admitted financial changes share one message.

SEC collection now retains operating unrealized crypto-asset gain/loss concepts.
When a signed gain/loss or positive loss-only concept explains at least half of
same-direction operating results, the accounting reading separates that item
from reported earnings. Revenue, operating results and the adjustment must all
be official, with identical filing identity, period start/end, duration, scope
and currency. This is a decomposition of reported accounts, not an inferred
price cause, normalized earnings measure or investment action. The remainder
is explicitly not cash flow. No ticker, fetched quarter or company-specific
amount is hardcoded. Valuation implications flag asset values, senior claims,
payment capacity and dilution without inventing NAV or a price target.
Missing or incompatible adjustment evidence never produces a crypto-loss claim;
outsized reported margins instead explain why the cause needs decomposition.

The full sections explain comparable accounting observations: how much
revenue remains as operating profit, how much operating cash remains after
capital expenditure, and why net income must be distinguished from operating
income. Recent periods are selected before annual history for each question.
These are arithmetic explanations, not newly inferred business causes or
investment actions. Every explanation retains its metric evidence, reporting
basis, limitations and a concrete next check. Quarterly income is never joined
to cumulative cash flow to compute a ratio.

The read-model application can attach an existing validated AI interpretation.
It reads bounded subject-case and insight history without calling an LLM or
TypeDB. Account, symbol, ABox, generation and candidate fingerprint must match
the latest subject case; publication must have passed, the financial decision
fingerprint and exact source revisions must match, and the analysis must not
postdate the report cutoff. Actionless presentation validation is retained.
The attached interpretation is explicitly dated: matching financial evidence
does not make its market/event commentary a current-price opinion. Missing,
failed, incompatible or directive-bearing interpretations remain unavailable.
This attachment does not create an independent investment dispatch trigger.

Existing eligible primary-model ranges can be explained when model identity,
currency, range order and agreement match. Unreviewed fair values remain in a
closed reference disclosure. Existing reverse-DCF results are conditional
business requirements, never observed market expectations. Their quote time,
fixed assumptions remain attached. The brief admits an inverse calculation only
after assumptions are reviewed and official inputs are ready, with a base period
at least as recent as the report's latest annual evidence. Old or unreviewed
calculations remain reference detail. Stored assumptions
and sensitivity ranges are available in the calculation disclosure.

Both the notification and web detail project the same captured reading. Change
reports distinguish company evidence changes from calculation/review changes;
comparison of linked AI prose does not invent a strengthening/weakening label.
Next checks are required research conditions, not automatic monitoring promises.

`news_intelligence/domain/company_report_evidence.py` selects already-collected
data. It does not fetch sources, call an LLM, fill missing facts or infer why
profits or share prices changed. Financial rows require the existing immutable
report contract. Each metric retains its provider, currency, period, duration,
scope, document identifier and source revision. A row-level official provider
does not make a merged vendor metric official. Comparisons additionally require
matching current values and matching provider/currency/scope/duration; margins
require the same basis for revenue and operating income. Loss-to-profit and
profit-to-loss transitions are labeled explicitly; two negative profit measures
are described as losses. SEC current-only debt concepts are excluded from the
total-borrowing display and change triggers instead of being presented as totals.
General accounting ratios additionally require matching period starts and filing
identities when those fields are present.

IR excerpts retain company wording and are shown only when the captured body
was verified. A verified body is not independent corroboration of the company's
claims. Company profiles and vendor business descriptions remain attributed
collected information. Missing segment, exposure or valuation inputs remain
visible; their absence is not described as an adverse company event.

The notification producer stores the full report with the outbox job and sends
plain structured summary fields. The renderer escapes them once, applies the
company-report identity and links to that saved notification's detail view when
a delivery base URL is configured. The detail adapter returns the report only
for a detail request; the UI renders the captured report instead of substituting
current company data. Reference valuation amounts are inside a closed disclosure
with their inputs and review state, and are omitted from the message summary.

A first report and a format upgrade are quiet reference updates. Model-review
state, document acquisition, profile changes and source enrichment alone never
send a company report. A new official reporting period or a comparable financial
amount changing by at least 10%, including every zero/sign transition, can enter
the delivery buffer. This threshold filters notification noise; it is not an
investment action or severity rule. Same-period changes are labeled corrections,
not quarter-on-quarter performance. New-period growth uses only existing verified
year-on-year comparisons; otherwise comparison direction stays unknown. This
path covers structured accounts, not inferred guidance or document sentiment;
the existing graph-backed investment-insight path retains its own policy.

`COMPANY_CHANGE_REPORT_COALESCE_MINUTES` defaults to 30. A subject's first
eligible change starts a fixed buffer, and the newest report is compared against
the saved baseline when it expires. More updates do not keep extending the wait.
`COMPANY_CHANGE_REPORT_COOLDOWN_HOURS` defaults to 24, measured from successful
delivery, per account and symbol. During cooldown changes accumulate; a reversal
to the baseline cancels the pending change. Queuing is never delivery success.
There is no empty daily message and no new cron job.

One subject-scoped app-store record persists the reference baseline, pending
clock, outbox job and last successful delivery across restarts. A MySQL named
lock serializes reconciliation for that subject. The baseline and cooldown are
advanced only by a completed job; failed or suppressed jobs cannot consume a
change. Dedupe includes the last delivered job identity so A→B→A→B remains
reportable. Initial adoption saves current evidence quietly, with no historical
catch-up flood. If initial financial evidence is empty, the first available
financial evidence quietly establishes the comparison reference. Legacy pending automatic jobs are blocked at final admission;
explicit receipt replay preserves its existing behavior. Delivered bodies are
never rewritten.

The reconciliation universe is every distinct holding and watchlist symbol by
default. `COMPANY_CHANGE_REPORT_SYMBOLS` may restrict that universe explicitly.
`COMPANY_CHANGE_REPORT_BATCH_SIZE` limits work per worker cycle; it does not cap
the total number of covered companies. Time-bucketed rotation includes companies
with quiet baselines and no notification jobs. Pending jobs are excluded until delivery or
retry completes. `COMPANY_CHANGE_REPORT_ROTATION_SECONDS` controls the bucket
duration and defaults to 60 seconds.

Each report labels coverage as `자료 충분`, `부분 확보` or `준비 중`. This
describes only the available report inputs. It must not be presented as a
judgement about company quality, investment merit or risk.

Validation extends `test_instrument_valuation_query`, the notification
presentation boundary tests and `insight-presentation.test.mjs`. Fixtures cover
baseline/upgrade/change behavior, period/source comparison boundaries, malicious
source text and URLs, one-time HTML escaping, reference-price disclosure and
saved report links. Tests do not require Telegram or vendor credentials.

## Temporal research records and accounting bridges

The report now decomposes changes only when both official statements share
provider, currency, scope and duration, each period has one filing identity,
and the periods are comparable year over year. Net income reconciles to pretax
income minus tax provision; free cash flow reconciles to operating cash flow
minus capital expenditure. A nonzero unexplained residual withholds the bridge.
These are accounting contributions, not established business causes or normalized
earnings. Tax expense is not cash tax, and capital expenditure is not part of CFO.
Vendor month-end aliases within 14 days of an official annual end date occupy one
history slot; the higher-quality complete row wins without mixing its metrics.

The SEC submissions collector prioritizes the latest annual and quarterly reports
before other recent forms, including reports outside the first twenty filings.
`sec.document` stores versioned, bounded, verbatim explanatory passages from the
whole HTML document, preserving sentence qualifications and passage hashes.
The selected passages are partial evidence, not a full-document review. They enter
the existing official research evidence and ontology projection path; no new
investment action or hypothesis is authored by the selector. A report bridge can
show an issuer statement only from the exact matching filing and reporting date.

Each account/symbol report state has a separate `researchRecord`, independent of
the successful-delivery baseline. First registration freezes its real observation
clock, official metric provenance, questions and any already validated linked AI
interpretation. It does not backfill predictive outcomes. Later records distinguish
new reporting periods, same-period revisions, and evidence/interpretation updates.
New-period arithmetic is shown only against comparable prior-year windows; a new
quarter is not compared with an annual total. Publication and source observation
clocks after the snapshot cutoff are excluded, and an older snapshot cannot rewind
the record. Repeated polling does not create a new revision.

The original baseline remains durable. The latest 24 revisions are retained, with
an explicit count of older omitted revisions. This is research continuity, not a
complete historical replay archive. New observed figures require review; they do
not mark a hypothesis correct or incorrect. The web shows actual registration
state and review history. Reads never register a case or claim a notification was
sent. The existing coalescing/cooldown and successful receipt policies still own
notification delivery.

The AI continuity packet includes dated original/current questions and previously
validated interpretations through an injected read port. The memory is bounded by
the frozen analysis cutoff and explicitly carries no action or hypothesis
qualification authority. Current numerical facts continue to come from the frozen
ABox source context. No synthetic future observation is written to operational
records during verification.

Validation: `test_company_report_reading` covers reconciled contributions,
provider/currency/filing/duration mismatch, first registration, duplicate polling,
corrections, later reports, future-data exclusion, persistence across service
instances, read-model display, and AI continuity compression. SEC extraction tests
check report selection beyond recent ownership filings and intact qualification
sentences after long cover pages.
