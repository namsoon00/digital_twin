# Notification AI Inference Queue

## Purpose

Notification delivery no longer waits inside a Codex subprocess. TypeDB still
owns the investment relation context and allowed action envelope; the AI queue
only schedules the final comparison and explanation of those immutable facts.

```text
TypeDB InferenceBox investmentInsight
                |
                v
notification_jobs: pending
                |
      delivery worker captures context
                |
                v
notification_jobs: awaiting_ai
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
ai_inference_results: completed
notification_jobs: pending
                |
                v
        final delivery outbox
```

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
- The primary queue uses `gpt-5.6-sol` with `max` reasoning. Deterministic local
  TypeDB-backed wording is used only after the configured MAX attempts fail.
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
