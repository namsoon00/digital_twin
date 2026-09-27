"""Bounded screen report without research initialization or population scans."""
from ..domain.decision_performance import evaluate_decision_performance


class HypothesisPerformanceReportService:
    def __init__(self, decision_episode_store, settings=None):
        self.store = decision_episode_store
        self.settings = dict(settings or {})

    def performance(self, account_id="", symbol="", limit=500):
        rows = self.store.performance_episodes(account_id=account_id, symbol=symbol,
                                               limit=max(1, min(500, int(limit or 500))))
        try:
            minimum = int(float(self.settings.get("investmentBrainPerformanceMinimumSamples") or 5))
        except (TypeError, ValueError):
            minimum = 5
        result = evaluate_decision_performance(rows, minimum_sample_count=max(2, min(100, minimum)))
        return {**result, "engine":"ontology-investment-brain", "source":"DecisionEpisode+ObservedOutcome",
                "accountId":account_id, "symbol":str(symbol or "").upper(),
                "evaluatedEpisodeCount":len(rows), "historySelection":"outcome-led-bounded-history",
                "outcomeCoverageBasis":"bounded-sample-only", "populationCoverageState":"not-measured"}
