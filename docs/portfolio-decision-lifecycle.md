# Portfolio Decision Lifecycle

The account-centered investment lifecycle is split into explicit ownership boundaries.

1. Broker snapshots establish trusted holdings and cash checkpoints.
2. The append-only portfolio ledger reconstructs lots and cash. Confirmed provider fills supersede matching inferred snapshot activity during replay without deleting audit rows.
3. Stored daily market history produces position return, volatility, drawdown, benchmark beta, pairwise correlation, and portfolio active return facts.
4. The active investment mandate supplies allocation bands, cash and concentration limits, risk limits, turnover limits, benchmark mappings, and estimated transaction costs.
5. Rebalance analysis creates deterministic no-action, policy-band restoration, and risk-review scenarios. These are arithmetic candidates rather than investment judgements.
6. The ontology projection writes risk snapshots, measured beta, rebalance scenarios, and action candidates to ABox. TypeDB rules decide which relationships require review.
7. AI receives the exact lifecycle packet and graph result. A final executable action is compiled back through the active mandate and candidate notional cap.
8. BUY, ADD, TRIM, and SELL plans require explicit approval and current account revalidation. Broker order submission remains disabled unless a configured gateway is deliberately supplied.
9. Confirmed fills, costs, outcome observations, benchmark-relative attribution, and decision reviews close the learning loop.

## Performance Contract

- Market history is loaded with one bounded SQL query for all held symbols and benchmarks.
- External calls and TypeDB work never run inside the portfolio write transaction.
- Analysis persistence uses one short transaction and stable content fingerprints.
- An unchanged risk fingerprint does not create a reasoning event.
- A changed risk fingerprint and its reasoning request are committed atomically with the new analysis bundle.
- The web trace follows source order: ledger, risk, scenarios, plan, approval, execution, fill, attribution, and review.

## Execution Safety

The fill import endpoint records already confirmed provider fills. It does not submit orders. Imported fills must reference an approved plan and a matching order intent. Provider execution IDs make imports idempotent.
