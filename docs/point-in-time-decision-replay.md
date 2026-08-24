# Point-in-Time Decision Replay

Historical investment validation must use only information that the system
could know when a decision became final. Replaying a current hydrated object is
not sufficient because decision outcomes and follow-up conditions change after
the original decision.

## Clocks

- `decisionAt`: market/reference clock presented with the decision.
- `recordedAt`: time the final decision episode was first persisted.
- `knowledgeCutoffAt`: strict replay cutoff. This is `recordedAt` when present,
  otherwise `decisionAt` for legacy records.
- `sourceAsOf`: source measurement clock.
- `publishedAt` / `availableAt`: external publication and availability clocks.
- `ingestedAt` / `fetchedAt`: local collection clocks.
- `derivedAt` / `generatedAt`: derived fact clocks.
- `observedAt`: post-decision outcome clock.

The market reference clock and final persistence clock must not be conflated.
Queued AI decisions can be finalized several minutes after their market
snapshot. Facts fetched before final persistence are valid inputs even when
their seconds are later than the minute-level market reference.

Daily price-change facts retain the current quote clock, previous-session
close, period-return formula, and adjustment status. A missing vendor change
rate may be derived only from explicitly provider-adjusted candles aligned to
the quote session. Unverified or unadjusted history cannot author a usable
return fact.

## Strict Contract

The immutable `factsAtDecision` payload is audited recursively. A timestamp
later than `knowledgeCutoffAt` blocks that replay case. Date-only provider
values remain visible as coarse timestamps because the immutable snapshot
proves they were already ingested, but they cannot establish intraday order.

Mutable records are always read separately:

- outcomes are inputs only when `observedAt <= nextKnowledgeCutoffAt`;
- transitioned follow-ups are inputs only when `transitionAt <= nextKnowledgeCutoffAt`;
- missing or invalid observation clocks are excluded;
- excluded future observations remain in the audit report.

## Replay Envelope

`DecisionReplayEnvelope` contains the original decision, immutable fact
fingerprint, point-in-time assessment, and engine manifest. Full facts are
hidden from summary responses unless an internal engine explicitly requests
them.

The engine manifest freezes:

- ontology investment brain version;
- TypeDB inference generation and ABox snapshot;
- TBox and RuleBox fingerprints;
- AI prompt and model versions;
- hypothesis and outcome contract versions.

Legacy episodes are classified as `partial-replay` or `audit-only` rather than
being rewritten with current versions.

## Safety

The current replay service is read-only. It cannot deliver notifications,
write an operational ABox, mutate RuleBox/TBox, or deploy a candidate. V1/V2/V3
engine adapters must consume the same envelope through the versioned replay
port and write comparison output to a separate replay store.

The read-only endpoint is:

```text
GET /api/investment-brain/decision-replay
```

Supported query parameters are `accountId`, `symbol`, `limit`, `includeCases`,
`caseLimit`, and `replayMode`.
