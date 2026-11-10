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
    subject boundaries conservatively overlap. This reads only the pending slice;
    no historical source payloads or graph reads enter queue scheduling.
    """
    overlaps = []
    for field in ("symbols",):
        left = "JSON_EXTRACT(predecessor.request_json, '$.request." + field + "')"
        right = "JSON_EXTRACT(reasoning_engine_jobs.request_json, '$.request." + field + "')"
        overlaps.append(
            "(COALESCE(JSON_LENGTH(" + left + "),0)=0 OR "
            "COALESCE(JSON_LENGTH(" + right + "),0)=0 OR "
            "JSON_OVERLAPS(" + left + "," + right + "))"
        )
    return (
        " AND NOT EXISTS (SELECT 1 FROM reasoning_engine_jobs predecessor "
        "WHERE predecessor.deployment_id=reasoning_engine_jobs.deployment_id "
        "AND predecessor.job_status IN ('queued','retry','awaiting_source','awaiting_world_projection','processing') "
        "AND predecessor.source_snapshot_at <> '' "
        "AND predecessor.source_snapshot_at < reasoning_engine_jobs.source_snapshot_at "
        "AND " + " AND ".join(overlaps) + ") "
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
