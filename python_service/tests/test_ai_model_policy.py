import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.infrastructure.disclosure_analyzer import disclosure_analyzer_from_settings  # noqa: E402
from digital_twin.infrastructure.hypothesis_proposal_ai import hypothesis_proposal_advisor_from_settings  # noqa: E402
from digital_twin.infrastructure.hypothesis_research_planner_ai import hypothesis_research_planning_advisor_from_settings  # noqa: E402
from digital_twin.infrastructure.model_reviewer import (  # noqa: E402
    _CODEX_PREFLIGHT_CACHE,
    healthy_codex_executable,
    reviewer_from_settings,
)
from digital_twin.infrastructure.news_ai_analyzer import news_ai_analyzer_from_settings  # noqa: E402
from digital_twin.infrastructure.local_ai_process_guard import (  # noqa: E402
    LocalAICapacityUnavailable,
    local_ai_capacity_lease,
    run_ai_prompt_command,
)
from digital_twin.infrastructure.notification_ai_reviewer import (  # noqa: E402
    CommandNotificationAIReviewer,
    notification_ai_reviewer_from_settings,
)
from digital_twin.infrastructure.rule_change_candidate_ai import rule_change_candidate_advisor_from_settings  # noqa: E402


class AiModelPolicyTests(unittest.TestCase):
    def test_all_application_ai_factories_ignore_custom_commands_and_use_the_fixed_codex_policy(self):
        fixed_command = ["codex", "--model", "gpt-5.6-sol", "exec", "-"]
        with patch("digital_twin.infrastructure.model_reviewer.background_codex_process_arguments", return_value=fixed_command) as model_command, \
             patch("digital_twin.infrastructure.notification_ai_reviewer.codex_process_arguments", return_value=["codex", "exec", "-"]) as notification_command, \
             patch("digital_twin.infrastructure.news_ai_analyzer.background_codex_process_arguments", return_value=fixed_command) as news_command, \
             patch("digital_twin.infrastructure.disclosure_analyzer.background_codex_process_arguments", return_value=fixed_command) as disclosure_command, \
             patch("digital_twin.infrastructure.rule_change_candidate_ai.background_codex_process_arguments", return_value=fixed_command) as rule_command, \
             patch("digital_twin.infrastructure.hypothesis_proposal_ai.background_codex_process_arguments", return_value=fixed_command) as proposal_command, \
             patch("digital_twin.infrastructure.hypothesis_research_planner_ai.background_codex_process_arguments", return_value=fixed_command) as planning_command:
            reviewer_from_settings({"modelReviewUseCodex": "1", "modelReviewCommand": "other-llm"})
            notification_reviewer = notification_ai_reviewer_from_settings({"notificationAiUseCodex": "1", "notificationAiCommand": "other-llm"})
            news_ai_analyzer_from_settings({"newsAiAnalysisUseCodex": "1", "newsAiAnalysisCommand": "other-llm"})
            disclosure_analyzer_from_settings({"dartDisclosureAiUseCodex": "1", "dartDisclosureAiCommand": "other-llm"})
            rule_change_candidate_advisor_from_settings({"ontologyRuleCandidateAiUseCodex": "1", "ontologyRuleCandidateAiCommand": "other-llm"})
            hypothesis_proposal_advisor_from_settings({"investmentBrainNovelHypothesisAiEnabled": "1", "investmentBrainNovelHypothesisAiCommand": "other-llm"})
            hypothesis_research_planning_advisor_from_settings({"investmentBrainHypothesisResearchPlannerAiEnabled": "1", "investmentBrainHypothesisResearchPlannerAiCommand": "other-llm"})
            notification_reviewer.primary.command_factory(reasoning_effort="max")

        for command in [
            model_command,
            rule_command,
            proposal_command,
            planning_command,
        ]:
            command.assert_called_once_with()
        news_command.assert_called_once_with("medium")
        disclosure_command.assert_called_once_with("medium")
        self.assertEqual("high", notification_command.call_args_list[0].kwargs["reasoning_effort"])
        self.assertEqual("max", notification_command.call_args_list[-1].kwargs["reasoning_effort"])

        _CODEX_PREFLIGHT_CACHE.clear()
        fake_stat = type("Stat", (), {"st_mtime_ns": 1, "st_size": 2})()
        with patch("digital_twin.infrastructure.model_reviewer.shutil.which", return_value="/tmp/codex"), \
             patch("digital_twin.infrastructure.model_reviewer.os.stat", return_value=fake_stat), \
             patch(
                 "digital_twin.infrastructure.model_reviewer.subprocess.run",
                 side_effect=subprocess.TimeoutExpired(["codex", "--version"], 5),
             ):
            self.assertEqual("", healthy_codex_executable())

    def test_notification_delivery_deadline_caps_codex_gate_wait(self):
        fixed_command = "codex --model gpt-5.6-sol --config model_reasoning_effort=max exec -"
        with patch(
            "digital_twin.infrastructure.notification_ai_reviewer.codex_process_arguments",
            return_value=["codex", "exec", "-"],
        ):
            reviewer = notification_ai_reviewer_from_settings({
                "notificationAiUseCodex": "1",
                "notificationAiTimeoutSeconds": "120",
                "notificationAiDeliveryDeadlineSeconds": "15",
            })

        self.assertEqual(15, reviewer.primary.timeout_seconds)

    def test_notification_ai_uses_separate_effort_and_configured_deadline(self):
        fixed_command = "codex --model gpt-5.6-sol --config model_reasoning_effort=low exec -"
        with patch(
            "digital_twin.infrastructure.notification_ai_reviewer.codex_process_arguments",
            return_value=["codex", "exec", "-"],
        ) as command:
            reviewer = notification_ai_reviewer_from_settings({
                "notificationAiUseCodex": "1",
                "notificationAiTimeoutSeconds": "120",
                "notificationAiDeliveryDeadlineSeconds": "90",
                "notificationAiReasoningEffort": "low",
            })

        self.assertEqual("low", command.call_args.kwargs["reasoning_effort"])
        self.assertEqual(90, reviewer.primary.timeout_seconds)
        self.assertIn("low", reviewer.primary.source)

        direct_reviewer = CommandNotificationAIReviewer(
            [
                sys.executable,
                "-c",
                "import sys,time; sys.stdin.read(); time.sleep(0.02); print('{}')",
            ],
            timeout_seconds=0,
        )
        expected = object()
        with patch(
            "digital_twin.infrastructure.notification_ai_reviewer.validated_response_from_text",
            return_value=expected,
        ):
            response = direct_reviewer.review({
                "messageType": "investmentInsight",
                "_notificationAiPreparedPrompt": "DecisionCore: {}",
            })
        self.assertIs(expected, response)
        self.assertEqual("wait-until-complete", direct_reviewer.last_execution_spans["completionPolicy"])
        self.assertEqual(0, direct_reviewer.last_execution_spans["configuredTimeoutSeconds"])
        self.assertGreater(direct_reviewer.last_execution_spans["modelProcessMs"], 0)

    def test_dedicated_notification_ai_queue_uses_primary_without_inline_fallback(self):
        fixed_command = "codex --model gpt-5.6-sol --config model_reasoning_effort=max exec -"
        with patch(
            "digital_twin.infrastructure.notification_ai_reviewer.codex_process_arguments",
            return_value=["codex", "exec", "-"],
        ):
            reviewer = notification_ai_reviewer_from_settings({
                "notificationAiUseCodex": "1",
                "notificationAiReasoningEffort": "max",
                "notificationAiTimeoutSeconds": "0",
                "notificationAiDeliveryDeadlineSeconds": "0",
            }, allow_local_fallback=False)

        self.assertIsNone(reviewer.timeout_seconds)
        self.assertIn("max", reviewer.source)
        self.assertFalse(hasattr(reviewer, "primary"))

        with tempfile.TemporaryDirectory() as directory:
            lock_dir = Path(directory)
            with local_ai_capacity_lease(
                lock_dir,
                max_concurrent=2,
                wait_seconds=1,
                lane="background",
                reserved_priority_slots=1,
            ) as background_slot:
                with local_ai_capacity_lease(
                    lock_dir,
                    max_concurrent=2,
                    wait_seconds=1,
                    lane="investment-judgement",
                    reserved_priority_slots=1,
                ) as investment_slot:
                    self.assertEqual("slot-2.lock", background_slot.name)
                    self.assertEqual("slot-1.lock", investment_slot.name)

            cancel_event = threading.Event()
            cancel_event.set()
            with self.assertRaisesRegex(LocalAICapacityUnavailable, "cancelled"):
                with local_ai_capacity_lease(
                    lock_dir,
                    max_concurrent=1,
                    wait_seconds=None,
                    lane="investment-judgement",
                    reserved_priority_slots=0,
                    cancel_event=cancel_event,
                ):
                    pass

            child_pid_path = lock_dir / "child.pid"
            script = (
                "import pathlib,subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)"
            )
            with self.assertRaises(subprocess.TimeoutExpired):
                run_ai_prompt_command(
                    [sys.executable, "-c", script, str(child_pid_path)],
                    "",
                    lock_dir=lock_dir,
                    max_concurrent=1,
                    wait_seconds=1,
                    lane="background",
                    reserved_priority_slots=0,
                    timeout_seconds=0.2,
                )
            child_pid = int(child_pid_path.read_text())
            time.sleep(0.05)
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)


if __name__ == "__main__":
    unittest.main()
