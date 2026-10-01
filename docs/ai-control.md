# Central AI control

`modules/ai_orchestration` owns independent observation, research scheduling,
execution accounting, bounded recall and recurring work. It is the thirteenth
business module. Domain-specific prompts and validators remain in their owning
contexts; runtime composition injects their capabilities into the controller.

## Running behavior

The managed `ai-control watch` worker discovers live portfolio/watchlist subjects
from persisted monitoring snapshots. It seeds one durable observation chain per
account and symbol. A matching RuleBox rule or a decision candidate is **not** a
prerequisite. Each observation reads bounded facts from the portfolio ABox and
its shared premise ABox, recording both snapshot identities and source clocks.
The versioned [observation evidence protocol](observation-evidence-protocol.md)
owns subject/linked/shared discovery, category budgets and explicit coverage.
Changing graph generations, missing facts and ownership mismatches defer work.

An observation compares those facts with up to three prior analyses (including
their dated numeric facts) and three research results. It produces an explicitly
unverified hypothesis, counter-evidence, comparison, up to two research questions
and a next-check interval of 60–1440 minutes. Identical monitored inputs and
research memory skip the model for up to six hours, with another check in three
hours. The fingerprint covers supplied business fields by default, including
nested source revisions, quality and eligibility. Only declared polling/storage
metadata is ignored. Excluded and unsupported facts retain an inventory digest.

Questions explicitly select an allowed capability. Future price/order-flow
checks remain observations and wait for the existing collectors; they do not
launch unrelated news searches. Documentary research questions call the existing research orchestration service, retaining
source verification, durable ResearchRun records, and source-change events into
graph projection. AI-generated account IDs, commands, URLs, trading actions or
unregistered capabilities are rejected. No automatic rule or model promotion is
introduced. The owner-requested central AI cutover retires the former
`investmentInsight` customer route, including relation-change and AI review
messages. Graph collection, RuleBox inference and internal case history continue
as evidence; they cannot trigger the former customer AI queue.

## Independent observation notifications

The planner must explicitly return `notification: {send: boolean, reason: string}`.
It sees the last **successfully delivered** observation, including its dated facts,
as well as prior internal analyses. It should notify only for a useful new
interpretation: a hypothesis-changing trend, conflicting signals or important
new evidence. Price changes, polling timestamps or repeated missing data alone
are insufficient. There is no rule-match trigger and no second AI rewrite call.

An actionless `aiObservation` outbox entry is committed in the same transaction as
the lease-fenced observation and successors. Unknown evidence IDs, trading
directives, ungrounded numbers and oversized narrative blocks cannot be published.
Facts come from the immutable ABox packet; text generation is not a source of
prices. The message shows price, change, holding cost/P&L when held, available
moving averages/volume/flows, price clock, novelty, comparison, hypothesis,
counter-evidence and next questions. Missing metrics are omitted. Financial
claims still require their source verification; this route grants no trading
authority and never manufactures a `DecisionEpisode`.

Before transport, an account lock covers durable task/job ownership verification,
active subject membership, exact rendered-content verification, repeat checks and
recording the transport receipt. Quotes must have a known source time no more than
24 hours old; captured analysis input must be no more than 30 minutes old. The
message labels the price's own time, not the send time. A pending alert that ages
out remains in history and the normal next observation reconsiders the subject.

Initial frequency policy: **at least 180 minutes per account/symbol, at most two
per symbol and eight per account per UTC day**. Only successful delivery receipts
consume these limits and become the next comparison baseline. Identical input
fingerprints, an advanced comparison baseline, replay of delivered jobs and
retired subjects are suppressed. Policy/validation silence remains visible on
the central AI page. Partial transport retries preserve the exact text and clock;
as with existing delivery, a provider success followed by a process crash before
receipt persistence cannot promise exactly-once external delivery.

Runtime settings pin `investmentNotificationRoute=ai-control`; the old worker
count cannot revive it. New legacy queue admissions, claims, completions, direct
notification admission and final sends are blocked. The central worker retires
pending/in-flight legacy requests as superseded and old pending notifications as
suppressed, retaining their history and reason. Historical component tests can
still exercise old contracts using an explicit non-runtime settings fixture;
there is no production UI switch back to that route.

## Execution and limits

Independent observation calls first persist their exact input, selected memories
and prompt locally, including failed attempts; calls require a matching task and
prompt hash. Existing domain workloads retain their own audit contracts.
All existing background Codex adapters, investment judgement and interactive
chat enter the central execution ledger. The ledger records workload, prompt
hash, start/completion state and a safe error category, never the prompt or raw
provider errors. Existing capacity reservations and domain-specific validation
remain in effect. News, disclosure and model-review queues retain their own jobs. The former
rule-triggered investment AI workers are retired.

Independent work defaults to **48 task starts and 24 model calls per UTC day**.
Retries consume budget too. Tasks use MySQL leases, heartbeat renewal, fenced
completion, stable root identities and transactional successor creation. A
completed ResearchRun is reused after retry. Source-provider work is at-least-once
when a process dies before its result is saved; this is not exactly-once external
execution. Three failed attempts terminate the task; failed observations start a
new recovery check six hours later. Removed subjects are retired when claimed.
Older in-progress process audit rows may remain `running` after an abrupt kill;
they must not be interpreted as proof the process still exists.

`aiControlEnabled=false` pauses central tasks and suppresses pending central
notification delivery; in-flight analyses may finish and remain in history.
`notificationAiQueueWorkerCount` no longer controls central AI. The managed central
worker has its own enable switch and reloads settings before each tick. News,
disclosure and other domain AI features retain their own admission budgets.
Priority is research follow-up before new observations, then oldest due work.

## Operator surfaces

- `/ai-control.html`: owner-only data, recent analyses, dated facts, schedules,
  process counts, enable switch and daily limits; linked from Operations.
- `GET /api/ai-control/status`: bounded owner-only task and call summary.
- `PUT /api/ai-control/settings`: writable-owner settings, validated limits.
- `python3 python_service/service.py ai-control status|once|watch`.

Tests in `test_ai_control.py` cover untrusted plans, independent operation,
account retirement, pause, lease loss, audit redaction, durable deduplication,
atomic successor creation, rollback and call/task budget enforcement. Existing
AI model, module-boundary and runtime-composition tests cover compatibility.

`test_ai_control_publication.py` verifies retirement, manual/replay blocks, stale
quotes, numeric/actionless validation, account isolation, cooldown boundaries,
atomic outbox rollback, successful-receipt memory and end-to-end worker delivery
without invoking the legacy reviewer.
