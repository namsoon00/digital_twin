# Insight-First Web Presentation

## Scope

This change reorganizes the existing web read models. It does not change
investment rules, hypothesis eligibility, AI publication gates, order execution,
notification dispatch, or data collection. No live records are rewritten.

## Reading Order

- Today: up to three current tasks, important blockers and the next event.
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
