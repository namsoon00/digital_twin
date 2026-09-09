import copy
import unittest

from digital_twin.domain.investment_reasoning_detail import subject_reasoning_lineage
from digital_twin.domain.notification_ai_context_router import (
    fit_notification_ai_decision_core,
    route_notification_ai_decision_context,
)
from digital_twin.domain.notification_ai_decision_brief import (
    notification_ai_decision_brief,
)


RULE_ID = "graph.company.market.fundamental_confirmation.support.v1"
ABOX_ID = "abox-manifest:sk"
GENERATION_ID = "inference-generation:sk"


def rule_evaluation(symbol, observed_value):
    relation_id = f"ontology-assertion:{symbol}:model"
    return {
        "evaluationId": f"evaluation:{symbol}",
        "accountId": "default",
        "ruleId": RULE_ID,
        "sourceAboxSnapshotId": ABOX_ID,
        "inferenceGenerationId": GENERATION_ID,
        "matched": True,
        "selected": False,
        "decisionEligible": True,
        "proof": {
            "proofId": f"proof:{symbol}",
            "ruleId": RULE_ID,
            "traceId": f"inference-trace:{symbol}:fundamental",
            "subjectId": "",
            "evidenceIds": [relation_id],
            "conditions": [{
                "conditionId": "validated-model-signal:fundamental",
                "kind": "relation",
                "role": "required",
                "relationId": relation_id,
                "relationType": "HAS_MODEL_SIGNAL",
                "observedValue": observed_value,
                "result": "matched",
                "source": "statistical-signal-pipeline",
                "sourceAsOf": "2026-09-09T01:00:00Z",
                "freshnessStatus": "fresh",
                "targetId": f"model-signal:{symbol}",
                "targetKind": "ModelSignal",
                "sourceFactIds": [f"source-fact:{symbol}:price"],
                "matchedTargetProperties": {
                    "signalType": "fundamental-confirmation",
                    "strengthBand": "strong",
                    "releaseId": "valuation-statistics-production-v2",
                    "sourceFeatureSnapshotId": f"feature-snapshot:{symbol}",
                    "modelEvidenceIds": [f"model-evidence:{symbol}"],
                    "currentPrice": observed_value,
                    "ma20Distance": 8.6,
                },
                "judgementEvidenceUsable": True,
            }],
        },
    }


def subject_case():
    hypothesis = {
        "hypothesisId": "hypothesis:sk:fundamental",
        "templateLabel": "실적 개선과 가격 회복",
        "claim": "실적 개선과 가격 회복이 보유 논리를 지지한다.",
        "stance": "support",
        "candidateAction": "HOLD",
        "supportingRuleIds": [RULE_ID],
        "supportingEvidenceIds": ["ontology-assertion:000660:model"],
        "claimContract": {"claimContractId": "claim:fundamental-confirmation"},
        "qualification": {"status": "shadow", "decisionUse": "research"},
        "knowledgeBasis": {
            "decisionEligibility": "investment-evidence",
            "plainLanguageBasis": "실적 개선과 가격 회복이 동시에 확인됐다.",
        },
    }
    return {
        "subjectCaseId": "subject-decision-case:sk",
        "batchCaseId": "reasoning-case:batch",
        "accountId": "default",
        "symbol": "000660",
        "sourceAboxSnapshotId": ABOX_ID,
        "inferenceGenerationId": GENERATION_ID,
        "updatedAt": "2026-09-09T01:01:00Z",
        "candidateSet": {
            "candidateSetId": "candidate-set:sk",
            "fingerprint": "candidate-fingerprint:sk",
            "accountId": "default",
            "symbol": "000660",
            "sourceAboxSnapshotId": ABOX_ID,
            "inferenceGenerationId": GENERATION_ID,
            "eligibleHypothesisIds": ["hypothesis:sk:fundamental"],
            "executionEligibleHypothesisIds": [],
            "referenceHypothesisIds": [],
            "hypotheses": [hypothesis],
        },
        "synthesis": {
            "synthesisId": "decision-synthesis:sk",
            "accountId": "default",
            "symbol": "000660",
            "sourceAboxSnapshotId": ABOX_ID,
            "inferenceGenerationId": GENERATION_ID,
            "selectedRuleId": RULE_ID,
            "graphCandidateAction": "HOLD",
        },
    }


def reasoning_case():
    return {
        "caseId": "reasoning-case:batch",
        "deploymentId": "reasoning-deployment:production",
        "releaseFingerprint": "release:production",
        "releaseManifest": {
            "releaseId": "ontology-release:test",
            "tboxReleaseId": "tbox-release:test",
            "tboxFingerprint": "tbox-fingerprint:test",
            "ruleboxReleaseId": "rulebox-release:test",
            "ruleboxFingerprint": "rulebox-fingerprint:test",
            "modelSignalReleaseId": "model-signal-release:test",
            "promptReleaseId": "prompt-release:test",
        },
        "inferenceResult": {
            "ruleEvaluations": [
                rule_evaluation("000660", 1776000),
                rule_evaluation("005380", 291000),
            ],
        },
    }


def fallback_ai_episode():
    return {
        "episodeId": "ai-insight-episode:fallback",
        "subjectCaseId": "subject-decision-case:sk",
        "accountId": "default",
        "symbol": "000660",
        "sourceAboxSnapshotId": ABOX_ID,
        "inferenceGenerationId": GENERATION_ID,
        "candidateFingerprint": "candidate-fingerprint:sk",
        "publicationMode": "typedb-fallback",
        "aiAuthored": False,
        "publicationContractPassed": False,
        "insight": {
            "action": "HOLD",
            "summary": "AI 실행 실패로 TypeDB 결과를 보존했다.",
        },
    }


