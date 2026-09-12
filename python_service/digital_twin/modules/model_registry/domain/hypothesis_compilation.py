"""Bounded rule-authoring inputs and operational blockers, not investment rules."""

import hashlib
import json
import re
from collections import Counter
from dataclasses import fields

from .ontology_rulebox_contracts import GraphInferenceRule, GraphRuleCondition, GraphRuleDerivation


RULE_DESIGN_VERSION = "hypothesis-rule-design-v4"
BLOCKER_KINDS = {
    "missing-observation", "stale-observation", "observation-window",
    "schema-mismatch", "unsupported-capability", "dependency-error", "unclassified",
    "unverified-observation",
    "condition-not-met", "validation-review",
}
DEVELOPMENT_BLOCKERS = {"schema-mismatch", "unsupported-capability", "unclassified", "unverified-observation"}


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
    if "validation-review" in kinds:
        return "needs-data", "waiting-validation"
    if "condition-not-met" in kinds:
        return "needs-data", "waiting-condition"
    if kinds == {"observation-window"}:
        return "needs-data", "waiting-observation"
    return "needs-data", "waiting-data"


def validation_requirements(candidate):
    """Keep post-authoring checks distinct from evidence needed to write a rule.

    Only actual TypeDB execution can discharge an automatic check. Arbitrary
    empirical/causal requirements need a separate verified review, not a count
    of price snapshots or an AI assertion that validation passed.
    """
    raw = candidate.get("validationRequirements") or []
    if not isinstance(raw, list):
        raw = [raw]
    if len(raw) > 40:
        raise ValueError("validationRequirements exceeds 40 checks")
    rows = []
    for item in raw:
        row = dict(item) if isinstance(item, dict) else {"requirement": str(item)}
        requirement = str(row.get("requirement") or "").strip()
        if not requirement or len(requirement) > 2000:
            raise ValueError("validationRequirements requires a bounded nonempty requirement")
        check = str(row.get("check") or "review")
        if check not in {"typedb-execution", "current-match", "review"}:
            check = "review"
        rows.append({"check": check, "requirement": requirement,
                     "dependencyKey": str(row.get("dependencyKey") or "")[:191]})
    return rows


def compilation_fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def authoring_input_fingerprint(case):
    return compilation_fingerprint({
        "designVersion": RULE_DESIGN_VERSION,
        "caseId": case.case_id, "accountId": case.account_id, "symbol": case.symbol,
        "claim": case.claim, "causalPath": case.causal_path,
        "supportingEvidenceIds": sorted(case.supporting_evidence_ids),
        "counterEvidenceIds": sorted(case.counter_evidence_ids),
        "requiredEvidenceTypes": sorted(case.required_evidence_types),
        "invalidationConditions": case.invalidation_conditions,
    })


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _rule_id(rule):
    return str(rule.get("rule_id") or rule.get("ruleId") or "")


def _search_terms(value):
    return set(re.findall(r"[a-z0-9]+|[가-힣]{2,}", str(value).lower()))


def ranked_authoring_rules(context):
    rulebox = context.get("ruleBox") or {}
    proposal = context.get("hypothesisProposal") or {}
    inference = context.get("inferenceBox") or {}
    rules = [row for row in rulebox.get("rules") or [] if isinstance(row, dict)]
    referenced = json.dumps({key: proposal.get(key) for key in (
        "causalPath", "supportingEvidenceIds", "counterEvidenceIds", "relatedRuleIds", "sourceRuleIds",
    )}, ensure_ascii=False)
    terms = _search_terms(" ".join([
        str(proposal.get("title") or ""), str(proposal.get("claim") or ""),
        " ".join(str(item) for item in proposal.get("causalPath") or []),
    ]))
    matched_ids = {str(row.get("ruleId") or "") for row in inference.get("relations") or [] if isinstance(row, dict)}
    rule_terms = {
        _rule_id(row): _search_terms(json.dumps({
            "label": row.get("label"),
            "claim": _mapping(row.get("claim_contract") or row.get("claimContract")).get("statement"),
        }, ensure_ascii=False)) for row in rules
    }
    frequency = Counter(term for values in rule_terms.values() for term in values)
    # Lexical retrieval selects documentation only, never an investment rule.
    rules.sort(key=lambda row: (
        not bool(_rule_id(row) and _rule_id(row) in referenced),
        _rule_id(row) not in matched_ids,
        -sum(1 / frequency[term] for term in sorted(terms & rule_terms[_rule_id(row)])),
        _rule_id(row),
    ))
    return rules


