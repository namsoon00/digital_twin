import unittest

from digital_twin.application.hypothesis_lifecycle_policy_service import HypothesisLifecyclePolicyService
from digital_twin.application.hypothesis_review_service import HypothesisReviewService
from digital_twin.application.notification_ai_gate_message import execution_telegram_message
from digital_twin.domain.hypothesis_review import (
    lifecycle_review_item,
    outcome_assessment_for_lifecycle,
)
from digital_twin.domain.notification_ai import notification_ai_prompt_context
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notification_ai_gate_validation import build_notification_ai_gate_prompt
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_schema import add_entity
from digital_twin.domain.portfolio_ontology_cognitive_concepts import add_investment_brain_concepts


MARKET_ID = "market:AAPL:trend-recovery"


def episode(episode_id, account_id, status, eligible=True, horizon_minutes=60):
    selected_id = "hypothesis:" + episode_id
    return {
        "episodeId": episode_id,
        "accountId": account_id,
        "symbol": "AAPL",
        "subjectName": "Apple",
        "selectedHypothesisId": selected_id,
        "hypothesisSet": {
            "hypotheses": [{
                "hypothesisId": selected_id,
                "familyId": "family:AAPL:trend-recovery",
                "marketHypothesisId": MARKET_ID,
                "accountHypothesisOverlayId": "overlay:" + account_id + ":AAPL",
                "supportingRuleIds": ["graph.aapl.trend-recovery.v1"],
            }],
        },
        "outcomes": [{
            "outcomeId": "outcome:" + episode_id,
            "observedAt": "2026-07-23T01:00:00Z",
            "selectedHypothesisStatus": status,
            "payload": {
                "calibrationEligibility": "eligible" if eligible else "delayed",
                "horizonMinutes": horizon_minutes,
            },
        }],
    }


def market_lifecycle():
    return {
        "lifecycleKey": "market|AAPL|" + MARKET_ID,
        "lifecycleId": MARKET_ID,
        "scope": "market",
        "symbol": "AAPL",
        "familyId": "family:AAPL:trend-recovery",
        "state": "maintained",
        "stateLabel": "유지",
        "lastObservedAt": "2026-07-23T00:00:00Z",
        "snapshot": {
            "policy": {
                "formationConditionIds": ["price-above-ma20"],
                "invalidationConditionIds": ["price-below-ma60"],
                "validityMinutes": 120,
                "requiredFreshnessDomains": ["price"],
                "nextDataRequirements": ["다음 정규장 가격과 거래량"],
            },
            "sourceRuleIds": ["graph.aapl.trend-recovery.v1"],
            "observationProfiles": {
                "price": {"freshnessStatus": "fresh", "freshnessGateReason": ""},
            },
        },
    }


def account_lifecycle(account_id="account-1"):
    return {
        "lifecycleKey": "account|" + account_id + "|AAPL|overlay:" + account_id + ":AAPL",
        "lifecycleId": "overlay:" + account_id + ":AAPL",
        "scope": "account",
        "accountId": account_id,
        "symbol": "AAPL",
        "familyId": "family:AAPL:trend-recovery",
        "state": "strengthened",
        "stateLabel": "강화",
        "lastObservedAt": "2026-07-23T00:00:00Z",
        "snapshot": {
            "policy": {
                "formationConditionIds": ["position-within-limit"],
                "invalidationConditionIds": ["loss-limit-exceeded"],
                "validityMinutes": 60,
                "requiredFreshnessDomains": ["price", "portfolio"],
                "nextDataRequirements": ["계좌 비중과 다음 가격"],
            },
            "sourceRuleIds": ["graph.aapl.trend-recovery.v1"],
            "observationProfiles": {
                "price": {"freshnessStatus": "fresh", "freshnessGateReason": ""},
                "portfolio": {"freshnessStatus": "fresh", "freshnessGateReason": ""},
            },
        },
    }


class FakeLifecycleStore:
    def __init__(self, records, events=None):
        self.records = list(records)
        self.events = list(events or [])

    def list_current(self, **_kwargs):
        return list(self.records)

    def list_events(self, **_kwargs):
        return list(self.events)


class FakeEpisodeStore:
    def __init__(self, rows):
        self.rows = list(rows)

    def list(self, account_id="", symbol="", limit=50):
        rows = list(self.rows)
        if account_id:
            rows = [item for item in rows if item["accountId"] == account_id]
        if symbol:
            rows = [item for item in rows if item["symbol"] == symbol]
        return rows[:limit]


class FakeRuleboxRepository:
    def __init__(self):
        self.saved_payload = None
        self.rule = default_graph_inference_rules()[0].to_dict()

    def rulebox_snapshot(self):
        return {"rules": [self.rule]}

    def save_rulebox(self, payload):
        self.saved_payload = payload
        return {"saved": True, "status": "ok", "ruleCount": len(payload.get("rules") or []), "versionCount": 1}


