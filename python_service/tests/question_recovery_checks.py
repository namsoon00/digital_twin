"""Rehearse source linkage, finite follow-up, transaction rollback and delivery intent."""
from copy import deepcopy
from unittest.mock import Mock, patch
import hashlib
import json


def check_source_references(test):
    from digital_twin.modules.news_intelligence.domain.financial_reporting import bind_financial_report_contract, FINANCIAL_REPORTING_VERSION
    from digital_twin.modules.news_intelligence.domain.financial_research_evidence import financial_research_evidence
    from digital_twin.modules.news_intelligence.domain.question_source_reference import question_source_reference
    from digital_twin.modules.news_intelligence.domain.investment_research import research_evidence_from_payload
    from digital_twin.modules.reasoning.domain.ontology_projection_input import compact_research_evidence_item
    from digital_twin.modules.reasoning.domain.observation_session import ObservationEvidenceSession
    from test_business_research import business_packet
    row = bind_financial_report_contract({"period": "2026-06-30", "periodEnd": "2026-06-30",
        "frequency": "quarterly", "provider": "SEC EDGAR", "financialReportingVersion": FINANCIAL_REPORTING_VERSION,
        "operatingCashFlow": 120, "officialSource": True, "metricProvenance": {"operatingCashFlow": {
            "provider": "SEC EDGAR", "period": "2026-06-30", "currency": "USD", "scope": "CFS",
            "durationBasis": "quarterly", "filed": "2026-08-01", "official": True}}},
        [{"datasetId": "sec.company_facts", "revisionId": "original-revision", "subjectKey": "TEST", "fetchedAt": "2026-10-18T00:00:00Z"}])
    item = financial_research_evidence("TEST", {"financials": {"quarterly": [row]}})[0]
    item.raw_payload["evidenceLifecycleState"] = "active"
    ref = question_source_reference(item)
    test.assertEqual("structured-report", ref["sourceKind"])
    restored = research_evidence_from_payload(compact_research_evidence_item(item.to_dict()))
    test.assertEqual(ref, question_source_reference(restored))
    test.assertEqual(item.raw_payload["financialReport"], restored.raw_payload["financialReport"])
    from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
    from digital_twin.modules.reasoning.domain.ontology_schema import add_entity
    from digital_twin.modules.reasoning.domain.portfolio_ontology_research_concepts import add_research_evidence_concepts
    from digital_twin.modules.reasoning.domain.projection_facts import graph_for_graph_store_persistence
    graph = PortfolioOntology("test")
    stock = add_entity(graph, "stock", "TEST", "Test", {"symbol": "TEST", "tboxClass": "Stock"})
    add_research_evidence_concepts(graph, stock, "", "", "TEST", {}, {"researchEvidence": {"TEST": [restored.to_dict()]}})
    node = next(n for n in graph.entities if n.kind == "research-evidence")
    test.assertEqual(ref, node.properties["questionSourceReference"])
    test.assertIn(node.entity_id, {n.entity_id for n in graph_for_graph_store_persistence(graph, {"inputRelationTypes": ["HAS_POSITION"]}).entities})
    packet = business_packet()
    fact = {**packet["facts"][-1], "questionSourceReference": ref}
    session = ObservationEvidenceSession(packet, [*packet["facts"][:-1], fact])
    index = session.question_document_index({item.evidence_id: ["question"]})
    test.assertEqual("structured-report", index[0]["sourceKind"])
    test.assertIn("전문", index[0]["limitation"])
    test.assertEqual([], session.question_document_index({item.evidence_id + "-newer": ["question"]}))
    for field, value in (("reportObservationId", "different"), ("evidenceLifecycleState", "retracted")):
        broken = deepcopy(item); broken.raw_payload[field] = value
        test.assertEqual({}, question_source_reference(broken))

    from test_research_judgment_continuity import document
    from digital_twin.modules.news_intelligence.domain.research_document_reference import research_document_reference
    issuer = document(); issuer.kind = "issuer-ir"; issuer.url = "https://issuer.example/release"
    raw = issuer.raw_payload; raw["publicationProof"] = {"kind": "original-publication-metadata", "value": issuer.published_at}
    raw["documentIssuerIdentity"] = {"symbol": "TEST", "sourceUrl": issuer.url,
        "verification": "registered-issuer-source", "registryUrls": ["https://issuer.example"]}
    issuer.evidence_id = "research:TEST:explored:" + hashlib.sha256((issuer.url + raw["documentHash"] + raw["officialDocumentText"]).encode()).hexdigest()[:24]
    ref = research_document_reference(issuer)
    test.assertEqual("dated-issuer-document", ref["purpose"])
    test.assertEqual("", ref["periodEnd"], "publication time must not manufacture a report period")
    test.assertEqual(ref, research_document_reference(research_evidence_from_payload(compact_research_evidence_item(issuer.to_dict()))))
    for field, value in (("officialDocumentText", "invented"), ("publicationProof", {}), ("evidenceLifecycleState", "retracted")):
        broken = deepcopy(issuer); broken.raw_payload[field] = value
        test.assertEqual({}, research_document_reference(broken))
    return item, issuer


