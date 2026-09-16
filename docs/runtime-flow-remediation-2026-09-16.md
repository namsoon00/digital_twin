# Runtime Flow Integrity Follow-Up

## Implemented Boundaries

1. KIS websocket idle reads handle both builtin `TimeoutError` and
   `socket.timeout` on Python 3.9. Real connection errors still trigger recovery.
2. Quiet-hours suppression records `account_quiet_hours`, including the final
   subject outcome and durable lifecycle trace. Disabling quiet hours persists
   across unrelated account edits; delivery qualification and cooldowns remain.
3. Context-only AI responses preserve `NO_ACTION`; the response parser no longer
   converts them to `HOLD`. The prompt and repair path prohibit implicit trading
   advice in actionless prose. Publication checks common directive patterns and
   leaked implementation identifiers. This is not a complete semantic proof.
4. Continuity v3 separates frozen current facts from historical account values.
   Missing source binding cannot claim frozen provenance. Unverified legacy
   hypothesis descriptions and observations after the reference cutoff are
   withheld without rewriting the historical ledger.
5. AI attempt queue time, cumulative request age and model/repair time are
   distinct. Repair honors its configured effort. Valid research comparison
   does not cause another model call simply because it is not execution-ready.
6. Evolution diagnoses missing inference, unobserved candidate conditions,
   ineligible candidates, missing frozen inputs and missing comparators before
   requesting future outcomes. Outcome diagnostics use actual market-calendar
   target times. External review no longer masks the earlier data gap.
7. Passive end-to-end proof joins the exact executed source boundary instead of
   expanding all coalesced predecessor events. Retained lifecycle reasons explain
   delivery suppression even after short-lived outbox rows are cleaned up.

## Validation Scope

- Focused tests exercise idle websocket reads, account updates, immutable
  continuity, narrative parsing/publication, attempt timing and durable delivery
  reasons.
- The experiment integration test uses a real temporary dataset store and
  source cleanup, then outcome pairing and the evolution application service.
  Controlled candidate wins trigger adoption; a separate regression cohort
  triggers rollback. Release side effects are mocked. These are not empirical
  market returns or proof that the live candidate is better than its baseline.
- Browser fixtures use their run timestamp so today's-review tests do not fail
  merely because the fixture date has aged. Desktop/mobile checks cover bundled
  and module rendering, experiment status, navigation and canvas contents.
- Read-only live diagnosis found 72 recent candidate-deployment subject cases
  but no exact candidate-claim matches. This is condition absence in that
  bounded sample, not a proven collection failure or successful experiment.
- Measured lineage reads returned 5 jobs in 100 ms and 20 jobs in 639 ms without
  truncation. This is read-side improvement, not faster TypeDB execution or an
  end-to-end latency guarantee.

## Operational Verification

After the committed-code restart, inspect managed worker status, new source
progress, AI completion and verified account-channel receipts. Do not force a
trade alert, relax qualification, manufacture an outcome or promote a shadow
candidate merely to obtain a green health check. Keep the final execution
report explicit about any stage not observed after restart.

### Observed Run

- On 2026-09-16, the committed `e64fbc1be` runtime was restarted with MySQL and
  TypeDB preserved. A 300-second passive run took all 11 scheduled observations;
  infrastructure, source progression, linked AI and Telegram delivery passed.
- Two real AI results used prompt v26 and continuity v3, preserved `NO_ACTION`,
  and passed publication validation without a repair call. One account-channel
  investment insight had a verified Telegram receipt. This is a provider receipt,
  not evidence that the recipient read the message.
- The completed AI jobs continued source flows begun before the observation
  window. No entire source-to-AI flow began and ended within those five minutes.
  Some other retained rows lacked historical lineage; the two matched results
  had complete identity links. This is not proof of every historical notification.
- The live retry inspection exposed that `started_at` preserves the first
  attempt. Queue timing was corrected to use the fresh claim's `updated_at`
  before heartbeat updates, and verified with a real test-database reclaim.
- Native inference and model execution still take time. The two model calls
  took about 211 and 220 seconds; this run does not establish lower end-to-end
  latency or empirical superiority of the shadow experiment.
