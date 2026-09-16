# Notification AI Inference Queue

## Purpose

Notification delivery no longer waits inside a Codex subprocess. TypeDB still
owns the investment relation context and allowed action envelope; the AI queue
only schedules the final comparison and explanation of those immutable facts.

```text
TypeDB native inference over a frozen source/ABox
                |
                v
SubjectDecisionCase + immutable CandidateSetSnapshot
                |
       inference-completed event / AIInsightHandoff
                |
ai_inference_requests: pending
                |
       SKIP LOCKED + lease/heartbeat
                |
       +--------+--------+
       |                 |
 AI worker 1         AI worker 2
 gpt-5.6-sol max     gpt-5.6-sol max
       |                 |
       +--------+--------+
                |
     ontology/action-envelope validation
                |
                v
ai_inference_results + AIInsightEpisode
                |
        reconciliation / delivery policy
                |
                v
notification_jobs: pending -> provider attempt -> verified receipt
```

The compatibility notification-first path can still park an existing job in
`awaiting_ai`; it must use the same immutable packet and publication checks.

## Correctness Rules

- The request context is immutable after `awaiting_ai` begins.
- `account + message type + symbol` is the subject key.
- A subject head serializes work. Pending work keeps the latest immutable
  context, while a running request is single-flight: quote/generation-only
  refreshes join it instead of cancelling it. A material action-envelope,
  rule family, hypothesis shape, source event, or decision transition change
  replaces it. A stale result cannot return an old notification to `pending`.
- Identical context hashes are coalesced without another model call.
- Claim uses `FOR UPDATE SKIP LOCKED`; one request has one lease owner.
- A heartbeat extends the lease while Codex runs. Expired latest leases retry;
  expired non-latest leases become superseded.
- The primary queue uses its configured model and reasoning profile. A failed
  model attempt is not replaced by a fabricated AI-authored investment opinion.
- Contract repair uses its own configured effort, not an implicit copy of the
  initial MAX profile. A valid `research-reviewed` explanation with `NO_ACTION`
  does not require an execution comparison or a redundant repair call.
- `context-narrative` retains `NO_ACTION` from model response to delivery. It
  may explain investment direction and causal evidence but cannot advise
  holding, buying or selling through prose while claiming to be actionless.
- Operational notifications do not enter this AI queue.
- Terminal subject decisions are suppressed before queueing. Normal
  supersession races end as `superseded`, not as actionable AI failures.
- The model acknowledges that it reviewed all candidate evidence once; the
  server binds the exact TypeDB evidence IDs back to the review. This avoids
  spending output tokens copying generation-scoped identifiers.
- Queue priority is an operational scheduling band, never an investment score
  or probability.

## Runtime Controls

- `NOTIFICATION_AI_QUEUE_WORKER_COUNT`
- `NOTIFICATION_AI_QUEUE_INTERVAL_SECONDS`
- `NOTIFICATION_AI_QUEUE_LEASE_SECONDS`
- `NOTIFICATION_AI_QUEUE_HEARTBEAT_SECONDS`
- `NOTIFICATION_AI_QUEUE_MAX_ATTEMPTS`
- `NOTIFICATION_AI_QUEUE_RETRY_SECONDS`
- `NOTIFICATION_AI_QUEUE_MAX_PROMPT_BYTES`
- `NOTIFICATION_AI_ATTEMPT_WATCHDOG_SECONDS`
- `NOTIFICATION_AI_QUEUE_RETENTION_HOURS`

The delivery deadline may remain disabled because inference is asynchronous.
The attempt watchdog is different: it bounds one local model process so a hung
execution cannot hold a worker forever. The normal packet targets 15 KiB and
may expand to a 16 KiB hard cap only when the minimum decision contract does
not fit. A retry starts with a 12 KiB minimum-contract packet and uses the same
bounded expansion path. The compact packet keeps action, hypothesis
identity, rules, evidence IDs, current facts, continuity, and valuation while
the unabridged decision brief remains in the immutable audit store.

Use `npm run python:ai-inference:status` to inspect queue state. The realtime
status API also exposes `aiInferenceQueue` separately from delivery jobs.

`executionSpans.queueWaitMs` measures the current attempt's available-to-claim
interval. It is null when either timestamp is unavailable. Previous model
attempts and retry backoff are not queue waiting. `attemptNumber` and
`requestElapsedBeforeAttemptMs` retain the broader request context separately.

Continuity v3 separates current ABox-bound facts from dated historical positions.
Old v2 packets cannot reintroduce account-ledger prices as current valuations.
Final delivery/suppression reasons are retained in the notification lifecycle,
so outbox cleanup cannot turn quiet-hours suppression into an invented
hypothesis-qualification failure. A stored terminal state is still distinct
from a verified channel receipt.
