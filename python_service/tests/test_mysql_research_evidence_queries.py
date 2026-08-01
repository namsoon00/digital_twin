import unittest

from digital_twin.infrastructure.mysql_research_evidence import MySQLResearchEvidenceStore


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchall(self):
        return list(self.rows)


class Connection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        rendered = str(sql)
        self.calls.append((rendered, tuple(params or ())))
        if rendered.startswith("SELECT evidence_id"):
            return Cursor([{"evidence_id": "new"}, {"evidence_id": "old"}])
        return Cursor([{"evidence_id": "old"}, {"evidence_id": "new"}])


class MySQLResearchEvidenceQueryTests(unittest.TestCase):
    def test_latest_rows_sorts_only_ids_then_fetches_selected_payloads(self):
        connection = Connection()

        rows = MySQLResearchEvidenceStore._latest_rows(
            connection,
            ["lifecycle_state = 'active'", "kind = %s"],
            ["news"],
            160,
            0,
        )

        self.assertEqual(["new", "old"], [row["evidence_id"] for row in rows])
        self.assertEqual(2, len(connection.calls))
        first_sql, first_params = connection.calls[0]
        self.assertTrue(first_sql.startswith("SELECT evidence_id"))
        self.assertNotIn("SELECT *", first_sql)
        self.assertIn("ORDER BY last_seen_at DESC, published_at DESC, evidence_id DESC", first_sql)
        self.assertEqual(("news", 160, 0), first_params)
        self.assertTrue(connection.calls[1][0].startswith("SELECT * FROM research_evidence WHERE evidence_id IN"))


if __name__ == "__main__":
    unittest.main()
