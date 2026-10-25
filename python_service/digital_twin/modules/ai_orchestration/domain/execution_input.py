"""Freeze the exact model input before execution, including bounded memories."""
import copy
import hashlib
import json

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash, validate_evidence_packet
from digital_twin.modules.ai_orchestration.domain.planning import planning_prompt, bounded_planning_prompt, legacy_planning_prompt
from digital_twin.modules.ai_orchestration.domain.insight_schema import planning_schema, bounded_planning_schema, legacy_planning_schema, review_schema
from digital_twin.modules.ai_orchestration.domain.brain_management import management_prompt, management_schema
from digital_twin.modules.ai_orchestration.domain.observation_clock import clocked_management_prompt, clocked_review_prompt
from digital_twin.modules.ai_orchestration.domain.observation_clock import citable_management_prompt, citable_review_prompt, citable_management_schema
from digital_twin.modules.ai_orchestration.domain.continuity import continuity_memory, required_memory, continuous_planning_prompt, continuous_review_prompt, memory_count
from digital_twin.modules.ai_orchestration.domain.research_request import continuous_planning_schema
from digital_twin.modules.ai_orchestration.domain.observation_wording import readable_planning_prompt, readable_review_prompt
from .business_research import business_prompt, business_schema, business_review_prompt
from .question_resolution import resolution_prompt, resolution_schema, resolution_review_prompt
from .knowledge_research import knowledge_prompt, knowledge_schema, knowledge_review_prompt
from . import working_retrieval
from . import scoped_retrieval
from . import indexed_retrieval, memory_reference, question_retrieval, lane_retrieval, bounded_retrieval, purpose_retrieval, compact_retrieval, quality_retrieval, completion_retrieval, business_retrieval, company_retrieval, judgment_policy, evidence_completion, business_insight, company_research_cycle, retrieval_view, evidence_readiness, evidence_purpose, company_value
from digital_twin.modules.ai_orchestration.domain.retrieval import (
    RETRIEVAL_PROMPT_VERSION, retrieval_prompt, retrieval_schema, directed_planning_prompt, trace_summary,
    LEGACY_RETRIEVAL_PROMPT_VERSION, legacy, validate_trace,
)


EXECUTION_INPUT_PROTOCOL = "ai-observation-execution-v1"
LEGACY_PROMPT_VERSION = "independent-observation-v3-insight-contract"
PREVIOUS_PROMPT_VERSION = "independent-observation-v4-scoped-evidence"
BOUNDED_PROMPT_VERSION = "independent-observation-v5-bounded-memory"
DEVELOPMENT_PROMPT_VERSION = "independent-observation-v6-ontology-development"
AGENDA_PROMPT_VERSION = "independent-observation-v7-persistent-agenda"
SOURCE_CLOCK_PROMPT_VERSION = "independent-observation-v8-source-clock"
CITABLE_PROMPT_VERSION = "independent-observation-v9-clock-citations"
DIRECTED_PROMPT_VERSION = "independent-observation-v10-directed-reads"
CONTINUITY_PROMPT_VERSION = "independent-observation-v11-continuity-research"
MANAGEMENT_BOUNDS_PROMPT_VERSION = "independent-observation-v12-management-evidence-bounds"
FILING_PROMPT_VERSION = "independent-observation-v13-filing-research-windows"
READABLE_PROMPT_VERSION = "independent-observation-v14-readable-meaning"
BUSINESS_PROMPT_VERSION = "independent-observation-v15-business-research"
QUESTION_PROMPT_VERSION = "independent-observation-v16-question-resolution"
KNOWLEDGE_PROMPT_VERSION = "independent-observation-v17-research-knowledge"
INDEXED_PROMPT_VERSION = "independent-observation-v18-indexed-memory"
PROMPT_VERSION = "independent-observation-v19-question-assessment"
LEGACY_REPAIR_PROMPT_VERSION = "independent-observation-repair-v1"
DEVELOPMENT_REPAIR_PROMPT_VERSION = "independent-observation-repair-v2-ontology-development"
AGENDA_REPAIR_PROMPT_VERSION = "independent-observation-repair-v3-persistent-agenda"
SOURCE_CLOCK_REPAIR_PROMPT_VERSION = "independent-observation-repair-v4-source-clock"
CITABLE_REPAIR_PROMPT_VERSION = "independent-observation-repair-v5-clock-citations"
DIRECTED_REPAIR_PROMPT_VERSION = "independent-observation-repair-v6-directed-reads"
CONTINUITY_REPAIR_PROMPT_VERSION = "independent-observation-repair-v7-continuity-research"
MANAGEMENT_BOUNDS_REPAIR_PROMPT_VERSION = "independent-observation-repair-v8-management-evidence-bounds"
FILING_REPAIR_PROMPT_VERSION = "independent-observation-repair-v9-filing-research-windows"
READABLE_REPAIR_PROMPT_VERSION = "independent-observation-repair-v10-readable-meaning"
BUSINESS_REPAIR_PROMPT_VERSION = "independent-observation-repair-v11-business-research"
QUESTION_REPAIR_PROMPT_VERSION = "independent-observation-repair-v12-question-resolution"
KNOWLEDGE_REPAIR_PROMPT_VERSION = "independent-observation-repair-v13-research-knowledge"
INDEXED_REPAIR_PROMPT_VERSION = "independent-observation-repair-v14-indexed-memory"
REPAIR_PROMPT_VERSION = "independent-observation-repair-v15-question-assessment"
LEGACY_REVIEW_PROMPT_VERSION = "independent-observation-review-v1"
SOURCE_CLOCK_REVIEW_PROMPT_VERSION = "independent-observation-review-v2-source-clock"
CITABLE_REVIEW_PROMPT_VERSION = "independent-observation-review-v3-clock-citations"
CONTINUITY_REVIEW_PROMPT_VERSION = "independent-observation-review-v4-continuity"
READABLE_REVIEW_PROMPT_VERSION = "independent-observation-review-v5-readable-meaning"
BUSINESS_REVIEW_PROMPT_VERSION = "independent-observation-review-v6-business-research"
QUESTION_REVIEW_PROMPT_VERSION = "independent-observation-review-v7-question-resolution"
REVIEW_PROMPT_VERSION = "independent-observation-review-v8-research-knowledge"
LANE_PROMPT_VERSION = "independent-observation-v20-isolated-lanes"
LANE_REPAIR_PROMPT_VERSION = "independent-observation-repair-v16-isolated-lanes"
LANE_REVIEW_PROMPT_VERSION = "independent-observation-review-v9-isolated-lanes"
DEFAULT_PROMPT_BYTES = 256 * 1024


