"""Native MySQL proof of atomic, independently reviewed knowledge admission."""
import copy
import os
import unittest
from unittest.mock import Mock

from test_research_knowledge import proposal
from test_business_research import business_packet, raw_research
from ai_insight_fixtures import plan
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan, stamp
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
from digital_twin.modules.ai_orchestration.domain import research_review


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class ResearchKnowledgeStorageTests(unittest.TestCase):
    def setUp(self):
        # The shared test-schema lock is acquired before refreshing the cached
        # existence proof; another test process may have dropped it on exit.
        from mysql_fixtures import mysql_test_settings, mysql_test_database_config
        from digital_twin.infrastructure.mysql_monitoring import forget_mysql_database, ensure_mysql_database_exists
        config = mysql_test_database_config(mysql_test_settings())
        forget_mysql_database(config)
        ensure_mysql_database_exists(config)
        from test_ai_brain_agenda import BrainAgendaStorageTests
        from digital_twin.modules.research_knowledge.infrastructure.mysql_repository import MySQLResearchKnowledgeRepository
        from digital_twin.modules.research_knowledge.public import ResearchKnowledgeService
        from digital_twin.infrastructure.transactions.ai_control_research_knowledge import AIControlResearchKnowledge
        from digital_twin.modules.reasoning.public import research_knowledge_projection
        self.fixture = BrainAgendaStorageTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.control, self.agenda = self.fixture.control, self.fixture.brain
        self.repository = MySQLResearchKnowledgeRepository(self.control.runtime_settings)
        self.service = ResearchKnowledgeService(self.repository)
        self.handoff = AIControlResearchKnowledge(self.service, self.agenda, research_knowledge_projection)
        self.control.knowledge_writer = self.handoff.record
        self.clean_knowledge()
        self.addCleanup(self.clean_knowledge)

    def clean_knowledge(self):
        with self.repository.transaction() as connection:
            for table in ("research_knowledge_revisions", "research_knowledge_claims", "research_knowledge_progress"):
                connection.execute("DELETE FROM " + table + " WHERE account_id=%s", (self.fixture.subject["accountId"],))
            connection.execute("DELETE FROM domain_events WHERE name IN ('research_knowledge.claim_revised','research_knowledge.progress_recorded') AND "
                "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.accountId'))=%s", (self.fixture.subject["accountId"],))

    def draft(self, reviewed=True):
        self.control.seed(self.fixture.subject); job = self.control.claim()
        p = {**business_packet(), "taskId": job["taskId"], "capturedAt": stamp()}
        p["researchKnowledge"] = self.handoff.context(p)
        envelope = freeze_execution_input(p, [], [])
        input_id = self.control.save_execution_input(job, envelope)
        call = self.control.begin_call("independent-observation", envelope["promptHash"], job["taskId"], input_id)
        self.control.finish_call(call)
        raw = {**plan(), "businessResearch": raw_research(), "knowledgeResearch": proposal(businessThesisIndex=0),
               "questionResolutions": [], "followUpConditions": [], "observations": []}
        raw["notification"]["send"] = False
        result = {**validate_plan(raw, p, envelope["researchResults"]), "input": p,
                  "executionInputId": input_id, "observedAt": stamp()}
        result["quality"] = local_quality(result)
        if reviewed:
            review = research_review.freeze(result, envelope["researchResults"], 262144)
            review_id = self.control.save_execution_input(job, review)
            call = self.control.begin_call("independent-observation", review["promptHash"], job["taskId"], review_id)
            self.control.finish_call(call)
            result["researchQuality"] = research_review.accept(result,
                {"version": research_review.VERSION, "accepted": True, "reason": "주장과 반론이 같은 시점의 근거와 적용 조건에 연결되어 있습니다."}, review_id)
        return job, result

    def test_rollback_retry_idempotency_and_next_research_recall(self):
        job, result = self.draft()
        self.control.outbox_writer = Mock(side_effect=RuntimeError("publication failed"))
        with self.assertRaises(RuntimeError):
            self.control.complete(job, result, [])
        self.assertEqual([], self.repository.recent(self.fixture.subject["accountId"]))
        self.assertEqual([], self.agenda.status()["cases"])
        self.control.outbox_writer = None
        self.assertTrue(self.control.complete(job, result, []))
        rows = self.repository.recent(self.fixture.subject["accountId"])
        self.assertEqual(1, len(rows)); self.assertTrue(rows[0]["businessThesisId"])
        p = {**result["input"], "capturedAt": stamp()}
        recalled = self.handoff.context(p)
        self.assertEqual(rows[0]["revisionId"], recalled["claims"][0]["revisionId"])
        self.assertEqual("none", recalled["ontology"]["actionAuthority"])
        self.assertFalse(self.control.complete(job, result, []))
        with self.repository.connect() as c:
            self.assertEqual(1, c.execute("SELECT COUNT(*) AS n FROM research_knowledge_revisions WHERE claim_id=%s", (rows[0]["claimId"],)).fetchone()["n"])
            self.assertEqual(1, c.execute("SELECT COUNT(*) AS n FROM domain_events WHERE aggregate_id=%s AND name='research_knowledge.claim_revised'", (rows[0]["claimId"],)).fetchone()["n"])
        before = self.repository.records(p, result["input"]["capturedAt"])
        self.assertEqual([], before)
        # Existing method editions survive startup and remain addressable.
        # A current release is appended, never written over an older edition.
        import json
        from digital_twin.modules.research_knowledge.domain.frameworks import framework_catalog
        from digital_twin.modules.research_knowledge.domain.knowledge import digest
        legacy = copy.deepcopy(framework_catalog()[0]); legacy["version"] = 1
        legacy["mechanism"] = "과거 연구 틀의 설명과 당시 적용 조건을 변경하지 않고 보존합니다."
        with self.repository.transaction() as c:
            c.execute("INSERT IGNORE INTO research_knowledge_frameworks "
                "(framework_id,version,content_hash,known_at,payload_json) VALUES (%s,%s,%s,%s,%s)",
                (legacy["id"], 1, digest(legacy), "2026-01-01T00:00:00.000000Z", json.dumps(legacy, ensure_ascii=False)))
        from digital_twin.modules.research_knowledge.infrastructure.mysql_repository import MySQLResearchKnowledgeRepository
        reopened = MySQLResearchKnowledgeRepository(self.control.runtime_settings)
        editions = reopened.frameworks()
        self.assertEqual(digest(legacy), next(row["contentHash"] for row in editions if row["id"] == legacy["id"] and row["version"] == 1))
        self.assertEqual(2, next(row["version"] for row in self.handoff.context({**p, "capturedAt": stamp()})["frameworks"] if row["id"] == legacy["id"]))
        # A later source-owner checkpoint is a new immutable assessment, not a
        # rewrite of the earlier prediction or empirical qualification.
        from digital_twin.modules.research_knowledge.infrastructure.transaction_writes import write_research
        from digital_twin.infrastructure.mysql_operational_events import insert_domain_event_with_connection
        observed = {**p, "capturedAt": stamp(), "businessThesisMemory": [{
            "caseId": rows[0]["businessThesisId"], "observations": [{
                "status": "direction-not-observed", "metric": "revenue", "observedAt": stamp()}]}]}
        observed["researchKnowledge"] = self.handoff.context(observed)
        empty = {"claims": [], "coverageNote": "사전 기준의 관측 결과를 보존하고 연결 과정을 다시 확인합니다."}
        with self.repository.transaction() as c:
            receipt = write_research(c, observed, empty, "independent-checkpoint", stamp())
            for event in receipt.pop("events"):
                insert_domain_event_with_connection(c, event)
        next_input = self.handoff.context({**observed, "capturedAt": stamp()})
        evaluated = next_input["claims"][0]
        self.assertEqual(2, evaluated["revision"])
        self.assertEqual("checkpoint-not-observed", evaluated["assessment"]["status"])
        self.assertFalse(evaluated["assessment"]["methodProposal"]["adopted"])
        self.assertEqual("not-empirically-qualified", evaluated["qualification"])
        with self.repository.transaction() as c:
            repeated = write_research(c, observed, empty, "independent-checkpoint", stamp())
            original = c.execute("SELECT payload_json FROM research_knowledge_revisions WHERE claim_id=%s AND revision=1", (evaluated["claimId"],)).fetchone()
        self.assertEqual([], repeated["revisionIds"])
        self.assertEqual("awaiting-observation", json.loads(original["payload_json"])["assessment"]["status"])

    def test_unreviewed_claim_cannot_commit_and_reviewed_content_is_frozen(self):
        job, result = self.draft(reviewed=False)
        self.assertTrue(self.control.complete(job, result, []))
        self.assertEqual("review-required", result["knowledge"]["status"])
        self.assertEqual([], self.repository.recent(self.fixture.subject["accountId"]))
        self.fixture.clean()
        job, result = self.draft()
        altered = copy.deepcopy(result)
        altered["knowledgeResearch"]["claims"][0]["mechanism"] = "독립 검토 후 다른 설명으로 바꾼 주장은 저장할 수 없어야 합니다."
        with self.assertRaises(ValueError):
            self.control.complete(job, altered, [])
        self.assertEqual([], self.repository.recent(self.fixture.subject["accountId"]))

    def test_read_wait_progress_rolls_back_and_reloads_without_manufacturing_knowledge(self):
        from scoped_knowledge_storage_checks import check_wait_progress
        check_wait_progress(self)

    def test_partial_research_real_controller_commits_recalls_and_revises_reviewed_knowledge(self):
        from scoped_knowledge_storage_checks import check_partial_controller
        check_partial_controller(self)
