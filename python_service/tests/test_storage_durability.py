import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from digital_twin import service_manager as manager


class CandidateDurabilityTests(unittest.TestCase):
    def test_process_ownership_uses_exact_storage_path_not_prefix(self):
        text = ("100 S /bin/typedb_server_bin --storage.data-directory /tmp/store\n"
                "101 S /bin/typedb_server_bin --storage.data-directory=/tmp/store-candidate\n"
                "102 Z /bin/typedb_server_bin --storage.data-directory /tmp/store\n")
        with patch.object(manager.subprocess, "check_output", return_value=text):
            self.assertEqual([100], manager.typedb_process_ids_for_data_path('/tmp/store'))
            self.assertEqual([101], manager.typedb_process_ids_for_data_path('/tmp/store-candidate'))

    def test_failed_owner_inspection_cannot_authorize_cutover(self):
        with patch.object(manager.subprocess, "check_output", side_effect=OSError('unavailable')):
            with self.assertRaises(RuntimeError):
                manager.typedb_process_ids_for_data_path('/tmp/store', strict=True)

    def test_live_owner_prevents_directory_swap(self):
        with tempfile.TemporaryDirectory() as root:
            active, candidate = Path(root)/'active', Path(root)/'candidate'
            active.mkdir(); candidate.mkdir()
            (active/'identity').write_text('original')
            with patch.object(manager, "typedb_process_ids_for_data_path", return_value=[123]):
                result = manager.swap_typedb_blue_green_data_paths({'dataPath': active}, {'dataPath': candidate})
            self.assertEqual('cutover-owner-still-running', result['status'])
            self.assertEqual('original', (active/'identity').read_text())

    def test_empty_wal_tail_is_rejected_without_modifying_files(self):
        with tempfile.TemporaryDirectory() as root:
            database = Path(root)/'db'
            (database/'checkpoint').mkdir(parents=True)
            (database/'wal').mkdir()
            tail = database/'wal'/'wal-0000000000000000000015686'
            tail.write_bytes(b'')
            self.assertFalse(manager.typedb_stopped_candidate_wal_ready(root))
            self.assertEqual(b'', tail.read_bytes())

    def probe(self, fingerprints, launch=True):
        candidate = {"role": "typedb-stage", "pid": Path("/unused/candidate.pid"), "typedbDatabase": "frozen"}
        with patch.object(manager, "typedb_blue_green_database_specs", return_value=[candidate]), \
             patch.object(manager, "typedb_candidate_world_fingerprint", side_effect=fingerprints) as worlds, \
             patch.object(manager, "stop_worker", return_value=0), \
             patch.object(manager, "stop_typedb_stage_data_path_processes", return_value=True), \
             patch.object(manager, "remove_pid"), \
             patch.object(manager, "launch_typedb_stage_process", return_value=launch), \
             patch.object(manager, "validate_typedb_candidate_release_contract", return_value={"ready": True}) as release, \
             patch.object(manager, "validate_typedb_candidate_inference_runtime", return_value={"ready": True}), \
             patch.object(manager, "ensure_typedb_shared_world_projection_rebuilt") as rebuild, \
             patch.object(manager, "swap_typedb_blue_green_data_paths") as swap, \
             patch.object(manager, "clear_typedb_stage_incomplete_checkpoints") as repair:
            result = manager.validate_typedb_candidate_durability(candidate, {"frozen": {"artifactFingerprint": "immutable"}})
        rebuild.assert_not_called()
        swap.assert_not_called()
        repair.assert_not_called()
        return result, worlds, release

    def test_recovery_failure_preserves_active_store_and_does_not_repair_candidate(self):
        result, worlds, release = self.probe([{"worlds": 2, "sha256": "before"}], launch=False)
        self.assertFalse(result["ready"])
        self.assertEqual("candidate-recovery-failed", result["reason"])
        self.assertEqual(1, worlds.call_count)
        release.assert_not_called()

    def test_lost_world_after_successful_restart_blocks_promotion(self):
        result, _, _ = self.probe([{"worlds": 2, "sha256": "before"}, {"worlds": 1, "sha256": "after"}])
        self.assertEqual("post-restart-world-mismatch", result["reason"])
        self.assertFalse(result["ready"])

    def test_frozen_release_and_exact_worlds_survive_reopen(self):
        result, _, release = self.probe([{"worlds": 2, "sha256": "same"}] * 2)
        self.assertTrue(result["ready"])
        self.assertEqual(2, result["worlds"])
        self.assertEqual("immutable", release.call_args.args[0]["_typedbSeedContract"]["artifactFingerprint"])
