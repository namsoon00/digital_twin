"""Bounded schema-bootstrap defaults shared by the facade and runtime."""

# Fresh candidates need smaller commits to keep the schema compiler from
# monopolizing TypeDB during cold starts; the seed retains its end-to-end budget.
DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE = 64
DEFAULT_TYPEDB_FRESH_SCHEMA_BOOTSTRAP_BATCH_SIZE = 16
DEFAULT_TYPEDB_FRESH_SCHEMA_BOOTSTRAP_TIMEOUT_SECONDS = 60.0
