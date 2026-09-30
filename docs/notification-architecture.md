# Notification Architecture

## Scope

The notification bounded context starts after an upstream component has produced
an `AlertEvent` or plain operational message. It does not infer an investment
action and does not call V1, V2, TypeDB, or an AI model to reinterpret a result.
It preserves the result, decides whether and when it may be delivered, renders
the customer artifact, and records the delivery outcome.

Both V1 and V2 use `NotificationIngressService`. Engine-specific fields are
preserved in `NotificationSourceTrace`; engine selection and reasoning semantics
remain outside this context.

## Boundaries

### Domain

`domain/notification/` owns immutable contracts:

- `NotificationRequest`: version-neutral producer input (`notification-request-v2`).
  Free text is sufficient; structured sections, links and extensions are optional.
- `NotificationKind`: customer-facing purpose, label and icon. The legacy
  `messageType` remains the delivery-policy key, not the customer purpose.
- `NotificationSourceTrace`: source event, engine deployment, ABox snapshot,
  inference generation, and decision continuity identity.
- `NotificationStage` and `NotificationLifecycleEvent`: append-only processing
  state and allowed transition vocabulary.
- `NotificationDocument`, `NotificationSection`, and `DeliveryReceipt`:
  transport-neutral presentation and channel result contracts.
- `CustomerDeliveryExplanation`: the single validated customer projection of
  why an eligible notification is being delivered now. It is derived from the
  source-event envelope, final decision transitions, and delivery trigger
  ledger after every delivery gate has passed.

The domain package imports neither MySQL nor Telegram.

### Application

`application/notification/` owns use cases:

- `intake.py`: converts alerts or text into the stable request and durable job.
- `admission.py`: evaluates cooldown and similarity, and records market-hours
  and initial freshness advisories from repository-supplied facts. Market-hours
  and freshness findings do not defer or suppress delivery.
- `eligibility.py`: rechecks live operational state and records non-blocking
  market-hours and freshness advisories at dispatch.
- `rendering.py`: creates the exact send-time artifact and content hash.
- `presentation.py`: formats optional content and applies the shared kind
  identity without selecting or changing an investment action.
- `dispatch.py`: selects the account or operations audience and records the
  concrete delivery attempt.
- `workflow.py`: leases jobs and orchestrates the preceding services.
- `query.py`: builds the chronological trace returned by the web API.

AI validation and research run before publication, in the existing AI queue.
The production rendering pipeline has only an instrument-name enricher. It no
longer runs disclosure analysis, decision-context reconstruction, AI validation,
or a deterministic opinion fallback. The compatibility workflow may hand an
unfinished legacy job to the AI queue, but it does not judge it while rendering.

Rendering reads saved customer documents or validated responses. Missing
responses retain the source body; they never produce a default HOLD response.
AI-authored narrative-only publications are `AI 해석`, not `투자 판단`.

### Delivery Cadence

Investment delivery uses four explicit classes. The class is delivery policy,
not investment evidence, and can only consume TypeDB-authored action authority,
notification severity, relation transitions, source-event identity, and stored
decision continuity.

- `immediate`: loss/profit, final action, or major threshold transitions. The
  default repeat floor is 10 minutes.
- `material`: a new important source document or material TypeDB relation
  transition. The default repeat floor is 60 minutes.
- `summary`: an unchanged but still active state. The default review interval
  is 360 minutes. This permits re-evaluation, not an automatic push: an unchanged
  final investment decision remains web-only under final publication policy.
- `web-only`: reference or unchanged state with no user decision value. It is
  retained for audit without interrupting the user.

The admin notification rule stores all three time intervals. A verified
immediate or material change is evaluated before unchanged-relation
suppression, and an unchanged relation becomes eligible for scheduled review
after the configured interval. Material changes must still satisfy their repeat
floor; neither completed AI authority nor the subsequent P/L similarity bypass
may clear a cooldown rejection.

