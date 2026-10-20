"""Persist native conclusions, competing alternatives and exact evidence.

This read model never selects a market action. Policy responses are authored
by RuleBox; a sole market candidate remains a review, not a recommendation.
"""

from digital_twin.modules.decisions.domain.investment_reasoning.evidence_composition import native_evidence_composition

from copy import deepcopy
import hashlib
import json

from digital_twin.modules.decisions.domain.notification_ai_context import is_graph_backed_relation_context
from digital_twin.modules.decisions.domain.rulebox_decision_brief import resolved_rule_threshold


VERSION = "rulebox-judgment-v1"
AUTHORITY = "typedb-materialized-synthesis"
SCOPE = ("accountId", "symbol", "worldId", "inferenceGenerationId", "sourceAboxSnapshotId")


def _rows(value):
    return [r for r in value or [] if isinstance(r, dict)]


def _unique(values):
    return list(dict.fromkeys(v for v in values if v))


def _identity(account, relation):
    graph = relation.get("graphStoreInference") or {}
    identity = {"accountId": account, "symbol": (relation.get("subject") or {}).get("symbol"),
                **{key: relation.get(key) or graph.get(key) for key in SCOPE[2:]}}
    if (not all(identity.values()) or not is_graph_backed_relation_context(relation)
            or relation.get("generationAligned") is False
            or any(s.get(k) and s[k] != v for s in (relation, graph) for k, v in identity.items())):
        return {}
    return identity


