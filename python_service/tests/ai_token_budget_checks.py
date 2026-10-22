"""Token admission regressions: provider fixtures and isolated MySQL, never live AI."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import threading
from unittest.mock import Mock, patch
from types import SimpleNamespace
import subprocess

from digital_twin.modules.ai_orchestration.domain.budget import AIControlBudgetWait
from digital_twin.modules.ai_orchestration.domain.token_budget import reserve, settle, usage_view, usage_counts
from digital_twin.modules.ai_orchestration.domain.execution_resilience import admit_execution, finish_execution, instant
from digital_twin.modules.ai_orchestration.infrastructure.execution import execute_ai_work

NOW = datetime(2026, 10, 23, 23, 59, tzinfo=timezone.utc)
USAGE = {"input_tokens": 80_000, "cached_input_tokens": 60_000,
         "output_tokens": 20_000, "reasoning_output_tokens": 5_000}


def check_usage_and_budget(test):
    settings = {"aiDailyTokenBudget": 100_000}
    with test.assertRaises(AIControlBudgetWait):
        reserve({"aiDailyTokenBudget": 0}, {}, "news-analysis", NOW)
    state = reserve(settings, {}, "news-analysis", NOW)
    with test.assertRaises(AIControlBudgetWait):
        reserve(settings, state, "independent-observation", NOW)
    state = settle(state, "news-analysis", USAGE)
    view = usage_view(settings, state, NOW)
    test.assertEqual(100_000, view["reportedTokens"])
    test.assertEqual(0, view["reservedTokens"])
    test.assertTrue(view["exhausted"])
    test.assertEqual("2026-10-24T00:00:00Z", view["resetAt"])
    test.assertFalse(usage_view({**settings, "aiTokenBudgetEnabled": "false"}, state, NOW)["exhausted"])
    unknown = settle(reserve(settings, {}, "news-analysis", NOW), "news-analysis", None)
    test.assertEqual(100_000, usage_view(settings, unknown, NOW)["unknownTokensAllowance"])
    no_process = settle(reserve(settings, {}, "news-analysis", NOW), "news-analysis", None, not_started=True)
    test.assertEqual(0, usage_view(settings, no_process, NOW)["unknownTokensAllowance"])
    test.assertFalse(usage_view(settings, no_process, NOW)["exhausted"])
    for invalid in ({}, {"input_tokens": True, "output_tokens": 2}, {"input_tokens": -1, "output_tokens": 2},
                    {"input_tokens": 1.5, "output_tokens": 2}, {**USAGE, "cached_input_tokens": 90_000}):
        test.assertIsNone(usage_counts(invalid))
    store = Mock(); store.begin_call.return_value = "call"
    events = '\n'.join([json.dumps({"type": "item.completed", "item": {"text": "private-secret"}}),
                        json.dumps({"type": "turn.completed", "usage": USAGE}),
                        json.dumps({"type": "turn.completed", "usage": USAGE})])
    with patch("digital_twin.modules.ai_orchestration.infrastructure.execution._store", return_value=store):
        execute_ai_work("news-analysis", "private-prompt", lambda: SimpleNamespace(returncode=0, stdout=events, stderr=""), {})
        metrics = store.finish_call.call_args.kwargs["metrics"]
        test.assertEqual(160_000, metrics["usage"]["input_tokens"])
        test.assertEqual(40_000, metrics["usage"]["output_tokens"])
        test.assertNotIn("private", repr(metrics))
        execute_ai_work("news-analysis", "", lambda: SimpleNamespace(returncode=0, stdout="{}", stderr=""), {})
        test.assertIsNone(store.finish_call.call_args.kwargs["metrics"]["usage"])
        invoke = Mock()
        store.begin_call.side_effect = AIControlBudgetWait("token", "2026-10-24T00:00:00Z")
        with test.assertRaises(AIControlBudgetWait):
            execute_ai_work("news-analysis", "", invoke, {})
        invoke.assert_not_called()
    # Production background adapters request JSONL for usage, then keep their
    # existing final-message return contract. Explicit event consumers keep JSONL.
    from digital_twin.modules.model_registry.infrastructure.model_reviewer import run_background_ai_prompt
    stdout = '\n'.join([json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"answer":1}'}}),
                        json.dumps({"type": "turn.completed", "usage": USAGE})])
    command = ["codex", "exec", "--skip-git-repo-check", "-"]
    store.begin_call.side_effect = None
    with patch("digital_twin.modules.ai_orchestration.infrastructure.execution._store", return_value=store), \
         patch("digital_twin.modules.model_registry.infrastructure.model_reviewer.run_ai_prompt_command",
               side_effect=lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout, "")) as run:
        result = run_background_ai_prompt(command, "fixture", 30, {"aiWorkload": "news-analysis"})
        test.assertIn("--json", run.call_args.args[0])
        test.assertNotIn("--json", command)
        test.assertEqual('{"answer":1}', result.stdout)
        test.assertEqual(USAGE, store.finish_call.call_args.kwargs["metrics"]["usage"])
        raw = run_background_ai_prompt([*command[:-1], "--json", "-"], "fixture", 30, {"aiWorkload": "source-search"})
        test.assertEqual(stdout, raw.stdout)


def check_quota_backoff(test):
    state, now = {}, NOW
    for hours in (1, 2, 4, 6, 6):
        state, ticket, wait = admit_execution(state, now, "probe")
        test.assertIsNone(wait)
        state = finish_execution(state, ticket, now, "failure", {"category": "quota"})
        test.assertEqual(now + timedelta(hours=hours), instant(state["blockedUntil"]))
        _, rejected, wait = admit_execution(state, now + timedelta(minutes=15), "other")
        test.assertIsNone(rejected)
        test.assertIsNotNone(wait)
        now = instant(state["blockedUntil"]) + timedelta(seconds=1)
    state, ticket, _ = admit_execution(state, now, "recovered")
    state = finish_execution(state, ticket, now, "success")
    test.assertEqual(0, state["quotaFailures"])


def check_persistence(test):
    store = test.store
    original = dict(store.runtime_settings)
    try:
        store.runtime_settings.update(aiTokenBudgetEnabled="true", aiDailyTokenBudget=100_000)
        other = type(store)(store.runtime_settings)
        barrier = threading.Barrier(2)
        def start(worker):
            barrier.wait(timeout=10)
            try:
                return worker.begin_call("news-analysis", "fixture-hash")
            except AIControlBudgetWait as error:
                test.assertEqual("ai-token-budget-exhausted", error.code)
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            calls = list(pool.map(start, (store, other)))
        winners = [call for call in calls if call]
        test.assertEqual(1, len(winners))
        call = winners[0]
        store.finish_call(call, "provider-error", metrics={"usage": USAGE})
        store.finish_call(call, metrics={"usage": USAGE})
        test.assertEqual(100_000, other.status()["tokenUsageToday"]["reportedTokens"])
        test.assertEqual("ai-token-budget-exhausted", store.status()["observationScheduling"]["reason"])
        with test.assertRaises(AIControlBudgetWait):
            other.begin_call("model-review", "hash")
        # A higher budget resumes without erasing previous usage.
        store.runtime_settings["aiDailyTokenBudget"] = 200_000
        second = store.begin_call("model-review", "hash")
        store.finish_call(second, "TimeoutError")
        tokens = store.status()["tokenUsageToday"]
        test.assertEqual(100_000, tokens["unknownTokensAllowance"])
        test.assertEqual(0, tokens["reservedTokens"])
        # New UTC day starts a separate ledger; a late completion belongs to its admission day.
        later = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        with patch("digital_twin.modules.ai_orchestration.infrastructure.mysql_control.stamp", return_value=later.isoformat().replace("+00:00", "Z")):
            third = store.begin_call("news-analysis", "hash")
        store.finish_call(third, metrics={"usage": {"input_tokens": 10, "output_tokens": 5}})
        with store.connect() as connection:
            next_day = store.token_usage(connection, later)
        test.assertEqual(15, next_day["reportedTokens"])
        test.assertEqual(100_000, store.status()["tokenUsageToday"]["reportedTokens"])
        test.assertEqual(0, next_day["reservedTokens"])
    finally:
        store.runtime_settings.clear()
        store.runtime_settings.update(original)
        test.clean()
