# AI Insight Follow-Up Tracking

## Ownership

An AI-authored numeric follow-up is a proposal, not a registration receipt.
Normalization validates the supplied value and market coverage and marks it
`unregistered`. Only the outcomes writer can attach a
`follow-up-registration-v1` receipt after persisting the watch.

Accepted, contract-passed AI insight publication writes the insight and watches
in one transaction, along with the notification outbox only when delivery is
admitted. A reconciled `unchanged_investment_insight` / `web-only` result can
register watches without a notification. This explicit novelty-only exception
does not admit arbitrary suppression, disabled delivery, stale candidates or
failed/unadopted AI output. The observation admission and actual registration
receipts are retained in the immutable insight for audit.

`NO_ACTION` remains an interpretation:
it does not create a `DecisionEpisode`, trade plan or hypothesis outcome.
The existing `investment_decision_follow_ups` ledger supports two explicit
owners, `decision` and `ai-insight`. If a final decision already owns the same
publication's follow-ups, its rows are reused instead of registering duplicates.
Equivalent conditions reuse their original row and receipt across analyses of
the same thesis. Equivalence uses field, operator, numeric threshold and purpose,
not the AI's prose or condition ID. The original baseline, confirmation count,
supplier clock, expiry and pending reanalysis dispatch are preserved. A shorter
authored expiry can shorten a pending watch; repetition never extends it.
Recently reached conditions referenced by the preceding validated insight are
retained rather than rearmed by the next analysis.

An unchanged insight also retains still-valid watches omitted from its latest
proposal. A revised threshold/operator replaces the old condition for that field
and purpose. A changed thesis or accepted new observation plan supersedes only
the pending AI watches that it no longer owns. Another account, symbol or final
decision's watches remain untouched. Failed or unaccepted analyses cannot cancel
the preceding valid plan. Unsupported fields remain unregistered research tasks.

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
AI admission. AI admission and the final observation/review notification policy
consume the same verified follow-up view. A confirmed AI-owned condition can
qualify a completed, grounded review for delivery even when its investment
direction stays unchanged; an already analyzed edge cannot do so again.
Registration itself does not bypass final delivery/materiality policy.

Only receipt-backed conditions appear as automatic tracking, including conditions
continued from earlier analyses even when the AI changes their wording. Legacy and
unsupported proposals remain explicitly unregistered. Historical messages and
old conditions are not silently backfilled or resent. New registrations start
with new accepted analyses after rollout, including validated novelty-only
suppressions. No historical conditions are backfilled by this change.

## Verification

- `test_ai_follow_up_tracking`: registration labels, finite values, freshness,
  duplicate source clocks, starting-state behavior, noise confirmation, expiry,
  scoped memory and once-per-analysis admission.
- `test_ai_inference_queue`: transaction rollback, independent `NO_ACTION`
  registration, account/symbol isolation, ledger hydration, pending dispatch
  recovery and retention-independent delivered insight memory. Duplicate
  web-only analyses also cover rollback without an outbox, confirmation
  preservation, omitted conditions, failed/disabled analyses, revised thresholds,
  changed theses and reached-condition continuity without fake trade decisions.
- `test_reasoning_snapshot_replay`: committed snapshot observation and stable
  transition/request IDs retaining the original snapshot across retries.

Controlled test observations validate software behavior, not investment returns
or a claim that a future live condition has already occurred.
