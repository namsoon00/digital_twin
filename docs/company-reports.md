# Company evidence reports

Company reports are factual reading documents, separate from graph-backed
`investmentInsight` decisions. Their v2 contract includes a collected company
profile, up to three annual periods, the latest interim and quarterly source
reports, simple comparable financial observations, up to six filings/IR
documents, market context, valuation assumptions and missing evidence.

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

A first report has no change list. Upgrading a delivered v1 report creates an
`expanded` report and does not claim a new company event. Subsequent source
values, review states and document changes can trigger delivery. Quote movement,
polling clocks and financial cache-revision changes alone do not. Immutable
source references remain in the captured report even when excluded from the
material-change fingerprint.

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
