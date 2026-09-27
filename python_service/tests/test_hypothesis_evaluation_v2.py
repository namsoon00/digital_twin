"""Regression cases from the hypothesis audit; no production data or writes."""

import json
import unittest
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from digital_twin.modules.outcomes.domain.observation_independence import select_independent_observations
from digital_twin.modules.outcomes.domain.decision_performance import claim_revision_metrics, investment_insight_performance_observations
from digital_twin.modules.outcomes.contracts import claim_validation_fingerprint
from digital_twin.modules.decisions.domain.investment_brain import hypothesis_templates_from_rulebox_snapshot
from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.infrastructure.transactions.decision_history_parts.performance import performance_episodes


def observation(index=0, minutes=1440, start=None, event=None):
    anchor = start or datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)
    return {"episodeId": "episode-" + str(index), "accountId": "synthetic", "symbol": "TEST",
            "claimFingerprint": "claim-v1", "frozenClaimFingerprint": "claim-v1",
            "startedAt": anchor.isoformat(), "observedAt": (anchor + timedelta(minutes=minutes)).isoformat(),
            "horizonMinutes": minutes, "independentEpisodeKey": event or "event-" + str(index),
            "calibrationEligible": True, "decisive": True, "corroborated": True,
            "actionAdjustedReturnPct": 1, "rawReturnPct": 1}


