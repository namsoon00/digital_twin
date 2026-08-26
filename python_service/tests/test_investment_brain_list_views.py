import unittest

from digital_twin.application.investment_brain_service import InvestmentBrainService


class DecisionStore:
    def __init__(self):
        self.full_list_calls = 0

    def list_summaries(self, account_id="", symbol="", limit=50):
        return [{"episodeId": "episode:1", "symbol": "005930", "detailRequired": True}]

    def list(self, account_id="", symbol="", limit=50):
        self.full_list_calls += 1
        return []

    def get(self, episode_id):
        return None


class ResearchStore:
    def __init__(self):
        self.full_list_calls = 0

    def list_run_summaries(self, account_id="", symbol="", limit=50):
        return [{"runId": "run:1", "symbol": "005930", "detailRequired": True}]

    def list_runs(self, account_id="", symbol="", limit=50):
        self.full_list_calls += 1
        return []

    def get_run(self, run_id):
        return {"runId": run_id, "verifiedClaims": [{"claimId": "claim:1"}]}


class LifecycleStore:
    def __init__(self):
        self.event_calls = 0

    def list_current_summary(self, **_kwargs):
        return [{"lifecycleKey": "lifecycle:1", "symbol": "005930"}]

    def list_events(self, **_kwargs):
        self.event_calls += 1
        return []


class InvestmentBrainListViewTests(unittest.TestCase):
    def service(self):
        decisions = DecisionStore()
        research = ResearchStore()
        lifecycles = LifecycleStore()
        service = InvestmentBrainService(
            monitor_store=None,
            ontology_repository=None,
            reviewer=None,
            decision_episode_store=decisions,
            research_store=research,
            hypothesis_lifecycle_store=lifecycles,
        )
        return service, decisions, research, lifecycles

    def test_summary_lists_use_projected_rows_without_full_payload_hydration(self):
        service, decisions, research, lifecycles = self.service()

        episodes = service.episodes(view="summary")
        runs = service.research_runs(view="summary")
        lifecycle = service.hypothesis_lifecycles(view="summary")

        self.assertEqual("summary", episodes["view"])
        self.assertEqual("summary", runs["view"])
        self.assertEqual("summary", lifecycle["view"])
        self.assertEqual(0, decisions.full_list_calls)
        self.assertEqual(0, research.full_list_calls)
        self.assertEqual(0, lifecycles.event_calls)

    def test_research_run_detail_retains_complete_claim_payload(self):
        service, _decisions, _research, _lifecycles = self.service()

        payload = service.research_run_detail("run:1")

        self.assertEqual("ok", payload["status"])
        self.assertEqual("claim:1", payload["run"]["verifiedClaims"][0]["claimId"])


if __name__ == "__main__":
    unittest.main()
