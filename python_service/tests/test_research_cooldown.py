"""Collection delays retain the original question and do not consume failures."""
import json
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.application.research import execute_research
from digital_twin.modules.ai_orchestration.domain.research_wait import AIResearchWait
import test_ai_control as controls

SUBJECT = controls.SUBJECT


def cooldown():
    return {'runId': 'run', 'status': 'research-cooldown', 'completedAt': datetime.now(timezone.utc).isoformat(),
            'providerStatuses': [{'status': 'cooldown', 'remainingMinutes': 7}]}


class ResearchCooldownTests(unittest.TestCase):
    def test_source_interval_does_not_prevent_answering_from_existing_receipts(self):
        from test_question_research_progress import EvidenceStore, RunStore, Gateway, Advisor, evidence, task, TARGET
        from digital_twin.modules.decisions.contracts import InvestmentQuestion
        from digital_twin.modules.news_intelligence.application.investment_research_orchestration_service import InvestmentResearchOrchestrationService
        from digital_twin.modules.news_intelligence.application.hypothesis_research_planner_service import HypothesisResearchPlanningService
        runs = RunStore()
        runs.list_runs = lambda *_: [{'status': 'evidence-collected', 'startedAt': datetime.now(timezone.utc).isoformat()}]
        gateway = Gateway()
        orchestrator = InvestmentResearchOrchestrationService(EvidenceStore([evidence()]), gateway, research_store=runs,
            hypothesis_research_planner=HypothesisResearchPlanningService(Advisor()))
        result = orchestrator.run(InvestmentQuestion.create('Did operating cash flow improve?', subject_symbol='AAPL'), TARGET,
            {'documentaryReviewOnly': True, 'researchPlan': {'tasks': [task()]}})
        self.assertEqual('cache-satisfied', result.status)
        self.assertEqual('requirements-met', result.stop_reason)
        self.assertFalse(gateway.requests)

    def test_same_task_resumes_instead_of_reusing_terminal_unavailable_answer(self):
        record = cooldown()
        repository = Mock(); repository.get_run.return_value = record
        orchestrator = Mock(); orchestrator.run.return_value = SimpleNamespace(run_id='run', to_dict=lambda: record)
        factory = Mock(return_value=orchestrator)
        job = {**SUBJECT, 'taskId': 'task', 'question': 'Compare official revenue', 'researchRequest': {}}
        with self.assertRaises(AIResearchWait) as caught:
            execute_research(job, repository, factory)
        factory.assert_called_once()
        wait = caught.exception
        self.assertGreater((datetime.fromisoformat(wait.retry_at.replace('Z','+00:00')) - datetime.now(timezone.utc)).total_seconds(), 400)
        record['status'] = 'evidence-collected'
        repository.get_run.return_value = cooldown()
        self.assertEqual('evidence-collected', execute_research(job, repository, factory)['status'])
        from test_documentary_answers import reviewed_run
        from digital_twin.modules.ai_orchestration.domain.planning import identity
        record.update(reviewed_run(), status='research-cooldown')
        for row in (record['taskAssessments'][0], record['roundHistory'][0]['taskAssessments'][0]):
            row.update(taskId=identity(job['taskId'], 'sources'), status='needs-evidence', semanticReviewState='partial',
                coverageState='incomplete', missingRequirements=['보고서 정정 여부'])
        with self.assertRaises(AIResearchWait) as caught:
            execute_research(job, repository, factory)
        self.assertEqual('partial', caught.exception.result()['documentaryAnswer']['status'])
        self.assertTrue(caught.exception.result()['documentaryAnswer']['sources'])

    def test_wait_does_not_complete_fail_or_publish_the_question(self):
        service, store, _ = controls.AIControlTests().runner(researcher=Mock(side_effect=AIResearchWait(cooldown())))
        store.claim.return_value['capability'] = 'research'
        for saved, expected in ((True, 'research-wait'), (False, 'lease-lost')):
            store.defer_research.return_value = saved
            self.assertEqual(expected, service.run_once()['status'])
        self.assertEqual(2, store.defer_research.call_count)
        store.complete.assert_not_called()
        store.fail.assert_not_called()


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class ResearchCooldownStorageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = controls.AIControlStorageTests()
        self.fixture.setUp()
        self.store = self.fixture.store

    def tearDown(self):
        self.fixture.tearDown()

    def test_wait_updates_owning_question_and_due_time_without_an_answer_notification(self):
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_brain_agenda import MySQLBrainAgendaStore
        from digital_twin.modules.ai_orchestration.domain.planning import stamp
        brain = MySQLBrainAgendaStore(self.store.runtime_settings)
        case = {**SUBJECT, 'caseId': 'cooldown-case', 'kind': 'question', 'status': 'waiting', 'revision': 0,
            'createdAt': stamp(), 'nextCheckAt': stamp(), 'researchAttempts': 1,
            'lastResearch': {'taskId': 'cooldown-linked'}, 'question': 'What does the official report say?'}
        self.store.agenda_writer = brain.record
        brain.progress_writer = Mock()
        try:
            with self.store.transaction() as connection:
                brain.save(connection, case, 'original', 'owner-research-retry')
                self.store.insert(connection, {**SUBJECT, 'taskId': 'cooldown-linked', 'brainCaseId': case['caseId'], 'capability': 'research'})
            job = self.store.claim(); wait = AIResearchWait(cooldown())
            self.assertTrue(self.store.defer_research(job, wait))
            with self.store.connect() as connection:
                saved = brain.read(connection, case['caseId'], SUBJECT['accountId'], SUBJECT['symbol'])
            self.assertEqual('waiting', saved['status'])
            self.assertEqual(wait.retry_at, saved['nextCheckAt'])
            self.assertEqual(wait.result(), saved['lastResearch']['result'])
            self.assertEqual(1, saved['researchAttempts'])
            brain.progress_writer.assert_not_called()
            self.assertFalse(self.store.defer_research(job, wait))
        finally:
            with self.store.transaction() as connection:
                connection.execute('DELETE FROM ai_brain_case_events WHERE case_id=%s', (case['caseId'],))
                connection.execute('DELETE FROM ai_brain_cases WHERE case_id=%s', (case['caseId'],))

    def test_waits_survive_restart_and_preserve_failure_allowance_and_lease(self):
        self.store.runtime_settings['aiControlBudgetEnabled'] = 'false'
        with self.store.transaction() as connection:
            self.store.insert(connection, {**SUBJECT, 'taskId': 'cooldown', 'capability': 'research'})
        first = None
        for count in range(1,5):
            job = self.store.claim(); first = first or job
            self.assertTrue(self.store.defer_research(job, AIResearchWait(cooldown())))
            with self.store.connect() as connection:
                row = connection.execute('SELECT status,payload_json,result_json FROM ai_control_tasks WHERE task_id=%s', (job['taskId'],)).fetchone()
                self.assertEqual('pending', row['status'])
                self.assertEqual(count, json.loads(row['payload_json'])['researchDeferrals'])
                self.assertEqual('research-wait', json.loads(row['result_json'])['status'])
                connection.execute("UPDATE ai_control_tasks SET available_at='2000' WHERE task_id=%s", (job['taskId'],))
        self.assertFalse(self.store.defer_research(first, AIResearchWait(cooldown())))
        for attempt in range(1,4):
            job = self.store.claim()
            self.assertEqual('failed' if attempt == 3 else 'pending', self.store.fail(job, 'source-error')['status'])
            with self.store.connect() as connection:
                connection.execute("UPDATE ai_control_tasks SET available_at='2000' WHERE task_id=%s", (job['taskId'],))
