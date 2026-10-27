"""External event -> company source -> bounded research -> reviewed observation."""
from copy import deepcopy
from datetime import datetime, timezone
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai_insight_fixtures import plan, ref, review
from test_efficient_retrieval import packet as company_packet
from test_scoped_knowledge_research import scoped_decision
from digital_twin.modules.ai_orchestration.domain import external_influence as influence
from digital_twin.modules.ai_orchestration.domain import external_influence_prompt as author
from digital_twin.modules.ai_orchestration.domain import external_influence_retrieval as reader
from digital_twin.modules.ai_orchestration.domain import question_purpose, question_source_handoff
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input)
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
from digital_twin.modules.ai_orchestration.domain.insight_quality import accept_review, quality_block
from digital_twin.modules.ai_orchestration.domain.publication import publication_block, repeat_block
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession, EvidenceContractError
from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence


def packet():
    p = company_packet()
    common = {k: p["facts"][0][k] for k in ("sourceWorldId", "sourceSnapshotId", "sourceAsOf")}
    event = {**common, "id": "rate-event", "kind": "interest-rate", "seriesId": "TEST-RATE",
             "value": 4.0, "unit": "percent", "judgementEvidenceUsable": True}
    exposure = {**common, "id": "company-exposure", "kind": "research-evidence", "symbol": p["symbol"],
        "documentVerified": True, "judgementEvidenceUsable": True, "publishedAt": p["capturedAt"],
        "officialDocumentText": "당사는 변동금리 차입에 노출되어 있으며 일부 차입은 헤지하고 있습니다."}
    p["facts"], p["coverage"] = select_evidence(p["facts"] + [event, exposure])
    p.update(questionPurposePolicy=question_purpose.VERSION, externalInfluencePolicy=influence.VERSION)
    p["questionSourceHandoff"] = question_source_handoff.build(p, [], p["facts"])
    return p


def path():
    return {"category": "interest-rates", "assessment": "conditional",
        "eventEvidence": [ref("value", fact_id="rate-event")],
        "exposureEvidence": [ref("officialDocumentText", fact_id="company-exposure")],
        "transmission": "금리가 변동금리 차입의 비용에 전달될 가능성을 검토합니다.",
        "horizon": "다음 분기 공시에서 차입과 이자비용을 확인합니다.",
        "assumptions": "차입 구조가 유지되고 헤지가 모든 변동분을 상쇄하지 않는다는 가정입니다.",
        "counterEvidence": "일부 헤지와 차입 감소가 이자비용의 변화를 상쇄할 수 있습니다.",
        "missingEvidence": ["변동금리 차입의 규모와 헤지 만기 내역을 확인해야 합니다."], "questionIndex": -1}


def raw_result():
    raw, value = plan(), path()
    raw.update(summary="변동금리 차입이 있어 금리 변화가 이자비용에 전달될 가능성을 검토합니다.",
        hypothesis=value["transmission"], counterEvidence=value["counterEvidence"], questionPurposes=[],
        externalInfluences=[value],
        judgmentChange={"kind": "business", "impact": "new", "reason": "새 차입 원문으로 금리 노출을 확인해 기존 설명을 보완합니다.",
                        "evidence": value["exposureEvidence"]})
    raw["evidenceIds"] += ["rate-event", "company-exposure"]
    raw["claimEvidence"]["summary"] = value["eventEvidence"] + value["exposureEvidence"]
    raw["claimEvidence"]["hypothesis"] = value["eventEvidence"] + value["exposureEvidence"]
    raw["claimEvidence"]["counterEvidence"] = value["exposureEvidence"]
    return raw


def reviewed_result(p, raw=None):
    result = {**validate_plan(raw or raw_result(), p), "input": p, "executionInputId": "author-id",
              "observedAt": p["capturedAt"], "inputFingerprint": "external-source-fingerprint"}
    checked = review()
    checked["judgmentChange"] = {"supported": True, "reason": "기업 원문과 조건부 설명의 연결을 확인했습니다."}
    checked["externalInfluences"] = {"supported": True, "reason": "금리 관측과 기업 차입 원문 및 헤지 한계를 대조했습니다."}
    result["quality"] = accept_review(result, checked, "external-review-id")
    return result


