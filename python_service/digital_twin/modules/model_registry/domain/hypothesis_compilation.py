"""Bounded rule-authoring inputs and operational blockers, not investment rules."""

import json
from dataclasses import fields

from .ontology_rulebox_contracts import GraphInferenceRule, GraphRuleCondition, GraphRuleDerivation


RULE_DESIGN_VERSION = "hypothesis-rule-design-v2"
BLOCKER_KINDS = {
    "missing-observation", "stale-observation", "observation-window",
    "schema-mismatch", "unsupported-capability", "dependency-error", "unclassified",
}
DEVELOPMENT_BLOCKERS = {"schema-mismatch", "unsupported-capability", "unclassified"}


def compilation_blockers(candidates):
    result = []
    seen = set()
    for candidate in candidates or []:
        structured = candidate.get("blockers") or []
        if not isinstance(structured, list):
            structured = []
        has_structured = False
        for item in structured:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "unclassified")
            kind = kind if kind in BLOCKER_KINDS else "unclassified"
            requirement = str(item.get("requirement") or "").strip()[:1000]
            if not requirement:
                continue
            has_structured = True
            row = {
                "kind": kind, "requirement": requirement,
                "dependencyKey": str(item.get("dependencyKey") or "")[:191],
                "owner": "development" if kind in DEVELOPMENT_BLOCKERS else (
                    "runtime" if kind == "dependency-error" else "observation"
                ),
            }
            key = (kind, requirement, row["dependencyKey"])
            if key not in seen:
                seen.add(key)
                result.append(row)
        # Structured v2 blockers are authoritative; a paraphrased legacy
        # display list must not turn an observation wait into a schema gap.
        if has_structured:
            continue
        # Legacy free text cannot prove that an observation is actually missing.
        covered = {item["requirement"] for item in result}
        for requirement in candidate.get("requiresData") or []:
            text = str(requirement or "").strip()[:1000]
            if text and text not in covered:
                result.append({"kind": "unclassified", "requirement": text,
                               "dependencyKey": "", "owner": "development"})
                covered.add(text)
    return result[:40]


def blocker_state(blockers):
    kinds = {item.get("kind") for item in blockers or []}
    if not kinds or kinds & DEVELOPMENT_BLOCKERS:
        return "needs-revision", "development-required"
    if "dependency-error" in kinds:
        return "needs-data", "dependency-error"
    if kinds == {"observation-window"}:
        return "needs-data", "waiting-observation"
    return "needs-data", "waiting-data"


def rule_design_context(context, max_bytes=20000):
    rulebox = context.get("ruleBox") or {}
    proposal = context.get("hypothesisProposal") or {}
    inference = context.get("inferenceBox") or {}
    rules = [row for row in rulebox.get("rules") or [] if isinstance(row, dict)]
    referenced = json.dumps({
        "causalPath": proposal.get("causalPath"),
        "supportingEvidenceIds": proposal.get("supportingEvidenceIds"),
        "counterEvidenceIds": proposal.get("counterEvidenceIds"),
    }, ensure_ascii=False)
    matched_ids = {str(row.get("ruleId") or "") for row in inference.get("relations") or [] if isinstance(row, dict)}
    rules.sort(key=lambda row: (
        not bool((row.get("rule_id") or row.get("ruleId")) and str(row.get("rule_id") or row.get("ruleId")) in referenced),
        str(row.get("rule_id") or row.get("ruleId") or "") not in matched_ids,
        str(row.get("rule_id") or row.get("ruleId") or ""),
    ))
    examples = []
    used = 0
    for row in rules:
        # Keep complete conditions/derivations; never substitute a condition count.
        example = {key: row[key] for key in (
            "rule_id", "ruleId", "source_kind", "label", "conditions", "derivations",
            "any_condition_min_count", "knowledge_basis", "claim_contract", "hypothesis_lifecycle",
            "hypothesis_family_key", "model_input_contract", "version", "action_group", "action_level", "prompt_hint",
        ) if key in row}
        size = len(json.dumps(example, ensure_ascii=False).encode("utf-8"))
        if size + used > max_bytes:
            continue
        examples.append(example)
        used += size
        if len(examples) == 3:
            break
    return {
        "version": RULE_DESIGN_VERSION,
        "ruleboxStatus": rulebox.get("status"),
        "ruleboxSnapshotId": rulebox.get("ruleboxSnapshotId"),
        "rulesHash": rulebox.get("rulesHash") or rulebox.get("ruleboxRulesHash"),
        "scope": {"symbols": list(context.get("symbols") or []), "worldId": context.get("worldId")},
        "ruleFields": [item.name for item in fields(GraphInferenceRule)],
        "conditionFields": [item.name for item in fields(GraphRuleCondition)],
        "derivationFields": [item.name for item in fields(GraphRuleDerivation)],
        "examples": examples,
        "omittedRuleCount": max(0, len(rules) - len(examples)),
        "blockerKinds": sorted(BLOCKER_KINDS),
        "boundaries": [
            "Examples prove authoring syntax, not current ABox availability or investment validity.",
            "Evidence IDs are provenance. Do not require a PRESERVES_RULE_LINEAGE fact to write a condition.",
            "Use observed fields and filters from scoped examples; unknown capabilities require development review.",
            "Predictive claims require an exact governed model contract and outcome contract; do not invent model evidence.",
            "A non-predictive context rule must not author candidate_action or replace the causal claim with a policy.",
            "Schema, model registration and provider support gaps are not solved by waiting for another price tick.",
        ],
    }
