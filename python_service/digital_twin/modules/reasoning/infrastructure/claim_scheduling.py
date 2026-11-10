"""Durable scheduling turns, independent of investment judgement and sources."""

LIVE_BURST = 2
LIVE_STATE = "(supersedable = 1 AND reasoning_lane = 'REALTIME' AND source_snapshot_at <> '')"


def source_predecessor_filter():
    """Reorder disjoint subjects, never overlapping immutable source clocks.

    Current-state and mixed-fact mailbox slots can coexist for one subject.
    Running the newer slot first would let the older mixed request roll its
    market scopes back later. An older pending source therefore remains a
    dependency, including a delayed retry. Market scopes are shared across
    accounts, so subject overlap blocks even when account IDs differ. Unknown
    subject boundaries conservatively overlap. Use the current event's small,
    indexed lineage keys instead of decoding every pending request body. A
    legacy job without lineage conservatively overlaps rather than weakening
    the source-order barrier. No historical payloads or graph reads are needed.
    """
    overlaps = (
        "(NOT EXISTS(SELECT 1 FROM reasoning_engine_job_sources current_source "
        "WHERE current_source.survivor_job_id=reasoning_engine_jobs.job_id "
        "AND current_source.source_event_id=reasoning_engine_jobs.source_event_id) "
        "OR NOT EXISTS(SELECT 1 FROM reasoning_engine_job_sources older_source "
        "WHERE older_source.survivor_job_id=predecessor.job_id "
        "AND older_source.source_event_id=predecessor.source_event_id) "
        "OR EXISTS(SELECT 1 FROM reasoning_engine_job_sources current_source "
        "JOIN reasoning_engine_job_sources older_source "
        "ON older_source.survivor_job_id=predecessor.job_id "
        "AND older_source.source_event_id=predecessor.source_event_id "
        "AND (older_source.symbol=current_source.symbol "
        "OR older_source.symbol='' OR current_source.symbol='') "
        "WHERE current_source.survivor_job_id=reasoning_engine_jobs.job_id "
        "AND current_source.source_event_id=reasoning_engine_jobs.source_event_id))"
    )
    return (
        " AND NOT EXISTS (SELECT 1 FROM reasoning_engine_jobs predecessor "
        "WHERE predecessor.deployment_id=reasoning_engine_jobs.deployment_id "
        "AND predecessor.job_status IN ('queued','retry','awaiting_source','awaiting_world_projection','processing') "
        "AND predecessor.source_snapshot_at <> '' "
        "AND predecessor.source_snapshot_at < reasoning_engine_jobs.source_snapshot_at "
        "AND " + overlaps + ") "
    )


def claim_turn(connection, deployment_id, *, lock=False, stamp=""):
    if lock:
        connection.execute(
            "INSERT IGNORE INTO reasoning_engine_claim_turns (deployment_id, live_streak, updated_at) VALUES (%s,0,%s)",
            (deployment_id, stamp),
        )
    row = connection.execute(
        "SELECT live_streak FROM reasoning_engine_claim_turns WHERE deployment_id=%s"
        + (" FOR UPDATE" if lock else ""), (deployment_id,),
    ).fetchone() or {}
    return max(0, min(LIVE_BURST, int(row.get("live_streak") or 0)))


def claim_order(streak, aged_cutoff):
    if streak >= LIVE_BURST:
        return (
            "CASE WHEN priority >= 100 THEN 0 ELSE 1 END, "
            "CASE WHEN created_at <= %s THEN 0 ELSE 1 END, "
            "CASE WHEN created_at <= %s THEN created_at ELSE '' END, "
            "priority DESC, CASE reasoning_lane WHEN 'REALTIME' THEN 0 WHEN 'CONTEXT' THEN 1 ELSE 2 END, created_at, job_id",
            (aged_cutoff, aged_cutoff),
        )
    return (
        "priority DESC, CASE WHEN " + LIVE_STATE + " THEN 0 ELSE 1 END, "
        "CASE WHEN " + LIVE_STATE + " THEN source_snapshot_at ELSE '' END DESC, "
        "CASE reasoning_lane WHEN 'REALTIME' THEN 0 WHEN 'CONTEXT' THEN 1 ELSE 2 END, created_at, job_id",
        (),
    )


def finish_claim_turn(connection, deployment_id, streak, anchor, stamp):
    live = bool(anchor.get("supersedable") and anchor.get("reasoning_lane") == "REALTIME"
                and anchor.get("source_snapshot_at"))
    # An explicit critical/validation priority retains its precedence without
    # consuming an already due history turn. Empty/rolled-back claims never
    # advance this cursor. One micro-batch is one scheduling turn.
    if streak >= LIVE_BURST:
        next_streak = LIVE_BURST if int(anchor.get("priority") or 0) >= 100 else 0
    else:
        next_streak = min(LIVE_BURST, streak + 1) if live else 0
    connection.execute(
        "UPDATE reasoning_engine_claim_turns SET live_streak=%s, updated_at=%s WHERE deployment_id=%s",
        (next_streak, stamp, deployment_id),
    )