def prompt_budget(value=DEFAULT_PROMPT_BYTES):
    try:
        return max(64 * 1024, min(512 * 1024, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_PROMPT_BYTES


def freeze_execution_input(packet, history, research, max_prompt_bytes=DEFAULT_PROMPT_BYTES, retrieval_trace=None):
    validate_evidence_packet(packet)
    current = copy.deepcopy(packet)
    indexed = packet.get("memoryReadPolicy") == memory_reference.VERSION
    continuity = memory_reference.continuity(history, research) if indexed else continuity_memory(history, research)
    previous, results = continuity["previousAnalyses"], continuity["researchResults"]
    builder = company_research_cycle.prompt if company_research_cycle.enabled(current) else business_insight.prompt if business_insight.enabled(current) else evidence_completion.prompt if evidence_completion.enabled(current) else judgment_policy.prompt if judgment_policy.enabled(current) else evidence_purpose.prompt if evidence_purpose.enabled(current) else evidence_readiness.prompt if evidence_readiness.enabled(current) else knowledge_prompt
    schema_builder = company_research_cycle.schema if company_research_cycle.enabled(current) else business_insight.schema if business_insight.enabled(current) else evidence_readiness.schema if evidence_readiness.enabled(current) else knowledge_schema
    prompt = builder(current, previous, results)
    limit = prompt_budget(max_prompt_bytes)
    if len(prompt.encode()) > limit:
        raise EvidenceContractError("observation execution exceeds context budget")
    # Current facts, due cases and the successful-delivery baseline are immutable. Admit
    # optional memories whole, in recency order, within both per-kind and total
    # budgets. An oversized first row must not block every subsequent attempt.
    excluded = {"analyses": [], "research": []}
    for name, rows, selected, budget in (("analyses", history, previous, 64 * 1024),
                                         ("research", research, results, 48 * 1024)):
        used = sum(len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode()) for row in selected)
        for row in rows:
            if indexed:
                continue  # Summaries and explicitly read sections are already selected.
            if required_memory(row):
                continue
            size = len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode())
            selected.append(copy.deepcopy(row))
            candidate = builder(current, previous, results)
            if used + size > budget or len(candidate.encode()) > limit:
                selected.pop()
                excluded[name].append({"hash": content_hash(row), "bytes": size, "reason": "context-budget"})
            else:
                used += size
                prompt = candidate
    return {"protocolVersion": EXECUTION_INPUT_PROTOCOL, "promptVersion": evidence_purpose.author_version(current) if evidence_purpose.enabled(current) else evidence_readiness.PROMPT_VERSION if evidence_readiness.enabled(current) else LANE_PROMPT_VERSION if current.get("executionLanePolicy") else PROMPT_VERSION, "promptBudgetBytes": limit,
        "current": current, "previousAnalyses": previous, "researchResults": results,
        "retrievalTrace": copy.deepcopy(retrieval_trace or []),
        "memoryCoverage": {"analysesAvailable": memory_count(history), "analysesIncluded": memory_count(previous),
                           "researchAvailable": memory_count(research), "researchIncluded": memory_count(results),
                           "continuityVersion": continuity["version"],
                           **({"memoryReadPolicy": memory_reference.VERSION,
                               "detailSectionsIncluded": sum(row.get("kind") == "memory-detail" for row in previous + results)} if indexed else {}),
                           "requiredAnalyses": sum(required_memory(row) for row in previous),
                           "requiredResearch": sum(required_memory(row) for row in results),
                           "excluded": excluded, "promptBytes": len(prompt.encode())},
        "prompt": prompt, "promptHash": hashlib.sha256(prompt.encode()).hexdigest(), "outputSchema": schema_builder(current, results)}


