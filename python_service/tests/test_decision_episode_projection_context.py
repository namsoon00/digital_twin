import unittest

from digital_twin.domain.investment_brain import (
    DecisionEpisode,
    decision_episode_ontology_context,
)
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder


def episode(symbol: str, episode_id: str, decided_at: str) -> DecisionEpisode:
    return DecisionEpisode.from_dict({
        "episodeId": episode_id,
        "accountId": "account-1",
        "symbol": symbol,
        "subjectName": symbol + " Holdings",
        "question": {
            "questionId": "question-" + episode_id,
            "text": symbol + "을 계속 보유해야 하나?",
            "subjectSymbol": symbol,
            "subjectName": symbol + " Holdings",
            "accountId": "account-1",
        },
        "hypothesisSet": {
            "hypothesisSetId": "set-" + episode_id,
            "subjectSymbol": symbol,
            "questionId": "question-" + episode_id,
            "hypotheses": [
                {
                    "hypothesisId": "support-" + episode_id,
                    "templateId": "template:support",
                    "templateLabel": "회복 확인",
                    "claim": "가격 회복 근거가 유지됩니다.",
                    "stance": "support",
                    "horizon": "short",
                    "evidenceState": "supported",
                    "evidenceStateLabel": "현재 근거로 확인됨",
                    "supportingEvidenceIds": ["evidence:price"],
                    "supportingRuleIds": ["graph.price.recovery"],
                },
                {
                    "hypothesisId": "risk-" + episode_id,
                    "templateId": "template:risk",
                    "templateLabel": "손실 방어",
                    "claim": "하락 위험을 다시 확인합니다.",
                    "stance": "risk",
                    "horizon": "short",
                    "evidenceState": "contested",
                    "evidenceStateLabel": "반대 근거가 함께 있음",
                    "counterEvidenceIds": ["evidence:flow"],
                    "supportingRuleIds": ["graph.loss_guard.breakdown"],
                },
            ],
        },
        "action": "HOLD",
        "selectedHypothesisId": "risk-" + episode_id,
        "decidedAt": decided_at,
        "factsAtDecision": {
            "rawPrompt": "never project this complete historic prompt",
            "largeSourcePayload": "x" * 100000,
            "hypothesisOutcomeContract": {
                "outcomeHorizonMinutes": [60, 1440],
                "requiredObservationDomains": ["quote", "trend"],
                "maximumObservationDelayMinutes": 180,
                "sourceRuleIds": ["graph.loss_guard.breakdown"],
            },
        },
        "researchPlan": {
            "fullResearchPrompt": "not part of live ABox memory",
        },
        "researchAudit": {
            "rawArticleBody": "not part of live ABox memory",
        },
        "outcomes": [
            {
                "outcomeId": "outcome-old-" + episode_id,
                "episodeId": episode_id,
                "observedAt": "2026-07-20T01:00:00Z",
                "selectedHypothesisStatus": "inconclusive",
                "payload": {"horizonMinutes": 60, "calibrationEligibility": "eligible"},
            },
            {
                "outcomeId": "outcome-new-" + episode_id,
                "episodeId": episode_id,
                "observedAt": "2026-07-21T01:00:00Z",
                "selectedHypothesisStatus": "directionally-corroborated",
                "payload": {"horizonMinutes": 1440, "calibrationEligibility": "eligible"},
            },
        ],
    })


class DecisionEpisodeProjectionContextTests(unittest.TestCase):
    def test_compaction_does_not_serialize_full_historical_episode(self):
        stored = episode("AAPL", "episode-1", "2026-07-20T00:00:00Z")

        def forbidden_full_serialization():
            raise AssertionError("live ABox projection must not call DecisionEpisode.to_dict")

        stored.to_dict = forbidden_full_serialization
        context = decision_episode_ontology_context(
            stored,
            maximum_hypotheses=1,
            maximum_outcomes=1,
        )

        self.assertEqual("decision-episode-ontology-context-v1", context["contextVersion"])
        self.assertEqual(["risk-episode-1"], [
            item["hypothesisId"] for item in context["hypothesisSet"]["hypotheses"]
        ])
        self.assertEqual("outcome-new-episode-1", context["outcomes"][0]["outcomeId"])
        self.assertEqual(
            ["quote", "trend"],
            context["factsAtDecision"]["hypothesisOutcomeContract"]["requiredObservationDomains"],
        )
        self.assertEqual({}, context["researchPlan"])
        self.assertEqual({}, context["researchAudit"])
        self.assertNotIn("largeSourcePayload", str(context))
        self.assertNotIn("fullResearchPrompt", str(context))

    def test_targeted_projection_reads_only_recent_episodes_for_target_symbol(self):
        latest = episode("AAPL", "episode-aapl-new", "2026-07-21T00:00:00Z")
        older = episode("AAPL", "episode-aapl-old", "2026-07-20T00:00:00Z")
        other = episode("NVDA", "episode-nvda", "2026-07-22T00:00:00Z")
        calls = []

        class Store:
            def list_for_symbols(self, symbols, account_id="", limit_per_symbol=20):
                calls.append({
                    "symbols": list(symbols),
                    "accountId": account_id,
                    "limitPerSymbol": limit_per_symbol,
                })
                return [latest, older, other]

        snapshot = AccountSnapshot(
            "account-1",
            "계정",
            "toss",
            "live",
            "ok",
            "2026-07-22T00:00:00Z",
            PortfolioSummary(total=1000, invested=1000, cash=0, markets=[], sectors=[], concentration=0),
            positions=[Position("AAPL", "Apple", market="US", currency="USD", quantity=1)],
        )
        recorder = PortfolioOntologyProjectionRecorder(
            None,
            decision_episode_store=Store(),
            settings={
                "ontologyDecisionEpisodeContextPerSymbolLimit": "2",
                "ontologyDecisionEpisodeContextMaxEpisodes": "1",
            },
        )

        context = recorder.decision_episode_projection_context(snapshot, target_symbols=["AAPL"])

        self.assertEqual(["AAPL"], calls[0]["symbols"])
        self.assertEqual("account-1", calls[0]["accountId"])
        self.assertEqual(2, calls[0]["limitPerSymbol"])
        self.assertEqual(1, context["projection"]["includedEpisodeCount"])
        self.assertEqual(1, context["projection"]["droppedEpisodeCount"])
        self.assertEqual(["episode-aapl-new"], [item["episodeId"] for item in context["episodes"]])

    def test_removed_target_does_not_fall_back_to_a_full_account_projection(self):
        snapshot = AccountSnapshot(
            "account-1",
            "계정",
            "toss",
            "live",
            "ok",
            "2026-07-22T00:00:00Z",
            PortfolioSummary(total=1000, invested=1000, cash=0, markets=[], sectors=[], concentration=0),
            positions=[Position("AAPL", "Apple", market="US", currency="USD", quantity=1)],
        )
        recorder = PortfolioOntologyProjectionRecorder(object())

        result = recorder.record_snapshot(snapshot, target_symbols=["NVDA"])

        self.assertEqual("skipped-inactive-target-symbols", result["status"])
        self.assertEqual(["NVDA"], result["targetSymbols"])
        self.assertIn("AAPL", result["availableSymbols"])


if __name__ == "__main__":
    unittest.main()
