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

- `NotificationRequest`: version-neutral producer input.
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
- `dispatch.py`: selects the account or operations audience and records the
  concrete delivery attempt.
- `workflow.py`: leases jobs and orchestrates the preceding services.
- `query.py`: builds the chronological trace returned by the web API.

AI validation and existing context enrichers are invoked by the workflow as
existing collaborators. Their investment semantics are intentionally unchanged
by this notification refactor.

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
  is 360 minutes.
- `web-only`: reference or unchanged state with no user decision value. It is
  retained for audit without interrupting the user.

The admin notification rule stores all three time intervals. A verified
immediate or material change is evaluated before unchanged-relation
suppression, and an unchanged relation becomes eligible for a scheduled summary
after the configured interval. This prevents a stable TypeDB fingerprint from
silencing a position forever.

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
2. `NotificationIngressService` creates `notification-request-v1` and copies
   the source event and reasoning identities into `NotificationSourceTrace`.
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
   A contradictory investment explanation is suppressed and reported to the
   operations channel. The final text is rendered once and hashed only after
   that contract passes.
6. A delivery attempt is stored before calling Telegram or another channel.
7. The attempt and terminal lifecycle state are updated after the channel
   result. The read model exposes attempt start and channel completion as
   separate timeline entries so a mutable final attempt status cannot appear
   at its earlier start time. Failed jobs remain retryable under the existing
   queue policy.
8. `/api/notification-jobs/{id}` returns the complete lifecycle and delivery
   timeline. The notification detail UI displays it in stored chronological
   order and exposes the full JSON audit payload.

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