def check_review_dispatch(test):
    from ai_insight_fixtures import packet, plan
    from test_ai_control import AIControlTests
    from test_ai_brain_agenda import review_case
    from question_resolution_checks import resolution
    from digital_twin.modules.ai_orchestration.domain import question_evidence, research_review
    from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input
    p = packet(); p["questionEvidencePolicy"] = question_evidence.VERSION
    q = {**{key: p[key] for key in ("accountId", "symbol", "worldId")}, "kind": "brain-case",
         "caseId": "question", "capability": "research", "revision": 1, "reviewDue": True}
    raw = {**plan(), "questions": [], "caseReviews": [review_case(q, "answered")],
           "questionResolutions": [resolution(q, "not-applicable", -1)]}
    raw["notification"]["send"] = False
    for market_status in ("accepted", "observation-only"):
        service, store, planner = AIControlTests().runner(evidence=Mock(return_value=deepcopy(p)),
            research_memory=Mock(return_value=[q]))
        def run(envelope, _):
            validate_execution_input(envelope)
            if envelope["promptVersion"] == research_review.VERSION:
                test.assertEqual("answered", envelope["proposal"]["caseReviews"][0]["action"])
                return {"version": research_review.VERSION, "accepted": True,
                        "reason": "질문의 원래 범위와 답변 근거를 시장 설명과 별도로 확인했습니다."}
            return deepcopy(raw)
        planner.side_effect = run
        with patch("digital_twin.modules.ai_orchestration.application.control.local_quality",
                   return_value={"status": market_status, "errors": []}):
            test.assertEqual("completed", service.run_once()["status"])
        test.assertEqual("accepted", store.complete.call_args.args[1]["researchQuality"]["status"])
        test.assertEqual(2, planner.call_count, "market acceptance must not bypass question answer review")


def check_finite_followup(test):
    from test_question_evidence_flow import question
    from test_business_research import business_packet
    from digital_twin.modules.ai_orchestration.domain import research_repeat as repeat, question_recovery as recovery
    from digital_twin.modules.ai_orchestration.domain.planning import stamp
    q = question(business_packet()); repeat.initialize(q); repeat.remember(q, "planned"); repeat.remember(q, "scheduled")
    test.assertEqual("unchanged-research-material", repeat.admission(q, "planned"))
    q["evidenceProgress"] = {"version": recovery.EVIDENCE_VERSION, "status": "waiting", "executionInputId": "verified-read",
        "missingRequirements": ["원래 기간의 현금흐름표 주석"], "progressKey": "read-one"}
    test.assertEqual("", repeat.admission(q, "planned"))
    test.assertTrue(repeat.planning_due(q, stamp()), "a new verified gap must not wait for a distant timer")
    key = recovery.gap_key(q); repeat.remember(q, "planned")
    q["evidenceProgress"].update(missingRequirements=["표현만 바뀐 부족분"], progressKey="another-selection")
    test.assertEqual("unchanged-research-material", repeat.admission(q, "planned"))
    test.assertEqual("unchanged-research-material", repeat.admission(q, "scheduled"), "a read alone cannot fund source collection")
    q["researchPlan"] = {"action": "research", "gapRecoveryKey": key}
    test.assertEqual("", repeat.admission(q, "scheduled"))
    repeat.remember(q, "scheduled", "followup")
    test.assertEqual("unchanged-research-material", repeat.admission(q, "scheduled"))
    q["researchSourceState"] = {"status": "ready", "fingerprints": ["new-source"]}
    test.assertEqual("", repeat.admission(q, "planned"))
    q["researchAttempts"] = 6
    test.assertEqual("research-attempt-limit", repeat.admission(q, "planned"))


