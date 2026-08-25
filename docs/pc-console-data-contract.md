# Orbit Alpha PC Console Data Contract

## Goal

The PC console presents one operational question per workspace without copying full domain records into every screen. Raw domain snapshots remain auditable; summary screens use canonical read models and load full records only in a detail surface.

## Source Ownership

| Data | Canonical source | Summary workspace | Detail boundary |
| --- | --- | --- | --- |
| Account connection and provider health | service accounts and Toss diagnostics | Settings and Operations | connection, credential and history detail |
| Portfolio totals, holdings and allocation policy | portfolio lifecycle, ledger and `Position` | Today and Portfolio | position, activity and rebalance detail |
| Investment action and decision-time values | `DecisionItem` and TypeDB-backed InferenceBox context | Decision | evidence, chart and inference trace detail |
| Research article and stock impact | `ResearchEvidence` | Market | Korean article summary, impact analysis and source |
| Delivery state and dispatch reason | `NotificationJob` | Alerts | gates, full message and linked article detail |
| Per-user notification state | `NotificationInboxReceipt` keyed by browser recipient and job | Alerts | read, acknowledged and important state |
| Ontology graph and inference | TypeDB TBox, ABox, RuleBox and InferenceBox | Validation | graph, rule and trace detail |
| Experiment lifecycle | `OntologyExperiment` | Validation | replay, comparison and promotion detail |
| Calendar event | investment calendar event repository | Calendar | month board, event rationale and reminder detail |
| Runtime settings | MySQL operational settings | Settings or Operations governance | full-screen category editor |

## Canonical Identity

- Instrument: `accountId + market + symbol`; a single-account response may omit `accountId` in the visual label but not in the read-model key.
- Research evidence: `evidenceId`.
- Decision: `decisionKey`, derived from `accountId + symbol + decisionEpisodeId`; historical rows may fall back to the current symbol identity.
- Notification: `jobId`.
- Decision-to-notification link: `decisionKey`, with `decisionEpisodeId` and `accountId + symbol` as compatibility fallbacks.
- Notification recipient: a browser-generated persistent recipient id. It separates inbox state without treating it as authenticated user identity.
- Experiment: experiment `id`.
- Ontology entity and relation: TypeDB entity id and relation id. Web code must not create a competing persistent relation identity.

## Duplication Rules

1. Exact display duplicates are removed. A field is rendered once per summary screen unless the second value is an explicit comparison.
2. Derived values are calculated by one selector. Render functions format values but do not recompute portfolio totals, state changes or freshness.
3. Context repetition is reference-only. Another workspace may show a short state, count or label and link to the owning detail.
4. Audit snapshots are preserved. Current position values and decision-time values are distinct records and must be labelled by time instead of merged.
5. Summary rows do not include article bodies, graph traces, raw settings, notification messages or balance ledgers.
6. Source conflicts are visible. Prefer actual, fresh data over cache and mock data; do not silently combine values from different timestamps.

## Workspace Read Models

- Today: at most three actionable decisions or delivery failures, three grouped blocker causes, the portfolio headline and upcoming events.
- Portfolio: account summary, positions, policy breaches, rebalance alternatives and compact ledger activity in independent views.
- Market: one instrument row per canonical identity plus linked evidence count and top impact.
- Decision: the latest current state per canonical decision, with structured action code, reason, data quality, API source and freshness. Full evidence, inference and outcome history load only after opening a case.
- Alerts: chronological state-change history per notification job, with unread, acknowledged, important and delivery state. It is not a duplicate current-decision queue.
- Validation: missing evidence, conflicts and model validation for the current decisions.
- Settings: account identity, brokerage connections and user display or delivery preferences only.
- Operations: provider, worker, TypeDB, AI and notification queue health plus governance entry points.

## Page API Boundaries

| Workspace | Summary API | Full-detail rule |
| --- | --- | --- |
| Today | `/api/dashboard/summary` | case, calendar and source details use their canonical APIs |
| Portfolio | `/api/portfolio/summary`, `/positions`, `/rebalance`, `/activity` | raw lifecycle payloads and order envelopes are never embedded in the page list |
| Market | `/api/market/instruments`, `/api/market/evidence` | article body and verification metadata use `/api/research-evidence/{id}` |
| Decision | `/api/decisions` | complete case uses `/api/decisions/{caseId}` and lazy history or trace endpoints |
| Operations | `/api/operations/health` | source-specific diagnostics and settings open only on demand |

The APIs above are application-layer projections. They may read canonical stores in parallel, but they do not collect vendor data, execute TypeDB reasoning or enqueue AI work during an HTTP request.

## Rendering Limits

- Four to six summary metrics.
- One primary work list and at most one secondary context surface. Today renders at most three primary tasks.
- Eight to twelve rows per page.
- No inline master-detail pair, nested scroll region or full record in a summary row.
- Full records open in a full-screen detail surface with their own route-compatible identity.
- Mobile lists use cursor-based cumulative loading and vertical records; a refresh must preserve the active workspace scroll position.
