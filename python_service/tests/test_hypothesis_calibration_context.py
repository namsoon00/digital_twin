import unittest

from digital_twin.application.investment_brain_service import InvestmentBrainService
from digital_twin.domain.hypothesis_calibration import (
    attach_abox_hypothesis_calibrations,
    hypothesis_calibration_snapshot_from_abox_rows,
)
from digital_twin.domain.investment_brain import DecisionEpisode
from digital_twin.domain.notification_ai import notification_ai_prompt_context
from digital_twin.domain.notification_ai_gate_validation import build_notification_ai_gate_prompt
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.portfolio_ontology_cognitive_concepts import add_investment_brain_concepts


def candidate_brain():
    return {
        "hypothesisSet": {
            "hypothesisSetId": "set-1",
            "subjectSymbol": "005930",
            "hypotheses": [
                {
                    "hypothesisId": "hypothesis-risk",
                    "templateId": "hypothesis-template:risk-rule",
                    "templateLabel": "Risk rule",
                    "claim": "Risk evidence remains active.",
                    "stance": "risk",
                },
                {
                    "hypothesisId": "hypothesis-support",
                    "templateId": "hypothesis-template:support-rule",
                    "templateLabel": "Support rule",
                    "claim": "Support evidence remains active.",
                    "stance": "support",
                },
            ],
        },
    }


def calibration_row(
    symbol="005930",
    template_id="hypothesis-template:risk-rule",
    latest_observed_at="2026-07-20T01:00:00Z",
    abox_snapshot_id="abox-1",
    outcome_state="more-contradicted",
):
    return {
        "id": "hypothesis-calibration:" + symbol + ":" + template_id,
        "kind": "hypothesis-calibration",
        "tboxClass": "HypothesisCalibration",
        "symbol": symbol,
        "templateId": template_id,
        "templateLabel": "Risk rule",
        "calibrationStatus": "usable",
        "outcomeState": outcome_state,
        "reviewRecommendation": "review-for-revision",
        "minimumDecisiveOutcomes": 3,
        "independentEpisodeCount": 4,
        "decisiveOutcomeCount": 4,
        "corroboratedCount": 1,
        "contradictedCount": 3,
        "inconclusiveCount": 0,
        "latestObservedAt": latest_observed_at,
        "outcomeHorizonMinutes": [60, 1440],
        "horizonSlices": [
            {
                "horizonMinutes": 60,
                "independentEpisodeCount": 3,
                "decisiveOutcomeCount": 3,
                "corroboratedCount": 1,
                "contradictedCount": 2,
                "inconclusiveCount": 0,
                "outcomeState": "more-contradicted",
                "calibrationStatus": "usable",
            },
        ],
        "aboxSnapshotId": abox_snapshot_id,
        "source": "investment-brain-feedback",
    }


