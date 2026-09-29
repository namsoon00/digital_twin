# TypeDB server admission and recovery

The active and candidate reasoning deployments keep separate immutable graph
bindings but may share one physical TypeDB address. Their local writer guards
therefore serialize turns by normalized server address, across database names.
Separate server addresses remain independent. This boundary covers callers of
`LocalGraphWriterGuard`; native driver clients that bypass it are not covered.

Delivery/active workers reserve the next turn when waiting for a writer.
Candidate and maintenance workers yield to that reservation. The operating
system releases both locks on process exit. A running candidate turn is not
interrupted; delivery can wait for that bounded turn to finish. Candidate
throughput may decrease while delivery is continuously busy.

A retryable TypeDB request/timeout/connection failure records a shared server
cooldown before releasing ownership. The delay starts at 5 seconds, doubles
on consecutive failures, adds up to 5 seconds of jitter and caps at 120 seconds.
It survives worker replacement and applies before claiming another graph job.
An idle turn does not reset failure history; a completed reasoning turn does.
Background projection is skipped after such a failure so it cannot immediately
repeat load during cooldown. Existing durable job deferral and source lineage
remain authoritative; this admission change does not discard queued jobs.

Rollout requires restarting all managed workers together: older versions use
per-database lock paths and cannot coordinate with the server-scoped lock.
No TypeDB database recreation or source/queue reset is required.

Validation covers cross-database exclusion, independent servers, delivery
priority, crash release, persisted cooldown, success reset and background
suppression. Runtime acceptance additionally requires successful native TypeDB
completion, decreasing oldest request age and backlog, and the same source
identity reaching AI and a provider delivery receipt when delivery is eligible.
A worker heartbeat alone is not recovery. Historical observation gaps and
pre-change failures must remain in the continuity evidence.
