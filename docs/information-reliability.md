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

Follow-up suggestions are labeled `자동 관찰 미등록` unless an actual observation contract is connected. This feature does not claim to monitor an unregistered suggestion. Price-reaction analysis is also explicitly unconnected.

## Official Calendar Results

Initial coverage:

| Dataset | Source | Values |
| --- | --- | --- |
| `official.bls-release` | BLS official CPI and employment releases | CPI monthly/yearly changes, prior monthly change when explicitly reported, payroll change, unemployment rate |
| `official.fomc-release` | Federal Reserve calendar and its published statement link | Federal funds target range |

The parser verifies the official host, publication date/time, reference period and exact value excerpt. FOMC links are discovered from the official calendar; the URL date must agree with the document date. A future statement, access-denied page or unrecognized value format is a collection error, not a release with empty or guessed values.

Current observations and immutable revisions use `external_fact_current` and `external_fact_revision`; the existing configured revision-retention policy applies. Source date, actual publication time where provided, first collection time and last successful collection time are distinct. A later source failure does not erase a previously captured historical result. Previously published data are not retroactively treated as known before collection.

Only the release on the event's New York local date is joined. A July decision never populates September's meeting. Operational reminder times are labeled separately from officially supplied publication times.

No market consensus is collected in this scope. Prior-period actuals are not relabeled as consensus; no surprise score or market-direction judgment is fabricated. BOK, BEA and company-specific schedules remain available, but automatic results for those types require separate source adapters and are visibly marked unsupported.

## Configuration and Operations

- External API settings: `externalOfficialReleaseEnabled`, default `1`.
- Collection interval: `externalOfficialReleaseCadenceSeconds`, default `1800` seconds. This is periodic collection, not a realtime feed.
- BLS and FOMC have separate provider circuits. Dataset state records errors and the next attempt; the calendar shows that state separately from stored results.
- Disabling a dataset also deactivates its existing recurring partitions at synchronization; an already-claimed disabled job is released without calling the provider.
- Latest endpoints capture current/future observations from deployment onward, not a full historical backfill. Source revisions are retained only while the existing retention policy allows.

## Validation

Run `npm test`, then `node scripts/test-information-browser.cjs`. The latter runs a synthetic, isolated server and checks real application detail routes, links, expandable source excerpts and overflow at 1440, 390 and 360 pixels. It does not send notifications or call vendors.

Live verification on 2026-09-13 captured and date-matched the July 29 FOMC statement. BLS returned HTTP 403 from the local environment; the failed request was recorded and did not create a false result or block FOMC. The seven active stored news records included six with source-bound excerpts and one current title/body conflict excluded from the new brief. This is a source-lineage check, not a claim that all article interpretations are correct.
