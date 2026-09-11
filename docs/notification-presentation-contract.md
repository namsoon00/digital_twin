# Notification Presentation Contract

The notification module owns templates and delivery, not investment analysis.
TypeDB and AI own the upstream facts, interpretation and final action. A missing
decision does not mean HOLD. Writing prose with AI does not grant action authority.

## Input

`NotificationRequest.from_dict` accepts a small envelope plus free text. Optional
fields enrich presentation but are not a completeness checklist. Unknown envelope
fields survive in `extensions`, and unknown content fields remain in the stored
`notificationContent` object. A missing event ID is traceable by the generated
request/job ID; when a source event exists, preserve its identity and dedupe key.

```python
from digital_twin.domain.notification.request import NotificationRequest
from digital_twin.infrastructure.notification.ingress import enqueue_request

request = NotificationRequest.from_dict({
    "accountId": "account-id",
    "kind": "news",
    "subject": {"symbol": "MSTR", "name": "Strategy"},
    "body": "The upstream news service supplies the verified summary here.",
    "trace": {"sourceEventId": "news-event-id"},
    "dedupeKey": "news-event-id:account-id",
    "content": {
        "links": [{"label": "Original article", "url": "https://example.com/article"}],
    },
})
accepted = enqueue_request(request)
```

`accepted` means durable queue admission, not external delivery. Inspect the job's
delivery attempt and terminal state to confirm the channel result. The queue is
still MySQL, with existing leases, retries, duplicate checks and worker polling;
this change does not introduce Kafka or claim exactly-once external delivery.

Optional content supports `summary`, `body`, `sections` (`title`, `lines`) and
`links` (`label`, `url`). One meaningful sentence is sufficient. Body text is
escaped when used as structured content; link URLs are not shortened or sliced.
Empty blocks are omitted. Exact repeated bullet rows are removed, but links with
different URLs remain distinct even if their visible labels are identical.

## Customer Kinds

| Kind | Customer Heading | Default Policy Type |
| --- | --- | --- |
| `price-change` | 📊 시세 변화 | `marketObservation` |
| `relation-change` | 🔗 관계 변화 | `investmentInsight` |
| `ai-interpretation` | 🧠 AI 해석 | `investmentInsight` |
| `investment-decision` | 🧭 투자 판단 | `investmentInsight` |
| `news` | 📰 뉴스·공시 | `newsDigest` |
| `account-change` | 💼 계좌 변화 | `portfolioActivityObservation` |
| `holdings` | 📋 보유 현황 | `portfolioHoldingsSnapshot` |
| `calendar` | 🗓️ 투자 일정 | `investmentCalendarReminder` |
| `operations` | ⚙️ 운영 상태 | Explicit operational type, or `notification` |
| `report` | 📦 개발·검토 보고 | Explicit report type, or `workHandoff` |
| Unknown | 🔔 알림 | `notification` |

Supply the existing `messageType` when preserving a particular operational rule.
Explicit `messageType` is never renamed. Notification purpose is metadata, not a
new investment rule. Stocks, holdings, watchlists and crypto are subject context,
not competing notification purposes.

The heading is `icon kind · subject`. Web list labels, type filters, detail labels
and delivered messages use the same catalog. The response exposes `notificationKind`,
`notificationKindLabel`, and `notificationKindIcon`; `messageType` still identifies
the existing admin rule and cadence policy. Existing rule-level statistics remain
rule-level statistics, rather than being silently relabeled as per-kind counts.
The lightweight web list projects only classification fields from the stored
context, including legacy metadata paths. It does not fetch full graph traces or
reconstruct an investment decision merely to display a type label and preview.
The kind selector filters the currently loaded page and is labeled accordingly.
It must not send a kind such as `ai-interpretation` as the legacy `messageType`
query parameter; doing so would hide valid history after the next refresh.

Legacy `investmentInsight` is resolved from its publication mode and saved action.
Reference-only or NO_ACTION results cannot be labeled as investment decisions.
An AI narrative needs upstream AI-writer provenance to use the AI label. A raw
price-only materiality trigger or crypto price threshold is a price observation,
not evidence that a new causal relationship was discovered.

## Validation And Delivery

- Missing optional fields: omit their sections and retain the available body.
- Unknown content fields/kinds: retain the data; use the generic presentation.
- Broken optional template: record a presentation warning and use the source body.
- Missing previous price: do not invent a zero baseline or price-change amount.
- No holding: do not show the provider's placeholder zero as a holding return.
- Missing AI result: do not call an AI model or manufacture an action in rendering.
- Missing explanation fields: record a partial explanation, not a delivery veto.
- Contradictory claimed action transition: keep the semantic safety check.
- Unknown recipient account: fail explicitly; never route to another account.
- Empty content: reject with an explicit reason rather than send an empty heading.

Rendering does not bypass upstream investment eligibility or delivery policy.
Optional-format recovery does not authorize unverified trade advice, enable muted
types, disable quiet hours, or reset cooldowns. Existing state fingerprints,
similarity history, source event IDs and dedupe keys are unchanged. Replays marked
`notificationReplayPreserveOriginal` keep their original bodies and envelopes.

## Maintenance

Own kind names/icons in `domain/notification/presentation.py` and formatting in
`application/notification/presentation.py`. Domain-specific producers supply the
actual news summary, relation change, decision or operational diagnosis. They must
not make the template layer fetch or re-infer missing facts. Account snapshots are
scoped to the requested account, including the upstream legacy enrichment adapter.

Contract tests cover each kind, free and structured input, unknown optional data,
template failure recovery, actionless AI narratives, unmodified cooldown identity,
account isolation, and queue-to-channel completion. No test needs live brokerage,
AI, TypeDB or Telegram credentials.
