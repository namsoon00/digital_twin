import unittest

from digital_twin.application.investment_strategy_proposal_service import (
    InvestmentStrategyProposalService,
)
from digital_twin.domain.investment_strategy_proposals import InvestmentStrategyProposal


class ProposalStore:
    def __init__(self, proposals):
        self.proposals = list(proposals)
        self.list_calls = 0
        self.status_count_calls = 0

    def list(self, limit=100):
        self.list_calls += 1
        return self.proposals[:limit]

    def status_counts(self):
        self.status_count_calls += 1
        return {"proposed": 1, "approved": 2}


class InvestmentStrategyProposalReadModelTests(unittest.TestCase):
    def setUp(self):
        self.proposal = InvestmentStrategyProposal(
            proposal_id="proposal-1",
            title="AI infrastructure leaders",
            thesis="Track durable infrastructure demand.",
            symbols=["NVDA"],
            entry_conditions=["revenue acceleration"],
            metadata={"auditPayload": "large-detail"},
            validation={"materialization": {"status": "ok", "matchedCount": 4}},
            performance={"summary": {"sampleCount": 3, "avgReturnPct": 2.1}},
            created_at="2026-08-20T00:00:00Z",
            updated_at="2026-08-20T01:00:00Z",
        )
        self.store = ProposalStore([self.proposal])
        self.service = InvestmentStrategyProposalService(self.store)

    def test_summary_list_omits_audit_sized_detail(self):
        payload = self.service.list(limit=20, detail="summary")

        self.assertEqual("summary", payload["detailLevel"])
        self.assertEqual(1, payload["count"])
        item = payload["proposals"][0]
        self.assertEqual("proposal-1", item["id"])
        self.assertEqual({"status": "ok", "matchedCount": 4}, item["validation"])
        self.assertEqual(3, item["performance"]["summary"]["sampleCount"])
        self.assertNotIn("metadata", item)
        self.assertNotIn("entryConditions", item)

    def test_full_list_keeps_detail_contract(self):
        payload = self.service.list(detail="full")

        self.assertEqual("full", payload["detailLevel"])
        self.assertEqual(["revenue acceleration"], payload["proposals"][0]["entryConditions"])
        self.assertEqual("large-detail", payload["proposals"][0]["metadata"]["auditPayload"])

    def test_status_uses_sql_counter_contract_without_hydrating_proposals(self):
        payload = self.service.status()

        self.assertEqual(3, payload["count"])
        self.assertEqual(1, self.store.status_count_calls)
        self.assertEqual(0, self.store.list_calls)


if __name__ == "__main__":
    unittest.main()
