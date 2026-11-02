# Requested AI portfolio reports

Open `/ai-reports.html`, select an account and create a report. The page is also
linked from Central AI. Creation freezes the current saved holding/watchlist
universe, company evidence reports, independently reviewed observation narratives
and reviewed question answers. Every opinion retains its analysis clock, partial
answer status, missing requirements and source references. The generated clock is
not a new AI assessment. No model, provider, TypeDB run or recurring schedule is
started by this feature.

The report is an account-scoped reading artifact, not a new investment action or
change notification. Company sections reuse the existing source-bound company
report read model against one captured monitor snapshot. Observation-only drafts
are not promoted to reviewed opinions. Observation excerpts require their frozen
review proof and matching narrative digest; question answers require the admitted
assessment for the same account, symbol, world and original question contract.
Incomplete question answers remain incomplete. Historical source data does not
become a current quote or a current causal explanation when a report is created.

Read bounds are explicit: at most 100 subjects, 100 most recently updated question case identities per
subject, 20 opinion records, five reviewed answers and five open questions per
subject. Omitted counts and case-limit warnings appear in the report. Absence in
this window is not a claim that the entire historical archive has no answer.
Database errors fail the request instead of masquerading as missing evidence.

`ai_portfolio_reports` stores immutable, content-hashed snapshots separately from
worker-task retention. The request ID is unique per account. A retry or concurrent
request returns the first saved snapshot. GET requests do not initialize schema,
create reports or send messages. Reports are accessible only to local owners or
authenticated owners; shared viewers cannot read them. Report payloads contain
the projected evidence, not raw account snapshots or credentials.

The separate **AI 봇으로 요약 받기** button requests delivery once per report.
The report row lock and notification outbox insertion commit together. The new
`aiReport` type uses the existing AI research bot and the selected account's
recipient. An unavailable research bot fails closed without falling back to a
different channel. This explicit owner request is independent of automatic-notice
subscriptions, novelty checks and quiet hours; it still respects notification
admission settings and does not modify the existing AI observation gate. Whole
qualified passages fit the short summary; omitted subjects remain in the web
report. The summary does not cut caveats to fit a character count.

The report screen distinguishes a request, pending work, failure and delivery.
Only a receipt with verified destination, matching account/report/content hash
and matching rendered message hash is displayed as delivered. Receipt status
survives outbox-job retention. The local report link is not advertised as a
phone-accessible public URL. The page offers browser printing/PDF saving, with
the source disclosures expanded for printing.

API: owner-only `GET /api/ai-reports/accounts`, `GET /api/ai-reports?accountId=…`,
`POST /api/ai-reports` with `accountId` and a stable `requestId`,
`GET /api/ai-reports/{id}?accountId=…`,
`GET /api/ai-reports/{id}/delivery?accountId=…`, and
`POST /api/ai-reports/{id}/send` with `accountId`.

Validation covers partial/future/wrong-scope evidence, frozen request retries,
viewer denial, dedicated-bot routing, concurrent sends, transactional rollback,
receipt verification and escaped HTML. Production verification uses an actual
requested report and its transport receipt, not a forced investment observation.
