"""Deterministic, credential-free compiler inputs for the extraction contract."""

from collections import defaultdict
import hashlib
import json

from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules, governed_graph_inference_rules
from digital_twin.domain.ontology_rulebox_contracts import GraphInferenceRule


SYMBOLS = ["005930", "TSLA"]
WORLD = "portfolio:query-test:alpha"


def enabled_rules():
    return [GraphInferenceRule.from_dict({**rule.to_dict(), "enabled": True}) for rule in default_graph_inference_rules()]


def evidence_index(rules):
    types = {"HAS_MODEL_SIGNAL"}
    fields = set()
    target_kinds = set()
    for rule in rules:
        for condition in rule.to_dict().get("conditions") or []:
            types.add(str(condition.get("relation_type") or "").upper())
            expected = (condition.get("target_property_filters") or {}).get("field")
            if isinstance(expected, dict):
                expected = expected.get("value")
            values = expected if isinstance(expected, (list, tuple, set)) else [expected]
            fields.update(str(value) for value in values if value is not None)
            if condition.get("target_kind"):
                target_kinds.add(condition["target_kind"])
    types.discard("")
    relations = {
        symbol: {kind: ["relation:" + symbol + ":" + kind] for kind in sorted(types)}
        for symbol in SYMBOLS
    }
    return {
        "status": "verified", "fingerprint": "index:query-test:fixed",
        "index": {
            "sourceIdsBySymbol": {symbol: ["stock:" + symbol] for symbol in SYMBOLS},
            "sourceStorageIdsBySourceId": {"stock:" + symbol: "node:" + symbol + ":fixed" for symbol in SYMBOLS},
            "relationStorageIdsBySymbolAndType": relations,
            "version": "native-rule-evidence-read-index-v3",
            "relationStorageIdsBySymbolAndTypeAndField": {
                symbol: {kind: {field: ids for field in sorted(fields)} for kind, ids in values.items()}
                for symbol, values in relations.items()
            },
            "relationStorageIdsBySymbolAndTypeAndTargetKind": {
                symbol: {kind: {target: ids for target in sorted(target_kinds)} for kind, ids in values.items()}
                for symbol, values in relations.items()
            },
        },
    }


def contract_outputs(api):
    rules = enabled_rules()
    index = evidence_index(rules)
    yield "catalog", "rules", [rule.to_dict() for rule in rules]
    yield "schema", "capabilities", api.typedb_rule_schema_capability_contract()
    yield "profiles", "catalog", api.typedb_native_reasoning_profile(rules)
    for rule in rules:
        payload = rule.to_dict()
        key = rule.rule_id
        yield "match", key + ":legacy", api.typedb_native_match_query(payload, SYMBOLS)
        yield "match", key + ":scoped", api.typedb_native_match_query(
            payload, SYMBOLS, scoped_manifest_only=True, world_id=WORLD,
        )
        yield "match", key + ":bound-manifest", api.typedb_native_match_query(
            payload, SYMBOLS, scoped_manifest_only=True, world_id=WORLD,
            manifest_id_variable="$fixedManifest", bind_active_manifest=False,
            include_any_conditions=False, compact_result_rows=True,
        )
        yield "indexed", key, api.typedb_native_indexed_evidence_match_query(payload, SYMBOLS, index, WORLD)
        yield "indexed", key + ":fallback", api.typedb_native_rule_runtime_query_plan(
            payload, SYMBOLS, scoped_manifest_only=True, world_id=WORLD,
            evidence_read_index={"status": "unavailable"}, compact_result_rows=True,
        )
        for condition_index, condition in enumerate(payload.get("conditions") or []):
            condition_key = key + ":" + str(condition.get("condition_id") or "")
            yield "condition", condition_key, api.typedb_native_condition_check_query(
                condition, "stock:005930", condition_index, scoped_manifest_only=True, world_id=WORLD,
            )
    for rule in governed_graph_inference_rules():
        payload = rule.to_dict()
        if not any(condition.get("role") in {"any", "optional", "negative"} for condition in payload.get("conditions") or []):
            continue
        yield "governed", rule.rule_id, api.typedb_native_match_query(payload, SYMBOLS, world_id=WORLD)
        yield "any-group", rule.rule_id + ":scoped", api.typedb_native_any_group_check_query(
            payload, "stock:005930", scoped_manifest_only=True, world_id=WORLD,
        )
        yield "any-group", rule.rule_id + ":indexed", api.typedb_native_any_group_check_query(
            payload, "stock:005930", world_id=WORLD,
            active_source_storage_id="node:005930:fixed",
            active_relation_storage_ids_by_type=index["index"]["relationStorageIdsBySymbolAndType"]["005930"],
        )
    for complete in [False, True]:
        type_index = {
            symbol: list(index["index"]["relationStorageIdsBySymbolAndType"][symbol]) if complete else []
            for symbol in SYMBOLS
        }
        plan = api.typedb_native_rule_execution_plan(rules, SYMBOLS, type_index, query_limit=1)
        yield "planning", "complete:" + str(complete), api.typedb_native_rule_execution_plan_summary(plan)
    bridge = api.typedb_model_signal_bridge_batch_plan(
        [{"rule": rule, "candidateSymbols": SYMBOLS} for rule in rules], SYMBOLS,
    )
    yield "bridge", "summary", api.typedb_model_signal_bridge_batch_plan_summary(bridge)
    for batch in bridge["batches"]:
        for indexed in [False, True]:
            yield "bridge", batch["sourceScope"] + ":" + str(indexed), api.typedb_model_signal_bridge_batch_query(
                batch, world_id=WORLD, evidence_read_index=index if indexed else {},
            )
        yield "bridge", batch["sourceScope"] + ":unknown-contract", api.typedb_dispatch_model_signal_bridge_rows(
            batch, [{"sourceId": "stock:005930", "hypothesisContractId": "test:unknown"}],
        )
    for attribute in ["ontology-symbol", "ontology-value-number", "ontology-unknown"]:
        for number, value in enumerate([None, "", 0, -0.0, 0.00006, float("inf"), True, False, "x\"; $other", {"default": "005930"}]):
            yield "literals", attribute + ":" + str(number), api.typedb_literal_for_attribute(attribute, value)


def contract_fingerprints(api):
    groups = defaultdict(list)
    for group, key, payload in contract_outputs(api):
        groups[group].append([key, payload])
    result = {}
    for group, cases in sorted(groups.items()):
        encoded = json.dumps(cases, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        result[group] = {"count": len(cases), "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}
    return result
