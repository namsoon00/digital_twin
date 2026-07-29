import json
import unittest

from digital_twin.domain.events import (
    DomainEvent,
    domain_event_storage_payload,
    ontology_reasoning_requested_event,
    research_evidence_collected_event,
)


class DomainEventStoragePayloadTests(unittest.TestCase):
    def large_delta(self):
        return {
            "evidenceId": "evidence:TSLA:1",
            "symbol": "TSLA",
            "transition": "modified",
            "previousLifecycleState": "active",
            "lifecycleState": "active",
            "previousEligible": True,
            "eligible": True,
            "previousSignature": "previous-signature-" * 1600,
            "signature": "current-signature-" * 1600,
            "occurredAt": "2026-07-30T00:00:00Z",
            "reason": "article body enriched",
            "changesInferenceEligibleSet": True,
            "eligibleEvidenceSetRevision": "revision:TSLA:1",
            "factFamilies": ["evidence"],
        }

    def large_research_item(self):
        return {
            "evidenceId": "evidence:TSLA:1",
            "symbol": "TSLA",
            "kind": "news",
            "source": "Example News",
            "title": "Tesla update",
            "summary": "summary",
            "relationScope": "investable",
            "payload": {
                "articleText": "raw article body " * 10000,
                "claimLedger": {"claims": ["raw claim " * 4000]},
            },
            "aiAnalysis": {
                "summary": "analysis summary",
                "impactReasonKo": "impact reason",
            },
        }

    def test_research_event_storage_omits_raw_article_and_signature_bodies(self):
        event = research_evidence_collected_event({
            "source": "news",
            "changedCount": 1,
            "symbols": ["TSLA"],
            "changedItems": [self.large_research_item()],
            "materialChangedItems": [self.large_research_item()],
            "evidenceDeltas": [self.large_delta()],
            "factRevisionsBySymbol": {"TSLA": "revision:TSLA:1"},
        })

        stored = domain_event_storage_payload(event.name, event.payload)
        encoded = json.dumps(stored, ensure_ascii=False).encode("utf-8")
        item = stored["changedItems"][0]
        delta = stored["evidenceDeltas"][0]

        self.assertNotIn("payload", item)
        self.assertNotIn("articleText", json.dumps(item, ensure_ascii=False))
        self.assertNotIn("signature", delta)
        self.assertNotIn("previousSignature", delta)
        self.assertTrue(delta["signatureDigest"])
        self.assertTrue(delta["previousSignatureDigest"])
        self.assertLess(len(encoded), 20000)

    def test_reasoning_mailbox_contract_uses_compact_evidence_delta(self):
        request = ontology_reasoning_requested_event(
            DomainEvent(name="research_evidence.collected", aggregate_id="news:TSLA", payload={}),
            "research-evidence-update",
            ["TSLA"],
            changed_count=1,
            fact_types=["ResearchEvidence"],
            evidence_deltas=[self.large_delta()],
        )

        stored = domain_event_storage_payload(request.name, request.payload)
        encoded = json.dumps(stored, ensure_ascii=False).encode("utf-8")
        delta = stored["evidenceDeltas"][0]

        self.assertNotIn("signature", delta)
        self.assertNotIn("previousSignature", delta)
        self.assertTrue(delta["signatureDigest"])
        self.assertLess(len(encoded), 10000)


if __name__ == "__main__":
    unittest.main()
