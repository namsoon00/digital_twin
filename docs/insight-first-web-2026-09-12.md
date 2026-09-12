# Insight-First Web Presentation

## Scope

This change reorganizes the existing web read models. It does not change
investment rules, hypothesis eligibility, AI publication gates, order execution,
notification dispatch, or data collection. No live records are rewritten.

## Reading Order

- Today: up to three current investment interpretations or opinions and the next
  event. Unfinished analysis and collection/delivery problems are separate groups.
- Decisions: current review, recheck and all records. Current review excludes
  blocked or outdated records; recheck retains them without invalidating history.
- Decision detail: stored opinion, why it matters, limitations, next checks and
  distinct source/opinion timestamps. Full evidence, rules, relations, hypotheses,
  AI assessment and outcome review remain in the detail tabs.
- Desktop detail: same-account records beside the active case. Mobile: one column.
- Notifications: event/change/reason first, delivery state separately. Quiet hours
  and transport failures are never substituted for investment reasoning. The
  lightweight SQL list projects authored headline/lead leaves without fetching
  the full graph per item. Legacy text is labeled as a preview, not an explanation.
- Market: use the account-qualified decision read model and canonical detail key.
- Portfolio: interpretation before totals; label prior-revision interpretation and
  distinguish total policy breaches from allocation-only breaches.
- Calendar: next event and investment impact before the month on narrow screens;
  operational tools and aggregate counts are secondary disclosures.

Aggregate metrics and administrative information remain reachable. Disclosures
have stable identities so background rendering can retain their open state.

## Question-Oriented Reading (2026-09-13)

`read_models/domain/investment_reading.py` projects existing case data into the
versioned `investment-reading-v1` presentation contract. List and dashboard
responses carry only the compact reading; detail carries the explanations,
recorded numeric facts and their source timestamps. Projection performs no extra
database/API query and never creates an investment opinion or changes a record.
The console list and dashboard cache keys include this presentation version so
the first response after deployment cannot reuse a disk cache from the old UI
contract. Refresh TTLs and last-success handling remain unchanged.

The main detail answers four questions in order:

1. What was observed? Saved changes and numeric facts, with their original dates.
2. What does it mean for my investment? Only the approved current-generation AI
   investment implication, or an explicit statement that no interpretation exists.
3. Why? Supporting explanations, counterarguments, constraints and missing data
   remain separate. Absence of recorded counterarguments does not mean no risk.
4. What needs checking? Saved next checks and invalidation conditions. The UI
   does not promise automatic monitoring or fabricate a future event.

`NO_ACTION` is not a holding recommendation. A case without a final action or a
validated interpretation belongs to preparation/recheck, not current opinions.
An approved interpretation without action authority is explicitly labeled as
reference interpretation with no trading opinion. Old records retain their
original dates and stay accessible in history and recheck.

The model tab first shows the saved hypothesis contract's expected outcome,
plain-language basis, qualification reason and falsification condition. Raw
facts, relations, model conditions, rules and processing lineage remain in
separate disclosures; they are not deleted or rewritten. Only a known engine
wiring sentence and identifier diagnostics are omitted from the reading layer.
Full source records retain them for audit.

Desktop uses a two-column question layout with same-account navigation; mobile
uses one column and links with at least 44px targets. Tab IDs and persistent
panel/scroll lifecycle remain unchanged. This iteration changes presentation,
not model quality or data availability; a missing explanation is not filled by
an invented insight.

## Data Semantics

- Default opinion recency is the existing 96-hour presentation window, or the
  dashboard-provided window. An explicit verification timestamp takes precedence.
  This is not a forecast expiry rule or an instruction to trade.
- Missing current account membership is shown as unknown, not as a holding.
- Missing evidence counts are unknown, not zero. A stored reasoning lineage is
  distinguished from a resolved original source. Counter-evidence IDs remain
  countable even when a plain-language counterargument has not been captured.
- Trailing EPS never inherits the forward EPS scenario period.
- No user feedback samples means insufficient evaluation, not zero helpfulness.
- Capital-flow scope states the observed markets, instruments and time window.

## Verification

Required commands:

```sh
npm test
npm run frontend:test:browser
PYTHONPATH=python_service python3 -m unittest python_service.tests.test_subject_reasoning_lineage python_service.tests.test_instrument_valuation_query
```

The browser suite serves isolated synthetic fixtures, blocks external requests,
and checks bundle/module builds at desktop and mobile widths. It covers light
and dark themes, first-opinion visibility, accessible filters, detail links,
touch targets, account scoping, unchanged scroll positions, stale-response
rejection, retained list identities and chart pixels. Screenshots and numeric
results are generated in `/tmp/orbit-frontend-screenshots`.

Real local APIs are inspected read-only for contract compatibility. Fixture tests
are not evidence of real-user comprehension, real-device Safari behavior or
Cloudflare end-to-end availability. Those require separate usability/device
validation. Large history/timeline aggregation and backend inference quality are
outside this presentation change.
