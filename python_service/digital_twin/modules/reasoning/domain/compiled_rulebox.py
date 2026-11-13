"""Immutable, data-driven RuleBox execution without a database or an AI call.

The owner approved this execution backend on 2026-11-13. Rules remain the
versioned source of investment meaning. Unsupported semantics reject the whole
release; neither a missing field nor an execution budget exhaustion is a match.
"""

from dataclasses import dataclass, fields
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
import time

from digital_twin.modules.model_registry.contracts import GraphInferenceRule, GraphRuleCondition, GraphRuleDerivation, management_resolution_probe
from .management_resolution import materialize_management_resolution
from digital_twin.modules.reasoning.domain.ontology_inference_materializer import (
    evidence_relation_index, materialize_rule_inference,
)


COMPILED_RULEBOX_VERSION = "compiled-rulebox-v1"
OPERATORS = frozenset({"==", "eq", "in", "!=", "ne", "<", "lt", "<=", "lte", ">", "gt", ">=", "gte", "exists", "present"})
ROLES = frozenset({"required", "any", "optional", "not"})


class RuleboxCompilationError(ValueError):
    def __init__(self, issues):
        self.issues = tuple(issues)
        super().__init__("; ".join(self.issues))


class RuleboxExecutionLimit(RuntimeError):
    pass


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class CompiledRulebox:
    rules_json: str
    fingerprint: str
    tbox_fingerprint: str
    rule_order: tuple
    dependencies: tuple
    version: str = COMPILED_RULEBOX_VERSION

    def rules(self):
        return [GraphInferenceRule.from_dict(row) for row in json.loads(self.rules_json)]

    def manifest(self):
        rows = json.loads(self.rules_json)
        return {"version": self.version, "fingerprint": self.fingerprint,
                "tboxFingerprint": self.tbox_fingerprint, "ruleCount": len(rows),
                "enabledRuleCount": sum(row.get("enabled") is not False for row in rows),
                "ruleOrder": list(self.rule_order),
                "dependencies": {rid: list(deps) for rid, deps in self.dependencies},
                "rules": rows}


