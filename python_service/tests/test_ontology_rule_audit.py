import unittest

from digital_twin.domain.ontology_rule_audit import rule_audit_payload
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules


class OntologyRuleAuditTest(unittest.TestCase):
    def test_audit_distinguishes_no_sample_slow_and_failing_rules(self):
        rules = default_graph_inference_rules()[:3]
        runtime = {
            "status": "ok",
            "sampleCount": 22,
            "rules": [
                {
                    "ruleId": rules[0].rule_id,
                    "sampleCount": 12,
                    "matchedCount": 0,
                    "failureCount": 0,
                    "p95DurationMs": 120,
                },
                {
                    "ruleId": rules[1].rule_id,
                    "sampleCount": 8,
                    "matchedCount": 2,
                    "failureCount": 1,
                    "p95DurationMs": 7000,
                },
            ],
        }

        audit = rule_audit_payload(rules, runtime)
        by_id = {item["ruleId"]: item for item in audit["rules"]}

        self.assertEqual("observed-no-match", by_id[rules[0].rule_id]["status"])
        self.assertEqual("failing", by_id[rules[1].rule_id]["status"])
        self.assertIn(by_id[rules[2].rule_id]["status"], {"waiting-for-event", "cold-no-sample", "routing-gap-review"})
        self.assertIn(by_id[rules[0].rule_id]["assessmentScope"], {
            "evidence-quality", "investment-opinion", "portfolio-fit", "execution-readiness",
        })
        self.assertFalse(audit["automaticRuleChange"])


if __name__ == "__main__":
    unittest.main()
