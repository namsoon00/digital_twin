"""Boundary regressions run inside existing RuleBox/AI development cases."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock


def assert_dispatch_revalidation(test, flow):
    from digital_twin.modules.notifications.application.notification.eligibility import NotificationDispatchEligibilityService
    from digital_twin.modules.notifications.domain.rule_dispatch_validity import rule_dispatch_validity
    _, admitted, ai, _ = flow.engine_run()
    original = admitted.job
    packet = original.context["relationChangeEvidence"]
    condition = packet["current"]["rules"][0]["conditions"][0]
    test.assertEqual("2026-10-02T01:10:00Z", condition["observedAt"])
    test.assertTrue(rule_dispatch_validity(packet, now=datetime(2026, 10, 2, 1, 20, tzinfo=timezone.utc))["allowed"])
    test.assertFalse(rule_dispatch_validity(packet, now=datetime(2026, 10, 2, 1, 20, 1, tzinfo=timezone.utc))["allowed"])
    # Evidence is checked against its own native validity, not a global quote age.
    for maximum, status, expected in ((120, "fresh", True), (10, "fresh", False), (120, "stale", False)):
        current = deepcopy(packet)
        c = current["current"]["rules"][0]["conditions"][0]
        c.update(maxAgeMinutes=maximum, freshnessRequired=True, freshnessStatus=status)
        with test.subTest(maximum=maximum, status=status):
            test.assertEqual(expected, rule_dispatch_validity(current,
                now=datetime(2026, 10, 2, 2, 0, tzinfo=timezone.utc))["allowed"])
    current = deepcopy(packet)
    c = current["current"]["rules"][0]["conditions"][0]
    c.update(field="strategyMaxPositionWeightPct", freshnessRequired=False, freshnessStatus="not-applicable", observedAt="")
    test.assertTrue(rule_dispatch_validity(current, now=datetime(2026, 10, 4, tzinfo=timezone.utc))["allowed"])
    for observed, maximum in (("", 10), ("2026-10-03T01:10:00Z", 10), ("2026-10-02T01:10:00Z", "nan"),
                              ("2026-10-02T01:10:00Z", 0)):
        current = deepcopy(packet)
        current["current"]["rules"][0]["conditions"][0].update(observedAt=observed, maxAgeMinutes=maximum)
        test.assertFalse(rule_dispatch_validity(current, now=datetime(2026, 10, 2, 1, 11, tzinfo=timezone.utc))["allowed"])
    missing = deepcopy(packet)
    missing["customerEvents"][0]["sourceRuleIds"] = []
    test.assertFalse(rule_dispatch_validity(missing)["allowed"])
    historical = deepcopy(packet)
    historical["customerEvents"][0]["outcome"] = {"outcomeId": "result"}
    test.assertTrue(rule_dispatch_validity(historical, now=datetime(2026, 10, 4, tzinfo=timezone.utc))["allowed"])
    queue, refresh = Mock(), Mock(return_value={"requested": True})
    gate = NotificationDispatchEligibilityService(queue, fresh_data_recheck_requester=refresh,
        freshness_enabled=False, now_provider=lambda: datetime(2026, 10, 2, 1, 21, tzinfo=timezone.utc))
    test.assertFalse(gate.apply_dispatch_freshness_gate(original, "발송 직전"))
    test.assertEqual("rule_evidence_expired_at_dispatch", original.context["deliverySuppressionReason"])
    test.assertFalse(gate.apply_dispatch_freshness_gate(original, "재시도"))
    refresh.assert_called_once_with(original.account_id, "MSTR", original.job_id)
    ai.enqueue.assert_not_called()
    # The worker must stop before rendering/transport and cannot create a receipt.
    from digital_twin.modules.notifications.application.notification.workflow import NotificationQueueRunner
    _, admitted, _, _ = flow.engine_run()
    job = admitted.job
    admitted.pending = lambda limit=10: [job] if job.status == "pending" else []
    admitted.mark_processing = lambda target: setattr(target, "status", "processing")
    admitted.mark_suppressed = lambda target, reason: setattr(target, "status", "suppressed")
    admitted.mark_failed = Mock()
    transport = Mock()
    account = SimpleNamespace(account_id=job.account_id, quiet_hours_active=lambda *_: False)
    runner = NotificationQueueRunner(admitted, SimpleNamespace(load_all=lambda: [account]), lambda _: transport,
        settings={"investmentNotificationRoute": "ai-control"}, fresh_data_recheck_requester=Mock(return_value={"requested": True}),
        now_provider=lambda: datetime(2026, 10, 2, 1, 21, tzinfo=timezone.utc))
    test.assertEqual(1, runner.run_once())
    test.assertEqual("suppressed", job.status)
    transport.send.assert_not_called()
    admitted.mark_failed.assert_not_called()


def assert_receipt_comparison(test, flow):
    from digital_twin.modules.notifications.application.notification.admission import NotificationAdmissionPolicy
    from digital_twin.modules.notifications.domain.notification_rules import default_notification_rule
    from digital_twin.modules.notifications.domain.rule_delivery_comparison import rule_delivery_comparison
    from digital_twin.modules.notifications.domain.relation_follow_up import delivery_snapshot
    from digital_twin.modules.notifications.domain.context_observation_notifications import context_observation_delivery_decision
    _, admitted, _, _ = flow.engine_run()
    job = admitted.job
    previous = delivery_snapshot(job.context["relationChangeEvidence"], "original", job.account_id)
    previous.update(receiptJobId="original", deliveredAt="2026-10-02T01:11:00Z")
    job.context["ontologyRelationDiff"] = {"material": True, "reason": "older coarse comparison"}
    job.context["_relationDeliveredSnapshot"] = previous
    policy, rule = NotificationAdmissionPolicy(), default_notification_rule("investmentInsight")
    decision = policy.evaluate(job, rule, policy.prepare(job, rule))
    test.assertFalse(policy.apply_result(job, decision).accepted)
    test.assertFalse(job.context["ontologyRelationDiff"]["material"])
    test.assertEqual("original", job.context["ruleNotificationComparison"]["baselineJobId"])
    test.assertEqual("successful-delivery-receipt", job.context["ruleNotificationComparison"]["baselineSource"])
    test.assertEqual("suppress", context_observation_delivery_decision(job.context)["decision"])
    for stage in ("publication", "admission", "dispatch-receipt-lock"):
        compared = rule_delivery_comparison(job.context, previous, stage=stage)
        test.assertFalse(compared["ontologyRelationDiff"]["material"])
        test.assertEqual(job.context["relationChangeEvidence"]["nonDeliveryReasonCode"], compared["ruleNotificationComparison"]["reasonCode"])
    foreign = deepcopy(previous)
    foreign["worldId"] = "another-world"
    test.assertTrue(rule_delivery_comparison(job.context, foreign)["relationChangeEvidence"]["eligible"])


def assert_rulebox_feedback(test):
    from ai_insight_fixtures import packet
    from digital_twin.modules.ai_orchestration.domain.rulebox_feedback import improvement_feedback
    from digital_twin.modules.ai_orchestration.domain.planning import observation_fingerprint
    from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, validate_execution_input
    p = packet()
    scope = {k: p[k] for k in ("accountId", "symbol", "worldId")}
    before = "2026-01-01T00:00:00Z"
    p["capturedAt"] = "2026-10-02T01:30:00Z"
    notices = [{**scope, "jobId": "expired", "observedAt": before, "status": "suppressed",
                "reasonCode": "rule_evidence_expired_at_dispatch", "recheck": {"requested": True},
                "comparison": {"sourceRuleIds": ["native-policy"]}},
               {**scope, "jobId": "repeat", "observedAt": before, "status": "suppressed", "reasonCode": "state_cooldown"}]
    notices += [{**notices[0], "accountId": "foreign"}, {**notices[0], "observedAt": "2099-01-01T00:00:00Z"}]
    outcome = {"outcomeId": "result", "observedAt": before, "selectedHypothesisStatus": "directionally-contradicted",
               "payload": {"calibrationEligibility": "eligible", "mode": "contract-criteria", "criterionAssessments": [{"matched": True}],
                           "contractFingerprint": "frozen-contract", "horizonMinutes": 60}}
    episodes = [{**scope, "episodeId": "episode", "decidedAt": before, "outcomes": [outcome, deepcopy(outcome),
                    {**outcome, "outcomeId": "missing", "payload": {"calibrationEligibility": "excluded"}},
                    {**outcome, "outcomeId": "future", "observedAt": "2099-01-01T00:00:00Z"}]}]
    cases = [{**scope, "subjectCaseId": "no-job-case", "observedAt": before, "managementResponseCount": 1,
              "dispatch": {"route": "ARCHIVE", "reasonCode": "no-downstream-publication-value"}, "publicationKind": "OBSERVATION"}]
    records = improvement_feedback(p, notices, episodes, cases=cases)
    test.assertEqual(["evidence-recheck", "repeat-control"], [r["category"] for r in records[0]["notifications"]])
    test.assertEqual(["eligible", "excluded-or-unverified"], [r["eligibility"] for r in records[0]["outcomes"]])
    test.assertEqual("historical-account-symbol", records[0]["outcomes"][0]["scope"])
    test.assertEqual("no-job-case", records[0]["cases"][0]["subjectCaseId"])
    test.assertEqual(records, improvement_feedback({**p, "capturedAt": "2026-10-02T01:31:00Z"}, notices, episodes, cases=cases))
    test.assertNotEqual(observation_fingerprint(p, []), observation_fingerprint(p, records))
    envelope = freeze_execution_input(p, [], records)
    validate_execution_input(envelope)
    test.assertIn("develop-hypothesis", envelope["prompt"])
    test.assertIn("rule_evidence_expired_at_dispatch", envelope["prompt"])
    test.assertTrue(any(r.get("kind") == "rulebox-feedback" and r.get("memoryRole") == "required-continuity"
                        for r in envelope["researchResults"]))
    from test_ai_control import AIControlTests, PLAN
    service, store, planner = AIControlTests().runner(rulebox_feedback=Mock(return_value=records))
    draft = deepcopy(PLAN)
    draft["notification"]["send"] = False
    draft["questions"] = [{"question": "반증된 설명을 현재 근거로 어떻게 개선하고 비교할 것인가?", "capability": "develop-hypothesis",
                           "research": {"queryTerms": [], "sourceTypes": [], "maxAgeMinutes": 0}}]
    planner.return_value = draft
    outcome = service.run_once()
    test.assertEqual("completed", outcome["status"], outcome)
    frozen = planner.call_args.args[0]
    test.assertTrue(any(r.get("kind") == "rulebox-feedback" for r in frozen["researchResults"]))
    result = store.complete.call_args.args[1]
    test.assertEqual(1, len(result["developmentQuestions"]))
    test.assertFalse(result["notification"]["send"])
    test.assertNotIn("action", result)
    # A repository outage is explicit missing feedback, never zero failed predictions.
    test.assertEqual(["outcome-history"], improvement_feedback(p, [], [], ["outcome-history"])[0]["unavailableSources"])
    from contextlib import nullcontext
    from digital_twin.modules.notifications.infrastructure.rule_feedback import RuleBoxNotificationFeedbackReader
    from digital_twin.modules.decisions.infrastructure.rule_feedback import RuleBoxCaseFeedbackReader
    connection, repository = Mock(), Mock()
    repository.connect.side_effect = lambda: nullcontext(connection)
    connection.execute.return_value.fetchall.return_value = [{"job_id": "expired", "status": "suppressed",
        "created_at": before, "updated_at": before, "delivered_at": None, "reasonCode": '"rule_evidence_expired_at_dispatch"',
        "comparison": '{"sourceRuleIds":["native"]}', "validity": '{"allowed":false}', "recheck": '{"requested":true}'}]
    rows = RuleBoxNotificationFeedbackReader(repository).read(scope["accountId"], scope["symbol"], scope["worldId"], p["capturedAt"])
    test.assertEqual("native", rows[0]["comparison"]["sourceRuleIds"][0])
    query, parameters = connection.execute.call_args.args
    test.assertIn("a.status='delivered'", query)
    test.assertEqual((p["capturedAt"], scope["accountId"], scope["symbol"], p["capturedAt"], p["capturedAt"], scope["worldId"]), parameters)
    test.assertNotIn("j.text", query)
    connection.execute.return_value.fetchall.return_value = [{"subject_case_id": "no-job", "updated_at": before,
        "deployment_id": "versioned", "dispatch": '{"route":"ARCHIVE"}', "publication_kind": "OBSERVATION", "response_count": 1}]
    rows = RuleBoxCaseFeedbackReader(repository).read(scope["accountId"], scope["symbol"], scope["worldId"], p["capturedAt"])
    test.assertEqual(1, rows[0]["managementResponseCount"])
    test.assertIn("$.synthesis.rulebox_judgment.worldId", connection.execute.call_args.args[0])
    test.assertIn("$.synthesis.management_decision.responses", connection.execute.call_args.args[0])
