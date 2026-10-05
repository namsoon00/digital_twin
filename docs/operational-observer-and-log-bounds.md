# Operational observation and diagnostic log bounds

Long-lived verification supervisors must run each native/DB observation cycle
in a fresh process. `python_service/tests/isolated_observer.py` supervises a
checkpoint-aware `--once` command, uses an exclusive supervisor lock, bounds
cycle lifetime, reaps the isolated process group and records exit/timeout
receipts. The child remains the owner of the private state and append-only
sample history. Keep its salt, window, cursor, missed-sample count and original
evidence; maintenance is a gap, not a new successful observation. This contains
native allocation growth without asserting that a particular driver allocation
has been diagnosed or fixed. A small resident set alone does not prove recovery:
check physical footprint, swapped allocations and host free disk too.

`central_ai_health_reads.py` supplies a bounded round-robin primary-key task
page, recent indexed call/notification pages and PK-only detail/receipt reads.
It advances the task cursor only after all reads succeed. It marks results as
`bounded-health-sample-v1`, exposes truncation and never represents page counts
as cumulative success totals. The separate end-to-end verifier must still join
immutable input, native evidence, author result and the same job's verified
transport receipt for each release/window. Failed queries are missing samples,
not zero queues or application terminal failures.

Storage notifications distinguish increasing used bytes from decreasing free
bytes. A recovery requires 2 GiB above the disk alert threshold and components
at or below 5 percentage points under the component alert threshold. Settings
`operationalStorageRecoveryMarginMb` and
`operationalStorageRecoveryMarginPercent` configure these margins. Recovery
hysteresis affects notifications only; current write-admission/capacity state
continues to describe the live measurements. Escalation uses the incident's
last alerted severity so a threshold bounce does not re-page.

Every 60 seconds the managed supervisor checks only its explicitly named root
`.log` files (including disabled worker logs), taking an exclusive maintenance
lock. Up to four largest logs are compressed per pass, using 1 MiB buffers.
Per-file thresholds divide half `operationalLogMaxSizeMb` across known logs,
with a 1–32 MiB bound. Completed compressed archives are retained for at most
seven days and at most a quarter of the budget. Archives are included in the
storage inventory. Other logs, TypeDB's own internal rotation, database files,
domain events, delivery receipts and private verification artifacts are never
pruned by this helper. It cannot guarantee a total budget if unmanaged logs
alone exceed it; the normal capacity alert continues to expose that condition.

Rotation uses copytruncate to preserve existing append descriptors. Files that
grow during compression are deferred. As with standard copytruncate, diagnostic
bytes racing the last check/truncate may be lost; this is not suitable for
authoritative audit data. Archives are private, flushed before truncate, and
symlink/hardlink targets are excluded. Failed compression leaves the source
intact and is reported. A host restart is not required to reclaim observer
memory; macOS decides when unused swap files shrink.