def check_recovery_transaction(test):
    from test_ai_brain_agenda import BrainAgendaStorageTests
    from test_research_work import answer, CONTRACT, REQUEST, plan
    from digital_twin.modules.ai_orchestration.domain import research_repeat as repeat, research_work as work, question_recovery as recovery
    from digital_twin.modules.ai_orchestration.domain.planning import stamp
    from digital_twin.modules.ai_orchestration.domain.brain_management import later
    from digital_twin.modules.ai_orchestration.infrastructure.mysql_research_work import reconcile_and_schedule
    fixture = BrainAgendaStorageTests(); fixture.setUp()
    try:
        job, result, children = fixture.observation(); fixture.control.complete(job, result, children)
        source = fixture.control.claim()
        fixture.control.complete(source, {"status": "completed", "documentaryAnswer": answer(source["taskId"])}, [])
        def state():
            with fixture.brain.connect() as c:
                case = json.loads(c.execute("SELECT payload_json FROM ai_brain_cases WHERE case_id=%s", (source["brainCaseId"],)).fetchone()["payload_json"])
                task = c.execute("SELECT available_at FROM ai_control_tasks WHERE task_id=%s", (children[0]["taskId"],)).fetchone()
            return case, task["available_at"]
        q, due = state()
        with test.assertRaises(RuntimeError):
            with fixture.brain.transaction() as c:
                reconcile_and_schedule(fixture.brain, c, [fixture.subject]); raise RuntimeError("rollback")
        test.assertEqual((q, due), state())
        with fixture.brain.transaction() as c: reconcile_and_schedule(fixture.brain, c, [fixture.subject])
        first, first_due = state(); test.assertTrue(first["answerReviewWake"]["key"])
        with fixture.brain.transaction() as c: reconcile_and_schedule(fixture.brain, c, [fixture.subject])
        test.assertEqual((first, first_due), state(), "polling must not create repeat review wakes")
        # A fresh source-owned read opens exactly one original-case follow-up.
        q.update(answerResolution={"status": "partial"}, questionContract=CONTRACT,
            nextCheckAt=later(stamp(), 1440), researchAttempts=2, researchRequest=REQUEST,
            evidenceProgress={"version": recovery.EVIDENCE_VERSION, "status": "waiting", "executionInputId": "stored-read",
                              "missingRequirements": ["공식 현금흐름표 주석"]})
        q["lastResearch"]["requestedAt"] = "2020-01-01T00:00:00Z"
        q["latestSourceAnswer"] = answer(source["taskId"], "partial")
        repeat.initialize(q); repeat.remember(q, "planned")
        q["researchRepeat"].pop("gapRecovery", None)
        with fixture.brain.transaction() as c: fixture.brain.save(c, q, "gap", "research-evidence-progress")
        with fixture.brain.transaction() as c:
            test.assertEqual(1, reconcile_and_schedule(fixture.brain, c, [fixture.subject])["plansScheduled"])
        task = fixture.control.claim()
        if task["capability"] == "observe":
            fixture.control.complete(task, {"status": "unchanged"}, [])
            task = fixture.control.claim()
        test.assertEqual("research-plan", task["capability"])
        memories = [x for x in fixture.brain.memory(*[task[k] for k in ("accountId", "symbol", "worldId")], research_only=True) if x.get("kind") == "brain-case"]
        for x in memories: x["planDue"] = repeat.planning_due(x, stamp())
        envelope = work.freeze(task, [repeat.planning_memory(x) for x in memories])
        input_id = fixture.control.save_execution_input(task, envelope)
        call = fixture.control.begin_call("research-planning", envelope["promptHash"], task["taskId"], input_id); fixture.control.finish_call(call)
        raw = plan(memories[0], "research"); raw["reviews"][0]["research"] = {**REQUEST, "queryTerms": ["현금흐름표 운전자본 주석 비교"]}
        completed = {"status": "research-planned", "executionInputId": input_id, "researchPlan": work.validate_plan(raw, envelope)}
        progress = Mock(); fixture.brain.progress_writer = progress
        test.assertTrue(fixture.control.complete(task, completed, []))
        notice = progress.call_args.args[1]
        test.assertEqual("부족한 자료 추가 조사 예약", notice["label"])
        test.assertEqual(["공식 현금흐름표 주석"], notice["sourceFollowUp"]["missingRequirements"])
        from digital_twin.modules.notifications.application.research_progress_message import render_research_progress
        from digital_twin.modules.ai_orchestration.domain.research_progress import research_progress
        test.assertIn("답변 확보나 검증 완료를 뜻하지 않습니다", render_research_progress(notice))
        test.assertIn("공식 현금흐름표 주석", render_research_progress(notice))
        queued = fixture.control.claim(); test.assertEqual("research", queued["capability"])
        test.assertEqual(source["brainCaseId"], queued["brainCaseId"])
        test.assertEqual(["공식 현금흐름표 주석"], queued["evidenceRequirements"])
        after, _ = state(); test.assertEqual(3, after["researchAttempts"])
        test.assertIsNone(research_progress(after, {"stage": "research-planned", "details": after["researchPlan"]}),
                          "the same scheduled follow-up must not announce twice")
        test.assertEqual(CONTRACT, after["questionContract"])
        test.assertEqual("unchanged-research-material", repeat.admission(after, "scheduled"))
    finally:
        fixture.tearDown()
