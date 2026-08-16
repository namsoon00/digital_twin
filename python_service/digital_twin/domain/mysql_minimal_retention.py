"""Policy for retaining only the MySQL facts needed by the live product.

This is operational data governance, not investment reasoning.  The policy
describes which completed records may be compacted after their current-state
counterparts have been persisted.  It deliberately has no knowledge of a
database driver so it can be previewed and tested without mutating storage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Mapping


FALSE_VALUES = {"", "0", "false", "no", "off", "disabled", "disable", "none"}
MINIMAL_MYSQL_RETENTION_PROFILE = "minimal-mysql-retention-v2"


def _bool_setting(settings: Mapping[str, object], key: str, fallback: bool) -> bool:
    value = settings.get(key) if settings and key in settings else fallback
    return str(value if value is not None else "").strip().lower() not in FALSE_VALUES


def _int_setting(
    settings: Mapping[str, object],
    key: str,
    fallback: int,
    minimum: int,
    maximum: int,
) -> int:
    value = settings.get(key) if settings and key in settings else fallback
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def _mode(settings: Mapping[str, object]) -> str:
    value = str((settings or {}).get("mysqlMinimalRetentionMode") or "preview").strip().lower()
    return value if value in {"preview", "apply"} else "preview"


@dataclass(frozen=True)
class MySQLMinimalRetentionPolicy:
    """Bounded retention limits for the local MySQL operational store."""

    profile: str
    enabled: bool
    mode: str
    interval_seconds: int
    batch_size: int
    max_delete_bytes: int
    max_run_seconds: int
    snapshot_history_keep_count: int
    delivered_notification_keep_count: int
    terminal_notification_retention_hours: int
    completed_world_projection_retention_hours: int
    completed_inference_detail_retention_hours: int
    failed_world_projection_payload_retention_hours: int
    failed_world_projection_retention_hours: int
    projection_payload_retention_hours: int
    lifecycle_event_retention_hours: int
    research_terminal_retention_hours: int
    inactive_evidence_retention_hours: int
    completed_time_series_projection_retention_hours: int
    temporal_feature_snapshot_retention_hours: int
    reasoning_shadow_job_retention_hours: int
    reasoning_comparison_retention_hours: int
    audit_keep_count: int
    market_time_series_retention_days: Dict[str, int]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def mysql_minimal_retention_policy(settings: Mapping[str, object] = None) -> MySQLMinimalRetentionPolicy:
    """Build the minimal profile.

    An empty mapping remains preview-only for isolated callers and tests. The
    persisted runtime settings enable bounded application after owner approval.
    """

    configured = settings or {}
    internal_batch = "_effectiveMysqlMinimalRetentionBatchSize" in configured
    internal_bytes = "_effectiveMysqlMinimalRetentionMaxDeleteBytes" in configured
    internal_seconds = "_effectiveMysqlMinimalRetentionMaxRunSeconds" in configured
    return MySQLMinimalRetentionPolicy(
        profile=MINIMAL_MYSQL_RETENTION_PROFILE,
        enabled=_bool_setting(configured, "mysqlMinimalRetentionEnabled", False),
        mode=_mode(configured),
        interval_seconds=_int_setting(
            configured,
            "mysqlMinimalRetentionIntervalSeconds",
            120,
            60,
            24 * 60 * 60,
        ),
        batch_size=_int_setting(
            configured,
            "_effectiveMysqlMinimalRetentionBatchSize" if internal_batch else "mysqlMinimalRetentionBatchSize",
            100,
            1,
            1000 if internal_batch else 100,
        ),
        max_delete_bytes=_int_setting(
            configured,
            "_effectiveMysqlMinimalRetentionMaxDeleteBytes" if internal_bytes else "mysqlMinimalRetentionMaxDeleteBytes",
            64 * 1024 * 1024,
            256 * 1024,
            512 * 1024 * 1024 if internal_bytes else 128 * 1024 * 1024,
        ),
        max_run_seconds=_int_setting(
            configured,
            "_effectiveMysqlMinimalRetentionMaxRunSeconds" if internal_seconds else "mysqlMinimalRetentionMaxRunSeconds",
            30,
            1,
            120 if internal_seconds else 60,
        ),
        snapshot_history_keep_count=_int_setting(
            configured,
            "mysqlMinimalSnapshotHistoryKeepCount",
            2,
            1,
            12,
        ),
        delivered_notification_keep_count=_int_setting(
            configured,
            "mysqlMinimalDeliveredNotificationKeepCount",
            5,
            1,
            30,
        ),
        terminal_notification_retention_hours=_int_setting(
            configured,
            "mysqlMinimalTerminalNotificationRetentionHours",
            6,
            1,
            24 * 30,
        ),
        completed_world_projection_retention_hours=_int_setting(
            configured,
            "mysqlMinimalCompletedWorldProjectionRetentionHours",
            1,
            1,
            24 * 7,
        ),
        completed_inference_detail_retention_hours=_int_setting(
            configured,
            "mysqlMinimalCompletedInferenceDetailRetentionHours",
            24,
            1,
            24 * 30,
        ),
        failed_world_projection_payload_retention_hours=_int_setting(
            configured,
            "mysqlMinimalFailedWorldProjectionPayloadRetentionHours",
            24,
            1,
            24 * 30,
        ),
        failed_world_projection_retention_hours=_int_setting(
            configured,
            "mysqlMinimalFailedWorldProjectionRetentionHours",
            24 * 7,
            24,
            24 * 90,
        ),
        projection_payload_retention_hours=_int_setting(
            configured,
            "mysqlMinimalProjectionPayloadRetentionHours",
            6,
            1,
            24 * 30,
        ),
        lifecycle_event_retention_hours=_int_setting(
            configured,
            "mysqlMinimalLifecycleEventRetentionHours",
            6,
            1,
            24 * 30,
        ),
        research_terminal_retention_hours=_int_setting(
            configured,
            "mysqlMinimalResearchTerminalRetentionHours",
            24,
            1,
            24 * 90,
        ),
        inactive_evidence_retention_hours=_int_setting(
            configured,
            "mysqlMinimalInactiveEvidenceRetentionHours",
            24,
            1,
            24 * 90,
        ),
        completed_time_series_projection_retention_hours=_int_setting(
            configured,
            "mysqlMinimalCompletedTimeSeriesProjectionRetentionHours",
            6,
            1,
            24 * 30,
        ),
        temporal_feature_snapshot_retention_hours=_int_setting(
            configured,
            "mysqlMinimalTemporalFeatureSnapshotRetentionHours",
            24,
            1,
            24 * 30,
        ),
        reasoning_shadow_job_retention_hours=_int_setting(
            configured,
            "mysqlMinimalReasoningShadowJobRetentionHours",
            24,
            1,
            24 * 30,
        ),
        reasoning_comparison_retention_hours=_int_setting(
            configured,
            "mysqlMinimalReasoningComparisonRetentionHours",
            24 * 7,
            24,
            24 * 90,
        ),
        audit_keep_count=_int_setting(
            configured,
            "mysqlMinimalRetentionAuditKeepCount",
            100,
            10,
            1000,
        ),
        market_time_series_retention_days={
            "3m": _int_setting(configured, "mysqlMinimalTimeSeries3mRetentionDays", 2, 1, 30),
            "15m": _int_setting(configured, "mysqlMinimalTimeSeries15mRetentionDays", 10, 1, 90),
            "1h": _int_setting(configured, "mysqlMinimalTimeSeries1hRetentionDays", 90, 7, 730),
            "1d": _int_setting(configured, "mysqlMinimalTimeSeries1dRetentionDays", 180, 60, 3650),
        },
    )


def policy_cutoff_iso(hours: int, now: datetime = None) -> str:
    """Return a UTC ISO cutoff shared by preview and destructive execution."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current.astimezone(timezone.utc) - timedelta(hours=max(1, int(hours or 1)))
    return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def policy_cutoffs(policy: MySQLMinimalRetentionPolicy, now: datetime = None) -> Dict[str, str]:
    """Expose only operational cutoff values; no source payload enters reports."""

    return {
        "terminalNotifications": policy_cutoff_iso(policy.terminal_notification_retention_hours, now),
        "completedWorldProjection": policy_cutoff_iso(policy.completed_world_projection_retention_hours, now),
        "completedInferenceDetail": policy_cutoff_iso(policy.completed_inference_detail_retention_hours, now),
        "failedWorldProjectionPayload": policy_cutoff_iso(
            policy.failed_world_projection_payload_retention_hours,
            now,
        ),
        "failedWorldProjection": policy_cutoff_iso(
            policy.failed_world_projection_retention_hours,
            now,
        ),
        "projectionPayload": policy_cutoff_iso(policy.projection_payload_retention_hours, now),
        "lifecycleEvents": policy_cutoff_iso(policy.lifecycle_event_retention_hours, now),
        "researchTerminal": policy_cutoff_iso(policy.research_terminal_retention_hours, now),
        "inactiveEvidence": policy_cutoff_iso(policy.inactive_evidence_retention_hours, now),
        "completedTimeSeriesProjection": policy_cutoff_iso(
            policy.completed_time_series_projection_retention_hours,
            now,
        ),
        "temporalFeatureSnapshots": policy_cutoff_iso(
            policy.temporal_feature_snapshot_retention_hours,
            now,
        ),
        "reasoningShadowJobs": policy_cutoff_iso(
            policy.reasoning_shadow_job_retention_hours,
            now,
        ),
        "reasoningEngineJobs": policy_cutoff_iso(
            policy.reasoning_shadow_job_retention_hours,
            now,
        ),
        "reasoningComparisons": policy_cutoff_iso(
            policy.reasoning_comparison_retention_hours,
            now,
        ),
        **{
            "marketTimeSeries:" + granularity: policy_cutoff_iso(days * 24, now)
            for granularity, days in policy.market_time_series_retention_days.items()
        },
    }
