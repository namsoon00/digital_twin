import json
import unittest

from digital_twin.application.hypothesis_lifecycle_policy_service import HypothesisLifecyclePolicyService
from digital_twin.application.hypothesis_outcome_replay_service import HypothesisOutcomeReplayService
from digital_twin.application.hypothesis_policy_governance_service import HypothesisPolicyGovernanceService
from digital_twin.application.hypothesis_quality_review_service import HypothesisQualityReviewService
from digital_twin.domain.hypothesis_review import outcome_assessment_for_lifecycle
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_rulebox_governance import rulebox_version_payload
from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules


def lifecycle(policy=None):
    return {
        "lifecycleKey": "account:demo:AAPL:trend",
        "lifecycleId": "overlay:demo:AAPL:trend",
        "scope": "account",
        "accountId": "demo",
        "symbol": "AAPL",
        "familyId": "family:AAPL:trend",
        "state": "maintained",
        "snapshot": {
            "sourceRuleIds": ["graph.loss_guard.breakdown.v1"],
            "policy": policy or {},
        },
    }


def episode(episode_id="episode-1", eligibility="eligible", missing=None):
    return {
        "episodeId": episode_id,
        "accountId": "demo",
        "symbol": "AAPL",
        "selectedHypothesisId": "hypothesis:trend",
        "hypothesisSet": {
            "hypotheses": [{
                "hypothesisId": "hypothesis:trend",
                "familyId": "family:AAPL:trend",
                "accountHypothesisOverlayId": "overlay:demo:AAPL:trend",
            }],
        },
        "outcomes": [{
            "outcomeId": "outcome:" + episode_id,
            "observedAt": "2026-07-23T01:00:00Z",
            "selectedHypothesisStatus": "directionally-corroborated",
            "payload": {
                "horizonMinutes": 60,
                "calibrationEligibility": eligibility,
                "missingObservationDomains": list(missing or []),
            },
        }],
    }


class GovernanceRepository:
    def __init__(self):
        self.rule = default_graph_inference_rules()[0].to_dict()
        self.saved_payloads = []
        self.restored = []
        self.validation_calls = []
        self.versions = [{
            "id": "rulebox-version:baseline:test",
            "versionLabel": "baseline",
            "rulesJson": json.dumps([self.rule]),
            "changeReason": "baseline",
            "author": "test",
            "createdAt": "2026-07-23T00:00:00Z",
            "status": "baseline",
        }]

    def rulebox_snapshot(self):
        return {
            "configured": True,
            "status": "ok",
            "rules": [self.rule],
            "versions": self.versions,
            "versionCount": len(self.versions),
        }

    def validate_rulebox_materialization(self, payload):
        self.validation_calls.append(payload)
        return {
            "status": "ok",
            "validationOnly": True,
            "mutatedOperationalRuleBox": False,
            "wroteInferenceBox": False,
            "matchedCount": 1,
        }

    def save_rulebox(self, payload):
        self.saved_payloads.append(payload)
        self.rule = payload["rules"][0]
        return {"saved": True, "status": "ok", "ruleCount": 1, "versionCount": len(self.versions) + 1}

    def restore_rulebox_version(self, version_id, change_reason, author):
        self.restored.append((version_id, change_reason, author))
        return {"saved": True, "status": "ok", "ruleCount": 1, "versionCount": len(self.versions) + 1}

    def ensure_rulebox_version_baseline(self, author):
        return {"saved": True, "status": "ok", "author": author}


class EpisodeStore:
    def __init__(self, rows):
        self.rows = list(rows)
        self.proposals = []

    def list(self, account_id="", symbol="", limit=500):
        rows = list(self.rows)
        if account_id:
            rows = [item for item in rows if item.get("accountId") == account_id]
        if symbol:
            rows = [item for item in rows if item.get("symbol") == symbol]
        return rows[:limit]

    def save_learning_proposal(self, proposal):
        self.proposals.append(proposal)
        return proposal


class WorkspaceReviewService:
    def __init__(self, workspace):
        self.workspace_value = workspace

    def workspace(self, **_kwargs):
        return self.workspace_value


