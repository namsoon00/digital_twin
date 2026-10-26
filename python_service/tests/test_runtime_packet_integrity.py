"""Replay packet preparation preserves bytes, source clocks and isolation."""

import base64
from copy import deepcopy
import hashlib
import json
import unittest
import zlib

from digital_twin.modules.reasoning.domain.reasoning_shadow import (
    ONTOLOGY_RUNTIME_POLICY_SETTING_KEYS, frozen_projection_runtime_context,
    pack_projection_runtime_contexts, unpack_projection_runtime_contexts,
)


class RuntimePacketIntegrityTests(unittest.TestCase):
    def test_packet_matches_original_serialization_without_copying_entire_history(self):
        context = {
            "settings": {"fxRates": {"USD": 1300}, "apiKey": "private-key"},
            "temporalObservationWindows": {"AAA": [{"sourceAsOf": "2026-10-26T01:00:00Z", "value": 0}]},
            "optional": None,
        }
        original = deepcopy(context)
        expected = deepcopy(context)
        expected["settings"] = {k: deepcopy(expected["settings"][k])
                                for k in sorted(ONTOLOGY_RUNTIME_POLICY_SETTING_KEYS) if k in expected["settings"]}
        raw = json.dumps({"account": expected}, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str).encode("utf-8")
        packet = pack_projection_runtime_contexts({"account": context})
        self.assertEqual(hashlib.sha256(raw).hexdigest(), packet["sha256"])
        self.assertEqual(base64.b64encode(zlib.compress(raw, level=9)).decode("ascii"), packet["data"])
        self.assertEqual(original, context)
        frozen = frozen_projection_runtime_context(context)
        context["settings"]["fxRates"]["USD"] = 1
        context["temporalObservationWindows"]["AAA"][0]["sourceAsOf"] = "changed"
        self.assertEqual(expected, frozen)
        decoded = unpack_projection_runtime_contexts(packet)
        self.assertEqual({"account": expected}, decoded)
        decoded["account"]["settings"]["fxRates"]["USD"] = 2
        decoded["account"]["temporalObservationWindows"]["AAA"].clear()
        self.assertEqual({"account": expected}, unpack_projection_runtime_contexts(packet))

    def test_corrupt_hash_size_account_and_compressed_data_remain_rejected(self):
        packet = pack_projection_runtime_contexts({"account": {"settings": {}, "sourceAsOf": "original"}})
        for change in ({"sha256": "bad"}, {"uncompressedBytes": 1}, {"accountIds": ["other"]},
                       {"data": "bad-base64"}, {"version": "unknown"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                unpack_projection_runtime_contexts({**packet, **change})


if __name__ == "__main__":
    unittest.main()