def validate_execution_input(envelope):
    if envelope.get("promptVersion") in company_research_cycle.VERSIONS:
        return company_research_cycle.validate_input(envelope)
    if envelope.get("promptVersion") in business_insight.VERSIONS:
        return business_insight.validate_input(envelope)
    if envelope.get("promptVersion") in evidence_completion.VERSIONS:
        return evidence_completion.validate_input(envelope)
    if envelope.get("promptVersion") in judgment_policy.VERSIONS:
        return judgment_policy.validate_input(envelope)
    if envelope.get("promptVersion") in {evidence_purpose.PROMPT_VERSION, evidence_purpose.REPAIR_VERSION, evidence_purpose.REVIEW_VERSION, company_value.PROMPT_VERSION, company_value.REPAIR_VERSION, company_value.REVIEW_VERSION}:
        from .insight_repair import repair_prompt
        packet, phase = envelope["current"], envelope["promptVersion"]
        evidence_purpose.validate(packet)
        value_version = phase in {company_value.PROMPT_VERSION, company_value.REPAIR_VERSION, company_value.REVIEW_VERSION}
        if value_version != company_value.enabled(packet):
            raise EvidenceContractError("company value prompt policy mismatch")
        if value_version:
            company_value.validate(packet)
        review = phase in {evidence_purpose.REVIEW_VERSION, company_value.REVIEW_VERSION}
        expected = (evidence_purpose.review_prompt(packet, envelope["draft"]) if review else
                    evidence_purpose.prompt(packet, envelope["previousAnalyses"], envelope["researchResults"]))
        if phase in {evidence_purpose.REPAIR_VERSION, company_value.REPAIR_VERSION}:
            expected = repair_prompt(expected, envelope["repair"])
        if envelope["prompt"] != expected or envelope["promptHash"] != hashlib.sha256(expected.encode()).hexdigest():
            raise EvidenceContractError("frozen purpose scoped input mismatch")
        old = copy.deepcopy(envelope)
        old["promptVersion"] = {company_value.PROMPT_VERSION: evidence_readiness.PROMPT_VERSION,
            company_value.REPAIR_VERSION: evidence_readiness.REPAIR_VERSION, company_value.REVIEW_VERSION: evidence_readiness.REVIEW_VERSION,
            evidence_purpose.PROMPT_VERSION: evidence_readiness.PROMPT_VERSION,
            evidence_purpose.REPAIR_VERSION: evidence_readiness.REPAIR_VERSION,
            evidence_purpose.REVIEW_VERSION: evidence_readiness.REVIEW_VERSION}[phase]
        old["prompt"] = (knowledge_review_prompt(packet, envelope["draft"]) if review else
                         evidence_readiness.prompt(packet, envelope["previousAnalyses"], envelope["researchResults"]))
        if phase in {evidence_purpose.REPAIR_VERSION, company_value.REPAIR_VERSION}:
            old["prompt"] = repair_prompt(old["prompt"], envelope["repair"])
        old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
        validate_execution_input(old)
        if len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes")):
            raise EvidenceContractError("frozen purpose scoped prompt exceeds context budget")
        return content_hash(envelope)
    if envelope.get("promptVersion") in {evidence_readiness.PROMPT_VERSION, evidence_readiness.REPAIR_VERSION, evidence_readiness.REVIEW_VERSION}:
        from .execution_lanes import identified
        from .insight_repair import repair_prompt
        if not identified(envelope.get("current", {})) or not evidence_readiness.enabled(envelope["current"]):
            raise EvidenceContractError("bounded evidence input requires its captured policy")
        phase = envelope["promptVersion"]
        review = phase == evidence_readiness.REVIEW_VERSION
        packet = envelope["current"]
        expected = (knowledge_review_prompt(packet, envelope["draft"]) if review else
                    evidence_readiness.prompt(packet, envelope["previousAnalyses"], envelope["researchResults"]))
        if phase == evidence_readiness.REPAIR_VERSION:
            expected = repair_prompt(expected, envelope["repair"])
        expected_schema = review_schema() if review else evidence_readiness.schema(packet, envelope["researchResults"])
        if (envelope["prompt"] != expected or envelope["promptHash"] != hashlib.sha256(expected.encode()).hexdigest()
                or envelope["outputSchema"] != expected_schema):
            raise EvidenceContractError("frozen bounded evidence input mismatch")
        # Reuse the historical source/memory/trace verifier without altering
        # either the new artifact or old prompt/schema constructors.
        legacy_view = copy.deepcopy(envelope)
        legacy_view["promptVersion"] = {evidence_readiness.PROMPT_VERSION: LANE_PROMPT_VERSION,
            evidence_readiness.REPAIR_VERSION: LANE_REPAIR_PROMPT_VERSION,
            evidence_readiness.REVIEW_VERSION: LANE_REVIEW_PROMPT_VERSION}[phase]
        if not review:
            legacy_view["prompt"] = knowledge_prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
            if phase == evidence_readiness.REPAIR_VERSION:
                legacy_view["prompt"] = repair_prompt(legacy_view["prompt"], envelope["repair"])
            legacy_view["outputSchema"] = knowledge_schema(packet, envelope["researchResults"])
        legacy_view["promptHash"] = hashlib.sha256(legacy_view["prompt"].encode()).hexdigest()
        validate_execution_input(legacy_view)
        if len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes")):
            raise EvidenceContractError("frozen bounded evidence prompt exceeds context budget")
        return content_hash(envelope)
    if envelope.get("promptVersion") in {LANE_PROMPT_VERSION, LANE_REPAIR_PROMPT_VERSION, LANE_REVIEW_PROMPT_VERSION}:
        from .execution_lanes import identified
        if not identified(envelope.get("current", {})):
            raise EvidenceContractError("isolated input requires its execution lane")
        legacy_view = copy.deepcopy(envelope)
        legacy_view["promptVersion"] = {LANE_PROMPT_VERSION: PROMPT_VERSION,
            LANE_REPAIR_PROMPT_VERSION: REPAIR_PROMPT_VERSION, LANE_REVIEW_PROMPT_VERSION: REVIEW_PROMPT_VERSION}[envelope["promptVersion"]]
        validate_execution_input(legacy_view)
        return content_hash(envelope)
    from . import research_review
    if envelope.get("promptVersion") in research_review.SUPPORTED_VERSIONS:
        return research_review.validate_input(envelope)
    from . import research_work
    if envelope.get("promptVersion") in {research_work.PROMPT_VERSION, research_work.LEGACY_PROMPT_VERSION}:
        return research_work.validate_input(envelope)
    if envelope.get("promptVersion") in {working_retrieval.VERSION, scoped_retrieval.VERSION, indexed_retrieval.VERSION, question_retrieval.VERSION, lane_retrieval.VERSION, bounded_retrieval.VERSION, purpose_retrieval.VERSION, company_value.RETRIEVAL_VERSION, compact_retrieval.VERSION, quality_retrieval.VERSION, completion_retrieval.VERSION, business_retrieval.VERSION, company_retrieval.VERSION}:
        contract = company_retrieval if envelope["promptVersion"] == company_retrieval.VERSION else business_retrieval if envelope["promptVersion"] == business_retrieval.VERSION else completion_retrieval if envelope["promptVersion"] == completion_retrieval.VERSION else quality_retrieval if envelope["promptVersion"] == quality_retrieval.VERSION else compact_retrieval if envelope["promptVersion"] == compact_retrieval.VERSION else purpose_retrieval if envelope["promptVersion"] in {purpose_retrieval.VERSION, company_value.RETRIEVAL_VERSION} else bounded_retrieval if envelope["promptVersion"] == bounded_retrieval.VERSION else lane_retrieval if envelope["promptVersion"] == lane_retrieval.VERSION else question_retrieval if envelope["promptVersion"] == question_retrieval.VERSION else indexed_retrieval if envelope["promptVersion"] == indexed_retrieval.VERSION else scoped_retrieval if envelope["promptVersion"] == scoped_retrieval.VERSION else working_retrieval
        if envelope.get("protocolVersion") != EXECUTION_INPUT_PROTOCOL:
            raise EvidenceContractError("unsupported AI execution input")
        validate_evidence_packet(envelope["current"])
        if contract is company_retrieval and not company_research_cycle.enabled(envelope["current"]):
            raise EvidenceContractError("company research policy missing")
        if contract in {business_retrieval, company_retrieval} and not business_insight.enabled(envelope["current"]):
            raise EvidenceContractError("business insight policy missing")
        if contract in {completion_retrieval, business_retrieval, company_retrieval} and not evidence_completion.enabled(envelope["current"]):
            raise EvidenceContractError("evidence completion policy missing")
        if contract in {quality_retrieval, completion_retrieval, business_retrieval, company_retrieval} and not judgment_policy.enabled(envelope["current"]):
            raise EvidenceContractError("quality retrieval policy missing")
        if contract in {purpose_retrieval, compact_retrieval, quality_retrieval, completion_retrieval, business_retrieval, company_retrieval}:
            if contract is purpose_retrieval and (envelope["promptVersion"] == company_value.RETRIEVAL_VERSION) != company_value.enabled(envelope["current"]):
                raise EvidenceContractError("company value retrieval policy mismatch")
            if company_value.enabled(envelope["current"]):
                company_value.validate(envelope["current"])
            evidence_purpose.validate(envelope["current"])
            if envelope["retrievalContext"].get("evidenceUses") != envelope["current"]["evidenceUses"]:
                raise EvidenceContractError("retrieval purpose clocks changed")
        if contract in {compact_retrieval, quality_retrieval, completion_retrieval, business_retrieval, company_retrieval} and envelope["retrievalContext"].get("readableContextPaths") != retrieval_view.paths(envelope["current"], envelope["retrievalContext"]):
            raise EvidenceContractError("frozen context paths mismatch")
        contract.validate_trace(envelope["retrievalContext"].get("trace", []))
        prompt = contract.prompt(envelope["current"], envelope["retrievalContext"])
        if (prompt != envelope["prompt"] or hashlib.sha256(prompt.encode()).hexdigest() != envelope["promptHash"]
                or envelope["outputSchema"] != contract.schema(envelope["retrievalContext"])
                or len(prompt.encode()) > prompt_budget(envelope.get("promptBudgetBytes"))):
            raise EvidenceContractError("frozen working retrieval input mismatch")
        return content_hash(envelope)
    if envelope.get("protocolVersion") != EXECUTION_INPUT_PROTOCOL or envelope.get("promptVersion") not in {INDEXED_PROMPT_VERSION, INDEXED_REPAIR_PROMPT_VERSION, KNOWLEDGE_PROMPT_VERSION, KNOWLEDGE_REPAIR_PROMPT_VERSION, QUESTION_PROMPT_VERSION, QUESTION_REPAIR_PROMPT_VERSION, QUESTION_REVIEW_PROMPT_VERSION, BUSINESS_REVIEW_PROMPT_VERSION, BUSINESS_PROMPT_VERSION, BUSINESS_REPAIR_PROMPT_VERSION, PROMPT_VERSION, READABLE_PROMPT_VERSION, READABLE_REPAIR_PROMPT_VERSION, READABLE_REVIEW_PROMPT_VERSION, FILING_PROMPT_VERSION, FILING_REPAIR_PROMPT_VERSION, MANAGEMENT_BOUNDS_PROMPT_VERSION, MANAGEMENT_BOUNDS_REPAIR_PROMPT_VERSION, CONTINUITY_PROMPT_VERSION, CONTINUITY_REPAIR_PROMPT_VERSION, DIRECTED_PROMPT_VERSION, DIRECTED_REPAIR_PROMPT_VERSION, AGENDA_PROMPT_VERSION, DEVELOPMENT_PROMPT_VERSION, BOUNDED_PROMPT_VERSION, PREVIOUS_PROMPT_VERSION, LEGACY_PROMPT_VERSION, LEGACY_REPAIR_PROMPT_VERSION, DEVELOPMENT_REPAIR_PROMPT_VERSION, AGENDA_REPAIR_PROMPT_VERSION, REPAIR_PROMPT_VERSION, LEGACY_REVIEW_PROMPT_VERSION, REVIEW_PROMPT_VERSION, CONTINUITY_REVIEW_PROMPT_VERSION, CITABLE_REVIEW_PROMPT_VERSION, SOURCE_CLOCK_PROMPT_VERSION, SOURCE_CLOCK_REPAIR_PROMPT_VERSION, SOURCE_CLOCK_REVIEW_PROMPT_VERSION, CITABLE_PROMPT_VERSION, CITABLE_REPAIR_PROMPT_VERSION, RETRIEVAL_PROMPT_VERSION, LEGACY_RETRIEVAL_PROMPT_VERSION}:
        raise EvidenceContractError("unsupported AI execution input")
    validate_evidence_packet(envelope["current"])
    if envelope["promptVersion"] == RETRIEVAL_PROMPT_VERSION:
        validate_trace(envelope["retrievalContext"].get("trace", []))
        prompt = retrieval_prompt(envelope["current"], envelope["retrievalContext"])
        schema = retrieval_schema(envelope["retrievalContext"])
    elif envelope["promptVersion"] == LEGACY_RETRIEVAL_PROMPT_VERSION:
        prompt = legacy.retrieval_prompt(envelope["current"], envelope["retrievalContext"])
        schema = legacy.retrieval_schema()
    elif envelope["promptVersion"] in {QUESTION_REVIEW_PROMPT_VERSION, BUSINESS_REVIEW_PROMPT_VERSION, REVIEW_PROMPT_VERSION, READABLE_REVIEW_PROMPT_VERSION, CONTINUITY_REVIEW_PROMPT_VERSION, CITABLE_REVIEW_PROMPT_VERSION, SOURCE_CLOCK_REVIEW_PROMPT_VERSION, LEGACY_REVIEW_PROMPT_VERSION}:
        from digital_twin.modules.ai_orchestration.domain.insight_quality import review_prompt
        builder = knowledge_review_prompt if envelope["promptVersion"] == REVIEW_PROMPT_VERSION else resolution_review_prompt if envelope["promptVersion"] == QUESTION_REVIEW_PROMPT_VERSION else business_review_prompt if envelope["promptVersion"] == BUSINESS_REVIEW_PROMPT_VERSION else readable_review_prompt if envelope["promptVersion"] == READABLE_REVIEW_PROMPT_VERSION else continuous_review_prompt if envelope["promptVersion"] == CONTINUITY_REVIEW_PROMPT_VERSION else citable_review_prompt if envelope["promptVersion"] == CITABLE_REVIEW_PROMPT_VERSION else clocked_review_prompt if envelope["promptVersion"] == SOURCE_CLOCK_REVIEW_PROMPT_VERSION else review_prompt
        prompt = builder(envelope["current"], envelope["draft"])
        schema = review_schema()
    elif envelope["promptVersion"] == LEGACY_PROMPT_VERSION:
        prompt = legacy_planning_prompt(envelope["current"], envelope["previousAnalyses"], envelope["researchResults"])
        schema = legacy_planning_schema(envelope["current"])
    else:
        old = envelope["promptVersion"] in {PREVIOUS_PROMPT_VERSION, BOUNDED_PROMPT_VERSION, LEGACY_REPAIR_PROMPT_VERSION}
        resolved = envelope["promptVersion"] in {INDEXED_PROMPT_VERSION, INDEXED_REPAIR_PROMPT_VERSION, KNOWLEDGE_PROMPT_VERSION, KNOWLEDGE_REPAIR_PROMPT_VERSION, PROMPT_VERSION, REPAIR_PROMPT_VERSION, QUESTION_PROMPT_VERSION, QUESTION_REPAIR_PROMPT_VERSION}
        business = resolved or envelope["promptVersion"] in {BUSINESS_PROMPT_VERSION, BUSINESS_REPAIR_PROMPT_VERSION}
        readable = business or envelope["promptVersion"] in {READABLE_PROMPT_VERSION, READABLE_REPAIR_PROMPT_VERSION}
        continuous = readable or envelope["promptVersion"] in {FILING_PROMPT_VERSION, FILING_REPAIR_PROMPT_VERSION, MANAGEMENT_BOUNDS_PROMPT_VERSION, MANAGEMENT_BOUNDS_REPAIR_PROMPT_VERSION, CONTINUITY_PROMPT_VERSION, CONTINUITY_REPAIR_PROMPT_VERSION}
        legacy_research = envelope["promptVersion"] not in {INDEXED_PROMPT_VERSION, INDEXED_REPAIR_PROMPT_VERSION, KNOWLEDGE_PROMPT_VERSION, KNOWLEDGE_REPAIR_PROMPT_VERSION, QUESTION_PROMPT_VERSION, QUESTION_REPAIR_PROMPT_VERSION, BUSINESS_PROMPT_VERSION, BUSINESS_REPAIR_PROMPT_VERSION, PROMPT_VERSION, REPAIR_PROMPT_VERSION, READABLE_PROMPT_VERSION, READABLE_REPAIR_PROMPT_VERSION, FILING_PROMPT_VERSION, FILING_REPAIR_PROMPT_VERSION}
        directed = continuous or envelope["promptVersion"] in {DIRECTED_PROMPT_VERSION, DIRECTED_REPAIR_PROMPT_VERSION}
        citable = directed or envelope["promptVersion"] in {CITABLE_PROMPT_VERSION, CITABLE_REPAIR_PROMPT_VERSION}
        clocked = citable or envelope["promptVersion"] in {SOURCE_CLOCK_PROMPT_VERSION, SOURCE_CLOCK_REPAIR_PROMPT_VERSION}
        managed = clocked or envelope["promptVersion"] in {AGENDA_PROMPT_VERSION, AGENDA_REPAIR_PROMPT_VERSION}
        builder = knowledge_prompt if envelope["promptVersion"] in {INDEXED_PROMPT_VERSION, INDEXED_REPAIR_PROMPT_VERSION, KNOWLEDGE_PROMPT_VERSION, KNOWLEDGE_REPAIR_PROMPT_VERSION, PROMPT_VERSION, REPAIR_PROMPT_VERSION} else resolution_prompt if resolved else business_prompt if business else readable_planning_prompt if readable else continuous_planning_prompt if continuous else bounded_planning_prompt if old else directed_planning_prompt if directed else citable_management_prompt if citable else clocked_management_prompt if clocked else management_prompt if managed else planning_prompt
        prompt = builder(envelope["current"], envelope["previousAnalyses"], envelope["researchResults"],
                         **({"legacy_research": legacy_research} if continuous and not readable else {}))
        if envelope["promptVersion"] in {INDEXED_REPAIR_PROMPT_VERSION, KNOWLEDGE_REPAIR_PROMPT_VERSION, QUESTION_REPAIR_PROMPT_VERSION, BUSINESS_REPAIR_PROMPT_VERSION, REPAIR_PROMPT_VERSION, READABLE_REPAIR_PROMPT_VERSION, FILING_REPAIR_PROMPT_VERSION, MANAGEMENT_BOUNDS_REPAIR_PROMPT_VERSION, CONTINUITY_REPAIR_PROMPT_VERSION, DIRECTED_REPAIR_PROMPT_VERSION, CITABLE_REPAIR_PROMPT_VERSION, SOURCE_CLOCK_REPAIR_PROMPT_VERSION, AGENDA_REPAIR_PROMPT_VERSION, DEVELOPMENT_REPAIR_PROMPT_VERSION, LEGACY_REPAIR_PROMPT_VERSION}:
            from digital_twin.modules.ai_orchestration.domain.insight_repair import repair_prompt
            prompt = repair_prompt(prompt, envelope["repair"])
        schema = (citable_management_schema if citable else management_schema)(envelope["current"], envelope["researchResults"]) if managed else (bounded_planning_schema if old else planning_schema)(envelope["current"])
        if continuous:
            schema = continuous_planning_schema(envelope["current"], envelope["researchResults"],
                bounded_evidence=envelope["promptVersion"] in {INDEXED_PROMPT_VERSION, INDEXED_REPAIR_PROMPT_VERSION, KNOWLEDGE_PROMPT_VERSION, KNOWLEDGE_REPAIR_PROMPT_VERSION, QUESTION_PROMPT_VERSION, QUESTION_REPAIR_PROMPT_VERSION, PROMPT_VERSION, REPAIR_PROMPT_VERSION, READABLE_PROMPT_VERSION, READABLE_REPAIR_PROMPT_VERSION, FILING_PROMPT_VERSION, FILING_REPAIR_PROMPT_VERSION, MANAGEMENT_BOUNDS_PROMPT_VERSION, MANAGEMENT_BOUNDS_REPAIR_PROMPT_VERSION},
                legacy_research=legacy_research)
        if business:
            schema = (knowledge_schema if envelope["promptVersion"] in {INDEXED_PROMPT_VERSION, INDEXED_REPAIR_PROMPT_VERSION, KNOWLEDGE_PROMPT_VERSION, KNOWLEDGE_REPAIR_PROMPT_VERSION, PROMPT_VERSION, REPAIR_PROMPT_VERSION} else resolution_schema if resolved else business_schema)(envelope["current"], envelope["researchResults"])
        if directed and envelope["current"].get("retrieval"):
            trace = envelope.get("retrievalTrace", [])
            version = envelope["current"]["retrieval"]["version"]
            if version in {working_retrieval.VERSION, scoped_retrieval.VERSION, indexed_retrieval.VERSION, question_retrieval.VERSION, lane_retrieval.VERSION, bounded_retrieval.VERSION, purpose_retrieval.VERSION, compact_retrieval.VERSION, quality_retrieval.VERSION, completion_retrieval.VERSION, business_retrieval.VERSION, company_retrieval.VERSION}:
                contract = company_retrieval if version == company_retrieval.VERSION else business_retrieval if version == business_retrieval.VERSION else completion_retrieval if version == completion_retrieval.VERSION else quality_retrieval if version == quality_retrieval.VERSION else compact_retrieval if version == compact_retrieval.VERSION else purpose_retrieval if version == purpose_retrieval.VERSION else bounded_retrieval if version == bounded_retrieval.VERSION else lane_retrieval if version == lane_retrieval.VERSION else question_retrieval if version == question_retrieval.VERSION else indexed_retrieval if version == indexed_retrieval.VERSION else scoped_retrieval if version == scoped_retrieval.VERSION else working_retrieval
                contract.validate_trace(trace)
                summary = contract.trace_summary(trace, envelope["current"]["retrieval"]["status"])
                if contract in {scoped_retrieval, indexed_retrieval, question_retrieval, lane_retrieval, bounded_retrieval, purpose_retrieval, compact_retrieval, quality_retrieval, completion_retrieval, business_retrieval, company_retrieval} and envelope["current"].get("researchScope") != contract.research_scope(trace, envelope["current"]["retrieval"]["status"]):
                    raise EvidenceContractError("research scope does not match retrieval trace")
            else:
                validate_trace(trace, version)
                summary = trace_summary(trace, envelope["current"]["retrieval"]["status"], version)
            if summary != envelope["current"]["retrieval"]:
                raise EvidenceContractError("retrieval trace does not match input")
    if prompt != envelope["prompt"] or hashlib.sha256(prompt.encode()).hexdigest() != envelope["promptHash"]:
        raise EvidenceContractError("frozen AI prompt does not match input")
    if envelope.get("outputSchema") != schema:
        raise EvidenceContractError("frozen AI schema does not match input")
    if "promptBudgetBytes" in envelope and len(prompt.encode()) > prompt_budget(envelope["promptBudgetBytes"]):
        raise EvidenceContractError("frozen AI prompt exceeds context budget")
    return content_hash(envelope)


