"""Freeze the exact model input before execution, including bounded memories."""
import copy
import hashlib
import json

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash, validate_evidence_packet
from digital_twin.modules.ai_orchestration.domain.planning import planning_prompt, bounded_planning_prompt, legacy_planning_prompt
from digital_twin.modules.ai_orchestration.domain.insight_schema import planning_schema, bounded_planning_schema, legacy_planning_schema, review_schema
from digital_twin.modules.ai_orchestration.domain.brain_management import management_prompt, management_schema, required_case_memory
from digital_twin.modules.ai_orchestration.domain.observation_clock import clocked_management_prompt, clocked_review_prompt
from digital_twin.modules.ai_orchestration.domain.observation_clock import citable_management_prompt, citable_review_prompt, citable_management_schema
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
PROMPT_VERSION = "independent-observation-v10-directed-reads"
LEGACY_REPAIR_PROMPT_VERSION = "independent-observation-repair-v1"
DEVELOPMENT_REPAIR_PROMPT_VERSION = "independent-observation-repair-v2-ontology-development"
AGENDA_REPAIR_PROMPT_VERSION = "independent-observation-repair-v3-persistent-agenda"
SOURCE_CLOCK_REPAIR_PROMPT_VERSION = "independent-observation-repair-v4-source-clock"
CITABLE_REPAIR_PROMPT_VERSION = "independent-observation-repair-v5-clock-citations"
REPAIR_PROMPT_VERSION = "independent-observation-repair-v6-directed-reads"
LEGACY_REVIEW_PROMPT_VERSION = "independent-observation-review-v1"
SOURCE_CLOCK_REVIEW_PROMPT_VERSION = "independent-observation-review-v2-source-clock"
REVIEW_PROMPT_VERSION = "independent-observation-review-v3-clock-citations"
DEFAULT_PROMPT_BYTES = 256 * 1024


