# End-to-End Flow Acceptance

## Scope

The existing SubjectDecisionCase, CandidateSetSnapshot, immutable AI inference
packet, DecisionPublication and outcome targets remain authoritative. This work
does not add a parallel inference engine, change TypeDB action policy, relax
cooldown, or force a notification to manufacture a successful test.

Technical completion, valid AI authorship, publication, delivery and empirical
investment outcomes are separate results. A completed queue job containing a
TypeDB fallback is not an AI success. An explained suppression is not a failed
Telegram delivery. A future outcome target is not an evaluated hypothesis.

## Reproduced Contract Defects

1. The repair prompt had a second response schema without disagreementReason
   or legacy evidence fields required downstream. Repair now imports the same
   response schema as initial generation, preserving its hard byte limit.
2. A response with packet-verified narrative claims but no duplicated evidence
   text was labelled blocked. Validation now counts the unique verified fact
   references as evidence; rejected claims and unavailable data still block.
3. A valid counter claim was ignored without a duplicate counterEvidence list.
   The verified counter text is now recognized before readiness is computed.
4. Explicit disagreement was dropped when the final action equalled the global
   precomputed action, even when the selected hypothesis proposed another action.
   Explicit authored disagreement is now retained.
5. Local repair could pass before the persisted subject contract rejected it.
   The queue injects that same validator into the initial/repair path. Final
   publication still rechecks identity and delivery independently.
6. Missing causal explanations could be filled with a copy of the conclusion.
   Cross-role aliases were removed. Same-role structured AI text may be recovered
   and verified; genuinely absent content receives at most one model repair.
7. Forced cache refresh failure could label an old model release fresh. Failure
   and nested component freshness now remain explicit; health refresh propagates
   to the external-data status cache.

Initial and repair raw responses are retained in the existing compressed execution
audit, under its existing local retention policy. They are private data, not git
fixtures or customer messages. Historical failures with discarded model responses
cannot be claimed as exact raw-response replays. Their contracts are reproduced
with credential-free fixtures instead.

## Verification Matrix

| Boundary | Regression coverage |
| --- | --- |
| Source account, subject, original observation time | verified snapshot, reasoning snapshot and point-in-time replay tests |
| Fact values, evidence scope, financial reporting periods | decision evidence assertion and insight evidence flow tests |
| AI input, claims, action restrictions, disagreement and repair | notification AI inference packet and inference queue tests |
| Subject recovery, duplicate work and delivery reconciliation | subject decision recovery, reasoning job fences and decision delta delivery tests |
| Future observations, missing benchmark, immutable baseline | decision outcome targets, follow-up tracking and benchmark collection tests |
| Passive SQL identity, polling deduplication, incomplete samples | runtime continuity, MySQL literal and flow accounting tests |

These are contract/failure-path tests, not a claim that every provider fact is
correct or that an investment recommendation is profitable.

## Passive Runtime Acceptance

`npm run python:verify:runtime` remains read-only: no model execution, queue writes,
repairs, threshold changes or notification sends. Supply explicit local MySQL
environment variables as described in runtime-continuity-verification.md.

Use `--cohort-since <deployment-start-UTC>` to exclude pre-deployment requests.
The request-led cohort includes pending, failed, superseded and fallback cases,
not only successful inference descendants. Results are salted identity hashes;
neither account names, symbols, raw errors nor model content are exported.

The default `--minimum-ai-samples 30` is an operational acceptance sample size,
not a statistical investment-confidence threshold. Repeated polls of one request
count once. Different rule/prompt release cohorts, truncated samples, missing
reads and pending deliveries cannot receive a complete cohort verdict. Fallbacks,
unvalidated results, unexplained delivery and identity conflicts are reported
separately rather than hidden by one successful result.

Exit 0 requires infrastructure, source progress, a live exact-linked AI completion,
the request cohort, and a real Telegram receipt in the observation window to pass.
Exit 1 means a detected defect/degradation. Exit 2 means incomplete evidence, not
success. The cohort is not all-service coverage: domestic/US sessions and
holding/watchlist coverage must also be recorded in the handoff report.

Outcome targets attached to the cohort's decision publications are separately
classified as observed, future, within observation window, explained data gap,
invalid outcome link, or overdue/unexplained. Explained data gaps are not empirical
successes. No outcome target for an actionless research narrative is expected;
the report must not turn that absence into a fabricated investment outcome.

## Rollout Record

Before this change was deployed, a bounded four-hour read on 2026-09-22 contained
33 real requests: 21 AI-authored results, 9 TypeDB fallbacks, 3 superseded requests.
The 21 authored results had 8 verified Telegram receipts and 13 explained
withholdings. Two decision outcome targets had not reached their observation
time. These are baseline observations, not post-change success statistics.

Post-change acceptance requires a new same-revision run. A short observation,
historical replay or a single delivered message cannot complete the 30-case,
domestic/US-session and due-outcome checks. Report such checks as pending.
