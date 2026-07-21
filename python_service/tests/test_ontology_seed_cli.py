from types import SimpleNamespace
import unittest
from unittest.mock import patch

from digital_twin.infrastructure.cli import ontology_command


class OntologySeedCliTests(unittest.TestCase):
    def test_ontology_seed_command_accepts_current_static_graph_as_success(self):
        repository = SimpleNamespace(seed_ontology=lambda _payload: {
            "configured": True,
            "saved": True,
            "seeded": True,
            "status": "unchanged",
        })
        args = SimpleNamespace(
            ontology_action="seed",
            replace_rulebox=True,
            clear_inference=False,
        )

        with patch("digital_twin.infrastructure.cli.runtime_settings", return_value={}), \
                patch("digital_twin.infrastructure.cli.ontology_repository_from_settings", return_value=repository):
            result = ontology_command(args)

        self.assertEqual(0, result)
