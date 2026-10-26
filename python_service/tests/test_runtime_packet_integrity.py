"""Replay packet preparation preserves bytes, source clocks and isolation."""

import base64
from copy import deepcopy
import hashlib
import json
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

from digital_twin.modules.reasoning.domain.reasoning_shadow import (
    ONTOLOGY_RUNTIME_POLICY_SETTING_KEYS, frozen_projection_runtime_context,
    pack_projection_runtime_contexts, unpack_projection_runtime_contexts,
)


class RuntimePacketIntegrityTests(unittest.TestCase):
    def feature_projection(self):
        from digital_twin.infrastructure import ontology_projection as api
        from digital_twin.modules.market_data.domain.time_series_storage import (
            TemporalFeatureSnapshot, TimeSeriesWatermark,
        )
        from projection_input_fixture import clear_caches, recorder, source_snapshot, PersistentCache
        clear_caches(api)
        self.addCleanup(clear_caches, api)
        snapshot = source_snapshot()
        feature = TemporalFeatureSnapshot.create(
            "fixture", snapshot.account_id, snapshot.generated_at,
            {"AAA": {"3D": [{"price": 100, "sourceAsOf": snapshot.generated_at}]}},
            TimeSeriesWatermark("fixture", snapshot.generated_at),
        )
        scorer = Mock(return_value={"status": "ready", "featureSnapshot": feature})
        persistent = PersistentCache()
        instance = recorder(api, snapshot, cache=True, persistent=persistent,
                            scorer=SimpleNamespace(run=scorer))
        return api, instance, snapshot, feature, persistent, scorer

    def test_oversized_replay_retains_exact_feature_receipt_without_caching_incomplete_graph(self):
        from digital_twin.modules.reasoning.domain import reasoning_shadow
        api, instance, snapshot, feature, persistent, scorer = self.feature_projection()
        expected = {k: v for k, v in feature.to_dict(False).items() if k in {
            "snapshotId", "payloadHash", "windowsHash", "backendId", "featureSetVersion", "asOf", "symbols",
        }}
        # Exercise the real byte limit in both capture and post-scoring packing.
        # An unavailable optional replay must not erase the executed input ID.
        with patch.object(reasoning_shadow, "MAX_PROJECTION_RUNTIME_CONTEXT_BYTES", 1):
            for _ in range(2):
                graph, persisted, telemetry = instance.build_graph_assembly(snapshot, {})
                self.assertTrue(graph.entities)
                self.assertTrue(persisted.entities)
                self.assertEqual(expected, telemetry["temporalFeatureInput"])
                self.assertEqual("none", telemetry["cacheLayer"])
        self.assertEqual(2, scorer.call_count)
        self.assertEqual({}, persistent.rows)
        self.assertFalse(api.SHARED_PORTFOLIO_GRAPH_ASSEMBLY_CACHE.entries)
        self.assertEqual(snapshot.generated_at, telemetry["temporalFeatureInput"]["asOf"])
        # A failed current model run cannot inherit a feature receipt from an
        # earlier context, even when that context has a valid historical ID.
        instance.runtime_context_overrides[snapshot.account_id]["temporalFeatureSnapshot"] = feature.to_dict(False)
        scorer.side_effect = RuntimeError("feature source unavailable")
        with patch.object(reasoning_shadow, "MAX_PROJECTION_RUNTIME_CONTEXT_BYTES", 1):
            _, _, failed = instance.build_graph_assembly(snapshot, {})
        self.assertEqual({}, failed["temporalFeatureInput"])
        # A serializable failed run must not restore an earlier feature ID
        # from its cache packet on the following read either.
        _, _, failed = instance.build_graph_assembly(snapshot, {})
        _, _, cached_failure = instance.build_graph_assembly(snapshot, {})
        self.assertEqual("memory", cached_failure["cacheLayer"])
        self.assertEqual({}, failed["temporalFeatureInput"])
        self.assertEqual({}, cached_failure["temporalFeatureInput"])

    def test_cache_with_missing_corrupt_or_other_account_context_rebuilds_input_receipt(self):
        from projection_input_fixture import clear_caches
        api, instance, snapshot, _, persistent, scorer = self.feature_projection()
        graph, persisted, original = instance.build_graph_assembly(snapshot, {})
        key, cached = next(iter(persistent.rows.items()))
        wrong_account = pack_projection_runtime_contexts({"other-account": {"asOf": snapshot.generated_at}})
        packets = [{}, {**cached["runtimeContextPacket"], "sha256": "corrupt"}, wrong_account]
        for layer in ("memory", "persistent"):
            for packet in packets:
                with self.subTest(layer=layer, packetKeys=list(packet)):
                    clear_caches(api)
                    persistent.rows.clear()
                    if layer == "memory":
                        api.SHARED_PORTFOLIO_GRAPH_ASSEMBLY_CACHE.put(key, graph, persisted, 10, packet)
                    else:
                        persistent.rows[key] = {**deepcopy(cached), "runtimeContextPacket": packet}
                    before = scorer.call_count
                    _, _, rebuilt = instance.build_graph_assembly(snapshot, {})
                    self.assertEqual(before + 1, scorer.call_count)
                    self.assertEqual("none", rebuilt["cacheLayer"])
                    self.assertEqual(original["temporalFeatureInput"], rebuilt["temporalFeatureInput"])
                    # The replacement is reusable only with its original,
                    # account-bound replay context and exact feature receipt.
                    _, _, reused = instance.build_graph_assembly(snapshot, {})
                    self.assertEqual(before + 1, scorer.call_count)
                    self.assertEqual("memory", reused["cacheLayer"])
                    self.assertEqual(original["temporalFeatureInput"], reused["temporalFeatureInput"])

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