def compile_rulebox(rows, *, tbox_fingerprint=""):
    """Compile the entire release. Never silently drop an executable rule."""
    payloads = [deepcopy(r.to_dict() if hasattr(r, "to_dict") else dict(r)) for r in rows]
    issues, rules, seen = [], [], set()
    def known_keys(cls):
        keys = {f.name for f in fields(cls)}
        return keys | {k.split('_')[0] + ''.join(p.title() for p in k.split('_')[1:]) for k in keys}
    allowed_rule_keys = known_keys(GraphInferenceRule) | {"conditionCount", "derivationCount", "domain_manifest", "execution_profile"}
    for row in payloads:
        rid = str(row.get("rule_id") or row.get("ruleId") or "")
        if not rid or rid in seen:
            issues.append((rid or "<missing-rule-id>") + ": missing or duplicate rule identity")
        seen.add(rid)
        unknown = set(row) - allowed_rule_keys
        if unknown:
            issues.append(rid + ": unsupported rule fields " + ", ".join(sorted(unknown)))
        if "enabled" in row and not isinstance(row["enabled"], bool):
            issues.append(rid + ": enabled must be a boolean")
        for key, cls in (("conditions", GraphRuleCondition), ("derivations", GraphRuleDerivation)):
            for item in row.get(key) or []:
                if not isinstance(item, dict) or set(item) - known_keys(cls):
                    issues.append(rid + ": unsupported " + key + " fields")
        try:
            rule = GraphInferenceRule.from_dict(row)
        except (ValueError, TypeError, KeyError) as error:
            issues.append(rid + ": " + str(error))
            continue
        rules.append(rule)
        if not rule.enabled:
            continue
        if not (row.get("source_kind") or row.get("sourceKind")):
            issues.append(rid + ": missing source kind")
        scope = rule.model_input_contract.get("evolutionScope")
        if scope is not None and (not isinstance(scope, dict) or not scope.get("worldId") or not scope.get("symbol")):
            issues.append(rid + ": evolution scope requires a world and symbol")
        ids = [c.condition_id for c in rule.conditions]
        # Validate authored identities before from_dict can normalize duplicates.
        raw_ids = [c.get("condition_id") or c.get("conditionId") for c in row.get("conditions", []) if isinstance(c, dict)]
        if not ids or len(set(raw_ids)) != len(raw_ids) or any(not cid for cid in raw_ids):
            issues.append(rid + ": conditions require unique stable identities")
        for c in rule.conditions:
            label = rid + "/" + c.condition_id
            if c.kind not in {"subject_property", "relation"}:
                issues.append(label + ": unsupported condition kind " + c.kind)
            if c.role not in ROLES:
                issues.append(label + ": unsupported condition role " + c.role)
            if c.operator not in OPERATORS:
                issues.append(label + ": unsupported operator " + c.operator)
            if c.kind == "subject_property" and not c.field:
                issues.append(label + ": missing subject field")
            if isinstance(c.value, dict) and set(c.value) - {"field", "value", "default"}:
                issues.append(label + ": unsupported expected-value fields")
            if c.kind == "relation" and (not c.relation_type or c.direction not in {"in", "out"}):
                issues.append(label + ": missing relation type or unsupported direction")
            for filters in (c.target_property_filters, c.relation_property_filters):
                for field, value in filters.items():
                    if not field or (isinstance(value, dict) and (
                            value.get("operator", "==") not in OPERATORS
                            or set(value) - {"operator", "field", "value", "default"})):
                        issues.append(label + ": unsupported filter " + str(field))
        groups = {c.evidence_group_key or c.condition_id for c in rule.conditions if c.role in {"any", "optional"}}
        if groups and rule.any_condition_min_count > len(groups):
            issues.append(rid + ": any-condition minimum exceeds distinct evidence groups")
        if not rule.derivations:
            issues.append(rid + ": missing derivation")
    if not payloads:
        issues.append("RuleBox release is empty")
    # Ordered derivations allow a governed rule to depend on another rule's
    # output. Cycles, including negated cycles, need a different explicit
    # semantics and cannot be accepted by this bounded execution backend.
    producers = defaultdict(set)
    for r in rules:
        if r.enabled:
            for d in r.derivations:
                producers[d.relation_type].add(r.rule_id)
    dependencies = {r.rule_id: set().union(*(producers[c.relation_type] for c in r.conditions
                       if c.kind == "relation")) if r.conditions else set()
                    for r in rules if r.enabled}
    pending, order = {k: set(v) for k, v in dependencies.items()}, []
    while pending:
        ready = sorted(k for k, v in pending.items() if not v)
        if not ready:
            issues.append("cyclic derived-rule dependencies: " + ", ".join(sorted(pending)))
            break
        for rid in ready:
            order.append(rid)
            del pending[rid]
        for values in pending.values():
            values.difference_update(ready)
    try:
        encoded = canonical_json(payloads)
    except (TypeError, ValueError) as error:
        issues.append("non-portable rule value: " + str(error))
        encoded = "[]"
    if issues:
        raise RuleboxCompilationError(issues)
    digest = fingerprint({"version": COMPILED_RULEBOX_VERSION, "rules": payloads,
                          "tboxFingerprint": tbox_fingerprint})
    return CompiledRulebox(encoded, digest, tbox_fingerprint, tuple(order),
                          tuple((k, tuple(sorted(v))) for k, v in sorted(dependencies.items())))


def _scalar(value):
    return "true" if value is True else "false" if value is False else value


def value_matches(actual, operator, expected, properties=None):
    if actual is None or actual == "":
        return False
    if operator in {"exists", "present"}:
        return True
    if isinstance(expected, dict):
        if expected.get("field"):
            expected = (properties or {}).get(expected["field"], expected.get("default"))
        else:
            expected = expected.get("default", expected.get("value"))
    if expected is None or expected == "":
        return False
    if isinstance(actual, (list, tuple, set)):
        return any(value_matches(v, operator, expected, properties) for v in actual)
    if isinstance(expected, (list, tuple, set)):
        # RuleBox/TypeQL list literals denote alternatives, including legacy
        # rows with the default == operator.
        return any(value_matches(actual, "==", v, properties) for v in expected)
    a, b = _scalar(actual), _scalar(expected)
    op = {"eq": "==", "in": "==", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}.get(operator, operator)
    if op in {"<", "<=", ">", ">="} or isinstance(a, (int, float)) or isinstance(b, (int, float)):
        try:
            a, b = float(a), float(b)
            if not math.isfinite(a) or not math.isfinite(b):
                return False
        except (TypeError, ValueError):
            return False
    else:
        a, b = str(a), str(b)
    return {"==": lambda: a == b, "!=": lambda: a != b,
            "<": lambda: a < b, "<=": lambda: a <= b,
            ">": lambda: a > b, ">=": lambda: a >= b}[op]()


