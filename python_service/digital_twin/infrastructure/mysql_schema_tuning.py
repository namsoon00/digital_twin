import os
import re
import warnings
from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class MySQLIndexDefinition:
    table: str
    name: str
    columns_sql: str

    def alter_sql(self) -> str:
        return (
            "ALTER TABLE "
            + quote_identifier(self.table)
            + " ADD INDEX "
            + quote_identifier(self.name)
            + " ("
            + self.columns_sql
            + ")"
        )


@dataclass(frozen=True)
class MySQLUniqueIndexRetirementDefinition:
    table: str
    name: str

    def alter_sql(self) -> str:
        return "ALTER TABLE " + quote_identifier(self.table) + " DROP INDEX " + quote_identifier(self.name)


@dataclass(frozen=True)
class MySQLColumnDefinition:
    table: str
    name: str
    definition_sql: str

    def alter_sql(self) -> str:
        return (
            "ALTER TABLE "
            + quote_identifier(self.table)
            + " ADD COLUMN "
            + quote_identifier(self.name)
            + " "
            + self.definition_sql
        )


@dataclass(frozen=True)
class MySQLColumnCompatibilityDefinition:
    table: str
    name: str
    definition_sql: str

    def alter_sql(self) -> str:
        return (
            "ALTER TABLE "
            + quote_identifier(self.table)
            + " MODIFY COLUMN "
            + quote_identifier(self.name)
            + " "
            + self.definition_sql
        )


@dataclass(frozen=True)
class MySQLColumnRetirementDefinition:
    """A no-longer-used column that can be removed after its replacement ships."""

    table: str
    name: str

    def alter_sql(self) -> str:
        return "ALTER TABLE " + quote_identifier(self.table) + " DROP COLUMN " + quote_identifier(self.name)


@dataclass(frozen=True)
class MySQLKeyPartitionDefinition:
    table: str
    columns: Sequence[str]
    partitions: int

    def alter_sql(self) -> str:
        columns_sql = ", ".join(quote_identifier(column) for column in self.columns)
        return (
            "ALTER TABLE "
            + quote_identifier(self.table)
            + " PARTITION BY KEY("
            + columns_sql
            + ") PARTITIONS "
            + str(max(1, int(self.partitions or 1)))
        )


