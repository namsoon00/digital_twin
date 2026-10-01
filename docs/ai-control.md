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
Changing graph generations, missing facts and ownership mismatches defer work.

An observation compares those facts with up to three prior analyses (including
their dated numeric facts) and three research results. It produces an explicitly
unverified hypothesis, counter-evidence, comparison, up to two research questions
and a next-check interval of 60–1440 minutes. Identical monitored inputs and
research memory skip the model for up to six hours, with another check in three
hours. The fingerprint deliberately covers supported quote/trend, flow,
position, claim and source-quality fields; it is not a universal materiality
classifier or a promise to react to every graph field.

Questions explicitly select an allowed capability. Future price/order-flow
checks remain observations and wait for the existing collectors; they do not
launch unrelated news searches. Documentary research questions call the existing research orchestration service, retaining
source verification, durable ResearchRun records, and source-change events into
graph projection. AI-generated account IDs, commands, URLs, trading actions or
unregistered capabilities are rejected. Collected evidence then travels through
the existing graph/decision/publication pathway. An internal research hypothesis
does not itself become a RuleBox release, trading decision or customer alert.
Research reports and prior observations remain internal evidence until those
domain gates admit publication. No automatic rule or model release promotion is
introduced.

## Execution and limits

All existing background Codex adapters, investment judgement and interactive
chat enter the central execution ledger. The ledger records workload, prompt
hash, start/completion state and a safe error category, never the prompt or raw
provider errors. Existing capacity reservations and domain-specific validation
remain in effect. Domain queues still own their jobs; this change does not move
their transactional ownership or cancel their workers.

Independent work defaults to **48 task starts and 24 model calls per UTC day**.
Retries consume budget too. Tasks use MySQL leases, heartbeat renewal, fenced
completion, stable root identities and transactional successor creation. A
completed ResearchRun is reused after retry. Source-provider work is at-least-once
when a process dies before its result is saved; this is not exactly-once external
execution. Three failed attempts terminate the task; failed observations start a
new recovery check six hours later. Removed subjects are retired when claimed.
Older in-progress process audit rows may remain `running` after an abrupt kill;
they must not be interpreted as proof the process still exists.

`notificationAiQueueWorkerCount=0` also pauses independent work. A running central
worker reloads settings before each tick. `aiControlEnabled=false` pauses new
central tasks; it does not abort in-flight calls or disable the other domain AI
features. Existing AI queues retain their own admission budgets. Priority is
research follow-up before new observations, then oldest due work; this is not an
AI-generated investment ranking.

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

The existing relation-change Telegram wording is not replaced by this module.
Its prior frozen transition evidence remains authoritative. This work adds an
independent research/control surface rather than fabricating new delivery
semantics for research notes.
