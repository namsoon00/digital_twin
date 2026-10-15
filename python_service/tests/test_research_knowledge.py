"""Research content, graph identity, frozen lineage and safe reuse contracts."""
import copy
import unittest

from ai_insight_fixtures import packet, plan
from test_business_research import business_packet, thesis, raw_research
from digital_twin.modules.research_knowledge.domain.frameworks import framework_catalog, service_research_policy
from digital_twin.modules.research_knowledge.domain.context import build_context, context_material_identity
from digital_twin.modules.research_knowledge.domain.knowledge import validate_proposals, raw_proposals, revise_claim, digest
from digital_twin.modules.research_knowledge.domain.assessments import assessment_for_claim
from digital_twin.modules.research_knowledge.domain.events import knowledge_revised_event
from digital_twin.modules.reasoning.domain.research_knowledge_projection import research_knowledge_projection
from digital_twin.modules.model_registry.contracts import tbox_class_def, tbox_relation_def
from digital_twin.modules.ai_orchestration.domain.knowledge_research import knowledge_schema, validate_research
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan, observation_fingerprint
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, validate_execution_input,
    QUESTION_PROMPT_VERSION, QUESTION_REPAIR_PROMPT_VERSION)


def methods():
    return [{**row, "knownAt": "2026-01-01T00:00:00Z", "contentHash": digest(row)} for row in framework_catalog()]


def context(p=None, records=()):
    p = p or business_packet()
    return build_context(p, methods(), records, usable_ids=[row["id"] for row in p["facts"]])


def proposal(**changes):
    row = {"frameworkId": "growth-cash-conversion-v1", "claimId": "", "disposition": "create",
        "question": "매출 확대가 현금 회수의 개선으로 이어지는가?",
        "mechanism": "판매 확대가 현금 회수로 이어지는지 다음 공시로 확인합니다.",
        "assumption": "성장이 외상 판매의 증가만으로 발생하지 않아야 합니다.",
        "alternative": "매출채권 증가가 현금 회수를 지연했을 가능성이 있습니다.",
        "invalidation": "동일 기간 현금 회수의 악화가 확인되면 설명을 재검토합니다.",
        "reason": "원래 사업 질문의 현금 연결을 확인하기 위해 연구합니다.",
        "evidenceIds": ["report-2025"], "counterEvidenceIds": [],
        "missingEvidence": ["동일 기간의 현금흐름과 매출채권 자료가 필요합니다."],
        "businessThesisIndex": -1, "reviewAfterDays": 7}
    row.update(changes)
    return {"claims": [row], "coverageNote": "매출의 근거는 있으나 현금 회수와 투자 지출을 더 확인해야 합니다."}


def registered(p=None):
    p = p or business_packet()
    c = context(p)
    validated = validate_proposals(proposal(), c, p, {row["id"] for row in p["facts"]})
    return revise_claim(None, validated["claims"][0], p, "input-origin", "2026-03-02T00:00:00Z")