MYSQL_OPERATIONAL_INDEXES: Dict[str, Sequence[MySQLIndexDefinition]] = {
    "account_watchlist_symbols": (
        MySQLIndexDefinition(
            "account_watchlist_symbols",
            "idx_account_watchlist_symbols_reverse",
            "`symbol`, `account_id`",
        ),
    ),
    "portfolio_decision_action_observations": (
        MySQLIndexDefinition(
            "portfolio_decision_action_observations",
            "idx_decision_action_prior_subject",
            "`prior_decision_episode_id`, `account_id`, `symbol`, `observed_at`",
        ),
    ),
    "investment_decision_episodes": (
        MySQLIndexDefinition(
            "investment_decision_episodes",
            "idx_decision_episodes_account_decided",
            "`account_id`, `decided_at`, `episode_id`",
        ),
    ),
    "reasoning_engine_comparisons": (
        MySQLIndexDefinition(
            "reasoning_engine_comparisons",
            "idx_reasoning_comparison_release_time",
            "`candidate_deployment_id`, `candidate_release_fingerprint`, `created_at`",
        ),
    ),
    "reasoning_engine_shadow_jobs": (
        MySQLIndexDefinition(
            "reasoning_engine_shadow_jobs",
            "idx_reasoning_shadow_release_ready",
            "`candidate_deployment_id`, `candidate_release_id`, `candidate_runtime_revision`, `job_status`, `created_at`",
        ),
    ),
    "reasoning_engine_jobs": (
        MySQLIndexDefinition(
            "reasoning_engine_jobs",
            "idx_reasoning_engine_job_release",
            "`deployment_id`, `release_fingerprint`, `job_status`, `completed_at`",
        ),
        MySQLIndexDefinition(
            "reasoning_engine_jobs",
            "idx_reasoning_engine_job_source_snapshot",
            "`source_snapshot_id`, `job_status`, `created_at`",
        ),
        MySQLIndexDefinition(
            "reasoning_engine_jobs",
            "idx_reasoning_engine_job_local_lease",
            "`job_status`, `lease_owner`, `updated_at`, `job_id`",
        ),
        MySQLIndexDefinition(
            "reasoning_engine_jobs",
            "idx_reasoning_engine_job_expired_lease",
            "`job_status`, `lease_expires_at`, `job_id`",
        ),
    ),
    "temporal_feature_snapshots": (
        MySQLIndexDefinition(
            "temporal_feature_snapshots",
            "idx_temporal_feature_created",
            "`created_at`, `snapshot_id`",
        ),
    ),
    "time_series_projection_outbox": (
        MySQLIndexDefinition(
            "time_series_projection_outbox",
            "idx_time_series_projection_completed",
            "`job_status`, `updated_at`, `job_id`",
        ),
    ),
    "service_accounts": (
        MySQLIndexDefinition("service_accounts", "idx_service_accounts_enabled_created", "`enabled`, `created_at`, `id`"),
    ),
    "domain_events": (
        MySQLIndexDefinition("domain_events", "idx_domain_events_time", "`occurred_at`, `event_id`"),
        MySQLIndexDefinition(
            "domain_events",
            "idx_domain_events_name_time_event",
            "`name`, `occurred_at`, `event_id`",
        ),
        MySQLIndexDefinition(
            "domain_events",
            "idx_domain_events_name_aggregate_time",
            "`name`, `aggregate_id`, `occurred_at`, `event_id`",
        ),
    ),
    "ontology_reasoning_mailbox_events": (
        MySQLIndexDefinition(
            "ontology_reasoning_mailbox_events",
            "idx_reasoning_mailbox_events_state_occurred",
            "`state`, `occurred_at`, `event_id`",
        ),
        MySQLIndexDefinition(
            "ontology_reasoning_mailbox_events",
            "idx_reasoning_mailbox_events_source_snapshot",
            "`source_snapshot_id`, `state`, `event_id`",
        ),
    ),
    "ontology_reasoning_mailbox": (
        MySQLIndexDefinition(
            "ontology_reasoning_mailbox",
            "idx_reasoning_mailbox_lane_pending",
            "`reasoning_lane`, `priority_hint`, `occurred_at`, `mailbox_key`",
        ),
    ),
    "monitor_snapshot_history": (
        MySQLIndexDefinition(
            "monitor_snapshot_history",
            "idx_monitor_snapshot_history_generated",
            "`generated_at`, `account_id`",
        ),
        MySQLIndexDefinition(
            "monitor_snapshot_history",
            "idx_monitor_snapshot_history_account_generated",
            "`account_id`, `generated_at`",
        ),
    ),
    "monitor_sent": (
        MySQLIndexDefinition("monitor_sent", "idx_monitor_sent_sent_at", "`sent_at`, `sent_key_hash`"),
    ),
    "notification_jobs": (
        MySQLIndexDefinition("notification_jobs", "idx_notification_jobs_created", "`created_at`, `job_id`"),
        MySQLIndexDefinition(
            "notification_jobs",
            "idx_notification_jobs_symbol_account_updated",
            "`symbol`, `account_id`, `updated_at`, `job_id`",
        ),
        MySQLIndexDefinition(
            "notification_jobs",
            "idx_notification_jobs_type_status_created",
            "`message_type`, `status`, `created_at`, `job_id`",
        ),
        MySQLIndexDefinition(
            "notification_jobs",
            "idx_notification_jobs_status_attempts_created",
            "`status`, `attempts`, `created_at`, `job_id`",
        ),
        MySQLIndexDefinition(
            "notification_jobs",
            "idx_notification_jobs_status_processing_age",
            "`status`, `processing_started_at`, `updated_at`, `created_at`, `job_id`",
        ),
        MySQLIndexDefinition("notification_jobs", "idx_notification_jobs_source_event", "`source_event_id`, `job_id`"),
        MySQLIndexDefinition(
            "notification_jobs",
            "idx_notification_jobs_account_status_updated",
            "`account_id`, `status`, `updated_at`, `job_id`",
        ),
        MySQLIndexDefinition(
            "notification_jobs",
            "idx_notification_jobs_decision_link",
            "`account_id`, `symbol`, `decision_episode_id`, `updated_at`, `job_id`",
        ),
    ),
    "notification_inbox_receipts": (
        MySQLIndexDefinition(
            "notification_inbox_receipts",
            "idx_notification_inbox_recipient_important",
            "`recipient_id`, `important`, `updated_at`, `job_id`",
        ),
    ),
    "ai_inference_subject_heads": (
        MySQLIndexDefinition(
            "ai_inference_subject_heads",
            "idx_ai_inference_subject_heads_updated",
            "`updated_at`, `subject_key`",
        ),
    ),
    "ai_inference_requests": (
        MySQLIndexDefinition(
            "ai_inference_requests",
            "idx_ai_inference_requests_ready",
            "`status`, `available_at`, `priority`, `created_at`, `request_id`",
        ),
        MySQLIndexDefinition(
            "ai_inference_requests",
            "idx_ai_inference_requests_subject",
            "`subject_key`, `status`, `updated_at`, `request_id`",
        ),
        MySQLIndexDefinition(
            "ai_inference_requests",
            "idx_ai_inference_requests_lease",
            "`status`, `lease_expires_at`, `request_id`",
        ),
        MySQLIndexDefinition(
            "ai_inference_requests",
            "idx_ai_inference_requests_completed",
            "`status`, `completed_at`, `request_id`",
        ),
    ),
    "ai_inference_results": (
        MySQLIndexDefinition(
            "ai_inference_results",
            "idx_ai_inference_results_notification",
            "`notification_job_id`, `created_at`",
        ),
        MySQLIndexDefinition(
            "ai_inference_results",
            "idx_ai_inference_results_created",
            "`created_at`, `result_id`",
        ),
    ),
    "mysql_retention_runs": (
        MySQLIndexDefinition(
            "mysql_retention_runs",
            "idx_mysql_retention_runs_created",
            "`created_at`, `run_id`",
        ),
    ),
    "model_review_jobs": (
        MySQLIndexDefinition("model_review_jobs", "idx_model_review_jobs_created", "`created_at`, `job_id`"),
        MySQLIndexDefinition(
            "model_review_jobs",
            "idx_model_review_jobs_status_attempts_created",
            "`status`, `attempts`, `created_at`, `job_id`",
        ),
    ),
    "market_time_series_observations": (
        MySQLIndexDefinition(
            "market_time_series_observations",
            "idx_market_time_series_snapshot_cutoff",
            "`account_id`, `symbol`, `granularity`, `observed_at`, `bucket_at`",
        ),
        MySQLIndexDefinition(
            "market_time_series_observations",
            "idx_market_time_series_projection_source",
            "`account_id`, `symbol`, `observed_at`, `granularity`, `bucket_at`",
        ),
    ),
    "symbol_universe": (
        MySQLIndexDefinition("symbol_universe", "idx_symbol_universe_active_market_seen", "`active`, `market`, `last_seen_at`"),
        MySQLIndexDefinition("symbol_universe", "idx_symbol_universe_active_symbol_market", "`active`, `symbol`, `market`"),
        MySQLIndexDefinition("symbol_universe", "idx_symbol_universe_active_name_market", "`active`, `name`, `market`"),
    ),
    "research_evidence": (
        MySQLIndexDefinition(
            "research_evidence",
            "idx_research_evidence_latest",
            "`last_seen_at`, `published_at`, `evidence_id`",
        ),
        MySQLIndexDefinition(
            "research_evidence",
            "idx_research_evidence_symbol_kind_latest",
            "`symbol`, `kind`, `last_seen_at`, `published_at`, `evidence_id`",
        ),
        MySQLIndexDefinition("research_evidence", "idx_research_evidence_source_latest", "`source`, `last_seen_at`"),
        MySQLIndexDefinition("research_evidence", "idx_research_evidence_polarity_latest", "`polarity`, `last_seen_at`"),
        MySQLIndexDefinition(
            "research_evidence",
            "idx_research_evidence_lifecycle_kind_time",
            "`lifecycle_state`, `kind`, `published_at`, `evidence_id`",
        ),
        MySQLIndexDefinition(
            "research_evidence",
            "idx_research_evidence_lifecycle_kind_seen",
            "`lifecycle_state`, `kind`, `last_seen_at`, `evidence_id`",
        ),
        MySQLIndexDefinition(
            "research_evidence",
            "idx_research_evidence_lifecycle_kind_latest",
            "`lifecycle_state`, `kind`, `last_seen_at`, `published_at`, `evidence_id`",
        ),
        MySQLIndexDefinition(
            "research_evidence",
            "idx_research_evidence_lifecycle_latest",
            "`lifecycle_state`, `last_seen_at`, `published_at`, `evidence_id`",
        ),
    ),
    "news_article_enrichment_revisions": (
        MySQLIndexDefinition(
            "news_article_enrichment_revisions",
            "idx_news_enrichment_subject",
            "`evidence_id`, `source_revision`, `analyzer_release`, `updated_at`",
        ),
    ),
    "ontology_ai_opinion_samples": (
        MySQLIndexDefinition("ontology_ai_opinion_samples", "idx_ontology_quality_created", "`created_at`, `sample_id`"),
    ),
    "ontology_projection_runs": (
        MySQLIndexDefinition(
            "ontology_projection_runs",
            "idx_ontology_projection_runs_account_updated",
            "`account_id`, `updated_at`, `run_id`",
        ),
        MySQLIndexDefinition(
            "ontology_projection_runs",
            "idx_ontology_projection_runs_world_updated",
            "`world_id`, `updated_at`, `run_id`",
        ),
        MySQLIndexDefinition(
            "ontology_projection_runs",
            "idx_ontology_projection_runs_namespace",
            "`execution_namespace_id`, `world_id`, `updated_at`, `run_id`",
        ),
        MySQLIndexDefinition(
            "ontology_projection_runs",
            "idx_ontology_projection_runs_status_updated",
            "`status`, `updated_at`, `run_id`",
        ),
        MySQLIndexDefinition("ontology_projection_runs", "idx_ontology_projection_runs_abox", "`abox_snapshot_id`"),
        MySQLIndexDefinition(
            "ontology_projection_runs",
            "idx_ontology_projection_runs_material",
            "`account_id`, `material_fingerprint`",
        ),
    ),
    "ontology_reasoning_run_stages": (
        MySQLIndexDefinition(
            "ontology_reasoning_run_stages",
            "idx_reasoning_run_stages_generation",
            "`inference_generation_id`, `account_id`, `updated_at`",
        ),
        MySQLIndexDefinition(
            "ontology_reasoning_run_stages",
            "idx_reasoning_run_stages_updated",
            "`updated_at`, `run_id`, `stage_key`",
        ),
    ),
    "ontology_reasoning_rule_runs": (
        MySQLIndexDefinition(
            "ontology_reasoning_rule_runs",
            "idx_reasoning_rule_runs_generation",
            "`inference_generation_id`, `account_id`, `updated_at`",
        ),
        MySQLIndexDefinition(
            "ontology_reasoning_rule_runs",
            "idx_reasoning_rule_runs_updated",
            "`updated_at`, `run_id`, `rule_run_key`",
        ),
    ),
    "ontology_reasoning_rule_result_slots": (
        MySQLIndexDefinition(
            "ontology_reasoning_rule_result_slots",
            "idx_reasoning_rule_slots_catalog",
            "`execution_namespace_id`, `world_id`, `account_id`, `rulebox_rules_hash`, `tbox_fingerprint`, `symbol`",
        ),
        MySQLIndexDefinition(
            "ontology_reasoning_rule_result_slots",
            "idx_reasoning_rule_slots_generation",
            "`inference_generation_id`, `account_id`, `updated_at`",
        ),
        MySQLIndexDefinition(
            "ontology_reasoning_rule_result_slots",
            "idx_reasoning_rule_slots_rule",
            "`rule_id`, `matched`, `updated_at`",
        ),
    ),
    "ontology_world_projection_outbox": (
        MySQLIndexDefinition(
            "ontology_world_projection_outbox",
            "idx_world_projection_outbox_completed",
            "`status`, `completed_at`, `job_id`",
        ),
        MySQLIndexDefinition(
            "ontology_world_projection_outbox",
            "idx_world_projection_outbox_world_kind_updated",
            "`world_id`, `projection_kind`, `updated_at`, `job_id`",
        ),
        MySQLIndexDefinition(
            "ontology_world_projection_outbox",
            "idx_world_projection_outbox_status_world_kind_updated",
            "`status`, `world_id`, `projection_kind`, `updated_at`, `job_id`",
        ),
        MySQLIndexDefinition(
            "ontology_world_projection_outbox",
            "idx_world_projection_outbox_status_world_kind_completed",
            "`status`, `world_id`, `projection_kind`, `completed_at`, `job_id`",
        ),
    ),
    "ontology_inference_detail_outbox": (
        MySQLIndexDefinition(
            "ontology_inference_detail_outbox",
            "idx_inference_detail_outbox_completed",
            "`status`, `completed_at`, `job_id`",
        ),
    ),
    "investment_strategy_proposals": (
        MySQLIndexDefinition(
            "investment_strategy_proposals",
            "idx_investment_strategy_proposals_status",
            "`status`, `updated_at`, `proposal_id`",
        ),
        MySQLIndexDefinition(
            "investment_strategy_proposals",
            "idx_investment_strategy_proposals_experiment",
            "`source_experiment_id`",
        ),
        MySQLIndexDefinition(
            "investment_strategy_proposals",
            "idx_investment_strategy_proposals_trigger",
            "`source_trigger`, `updated_at`",
        ),
        MySQLIndexDefinition(
            "investment_strategy_proposals",
            "idx_investment_strategy_proposals_updated",
            "`updated_at`, `proposal_id`",
        ),
    ),
    "investment_hypothesis_lifecycle_states": (
        MySQLIndexDefinition(
            "investment_hypothesis_lifecycle_states",
            "idx_hypothesis_lifecycle_account_symbol",
            "`account_id`, `symbol`, `updated_at`",
        ),
        MySQLIndexDefinition(
            "investment_hypothesis_lifecycle_states",
            "idx_hypothesis_lifecycle_market_symbol",
            "`market_id`, `symbol`, `updated_at`",
        ),
        MySQLIndexDefinition(
            "investment_hypothesis_lifecycle_states",
            "idx_hypothesis_lifecycle_state_updated",
            "`state`, `updated_at`",
        ),
        MySQLIndexDefinition(
            "investment_hypothesis_lifecycle_states",
            "idx_hypothesis_lifecycle_generation",
            "`inference_generation_id`",
        ),
        MySQLIndexDefinition(
            "investment_hypothesis_lifecycle_states",
            "idx_hypothesis_lifecycle_live_subject",
            "`symbol`, `scope`, `account_id`, `lifecycle_key`",
        ),
    ),
    "investment_hypothesis_lifecycle_events": (
        MySQLIndexDefinition(
            "investment_hypothesis_lifecycle_events",
            "idx_hypothesis_lifecycle_events_occurred",
            "`occurred_at`, `transition_id`",
        ),
        MySQLIndexDefinition(
            "investment_hypothesis_lifecycle_events",
            "idx_hypothesis_lifecycle_events_key_time",
            "`lifecycle_key`, `occurred_at`",
        ),
        MySQLIndexDefinition(
            "investment_hypothesis_lifecycle_events",
            "idx_hypothesis_lifecycle_events_account_symbol",
            "`account_id`, `symbol`, `occurred_at`",
        ),
        MySQLIndexDefinition(
            "investment_hypothesis_lifecycle_events",
            "idx_hypothesis_lifecycle_events_state_time",
            "`current_state`, `occurred_at`",
        ),
    ),
}