def authoring_capability_index(rules, max_bytes=12000):
    """Index the loaded release, without declaring facts or absent capabilities."""
    rows = []
    used = 0
    for rule in rules[:16]:
        claim = _mapping(rule.get("claim_contract") or rule.get("claimContract"))
        model = _mapping(rule.get("model_input_contract") or rule.get("modelInputContract"))
        conditions = rule.get("conditions") or []
        model_conditions = model.get("conditionProfiles") or []
        row = {
            "ruleId": _rule_id(rule), "label": rule.get("label"),
            "enabled": rule.get("enabled"),
            "claimType": claim.get("claimType"),
            "thesisFamily": claim.get("thesisFamily"),
            "modelEvidence": [
                {key: value for key, value in _mapping(
                    item.get("target_property_filters") or item.get("targetPropertyFilters")
                ).items() if key in {"signalType", "releaseId", "hypothesisContractId"}}
                for item in conditions if isinstance(item, dict)
                and (item.get("relation_type") or item.get("relationType")) == "HAS_MODEL_SIGNAL"
            ],
            "inputRelations": sorted({
                str(item.get("relation_type") or item.get("relationType"))
                for item in list(conditions) + list(model_conditions) if isinstance(item, dict)
                and (item.get("relation_type") or item.get("relationType"))
            }),
        }
        size = len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
        if size + used > max_bytes:
            continue
        rows.append(row)
        used += size
    registered_ids = []
    id_bytes = 0
    for rule in rules:
        identifier = _rule_id(rule)
        size = len(json.dumps(identifier, ensure_ascii=False).encode("utf-8")) + 2
        if id_bytes + size > max_bytes:
            break
        registered_ids.append(identifier)
        id_bytes += size
    return {
        "rules": rows, "includedRuleCount": len(rows), "totalRuleCount": len(rules),
        "omittedRuleCount": len(rules) - len(rows),
        "coverage": "complete-loaded-release" if len(rows) == len(rules) else "partial-loaded-release",
        "registeredRuleIds": registered_ids, "omittedRuleIdCount": len(rules) - len(registered_ids),
        "authority": "registered-contracts-only-not-current-observations",
    }


def rule_design_context(context, max_bytes=20000):
    rulebox = context.get("ruleBox") or {}
    rules = ranked_authoring_rules(context)
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
        "observationState": "not-queried" if (context.get("inferenceBox") or {}).get("status") == "deferred-validation" else "inference-summary-only",
        "capabilityIndex": authoring_capability_index(rules),
        "ruleFields": [item.name for item in fields(GraphInferenceRule)],
        "conditionFields": [item.name for item in fields(GraphRuleCondition)],
        "derivationFields": [item.name for item in fields(GraphRuleDerivation)],
        "examples": examples,
        "omittedRuleCount": max(0, len(rules) - len(examples)),
        "blockerKinds": sorted(BLOCKER_KINDS),
        "boundaries": [
            "Examples prove authoring syntax, not current ABox availability or investment validity.",
            "Unqueried or omitted data are unknown, not missing. Candidate preview owns current ABox verification.",
            "The capability index covers only this loaded release; an omitted example is not an unsupported model.",
            "Evidence IDs are provenance. Do not require a PRESERVES_RULE_LINEAGE fact to write a condition.",
            "Use observed fields and filters from scoped examples; unknown capabilities require development review.",
            "Predictive claims require an exact governed model contract and outcome contract; do not invent model evidence.",
            "A non-predictive context rule must not author candidate_action or replace the causal claim with a policy.",
            "Schema, model registration and provider support gaps are not solved by waiting for another price tick.",
        ],
    }