def property_filters_match(properties, filters):
    for field, expected in filters.items():
        op = "=="
        if field.startswith(("min", "max")) and len(field) > 3:
            op, field = (">=" if field.startswith("min") else "<="), field[3].lower() + field[4:]
        if isinstance(expected, dict):
            op = expected.get("operator", op)
        if not value_matches(properties.get(field), op, expected, properties):
            return False
    return True


def _relation_identity(relation):
    props = relation.properties or {}
    return str(props.get("_relationId") or props.get("relationId") or "relation:" + fingerprint({
        "source": relation.source, "target": relation.target, "type": relation.relation_type,
        "properties": props})[:32])


class CompiledRuleboxEvaluator:
    def __init__(self, program, *, maximum_checks=2_000_000, deadline_seconds=10, clock=time.monotonic):
        self.program = program
        self.maximum_checks = maximum_checks
        self.deadline_seconds = deadline_seconds
        self.clock = clock

    def evaluate(self, source_graph, *, symbols=None, materialize=True):
        """Evaluate an isolated, complete as-of graph; input is never mutated."""
        started = self.clock()
        graph = deepcopy(source_graph)
        if any(e.properties.get("ontologyBox") == "InferenceBox" for e in graph.entities):
            raise ValueError("Compiled input must contain source facts, not previous inference output")
        entities = {e.entity_id: e for e in graph.entities}
        if len(entities) != len(graph.entities):
            raise ValueError("Duplicate source fact identity")
        for relation in graph.relations:
            if relation.source not in entities or relation.target not in entities:
                raise ValueError("Incomplete source graph endpoint: " + relation.relation_type)
            relation.properties["_relationId"] = _relation_identity(relation)
        selected = set(str(s).upper() for s in (symbols or []))
        rules = {r.rule_id: r for r in self.program.rules()}
        depended_on = {rid for _, dependencies in self.program.dependencies for rid in dependencies}
        matches, evaluated, checks = [], [], 0
        index = evidence_relation_index(graph)

        def check_budget():
            nonlocal checks
            checks += 1
            if checks > self.maximum_checks or self.clock() - started > self.deadline_seconds:
                raise RuleboxExecutionLimit("RuleBox execution budget exhausted; no partial decision published")

        def condition_match(subject, condition):
            check_budget()
            if condition.kind == "subject_property":
                props = {**subject.properties, "entityId": subject.entity_id, "kind": subject.kind}
                return value_matches(props.get(condition.field), condition.operator, condition.value, props), []
            candidates = index["bySubjectDirectionType"].get((subject.entity_id, condition.direction, condition.relation_type), [])
            found = []
            for relation, target in candidates:
                check_budget()
                if target is None or (condition.target_kind and target.kind != condition.target_kind):
                    continue
                target_props = {**target.properties, "entityId": target.entity_id, "kind": target.kind}
                relation_props = {**relation.properties, "weight": relation.weight}
                if property_filters_match(target_props, condition.target_property_filters) and property_filters_match(relation_props, condition.relation_property_filters):
                    found.append(relation.properties["_relationId"])
            return bool(found), sorted(set(found))

        def match_rule(rule, subject):
            conditions, evidence, groups = [], set(), set()
            for c in rule.conditions:
                matched, ids = condition_match(subject, c)
                if (c.role == "required" and not matched) or (c.role == "not" and matched):
                    return None
                if not matched and c.role in {"any", "optional"}:
                    continue
                if c.role in {"any", "optional"}:
                    groups.add(c.evidence_group_key or c.condition_id)
                detail = {"conditionId": c.condition_id, "kind": c.kind, "role": c.role,
                          "matched": True, "matchedByRuleEngine": True, "ruleEngineBackend": COMPILED_RULEBOX_VERSION}
                if c.role == "not":
                    detail["absenceSatisfied"] = True
                elif c.kind == "subject_property":
                    detail.update(field=c.field, operator=c.operator, value=c.value, observedValue=subject.properties.get(c.field))
                else:
                    detail.update(relationType=c.relation_type, relationId=ids[0], evidenceRelationIds=ids)
                    evidence.update(ids)
                conditions.append(detail)
            if any(c.role in {"any", "optional"} for c in rule.conditions) and len(groups) < rule.any_condition_min_count:
                return None
            if groups:
                conditions.append({"conditionId": "any-condition-group", "kind": "any-condition-group",
                    "role": "required", "matchedByRuleEngine": True, "ruleEngineBackend": COMPILED_RULEBOX_VERSION,
                    "detailDeferred": False, "minimumCount": rule.any_condition_min_count,
                    "matchedConditionIds": [c["conditionId"] for c in conditions if c["role"] in {"any", "optional"}],
                    "matchedEvidenceGroupKeys": sorted(groups)})
            return {"ruleId": rule.rule_id, "sourceId": subject.entity_id, "sourceKind": rule.source_kind,
                    "sourceLabel": subject.label, "matchedConditions": conditions,
                    "evidenceRelationIds": sorted(evidence), "conditionDetailSource": COMPILED_RULEBOX_VERSION}

        for rid in self.program.rule_order:
            rule = rules[rid]
            scope = rule.model_input_contract.get("evolutionScope") or {}
            subjects = [e for e in graph.entities if e.kind == rule.source_kind
                        and (not selected or rule.source_kind in {"portfolio", "account"}
                             or str(e.properties.get("symbol") or "").upper() in selected)
                        and (not scope or (str(e.properties.get("symbol")) == str(scope["symbol"])
                             and graph.worldview.get("worldId") == scope["worldId"]))]
            rule_matches = []
            for subject in subjects:
                match = match_rule(rule, subject)
                if match:
                    matches.append(match)
                    rule_matches.append(match)
                    if materialize or rid in depended_on:
                        materialize_rule_inference(graph, rule, subject, match, evidence_index=index)
                else:
                    probe = management_resolution_probe(rule)
                    if not probe:
                        continue
                    conditions = probe["conditions"]
                    if not all(value_matches(subject.properties.get(c["field"]), c["operator"], c["value"], subject.properties)
                               for c in conditions):
                        continue
                    response = probe["managementResponse"]
                    details = []
                    for c in conditions:
                        cid = c["condition_id"]
                        original = next(c for c in rule.conditions if c.condition_id == cid)
                        resolved = cid == response["conditionId"]
                        details.append({"conditionId": cid, "kind": "subject_property", "role": "required",
                            "field": original.field, "operator": original.operator, "value": original.value,
                            "observedValue": subject.properties.get(original.field),
                            "ruleEngineBackend": COMPILED_RULEBOX_VERSION,
                            "matched": not resolved, "matchedByRuleEngine": not resolved,
                            **({"resolutionMatchedByRuleEngine": True, "resolutionOperator": response["objective"]["operator"]} if resolved else {})})
                    resolution = {"ruleId": rule.rule_id, "sourceId": subject.entity_id, "sourceLabel": subject.label,
                        "matchedConditions": details, "evidenceRelationIds": [],
                        "managementResolution": {"version": "compiled-policy-resolution-v1", "response": response, "matched": True},
                        "conditionDetailSource": COMPILED_RULEBOX_VERSION}
                    matches.append(resolution)
                    if materialize:
                        materialize_management_resolution(graph, rule, subject, resolution, index)
            evaluated.append({"ruleId": rid, "sourceKind": rule.source_kind,
                              "matchedSourceIds": [m["sourceId"] for m in rule_matches],
                              "evaluatedSourceIds": [s.entity_id for s in subjects]})
            if rule_matches and rid in depended_on:
                for relation in graph.relations:
                    if "_relationId" not in relation.properties:
                        relation.properties["_relationId"] = _relation_identity(relation)
                index = evidence_relation_index(graph)
        check_budget()
        return {"status": "ok", "backend": COMPILED_RULEBOX_VERSION,
                "compiledRuleboxFingerprint": self.program.fingerprint,
                "enabledRuleIds": list(self.program.rule_order),
                "complete": True, "matches": matches, "evaluations": evaluated,
                "checks": checks, "durationMs": round((self.clock() - started) * 1000, 3), "graph": graph}
