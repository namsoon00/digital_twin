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
    "service_accounts": (
        MySQLIndexDefinition("service_accounts", "idx_service_accounts_enabled_created", "`enabled`, `created_at`, `id`"),
    ),
    "domain_events": (
        MySQLIndexDefinition("domain_events", "idx_domain_events_time", "`occurred_at`, `event_id`"),
        MySQLIndexDefinition(
            "domain_events",
            "idx_domain_events_name_aggregate_time",
            "`name`, `aggregate_id`, `occurred_at`, `event_id`",
        ),
    ),
    "monitor_snapshot_history": (
        MySQLIndexDefinition(
            "monitor_snapshot_history",
            "idx_monitor_snapshot_history_generated",
            "`generated_at`, `account_id`",
        ),
    ),
    "monitor_sent": (
        MySQLIndexDefinition("monitor_sent", "idx_monitor_sent_sent_at", "`sent_at`, `sent_key_hash`"),
    ),
    "notification_jobs": (
        MySQLIndexDefinition("notification_jobs", "idx_notification_jobs_created", "`created_at`, `job_id`"),
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
    ),
    "model_review_jobs": (
        MySQLIndexDefinition("model_review_jobs", "idx_model_review_jobs_created", "`created_at`, `job_id`"),
        MySQLIndexDefinition(
            "model_review_jobs",
            "idx_model_review_jobs_status_attempts_created",
            "`status`, `attempts`, `created_at`, `job_id`",
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
    ),
    "ontology_ai_opinion_samples": (
        MySQLIndexDefinition("ontology_ai_opinion_samples", "idx_ontology_quality_created", "`created_at`, `sample_id`"),
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
}


MYSQL_OPERATIONAL_COLUMNS: Dict[str, Sequence[MySQLColumnDefinition]] = {
    "service_accounts": (
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


def mysql_column_exists(connection, table: str, column_name: str) -> bool:
    cursor = _execute(connection, "SHOW COLUMNS FROM " + quote_identifier(table) + " LIKE %s", (column_name,))
    return bool(cursor.fetchone())


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
    return {
        "columns": ensure_mysql_columns(connection, MYSQL_OPERATIONAL_COLUMNS),
        "retiredColumns": retire_mysql_columns(connection, MYSQL_OPERATIONAL_RETIRED_COLUMNS),
        "indexes": ensure_mysql_indexes(connection, MYSQL_OPERATIONAL_INDEXES),
        "partitions": ensure_mysql_key_partitions(connection, MYSQL_OPERATIONAL_KEY_PARTITIONS, settings),
    }


def ensure_mysql_monitoring_schema_tuning(connection) -> Dict[str, List[str]]:
    return {
        "indexes": ensure_mysql_indexes(connection, MYSQL_MONITORING_INDEXES),
        "partitions": [],
    }
