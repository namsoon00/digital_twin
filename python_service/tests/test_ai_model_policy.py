import sys
import unittest
from pathlib import Path
from unittest.mock import call, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.infrastructure.disclosure_analyzer import disclosure_analyzer_from_settings  # noqa: E402
from digital_twin.infrastructure.hypothesis_proposal_ai import hypothesis_proposal_advisor_from_settings  # noqa: E402
from digital_twin.infrastructure.hypothesis_research_planner_ai import hypothesis_research_planning_advisor_from_settings  # noqa: E402
from digital_twin.infrastructure.model_reviewer import reviewer_from_settings  # noqa: E402
from digital_twin.infrastructure.news_ai_analyzer import news_ai_analyzer_from_settings  # noqa: E402
from digital_twin.infrastructure.notification_ai_reviewer import notification_ai_reviewer_from_settings  # noqa: E402
from digital_twin.infrastructure.rule_change_candidate_ai import rule_change_candidate_advisor_from_settings  # noqa: E402


class AiModelPolicyTests(unittest.TestCase):
    def test_all_application_ai_factories_ignore_custom_commands_and_use_the_fixed_codex_policy(self):
        fixed_command = "codex --model gpt-5.6-sol --config model_reasoning_effort=max exec -"
        with patch("digital_twin.infrastructure.model_reviewer.codex_command", return_value=fixed_command) as model_command, \
             patch("digital_twin.infrastructure.notification_ai_reviewer.codex_command", return_value=fixed_command) as notification_command, \
             patch("digital_twin.infrastructure.news_ai_analyzer.codex_command", return_value=fixed_command) as news_command, \
             patch("digital_twin.infrastructure.disclosure_analyzer.codex_command", return_value=fixed_command) as disclosure_command, \
             patch("digital_twin.infrastructure.rule_change_candidate_ai.codex_command", return_value=fixed_command) as rule_command, \
             patch("digital_twin.infrastructure.hypothesis_proposal_ai.codex_command", return_value=fixed_command) as proposal_command, \
             patch("digital_twin.infrastructure.hypothesis_research_planner_ai.codex_command", return_value=fixed_command) as planning_command:
            reviewer_from_settings({"modelReviewUseCodex": "1", "modelReviewCommand": "other-llm"})
            notification_reviewer = notification_ai_reviewer_from_settings({"notificationAiUseCodex": "1", "notificationAiCommand": "other-llm"})
            news_ai_analyzer_from_settings({"newsAiAnalysisUseCodex": "1", "newsAiAnalysisCommand": "other-llm"})
            disclosure_analyzer_from_settings({"dartDisclosureAiUseCodex": "1", "dartDisclosureAiCommand": "other-llm"})
            rule_change_candidate_advisor_from_settings({"ontologyRuleCandidateAiUseCodex": "1", "ontologyRuleCandidateAiCommand": "other-llm"})
            hypothesis_proposal_advisor_from_settings({"investmentBrainNovelHypothesisAiEnabled": "1", "investmentBrainNovelHypothesisAiCommand": "other-llm"})
            hypothesis_research_planning_advisor_from_settings({"investmentBrainHypothesisResearchPlannerAiEnabled": "1", "investmentBrainHypothesisResearchPlannerAiCommand": "other-llm"})

        for command in [
            model_command,
            news_command,
            disclosure_command,
            rule_command,
            proposal_command,
            planning_command,
        ]:
            command.assert_called_once_with()
        notification_command.assert_called_once_with(reasoning_effort="high")
        notification_reviewer.primary.command_factory(reasoning_effort="max")
        self.assertEqual(
            [call(reasoning_effort="high"), call(reasoning_effort="max")],
            notification_command.call_args_list,
        )

    def test_notification_delivery_deadline_caps_codex_gate_wait(self):
        fixed_command = "codex --model gpt-5.6-sol --config model_reasoning_effort=max exec -"
        with patch(
            "digital_twin.infrastructure.notification_ai_reviewer.codex_command",
            return_value=fixed_command,
        ):
            reviewer = notification_ai_reviewer_from_settings({
                "notificationAiUseCodex": "1",
                "notificationAiTimeoutSeconds": "120",
                "notificationAiDeliveryDeadlineSeconds": "15",
            })

        self.assertEqual(15, reviewer.primary.timeout_seconds)

    def test_notification_ai_uses_separate_low_latency_effort_and_default_deadline(self):
        fixed_command = "codex --model gpt-5.6-sol --config model_reasoning_effort=low exec -"
        with patch(
            "digital_twin.infrastructure.notification_ai_reviewer.codex_command",
            return_value=fixed_command,
        ) as command:
            reviewer = notification_ai_reviewer_from_settings({
                "notificationAiUseCodex": "1",
                "notificationAiTimeoutSeconds": "120",
                "notificationAiDeliveryDeadlineSeconds": "90",
                "notificationAiReasoningEffort": "low",
            })

        command.assert_called_once_with(reasoning_effort="low")
        self.assertEqual(90, reviewer.primary.timeout_seconds)
        self.assertIn("low", reviewer.primary.source)

    def test_dedicated_notification_ai_queue_uses_primary_without_inline_fallback(self):
        fixed_command = "codex --model gpt-5.6-sol --config model_reasoning_effort=max exec -"
        with patch(
            "digital_twin.infrastructure.notification_ai_reviewer.codex_command",
            return_value=fixed_command,
        ):
            reviewer = notification_ai_reviewer_from_settings({
                "notificationAiUseCodex": "1",
                "notificationAiReasoningEffort": "max",
                "notificationAiTimeoutSeconds": "300",
                "notificationAiDeliveryDeadlineSeconds": "300",
            }, allow_local_fallback=False)

        self.assertEqual(300, reviewer.timeout_seconds)
        self.assertIn("max", reviewer.source)
        self.assertFalse(hasattr(reviewer, "primary"))


if __name__ == "__main__":
    unittest.main()