class ResearchKnowledgeTests(unittest.TestCase):
    def test_seed_content_covers_three_purposes_with_conditions_and_methodology_sources(self):
        rows = framework_catalog()
        self.assertEqual({"company-value", "opportunity", "portfolio-risk"}, {row["purpose"] for row in rows})
        self.assertEqual(3, len(service_research_policy()["purposes"]))
        for row in rows:
            self.assertTrue(row["mechanism"] and row["applicability"] and row["alternatives"] and row["limits"])
            self.assertTrue(all(ref["role"] == "methodology" and ref["url"].startswith("https://") for ref in row["references"]))
        rows[0]["questions"].clear()
        self.assertTrue(framework_catalog()[0]["questions"])

    def test_scope_cutoff_and_zero_are_preserved_without_answer_promotion(self):
        p = business_packet()
        p["facts"][-1]["reportedValues"]["revenue"] = 0
        c = context(p)
        revenue = c["frameworks"][0]["coverage"][0]
        self.assertEqual("present-needs-review", revenue["status"])
        self.assertEqual("availability-only", revenue["qualification"])
        self.assertTrue(any(item["status"] == "missing" for item in c["frameworks"][0]["coverage"]))
        self.assertEqual([], build_context(p, [{**row, "knownAt": "2027-01-01T00:00:00Z"} for row in methods()], []) ["frameworks"])
        previous = registered()
        self.assertEqual([], context(p, [previous])["claims"])
        later = {**p, "capturedAt": "2026-03-03T00:00:00Z"}
        self.assertEqual(1, len(context(later, [previous])["claims"]))
        with self.assertRaises(ValueError):
            context(later, [{**previous, "accountId": "another-account"}])

    def test_exact_evidence_scope_and_declared_gaps_are_required(self):
        p = business_packet(); c = context(p); usable = {row["id"] for row in p["facts"]}
        validated = validate_proposals(proposal(), c, p, usable)
        self.assertEqual(proposal(), raw_proposals(validated))
        for bad in (proposal(evidenceIds=["invented"]),
                    proposal(evidenceIds=[], missingEvidence=[]), proposal(businessThesisIndex=0),
                    proposal(mechanism="성장률 99가 반드시 지속됩니다.")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_proposals(bad, c, p, usable)
        with self.assertRaises(ValueError):
            validate_proposals(proposal(), {**c, "worldId": "other-world"}, p, usable)
        with self.assertRaises(ValueError):
            validate_proposals(proposal(), c, p, set())
        future = copy.deepcopy(p); future["facts"][-1]["publishedAt"] = "2027-01-01T00:00:00Z"
        with self.assertRaises(ValueError):
            validate_proposals(proposal(), c, future, usable)
        # One filing may contain both growing revenue and cash-flow concerns.
        # Preserve both roles while retaining one independent source reference.
        mixed = validate_proposals(proposal(counterEvidenceIds=["report-2025"]), c, p, usable)
        record = revise_claim(None, mixed["claims"][0], p, "mixed-report", "2026-03-02T00:00:00Z")
        self.assertEqual(1, len(record["evidence"]))
        self.assertEqual(["supports", "contradicts"], record["evidence"][0]["roles"])
        self.assertTrue(record["evidence"][0]["independenceKey"])
        graph = research_knowledge_projection(context({**p, "capturedAt": "2026-03-03T00:00:00Z"}, [record]))
        links = [row for row in graph["relations"] if row["type"] in {"SUPPORTED_BY", "CONTRADICTS"}]
        self.assertEqual(2, len(links)); self.assertEqual(links[0]["target"], links[1]["target"])

    def test_revisions_preserve_parent_and_reject_stale_updates(self):
        first = registered()
        p = {**business_packet(), "capturedAt": "2026-03-03T00:00:00Z"}
        c = context(p, [first])
        raw = proposal(claimId=first["claimId"], disposition="revise", reason="현금 회수의 자료가 부족하여 조건을 명확히 하고 다시 확인합니다.")
        validated = validate_proposals(raw, c, p, {row["id"] for row in p["facts"]})
        second = revise_claim(first, validated["claims"][0], p, "input-next", "2026-03-04T00:00:00Z")
        self.assertEqual(first["revisionId"], second["previousRevisionId"])
        self.assertEqual(first["revision"] + 1, second["revision"])
        self.assertEqual(1, first["revision"])
        with self.assertRaises(ValueError):
            revise_claim(second, validated["claims"][0], p, "stale-input", "2026-03-05T00:00:00Z")
        self.assertEqual(first["revisionId"], context(p, [first, second])["claims"][0]["revisionId"])
        updated_methods = methods()
        updated_methods[0]["version"] = first["frameworkVersion"] + 1
        updated_methods[0]["contentHash"] = digest(updated_methods[0])
        changed_method = build_context(p, updated_methods, [first], usable_ids=[row["id"] for row in p["facts"]])
        with self.assertRaises(ValueError):
            validate_proposals(proposal(claimId=first["claimId"], disposition="retain"), changed_method, p, {row["id"] for row in p["facts"]})
        graph = research_knowledge_projection(changed_method)
        by_id = {row["id"]: row for row in graph["entities"]}
        claim_node = next(row for row in graph["entities"] if row["kind"] == "research-knowledge-claim")
        link = next(row for row in graph["relations"] if row["source"] == claim_node["id"] and row["type"] == "USES_RESEARCH_FRAMEWORK")
        self.assertEqual(first["frameworkVersion"], by_id[link["target"]]["properties"]["frameworkVersion"])

    def test_failed_metric_creates_scoped_unadopted_method_proposal_without_causal_qualification(self):
        claim = {**registered(), "businessThesisId": "thesis"}
        memory = [{"caseId": "thesis", "observations": [{"status": "direction-not-observed", "metric": "revenue"}]}]
        assessed = assessment_for_claim(claim, memory)
        self.assertEqual("checkpoint-not-observed", assessed["status"])
        self.assertEqual("not-established", assessed["causalValidation"])
        self.assertFalse(assessed["methodProposal"]["adopted"])
        self.assertEqual(claim["claimId"], assessed["methodProposal"]["applicability"]["claimId"])
        self.assertEqual(assessed, assessment_for_claim(claim, memory))

    def test_own_rephrasing_cannot_wake_research_but_new_method_or_observation_can(self):
        first = registered(); p = {**business_packet(), "capturedAt": "2026-03-03T00:00:00Z"}
        c = context(p, [first]); changed = copy.deepcopy(c)
        changed["claims"][0].update(revision=99, revisionId="new", reason="표현만 고친 설명입니다.")
        self.assertEqual(context_material_identity(c), context_material_identity(changed))
        self.assertEqual(observation_fingerprint({**p, "researchKnowledge": c}, []),
                         observation_fingerprint({**p, "researchKnowledge": changed}, []))
        changed["claims"][0]["assessment"]["assessmentId"] = "independent-observation"
        self.assertNotEqual(context_material_identity(c), context_material_identity(changed))
        changed = copy.deepcopy(c); changed["policy"]["id"] = "new-research-purpose-policy"
        self.assertNotEqual(context_material_identity(c), context_material_identity(changed))

    def test_canonical_graph_has_closed_scoped_edges_and_zero_action_authority(self):
        first = registered(); p = {**business_packet(), "capturedAt": "2026-03-03T00:00:00Z"}
        graph = research_knowledge_projection(context(p, [first]))
        nodes = {row["id"] for row in graph["entities"]}
        for row in graph["entities"]:
            self.assertIsNotNone(tbox_class_def(row["properties"]["tboxClass"]))
            self.assertFalse(row["properties"]["reasoningEligible"])
            self.assertFalse(row["properties"]["investmentJudgmentEligible"])
        for row in graph["relations"]:
            self.assertIn(row["source"], nodes); self.assertIn(row["target"], nodes)
            self.assertIsNotNone(tbox_relation_def(row["type"]))
        self.assertEqual("none", graph["actionAuthority"])
        event = knowledge_revised_event(first)
        self.assertEqual(event.event_id, knowledge_revised_event(first).event_id)
        self.assertNotIn("mechanism", event.payload)

    def test_ai_input_output_and_old_prompt_replay_remain_separate(self):
        p = business_packet(); p["researchKnowledge"] = context(p)
        raw = {**plan(), "businessResearch": raw_research(), "questionResolutions": [], "knowledgeResearch": proposal(), "followUpConditions": []}
        validated = validate_plan(raw, p)
        self.assertEqual("company-value", validated["knowledgeResearch"]["claims"][0]["purpose"])
        self.assertIn("knowledgeResearch", knowledge_schema(p, [])["required"])
        frozen = freeze_execution_input(p, [], [])
        validate_execution_input(frozen)
        self.assertIn("성장의 질", frozen["prompt"])
        repaired = freeze_repair_input(frozen, raw, ["명확한 반론이 필요합니다."], "input-original")
        validate_execution_input(repaired)
        self.assertEqual(frozen["current"]["researchKnowledge"], repaired["current"]["researchKnowledge"])
        from digital_twin.modules.ai_orchestration.domain.question_resolution import resolution_prompt, resolution_schema
        from digital_twin.modules.ai_orchestration.domain.insight_repair import repair_prompt
        import hashlib
        old = freeze_execution_input(business_packet(), [], [])
        for version in (QUESTION_PROMPT_VERSION, QUESTION_REPAIR_PROMPT_VERSION):
            legacy = copy.deepcopy(old); legacy["promptVersion"] = version
            legacy["prompt"] = resolution_prompt(legacy["current"], legacy["previousAnalyses"], legacy["researchResults"])
            legacy["outputSchema"] = resolution_schema(legacy["current"], legacy["researchResults"])
            if version == QUESTION_REPAIR_PROMPT_VERSION:
                legacy["repair"] = {"errors": [], "rejectedDraft": plan(), "parentInputId": "origin", "comparisons": []}
                legacy["prompt"] = repair_prompt(legacy["prompt"], legacy["repair"])
            legacy["promptHash"] = hashlib.sha256(legacy["prompt"].encode()).hexdigest()
            validate_execution_input(legacy)

    def test_ai_loop_receives_methods_and_independently_reviews_knowledge_even_without_notification(self):
        from unittest.mock import Mock
        from test_ai_control import AIControlTests
        from digital_twin.modules.ai_orchestration.domain import research_review
        p = business_packet()
        subject = {key: p[key] for key in ("accountId", "symbol", "worldId")}
        service, store, planner = AIControlTests().runner(subjects=lambda: [subject],
            evidence=Mock(return_value=copy.deepcopy(p)), knowledge_context=lambda packet: context(packet))
        store.claim.return_value.update(subject)
        raw = {**plan(), "businessResearch": raw_research(), "questionResolutions": [],
               "knowledgeResearch": proposal(), "followUpConditions": [], "observations": []}
        raw["notification"]["send"] = False
        def run(envelope, _):
            validate_execution_input(envelope)
            if envelope["promptVersion"] == research_review.VERSION:
                self.assertIn("knowledgeResearch", envelope["proposal"])
                return {"version": research_review.VERSION, "accepted": True, "reason": "연구 방법과 근거 연결이 가정·반론·자료 부족을 함께 보존합니다."}
            self.assertEqual(3, len(envelope["current"]["researchKnowledge"]["frameworks"]))
            return copy.deepcopy(raw)
        planner.side_effect = run
        self.assertEqual("completed", service.run_once()["status"])
        self.assertEqual("accepted", store.complete.call_args.args[1]["researchQuality"]["status"])
        self.assertEqual(2, planner.call_count)

    def test_knowledge_budget_is_independent_and_omissions_never_mean_refutation(self):
        from digital_twin.modules.reasoning.contracts import validate_evidence_packet, EvidenceContractError
        from digital_twin.modules.reasoning.domain.observation_evidence import canonical_json
        p = business_packet(); p["sourceAuditPadding"] = "x" * 88000
        self.assertLess(len(canonical_json(p).encode()), 96000)
        p["researchKnowledge"] = context(p)
        self.assertGreater(len(canonical_json(p).encode()), 96000)
        validate_evidence_packet(p)
        p["researchKnowledge"]["policy"]["mission"] = "tampered"
        with self.assertRaises(EvidenceContractError):
            validate_evidence_packet(p)
        first = registered(); later = {**business_packet(), "capturedAt": "2026-03-03T00:00:00Z"}
        later["facts"] = [row for row in later["facts"] if row["id"] != "report-2025"]
        selected = context(later, [first])["claims"][0]
        self.assertEqual("not-in-selected-input", selected["evidenceState"][0]["state"])
        self.assertEqual("tracking", selected["status"])