class HypothesisGovernanceTests(unittest.TestCase):
    def test_outcome_contract_excludes_missing_required_observation(self):
        policy = {
            "outcomeContract": {
                "outcomeHorizonMinutes": [60],
                "requiredObservationDomains": ["quote", "flow"],
                "minimumIndependentEpisodes": 1,
                "maximumObservationDelayMinutes": 90,
            },
        }
        assessment = outcome_assessment_for_lifecycle(
            lifecycle(policy),
            [episode(eligibility="excluded-contract-data-gap", missing=["flow"])],
            minimum_samples=1,
        )

        self.assertEqual("insufficient-sample", assessment["outcomeState"])
        self.assertEqual(0, assessment["sampleCount"])
        self.assertEqual(["flow"], assessment["missingObservationDomains"])
        self.assertEqual(90, assessment["outcomeContract"]["maximumObservationDelayMinutes"])

    def test_legacy_outcome_without_eligibility_is_visible_as_migration_gap(self):
        legacy = episode()
        legacy["outcomes"][0]["payload"].pop("calibrationEligibility")
        assessment = outcome_assessment_for_lifecycle(lifecycle(), [legacy], minimum_samples=1)

        self.assertEqual(1, assessment["excludedOutcomeCount"])
        self.assertEqual(1, assessment["excludedOutcomeReasons"]["legacy-eligibility-not-recorded"])

    def test_governed_preview_does_not_save_and_approval_is_human_gated(self):
        repository = GovernanceRepository()
        lifecycle_service = HypothesisLifecyclePolicyService(repository)
        service = HypothesisPolicyGovernanceService(repository, lifecycle_service)
        rule_id = repository.rule["rule_id"]
        policy = {
            "validityMinutes": 75,
            "outcomeContract": {
                "outcomeHorizonMinutes": [60, 1440],
                "requiredObservationDomains": ["quote", "trend"],
                "minimumIndependentEpisodes": 4,
                "maximumObservationDelayMinutes": 120,
            },
        }

        preview = service.preview(rule_id, policy, "계약 미리보기", symbols=["AAPL"])

        self.assertEqual("ready-for-approval", preview["status"])
        self.assertFalse(repository.saved_payloads)
        self.assertTrue(preview["validation"]["validationOnly"])
        self.assertFalse(preview["validation"]["mutatedOperationalRuleBox"])

        approved = service.approve(rule_id, policy, "계약 승인", author="tester", symbols=["AAPL"])

        self.assertEqual("approved", approved["status"])
        self.assertEqual(1, len(repository.saved_payloads))
        saved_policy = repository.saved_payloads[0]["rules"][0]["hypothesis_lifecycle"]
        self.assertEqual(4, saved_policy["outcomeContract"]["minimumIndependentEpisodes"])
        self.assertNotIn("state", saved_policy)

    def test_restore_revalidates_stored_version_before_writing(self):
        repository = GovernanceRepository()
        service = HypothesisPolicyGovernanceService(repository)

        restored = service.restore("rulebox-version:baseline:test", "복원 테스트", author="tester", symbols=["AAPL"])

        self.assertEqual("restored", restored["status"])
        self.assertEqual(1, len(repository.validation_calls))
        self.assertEqual("rulebox-version:baseline:test", repository.restored[0][0])

    def test_rulebox_version_is_projected_as_immutable_governance_concept(self):
        rules = default_graph_inference_rules()
        version = rulebox_version_payload(rules, "2026-07-23T00:00:00Z", "baseline", "tester")
        graph = rulebox_graph_from_rules(rules, include_tbox=False, rulebox_version=version)

        row = next(item for item in graph.entities if item.kind == "rulebox-version")
        self.assertEqual(version["id"], row.properties["versionId"])
        self.assertEqual(version["rulesJson"], row.properties["rulesJson"])
        self.assertIn("HAS_RULEBOX_VERSION", {item.relation_type for item in graph.relations})

    def test_baseline_record_only_adds_governance_history(self):
        repository = GovernanceRepository()
        result = HypothesisPolicyGovernanceService(repository).record_baseline("tester")

        self.assertEqual("baseline-recorded", result["status"])
        self.assertFalse(result["automaticDeployment"])

    def test_quality_review_and_replay_stay_review_only(self):
        assessment = outcome_assessment_for_lifecycle(
            lifecycle({"outcomeContract": {"minimumIndependentEpisodes": 1}}),
            [episode("episode-contradicted", eligibility="eligible")],
            minimum_samples=1,
        )
        assessment["outcomeState"] = "contradicted"
        workspace = {
            "items": [{
                "lifecycleKey": "account:demo:AAPL:trend",
                "lifecycleId": "overlay:demo:AAPL:trend",
                "scope": "account",
                "scopeLabel": "계정 적용 가설",
                "symbol": "AAPL",
                "familyId": "family:AAPL:trend",
                "sourceRuleIds": ["graph.loss_guard.breakdown.v1"],
                "state": "maintained",
                "freshness": [],
                "outcomeAssessment": assessment,
            }],
        }
        store = EpisodeStore([episode("episode-contradicted")])
        quality_service = HypothesisQualityReviewService(store)
        review = quality_service.assess(workspace)
        proposed = quality_service.propose(workspace, reviewed_by="tester")
        replay = HypothesisOutcomeReplayService(store, WorkspaceReviewService(workspace), quality_service).run(
            account_id="demo", symbol="AAPL",
        )

        self.assertEqual("revision-required", review["items"][0]["qualityState"])
        self.assertEqual("proposed", proposed["status"])
        self.assertFalse(proposed["proposals"][0]["proposedChange"]["automaticDeployment"])
        self.assertFalse(replay["mutated"])
        self.assertEqual("historical-replay-only", replay["decisionEligibility"])


if __name__ == "__main__":
    unittest.main()
