# Orbit Alpha PC Console Data Contract

## Goal

The PC console presents one operational question per workspace without copying full domain records into every screen. Raw domain snapshots remain auditable; summary screens use canonical read models and load full records only in a detail surface.

## Source Ownership

| Data | Canonical source | Summary workspace | Detail boundary |
| --- | --- | --- | --- |
| Account connection and provider health | service accounts and Toss diagnostics | Operations | connection, credential and history detail |
| Portfolio totals and current holdings | `AccountSnapshot.portfolio` and `Position` | Today and Market | balance ledger and instrument detail |
| Investment action and decision-time values | `DecisionItem` and TypeDB-backed InferenceBox context | Decision | evidence, chart and inference trace detail |
| Research article and stock impact | `ResearchEvidence` | Market | Korean article summary, impact analysis and source |
| Delivery state and dispatch reason | `NotificationJob` | Alerts | gates, full message and linked article detail |
| Ontology graph and inference | TypeDB TBox, ABox, schema functions and InferenceBox | Validation | graph, rule and trace detail |
| Experiment lifecycle | `OntologyExperiment` | Validation | replay, comparison and promotion detail |
| Calendar event | investment calendar event repository | Calendar | month board, event rationale and reminder detail |
| Runtime settings | MySQL operational settings | Operations | full-screen category editor |

## Canonical Identity

- Instrument: `accountId + market + symbol`; a single-account response may omit `accountId` in the visual label but not in the read-model key.
- Research evidence: `evidenceId`.
- Decision: server decision id, falling back to `accountId + symbol + generatedAt` only for historical compatibility.
- Notification: `jobId`.
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

- Today: portfolio summary, due events, urgent decisions, alert failures and data-health count.
- Market: one instrument row per canonical identity plus linked evidence count and top impact.
- Decision: one action row per canonical decision with action, conviction, reason, invalidation and freshness.
- Alerts: one dispatch row per notification job with state change, trigger reason and delivery state.
- Validation: experiment rows plus TypeDB health and structural warnings.
- Operations: provider and worker health plus settings category entry points.

## Rendering Limits

- Four to six summary metrics.
- One primary work list and at most one secondary context surface.
- Eight to twelve rows per page.
- No inline master-detail pair, nested scroll region or full record in a summary row.
- Full records open in a full-screen detail surface with their own route-compatible identity.
