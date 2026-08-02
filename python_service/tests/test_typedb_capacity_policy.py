import unittest

from digital_twin.domain.typedb_capacity_policy import evaluate_typedb_capacity_policy


class TypeDBCapacityPolicyTests(unittest.TestCase):
    def snapshot(self, size_mb=100, limit_mb=1000):
        return {
            "typedbSizeMb": size_mb,
            "typedbLimitMb": limit_mb,
            "typedbWalMb": size_mb * 0.5,
            "typedbCheckpointMb": size_mb * 0.4,
        }

    def test_normal_capacity_allows_every_role(self):
        policy = evaluate_typedb_capacity_policy(self.snapshot(300), role="world-projection")

        self.assertTrue(policy["ready"])
        self.assertEqual("normal", policy["mode"])
        self.assertFalse(policy["rotationRequired"])

    def test_pressure_yields_background_work_but_keeps_live_reasoning_available(self):
        projection = evaluate_typedb_capacity_policy(self.snapshot(800), role="world-projection")
        reasoning = evaluate_typedb_capacity_policy(self.snapshot(800), role="reasoning")
        maintenance = evaluate_typedb_capacity_policy(self.snapshot(800), role="maintenance")

        self.assertEqual("write-throttled", projection["mode"])
        self.assertFalse(projection["ready"])
        self.assertTrue(reasoning["ready"])
        self.assertTrue(maintenance["ready"])
        self.assertTrue(maintenance["capacityPriority"])
        self.assertTrue(maintenance["bypassReasoningDeferral"])

    def test_rotation_threshold_blocks_all_graph_writers(self):
        for role in ["reasoning", "world-projection", "maintenance", "rulebox-prewarm"]:
            policy = evaluate_typedb_capacity_policy(self.snapshot(900), role=role)
            self.assertFalse(policy["ready"])
            self.assertEqual("rotation-required", policy["mode"])
            self.assertTrue(policy["rotationRequired"])

    def test_low_disk_blocks_even_when_typedb_is_small(self):
        policy = evaluate_typedb_capacity_policy(
            self.snapshot(100),
            disk_health={"ready": False, "status": "blocked-low-disk", "reason": "reserve"},
        )

        self.assertFalse(policy["ready"])
        self.assertEqual("blocked-low-disk", policy["mode"])
        self.assertIn("reserve", policy["reason"])


if __name__ == "__main__":
    unittest.main()
