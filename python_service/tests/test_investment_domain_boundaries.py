import ast
import unittest
from pathlib import Path

from digital_twin.domain.ontology_domain_tbox import (
    DOMAIN_CLASS_DEFS,
    DOMAIN_RELATION_DEFS,
    DOMAIN_RULE_DEFS,
)


class InvestmentDomainBoundaryTests(unittest.TestCase):
    def test_domain_layer_does_not_import_infrastructure(self):
        root = Path(__file__).resolve().parents[1] / "digital_twin" / "domain"
        violations = []
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    names = [module]
                    if node.level >= 2 and module.startswith("infrastructure"):
                        violations.append(str(path.relative_to(root)))
                else:
                    continue
                if any(name.startswith("digital_twin.infrastructure") for name in names):
                    violations.append(str(path.relative_to(root)))
        self.assertEqual([], sorted(set(violations)))

    def test_closed_loop_is_explicit_in_tbox_contract(self):
        classes = {item.name for item in DOMAIN_CLASS_DEFS}
        relations = {item.name for item in DOMAIN_RELATION_DEFS}
        rules = {item.text for item in DOMAIN_RULE_DEFS}

        self.assertTrue({
            "DecisionOutcomeTarget",
            "DecisionReview",
            "DecisionLifecycleIncident",
        }.issubset(classes))
        self.assertTrue({
            "SCHEDULES_OUTCOME_OBSERVATION",
            "OBSERVES_OUTCOME_TARGET",
            "VALIDATES_SELECTED_HYPOTHESIS",
            "FEEDS_DECISION_REVIEW",
        }.issubset(relations))
        self.assertTrue(any("every final investment decision" in item for item in rules))
        self.assertTrue(any("NO_ACTION" in item for item in rules))
        self.assertTrue(any("delivery suppression" in item for item in rules))


if __name__ == "__main__":
    unittest.main()
