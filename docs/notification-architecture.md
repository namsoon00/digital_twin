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

The domain package imports neither MySQL nor Telegram.

### Application

`application/notification/` owns use cases:

- `intake.py`: converts alerts or text into the stable request and durable job.
- `admission.py`: evaluates cooldown, similarity, market-hours, and initial
  freshness policy from repository-supplied history facts. For graph-backed
  investment insights, delivery-only failures are retained as deferred policy
  facts so they cannot prevent the AI decision from being completed.
- `eligibility.py`: rechecks live operational state and freshness at dispatch.
- `rendering.py`: creates the exact send-time artifact and content hash.
- `dispatch.py`: selects the account or operations audience and records the
  concrete delivery attempt.
- `workflow.py`: leases jobs and orchestrates the preceding services.
- `query.py`: builds the chronological trace returned by the web API.

AI validation and existing context enrichers are invoked by the workflow as
existing collaborators. Their investment semantics are intentionally unchanged
by this notification refactor.

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
4. The worker claims the job. A closed market, cooldown, or similar-message
   result does not block an otherwise material investment insight before AI.
   AI-gated jobs enter `awaiting_decision`, persist the final DecisionEpisode,
   and keep the earlier delivery assessment for the post-decision gate.
5. Dispatch eligibility is checked after the decision is stored. Market-hours
   policy may send all off-hours decisions, send only TypeDB-backed material
   events and urgent transitions, or defer delivery until a later observation.
   The final text is then rendered once and
   hashed.
6. A delivery attempt is stored before calling Telegram or another channel.
7. The attempt and terminal lifecycle state are updated after the channel
   result. The read model exposes attempt start and channel completion as
   separate timeline entries so a mutable final attempt status cannot appear
   at its earlier start time. Failed jobs remain retryable under the existing
   queue policy.
8. `/api/notification-jobs/{id}` returns the complete lifecycle and delivery
   timeline. The notification detail UI displays it in stored chronological
   order and exposes the full JSON audit payload.

Every admission result also stores `notification-delivery-trigger-ledger-v1`.
This ledger keeps the configured condition, TypeDB relation-state change,
cooldown or repeat release, and final delivery gate as separate records. It is
delivery provenance only: it explains why a message was sent or withheld and
must never be used as investment evidence or an action-selection input.

## Performance Rules

- No TypeDB query or AI inference is added to notification ingress, admission,
  rendering, or transport.
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
