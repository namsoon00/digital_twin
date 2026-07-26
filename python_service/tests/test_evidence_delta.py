import unittest

from digital_twin.domain.evidence_delta import (
    evidence_delta,
    eligible_evidence_set_revision,
)
from digital_twin.domain.events import (
    ONTOLOGY_REASONING_REQUESTED,
    RESEARCH_EVIDENCE_LIFECYCLE_CHANGED,
    research_evidence_lifecycle_events,
)
from digital_twin.domain.investment_research import ResearchEvidence


def eligible_evidence(evidence_id="evidence:1", title="실적 전망 상향"):
    return ResearchEvidence(
        evidence_id,
        "005930",
        "news",
        "Reuters",
        title,
        "실적 전망과 수요가 개선됐다는 본문 확인 기사입니다.",
        "https://example.test/" + evidence_id,
        "2026-07-26T00:00:00Z",
        "support",
        published_at="2026-07-26T00:00:00Z",
        raw_payload={
            "relationScope": "direct",
            "articleReadStatus": "body",
            "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
            "evidenceGovernance": {"investmentJudgmentEligible": True, "dataState": "sufficient"},
        },
    )


def ineligible_evidence():
    return ResearchEvidence(
        "evidence:weak",
        "005930",
        "news",
        "Blog",
        "단순 언급 기사",
        "본문이 없는 시장 주변 기사입니다.",
        "https://example.test/weak",
        "2026-07-26T00:00:00Z",
        "context",
        published_at="2026-07-26T00:00:00Z",
        raw_payload={"relationScope": "sector", "articleReadStatus": "feed-summary"},
    )


class EvidenceDeltaTests(unittest.TestCase):
    def test_eligible_addition_and_expiration_change_the_inference_fact_set(self):
        evidence = eligible_evidence()

        added = evidence_delta(None, evidence, lifecycle_state="active")
        expired = evidence_delta(
            evidence,
            evidence,
            previous_lifecycle_state="active",
            lifecycle_state="expired",
            transition="expiration",
        )

        self.assertEqual("added", added.transition)
        self.assertTrue(added.eligible)
        self.assertTrue(added.changes_inference_eligible_set)
        self.assertEqual("expiration", expired.transition)
        self.assertTrue(expired.previous_eligible)
        self.assertFalse(expired.eligible)
        self.assertTrue(expired.changes_inference_eligible_set)

    def test_ineligible_audit_evidence_does_not_request_inference(self):
        delta = evidence_delta(None, ineligible_evidence(), lifecycle_state="active")

        self.assertFalse(delta.eligible)
        self.assertFalse(delta.changes_inference_eligible_set)

    def test_eligible_set_revision_is_stable_for_order_only_changes(self):
        first = eligible_evidence("evidence:first")
        second = eligible_evidence("evidence:second")
        first_delta = evidence_delta(None, first, lifecycle_state="active")
        second_delta = evidence_delta(None, second, lifecycle_state="active")

        first_revision = eligible_evidence_set_revision(
            "005930",
            [first_delta.signature, second_delta.signature],
        )
        second_revision = eligible_evidence_set_revision(
            "005930",
            [second_delta.signature, first_delta.signature],
        )

        self.assertEqual(first_revision, second_revision)

    def test_lifecycle_event_requests_reasoning_only_for_eligible_set_changes(self):
        eligible = eligible_evidence()
        expired = evidence_delta(
            eligible,
            eligible,
            previous_lifecycle_state="active",
            lifecycle_state="expired",
            transition="expiration",
        )
        revision = eligible_evidence_set_revision("005930", [])
        events = research_evidence_lifecycle_events({
            "expiredCount": 1,
            "changedSymbols": ["005930"],
            "inferenceChangedSymbols": ["005930"],
            "evidenceDeltas": [expired.with_eligible_set_revision(revision).to_dict()],
            "factRevisionsBySymbol": {"005930": revision},
        })

        self.assertEqual(
            [RESEARCH_EVIDENCE_LIFECYCLE_CHANGED, ONTOLOGY_REASONING_REQUESTED],
            [event.name for event in events],
        )
        self.assertEqual({"005930": revision}, events[1].payload["factRevisionsBySymbol"])
        self.assertEqual("expiration", events[1].payload["evidenceDeltas"][0]["transition"])

        no_inference_events = research_evidence_lifecycle_events({
            "retractedCount": 1,
            "changedSymbols": ["005930"],
            "inferenceChangedSymbols": [],
        })
        self.assertEqual([RESEARCH_EVIDENCE_LIFECYCLE_CHANGED], [event.name for event in no_inference_events])
        self.assertEqual(["005930"], no_inference_events[0].payload["symbols"])
        self.assertEqual([], no_inference_events[0].payload["inferenceChangedSymbols"])


if __name__ == "__main__":
    unittest.main()