class HypothesisReviewTests(unittest.TestCase):
    def setUp(self):
        self.account_one = episode("episode-one", "account-1", "directionally-corroborated")
        self.account_two = episode("episode-two", "account-2", "directionally-contradicted")

    def test_market_and_account_outcomes_are_kept_separate(self):
        market = outcome_assessment_for_lifecycle(market_lifecycle(), [self.account_one, self.account_two], minimum_samples=2)
        overlay = outcome_assessment_for_lifecycle(account_lifecycle(), [self.account_one, self.account_two], minimum_samples=1)

        self.assertEqual("inconclusive", market["outcomeState"])
        self.assertEqual(2, market["sampleCount"])
        self.assertEqual("supported", overlay["outcomeState"])
        self.assertEqual(1, overlay["sampleCount"])
        self.assertEqual("account-1", overlay["accountId"])
        self.assertFalse(market["automaticDeployment"])
        self.assertEqual("historical-review-only", market["decisionEligibility"])

    def test_ineligible_later_observation_stays_out_of_conclusion(self):
        delayed = episode("episode-delayed", "account-1", "directionally-corroborated", eligible=False)
        assessment = outcome_assessment_for_lifecycle(account_lifecycle(), [delayed], minimum_samples=1)

        self.assertEqual("insufficient-sample", assessment["outcomeState"])
        self.assertEqual(0, assessment["sampleCount"])
        self.assertEqual(1, assessment["excludedOutcomeCount"])

    def test_workspace_exposes_lifecycle_and_outcomes_without_action_selector(self):
        service = HypothesisReviewService(
            hypothesis_lifecycle_store=FakeLifecycleStore([market_lifecycle(), account_lifecycle()]),
            decision_episode_store=FakeEpisodeStore([self.account_one, self.account_two]),
            settings={"hypothesisOutcomeReviewMinimumSamples": "1"},
        )

        workspace = service.workspace(account_id="account-1", symbol="AAPL")
        items = {item["scope"]: item for item in workspace["items"]}

        self.assertEqual("review-only-not-action-selector", workspace["decisionEligibility"])
        self.assertEqual("inconclusive", items["market"]["outcomeAssessment"]["outcomeState"])
        self.assertEqual("supported", items["account"]["outcomeAssessment"]["outcomeState"])
        self.assertEqual("2026-07-23T01:00:00Z", items["account"]["outcomeAssessment"]["latestObservedAt"])

    def test_lifecycle_item_has_expiry_and_freshness_without_mutating_state(self):
        item = lifecycle_review_item(account_lifecycle())

        self.assertEqual("strengthened", item["state"])
        self.assertEqual("2026-07-23T01:00:00Z", item["expiresAt"])
        self.assertEqual("fresh", item["freshness"][0]["status"])

    def test_rulebox_policy_update_changes_policy_not_lifecycle_state(self):
        repository = FakeRuleboxRepository()
        rule_id = repository.rule["rule_id"]
        result = HypothesisLifecyclePolicyService(repository).update(rule_id, {
            "formationConditionIds": ["price-above-ma20"],
            "invalidationConditionIds": ["price-below-ma60"],
            "validityMinutes": 90,
            "requiredFreshnessDomains": ["price"],
            "nextDataRequirements": ["다음 정규장 가격"],
            "invalidationMode": "typedb-rule-not-materialized",
        }, "테스트 정책 변경")

        self.assertEqual("ok", result["status"])
        self.assertEqual(90, result["updatedRule"]["policy"]["validityMinutes"])
        saved = repository.saved_payload["rules"][0]
        self.assertEqual(90, saved["hypothesis_lifecycle"]["validityMinutes"])
        self.assertNotIn("state", saved["hypothesis_lifecycle"])

    def test_abox_projects_hypothesis_outcomes_as_historical_review_only(self):
        graph = PortfolioOntology("account-1")
        add_entity(graph, "portfolio", "account-1", "계좌", {"tboxClass": "Portfolio"})
        add_entity(graph, "stock", "AAPL", "Apple", {"tboxClass": "Stock"})

        add_investment_brain_concepts(graph, "account-1", [self.account_one, self.account_two])

        rows = [item for item in graph.entities if item.kind == "hypothesis-outcome-assessment"]
        self.assertEqual(3, len(rows))
        self.assertTrue(all(item.properties["automaticDeployment"] is False for item in rows))
        self.assertTrue(all(item.properties["decisionEligibility"] == "historical-review-only" for item in rows))
        self.assertIn("HAS_HYPOTHESIS_OUTCOME_ASSESSMENT", {item.relation_type for item in graph.relations})

    def test_ai_prompt_and_message_receive_lifecycle_context_as_explanation_only(self):
        brief = {
            "decisionEligibility": "context-only-not-action-selector",
            "summary": "이전 세대와 비교해 근거가 바뀌었습니다.",
            "materialChanges": [{
                "scopeLabel": "시장 공통 가설",
                "stateLabel": "약화",
                "transitionReason": "새 반대 근거가 추가되었습니다.",
            }],
            "nextDataRequirements": ["다음 정규장 거래량"],
            "items": [{
                "outcomeAssessment": {
                    "outcomeState": "supported",
                    "outcomeStateLabel": "지지됨",
                    "summary": "유효한 사후 관측에서 같은 방향의 결과가 더 많이 확인됐습니다.",
                },
            }],
        }
        context = {
            "messageType": "investmentInsight",
            "title": "Apple 가설 검증",
            "displayTarget": "Apple / AAPL",
            "messageDeliveryLevel": "intermediate",
            "ontologyRelationContext": {"hypothesisDecisionBrief": brief},
        }
        response = NotificationAIValidatedResponse(
            summary="현재 근거를 확인합니다.",
            opinion="추가 확인이 필요합니다.",
            strategy_guide={
                "hypothesisUpdate": "새 반대 근거가 추가돼 기존 가설이 약화됐습니다.",
                "hypothesisNextCheck": "다음 정규장 거래량을 확인합니다.",
            },
        )

        prompt_context = notification_ai_prompt_context("investmentInsight", context)
        prompt = build_notification_ai_gate_prompt(context)
        message = execution_telegram_message(context, response)

        self.assertEqual("context-only-not-action-selector", prompt_context["facts"]["hypothesisDecisionBrief"]["decisionEligibility"])
        self.assertIn("hypothesisDecisionBrief", prompt)
        self.assertIn("가설 변화와 검증", message)
        self.assertIn("AI가 본 가설 변화", message)


if __name__ == "__main__":
    unittest.main()
