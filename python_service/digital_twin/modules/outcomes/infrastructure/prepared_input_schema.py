"""Transactional invalidation for prepared outcome inputs, including repair SQL.

These triggers invalidate read models only; they never evaluate a market rule.
Unobserved decision/observation inserts do not invalidate historical calibration.
"""

CONTRACT = "prepared-outcome-input-v1"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS outcome_input_cache_control (
        contract VARCHAR(96) PRIMARY KEY, generation CHAR(36) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS outcome_input_revisions (
        scope_key VARCHAR(191) PRIMARY KEY, generation CHAR(36) NOT NULL,
        revision BIGINT UNSIGNED NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS prepared_outcome_inputs (
        cache_key CHAR(64) PRIMARY KEY, revision_token VARCHAR(191) NOT NULL,
        from_at VARCHAR(40) NOT NULL, until_at VARCHAR(40) NOT NULL,
        qualification_until_at VARCHAR(40) NOT NULL,
        payload_json LONGTEXT NOT NULL, updated_at VARCHAR(40) NOT NULL,
        KEY idx_prepared_outcome_expiry (updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


def _bump():
    return ("INSERT INTO outcome_input_revisions (scope_key, generation, revision) "
            "VALUES ('calibration-history', UUID(), 1) ON DUPLICATE KEY UPDATE revision = revision + 1;")


def trigger_definitions():
    # Revision changes commit/roll back with the authoritative row. Tracking only
    # MAX(updated_at) would miss same-clock corrections and deletes.
    tables = {
        "investment_decision_outcomes": ("do", None),
        "investment_hypothesis_observation_outcomes": ("ho", None),
        "investment_decision_episodes": ("de", ("investment_decision_outcomes", "episode_id")),
        "investment_hypothesis_observation_episodes": ("he", ("investment_hypothesis_observation_outcomes", "observation_episode_id")),
        "hypothesis_development_cases": ("qc", "qualification"),
        "investment_decision_calibration_inputs": ("ci", "calibration"),
    }
    definitions = {}
    for table, (short, condition) in tables.items():
        for event, aliases in (("INSERT", ("NEW",)), ("UPDATE", ("OLD", "NEW")), ("DELETE", ("OLD",))):
            conditions = []
            for alias in aliases:
                if condition == "calibration":
                    conditions.append(f"EXISTS (SELECT 1 FROM investment_decision_outcomes WHERE episode_id = {alias}.episode_id)")
                elif condition == "qualification":
                    conditions.append(f"{alias}.status IN ('evolution-monitoring', 'strengthened')")
                elif condition:
                    outcome_table, episode_column = condition
                    conditions.append(f"EXISTS (SELECT 1 FROM {outcome_table} WHERE {episode_column} = {alias}.episode_id)")
            # A DB-wide history revision also covers legacy rows whose outcome
            # and episode account metadata disagree. Cache values remain scoped
            # by account/symbol. Pending-only writes never take this row lock.
            body = _bump()
            if conditions:
                body = f"IF {' OR '.join(conditions)} THEN {body} END IF;"
            name = f"outcome_input_v1_{short}_{event.lower()}"
            definitions[name] = f"CREATE TRIGGER {name} AFTER {event} ON {table} FOR EACH ROW BEGIN {body} END"
    return definitions


def ensure_prepared_input_schema(connection):
    """Install once under a DB-local lock; invalidate caches after any repair.

    Missing privileges fail schema bootstrap visibly. Workers with bootstrap
    disabled safely use uncached reads until this contract is installed.
    """
    for statement in SCHEMA:
        connection.execute(statement)
    lock = connection.execute("SELECT GET_LOCK(CONCAT('outcome-input:', LEFT(SHA2(DATABASE(), 256), 40)), 10) AS acquired").fetchone() or {}
    if lock.get("acquired") != 1:
        raise RuntimeError("outcome input cache schema lock unavailable")
    try:
        definitions = trigger_definitions()
        rows = connection.execute(
            "SELECT TRIGGER_NAME AS name FROM information_schema.TRIGGERS "
            "WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME LIKE %s", ("outcome_input_v1_%",)
        ).fetchall()
        existing = {row["name"] for row in rows}
        missing = set(definitions) - existing
        if missing:
            # DDL commits implicitly. Remove readiness before the first DDL, so
            # another worker cannot accept a partly installed revision contract.
            connection.execute("DELETE FROM outcome_input_cache_control WHERE contract = %s", (CONTRACT,))
            for name in sorted(missing):
                connection.execute(definitions[name])
        connection.execute(
            "INSERT IGNORE INTO outcome_input_cache_control (contract, generation) VALUES (%s, UUID())", (CONTRACT,)
        )
    finally:
        connection.execute("SELECT RELEASE_LOCK(CONCAT('outcome-input:', LEFT(SHA2(DATABASE(), 256), 40)))")
