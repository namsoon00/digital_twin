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
are insufficient. There is no rule-match trigger. A second call critiques admitted
drafts against their exact evidence; it does not rewrite the customer message.

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

Frequency policy: **180 minutes per account/symbol, at most two per symbol and
eight per account per UTC day**. A newly verified false-to-true transition of a
previously delivered `invalidates` condition may use a 60-minute interval. Daily
limits still apply. A price movement or model-authored urgency cannot waive them. Only successful delivery receipts
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
The owner can set `aiControlBudgetEnabled=false` to remove both daily limits.
Usage accounting, duplicate-input suppression, leases, process concurrency and
notification quality/frequency policies still apply. Numeric limits remain saved
for re-enabling; zero keeps its existing meaning of no capacity when limits are
enabled. Unlimited operation does not erase consumed calls or task history.
Retries that execute work consume budget too. Admission checks both budgets
before claiming a task; a new observation needs room for generation and an
independent critique. Exhaustion is `budget-wait`, not an idle worker or a
failed analysis. It preserves the pending job without using task attempts.
A call-budget race during execution defers the same leased task until the next
UTC day and does not consume its failure allowance; a raised limit lets that
task resume earlier. Counters and frozen input history are never reset to
manufacture capacity. The owner page shows the independent call counter,
remaining capacity, limit wait and next reset separately from other AI workloads.
Tasks use MySQL leases, heartbeat renewal, fenced
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

An explicit send candidate that fails final publication validation now creates
one `aiObservationDiagnostic` operations notification in the same transaction as
the completed observation. It shows the rejected prose, validator/reviewer
reasons, correction status and the original captured quote. The prominent
`검증 미통과 초안` label also makes possible validator false positives visible;
it does not claim every rejected sentence is false. Ordinary `send=false`
observations remain silent, and an accepted corrected draft follows the normal
observation route. Parse/provider failures without a completed usable draft stay
in the task error ledger; this is not a fallback that invents an AI message.

Diagnostics use the configured operations/global-owner destination, never the
observed account's notification destination. They bypass account quiet hours and
investor cooldowns/quotas; the stable task-based outbox key and successful receipt
prevent duplicate sends. They do not update the investment delivery baseline,
follow-up conditions, decision outcomes or notification quota. The final transport
guard compares the exact diagnostic body with the completed task. An unavailable
operations transport remains a visible delivery failure, without account fallback.

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

## Evidence-bound customer explanations

`observation-insight-v1` requires a current/baseline fact ID and exact field path
for each of six explanation sections, observed numeric comparisons and one to
three executable follow-up conditions. Free prose cannot supply quantities;
values, labels, units and clocks are rendered from frozen facts. Small nonzero
ratios display as less than 0.01 instead of zero. Missing zero/default changes
are omitted when the quote is partial. The source is the provider, not the
holding/watchlist role. Daily-volume ratios are explicitly not same-time-of-day
comparisons.

The output JSON schema is generated from captured fact IDs and scalar paths and
persisted alongside the exact prompt. Model generation uses this schema; local
validation still checks field ownership, time, comparison truth/units, unsupported
flows, reference-only evidence and selected unsupported causal/certainty language.
Generation v4 binds each reference to its actual fact, field and period. Reference
facts are available only to the limitation section; numeric comparisons exclude
monetary facts without a recorded currency. Historical generation v3 inputs retain
their original prompt/schema validation and are never rewritten for this upgrade.
Period shorthand such as `5·20·60일선`, explicit negated certainty and statements
about limited data reliability do not by themselves assert a quantity or a price
cause. Unsupported quantities, asserted causes and investment directives still fail.
The draft's natural-language meaning is then independently critiqued with
`observation-review-v1`. Every section must be supported; generic or repeated
explanations remain internal. This critique is another AI assessment, not proof
of every possible semantic claim or commercial usefulness.

Generation, correction and review consume the existing central call budget. Only an
explicit send candidate that passes local checks receives a critique. Each review
has a frozen draft/input/schema, persisted execution record and completed model
call. Admission and final transport recheck that proof and the draft hash. Legacy
observations without this contract cannot pass the new final gate. Successful
receipts preserve the cited fields, explanation fingerprint and registered
conditions. Exact price jitter with the same cited relationships does not create
a new explanation; the reviewer also compares meaning with the last receipt.

If a send candidate fails deterministic validation, or independent review finds
grounding errors in an otherwise useful new explanation, it gets at most one correction
on the same frozen facts. The original draft, validation errors, comparison
diagnostics, any independent critique and parent input ID are persisted before that call. The correction may
choose silence and still needs full validation and independent review before
publication. Repetition and generic content do not trigger review-driven correction.
A failed correction never grants delivery permission. Rejected work
and results from an older generation prompt cannot activate the six-hour unchanged
input shortcut; only validated observations or accepted drafts from the current
prompt may reuse an unchanged input. No historical failed alert is auto-delivered.

Legacy receipt summaries omitted some quote fields, including currency and the
five-day average. For a new observation only, memory may recover those fields from
the original completed delivery task's frozen stock facts. Account, subject, task,
receipt job, input fingerprint, source/capture clocks and every retained scalar
must agree. Missing originals or mismatches remain incomplete. Added fields carry
explicit original-input provenance; neither receipt rows nor historical execution
inputs are rewritten, and current market data is never used for this recovery.

Follow-up evaluation belongs to `outcomes`. It compares the next collected stock
values with the two registered fields, using source clocks and a fixed horizon.
An already-true condition must become false before a new true transition counts.
Unchanged clocks, unavailable data and expired windows cannot become a success or
failure. Conditions support/weakening/invalidation describe an observation
contract, not calibrated prediction performance, and do not create investment
DecisionEpisodes. Checks run at scheduled observations, not on every market tick.
The next delivered explanation supersedes the prior receipt's active conditions.
Skipped model calls still persist condition state. Source currency changes and
unusable observations cannot trigger a check; cross-period comparisons require
chronological source timestamps.

The owner page shows rejection reasons, sentence references, condition results and
the actual transported text from the receipt (not a freshly rendered replacement).
Its review counts are operational quality diagnostics, not paid-service or return
qualification. User usefulness and willingness to pay still require a pilot.

Verification:

- `test_ai_insight_quality.py`: wrong price/cost and return/weight claims, unsupported
  causes and moving-average slopes, timestamps, units, critique tampering, semantic
  repetition, durable follow-up state and review failure.
- `test_ai_control_publication.py`: actual isolated MySQL review proof, atomic
  outbox, delivery receipt and retirement boundaries.
- `node scripts/test-ai-control-browser.cjs`: desktop/mobile receipt display,
  rejected explanations, expired checks, escaping and layout.
- `python3 scripts/replay-ai-observation.py /private/capture.json --output /private/output`:
  real model generation/critique from preserved historical facts, with zero queue
  writes or transport calls. Historical evidence is never enriched with later
  facts. It writes private local inputs, outputs and rendered messages. Central
  execution audit rows are recorded for model calls. Passing this replay is not
  a live delivery receipt.
