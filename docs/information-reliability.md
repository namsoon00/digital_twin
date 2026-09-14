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

Document download observations persist their collection source in the immutable quality envelope. `document-recovery` remains non-alerting when its download-completed event is replayed by another worker. Projection reads the event's exact document revision, not the symbol's latest document; a missing revision is an explicit error. For pre-migration revisions, the reader recovers this purpose from the exact retained document job without rewriting source history. Metadata refreshes preserve this purpose and digest admission rejects recovered bodies even through a legacy event. Newly discovered, eligible documents retain their normal alert path; an amendment with a new accession remains a distinct document.

Disclosure alerts identify the filing form and accession/receipt number. Form 4 is labeled as an insider holdings/transactions report rather than just `4`. A source supplying only a publication date is not rendered as 09:00 KST. A different accession is not deduplicated merely because its form title matches.

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

The comparison describes price movement across a time window, not a causal effect attributable to the event. Detail reads refresh on reopening after the one-minute cache expires. Unregistered historical items still use read-time observation; registered items display the durable follow-up record described below. Exact publication time and retained intraday history are prerequisites; older events may have no usable observations because existing retention rules still apply.

Initial deep links, in-app detail opens and browser history navigation share the same information-detail loader. List payloads are not a substitute for the dedicated detail read. Browser regressions omit calendar detail records from the list and omit price observations from news list items so this distinction is tested end to end.

## Configuration and Operations

- External API settings: `externalOfficialReleaseEnabled`, default `1`.
- Collection interval: `externalOfficialReleaseCadenceSeconds`, default `1800` seconds. This is periodic collection, not a realtime feed.
- `externalBlsStatisticsEnabled`, default `1`: one four-series batch every six hours, capped at ten requests per day, with a separate rate limit and circuit.
- `externalDocumentRecoveryEnabled`, default `1`; `externalDocumentRecoveryBatchSize`, default `25`, bounded to 100. Existing SEC/OpenDART document switches and access configuration still apply.
- BLS release documents, BLS public statistics, FOMC and BOK have separate provider circuits. Dataset state records errors and the next attempt; the calendar shows that state separately from stored results.
- Disabling a dataset also deactivates its existing recurring partitions at synchronization; an already-claimed disabled job is released without calling the provider.
- Latest endpoints capture current/future observations from deployment onward, not a full historical backfill. Source revisions are retained only while the existing retention policy allows.

## Free-Tier Request Control

The SEC contact is private runtime configuration, not a repository secret or API key. SEC submissions and document collection now reload changed settings at worker cycle boundaries. A local missing-contact preflight is a configuration deferral, not a vendor circuit failure. Recovery clears only that identified preflight state; it never clears a vendor HTTP 403/429 circuit. Document jobs retain their exact accession and original URL and use the existing leased queue and one-second SEC spacing.

GDELT discovery, research and the legacy collector share a MySQL HTTP reservation ledger. `newsCollectionGdeltRateLimitSeconds` defaults to 6 seconds (minimum 6); `newsCollectionGdeltDailyRequestBudget` defaults to 240 attempts per UTC day. These are conservative local budgets, **not a claim about an official paid/free quota**. Deferred calls do not consume another attempt. Vendor failure opens a persistent, exponentially backed-off circuit from five minutes to six hours. HTTP 429 honors numeric and HTTP-date `Retry-After`; it is not immediately retried. Other news providers remain independent. Both controls are exposed in external data settings.