def freeze_repair_input(envelope, draft, errors, parent_input_id, review=None):
    from digital_twin.modules.ai_orchestration.domain.insight_repair import repair_prompt, comparison_diagnostics
    value = copy.deepcopy(envelope)
    limit = prompt_budget(value.get("promptBudgetBytes", DEFAULT_PROMPT_BYTES))
    value["promptBudgetBytes"] = limit
    active = evidence_readiness.enabled(value["current"])
    builder = company_research_cycle.prompt if company_research_cycle.enabled(value["current"]) else business_insight.prompt if business_insight.enabled(value["current"]) else evidence_completion.prompt if evidence_completion.enabled(value["current"]) else judgment_policy.prompt if judgment_policy.enabled(value["current"]) else evidence_purpose.prompt if evidence_purpose.enabled(value["current"]) else evidence_readiness.prompt if active else knowledge_prompt
    value["promptVersion"] = evidence_purpose.repair_version(value["current"]) if evidence_purpose.enabled(value["current"]) else evidence_readiness.REPAIR_VERSION if active else LANE_REPAIR_PROMPT_VERSION if value["current"].get("executionLanePolicy") else REPAIR_PROMPT_VERSION
    value["repair"] = {"parentInputId": parent_input_id, "rejectedDraft": copy.deepcopy(draft),
                       "errors": list(errors), "comparisons": comparison_diagnostics(draft, value["current"])}
    if review:
        value["repair"]["independentReview"] = copy.deepcopy(review)
    value["prompt"] = repair_prompt(builder(value["current"], value["previousAnalyses"], value["researchResults"]), value["repair"])
    # A repair adds the rejected draft and feedback. Re-budget optional memory
    # without changing the packet or the parent audit artifact.
    for field, kind in (("researchResults", "research"), ("previousAnalyses", "analyses")):
        while len(value["prompt"].encode()) > limit and value[field]:
            eligible = [index for index, row in enumerate(value[field]) if not required_memory(row)]
            if not eligible:
                break
            omitted = value[field].pop(eligible[-1])
            value.setdefault("memoryCoverage", {}).setdefault("excluded", {}).setdefault(kind, []).append(
                {"hash": content_hash(omitted), "reason": "repair-context-budget"})
            value["prompt"] = repair_prompt(builder(value["current"], value["previousAnalyses"], value["researchResults"]), value["repair"])
    value["outputSchema"] = (company_research_cycle.schema if company_research_cycle.enabled(value["current"]) else business_insight.schema if business_insight.enabled(value["current"]) else evidence_readiness.schema if active else knowledge_schema)(value["current"], value["researchResults"])
    value.setdefault("memoryCoverage", {}).update(analysesIncluded=memory_count(value["previousAnalyses"]),
        researchIncluded=memory_count(value["researchResults"]), promptBytes=len(value["prompt"].encode()))
    value["promptHash"] = hashlib.sha256(value["prompt"].encode()).hexdigest()
    if len(value["prompt"].encode()) > limit:
        raise EvidenceContractError("observation repair exceeds context budget")
    return value


