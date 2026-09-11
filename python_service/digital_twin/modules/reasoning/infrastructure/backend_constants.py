"""Storage/control constants; investment engine version stays in typeql.constants."""

NATIVE_RULE_EVIDENCE_READ_INDEX_TYPED_VERSION = 'native-rule-evidence-read-index-v2'
NATIVE_RULE_EVIDENCE_READ_INDEX_LEGACY_VERSION = 'native-rule-evidence-read-index-v1'


TYPEDB_NATIVE_REASONING_MODE = "typedb-native-rule-materialized"
TYPEDB_NATIVE_BLOCKED_MODE = "typedb-native-rule-materialization-blocked"
TYPEDB_NATIVE_REQUIRED_MODE = "typedb-native-rule-materialization-required"
TYPEDB_NATIVE_MATERIALIZATION_SOURCE = "typedb-abox-native-rule"
# Active ABox generations are selected through a control pointer. Every direct
# TypeQL query binds the exact Manifest and world, so inactive generations can
# never satisfy a current rule evaluation.
# Scoped ABox writes span many short TypeDB transactions. Keep their lease in
# a separate control box so pointer replacement cannot delete it mid-write.
SCOPED_ABOX_WRITE_LEASE_ID = "scoped-abox-write-lease"
SCOPED_ABOX_WRITE_LEASE_BOX = "ABoxLease"
SCOPED_ABOX_WRITE_LEASE_VERSION = "scoped-abox-write-lease-v1"
# TypeDB accepts separate logical worlds, but its write path still has one
# database-wide contention domain.  This synthetic world owns that narrow
# physical-write coordinator without becoming an ABox fact or a user world.
TYPEDB_PROJECTION_COORDINATOR_WORLD_ID = "system:typedb-projection-coordinator"
TYPEDB_PROJECTION_COORDINATOR_VERSION = "typedb-projection-coordinator-v1"
# The production RuleBox can plan many direct TypeQL rules for one
# material symbol. A verified live ABox replay prunes most of them, but an
# applicable Manifest-scoped query can still take several seconds on the
# local TypeDB planner. A one-symbol complete replay with exact preflight
# takes about a minute for the current RuleBox, so keep bounded headroom for a
# complete generation rather than turning a slow, otherwise valid query
# into a partial judgement.
# The reasoning scheduler and circuit breaker continue to bound aggregate CPU.
DEFAULT_TYPEDB_NATIVE_RULE_QUERY_TIMEOUT_SECONDS = 30.0
# An indexed N-of-M group is bound to one verified active ABox source and its
# exact relation rows. It is structurally bounded but TypeDB's aggregation
# planner can take longer than an ordinary direct rule lookup on a cold
# local server. Keep this allowance separate from the broad-query deadline.
DEFAULT_TYPEDB_NATIVE_RULE_INDEXED_ANY_CONDITION_QUERY_TIMEOUT_SECONDS = 20.0
# A complete one-symbol production replay needs roughly 84 seconds on the
# local TypeDB dataset. Leave headroom for the final applicable rule instead
# of failing a whole candidate when its last bounded read starts with <1s.
DEFAULT_TYPEDB_NATIVE_RULE_EXECUTION_BUDGET_SECONDS = 105.0
# Direct TypeQL rules are independent read-only evaluations while the scoped ABox
# write lease is held. Four workers keep local TypeDB planner pressure bounded
# while removing the serial wait across the small applicable-rule set.
DEFAULT_TYPEDB_NATIVE_RULE_PARALLELISM = 4
# Target work is deliberately opt-in. A generation still has exactly one ABox
# activation and one InferenceBox publication; this value only controls how a
# verified target set is split into read-only TypeDB work items in between.
DEFAULT_TYPEDB_NATIVE_RULE_TARGET_PARALLELISM = 1