Cooldown history is account/subject scoped before the bounded SQL limit and
uses successful transport `completed_at`, never job `created_at`. A `done` job
without a receipt is not a sent baseline. A receipt remains authoritative if
the job-completion write failed. Immediately before investment dispatch, the
worker holds the subject send lock and rechecks the latest receipt against the
current cadence configuration. Admission alone cannot authorize a burst of
concurrently queued decisions.
Count and age retention protect successful jobs throughout the longest enabled
repeat window. The usual recent-history count applies outside that window so
a burst of other messages cannot erase the cooldown baseline.

Portfolio activity uses the immutable activity episode identity for duplicate
checks; equal titles must not merge different instruments or quantity changes.
External connection alerts retain a provider-scoped incident ID in monitor
snapshot metadata. Changing failure counts does not open a new incident.
Recovery requires an observed successful provider response; a missing source
does not imply recovery. A relapse receives a new incident ID. Operations
messages still use the configured operations destination, with the existing
default-destination fallback when no separate destination is configured.

The settings screen distinguishes event-driven news, account changes, manual
holdings requests, calendar reminders and investment decisions from minimum
interval polling. Raw quote delivery displays and edits its dedicated
`marketObservationImmediateCadenceMinutes` (default 10), not the older deferred
observation cadence. Quiet hours remain suppression rather than next-morning
rescheduling; market hours and freshness remain advisory.

Completed AI insights use the same `final_ai_insight_delivery_is_authorized`
contract at outbox admission and dispatch. A reconciled semantic send with
completed AI execution, writer provenance, a passed publication contract, and a
material insight transition must not be revoked merely because the preceding
TypeDB relation fingerprint is unchanged. This authorization does not bypass
account quiet hours, repeat policy, recipient checks, or final publication
validation; an unverified response or an abstention cannot use it.

Analysis continuity and customer delivery memory are different histories.
`previousInvestmentAIInsightEpisode` retains the latest publishable analysis for
AI context. `previousDeliveredInvestmentAIInsightEpisode` is captured separately
from account/symbol-scoped episodes with a successful transport receipt. Only
the latter establishes the customer novelty baseline. Web-only, suppressed,
failed, queued, and unconfirmed jobs must not erase a still-undelivered insight
change. Existing in-flight and cooldown policies continue to prevent duplicates.
When delivery history cannot be read, retain an explicit error and the legacy
analysis comparison instead of pretending the customer history is empty.

An `AIInsightEpisode` keeps its immutable semantic decision and outbox admission
receipt. The read model joins the current job and transport receipt as
`notificationDelivery`; it never rewrites that original episode. The web must
show queued, suppressed, failed, unconfirmed, and delivered separately. A semantic
`send` or a `done` job without a transport receipt is not proof of delivery.

Profit/loss transitions are compared at the same one-decimal precision shown
in the customer message. This prevents a visible `1.0%p` move from being
silently rejected because hidden raw decimals differ by slightly less.

TypeDB action authority also splits publication paths. `originate` may enter
the AI investment-judgement contract. `modify` and `observe` enter a narrative
review path that may explain a verified risk or constraint but must publish
`NO_ACTION`; it cannot synthesize HOLD, BUY, TRIM, or SELL. If optional AI prose
fails, a materially authorized review may still deliver TypeDB facts without a
fabricated investment action.

### Infrastructure

`infrastructure/notification/` owns adapters:

- `ingress.py`: durable outbox producer adapters.
- `transport.py`: console and Telegram implementations.
- `mysql_notification_jobs.py`: job, lifecycle-event, and delivery-attempt
  persistence.

The old `application/notification_service.py` and
`infrastructure/notifications.py` paths are compatibility facades only.

## Runtime Flow

1. V1 or V2 produces an `AlertEvent` after its own reasoning completes.
2. `NotificationIngressService` creates `notification-request-v2` and copies
   the source event and reasoning identities into `NotificationSourceTrace`.
   Producers can call `enqueue_request(NotificationRequest)`. Legacy direct
   `NotificationJob` producers cross the same preparation boundary inside the
   MySQL adapter, preserving job IDs, source IDs and dedupe keys.