def freeze_review_input(result, max_prompt_bytes=DEFAULT_PROMPT_BYTES):
    draft = copy.deepcopy({key: value for key, value in result.items() if key not in {"input", "quality", "comparisonFacts"}})
    packet = copy.deepcopy(result["input"])
    prompt = (company_research_cycle.review_prompt if company_research_cycle.enabled(packet) else business_insight.review_prompt if business_insight.enabled(packet) else evidence_completion.review_prompt if evidence_completion.enabled(packet) else judgment_policy.review_prompt if judgment_policy.enabled(packet) else evidence_purpose.review_prompt if evidence_purpose.enabled(packet) else knowledge_review_prompt)(packet, draft)
    limit = prompt_budget(max_prompt_bytes)
    if len(prompt.encode()) > limit:
        raise EvidenceContractError("observation review exceeds context budget")
    return {"protocolVersion": EXECUTION_INPUT_PROTOCOL, "promptVersion": evidence_purpose.review_version(packet) if evidence_purpose.enabled(packet) else evidence_readiness.REVIEW_VERSION if evidence_readiness.enabled(packet) else LANE_REVIEW_PROMPT_VERSION if packet.get("executionLanePolicy") else REVIEW_PROMPT_VERSION, "promptBudgetBytes": limit,
            "current": packet, "draft": draft, "prompt": prompt, "promptHash": hashlib.sha256(prompt.encode()).hexdigest(), "outputSchema": business_insight.review_schema() if business_insight.enabled(packet) else review_schema()}