class SubjectReasoningLineageTests(unittest.TestCase):
    def test_batch_proof_is_subject_scoped_and_links_every_reasoning_layer(self):
        lineage = subject_reasoning_lineage(
            subject_case(),
            reasoning_case(),
            fallback_ai_episode(),
            [{
                "episodeId": "observation:1",
                "accountId": "default",
                "symbol": "000660",
                "hypothesisId": "hypothesis:old-generation",
                "claimContractId": "claim:fundamental-confirmation",
                "candidateSetId": "candidate-set:old",
                "sourceAboxSnapshotId": "abox-manifest:old",
                "inferenceGenerationId": "inference-generation:old",
                "status": "observed",
            }],
        )

        reasoning = lineage["reasoning"]
        self.assertEqual("000660", lineage["identity"]["symbol"])
        self.assertEqual("tbox-release:test", lineage["identity"]["tboxReleaseId"])
        self.assertEqual("rulebox-release:test", lineage["identity"]["ruleboxReleaseId"])
        self.assertEqual(1, lineage["integrity"]["includedRuleEvaluationCount"])
        self.assertEqual(1, lineage["integrity"]["excludedForeignSubjectEvaluationCount"])
        self.assertEqual(0, lineage["integrity"]["invalidRuleEvaluationCount"])
        self.assertEqual([RULE_ID], [row["id"] for row in reasoning["rules"]])
        self.assertTrue(reasoning["rules"][0]["selected"])
        self.assertEqual(1776000, reasoning["facts"][0]["observedValue"])
        self.assertNotIn("005380", str(lineage))
        self.assertEqual("typedb-fallback", lineage["ai"]["status"])
        self.assertEqual(1, lineage["scenarios"][0]["observationState"]["sampleCount"])
        self.assertEqual(
            ["fact", "relation", "rule", "hypothesis", "decision"],
            [node["layer"] for node in lineage["explanation"]["causalPaths"][0]["nodes"]],
        )

    def test_ai_core_receives_same_immutable_proof_and_rejects_wrong_subject(self):
        lineage = subject_reasoning_lineage(
            subject_case(),
            reasoning_case(),
            fallback_ai_episode(),
        )
        brief = {
            "subject": {"symbol": "000660", "name": "SK하이닉스", "market": "KR"},
            "decisionState": {"actionEnvelope": {"selectedRuleId": RULE_ID}},
            "currentSituation": {},
            "inference": {"lineage": lineage, "hypothesisSet": {"hypotheses": []}},
            "assessmentBundle": {},
            "dataCoverage": {},
        }

        core, audit = route_notification_ai_decision_context(brief)

        routed = core["reasoningLineage"]
        self.assertTrue(routed["judgementEligible"])
        self.assertEqual("000660", routed["identity"]["symbol"])
        self.assertEqual("tbox-fingerprint:test", routed["identity"]["tboxFingerprint"])
        self.assertEqual("rulebox-fingerprint:test", routed["identity"]["ruleboxFingerprint"])
        self.assertEqual(1776000, routed["proof"]["facts"][0]["observedValue"])
        self.assertEqual(RULE_ID, routed["proof"]["traces"][0]["ruleId"])
        self.assertEqual("pass", audit["included"]["reasoningLineageIntegrity"])

        fitted = fit_notification_ai_decision_core(core, 6 * 1024 + 1)
        self.assertEqual(RULE_ID, fitted["reasoningLineage"]["proof"]["rules"][0]["id"])
        self.assertEqual(1776000, fitted["reasoningLineage"]["proof"]["facts"][0]["observedValue"])

        wrong_brief = copy.deepcopy(brief)
        wrong_brief["subject"]["symbol"] = "005380"
        blocked, _audit = route_notification_ai_decision_context(wrong_brief)
        blocked_lineage = blocked["reasoningLineage"]
        self.assertFalse(blocked_lineage["judgementEligible"])
        self.assertEqual("blocked", blocked_lineage["integrity"]["state"])
        self.assertEqual([], blocked_lineage["proof"]["facts"])
        self.assertIn(
            "AI_LINEAGE_SUBJECT_MISMATCH",
            [row["code"] for row in blocked_lineage["integrity"]["issues"]],
        )

    def test_replay_restores_hypotheses_from_lineage_without_old_queue_context(self):
        lineage = subject_reasoning_lineage(
            subject_case(),
            reasoning_case(),
            fallback_ai_episode(),
        )

        brief = notification_ai_decision_brief({
            "messageType": "investmentInsight",
            "rawSymbol": "000660",
            "investmentReasoningLineage": lineage,
        }, {})

        hypothesis_set = brief["inference"]["hypothesisSet"]
        self.assertEqual("subject-reasoning-lineage", hypothesis_set["lineageSource"])
        self.assertEqual(
            ["hypothesis:sk:fundamental"],
            [item["hypothesisId"] for item in hypothesis_set["hypotheses"]],
        )
        self.assertEqual([], hypothesis_set["executionEligibleHypothesisIds"])
        self.assertTrue(brief["guardrails"]["mustReviewEveryInputHypothesis"])

    def test_identity_mismatch_blocks_the_subject_lineage(self):
        mismatched = subject_case()
        mismatched["candidateSet"]["symbol"] = "005380"

        lineage = subject_reasoning_lineage(mismatched, reasoning_case())

        self.assertEqual("integrity-blocked", lineage["status"])
        self.assertEqual("blocked", lineage["integrity"]["state"])
        self.assertIn(
            "LINEAGE_IDENTITY_MISMATCH",
            [row["code"] for row in lineage["integrity"]["issues"]],
        )


if __name__ == "__main__":
    unittest.main()
