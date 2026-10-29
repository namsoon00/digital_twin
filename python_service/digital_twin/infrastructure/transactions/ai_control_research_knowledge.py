"""Frozen AI research handoff; the control transaction owns commit/rollback."""
import gzip
import json
from digital_twin.modules.research_knowledge.contracts import raw_knowledge_proposals
from digital_twin.modules.research_knowledge.infrastructure.transaction_writes import write_research, write_progress
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
        context = build_context(packet, captured["frameworks"], captured["claims"], usable_ids=usable, progress=captured.get("progress", []))
        for claim in context["claims"]:
            claim["currentAssessment"] = assessment_for_claim(claim, packet.get("businessThesisMemory", []))
        context["contextHash"] = digest({key: value for key, value in context.items() if key != "contextHash"})
        context["ontology"] = self.projector(context)
        return context

    def record(self, connection, job, result):
        from digital_twin.infrastructure.mysql_operational_events import insert_domain_event_with_connection
        if not result.get("summary"):
            trace = self.read_proof(connection, job, result)
            receipt = write_progress(connection, result["input"], trace, stamp())
            questions = self.question_progress(connection, job, result["input"], trace)
            for event in receipt.pop("events", []):
                insert_domain_event_with_connection(connection, event)
            return {"status": "progress-only", "progress": receipt, **questions}
        if not result.get("knowledgeResearch"):
            return {"status": "not-requested"}
        envelope = self.agenda.proof(connection, job, result)
        progress = write_progress(connection, result["input"], envelope.get("retrievalTrace", []), stamp())
        questions = self.question_progress(connection, job, result["input"], envelope.get("retrievalTrace", []))
        for event in progress.pop("events", []):
            insert_domain_event_with_connection(connection, event)
        reviewed = self.agenda.research_proof(connection, job, result, envelope, section="knowledge")
        if not reviewed and result["knowledgeResearch"]["claims"]:
            return {"status": "review-required", "progress": progress, **questions}
        theses = (result.get("businessResearch") or {}).get("theses", [])
        proposals = validate_research(raw_knowledge_proposals(result["knowledgeResearch"]), envelope["current"], theses)
        if proposals != result["knowledgeResearch"]:
            raise ValueError("knowledge research changed after validation")
        now = stamp()
        business_ids = [thesis_case(job, result, thesis, now)["caseId"] for thesis in theses]
        review_receipt = None
        if reviewed:
            from digital_twin.modules.reasoning.contracts import content_hash
            quality = result["researchQuality"]
            from digital_twin.modules.ai_orchestration.domain.research_review_parts import reason
            frozen = connection.execute("SELECT input_hash FROM ai_control_inputs WHERE input_id=%s", (quality["reviewInputId"],)).fetchone()
            review_receipt = {"version": quality["version"], "status": "accepted", "authorInputId": result["executionInputId"],
                "authorInputHash": content_hash(envelope), "reviewInputId": quality["reviewInputId"], "reviewInputHash": frozen["input_hash"],
                "proposalHash": quality["proposalHash"], "reason": reason(quality, "knowledge"),
                "authority": "research-assessment-only", "qualification": "not-empirically-qualified"}
        receipt = write_research(connection, result["input"], proposals, result["executionInputId"], now, business_ids, review_receipt)
        for event in receipt.pop("events"):
            insert_domain_event_with_connection(connection, event)
        receipt["progress"] = progress
        receipt.update(questions)
        return receipt

    def question_progress(self, connection, job, packet, trace):
        from digital_twin.modules.ai_orchestration.domain.question_evidence import VERSION
        from digital_twin.modules.ai_orchestration.domain.execution_lanes import market
        if market(packet):
            return {}  # Market reads cannot update original-question ownership.
        if packet.get("questionEvidencePolicy") != VERSION:
            return {}  # Historical frozen input remains unchanged.
        return {"questions": self.agenda.record_evidence_progress(connection, job, packet, trace)}

    @staticmethod
    def read_proof(connection, job, result):
        from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input
        from digital_twin.modules.ai_orchestration.domain import scoped_retrieval, working_retrieval, indexed_retrieval, question_retrieval, lane_retrieval, bounded_retrieval, purpose_retrieval, compact_retrieval, quality_retrieval, completion_retrieval, business_retrieval, company_retrieval, efficient_retrieval, external_influence_retrieval, market_explanation_retrieval, retrieval_view
        from digital_twin.modules.ai_orchestration.domain.question_evidence import captured_index
        from digital_twin.modules.ai_orchestration.domain import question_purpose
        from digital_twin.modules.reasoning.contracts import content_hash, validate_evidence_packet
        packet = result["input"]
        validate_evidence_packet(packet)
        ids = result.get("retrievalInputIds", [])
        if not ids or len(ids) > 3 or len(set(ids)) != len(ids):
            raise ValueError("research progress requires bounded completed reads")
        trace, known, captured_versions = [], {}, {}
        for input_id in ids:
            row = connection.execute("SELECT i.artifact_gzip AS input_gzip,r.artifact_gzip AS round_gzip,r.artifact_hash "
                "FROM ai_control_inputs i JOIN ai_control_retrieval_rounds r ON r.input_id=i.input_id AND r.attempt=i.attempt "
                "JOIN ai_control_input_calls l ON l.input_id=i.input_id JOIN ai_control_calls c ON c.call_id=l.call_id "
                "AND c.task_id=i.task_id AND c.prompt_hash=i.prompt_hash AND c.status='completed' "
                "WHERE i.input_id=%s AND i.task_id=%s AND i.attempt=%s LIMIT 1", (input_id, job["taskId"], job["attempts"])).fetchone()
            if not row:
                raise ValueError("research progress requires completed frozen read proof")
            envelope, step = json.loads(gzip.decompress(row["input_gzip"])), json.loads(gzip.decompress(row["round_gzip"]))
            validate_execution_input(envelope)
            contract = market_explanation_retrieval if envelope["promptVersion"] == market_explanation_retrieval.VERSION else external_influence_retrieval if envelope["promptVersion"] == external_influence_retrieval.VERSION else efficient_retrieval if envelope["promptVersion"] == efficient_retrieval.VERSION else company_retrieval if envelope["promptVersion"] == company_retrieval.VERSION else business_retrieval if envelope["promptVersion"] == business_retrieval.VERSION else completion_retrieval if envelope["promptVersion"] == completion_retrieval.VERSION else quality_retrieval if envelope["promptVersion"] == quality_retrieval.VERSION else compact_retrieval if envelope["promptVersion"] == compact_retrieval.VERSION else purpose_retrieval if envelope["promptVersion"] in purpose_retrieval.SUPPORTED_VERSIONS else bounded_retrieval if envelope["promptVersion"] == bounded_retrieval.VERSION else lane_retrieval if envelope["promptVersion"] == lane_retrieval.VERSION else question_retrieval if envelope["promptVersion"] == question_retrieval.VERSION else indexed_retrieval if envelope["promptVersion"] == indexed_retrieval.VERSION else scoped_retrieval if envelope["promptVersion"] == scoped_retrieval.VERSION else working_retrieval
            if (envelope["promptVersion"] != (purpose_retrieval.version_for(envelope["current"]) if contract is purpose_retrieval else contract.VERSION) or step["inputId"] != input_id or content_hash(step) != row["artifact_hash"]
                    or envelope["retrievalContext"].get("trace", []) != trace
                    or any(envelope["current"].get(key) != packet.get(key) or packet.get(key) != job.get(key)
                           for key in ("accountId", "symbol", "worldId", "taskId"))
                    or envelope["current"]["sourceSnapshots"] != packet["sourceSnapshots"]
                    or envelope["current"]["capturedAt"] != packet["capturedAt"]
                    or envelope["current"].get("researchKnowledge") != packet.get("researchKnowledge")
                    or any(envelope["current"].get(key) != packet.get(key)
                           for key in ("companyValuePolicy", "companyValueEvidence", "previousCompanyValue", "businessInsightPolicy", "companyResearchPolicy", "questionPurposePolicy", "externalInfluencePolicy", "marketExplanationPolicy"))
                    or envelope["retrievalContext"].get("questionEvidencePolicy") != packet.get("questionEvidencePolicy")
                    or (packet.get("questionEvidencePolicy") and captured_index(envelope["retrievalContext"].get("questionIndex", []), preserve_sources=question_purpose.enabled(packet)) != packet.get("questionEvidenceIndex"))
                    or step.get("authorScopePolicy") != envelope["retrievalContext"].get("authorScopePolicy")):
                raise ValueError("research progress input scope or trace mismatch")
            if packet.get("businessInsightPolicy") and envelope["current"].get("businessEvidence") != packet.get("businessEvidence"):
                raise ValueError("research progress report requirements changed")
            for fact in envelope["current"]["facts"]:
                known.setdefault(fact["id"], fact)
                captured_versions.setdefault(fact["id"], set()).add(digest(fact))
            if step.get("decision"):
                if set(step["decision"]["selectedFactIds"]) - known.keys():
                    raise ValueError("research progress selected unread evidence")
                if contract in {scoped_retrieval, indexed_retrieval, question_retrieval, lane_retrieval, bounded_retrieval, purpose_retrieval, compact_retrieval, quality_retrieval, completion_retrieval, business_retrieval, company_retrieval, efficient_retrieval, external_influence_retrieval, market_explanation_retrieval}:
                    contract.validate_scope(step["decision"], envelope["retrievalContext"], known)
            for item in step["reads"]:
                for fact in item["result"].get("facts", []):
                    known[fact["id"]] = fact
                    captured_versions.setdefault(fact["id"], set()).add(digest(fact))
            trace.append(step)
            contract.validate_trace(trace)
            if contract in {compact_retrieval, quality_retrieval, completion_retrieval, business_retrieval, company_retrieval, efficient_retrieval, external_influence_retrieval, market_explanation_retrieval}:
                getattr(contract, "context_view", retrieval_view).paths(envelope["current"], {**envelope["retrievalContext"], "trace": trace})
        if (packet["retrieval"] != contract.trace_summary(trace, packet["retrieval"]["status"])
                or any(digest(fact) not in captured_versions.get(fact["id"], set()) for fact in packet["facts"])):
            raise ValueError("research progress evidence changed after capture")
        if contract in {scoped_retrieval, indexed_retrieval, question_retrieval, lane_retrieval, bounded_retrieval, purpose_retrieval, compact_retrieval, quality_retrieval, completion_retrieval, business_retrieval, company_retrieval, efficient_retrieval, external_influence_retrieval, market_explanation_retrieval} and packet.get("researchScope") != contract.research_scope(trace, packet["retrieval"]["status"]):
            raise ValueError("research progress question scope changed")
        last = (trace[-1].get("decision") or {}).get("action")
        expected = {"finish": "ready", "research": "research-ready", "defer": "deferred", "assess": "assessment-ready"}.get(last)
        from digital_twin.modules.ai_orchestration.domain import company_value
        if company_value.enabled(packet):
            company_value.validate(packet)
        if contract in {bounded_retrieval, purpose_retrieval, compact_retrieval, quality_retrieval, completion_retrieval, business_retrieval, company_retrieval, efficient_retrieval, external_influence_retrieval, market_explanation_retrieval} and not company_value.available(packet) and bounded_retrieval.price_only_finish(trace[-1].get("decision") or {}, known):
            expected = "deferred"
        from digital_twin.modules.ai_orchestration.domain.business_progress import report_gaps
        if expected == "ready" and report_gaps(packet):
            expected = "deferred"
        if (expected and packet["retrieval"]["status"] != expected
                and not (contract in {lane_retrieval, bounded_retrieval, purpose_retrieval, compact_retrieval, quality_retrieval, completion_retrieval, business_retrieval, company_retrieval, efficient_retrieval, external_influence_retrieval, market_explanation_retrieval} and packet["retrieval"]["status"] == "context-budget")):
            raise ValueError("research progress disposition changed")
        return trace
