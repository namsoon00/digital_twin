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

    def test_ontology_reasoning_once_recovers_only_dead_local_write_leases_before_running(self):
        repository = SimpleNamespace(
            recover_dead_local_scoped_abox_write_lease=lambda: {"status": "cleared"},
        )
        runner = SimpleNamespace(run_once=lambda **_kwargs: {"status": "idle"})
        args = SimpleNamespace(
            ontology_reasoning_action="once",
            limit=20,
            force=False,
        )

        with patch("digital_twin.infrastructure.cli.runtime_settings", return_value={}), \
                patch("digital_twin.infrastructure.cli.ontology_repository_from_settings", return_value=repository), \
                patch("digital_twin.infrastructure.cli.build_ontology_reasoning_runner", return_value=runner), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            result = ontology_reasoning_command(args)

        self.assertEqual(0, result)
        self.assertIn('"localScopedABoxWriteLeaseRecovery": {"status": "cleared"}', output.getvalue())

    def test_ontology_reasoning_prefers_all_world_local_write_lease_recovery(self):
        repository = SimpleNamespace(
            recover_all_dead_local_scoped_abox_write_leases=lambda: {
                "status": "cleared",
                "clearedWorldIds": ["portfolio:local:default"],
            },
            recover_dead_local_scoped_abox_write_lease=lambda: (_ for _ in ()).throw(
                AssertionError("legacy single-world recovery must not be used")
            ),
        )
        runner = SimpleNamespace(run_once=lambda **_kwargs: {"status": "idle"})
        args = SimpleNamespace(
            ontology_reasoning_action="once",
            limit=20,
            force=False,
        )

        with patch("digital_twin.infrastructure.cli.runtime_settings", return_value={}), \
                patch("digital_twin.infrastructure.cli.ontology_repository_from_settings", return_value=repository), \
                patch("digital_twin.infrastructure.cli.build_ontology_reasoning_runner", return_value=runner), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            result = ontology_reasoning_command(args)

        self.assertEqual(0, result)
        self.assertIn("portfolio:local:default", output.getvalue())
