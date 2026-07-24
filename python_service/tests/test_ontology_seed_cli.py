import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from digital_twin.infrastructure.cli import ontology_command, ontology_reasoning_command


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

    def test_ontology_seed_command_requests_write_lease_recovery_when_explicit(self):
        captured = {}

        def seed(payload):
            captured.update(payload)
            return {
                "configured": True,
                "saved": True,
                "seeded": True,
                "status": "unchanged",
            }

        repository = SimpleNamespace(seed_ontology=seed)
        args = SimpleNamespace(
            ontology_action="seed",
            replace_rulebox=False,
            clear_inference=False,
            recover_scoped_write_lease=True,
        )

        with patch("digital_twin.infrastructure.cli.runtime_settings", return_value={}), \
                patch("digital_twin.infrastructure.cli.ontology_repository_from_settings", return_value=repository):
            result = ontology_command(args)

        self.assertEqual(0, result)
        self.assertTrue(captured["recoverScopedABoxWriteLease"])

    def test_scoped_write_lease_recovery_command_uses_managed_shutdown_recovery(self):
        repository = SimpleNamespace(
            recover_scoped_abox_write_lease_after_managed_shutdown=lambda: {
                "configured": True,
                "status": "cleared",
                "graphStore": "typedb",
            },
        )
        args = SimpleNamespace(ontology_action="recover-scoped-write-lease")

        with patch("digital_twin.infrastructure.cli.runtime_settings", return_value={}), \
                patch("digital_twin.infrastructure.cli.ontology_repository_from_settings", return_value=repository):
            result = ontology_command(args)

        self.assertEqual(0, result)

    def test_ontology_reasoning_once_defers_global_write_lease_inventory_until_world_acquisition(self):
        repository = SimpleNamespace(
            recover_all_dead_local_scoped_abox_write_leases=lambda: (_ for _ in ()).throw(
                AssertionError("worker startup must not inventory every TypeDB lease")
            ),
        )
        runner = SimpleNamespace(run_once=lambda **_kwargs: {"status": "idle"})
        args = SimpleNamespace(
            ontology_reasoning_action="once",
            limit=20,
            force=False,
        )

        with patch("digital_twin.infrastructure.cli.runtime_settings", return_value={}), \
                patch("digital_twin.infrastructure.cli.ontology_repository_from_settings", return_value=repository) as repository_factory, \
                patch("digital_twin.infrastructure.cli.build_ontology_reasoning_runner", return_value=runner), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            result = ontology_reasoning_command(args)

        self.assertEqual(0, result)
        repository_factory.assert_not_called()
        self.assertIn('"status": "deferred"', output.getvalue())
        self.assertIn("per-world-acquisition", output.getvalue())
