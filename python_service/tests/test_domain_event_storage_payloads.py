import json
import unittest

from digital_twin.domain.events import (
    DOMAIN_EVENT_SCHEMA_VERSION,
    DomainEvent,
    domain_event_storage_payload,
    ontology_reasoning_requested_event,
    research_evidence_collected_event,
    investment_decision_changed_event,
)


class DomainEventStoragePayloadTests(unittest.TestCase):
    def test_domain_event_schema_version_is_explicit_and_backward_compatible(self):
        event = DomainEvent(name="market_data.collected", aggregate_id="market:005930")

        self.assertEqual(DOMAIN_EVENT_SCHEMA_VERSION, event.to_dict()["schema_version"])
        self.assertEqual(
            DOMAIN_EVENT_SCHEMA_VERSION,
            DomainEvent.from_dict({"name": event.name, "aggregateId": event.aggregate_id}).schema_version,
        )
        self.assertEqual(
            "domain-event-v2",
            DomainEvent.from_dict({
                "name": event.name,
                "aggregate_id": event.aggregate_id,
                "schemaVersion": "domain-event-v2",
            }).schema_version,
        )

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
                "articleSummaryQuality": {"state": "ready", "issues": []},
                "newsEligibility": {
                    "displayEligible": True,
                    "alertEligible": True,
                    "reasoningEligible": False,
                    "reasoningReasonCodes": ["claim-governance-not-eligible"],
                },
                "promptEvidenceAdmission": {
                    "usage": "alert",
                    "alertEligible": True,
                    "promptEligible": False,
                    "reasonCodes": ["claim-governance-not-eligible"],
                },
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
        self.assertTrue(item["newsEligibility"]["alertEligible"])
        self.assertFalse(item["newsEligibility"]["reasoningEligible"])
        self.assertFalse(item["promptEvidenceAdmission"]["promptEligible"])
        self.assertNotIn("signature", delta)
        self.assertNotIn("previousSignature", delta)
        self.assertTrue(delta["signatureDigest"])
        self.assertTrue(delta["previousSignatureDigest"])
        self.assertLess(len(encoded), 20000)

    def test_research_event_storage_keeps_bounded_official_analysis(self):
        event = research_evidence_collected_event({
            "source": "official",
            "changedItems": [{
                "evidenceId": "research:005930:dart:1",
                "symbol": "005930",
                "kind": "disclosure",
                "title": "자기주식 취득 결정",
                "payload": {
                    "officialDocumentText": "공식 문서 원문 " * 1000,
                    "documentVerified": True,
                    "analysisReady": True,
                    "disclosureAnalysis": {
                        "summary": "회사가 자기주식 취득을 결의했습니다.",
                        "confirmedFacts": ["보통주 1,000,000주를 취득합니다."],
                        "materialNumbers": ["1,000,000주"],
                        "documentDates": ["2026-08-26"],
                        "watchItems": ["실제 취득 체결 내역"],
                    },
                },
            }],
        })

        stored = domain_event_storage_payload(event.name, event.payload)
        item = stored["changedItems"][0]

        self.assertNotIn("officialDocumentText", json.dumps(item, ensure_ascii=False))
        self.assertEqual(["2026-08-26"], item["disclosureAnalysis"]["documentDates"])
        self.assertEqual(["보통주 1,000,000주를 취득합니다."], item["disclosureAnalysis"]["confirmedFacts"])

    def test_research_event_storage_preserves_authoritative_empty_alert_set(self):
        event = research_evidence_collected_event({
            "changedItems": [self.large_research_item()],
            "materialChangedItems": [self.large_research_item()],
            "alertEligibleItems": [],
            "alertEligibleCount": 0,
        })

        stored = domain_event_storage_payload(event.name, event.payload)

        self.assertIn("alertEligibleItems", stored)
        self.assertEqual([], stored["alertEligibleItems"])
        self.assertEqual(0, stored["alertEligibleCount"])

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

    def test_reasoning_request_storage_keeps_fact_types_bound_to_each_symbol(self):
        request = ontology_reasoning_requested_event(
            DomainEvent(name="monitoring.snapshot_collected", aggregate_id="monitor:acct", payload={}),
            "verified-monitor-snapshot",
            ["AAPL", "MSFT"],
            changed_count=2,
            fact_types=["MarketQuote", "ResearchEvidence"],
            fact_types_by_symbol={
                "AAPL": ["ResearchEvidence"],
                "MSFT": ["MarketQuote"],
            },
        )

        stored = domain_event_storage_payload(request.name, request.payload)

        self.assertEqual(
            {"AAPL": ["ResearchEvidence"], "MSFT": ["MarketQuote"]},
            stored["factTypesBySymbol"],
        )

    def test_reasoning_request_keeps_complete_position_change_field_set(self):
        fields = [f"field_{index:02d}" for index in range(55)]
        request = ontology_reasoning_requested_event(
            DomainEvent(name="monitoring.snapshot_collected", aggregate_id="monitor:acct", payload={}),
            "verified-monitor-snapshot",
            ["AAPL"],
            changed_count=1,
            fact_types=["MarketQuote"],
            changed_fields_by_symbol={"AAPL": fields},
        )

        stored = domain_event_storage_payload(request.name, request.payload)

        self.assertEqual(fields, stored["changedFieldsBySymbol"]["AAPL"])

    def test_decision_change_event_carries_flow_and_state_transition(self):
        event = investment_decision_changed_event(
            {"action": "HOLD", "validationState": "conditional"},
            {
                "flowId": "flow:1",
                "episodeId": "episode:1",
                "accountId": "account-1",
                "symbol": "AAPL",
                "action": "BUY",
                "validationState": "ready",
                "inferenceGenerationId": "generation:1",
            },
        )

        self.assertEqual("investment.decision_changed", event.name)
        self.assertEqual("flow:1", event.correlation_id)
        self.assertEqual("HOLD", event.payload["previousAction"])
        self.assertEqual("BUY", event.payload["currentAction"])


if __name__ == "__main__":
    unittest.main()