class HypothesisEvaluationV2Tests(unittest.TestCase):
    def test_performance_http_read_never_bootstraps_or_runs_retention(self):
        from unittest.mock import Mock
        from digital_twin.infrastructure.web.routes.outcomes import OutcomesRoutes
        settings = {"_skipOperationalHistoryRetention":"1", "_skipOperationalSchemaBootstrap":"1"}
        service = SimpleNamespace(performance=Mock(return_value={"evaluationVersion":"hypothesis-evaluation-v2"}))
        factory = Mock(return_value=service)
        routes = OutcomesRoutes(build_investment_brain_service=factory, operational_read_settings=lambda:settings)
        request = SimpleNamespace(command="GET", send_payload=lambda status,payload:(status,payload))
        status, payload = routes.route_investment_brain_performance(request, "/api/investment-brain/performance", {"limit":["500"]})
        factory.assert_called_once_with(settings=settings)
        self.assertEqual(200, status)
        self.assertEqual("hypothesis-evaluation-v2", payload["evaluationVersion"])

    def test_screen_report_is_bounded_and_does_not_build_research_or_scan_population(self):
        from unittest.mock import Mock
        from digital_twin.infrastructure.web.routes.outcomes import OutcomesRoutes
        from digital_twin.modules.outcomes.public import HypothesisPerformanceReportService
        store = SimpleNamespace(performance_episodes=Mock(return_value=[]))
        factory = Mock(return_value=HypothesisPerformanceReportService(store))
        heavy = Mock(side_effect=AssertionError("research service must not initialize"))
        routes = OutcomesRoutes(build_investment_brain_service=heavy,
            build_hypothesis_performance_report_service=factory, operational_read_settings=lambda:{"_skipOperationalSchemaBootstrap":"1"})
        request = SimpleNamespace(command="GET", send_payload=lambda status,payload:payload)
        report = routes.route_investment_brain_performance(request, "/api/investment-brain/performance", {"sampleOnly":["1"], "limit":["10000"]})
        store.performance_episodes.assert_called_once_with(account_id="", symbol="", limit=500)
        self.assertEqual("not-measured", report["populationCoverageState"])
        self.assertEqual("hypothesis-evaluation-v2", report["evaluationVersion"])
        self.assertEqual(0, report["assistantQuality"]["labelledEpisodeCount"])

    def test_bucket_boundary_does_not_make_overlapping_predictions_independent(self):
        anchor = datetime(2026, 1, 1, 0, 59, tzinfo=timezone.utc)
        a, b = observation(0, start=anchor), observation(1, start=anchor + timedelta(minutes=1))
        result = select_independent_observations([b, a], require_interval=True)
        self.assertEqual([a], result["selected"])
        self.assertEqual("overlapping-outcome-window", result["excluded"][0]["reason"])

    def test_first_prediction_survives_a_better_later_prediction(self):
        a, b = observation(0, event="one-event"), observation(1, event="one-event")
        a.update(calibrationEligible=False, corroborated=False)
        result = select_independent_observations([b, a], require_interval=True)
        self.assertEqual([a], result["selected"])
        self.assertEqual(0, claim_revision_metrics([b, a])[0]["decisiveOutcomeCount"])

    def test_each_horizon_is_preserved_without_counting_it_as_the_same_trial(self):
        a, b = observation(minutes=60), observation(minutes=1440)
        result = select_independent_observations([a, b], require_interval=True)
        self.assertEqual({60, 1440}, {r["horizonMinutes"] for r in result["selected"]})
        self.assertEqual(2, len(claim_revision_metrics([a, b])))

    def test_missing_original_clock_and_changed_revision_cannot_qualify(self):
        a, b = observation(), observation(1)
        a.pop("startedAt")
        b["frozenClaimFingerprint"] = "other-revision"
        result = claim_revision_metrics([a, b])[0]
        self.assertEqual(0, result["decisiveOutcomeCount"])
        self.assertEqual({"unverified-observation-interval", "claim-revision-unverified"}, {r["reason"] for r in result["exclusions"]})

    def test_qualification_is_exact_revision_and_subject_scoped(self):
        rule = next(r for r in default_graph_inference_rules() if r.enabled and r.resolved_claim_contract.is_predictive)
        fingerprint = claim_validation_fingerprint(rule.resolved_claim_contract.to_dict())
        horizon = min(rule.resolved_claim_contract.outcome_contract.to_dict()["outcomeHorizonMinutes"])
        samples = [observation(i, minutes=horizon, start=datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i*horizon)) for i in range(20)]
        for row in samples:
            row.update(claimFingerprint=fingerprint, frozenClaimFingerprint=fingerprint)
        performance = {"byClaimRevision": claim_revision_metrics(samples)}
        original = hypothesis_templates_from_rulebox_snapshot({"rules": [rule.to_dict()]}, performance)[0]
        self.assertEqual("active", original["qualification"]["status"])
        changed = replace(rule, conditions=[replace(rule.conditions[0], value=123), *rule.conditions[1:]])
        revised = hypothesis_templates_from_rulebox_snapshot({"rules": [changed.to_dict()]}, performance)[0]
        self.assertEqual("shadow", revised["qualification"]["status"])
        self.assertEqual("TEST", original["qualification"]["qualificationScopes"][0]["symbol"])

    def test_sql_projection_preserves_original_ai_assessment(self):
        assessment = {"publishable": True, "direction": "positive", "conviction": "moderate"}
        row = {"episode_id": "e", "account_id": "a", "symbol": "TEST", "action": "HOLD",
               "selected_hypothesis_id": "h", "decided_at": "2026-01-01T00:00:00Z",
               "ai_judgment_json": json.dumps({"insightAssessment": assessment}),
               "hypotheses_json": json.dumps([{"hypothesisId": "h"}]),
               "outcome_json": json.dumps({"observedAt": "2026-01-02T00:00:00Z", "priceChangeFromDecisionPct": 1,
                                           "payload": {"selectedHypothesisId": "h", "horizonMinutes": 1440}})}
        queries = []
        def execute(sql, args):
            queries.append(sql)
            return SimpleNamespace(fetchall=lambda: [row])
        loaded = performance_episodes(_connect=lambda: nullcontext(SimpleNamespace(execute=execute)),
                                      _runtime_settings={}, _shadow_performance_episodes=lambda **kwargs: [])
        self.assertIn("$.factsAtDecision.aiJudgment", queries[0])
        self.assertEqual(assessment, loaded[0]["factsAtDecision"]["aiJudgment"]["insightAssessment"])
        self.assertEqual(1, len(investment_insight_performance_observations(loaded)))


if __name__ == "__main__":
    unittest.main()