BLS Public Data API v1 batches four series once per six hours with a local ten-request daily cap and one network attempt per admitted job. The unregistered public API and BLS HTML release website remain separate sources. An HTML 401/403 defers the release job for six hours, retains the error and reports the access problem through the existing operational notifier. Increasing request frequency cannot repair an access denial. See [BLS API limits](https://www.bls.gov/developers/api_faqs.htm), [SEC automated access policy](https://www.sec.gov/about/developer-resources) and [GDELT DOC documentation](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/).

## Durable Information Follow-Ups

`informationFollowupEnabled` defaults to `1` and is editable with the external data settings. The existing calendar worker runs this service; no new worker, paid provider or investment decision stage is added. Calendar, external-data and news collectors reload collection settings between cycles.

- Register source-linked, alert-eligible news and verified disclosures for held/watched symbols, plus verified official calendar results, from the last 72 hours. Store the exact source version and account-independent baseline quote fields in `information_followups`.
- Recheck 1-hour and 24-hour windows every five minutes, using only the shared retained market history. Save completed observations so a later time-series cleanup does not erase them. If a window still lacks compatible observations after three hours, finish that window without inventing a price or sending a result.
- Do not send retroactive price alerts for horizons already passed at initial registration. Date-only official releases can report a verified result on their local publication date but never create an intraday price window. Older results remain available on the web.
- Revalidate canonical source eligibility, lifecycle and body hash before each follow-up. Retractions/corrections cancel the old active version. Temporary read failures are explicit and retried; persistent read failures expire after four days. Completed/canceled records have bounded 90-day retention, purged in small batches on registration.
- Store the observation event, completion and notification outbox admission in one transaction with optimistic concurrency. Exact source/story, phase and account identity prevents retries or equivalent article versions from repeatedly notifying. A rolled-back outbox write leaves the observation due for retry.
- `informationUpdate` contains official actual/prior values or measured before/after prices with their real quote times and original source link. No generic AI opinion, causal attribution, consensus guess, buy/sell action, hypothesis update or strategy authority is added.
- Web detail distinguishes active, paused, delayed, canceled and completed observation from queued/sent/failed/suppressed delivery. A registration is not proof of delivery. `investment-calendar status` includes persisted follow-up counts.

AI-authored free-text questions such as “check the next earnings report” remain separately labeled **not automatically registered**. This service observes the explicit price windows and verified official releases only; it does not claim to semantically monitor arbitrary future conditions.

Article extraction now selects one body representation instead of concatenating metadata teasers, DOM text and JSON-LD. Known article containers exclude navigation, related stories, generated-summary tooltips and photo captions. Repeated blocks are deduplicated. Empty/short/low-quality bodies get the short failure cache, not the successful-body cache; a missing publisher body is never supplied by unrelated page paragraphs.

News health counts quality-admitted items, not merely fetched candidates, when resetting freshness and the zero-result streak. A run that retrieves five candidates and admits none is filtered/idle (or stale after the configured gap), never reported as successful quality admission. Discovery success and evidence quality remain distinct.

## Validation

Run `npm test`, then `node scripts/test-information-browser.cjs`. The latter runs a synthetic, isolated server and checks real application detail routes, links, expandable source excerpts and overflow at 1440, 390 and 360 pixels. It does not send notifications or call vendors.

Live verification on 2026-09-13 captured and date-matched the July 29 FOMC statement. BLS returned HTTP 403 from the local environment; the failed request was recorded and did not create a false result or block FOMC. The seven active stored news records included six with source-bound excerpts and one current title/body conflict excluded from the new brief. This is a source-lineage check, not a claim that all article interpretations are correct.

The follow-up verification captured the August 27 BOK statement and matched the source excerpt for a 2.75% to 3.00% policy-rate change. Historical domestic body verification increased from 56 to 75 of 122 retained disclosures during the audit. Forty-four SEC filings still lacked document access because the local contact email was unset; they were kept as metadata, not silently promoted to verified bodies. A bounded recovery scan subsequently queued 43 additional missing domestic bodies and explicitly reported those 44 SEC blocks. Queue admission is not proof of completed analysis.

After processing those jobs and sequentially replaying retained bodies, 121 of 122 domestic disclosures had verified bodies and analysis-ready source inputs. The remaining Kakao receipt `20260824000219` returned `official-file-not-found`; its terminal state prevents repeated downloads. The SEC contact requirement remained unresolved. These counts certify collection and source linkage, not the correctness of every AI interpretation. The final full test rerun passed 1,317 Python tests and 36 frontend tests; an earlier pre-existing 200 ms subprocess-start timing failure passed in isolation and on the full rerun without changing production AI behavior.

The restarted BLS public API worker collected the August 2026 statistical vintage at `2026-09-13T09:08:17Z`: CPI index-derived changes 0.3960% monthly and 3.3965% yearly, payroll change 162,000 people and unemployment 4.1%. These are retained series calculations, not asserted original release figures. The existing BLS release-page 403 remained a separate coverage gap.

On 2026-09-14 the authorized SEC contact was saved locally. A stale long-running collector was found still using its prior empty contact; cycle-boundary reload and configuration-deferral handling fix that failure mode. All 44 SEC document downloads then completed and all 44 canonical filings had verified bodies after replaying retained facts. A subsequent notification audit corrected the initial no-historical-alert claim: two recovered NVDA Form 4 filings were sent as new disclosures because download-completion replay lost the recovery purpose. The retained-body replay flag alone was insufficient. Durable collection-source propagation and exact-revision reads now cover that asynchronous path. The domestic count remained 121/122; receipt `20260824000219` remains an unavailable original file, not a rate-limit problem.

The first live follow-up cycle registered five eligible information records without retroactive notifications. Two completed historical windows and three active observations were persisted; a second cycle reused the same identities. A live GDELT attempt timed out during TLS negotiation; the following call was deferred by the shared circuit without another network call. BLS release access remained denied while its free statistical API remained independently available. These external availability limits are explicit residual coverage gaps, not silently successful collections.