MYSQL_OPERATIONAL_UNIQUE_INDEX_RETIREMENTS: Sequence[MySQLUniqueIndexRetirementDefinition] = (
    MySQLUniqueIndexRetirementDefinition(
        "news_article_enrichment_revisions",
        "idx_news_enrichment_subject",
    ),
)


MYSQL_OPERATIONAL_COLUMNS: Dict[str, Sequence[MySQLColumnDefinition]] = {
    "ai_inference_results": (
        MySQLColumnDefinition("ai_inference_results", "publication_mode", "VARCHAR(32) NOT NULL DEFAULT 'unknown'"),
        MySQLColumnDefinition("ai_inference_results", "ai_authored", "TINYINT NOT NULL DEFAULT 0"),
        MySQLColumnDefinition("ai_inference_results", "publication_contract_passed", "TINYINT NOT NULL DEFAULT 0"),
        MySQLColumnDefinition("ai_inference_results", "contract_failure_code", "VARCHAR(96) NOT NULL DEFAULT ''"),
    ),
    "ontology_reasoning_mailbox_events": (
        MySQLColumnDefinition(
            "ontology_reasoning_mailbox_events",
            "source_snapshot_id",
            "VARCHAR(191) NOT NULL DEFAULT ''",
        ),
    ),
    "reasoning_engine_jobs": (
        MySQLColumnDefinition("reasoning_engine_jobs", "heartbeat_at", "VARCHAR(40) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_jobs", "current_stage", "VARCHAR(96) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_jobs", "stage_started_at", "VARCHAR(40) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_jobs", "stage_updated_at", "VARCHAR(40) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_jobs", "stage_details_json", "LONGTEXT NULL"),
        MySQLColumnDefinition("reasoning_engine_jobs", "source_snapshot_id", "VARCHAR(191) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_jobs", "source_snapshot_at", "VARCHAR(40) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_jobs", "source_boundary_json", "LONGTEXT NULL"),
        MySQLColumnDefinition("reasoning_engine_jobs", "source_payload_hash", "CHAR(64) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_jobs", "release_fingerprint", "VARCHAR(64) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_jobs", "validation_cohort_id", "VARCHAR(96) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_jobs", "runtime_revision", "VARCHAR(64) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_jobs", "reasoning_lane", "VARCHAR(32) NOT NULL DEFAULT 'CONTEXT'"),
        MySQLColumnDefinition("reasoning_engine_jobs", "superseded_by_job_id", "VARCHAR(191) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_jobs", "terminal_reason_code", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ),
    "reasoning_engine_comparisons": (
        MySQLColumnDefinition("reasoning_engine_comparisons", "baseline_release_id", "VARCHAR(191) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_comparisons", "candidate_release_id", "VARCHAR(191) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_comparisons", "candidate_release_fingerprint", "VARCHAR(64) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_comparisons", "validation_cohort_id", "VARCHAR(96) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_comparisons", "candidate_runtime_revision", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ),
    "reasoning_engine_shadow_jobs": (
        MySQLColumnDefinition("reasoning_engine_shadow_jobs", "candidate_release_id", "VARCHAR(191) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("reasoning_engine_shadow_jobs", "candidate_runtime_revision", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ),
    "market_time_series_observations": (
        MySQLColumnDefinition(
            "market_time_series_observations",
            "investor_coverage_json",
            "LONGTEXT NULL",
        ),
    ),
    "notification_jobs": (
        MySQLColumnDefinition("notification_jobs", "symbol", "VARCHAR(64) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("notification_jobs", "decision_episode_id", "VARCHAR(191) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("notification_jobs", "decision_key", "VARCHAR(191) NOT NULL DEFAULT ''"),
        MySQLColumnDefinition("notification_jobs", "api_source", "VARCHAR(191) NOT NULL DEFAULT 'notification_jobs'"),
        MySQLColumnDefinition("notification_jobs", "data_quality", "VARCHAR(32) NOT NULL DEFAULT 'actual'"),
        MySQLColumnDefinition("notification_jobs", "is_mock", "TINYINT NOT NULL DEFAULT 0"),
    ),
    "notification_rules": (
        MySQLColumnDefinition(
            "notification_rules",
            "off_hours_delivery_mode",
            "VARCHAR(32) NOT NULL DEFAULT 'important_only'",
        ),
    ),
    "monitor_snapshot_history": (
        MySQLColumnDefinition(
            "monitor_snapshot_history",
            "projection_payload_json",
            "LONGTEXT NULL",
        ),
    ),
    "service_accounts": (
        MySQLColumnDefinition(
            "service_accounts",
            "notification_detail_level",
            "VARCHAR(32) NOT NULL DEFAULT 'concise'",
        ),
        MySQLColumnDefinition(
            "service_accounts",
            "investment_strategy_profile",
            "VARCHAR(64) NOT NULL DEFAULT 'balanced'",
        ),
    ),
    "investment_calendar_candidates": (
        MySQLColumnDefinition(
            "investment_calendar_candidates",
            "readiness_state",
            "VARCHAR(32) NOT NULL DEFAULT 'needs-review'",
        ),
    ),
    "ontology_projection_runs": (
        MySQLColumnDefinition(
            "ontology_projection_runs",
            "execution_namespace_id",
            "VARCHAR(64) NOT NULL DEFAULT ''",
        ),
        MySQLColumnDefinition(
            "ontology_projection_runs",
            "engine_deployment_id",
            "VARCHAR(96) NOT NULL DEFAULT ''",
        ),
        MySQLColumnDefinition(
            "ontology_projection_runs",
            "graph_database",
            "VARCHAR(96) NOT NULL DEFAULT ''",
        ),
        MySQLColumnDefinition(
            "ontology_projection_runs",
            "release_fingerprint",
            "VARCHAR(64) NOT NULL DEFAULT ''",
        ),
        MySQLColumnDefinition(
            "ontology_projection_runs",
            "validation_cohort_id",
            "VARCHAR(96) NOT NULL DEFAULT ''",
        ),
        MySQLColumnDefinition(
            "ontology_projection_runs",
            "tenant_id",
            "VARCHAR(191) NOT NULL DEFAULT ''",
        ),
        MySQLColumnDefinition(
            "ontology_projection_runs",
            "world_id",
            "VARCHAR(191) NOT NULL DEFAULT ''",
        ),
        MySQLColumnDefinition(
            "ontology_projection_runs",
            "world_type",
            "VARCHAR(64) NOT NULL DEFAULT ''",
        ),
        MySQLColumnDefinition(
            "ontology_projection_runs",
            "market_world_id",
            "VARCHAR(191) NOT NULL DEFAULT ''",
        ),
    ),
    "ontology_reasoning_rule_result_slots": (
        MySQLColumnDefinition(
            "ontology_reasoning_rule_result_slots",
            "execution_namespace_id",
            "VARCHAR(64) NOT NULL DEFAULT ''",
        ),
        MySQLColumnDefinition(
            "ontology_reasoning_rule_result_slots",
            "engine_deployment_id",
            "VARCHAR(96) NOT NULL DEFAULT ''",
        ),
        MySQLColumnDefinition(
            "ontology_reasoning_rule_result_slots",
            "graph_database",
            "VARCHAR(96) NOT NULL DEFAULT ''",
        ),
        MySQLColumnDefinition(
            "ontology_reasoning_rule_result_slots",
            "release_fingerprint",
            "VARCHAR(64) NOT NULL DEFAULT ''",
        ),
        MySQLColumnDefinition(
            "ontology_reasoning_rule_result_slots",
            "validation_cohort_id",
            "VARCHAR(96) NOT NULL DEFAULT ''",
        ),
    ),
    "ontology_reasoning_run_stages": (
        MySQLColumnDefinition(
            "ontology_reasoning_run_stages",
            "inference_generation_id",
            "VARCHAR(191) NOT NULL DEFAULT ''",
        ),
    ),
    "ontology_reasoning_rule_runs": (
        MySQLColumnDefinition(
            "ontology_reasoning_rule_runs",
            "inference_generation_id",
            "VARCHAR(191) NOT NULL DEFAULT ''",
        ),
    ),
    "ontology_reasoning_mailbox": (
        MySQLColumnDefinition(
            "ontology_reasoning_mailbox",
            "work_class",
            "VARCHAR(32) NOT NULL DEFAULT 'MARKET'",
        ),
        MySQLColumnDefinition(
            "ontology_reasoning_mailbox",
            "impact_scope",
            "VARCHAR(32) NOT NULL DEFAULT 'SUBJECT'",
        ),
        MySQLColumnDefinition(
            "ontology_reasoning_mailbox",
            "reasoning_lane",
            "VARCHAR(40) NOT NULL DEFAULT 'REALTIME_REASONING'",
        ),
        MySQLColumnDefinition(
            "ontology_reasoning_mailbox",
            "market_scope",
            "VARCHAR(96) NOT NULL DEFAULT 'market'",
        ),
        MySQLColumnDefinition(
            "ontology_reasoning_mailbox",
            "rule_families_json",
            "TEXT NOT NULL",
        ),
        MySQLColumnDefinition(
            "ontology_reasoning_mailbox",
            "revision_vector_json",
            "TEXT NOT NULL",
        ),
    ),
    "ontology_reasoning_work_items": (
        MySQLColumnDefinition(
            "ontology_reasoning_work_items",
            "stage_started_at",
            "VARCHAR(40) NOT NULL DEFAULT ''",
        ),
    ),
    "investment_decision_episodes": (
        MySQLColumnDefinition(
            "investment_decision_episodes",
            "review_level",
            "VARCHAR(32) NOT NULL DEFAULT 'check'",
        ),
        MySQLColumnDefinition(
            "investment_decision_episodes",
            "data_state",
            "VARCHAR(32) NOT NULL DEFAULT 'partial'",
        ),
        MySQLColumnDefinition(
            "investment_decision_episodes",
            "validation_state",
            "VARCHAR(32) NOT NULL DEFAULT 'conditional'",
        ),
    ),
    "research_evidence": (
        MySQLColumnDefinition(
            "research_evidence",
            "source_trust_state",
            "VARCHAR(32) NOT NULL DEFAULT 'unknown'",
        ),
        MySQLColumnDefinition(
            "research_evidence",
            "materiality_state",
            "VARCHAR(32) NOT NULL DEFAULT 'context'",
        ),
        MySQLColumnDefinition(
            "research_evidence",
            "data_state",
            "VARCHAR(32) NOT NULL DEFAULT 'partial'",
        ),
        MySQLColumnDefinition(
            "research_evidence",
            "validation_state",
            "VARCHAR(32) NOT NULL DEFAULT 'conditional'",
        ),
        MySQLColumnDefinition(
            "research_evidence",
            "lifecycle_state",
            "VARCHAR(32) NOT NULL DEFAULT 'active'",
        ),
        MySQLColumnDefinition(
            "research_evidence",
            "lifecycle_changed_at",
            "VARCHAR(40) NOT NULL DEFAULT ''",
        ),
    ),
    "ontology_ai_opinion_samples": (
        MySQLColumnDefinition(
            "ontology_ai_opinion_samples",
            "overall_state",
            "VARCHAR(32) NOT NULL DEFAULT 'blocked'",
        ),
        MySQLColumnDefinition(
            "ontology_ai_opinion_samples",
            "data_state",
            "VARCHAR(32) NOT NULL DEFAULT 'unavailable'",
        ),
        MySQLColumnDefinition(
            "ontology_ai_opinion_samples",
            "context_state",
            "VARCHAR(32) NOT NULL DEFAULT 'insufficient'",
        ),
        MySQLColumnDefinition(
            "ontology_ai_opinion_samples",
            "reasoning_state",
            "VARCHAR(32) NOT NULL DEFAULT 'blocked'",
        ),
        MySQLColumnDefinition(
            "ontology_ai_opinion_samples",
            "relation_state",
            "VARCHAR(32) NOT NULL DEFAULT 'empty'",
        ),
        MySQLColumnDefinition(
            "ontology_ai_opinion_samples",
            "validation_state",
            "VARCHAR(32) NOT NULL DEFAULT 'blocked'",
        ),
        MySQLColumnDefinition(
            "ontology_ai_opinion_samples",
            "action_required_count",
            "INT NOT NULL DEFAULT 0",
        ),
    ),
}


MYSQL_OPERATIONAL_COLUMN_COMPATIBILITY: Dict[str, Sequence[MySQLColumnCompatibilityDefinition]] = {
    "market_time_series_observations": (
        MySQLColumnCompatibilityDefinition(
            "market_time_series_observations",
            "investor_coverage_json",
            "LONGTEXT NULL",
        ),
    ),
}


# The delivery system now stores only categorical conditions and state-based
# cooldowns. These former aggregate score columns have no remaining readers.
MYSQL_OPERATIONAL_RETIRED_COLUMNS: Dict[str, Sequence[MySQLColumnRetirementDefinition]] = {
    "notification_rules": (
        MySQLColumnRetirementDefinition("notification_rules", "threshold"),
        MySQLColumnRetirementDefinition("notification_rules", "base_score"),
        MySQLColumnRetirementDefinition("notification_rules", "low_score_action"),
        MySQLColumnRetirementDefinition("notification_rules", "similarity_penalty"),
        MySQLColumnRetirementDefinition("notification_rules", "similarity_bypass_score_delta"),
    ),
}


MYSQL_MONITORING_INDEXES: Dict[str, Sequence[MySQLIndexDefinition]] = {
    "monitor_account_jobs": (
        MySQLIndexDefinition(
            "monitor_account_jobs",
            "idx_monitor_account_jobs_status_priority_due",
            "`status`, `priority`, `next_run_at`, `account_id`",
        ),
        MySQLIndexDefinition("monitor_account_jobs", "idx_monitor_account_jobs_updated", "`status`, `updated_at`, `account_id`"),
    ),
}


MYSQL_OPERATIONAL_KEY_PARTITIONS: Dict[str, MySQLKeyPartitionDefinition] = {
    "domain_events": MySQLKeyPartitionDefinition("domain_events", ("event_id",), 16),
    "monitor_snapshot_history": MySQLKeyPartitionDefinition("monitor_snapshot_history", ("account_id", "generated_at"), 8),
    "model_review_jobs": MySQLKeyPartitionDefinition("model_review_jobs", ("job_id",), 8),
    "market_quote_cache": MySQLKeyPartitionDefinition("market_quote_cache", ("provider", "account_id", "symbol"), 8),
    "symbol_universe": MySQLKeyPartitionDefinition("symbol_universe", ("market", "symbol"), 8),
    "research_evidence": MySQLKeyPartitionDefinition("research_evidence", ("evidence_id",), 8),
    "ontology_ai_opinion_samples": MySQLKeyPartitionDefinition("ontology_ai_opinion_samples", ("sample_id",), 8),
    "investment_strategy_proposals": MySQLKeyPartitionDefinition("investment_strategy_proposals", ("proposal_id",), 8),
}


def quote_identifier(value: str) -> str:
    name = str(value or "").strip()
    if not IDENTIFIER_PATTERN.match(name):
        raise ValueError("Unsafe MySQL identifier: " + name)
    return "`" + name + "`"


def _execute(connection, sql: str, params=()):
    if hasattr(connection, "execute"):
        return connection.execute(sql, params)
    cursor = connection.cursor()
    cursor.execute(sql, params or ())
    return cursor


def _is_duplicate_index_error(error: Exception) -> bool:
    args = getattr(error, "args", ())
    code = args[0] if args else None
    return code == 1061 or "Duplicate key name" in str(error)


def mysql_index_exists(connection, table: str, index_name: str) -> bool:
    cursor = _execute(connection, "SHOW INDEX FROM " + quote_identifier(table) + " WHERE Key_name = %s", (index_name,))
    return bool(cursor.fetchone())


def mysql_index_is_unique(connection, table: str, index_name: str) -> bool:
    cursor = _execute(connection, "SHOW INDEX FROM " + quote_identifier(table) + " WHERE Key_name = %s", (index_name,))
    row = cursor.fetchone()
    if not row:
        return False
    if isinstance(row, Mapping):
        return int(row.get("Non_unique") if row.get("Non_unique") is not None else row.get("NON_UNIQUE") or 0) == 0
    return len(row) > 1 and int(row[1] or 0) == 0


def mysql_column_exists(connection, table: str, column_name: str) -> bool:
    cursor = _execute(connection, "SHOW COLUMNS FROM " + quote_identifier(table) + " LIKE %s", (column_name,))
    return bool(cursor.fetchone())


def mysql_column_is_nullable(connection, table: str, column_name: str) -> bool:
    cursor = _execute(connection, "SHOW COLUMNS FROM " + quote_identifier(table) + " LIKE %s", (column_name,))
    row = cursor.fetchone()
    if not row:
        return False
    if isinstance(row, Mapping):
        return str(row.get("Null") or row.get("NULL") or "").upper() == "YES"
    return len(row) > 2 and str(row[2] or "").upper() == "YES"


def ensure_mysql_columns(
    connection,
    column_map: Mapping[str, Sequence[MySQLColumnDefinition]],
) -> List[str]:
    created: List[str] = []
    for table, definitions in column_map.items():
        for definition in definitions:
            if mysql_column_exists(connection, table, definition.name):
                continue
            try:
                _execute(connection, definition.alter_sql())
            except Exception as error:
                args = getattr(error, "args", ())
                code = args[0] if args else None
                if code == 1060 or "Duplicate column name" in str(error):
                    continue
                raise
            created.append(definition.table + "." + definition.name)
    return created


def ensure_mysql_column_compatibility(
    connection,
    column_map: Mapping[str, Sequence[MySQLColumnCompatibilityDefinition]],
) -> List[str]:
    modified: List[str] = []
    for table, definitions in column_map.items():
        for definition in definitions:
            if not mysql_column_exists(connection, table, definition.name):
                continue
            if mysql_column_is_nullable(connection, table, definition.name):
                continue
            _execute(connection, definition.alter_sql())
            modified.append(definition.table + "." + definition.name)
    return modified


def retire_mysql_columns(
    connection,
    column_map: Mapping[str, Sequence[MySQLColumnRetirementDefinition]],
) -> List[str]:
    retired: List[str] = []
    for table, definitions in column_map.items():
        for definition in definitions:
            if not mysql_column_exists(connection, table, definition.name):
                continue
            _execute(connection, definition.alter_sql())
            retired.append(table + "." + definition.name)
    return retired


def ensure_mysql_indexes(
    connection,
    index_map: Mapping[str, Sequence[MySQLIndexDefinition]],
) -> List[str]:
    created: List[str] = []
    for table, definitions in index_map.items():
        for definition in definitions:
            if mysql_index_exists(connection, table, definition.name):
                continue
            try:
                _execute(connection, definition.alter_sql())
            except Exception as error:
                if _is_duplicate_index_error(error):
                    continue
                raise
            created.append(definition.name)
    return created


def retire_mysql_unique_indexes(
    connection,
    definitions: Sequence[MySQLUniqueIndexRetirementDefinition],
) -> List[str]:
    retired: List[str] = []
    for definition in definitions or ():
        if not mysql_index_is_unique(connection, definition.table, definition.name):
            continue
        _execute(connection, definition.alter_sql())
        retired.append(definition.table + "." + definition.name)
    return retired


def mysql_primary_key_columns(connection, table: str) -> List[str]:
    cursor = _execute(
        connection,
        """
        SELECT COLUMN_NAME
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME = 'PRIMARY'
        ORDER BY SEQ_IN_INDEX
        """,
        (table,),
    )
    columns: List[str] = []
    for row in cursor.fetchall() or []:
        if isinstance(row, Mapping):
            value = row.get("COLUMN_NAME") or row.get("column_name")
        else:
            value = row[0] if row else ""
        clean = str(value or "").strip()
        if clean:
            columns.append(clean)
    return columns


def ensure_reasoning_rule_slot_namespace_primary_key(connection) -> List[str]:
    """Separate V1/V2/V3 result slots before any proof can be reused."""
    table = "ontology_reasoning_rule_result_slots"
    expected = ["execution_namespace_id", "world_id", "symbol", "rule_id"]
    current = mysql_primary_key_columns(connection, table)
    if current == expected:
        return []
    if not current:
        _execute(
            connection,
            "ALTER TABLE `" + table + "` ADD PRIMARY KEY "
            "(`execution_namespace_id`, `world_id`, `symbol`, `rule_id`)",
        )
    else:
        _execute(
            connection,
            "ALTER TABLE `" + table + "` DROP PRIMARY KEY, ADD PRIMARY KEY "
            "(`execution_namespace_id`, `world_id`, `symbol`, `rule_id`)",
        )
    return [table + ".PRIMARY"]


def mysql_partitioning_mode(settings: Mapping[str, object] = None) -> str:
    configured = settings or {}
    raw = str(
        configured.get("mysqlTablePartitioning")
        or configured.get("mysqlEnableTablePartitioning")
        or os.environ.get("MYSQL_TABLE_PARTITIONING")
        or os.environ.get("MYSQL_ENABLE_TABLE_PARTITIONING")
        or "auto"
    ).strip().lower()
    if raw in {"0", "false", "no", "off", "disabled", "disable", "none"}:
        return "off"
    if raw in {"force", "always", "all", "rebuild"}:
        return "force"
    return "auto"


def mysql_table_is_partitioned(connection, table: str) -> bool:
    cursor = _execute(
        connection,
        """
        SELECT PARTITION_NAME
        FROM information_schema.PARTITIONS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND PARTITION_NAME IS NOT NULL
        LIMIT 1
        """,
        (table,),
    )
    return bool(cursor.fetchone())


def mysql_table_is_empty(connection, table: str) -> bool:
    cursor = _execute(connection, "SELECT 1 FROM " + quote_identifier(table) + " LIMIT 1")
    return not bool(cursor.fetchone())


def ensure_mysql_key_partitions(
    connection,
    partition_map: Mapping[str, MySQLKeyPartitionDefinition],
    settings: Mapping[str, object] = None,
) -> List[str]:
    mode = mysql_partitioning_mode(settings)
    if mode == "off":
        return []
    partitioned: List[str] = []
    for table, definition in partition_map.items():
        if mysql_table_is_partitioned(connection, table):
            continue
        if mode == "auto" and not mysql_table_is_empty(connection, table):
            continue
        try:
            _execute(connection, definition.alter_sql())
        except Exception as error:
            if mode == "force":
                raise
            warnings.warn(
                "MySQL table partitioning skipped for " + table + ": " + str(error),
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        partitioned.append(table)
    return partitioned


def ensure_mysql_operational_schema_tuning(connection, settings: Mapping[str, object] = None) -> Dict[str, List[str]]:
    columns = ensure_mysql_columns(connection, MYSQL_OPERATIONAL_COLUMNS)
    primary_keys = ensure_reasoning_rule_slot_namespace_primary_key(connection)
    retired_unique_indexes = retire_mysql_unique_indexes(connection, MYSQL_OPERATIONAL_UNIQUE_INDEX_RETIREMENTS)
    return {
        "columns": columns,
        "primaryKeys": primary_keys,
        "compatibleColumns": ensure_mysql_column_compatibility(connection, MYSQL_OPERATIONAL_COLUMN_COMPATIBILITY),
        "retiredColumns": retire_mysql_columns(connection, MYSQL_OPERATIONAL_RETIRED_COLUMNS),
        "retiredUniqueIndexes": retired_unique_indexes,
        "indexes": ensure_mysql_indexes(connection, MYSQL_OPERATIONAL_INDEXES),
        "partitions": ensure_mysql_key_partitions(connection, MYSQL_OPERATIONAL_KEY_PARTITIONS, settings),
    }


def ensure_mysql_monitoring_schema_tuning(connection) -> Dict[str, List[str]]:
    return {
        "indexes": ensure_mysql_indexes(connection, MYSQL_MONITORING_INDEXES),
        "partitions": [],
    }
