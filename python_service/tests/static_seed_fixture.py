"""Secret-free static release scenarios, also run against the original facade."""

import copy
import hashlib
import json
from functools import lru_cache
from unittest.mock import patch

from digital_twin.modules.model_registry.domain.investment_ubiquitous_language import (
    investment_language_registry,
)
from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.modules.model_registry.domain.ontology_rulebox_governance import rulebox_rules_hash
from digital_twin.infrastructure.graph_store_lifecycle import (
    ontology_release_seed_artifact,
    ontology_seed_graph_from_artifact,
)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


@lru_cache(maxsize=1)
def _artifact():
    value = ontology_release_seed_artifact(
        default_graph_inference_rules()[:1],
        language_registry=investment_language_registry({}),
        release_bundle={"release_id": "fixture-frozen-release"},
    )
    value["rules"][0]["legacy_extension"] = {"version": "fixture-authored-v1"}
    value["ruleboxFingerprint"] = rulebox_rules_hash(value["rules"])
    return value


def artifact():
    return copy.deepcopy(_artifact())


def repository(api, **kwargs):
    with patch.object(api, "runtime_settings", return_value={}):
        return api.TypeDBOntologyGraphRepository("127.0.0.1:1", **kwargs)


def restore_scenario(api, mode="ok"):
    source = artifact()
    repo = repository(api)
    events = []

    def call(name, result):
        def execute(*args, **kwargs):
            events.append(name)
            if name in {"static", "manifest"}:
                rows = kwargs.get("rules_payload") if name == "static" else args[1]
                events.append(["authored", fingerprint(rows)])
            return copy.deepcopy(result)

        return execute

    repo.sync_base_schema_contract = call("schema", {"saved": mode != "schema-error"})
    repo.save_static_seed_boxes = call("static", {"saved": mode != "static-error"})
    repo.save_seed_static_manifest = call(
        "manifest", {"saved": mode != "manifest-error"}
    )
    repo.clear_rulebox_snapshot_cache = call("cache-clear", None)
    repo.rulebox_snapshot = call(
        "rulebox", {"status": "ok", "sourceRulesHash": "fixture-executable-hash"}
    )
    repo.active_tbox_metadata = call(
        "tbox", {"status": "ok", "fingerprint": source["tboxFingerprint"]}
    )
    repo.read_seed_static_manifest = call(
        "manifest-read",
        {
            "status": "ok",
            "metadata": {
                "ruleboxRulesHash": (
                    "wrong"
                    if mode == "readback-error"
                    else source["ruleboxFingerprint"]
                ),
                "tboxFingerprint": source["tboxFingerprint"],
            },
        },
    )
    if mode == "hash-error":
        source["ruleboxFingerprint"] = "wrong"
    if mode == "contract-error":
        source["version"] = "unsupported"
    if mode == "missing-box":
        source["graph"]["entities"] = [
            e
            for e in source["graph"]["entities"]
            if e["properties"].get("ontologyBox") != "LanguageGovernance"
        ]
    before = copy.deepcopy(source)
    result = api.TypeDBOntologyGraphRepository.seed_release_artifact.__wrapped__(
        repo, source
    )
    return {"result": result, "events": events, "authoredUnchanged": source == before}


def execution_contracts(api):
    repo = repository(api)
    source = artifact()
    graph = ontology_seed_graph_from_artifact(source)
    identity = repo.seed_static_manifest_metadata(
        graph, source["rules"], source["tboxMetadata"]
    )
    generated = repo.graph_with_static_seed_generation(
        graph, repo.seed_static_box_names(), repo.static_seed_generation_ids(identity)
    )
    result = {
        "schema": fingerprint(repo.schema_query()),
        "schemaIdentity": fingerprint(repo.base_schema_contract_metadata()),
        "manifestIdentity": fingerprint(identity),
        "generatedRows": fingerprint(repo.graph_persistence_rows(generated)),
        "sentinels": fingerprint(repo.seed_static_sentinels(graph, identity)),
    }
    for mode in [
        "ok",
        "schema-error",
        "static-error",
        "manifest-error",
        "readback-error",
        "hash-error",
        "contract-error",
        "missing-box",
    ]:
        result["restore:" + mode] = fingerprint(restore_scenario(api, mode))
    return result