class ExternalInfluenceFlowTests(unittest.TestCase):
    def test_bounded_read_author_repair_and_review_retain_exact_sources(self):
        p, saved = packet(), []
        ids = ["quote-1", "rate-event", "company-exposure"]
        def save(env):
            validate_execution_input(env); saved.append(deepcopy(env)); return "input-" + str(len(saved))
        model = Mock(side_effect=[
            {**scoped_decision("read", ["quote-1", "company-exposure"]), "purpose": "source-dated", "blocker": "none",
             "requests": [{"tool": "read_fact", "category": "macro", "factId": "rate-event"}]},
            {**scoped_decision("finish", ids), "purpose": "source-dated", "blocker": "none"}])
        final, history, research, trace = retrieve_working_evidence(ObservationEvidenceSession(p, p["facts"]),
            p, [], [], model, save, 262144)
        self.assertEqual(2, model.call_count)
        self.assertEqual(reader.VERSION, saved[0]["promptVersion"])
        self.assertEqual("ready", final["retrieval"]["status"])
        result = reviewed_result(final)
        self.assertEqual("accepted", result["quality"]["status"], result["quality"])
        frozen = freeze_execution_input(final, history, research, retrieval_trace=trace)
        self.assertEqual(author.PROMPT_VERSION, frozen["promptVersion"])
        envelopes = [frozen, freeze_repair_input(frozen, raw_result(), ["원문 연결 확인"], "parent"), freeze_review_input(result)]
        for envelope in envelopes:
            original = deepcopy(envelope)
            validate_execution_input(envelope)
            self.assertEqual(original, envelope)
            broken = deepcopy(envelope); broken["current"].pop("externalInfluencePolicy")
            with self.assertRaises(EvidenceContractError): validate_execution_input(broken)
        self.assertEqual("", quality_block(result))
        self.assertEqual("", publication_block(result, datetime.fromisoformat(final["capturedAt"].replace("Z", "+00:00"))))
        from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation
        self.assertIn(result["hypothesis"], render_ai_observation(result))
        altered = deepcopy(result); altered["externalInfluences"][0]["assumptions"] = "검토 이후 전제를 임의로 변경한 잘못된 설명입니다."
        self.assertTrue(quality_block(altered))

    def test_unconfirmed_exposure_stays_limited_and_uses_one_existing_research_route(self):
        p, raw = packet(), raw_result()
        row = raw["externalInfluences"][0]
        row.update(assessment="unconfirmed", exposureEvidence=[], questionIndex=0)
        raw["notification"]["send"] = False
        raw["claimEvidence"]["counterEvidence"] += row["eventEvidence"]
        raw["questions"] = [{"capability": "research", "question": "해당 기업의 변동금리 차입과 헤지 범위가 금리 변화의 영향을 제한하는가?",
            "research": {"queryTerms": ["변동금리 차입 헤지 주석"], "sourceTypes": ["official-filing"], "maxAgeMinutes": 527040}}]
        raw["questionPurposes"] = [{"questionIndex": 0,
            "decisionRelevance": "기업 차입의 금리 민감도에 관한 설명을 구체화합니다.",
            "uncertainty": "차입 구조와 헤지가 금리 전달을 얼마나 제한하는지 미확인입니다.",
            "expectedInsight": "변동금리 노출이 확인되면 비용 경로를 검토하고 헤지가 크면 그 한계를 반영합니다.",
            "evidenceTasks": [{"target": "변동금리 차입과 헤지 내역", "period": "최근 공시 대상 기간", "answerCriterion": "기업의 차입 및 헤지 원문 확인"}],
            "evidenceIds": ["rate-event"]}]
        result = validate_plan(raw, p)
        self.assertEqual(1, len(result["workQuestions"]))
        from digital_twin.modules.ai_orchestration.domain.brain_management import new_case
        result.update(input=p, executionInputId="external-author", observedAt=p["capturedAt"])
        case = new_case({**p, "taskId": "external-task"}, result, raw["questions"][0]["question"], "research", p["capturedAt"])
        self.assertEqual(["rate-event"], case["questionPurpose"]["evidenceIds"])
        bad = deepcopy(raw); bad["notification"]["send"] = True; bad["judgmentChange"]["evidence"] = row["eventEvidence"]
        with self.assertRaises(ValueError): validate_plan(bad, p)
        bad = deepcopy(raw); bad["questionPurposes"][0]["evidenceIds"] = ["quote-1"]
        with self.assertRaises(ValueError): validate_plan(bad, p)

    def test_missing_foreign_reference_and_metadata_cannot_be_company_impact(self):
        p = packet()
        choices = influence.reference_schema(p, influence.EVENT_KINDS)
        variants = choices.get("anyOf", [choices])
        for variant in variants:
            self.assertNotIn("seriesId", variant["properties"]["field"]["enum"])
            self.assertNotIn("quote-1", variant["properties"]["factId"]["enum"])
        for mutation in (
            lambda r: r["externalInfluences"][0].update(exposureEvidence=[]),
            lambda r: r["externalInfluences"][0].update(eventEvidence=[ref("currentPrice")]),
            lambda r: r["externalInfluences"][0]["eventEvidence"][0].update(period="baseline"),
            lambda r: r["externalInfluences"][0]["eventEvidence"][0].update(field="seriesId"),
            lambda r: r["externalInfluences"].append(deepcopy(r["externalInfluences"][0])),
        ):
            bad = raw_result(); mutation(bad)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): validate_plan(bad, p)
        for changes in ({"symbol": "OTHER"}, {"judgementEvidenceUsable": False}):
            bad = deepcopy(p)
            next(f for f in bad["facts"] if f["id"] == "company-exposure").update(changes)
            with self.assertRaises(ValueError): validate_plan(raw_result(), bad)

    def test_review_memory_and_repeat_control_preserve_the_external_explanation(self):
        p, raw = packet(), raw_result()
        result = reviewed_result(p)
        from digital_twin.modules.ai_orchestration.domain.memory_reference import brief
        self.assertEqual(result["externalInfluences"], brief(result, "analyses")["externalInfluences"])
        checked = result["quality"]["review"]
        checked["externalInfluences"]["supported"] = False
        self.assertEqual("rejected", accept_review(result, checked, "other-review")["status"])
        receipt = {"accountId": p["accountId"], "symbol": p["symbol"], "inputFingerprint": result["inputFingerprint"],
                   "deliveredAt": p["capturedAt"], "insightFingerprint": result["quality"]["insightFingerprint"]}
        self.assertTrue(repeat_block(result, [receipt], p["accountId"], p["symbol"], datetime.now(timezone.utc)))
        raw.update(externalInfluences=[])
        self.assertEqual([], validate_plan(raw, p)["externalInfluences"])

    def test_prior_prompt_and_unrelated_subject_wake_remain_unchanged(self):
        old = company_packet()
        frozen = freeze_execution_input(old, [], [])
        before = deepcopy(frozen); validate_execution_input(frozen); self.assertEqual(before, frozen)
        from digital_twin.modules.ai_orchestration.domain.evidence_wake import evidence_wake_targets
        p = packet(); other = {**p, "symbol": "OTHER"}
        event = {"status": "ok", "projectionOutcomes": [{"accountId": p["accountId"], "worldId": p["worldId"],
            "sourceAboxSnapshotId": "new-snapshot", "generationAligned": True, "nativeTypeDbReasoningCompleted": True,
            "alertPipeline": {"targetSymbols": [p["symbol"]]}}]}
        self.assertEqual([p["symbol"]], [r["symbol"] for r in evidence_wake_targets(event, [p, other])])
        event["projectionOutcomes"][0]["nativeTypeDbReasoningCompleted"] = False
        self.assertEqual([], evidence_wake_targets(event, [p, other]))


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class ExternalInfluenceStorageTests(unittest.TestCase):
    def setUp(self):
        from digital_twin.infrastructure.settings import load_local_env
        from mysql_fixtures import mysql_test_settings
        load_local_env()
        isolated = mysql_test_settings()
        environment = patch.dict(os.environ, {"MYSQL_DATABASE": isolated["mysqlDatabase"]})
        environment.start(); self.addCleanup(environment.stop)
        from test_ai_control_publication import CentralPublicationStorageTests
        self.storage = CentralPublicationStorageTests(); self.storage.setUp()
        self.addCleanup(self.storage.tearDown)

    def test_exact_read_author_review_outbox_and_receipt_keep_event_and_exposure(self):
        from test_ai_control import SUBJECT
        from digital_twin.modules.ai_orchestration.domain.planning import stamp
        from digital_twin.modules.ai_orchestration.domain.evidence_purpose import annotate
        from digital_twin.modules.reasoning.contracts import quote_clock_assessment
        from digital_twin.infrastructure.transactions.ai_control_research_knowledge import AIControlResearchKnowledge
        from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
        from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
        from digital_twin.modules.notifications.domain.delivery_suppression import NotificationDeliverySuppressed
        store, queue, publication = self.storage.control, self.storage.queue, self.storage.publication
        store.runtime_settings.update(aiControlBudgetEnabled="false", aiTokenBudgetEnabled="false")
        with store.transaction() as connection:
            store.insert(connection, {**SUBJECT, "taskId": "external-influence-test", "capability": "market-observe"})
        job = store.claim()
        p = {**packet(), **SUBJECT, "taskId": job["taskId"], "capturedAt": stamp()}
        p["sourceSnapshots"] = {p["worldId"]: p["sourceSnapshotId"]}
        for fact in p["facts"]:
            fact.update(symbol=p["symbol"], sourceWorldId=p["worldId"], sourceSnapshotId=p["sourceSnapshotId"])
        p["quoteAssessment"] = quote_clock_assessment(p["facts"], p["capturedAt"])
        p = annotate(p)
        def save(envelope):
            input_id = store.save_execution_input(job, envelope)
            call = store.begin_call("independent-observation", envelope["promptHash"], job["taskId"], input_id)
            store.finish_call(call)
            return input_id
        model = Mock(side_effect=[
            {**scoped_decision("read", ["quote-1", "company-exposure"]), "purpose": "source-dated", "blocker": "none",
             "requests": [{"tool": "read_fact", "category": "macro", "factId": "rate-event"}]},
            {**scoped_decision("finish", ["quote-1", "company-exposure", "rate-event"]), "purpose": "source-dated", "blocker": "none"}])
        final, history, research, trace = retrieve_working_evidence(ObservationEvidenceSession(p, p["facts"]),
            p, [], [], model, save, 262144, record_round=lambda step: store.save_retrieval_round(job, step))
        result = reviewed_result(final)
        envelope = freeze_execution_input(final, history, research, retrieval_trace=trace)
        result.update(executionInputId=save(envelope), executionPromptVersion=envelope["promptVersion"],
                      retrievalInputIds=[step["inputId"] for step in trace])
        with store.connect() as connection:
            self.assertEqual(trace, AIControlResearchKnowledge.read_proof(connection, job, result))
            altered = deepcopy(result); altered["input"].pop("externalInfluencePolicy")
            with self.assertRaises(ValueError): AIControlResearchKnowledge.read_proof(connection, job, altered)
        review_id = save(freeze_review_input(result))
        result["quality"] = accept_review(result, result["quality"]["review"], review_id)
        self.assertTrue(store.complete(job, result, []))
        self.assertEqual("queued", result["publication"]["status"], result["publication"])
        with queue.connect() as connection:
            row = connection.execute("SELECT text,payload_json FROM notification_jobs WHERE job_id=%s",
                                     (result["publication"]["jobId"],)).fetchone()
        notice = queue.job_from_row(row)
        self.assertEqual({}, publication.memory(notice.account_id, SUBJECT["symbol"]))
        message = NotificationRenderingService().render(notice)
        notifier = Mock(supports_delivery_checkpoints=False)
        notifier.send.return_value = SimpleNamespace(delivered=True, label="test", reason="", metadata={})
        dispatcher = NotificationDispatchService(queue, lambda _: notifier, delivery_guard=publication.delivery_guard)
        with patch("digital_twin.infrastructure.settings.runtime_settings", return_value=self.storage.settings):
            dispatcher.deliver(notice, {notice.account_id: object()}, message)
            with self.assertRaises(NotificationDeliverySuppressed):
                dispatcher.deliver(notice, {notice.account_id: object()}, message)
        notifier.send.assert_called_once()
        receipt = publication.memory(notice.account_id, SUBJECT["symbol"])
        self.assertEqual(result["externalInfluences"], receipt["externalInfluences"])
        self.assertEqual(notice.job_id, receipt["jobId"])
