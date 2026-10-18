import unittest
from copy import deepcopy

from digital_twin.modules.reasoning.domain.ontology_scopes import target_scope_manifest_fingerprint
from digital_twin.modules.reasoning.domain.reasoning_batch import reasoning_batch_key
from digital_twin.modules.reasoning.domain.reasoning_comparison_identity import comparison_input_contract


class ReasoningLogicalInputTests(unittest.TestCase):
    def plan(self):
        return [
            {"scopeId": "symbol:NVDA:market", "baseFingerprint": "price-100", "fingerprint": "storage-price-a"},
            {"scopeId": "symbol:NVDA:position", "baseFingerprint": "holding-8", "fingerprint": "storage-position-a"},
            {"scopeId": "link:symbol:NVDA:position", "baseFingerprint": "owns-position",
             "fingerprint": "storage-link-a", "dependencyScopeIds": ["symbol:NVDA:market", "symbol:NVDA:position"]},
            {"scopeId": "symbol:PLTR:market", "baseFingerprint": "other-price", "fingerprint": "other-storage"},
        ]

    def test_isolated_storage_generations_preserve_facts_and_dependency_changes_do_not(self):
        left = self.plan()
        right = deepcopy(left)
        for row in right:
            row.update(fingerprint="different-storage-" + row["scopeId"], generationId="different-generation")
        expected = target_scope_manifest_fingerprint(left, ["NVDA"])
        self.assertTrue(expected["complete"])
        self.assertEqual(expected, target_scope_manifest_fingerprint(right, ["NVDA"]))
        right[-1]["baseFingerprint"] = "unrelated-change"
        self.assertEqual(expected, target_scope_manifest_fingerprint(right, ["NVDA"]))
        for index in (0, 1, 2):
            changed = deepcopy(right)
            changed[index]["baseFingerprint"] = "changed-actual-assertion"
            self.assertNotEqual(expected["fingerprint"], target_scope_manifest_fingerprint(changed, ["NVDA"])["fingerprint"])
        changed = deepcopy(right)
        changed[2]["dependencyScopeIds"] = ["symbol:NVDA:position"]
        self.assertNotEqual(expected["fingerprint"], target_scope_manifest_fingerprint(changed, ["NVDA"])["fingerprint"])

    def test_missing_fragment_and_legacy_proof_cannot_establish_input_equality(self):
        plan = self.plan()
        plan[2]["dependencyScopeIds"].append("symbol:NVDA:missing")
        scope = target_scope_manifest_fingerprint(plan, ["NVDA"])
        self.assertFalse(scope["complete"])
        job = {"jobId": "job-a", "scopeKey": "scope", "sourceSnapshotId": "source",
               "sourceSnapshotAt": "2026-10-18T00:00:00Z", "sourcePayloadHash": "hash",
               "sourceEvent": {"payload": {"accountIds": ["acct"], "symbols": ["NVDA"]}},
               "result": {"projection_results": {"acct": {"comparisonScope": scope, "persistedComparisonScope": scope}}}}
        self.assertFalse(comparison_input_contract(job, deepcopy(job))["checks"]["aboxInputs"])
        scope = target_scope_manifest_fingerprint(self.plan(), ["NVDA"])
        scope.pop("contractVersion")
        job["result"]["projection_results"]["acct"] = {"comparisonScope": scope, "persistedComparisonScope": scope}
        self.assertFalse(comparison_input_contract(job, deepcopy(job))["checks"]["aboxInputs"])

    def test_source_batch_never_combines_different_subject_bundles(self):
        source = {"payload": {"accountIds": ["acct"], "symbols": ["NVDA"],
                              "verifiedSourceSnapshot": {"snapshotId": "immutable", "generatedAt": "clock"}}}
        other = deepcopy(source)
        other["payload"]["symbols"] = ["PLTR"]
        self.assertNotEqual(reasoning_batch_key(source, "CONTEXT"), reasoning_batch_key(other, "CONTEXT"))
        other["payload"]["symbols"] = ["NVDA"]
        self.assertEqual(reasoning_batch_key(source, "CONTEXT"), reasoning_batch_key(other, "CONTEXT"))
        other["payload"]["candidateValidationReplay"] = True
        self.assertNotEqual(reasoning_batch_key(source, "CONTEXT"), reasoning_batch_key(other, "CONTEXT"))
        other["payload"].pop("candidateValidationReplay")
        other["payload"]["verifiedSourceSnapshot"]["snapshotId"] = "newer"
        self.assertNotEqual(reasoning_batch_key(source, "CONTEXT"), reasoning_batch_key(other, "CONTEXT"))

    def test_paired_validation_uses_one_durable_subject_and_is_idempotent(self):
        from types import SimpleNamespace
        from digital_twin.modules.portfolio.contracts import AccountSnapshot, PortfolioSummary, Position
        from digital_twin.modules.reasoning.application.paired_reasoning_validation import PairedReasoningValidationService
        from digital_twin.modules.reasoning.domain.independent_reasoning import independent_reasoning_request
        snapshot = AccountSnapshot(account_id="acct", account_label="Test", provider="toss", mode="live", status="ok",
                                   generated_at="2026-10-18T00:00:00Z", portfolio=PortfolioSummary(1000, 100, 900, [], [], 10),
                                   positions=[Position(symbol="NVDA", name="NVIDIA", current_price=100, quantity=1,
                                                       market="US", currency="USD", source="holding")])
        events = []
        registry = SimpleNamespace(control=lambda: SimpleNamespace(delivery_deployment_id="baseline", active_deployment_id="baseline",
                                                                    candidate_deployment_id="candidate"),
                                   get=lambda _key: {"status": "shadow"})
        service = PairedReasoningValidationService(registry, lambda _account, _symbols: [snapshot],
                                                  lambda event: events.append(event) or {"saved": True},
                                                  lambda: "2026-10-18T00:01:00Z")
        first = service.request(["NVDA"], limit=1)
        second = service.request(["NVDA"], limit=1)
        self.assertEqual("requested", first["status"])
        self.assertEqual(first["requests"][0]["eventId"], second["requests"][0]["eventId"])
        request = independent_reasoning_request("baseline", [events[0]])
        self.assertTrue(request.context["validationOnly"])
        self.assertEqual(("NVDA",), request.symbols)
        self.assertEqual("2026-10-18T00:00:00Z", request.source_observed_at)
        self.assertFalse(request.supersedable)
        from digital_twin.platform.domain.events import domain_event_storage_payload
        from digital_twin.shared_kernel.events import DomainEvent
        durable = DomainEvent.from_dict({**events[0].to_dict(), "payload": domain_event_storage_payload(events[0].name, events[0].payload)})
        repaired_request = independent_reasoning_request("baseline", [durable])
        self.assertTrue(repaired_request.context["validationOnly"])
        self.assertFalse(repaired_request.supersedable)
        self.assertEqual(request.account_ids, repaired_request.account_ids)
        self.assertEqual(events[0].payload["validationPair"], durable.payload["validationPair"])
        self.assertEqual("unavailable-source", service.request(["UNKNOWN"])["status"])
        blocked = PairedReasoningValidationService(registry, lambda *_args: [snapshot],
                                                  lambda _event: {"status": "control-pair-changed"}, service.now_provider)
        self.assertEqual("blocked", blocked.request(["NVDA"])["status"])
