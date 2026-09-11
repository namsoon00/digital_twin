"""Storage/control constants; investment engine version stays in typeql.constants."""

NATIVE_RULE_EVIDENCE_READ_INDEX_TYPED_VERSION = "native-rule-evidence-read-index-v2"

NATIVE_RULE_EVIDENCE_READ_INDEX_LEGACY_VERSION = "native-rule-evidence-read-index-v1"

TYPEDB_NATIVE_REASONING_MODE = "typedb-native-rule-materialized"

TYPEDB_NATIVE_BLOCKED_MODE = "typedb-native-rule-materialization-blocked"

TYPEDB_NATIVE_REQUIRED_MODE = "typedb-native-rule-materialization-required"

TYPEDB_NATIVE_MATERIALIZATION_SOURCE = "typedb-abox-native-rule"

SCOPED_ABOX_WRITE_LEASE_ID = "scoped-abox-write-lease"

SCOPED_ABOX_WRITE_LEASE_BOX = "ABoxLease"

SCOPED_ABOX_WRITE_LEASE_VERSION = "scoped-abox-write-lease-v1"

TYPEDB_PROJECTION_COORDINATOR_WORLD_ID = "system:typedb-projection-coordinator"

TYPEDB_PROJECTION_COORDINATOR_VERSION = "typedb-projection-coordinator-v1"

DEFAULT_TYPEDB_NATIVE_RULE_QUERY_TIMEOUT_SECONDS = 30.0

DEFAULT_TYPEDB_NATIVE_RULE_INDEXED_ANY_CONDITION_QUERY_TIMEOUT_SECONDS = 20.0

DEFAULT_TYPEDB_NATIVE_RULE_EXECUTION_BUDGET_SECONDS = 105.0

DEFAULT_TYPEDB_NATIVE_RULE_PARALLELISM = 4

DEFAULT_TYPEDB_NATIVE_RULE_TARGET_PARALLELISM = 1