3. The MySQL adapter evaluates admission policy and atomically stores the job
   plus `received` and `eligibility_checked` events.
4. The worker claims the job. TypeDB action authority routes it to investment
   judgement or actionless review, then the cadence policy applies immediate,
   material, summary, or web-only delivery. Closed-market and stale-data
   findings are recorded without blocking AI or delivery. Actionable AI-gated
   jobs enter `awaiting_decision` and persist the final DecisionEpisode.
5. Dispatch eligibility is checked after the decision is stored. Market-hours
   and freshness are rechecked as advisories; stale investment data may request
   an asynchronous refresh while the current notification continues. The
   worker then freezes and validates one `CustomerDeliveryExplanation`.
   A contradictory investment transition is suppressed and reported to the
   operations channel. Missing optional explanation fields or presentation-only
   warnings do not veto an otherwise admitted message: the incomplete explanation
   stays in the audit and the available body is sent. Template exceptions use the
   source body. Channel splitting owns length limits; rendering never truncates
   source URLs to satisfy a message-size limit.
6. A delivery attempt is stored before calling Telegram or another channel.
7. The attempt and terminal lifecycle state are updated after the channel
   result. The read model exposes attempt start and channel completion as
   separate timeline entries so a mutable final attempt status cannot appear
   at its earlier start time. Failed jobs remain retryable under the existing
   queue policy.
8. `/api/notification-jobs/{id}` returns the complete lifecycle and delivery
   timeline. The notification detail UI displays it in stored chronological
   order and exposes the full JSON audit payload.

Telegram dispatch freezes the message and checkpoints each confirmed chunk in
the durable job context before continuing. A retry resumes the unsent suffix;
a complete checkpoint can recover a failed completion write without sending
again. Checkpoints bind the exact body and bot/destination identity. HTML falls
back to plain text only for confirmed parse errors. HTTP sends have no hidden
automatic retry, and Telegram `retry_after` is a lower bound on queue retry time.
Ready retries, abandoned claims and new jobs are merged by readiness time so
new arrivals cannot indefinitely displace due retries.

Telegram has no idempotency key: a process loss between server acceptance and
durable checkpointing, or a lost HTTP response, still leaves an ambiguous send.
This recovery protects confirmed chunks; it does not claim exactly-once delivery.

Every admission result also stores `notification-delivery-trigger-ledger-v2`.
This ledger keeps the configured condition, TypeDB relation-state change,
cooldown or repeat release, and final delivery gate as separate records. It is
delivery provenance only: it explains why a message was sent or withheld and
must never be used as investment evidence or an action-selection input.

The ledger is audit provenance, not customer prose. The customer message reads
only `customer-delivery-explanation-v1`, which has exactly one primary cause.
Matched TypeDB rules and their observed values remain in the inference section
and cannot be substituted for the delivery cause. Replay and verification jobs
carry their own source-event purpose even when an archived investment body is
preserved.

## Performance Rules

- No TypeDB query or AI inference is added to notification ingress, admission,
  rendering, or transport.
- Delivery-explanation finalization is one bounded in-memory pass over the
  already stored transition and trigger data. It performs no external or
  database read.
- Admission policy receives already-loaded history facts; it does not open a
  database connection.
- Initial lifecycle records are written in the same short transaction as the
  job. Delivery attempts use bounded indexed writes keyed by job and time.
- Full message text is rendered once per delivery attempt. The audit stores a
  hash and byte count rather than a duplicate message body.
- V1 and V2 share the ingress contract but retain independent reasoning queues,
  engine releases, and delivery authorization.

## Compatibility And Change Policy

New code imports the package modules directly. Existing callers may continue to
use the compatibility facades during migration. A future reasoning or AI
modularization must depend on `NotificationRequest` or publish its own domain
event; it must not move investment rules into this bounded context.

See [Notification Presentation Contract](notification-presentation-contract.md)
for the flexible input, kind catalog, and compatibility rules.
