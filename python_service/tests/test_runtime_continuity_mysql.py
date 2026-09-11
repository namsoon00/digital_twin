"""Real SQL predicate checks with literals only: no selected schema or tables."""

from copy import deepcopy
import json
import os
import unittest

from runtime_continuity_reads import ReadOnlyDatabase, SOURCE_SCOPE_MATCH_SQL, database_options
from verify_runtime_continuity import ObservationDeadline, deadline


# Deliberately incompatible text collations reproduce the JSON_TABLE boundary
# failure without creating tables or changing any session/database collation.
SCOPE_FIXTURE_SQL = "SELECT " + SOURCE_SCOPE_MATCH_SQL + """ AS matched,
 DATABASE() IS NULL AS no_database, @@session.transaction_read_only AS read_only,
 COLLATION(s.account_id) <> COLLATION(b.account_id) AS different_collations
FROM (SELECT CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci AS account_id) s
CROSS JOIN (SELECT CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci AS account_id) l
CROSS JOIN (SELECT %s AS event_id, %s AS payload_json) e
CROSS JOIN (SELECT
 CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci AS account_id,
 CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci AS snapshot_id,
 CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci AS generated_at) v
LEFT JOIN JSON_TABLE(%s, '$[*]' COLUMNS (
 account_id VARCHAR(191) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci PATH '$.accountId',
 snapshot_id VARCHAR(191) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci PATH '$.snapshotId',
 generated_at VARCHAR(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci PATH '$.generatedAt')) b ON TRUE"""


class RuntimeContinuityMySQLTests(unittest.TestCase):
    def test_source_scope_predicate_on_read_only_mysql_literals(self):
        base = {"subject": "Account-A", "lineage": "Account-A", "event": "fixture-event",
                "payload": {"accountId": "Account-A"}, "snapshot_account": "Account-A",
                "snapshot_id": "Snapshot-A", "generated_at": "2026-01-01T12:00:00Z",
                "boundaries": [{"accountId": "Account-A", "snapshotId": "Snapshot-A",
                                "generatedAt": "2026-01-01T12:00:00Z"}]}
        cases = [
            ("different-collations-exact-identity", {}, 1),
            ("blank-lineage-singular-source", {"lineage": ""}, 1),
            ("missing-lineage-singular-source", {"lineage": None}, 1),
            ("lineage-cannot-override-explicit-event", {"payload": {"accountId": "Account-B"}}, 0),
            ("event-cannot-override-explicit-lineage", {"lineage": "Account-B"}, 0),
            ("global-exact-boundary", {"payload": {}, "lineage": ""}, 1),
            ("global-matching-lineage-exact-boundary", {"payload": {}}, 1),
            ("global-missing-boundary", {"payload": {}, "boundaries": []}, None),
            ("scoped-missing-boundary", {"boundaries": []}, None),
            ("missing-event", {"event": None}, None),
            ("missing-payload", {"payload": None}, None),
            ("non-object-payload-is-unknown", {"payload": []}, None),
            ("missing-stored-snapshot", {"snapshot_id": None}, None),
            ("plural-precedes-conflicting-singular", {"payload": {"accountIds": ["Account-A"], "accountId": "Account-B"}}, 1),
            ("plural-exclusion-cannot-use-singular", {"payload": {"accountIds": ["Account-B"], "accountId": "Account-A"}}, 0),
            ("empty-plural-uses-singular", {"payload": {"accountIds": [], "accountId": "Account-A"}}, 1),
            ("null-plural-uses-singular", {"payload": {"accountIds": None, "accountId": "Account-A"}}, 1),
            ("malformed-plural-is-unknown", {"payload": {"accountIds": "Account-A"}}, None),
            ("singular-case-sensitive", {"payload": {"accountId": "account-a"}}, 0),
            ("plural-case-sensitive", {"payload": {"accountIds": ["account-a"]}}, 0),
            ("lineage-case-sensitive", {"lineage": "account-a"}, 0),
            ("snapshot-account-case-sensitive", {"snapshot_account": "account-a"}, 0),
            ("snapshot-id-case-sensitive", {"snapshot_id": "snapshot-a"}, 0),
            ("snapshot-time-exact", {"generated_at": "2026-01-01T12:00:01Z"}, 0),
            ("boundary-account-exact", {"boundaries": [{**base["boundaries"][0], "accountId": "Account-B"}]}, 0),
            ("boundary-time-missing", {"boundaries": [{**base["boundaries"][0], "generatedAt": None}]}, None),
        ]
        environ = {key: os.environ[key] for key in (
            "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_UNIX_SOCKET") if key in os.environ}
        environ["MYSQL_USER"] = environ.get("MYSQL_USER") or "root"
        environ["MYSQL_DATABASE"] = "runtime_continuity_literal_fixture"
        db = None
        try:
            with deadline(20):
                options = database_options(environ, 3)
                del options["database"]
                db = ReadOnlyDatabase(options, row_limit=1, query_ms=1000)
                for name, changes, expected in cases:
                    with self.subTest(case=name):
                        case = dict(deepcopy(base), **changes)
                        payload = json.dumps(case["payload"]) if case["payload"] is not None else None
                        result = db.read(SCOPE_FIXTURE_SQL, (
                            case["subject"], case["lineage"], case["event"], payload,
                            case["snapshot_account"], case["snapshot_id"], case["generated_at"],
                            json.dumps(case["boundaries"])))
                        self.assertFalse(result["truncated"])
                        self.assertEqual(len(result["rows"]), 1)
                        row = result["rows"][0]
                        self.assertEqual(row["matched"], expected)
                        self.assertEqual(row["no_database"], 1)
                        self.assertEqual(row["read_only"], 1)
                        if case["boundaries"]:
                            self.assertEqual(row["different_collations"], 1)
                self.assertEqual(db.queries, len(cases))
        except (Exception, ObservationDeadline) as error:
            code = error.args[0] if error.args and type(error.args[0]) is int else None
            raise AssertionError("Read-only literal MySQL check failed; numeric code=" + str(code)) from None
        finally:
            if db:
                db.close()


if __name__ == "__main__":
    unittest.main()
