# AI Insight Follow-Up Tracking

## Ownership

An AI-authored numeric follow-up is a proposal, not a registration receipt.
Normalization validates the supplied value and market coverage and marks it
`unregistered`. Only the outcomes writer can attach a
`follow-up-registration-v1` receipt after persisting the watch.

Accepted, contract-passed AI insight publication writes the insight, notification
outbox and watches in one transaction. `NO_ACTION` remains an interpretation:
it does not create a `DecisionEpisode`, trade plan or hypothesis outcome.
The existing `investment_decision_follow_ups` ledger supports two explicit
owners, `decision` and `ai-insight`. If a final decision already owns the same
publication's follow-ups, its rows are reused instead of registering duplicates.
New accepted AI publication supersedes earlier pending AI watches for the same
account and symbol, never another account's or final decision's watches.
Fallback, contract failure and notification suppression cannot register watches.

## Observation

The existing snapshot observer handles AI watches; there is no additional worker.
Watch IDs include account, symbol, owner episode and source condition identity.
AI watches expire after at most seven days, or the earlier authored expiry.
Unsupported fields remain additional research tasks, not automatic watches.

The first fresh observation at or after registration establishes the watch's
starting state. Already-true conditions must become false before triggering.
Supplier timestamps, not polling timestamps, distinguish new observations.
Repeated, out-of-order, missing, nonfinite and stale inputs cannot confirm a
transition. At least two distinct fresh observations must confirm the condition;
the existing market-signal persistence setting can require more.

Near-current thresholds additionally use the existing market signal policy's
movement buffer. The authored threshold is retained unchanged; the registration
receipt stores the extra observation policy, which is also shown in the message.
This filters measurement noise, not investment hypotheses. One verified edge
ends the watch and requests reanalysis; it does not prove the hypothesis or
authorize a trade.

The ledger update uses a payload compare-and-set. A reached condition retains a
pending dispatch marker until both the transition event and TypeDB request have
been published. Retries preserve the original source snapshot and deterministic
event IDs. Existing durable event ingestion deduplicates those IDs.

## Memory And Presentation

Delivery memory uses the immutable insight's reserved notification ID and actual
transport receipts. Queue/result retention must not turn a previously delivered
interpretation into a first insight. Stored publication provenance is required
when the transient AI result has been deleted.

Current watch state is hydrated from the outcomes ledger for web and subsequent
AI input. The immutable interpretation is not rewritten when the watch changes.
Both analysis memory and delivered memory carry scoped follow-up observations;
only a transition newer than the latest analysis can reopen an unchanged-graph
AI admission. Registration does not bypass final delivery/materiality policy.

Only receipt-backed conditions appear as automatic tracking. Legacy and
unsupported proposals remain explicitly unregistered. Historical messages and
old conditions are not silently backfilled or resent. New registrations start
with new accepted publications after rollout.

## Verification

- `test_ai_follow_up_tracking`: registration labels, finite values, freshness,
  duplicate source clocks, starting-state behavior, noise confirmation, expiry,
  scoped memory and once-per-analysis admission.
- `test_ai_inference_queue`: transaction rollback, independent `NO_ACTION`
  registration, account/symbol isolation, ledger hydration, pending dispatch
  recovery and retention-independent delivered insight memory.
- `test_reasoning_snapshot_replay`: committed snapshot observation and stable
  transition/request IDs retaining the original snapshot across retries.

Controlled test observations validate software behavior, not investment returns
or a claim that a future live condition has already occurred.
