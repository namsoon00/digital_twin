# Company evidence reports

Company reports are source-bound reading documents, separate from graph-backed
`investmentInsight` decisions. Their v3 contract includes a collected company
profile, up to three annual periods, the latest interim and quarterly source
reports, simple comparable financial observations, up to six filings/IR
documents, market context, valuation assumptions and missing evidence.

The default v3 view and new notification messages show a compact captured brief:
one sentence, up to two financial facts, one valuation condition and one next
check. Full AI interpretations, counter-evidence, tables, sources and calculation
assumptions remain in a closed full-report disclosure. Historical v3 reports
without a captured brief use their own saved reading for the compact web view;
already delivered message bodies are not rewritten. Shortening presentation does
not change material fingerprints or resend unchanged reports. Qualified AI prose
is never cut mid-claim to fit the brief; it remains complete in the detail.

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
fixed assumptions and pending-review state remain attached. Stored assumptions
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
profit-to-loss transitions are labeled explicitly.

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

A first report has no change list. Upgrading a delivered older report creates an
`expanded` report and does not claim a new company event. Subsequent source
values, review states and document changes can trigger delivery. Quote movement,
polling clocks and financial cache-revision changes alone do not. Immutable
source references remain in the captured report even when excluded from the
material-change fingerprint.
The report contract version is included in the material fingerprint so a v3
upgrade has its own deduplication identity. AI prose and read availability alone
do not change that fingerprint or bypass existing investment delivery policy.

The reconciliation universe is every distinct holding and watchlist symbol by
default. `COMPANY_CHANGE_REPORT_SYMBOLS` may restrict that universe explicitly.
`COMPANY_CHANGE_REPORT_BATCH_SIZE` limits work and new baseline messages per
worker cycle; it does not cap the total number of covered companies. Companies
without any report job are selected first. Once every company has a report job,
the reconciler uses a time-bucketed rotation so unchanged companies still get
checked without a mutable cursor. Pending jobs are excluded until delivery or
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
