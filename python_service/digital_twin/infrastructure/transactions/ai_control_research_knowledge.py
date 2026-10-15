"""Frozen AI research handoff; the control transaction owns commit/rollback."""
from digital_twin.modules.research_knowledge.contracts import raw_knowledge_proposals
from digital_twin.modules.research_knowledge.infrastructure.transaction_writes import write_research
from digital_twin.modules.ai_orchestration.domain.knowledge_research import validate_research
from digital_twin.modules.ai_orchestration.domain.research_evidence_use import usable_fact
from digital_twin.modules.ai_orchestration.domain.business_research import thesis_case
from digital_twin.modules.research_knowledge.domain.knowledge import stamp, digest
from digital_twin.modules.research_knowledge.domain.context import build_context
from digital_twin.modules.research_knowledge.domain.assessments import assessment_for_claim


class AIControlResearchKnowledge:
    def __init__(self, service, agenda, projector):
        self.service, self.agenda, self.projector = service, agenda, projector

    def context(self, packet):
        usable = {row["id"] for row in packet["facts"] if usable_fact(row)}
        context = self.service.context(packet, usable)
        context["ontology"] = self.projector(context)
        return context

    def refresh(self, captured, packet):
        usable = {row["id"] for row in packet["facts"] if usable_fact(row)}
        context = build_context(packet, captured["frameworks"], captured["claims"], usable_ids=usable)
        for claim in context["claims"]:
            claim["currentAssessment"] = assessment_for_claim(claim, packet.get("businessThesisMemory", []))
        context["contextHash"] = digest({key: value for key, value in context.items() if key != "contextHash"})
        context["ontology"] = self.projector(context)
        return context

    def record(self, connection, job, result):
        if not result.get("knowledgeResearch"):
            return {"status": "not-requested"}
        envelope = self.agenda.proof(connection, job, result)
        reviewed = self.agenda.research_proof(connection, job, result, envelope)
        if not reviewed and result["knowledgeResearch"]["claims"]:
            return {"status": "review-required"}
        theses = (result.get("businessResearch") or {}).get("theses", [])
        proposals = validate_research(raw_knowledge_proposals(result["knowledgeResearch"]), envelope["current"], theses)
        if proposals != result["knowledgeResearch"]:
            raise ValueError("knowledge research changed after validation")
        now = stamp()
        business_ids = [thesis_case(job, result, thesis, now)["caseId"] for thesis in theses]
        receipt = write_research(connection, result["input"], proposals, result["executionInputId"], now, business_ids)
        from digital_twin.infrastructure.mysql_operational_events import insert_domain_event_with_connection
        for event in receipt.pop("events"):
            insert_domain_event_with_connection(connection, event)
        return receipt
