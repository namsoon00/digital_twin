# Calendar, News and Disclosure Reliability

## Ownership and Boundaries

- `news_intelligence` builds a read-only information brief from retained text and the existing claim ledger. It does not approve a claim or alter prompt admission.
- `market_data` collects official release documents through the existing leased external-data worker. Provider budgets, rate limits, retries and circuit breakers remain centralized.
- `investment_calendar` matches a stored result to an official schedule by country, indicator and local publication date. Calendar GET requests never call a provider.
- `read_models` preserves the information brief for the market evidence feed. Web detail and compact summaries consume the same brief.
- The new calendar datasets have no investment authority. They neither emit material investment events nor enter the legacy investment snapshot. Hypotheses, rules, AI decision policies and notification thresholds are unchanged.

## News and Disclosures

Each displayed fact must match a retained source excerpt. Claims scoped to another symbol or document, rejected/conflicted claims, withdrawn records, future publications and current title/body conflicts cannot become displayed facts. Truncated excerpts are matched before rendering, and source hashes and character offsets remain available for audit.

`기사에 기재` means the article contains the statement, not that the statement is independently proven true. `공식 문서 기재` likewise describes document provenance, not an investment conclusion. Independent source counts default to zero, never an invented one.

AI summaries and opinions are separate from source statements. A known news-analysis source hash mismatch suppresses the old analysis without deleting the original record. Metadata-only disclosures remain visible as metadata, not full-document verification. Financial facts and market-move records retain their existing specialized presentation.

New news/disclosure digests hydrated from canonical evidence use the same source-bound brief. Their fact section no longer falls back to generic topic statements or numbers without context. Existing admission, deduplication and delivery policies are unchanged. Previously sent messages are not rewritten; legacy compact events without a retained canonical record retain their compatibility path.

Detail reads show same-symbol, same-kind records sharing an explicit story identity. Similar titles alone never establish a common event. Existing lifecycle and correction metadata remain intact; no historical rows are rewritten by this feature.

Follow-up suggestions are labeled `자동 관찰 미등록` unless an actual observation contract is connected. This feature does not claim to monitor an unregistered suggestion.

Missing historical SEC/OpenDART bodies are scanned by stable evidence ID in bounded pages every 15 minutes. Recovery reuses the existing document partitions, provider limits, immutable revisions and canonical projector. It does not implement another downloader or AI engine. Legacy records missing an explicit receipt/accession field are recoverable only when the official URL and canonical evidence ID agree exactly. Title similarity never supplies a document identity.

A retained usable document is replayed without an old-news alert, with at most one potentially slow AI replay per scan. Remaining bodies enter the existing leased queue; a known permanently unavailable official file is not repeatedly downloaded. Source-bound analysis reuse still requires the exact document hash and current prompt version. New collection cannot certify an old document as known before its actual collection. SEC collection requires the configured contact email; the recovery worker never invents one.

## Official Calendar Results

Initial coverage:

| Dataset | Source | Values |
| --- | --- | --- |
| `official.bls-release` | BLS official CPI and employment releases | CPI monthly/yearly changes, prior monthly change when explicitly reported, payroll change, unemployment rate |
| `official.fomc-release` | Federal Reserve calendar and its published statement link | Federal funds target range |
| `official.bok-release` | Bank of Korea homepage policy-statement link and statement | Korean policy rate, explicit prior rate and change |
| `official.bls-statistics` | BLS Public Data API, no registration key | Latest CPI monthly/yearly changes, payroll change and unemployment level, separate from release results |

The parser verifies the official host, publication date/time, reference period and exact value excerpt. FOMC links are discovered from the official calendar; the URL date must agree with the document date. A future statement, access-denied page or unrecognized value format is a collection error, not a release with empty or guessed values.

Current observations and immutable revisions use `external_fact_current` and `external_fact_revision`; the existing configured revision-retention policy applies. Source date, actual publication time where provided, first collection time and last successful collection time are distinct. A later source failure does not erase a previously captured historical result. Previously published data are not retroactively treated as known before collection.

Only the release on the event's local date is joined: New York for US events, Seoul for BOK events. A July decision never populates September's meeting. BOK statement title and registration dates must agree. Where only a date is known, no publication clock is invented and intraday reaction is unavailable. Operational reminder times are labeled separately from officially supplied publication times. Historical event detail reads use the exact stored ID, not a search limited to the future calendar window.

The BLS statistics adapter batches four series in one request: `CUSR0000SA0`, `CUUR0000SA0`, `CES0000000001`, `LNS14000000`. Calculations retain series IDs, periods, raw values and formulas. Missing comparison periods, duplicate series, nonfinite values and future observations are errors; explicit unpublished `-` values are skipped. CPI uses the seasonally adjusted index for monthly change and the unadjusted index for yearly change. Payroll levels are reported in thousands and their difference is converted to people. Rendering revalidates the calculations against the retained source hash.