def prompt_budget(value=DEFAULT_PROMPT_BYTES):
    try:
        return max(64 * 1024, min(512 * 1024, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_PROMPT_BYTES


def freeze_execution_input(packet, history, research, max_prompt_bytes=DEFAULT_PROMPT_BYTES, retrieval_trace=None):
    validate_evidence_packet(packet)
    current = copy.deepcopy(packet)
    previous, results = [], [copy.deepcopy(row) for row in research if required_case_memory(row)]
    prompt = directed_planning_prompt(current, previous, results)
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
            if name == "research" and required_case_memory(row):
                continue
            size = len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode())
            selected.append(copy.deepcopy(row))
            candidate = directed_planning_prompt(current, previous, results)
            if used + size > budget or len(candidate.encode()) > limit:
                selected.pop()
                excluded[name].append({"hash": content_hash(row), "bytes": size, "reason": "context-budget"})
            else:
                used += size
                prompt = candidate
    return {"protocolVersion": EXECUTION_INPUT_PROTOCOL, "promptVersion": PROMPT_VERSION, "promptBudgetBytes": limit,
        "current": current, "previousAnalyses": previous, "researchResults": results,
        "retrievalTrace": copy.deepcopy(retrieval_trace or []),
        "memoryCoverage": {"analysesAvailable": len(history), "analysesIncluded": len(previous),
                           "researchAvailable": len(research), "researchIncluded": len(results),
                           "excluded": excluded, "promptBytes": len(prompt.encode())},
        "prompt": prompt, "promptHash": hashlib.sha256(prompt.encode()).hexdigest(), "outputSchema": citable_management_schema(current, results)}


def validate_execution_input(envelope):
    if envelope.get("protocolVersion") != EXECUTION_INPUT_PROTOCOL or envelope.get("promptVersion") not in {PROMPT_VERSION, AGENDA_PROMPT_VERSION, DEVELOPMENT_PROMPT_VERSION, BOUNDED_PROMPT_VERSION, PREVIOUS_PROMPT_VERSION, LEGACY_PROMPT_VERSION, LEGACY_REPAIR_PROMPT_VERSION, DEVELOPMENT_REPAIR_PROMPT_VERSION, AGENDA_REPAIR_PROMPT_VERSION, REPAIR_PROMPT_VERSION, LEGACY_REVIEW_PROMPT_VERSION, REVIEW_PROMPT_VERSION, SOURCE_CLOCK_PROMPT_VERSION, SOURCE_CLOCK_REPAIR_PROMPT_VERSION, SOURCE_CLOCK_REVIEW_PROMPT_VERSION, CITABLE_PROMPT_VERSION, CITABLE_REPAIR_PROMPT_VERSION, RETRIEVAL_PROMPT_VERSION, LEGACY_RETRIEVAL_PROMPT_VERSION}:
        raise EvidenceContractError("unsupported AI execution input")
    validate_evidence_packet(envelope["current"])
    if envelope["promptVersion"] == RETRIEVAL_PROMPT_VERSION:
        validate_trace(envelope["retrievalContext"].get("trace", []))
        prompt = retrieval_prompt(envelope["current"], envelope["retrievalContext"])
        schema = retrieval_schema(envelope["retrievalContext"])
    elif envelope["promptVersion"] == LEGACY_RETRIEVAL_PROMPT_VERSION:
        prompt = legacy.retrieval_prompt(envelope["current"], envelope["retrievalContext"])
        schema = legacy.retrieval_schema()
    elif envelope["promptVersion"] in {REVIEW_PROMPT_VERSION, SOURCE_CLOCK_REVIEW_PROMPT_VERSION, LEGACY_REVIEW_PROMPT_VERSION}:
        from digital_twin.modules.ai_orchestration.domain.insight_quality import review_prompt
        builder = citable_review_prompt if envelope["promptVersion"] == REVIEW_PROMPT_VERSION else clocked_review_prompt if envelope["promptVersion"] == SOURCE_CLOCK_REVIEW_PROMPT_VERSION else review_prompt
        prompt = builder(envelope["current"], envelope["draft"])
        schema = review_schema()
    elif envelope["promptVersion"] == LEGACY_PROMPT_VERSION:
        prompt = legacy_planning_prompt(envelope["current"], envelope["previousAnalyses"], envelope["researchResults"])
        schema = legacy_planning_schema(envelope["current"])
    else:
        old = envelope["promptVersion"] in {PREVIOUS_PROMPT_VERSION, BOUNDED_PROMPT_VERSION, LEGACY_REPAIR_PROMPT_VERSION}
        directed = envelope["promptVersion"] in {PROMPT_VERSION, REPAIR_PROMPT_VERSION}
        citable = directed or envelope["promptVersion"] in {CITABLE_PROMPT_VERSION, CITABLE_REPAIR_PROMPT_VERSION}
        clocked = citable or envelope["promptVersion"] in {SOURCE_CLOCK_PROMPT_VERSION, SOURCE_CLOCK_REPAIR_PROMPT_VERSION}
        managed = clocked or envelope["promptVersion"] in {AGENDA_PROMPT_VERSION, AGENDA_REPAIR_PROMPT_VERSION}
        builder = bounded_planning_prompt if old else directed_planning_prompt if directed else citable_management_prompt if citable else clocked_management_prompt if clocked else management_prompt if managed else planning_prompt
        prompt = builder(envelope["current"], envelope["previousAnalyses"], envelope["researchResults"])
        if envelope["promptVersion"] in {REPAIR_PROMPT_VERSION, CITABLE_REPAIR_PROMPT_VERSION, SOURCE_CLOCK_REPAIR_PROMPT_VERSION, AGENDA_REPAIR_PROMPT_VERSION, DEVELOPMENT_REPAIR_PROMPT_VERSION, LEGACY_REPAIR_PROMPT_VERSION}:
            from digital_twin.modules.ai_orchestration.domain.insight_repair import repair_prompt
            prompt = repair_prompt(prompt, envelope["repair"])
        schema = (citable_management_schema if citable else management_schema)(envelope["current"], envelope["researchResults"]) if managed else (bounded_planning_schema if old else planning_schema)(envelope["current"])
        if directed and envelope["current"].get("retrieval"):
            trace = envelope.get("retrievalTrace", [])
            version = envelope["current"]["retrieval"]["version"]
            validate_trace(trace, version)
            if trace_summary(trace, envelope["current"]["retrieval"]["status"], version) != envelope["current"]["retrieval"]:
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
    value["promptVersion"] = REPAIR_PROMPT_VERSION
    value["repair"] = {"parentInputId": parent_input_id, "rejectedDraft": copy.deepcopy(draft),
                       "errors": list(errors), "comparisons": comparison_diagnostics(draft, value["current"])}
    if review:
        value["repair"]["independentReview"] = copy.deepcopy(review)
    value["prompt"] = repair_prompt(directed_planning_prompt(value["current"], value["previousAnalyses"], value["researchResults"]), value["repair"])
    # A repair adds the rejected draft and feedback. Re-budget optional memory
    # without changing the packet or the parent audit artifact.
    for field, kind in (("researchResults", "research"), ("previousAnalyses", "analyses")):
        while len(value["prompt"].encode()) > limit and value[field]:
            eligible = [index for index, row in enumerate(value[field]) if field != "researchResults" or not required_case_memory(row)]
            if not eligible:
                break
            omitted = value[field].pop(eligible[-1])
            value.setdefault("memoryCoverage", {}).setdefault("excluded", {}).setdefault(kind, []).append(
                {"hash": content_hash(omitted), "reason": "repair-context-budget"})
            value["prompt"] = repair_prompt(directed_planning_prompt(value["current"], value["previousAnalyses"], value["researchResults"]), value["repair"])
    value["outputSchema"] = citable_management_schema(value["current"], value["researchResults"])
    value.setdefault("memoryCoverage", {}).update(analysesIncluded=len(value["previousAnalyses"]),
        researchIncluded=len(value["researchResults"]), promptBytes=len(value["prompt"].encode()))
    value["promptHash"] = hashlib.sha256(value["prompt"].encode()).hexdigest()
    if len(value["prompt"].encode()) > limit:
        raise EvidenceContractError("observation repair exceeds context budget")
    return value


def freeze_review_input(result, max_prompt_bytes=DEFAULT_PROMPT_BYTES):
    draft = copy.deepcopy({key: value for key, value in result.items() if key not in {"input", "quality", "comparisonFacts"}})
    packet = copy.deepcopy(result["input"])
    prompt = citable_review_prompt(packet, draft)
    limit = prompt_budget(max_prompt_bytes)
    if len(prompt.encode()) > limit:
        raise EvidenceContractError("observation review exceeds context budget")
    return {"protocolVersion": EXECUTION_INPUT_PROTOCOL, "promptVersion": REVIEW_PROMPT_VERSION, "promptBudgetBytes": limit,
            "current": packet, "draft": draft, "prompt": prompt, "promptHash": hashlib.sha256(prompt.encode()).hexdigest(), "outputSchema": review_schema()}
