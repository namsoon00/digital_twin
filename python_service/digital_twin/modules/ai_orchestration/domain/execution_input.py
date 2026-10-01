"""Freeze the exact model input before execution, including bounded memories."""
import copy
import hashlib
import json

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash, validate_evidence_packet
from digital_twin.modules.ai_orchestration.domain.planning import planning_prompt, bounded_planning_prompt, legacy_planning_prompt
from digital_twin.modules.ai_orchestration.domain.insight_schema import planning_schema, bounded_planning_schema, legacy_planning_schema, review_schema


EXECUTION_INPUT_PROTOCOL = "ai-observation-execution-v1"
LEGACY_PROMPT_VERSION = "independent-observation-v3-insight-contract"
PREVIOUS_PROMPT_VERSION = "independent-observation-v4-scoped-evidence"
BOUNDED_PROMPT_VERSION = "independent-observation-v5-bounded-memory"
PROMPT_VERSION = "independent-observation-v6-ontology-development"
LEGACY_REPAIR_PROMPT_VERSION = "independent-observation-repair-v1"
REPAIR_PROMPT_VERSION = "independent-observation-repair-v2-ontology-development"
REVIEW_PROMPT_VERSION = "independent-observation-review-v1"
DEFAULT_PROMPT_BYTES = 256 * 1024


def prompt_budget(value=DEFAULT_PROMPT_BYTES):
    try:
        return max(64 * 1024, min(512 * 1024, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_PROMPT_BYTES


def freeze_execution_input(packet, history, research, max_prompt_bytes=DEFAULT_PROMPT_BYTES):
    validate_evidence_packet(packet)
    current = copy.deepcopy(packet)
    previous, results = [], []
    prompt = planning_prompt(current, previous, results)
    limit = prompt_budget(max_prompt_bytes)
    if len(prompt.encode()) > limit:
        raise EvidenceContractError("observation execution exceeds context budget")
    # Current facts and the successful-delivery baseline are immutable. Admit
    # optional memories whole, in recency order, within both per-kind and total
    # budgets. An oversized first row must not block every subsequent attempt.
    excluded = {"analyses": [], "research": []}
    for name, rows, selected, budget in (("analyses", history, previous, 64 * 1024),
                                         ("research", research, results, 48 * 1024)):
        used = 0
        for row in rows:
            size = len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode())
            selected.append(copy.deepcopy(row))
            candidate = planning_prompt(current, previous, results)
            if used + size > budget or len(candidate.encode()) > limit:
                selected.pop()
                excluded[name].append({"hash": content_hash(row), "bytes": size, "reason": "context-budget"})
            else:
                used += size
                prompt = candidate
    return {"protocolVersion": EXECUTION_INPUT_PROTOCOL, "promptVersion": PROMPT_VERSION, "promptBudgetBytes": limit,
        "current": current, "previousAnalyses": previous, "researchResults": results,
        "memoryCoverage": {"analysesAvailable": len(history), "analysesIncluded": len(previous),
                           "researchAvailable": len(research), "researchIncluded": len(results),
                           "excluded": excluded, "promptBytes": len(prompt.encode())},
        "prompt": prompt, "promptHash": hashlib.sha256(prompt.encode()).hexdigest(), "outputSchema": planning_schema(current)}


def validate_execution_input(envelope):
    if envelope.get("protocolVersion") != EXECUTION_INPUT_PROTOCOL or envelope.get("promptVersion") not in {PROMPT_VERSION, BOUNDED_PROMPT_VERSION, PREVIOUS_PROMPT_VERSION, LEGACY_PROMPT_VERSION, LEGACY_REPAIR_PROMPT_VERSION, REPAIR_PROMPT_VERSION, REVIEW_PROMPT_VERSION}:
        raise EvidenceContractError("unsupported AI execution input")
    validate_evidence_packet(envelope["current"])
    if envelope["promptVersion"] == REVIEW_PROMPT_VERSION:
        from digital_twin.modules.ai_orchestration.domain.insight_quality import review_prompt
        prompt = review_prompt(envelope["current"], envelope["draft"])
        schema = review_schema()
    elif envelope["promptVersion"] == LEGACY_PROMPT_VERSION:
        prompt = legacy_planning_prompt(envelope["current"], envelope["previousAnalyses"], envelope["researchResults"])
        schema = legacy_planning_schema(envelope["current"])
    else:
        old = envelope["promptVersion"] in {PREVIOUS_PROMPT_VERSION, BOUNDED_PROMPT_VERSION, LEGACY_REPAIR_PROMPT_VERSION}
        builder = bounded_planning_prompt if old else planning_prompt
        prompt = builder(envelope["current"], envelope["previousAnalyses"], envelope["researchResults"])
        if envelope["promptVersion"] in {REPAIR_PROMPT_VERSION, LEGACY_REPAIR_PROMPT_VERSION}:
            from digital_twin.modules.ai_orchestration.domain.insight_repair import repair_prompt
            prompt = repair_prompt(prompt, envelope["repair"])
        schema = (bounded_planning_schema if old else planning_schema)(envelope["current"])
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
    limit = prompt_budget(value.get("promptBudgetBytes", 120000))
    value["promptBudgetBytes"] = limit
    value["promptVersion"] = REPAIR_PROMPT_VERSION
    value["repair"] = {"parentInputId": parent_input_id, "rejectedDraft": copy.deepcopy(draft),
                       "errors": list(errors), "comparisons": comparison_diagnostics(draft, value["current"])}
    if review:
        value["repair"]["independentReview"] = copy.deepcopy(review)
    value["outputSchema"] = planning_schema(value["current"])
    value["prompt"] = repair_prompt(planning_prompt(value["current"], value["previousAnalyses"], value["researchResults"]), value["repair"])
    # A repair adds the rejected draft and feedback. Re-budget optional memory
    # without changing the packet or the parent audit artifact.
    for field, kind in (("researchResults", "research"), ("previousAnalyses", "analyses")):
        while len(value["prompt"].encode()) > limit and value[field]:
            omitted = value[field].pop()
            value.setdefault("memoryCoverage", {}).setdefault("excluded", {}).setdefault(kind, []).append(
                {"hash": content_hash(omitted), "reason": "repair-context-budget"})
            value["prompt"] = repair_prompt(planning_prompt(value["current"], value["previousAnalyses"], value["researchResults"]), value["repair"])
    value.setdefault("memoryCoverage", {}).update(analysesIncluded=len(value["previousAnalyses"]),
        researchIncluded=len(value["researchResults"]), promptBytes=len(value["prompt"].encode()))
    value["promptHash"] = hashlib.sha256(value["prompt"].encode()).hexdigest()
    if len(value["prompt"].encode()) > limit:
        raise EvidenceContractError("observation repair exceeds context budget")
    return value


def freeze_review_input(result, max_prompt_bytes=DEFAULT_PROMPT_BYTES):
    from digital_twin.modules.ai_orchestration.domain.insight_quality import review_prompt
    draft = copy.deepcopy({key: value for key, value in result.items() if key not in {"input", "quality", "comparisonFacts"}})
    packet = copy.deepcopy(result["input"])
    prompt = review_prompt(packet, draft)
    limit = prompt_budget(max_prompt_bytes)
    if len(prompt.encode()) > limit:
        raise EvidenceContractError("observation review exceeds context budget")
    return {"protocolVersion": EXECUTION_INPUT_PROTOCOL, "promptVersion": REVIEW_PROMPT_VERSION, "promptBudgetBytes": limit,
            "current": packet, "draft": draft, "prompt": prompt, "promptHash": hashlib.sha256(prompt.encode()).hexdigest(), "outputSchema": review_schema()}