These latest statistical vintages may contain revisions. They are shown in a separate section with the reference period, collection time and cache age, never as the event's original release or a pre-release expectation. A cache older than 12 hours is visibly stale. This official public API is not a proxy for the BLS release website and does not change its independent error/circuit state. See [BLS API documentation](https://www.bls.gov/developers/api_signature.htm) and [seasonal adjustment revisions](https://www.bls.gov/cpi/seasonal-adjustment/using-seasonally-adjusted-data.htm).

No market consensus is collected in this scope. Prior-period actuals are not relabeled as consensus; no surprise score or market-direction judgment is fabricated. BEA, other Korean macro indicators and company-specific schedule results still require separate source adapters and are visibly marked unsupported.

## Event-Window Prices

News/disclosure and released-calendar detail reads compare existing stored prices immediately before publication with observations at the 1-hour and 24-hour horizons. The service consumes the versioned time-series interface, reads only the shared market account and strips all account fields from its output. It performs no vendor requests, orders, hypothesis updates or notification registration.

A baseline must have been observed and recorded by publication, no older than 24 hours. The outcome must fall between the horizon and three hours afterward, with the actual quote times displayed. Daily bars, synthetic/invalid data, missing timestamps, nonpositive prices and mismatched currencies/providers cannot supply a comparison. Missing history remains missing; a present quote is never substituted into the past. Volume is not compared because cumulative intervals are not equivalent.

The comparison describes price movement across a time window, not a causal effect attributable to the event. Detail reads refresh on reopening after the one-minute cache expires. This is **read-time observation**, not background follow-up alerts. Exact publication time and retained intraday history are prerequisites; older events may have no usable observations because existing retention rules still apply.

Initial deep links, in-app detail opens and browser history navigation share the same information-detail loader. List payloads are not a substitute for the dedicated detail read. Browser regressions omit calendar detail records from the list and omit price observations from news list items so this distinction is tested end to end.

## Configuration and Operations

- External API settings: `externalOfficialReleaseEnabled`, default `1`.
- Collection interval: `externalOfficialReleaseCadenceSeconds`, default `1800` seconds. This is periodic collection, not a realtime feed.
- `externalBlsStatisticsEnabled`, default `1`: one four-series batch every six hours, capped at ten requests per day, with a separate rate limit and circuit.
- `externalDocumentRecoveryEnabled`, default `1`; `externalDocumentRecoveryBatchSize`, default `25`, bounded to 100. Existing SEC/OpenDART document switches and access configuration still apply.
- BLS release documents, BLS public statistics, FOMC and BOK have separate provider circuits. Dataset state records errors and the next attempt; the calendar shows that state separately from stored results.
- Disabling a dataset also deactivates its existing recurring partitions at synchronization; an already-claimed disabled job is released without calling the provider.
- Latest endpoints capture current/future observations from deployment onward, not a full historical backfill. Source revisions are retained only while the existing retention policy allows.

## Validation

Run `npm test`, then `node scripts/test-information-browser.cjs`. The latter runs a synthetic, isolated server and checks real application detail routes, links, expandable source excerpts and overflow at 1440, 390 and 360 pixels. It does not send notifications or call vendors.

Live verification on 2026-09-13 captured and date-matched the July 29 FOMC statement. BLS returned HTTP 403 from the local environment; the failed request was recorded and did not create a false result or block FOMC. The seven active stored news records included six with source-bound excerpts and one current title/body conflict excluded from the new brief. This is a source-lineage check, not a claim that all article interpretations are correct.

The follow-up verification captured the August 27 BOK statement and matched the source excerpt for a 2.75% to 3.00% policy-rate change. Historical domestic body verification increased from 56 to 75 of 122 retained disclosures during the audit. Forty-four SEC filings still lacked document access because the local contact email was unset; they were kept as metadata, not silently promoted to verified bodies. A bounded recovery scan subsequently queued 43 additional missing domestic bodies and explicitly reported those 44 SEC blocks. Queue admission is not proof of completed analysis.

After processing those jobs and sequentially replaying retained bodies, 121 of 122 domestic disclosures had verified bodies and analysis-ready source inputs. The remaining Kakao receipt `20260824000219` returned `official-file-not-found`; its terminal state prevents repeated downloads. The SEC contact requirement remained unresolved. These counts certify collection and source linkage, not the correctness of every AI interpretation. The final full test rerun passed 1,317 Python tests and 36 frontend tests; an earlier pre-existing 200 ms subprocess-start timing failure passed in isolation and on the full rerun without changing production AI behavior.

The restarted BLS public API worker collected the August 2026 statistical vintage at `2026-09-13T09:08:17Z`: CPI index-derived changes 0.3960% monthly and 3.3965% yearly, payroll change 162,000 people and unemployment 4.1%. These are retained series calculations, not asserted original release figures. The existing BLS release-page 403 remained a separate coverage gap.