def _assemble(identity, management, candidates, restrictions, relation, checks, source):
    groups = {}
    for candidate in candidates:
        action = candidate["action"]
        group = groups.setdefault(action, {"action": action, "hypothesisIds": [], "supportingRuleIds": [],
            "supportingEvidenceIds": [], "counterEvidenceIds": [], "invalidationConditions": [], "executionEligible": False})
        for key in ("hypothesisIds", "supportingRuleIds", "supportingEvidenceIds", "counterEvidenceIds", "invalidationConditions"):
            group[key] = _unique(group[key] + list(candidate.get(key) or []))
        group["executionEligible"] |= bool(candidate.get("executionEligible"))
    groups = sorted(groups.values(), key=lambda r: r["action"])
    blocked = sorted({r["action"] for r in restrictions})
    status = ("management-response" if management.get('responses') else "management-resolution" if management.get('resolutions') else "competing-candidates" if len(groups) > 1
              else "candidate-review" if groups else "restriction-only" if blocked else "evidence-review")
    used = _unique([r.get("ruleId") for r in restrictions] + [rid for g in groups for rid in g["supportingRuleIds"]]
                   + [r.get("ruleId") for r in [*management.get("responses", []), *management.get('resolutions', [])]])
    conditions = []
    for trace in _rows((relation.get("graphStoreInference") or {}).get("traces")):
        rid = trace.get("ruleId") or trace.get("rule_id")
        if rid not in used:
            continue
        for c in _rows(trace.get("matchedConditions")):
            if (c.get("judgementEvidenceUsable") is False
                    or c.get("freshnessStatus") in {"stale", "expired", "missing", "unknown"}
                    or not (c.get("matchedByTypeDB") is True or c.get("matched") is True
                            or c.get('resolutionMatchedByTypeDB') is True)):
                continue
            conditions.append({"ruleId": rid, **deepcopy(c),
                               "resolvedThreshold": resolved_rule_threshold(c, relation.get("facts") or {})})
    result = {"version": VERSION, "authority": AUTHORITY, **identity, "source": source, "status": status,
              "managementDecision": deepcopy(management), "candidateGroups": groups, "blockedActions": blocked,
              "restrictions": deepcopy(restrictions), "conditions": conditions, "sourceRuleIds": used,
              "nextChecks": _unique(checks), "comparisonRequired": len(groups) > 1,
              "finalMarketActionSelected": False, "orderAuthorized": False}
    applicability = [r['actionApplicability'] for r in _rows(relation.get('matchedRules'))
                     if (r.get('actionApplicability') or {}).get('version') == 'native-action-applicability-v1']
    not_applicable = sorted({a for row in applicability for a in row.get('notApplicableActions') or []})
    available_groups = [g for g in groups if g['action'] not in set(blocked) | set(not_applicable)]
    composition = native_evidence_composition(identity, relation)
    result["evidenceComposition"] = composition
    result["sourceRuleIds"] = sorted(set(result["sourceRuleIds"]) |
        {r["ruleId"] for r in composition["evidenceGroups"]} |
        {r["ruleId"] for r in composition["compositions"]})
    result['responsePlan'] = {
        'version': 'native-response-plan-v2', 'evidenceComposition': deepcopy(composition), 'authority': AUTHORITY, **identity,
        'primaryKind': ('management-response' if management.get('responses') else 'management-resolution' if management.get('resolutions')
                        else 'restriction' if restrictions else 'candidate-review' if any(g['executionEligible'] for g in available_groups)
                        else 'qualification-pending' if available_groups else 'evidence-review'),
        'policyResponses': deepcopy(management.get('responses') or []),
        'activeRestrictions': deepcopy(restrictions),
        'notApplicableActions': not_applicable,
        'reviewCandidates': deepcopy([g for g in available_groups if g['executionEligible']]),
        'pendingCandidates': [dict(deepcopy(g), pendingReason='execution-qualification-pending') for g in available_groups if not g['executionEligible']],
        'blockedCandidates': deepcopy([g for g in groups if g['action'] in blocked]),
        'notApplicableCandidates': deepcopy([g for g in groups if g['action'] in not_applicable]),
        'releaseChecks': [{'ruleId': r.get('ruleId'), 'condition': r.get('releaseCondition'),
                          'objective': deepcopy(r.get('objective') or {}), 'targetValue': r.get('targetValue')}
                         for r in management.get('responses') or []],
        'comparisonRequired': len(available_groups) > 1,
        'selectedMarketAction': None, 'orderAuthorized': False}
    # Values and proof identities remain in the record, but repeated polling
    # of the same conclusion is not a new decision.
    semantics = {"status": status, "blockedActions": blocked,
        "compositions": [{k: r.get(k) for k in ("code", "policyRuleId", "sourceContractIds")}
                         for r in composition["compositions"]],
        "candidates": [{k: r[k] for k in ("action", "hypothesisIds", "executionEligible")} for r in groups],
        'notApplicableActions': result['responsePlan']['notApplicableActions'],
        "responses": [{k: r.get(k) for k in ("code", "objective", "targetValue", "releaseCondition")}
                      for r in management.get("responses", [])],
        'resolvedPolicies': sorted(r['ruleId'] for r in management.get('resolutions', []))}
    result["conclusionFingerprint"] = hashlib.sha256(json.dumps(semantics, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return result


def judgment_from_synthesis(synthesis, relation):
    identity = _identity(synthesis.account_id, relation)
    if not identity or any(identity[k] != v for k, v in {"symbol": synthesis.symbol,
            "inferenceGenerationId": synthesis.inference_generation_id, "sourceAboxSnapshotId": synthesis.source_abox_snapshot_id}.items()):
        return {}
    eligibility = {r.get("ruleId"): r for r in _rows((relation.get("actionEnvelope") or {}).get("inferenceEligibilityAssessments"))}
    blocked = set((relation.get("actionEnvelope") or {}).get("blockedActions") or [])
    native, restrictions = {}, []
    for r in _rows(relation.get("matchedRules")):
        rid = r.get("rule_id") or r.get("ruleId")
        e = eligibility.get(rid) or {}
        if (r.get("matched") is False or r.get("reference_only") or r.get("referenceOnly")
                or e.get("status") != "eligible" or e.get("evidenceUsableForJudgement") is not True or e.get("freshnessStatus") != "fresh"):
            continue
        native[rid] = r
        applicability = r.get('actionApplicability') or {}
        not_applicable = set(applicability.get('notApplicableActions') or []) if applicability.get('version') == 'native-action-applicability-v1' else set()
        restrictions.extend({"ruleId": rid, "action": a, "nextChecks": list(r.get("nextChecks") or [])}
                            for a in r.get("blockedActions") or [] if a in blocked and a not in not_applicable)
    candidates = [{"action": a.action, "hypothesisIds": list(a.hypothesis_ids), "supportingRuleIds": list(a.supporting_rule_ids),
                   "supportingEvidenceIds": list(a.supporting_evidence_ids), "counterEvidenceIds": list(a.counter_evidence_ids),
                   "invalidationConditions": list(a.invalidation_conditions), "executionEligible": a.execution_eligible}
                  for a in synthesis.alternatives if a.decision_eligible and set(a.supporting_rule_ids).intersection(native)]
    used = {r["ruleId"] for r in restrictions} | {rid for r in candidates for rid in r["supportingRuleIds"]}
    checks = [c for rid in used.intersection(native) for c in native[rid].get("nextChecks") or []]
    return _assemble(identity, synthesis.management_decision or {}, candidates, restrictions, relation, checks, "decision-synthesis")


def judgment_from_frozen_brief(brief, relation):
    """Read legacy receipts without claiming a newly persisted policy decision."""
    identity = _identity(brief.get("accountId"), relation)
    if (not identity or brief.get("authority") != "typedb-materialized-decision-effects"
            or any(brief.get(k) != identity[k] for k in SCOPE)):
        return {}
    candidates = [{"action": r["action"], "hypothesisIds": [h.get("id") for h in _rows(r.get("hypotheses"))],
                   "supportingRuleIds": [r.get("ruleId")], "invalidationConditions": list(r.get("invalidationConditions") or []),
                   "executionEligible": r.get("executionEligible")}
                  for r in _rows(brief.get("candidates"))]
    restrictions = _rows(brief.get("restrictions"))
    checks = [c for r in [*restrictions, *_rows(brief.get("candidates")), *_rows(brief.get("checks"))] for c in r.get("nextChecks") or []]
    return _assemble(identity, brief.get("managementDecision") or {}, candidates, restrictions, relation, checks, "legacy-frozen-brief")