class HypothesisCalibrationContextTests(unittest.TestCase):
    def snapshot(self, rows):
        return hypothesis_calibration_snapshot_from_abox_rows(
            rows,
            symbols=["005930"],
            source_abox_snapshot_id="abox-1",
            generation_aligned=True,
        )

    def test_snapshot_keeps_only_current_symbol_and_abox_generation(self):
        snapshot = self.snapshot([
            calibration_row(),
            calibration_row(symbol="000660"),
            calibration_row(template_id="hypothesis-template:stale-rule", abox_snapshot_id="abox-old"),
        ])

        self.assertEqual("ok", snapshot["status"])
        self.assertEqual(1, snapshot["calibrationCount"])
        self.assertEqual("005930", snapshot["calibrations"][0]["symbol"])
        self.assertEqual("hypothesis-template:risk-rule", snapshot["calibrations"][0]["templateId"])
        self.assertFalse(snapshot["automaticDeployment"])
        self.assertEqual("historical-review-only", snapshot["decisionEligibility"])

    def test_active_membership_can_validate_reused_scoped_fact(self):
        snapshot = hypothesis_calibration_snapshot_from_abox_rows(
            [calibration_row(abox_snapshot_id="older-scope-manifest")],
            symbols=["005930"],
            source_abox_snapshot_id="active-worldview-manifest",
            generation_aligned=True,
            active_membership_verified=True,
        )

        self.assertEqual("ok", snapshot["status"])
        calibration = snapshot["calibrations"][0]
        self.assertEqual("active-worldview-manifest", calibration["sourceAboxSnapshotId"])
        self.assertEqual("older-scope-manifest", calibration["storedAboxSnapshotId"])
        self.assertTrue(calibration["activeAboxMembershipValidated"])

    def test_attaches_only_exact_candidate_and_persists_audit_context(self):
        snapshot = self.snapshot([calibration_row()])
        attached = attach_abox_hypothesis_calibrations(
            candidate_brain(),
            snapshot,
            subject_symbol="005930",
            inference_generation_id="generation-1",
            inference_generation_at="2026-07-20T02:00:00Z",
            source_abox_snapshot_id="abox-1",
            generation_aligned=True,
        )

        hypotheses = attached["hypothesisSet"]["hypotheses"]
        risk = next(item for item in hypotheses if item["hypothesisId"] == "hypothesis-risk")
        support = next(item for item in hypotheses if item["hypothesisId"] == "hypothesis-support")
        self.assertEqual("applied", attached["hypothesisCalibration"]["status"])
        self.assertEqual("more-contradicted", risk["historicalCalibration"]["outcomeState"])
        self.assertNotIn("historicalCalibration", support)

        episode = DecisionEpisode.from_dict({
            "episodeId": "episode-1",
            "accountId": "account-1",
            "symbol": "005930",
            "subjectName": "Samsung Electronics",
            "question": {},
            "hypothesisSet": attached["hypothesisSet"],
            "action": "HOLD",
        })
        stored_risk = next(item for item in episode.hypothesis_set.hypotheses if item.hypothesis_id == "hypothesis-risk")
        self.assertEqual("more-contradicted", stored_risk.historical_calibration["outcomeState"])

    def test_excludes_future_outcome_from_current_generation(self):
        snapshot = self.snapshot([
            calibration_row(latest_observed_at="2026-07-20T03:00:01Z"),
        ])
        attached = attach_abox_hypothesis_calibrations(
            candidate_brain(),
            snapshot,
            subject_symbol="005930",
            inference_generation_id="generation-1",
            inference_generation_at="2026-07-20T03:00:00Z",
            source_abox_snapshot_id="abox-1",
            generation_aligned=True,
        )

        self.assertEqual("no-exact-history", attached["hypothesisCalibration"]["status"])
        self.assertNotIn("historicalCalibration", attached["hypothesisSet"]["hypotheses"][0])

    def test_application_boundary_and_ai_prompt_receive_review_only_context(self):
        snapshot = self.snapshot([calibration_row()])
        relation_context = {
            "subject": {"symbol": "005930", "name": "Samsung Electronics"},
            "facts": {"symbol": "005930"},
            "inferenceGenerationId": "generation-1",
            "inferenceGenerationAt": "2026-07-20T02:00:00Z",
            "sourceAboxSnapshotId": "abox-1",
            "generationAligned": True,
            "hypothesisCalibration": snapshot,
        }
        service = InvestmentBrainService(None, None, None, None)
        brain = service.brain_with_reasoning_generation(candidate_brain(), relation_context)
        relation_context.update({
            "investmentBrain": brain,
            "hypothesisSet": brain["hypothesisSet"],
            "hypothesisCalibration": brain["hypothesisCalibration"],
        })
        context = {
            "messageType": "investmentInsight",
            "displayTarget": "Samsung Electronics",
            "ontologyRelationContext": relation_context,
        }
        prompt_context = notification_ai_prompt_context("investmentInsight", context)
        prompt = build_notification_ai_gate_prompt(context)

        self.assertEqual("applied", prompt_context["facts"]["hypothesisCalibration"]["status"])
        self.assertIn("historicalCalibration", prompt)
        self.assertIn("사후 결과 집계", prompt)
        self.assertIn("more-contradicted", prompt)

    def test_application_boundary_reads_calibration_from_typedb_audit_context(self):
        snapshot = self.snapshot([calibration_row()])
        relation_context = {
            "subject": {"symbol": "005930", "name": "Samsung Electronics"},
            "facts": {"symbol": "005930"},
            "typedbInference": {
                "inferenceGenerationId": "generation-1",
                "inferenceGenerationAt": "2026-07-20T02:00:00Z",
                "sourceAboxSnapshotId": "abox-1",
                "generationAligned": True,
                "hypothesisCalibration": snapshot,
            },
        }
        service = InvestmentBrainService(None, None, None, None)
        brain = service.brain_with_reasoning_generation(candidate_brain(), relation_context)

        self.assertEqual("applied", brain["hypothesisCalibration"]["status"])
        risk = brain["hypothesisSet"]["hypotheses"][0]
        self.assertEqual("more-contradicted", risk["historicalCalibration"]["outcomeState"])

    def test_projection_keeps_same_template_calibration_separate_per_symbol(self):
        episodes = []
        for symbol, status in [
            ("005930", "directionally-contradicted"),
            ("005930", "directionally-contradicted"),
            ("005930", "directionally-corroborated"),
            ("000660", "directionally-corroborated"),
            ("000660", "directionally-corroborated"),
            ("000660", "directionally-corroborated"),
        ]:
            index = len(episodes)
            hypothesis = {
                "hypothesisId": "hypothesis-" + symbol + "-" + str(index),
                "templateId": "hypothesis-template:shared-rule",
                "templateLabel": "Shared rule",
            }
            episodes.append({
                "episodeId": "episode-" + str(index),
                "symbol": symbol,
                "subjectName": symbol,
                "selectedHypothesisId": hypothesis["hypothesisId"],
                "hypothesisSet": {"hypotheses": [hypothesis]},
                "outcomes": [{
                    "outcomeId": "outcome-" + str(index),
                    "observedAt": "2026-07-20T0" + str(index) + ":00:00Z",
                    "selectedHypothesisStatus": status,
                    "payload": {"calibrationEligibility": "eligible", "horizonMinutes": 60},
                }],
            })
        graph = PortfolioOntology("account-1")
        add_investment_brain_concepts(graph, "account-1", episodes)
        calibrations = {
            item.properties["symbol"]: item.properties
            for item in graph.entities
            if item.kind == "hypothesis-calibration"
        }

        self.assertEqual(2, len(calibrations))
        self.assertEqual("more-contradicted", calibrations["005930"]["outcomeState"])
        self.assertEqual("more-corroborated", calibrations["000660"]["outcomeState"])
        self.assertEqual([60], calibrations["005930"]["outcomeHorizonMinutes"])

    def test_projection_keeps_latest_result_per_episode_and_horizon(self):
        hypothesis = {
            "hypothesisId": "hypothesis-risk",
            "templateId": "hypothesis-template:risk-rule",
            "templateLabel": "Risk rule",
        }
        episodes = [
            {
                "episodeId": "episode-1",
                "symbol": "005930",
                "subjectName": "Samsung Electronics",
                "selectedHypothesisId": "hypothesis-risk",
                "hypothesisSet": {"hypotheses": [hypothesis]},
                "outcomes": [
                    {
                        "outcomeId": "outcome-1-short",
                        "observedAt": "2026-07-20T01:00:00Z",
                        "selectedHypothesisStatus": "directionally-corroborated",
                        "payload": {"calibrationEligibility": "eligible", "horizonMinutes": 60},
                    },
                    {
                        "outcomeId": "outcome-1-long",
                        "observedAt": "2026-07-21T01:00:00Z",
                        "selectedHypothesisStatus": "directionally-contradicted",
                        "payload": {"calibrationEligibility": "eligible", "horizonMinutes": 1440},
                    },
                ],
            },
            {
                "episodeId": "episode-2",
                "symbol": "005930",
                "subjectName": "Samsung Electronics",
                "selectedHypothesisId": "hypothesis-risk",
                "hypothesisSet": {"hypotheses": [hypothesis]},
                "outcomes": [
                    {
                        "outcomeId": "outcome-2-short",
                        "observedAt": "2026-07-20T02:00:00Z",
                        "selectedHypothesisStatus": "directionally-corroborated",
                        "payload": {"calibrationEligibility": "eligible", "horizonMinutes": 60},
                    },
                ],
            },
        ]
        graph = PortfolioOntology("account-1")
        add_investment_brain_concepts(graph, "account-1", episodes)
        calibration = next(item.properties for item in graph.entities if item.kind == "hypothesis-calibration")
        slices = {item["horizonMinutes"]: item for item in calibration["horizonSlices"]}

        self.assertEqual("insufficient-history", calibration["outcomeState"])
        self.assertEqual([60, 1440], calibration["outcomeHorizonMinutes"])
        self.assertEqual(2, slices[60]["corroboratedCount"])
        self.assertEqual(1, slices[1440]["contradictedCount"])


if __name__ == "__main__":
    unittest.main()
