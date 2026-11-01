"""Storage classes shared by explicit maintenance and automatic retention.

Age is not proof that a decision input is disposable. Cold records keep their
original identifiers, clocks and bytes; current and in-flight state stays hot.
"""

# These tables anchor the decision/evidence/delivery graph. Never
# trade their identity for a TTL or an aggregate. Physical compression is safe.
PROTECTED_HISTORY_TABLES = frozenset({
    "investment_decision_episodes", "investment_decision_outcomes",
    "investment_decision_outcome_targets", "investment_decision_follow_ups",
    "investment_decision_audit_entries", "investment_subject_decision_cases",
    "investment_reasoning_cases", "investment_ai_insight_episodes", "decision_candidate_snapshots",
    "decision_publications", "decision_notification_receipts",
    "notification_delivery_attempts", "investment_hypothesis_transition_history",
    "ai_control_tasks", "ai_control_inputs", "ai_control_input_calls",
    "reasoning_engine_release_artifacts",
})

# Full, lossless archival must succeed before a terminal row can leave MySQL.
# The archive is versioned and has no automatic expiry. Pending-reference
# checks remain mandatory; an archive is not permission to evict a live input.
ARCHIVE_BEFORE_DELETE = {
    "verified_reasoning_source_snapshots": ("snapshot_id",),
    "reasoning_source_facts": ("fact_id",),
    "reasoning_engine_jobs": ("job_id",),
    "reasoning_engine_job_sources": ("survivor_job_id", "source_event_id", "account_id", "symbol"),
    "notification_jobs": ("job_id",),
    "temporal_feature_snapshots": ("snapshot_id",),
    "statistical_model_signal_snapshots": ("snapshot_id",),
}

ARCHIVE_FORMAT = "orbit-history-archive-v1"
ARCHIVE_MAX_RECORD_BYTES = 64 * 1024 * 1024
ARCHIVE_MAX_CHUNK_BYTES = 64 * 1024
# Archival is essential preservation, but must leave the critical disk reserve.
ARCHIVE_MIN_FREE_BYTES = 6 * 1024 * 1024 * 1024
