import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_rulebox_release_manifest import (
    DEPRECATED_TYPEDB_RULE_IDS,
    RULEBOX_DECISION_SCOPE_RULE_IDS,
    RULEBOX_MARKET_EVIDENCE_GUARD_RULE_IDS,
    RULEBOX_PLATFORM_RELEASE_ADDITION_IDS,
    RULEBOX_RAW_ABOX_RUNTIME_RULE_IDS,
    RULEBOX_RUNTIME_CONTRACT_RULE_IDS,
    RULEBOX_RUNTIME_CONTRACT_RULE_VERSIONS,
    rulebox_release_manifest,
)


class RuleBoxReleaseManifestTests(unittest.TestCase):
    def test_release_manifest_references_current_bootstrap_rules_and_versions(self):
        rules = {rule.rule_id: rule for rule in default_graph_inference_rules()}

        self.assertTrue(RULEBOX_PLATFORM_RELEASE_ADDITION_IDS.issubset(rules))
        self.assertTrue(RULEBOX_RAW_ABOX_RUNTIME_RULE_IDS.issubset(rules))
        self.assertTrue(RULEBOX_DECISION_SCOPE_RULE_IDS.issubset(rules))
        self.assertEqual(
            RULEBOX_RAW_ABOX_RUNTIME_RULE_IDS
            | RULEBOX_DECISION_SCOPE_RULE_IDS
            | RULEBOX_MARKET_EVIDENCE_GUARD_RULE_IDS,
            RULEBOX_RUNTIME_CONTRACT_RULE_IDS,
        )
        self.assertFalse(DEPRECATED_TYPEDB_RULE_IDS.intersection(rules))
        for rule_id, expected_version in RULEBOX_RUNTIME_CONTRACT_RULE_VERSIONS.items():
            self.assertEqual(expected_version, rules[rule_id].version, rule_id)

    def test_manifest_is_explicitly_excluded_from_runtime_decisions(self):
        payload = rulebox_release_manifest()

        self.assertFalse(payload["runtimeDecisionUse"])
        self.assertEqual(
            sorted(RULEBOX_PLATFORM_RELEASE_ADDITION_IDS),
            payload["platformAdditionRuleIds"],
        )
        self.assertEqual(
            sorted(RULEBOX_DECISION_SCOPE_RULE_IDS),
            payload["decisionScopeContractRuleIds"],
        )
        self.assertEqual(
            sorted(RULEBOX_MARKET_EVIDENCE_GUARD_RULE_IDS),
            payload["marketEvidenceGuardRuleIds"],
        )


if __name__ == "__main__":
    unittest.main()
