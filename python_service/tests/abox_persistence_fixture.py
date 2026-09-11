"""Synthetic ABox storage journal; no credentials, sockets or repository setup."""

from contextlib import ExitStack
import hashlib
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, PortfolioOntology


WORLD = "portfolio:abox-test:alpha"
OTHER_WORLD = "portfolio:abox-test:beta"
NOW = "2026-01-02T03:04:05Z"
OLD = "abox-manifest:old"
NEW = "abox-manifest:new"
METHODS = {
    "write_persistence_rows": "writer",
    "replace_scoped_abox_control_graph": "controls",
    "clear_scoped_abox_pending_activation": "controls",
    "activate_scoped_abox_manifest": "lifecycle",
    "prepare_pending_abox_activation_for_inference": "lifecycle",
    "finalize_scoped_abox_manifest": "lifecycle",
}


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def rows_fixture():
    nodes = [{"id": "node:" + str(i), "scopeId": "scope:a"} for i in range(3)]
    relations = [{
        "id": "relation:" + str(i), "scopeId": "scope:a", "type": "HAS_VALUE",
        "sourceStorageId": "node:0", "targetStorageId": "node:" + str(i + 1),
    } for i in range(2)]
    return nodes, relations


def control_graph(manifest=NEW, pending=True, scopes=("scope:a",)):
    entities = [OntologyEntity("active", "Active", "worldview-manifest-active-pointer", {
        "worldId": WORLD, "manifest": manifest,
    })]
    entities.extend(OntologyEntity(scope, scope, "abox-scope-active-pointer", {
        "worldId": WORLD, "scopeId": scope, "manifest": manifest,
    }) for scope in scopes)
    if pending:
        entities.append(OntologyEntity("pending", "Pending", "abox-activation-pending", {
            "worldId": WORLD, "manifest": manifest, "previous": OLD,
            "targetSymbols": ["TEST"], "activationStatus": "pending-native-inference",
        }))
    return PortfolioOntology("abox-test", entities=entities)


def delete_query(kind, world_id="", scope_id=""):
    return (
        'match $n isa ontology-node, has ontology-box "ABoxControl", has ontology-kind '
        + json.dumps(kind)
        + (", has ontology-world-id " + json.dumps(world_id) if world_id.strip() else "")
        + (", has ontology-scope-id " + json.dumps(scope_id) if scope_id.strip() else "")
        + "; delete $n;"
    )


class RecordingTransaction:
    def __init__(self, store, mode, options):
        self.store, self.mode, self.options = store, mode, options
        self.pending = []
        self.committed = False

    def __enter__(self):
        self.store.journal.append(["begin", self.mode, self.options])
        return self

    def __exit__(self, error_type, error, traceback):
        self.store.journal.append(["exit", "committed" if self.committed else "rolled-back", str(error or "")])
        return False

    def query(self, query, given_rows=None):
        def resolve():
            self.store.journal.append(["query", query, given_rows])
            stage = query.split(" ", 1)[0]
            failure = self.store.failure
            if stage == "NODE" and failure in {"node-write", "timeout", "retry-node"}:
                if failure != "retry-node" or not self.store.retried:
                    self.store.retried = True
                    raise RuntimeError("deadline exceeded" if failure == "timeout" else "node write failed")
            if stage == "GIVEN" and failure in {"given-fallback", "relation-write"}:
                raise RuntimeError("given row write failed")
            if stage == "RELATION" and failure == "relation-write":
                raise RuntimeError("relation write failed")
            if stage == "CONTROL" and failure == "control-insert":
                raise RuntimeError("control insert failed")
            if stage == "match" and failure == "control-delete":
                raise RuntimeError("control delete failed")
            self.pending.append([query, given_rows])
            return []
        return SimpleNamespace(resolve=resolve)

    def commit(self):
        self.store.journal.append(["commit-attempt"])
        if self.store.failure == "commit" or (
            self.store.failure == "second-commit" and self.store.commits == 1
        ):
            raise RuntimeError("control commit failed")
        for query, given_rows in self.pending:
            self.store.apply(query, given_rows)
        self.committed = True
        self.store.commits += 1
        self.store.journal.append(["commit"])


class RecordingABoxStore:
    address = "test-driver"
    database = "isolated-abox-test"

    def __init__(self, implementation, failure=""):
        self.implementation, self.failure = implementation, failure
        self.journal = []
        self.retried = False
        self.commits = 0
        self.nodes, self.relations = {}, {}
        self.controls = {
            (world, entity.kind, entity.properties.get("scopeId", "")): dict(entity.properties, worldId=world)
            for world in [WORLD, OTHER_WORLD]
            for entity in control_graph(OLD, pending=False, scopes=("scope:a", "scope:b")).entities
        }
        self.active_reads = 0
        self.pending_reads = 0
        self.target_symbols = ["TEST"]

    def state(self):
        return [[list(key), value] for key, value in sorted(self.controls.items())]

    def stage_candidate(self):
        self.controls[WORLD, "abox-activation-pending", ""] = {
            "worldId": WORLD, "manifest": NEW, "previous": OLD,
            "targetSymbols": list(self.target_symbols), "activationStatus": "staged-native-inference",
        }

    def transaction(self, database, mode, options=None):
        assert database == self.database
        return RecordingTransaction(self, mode, options)

    def apply(self, query, given_rows):
        stage = query.split(" ", 1)[0]
        if stage == "NODE":
            for row in json.loads(query[5:]):
                self.nodes[row["id"]] = row
        elif stage in {"GIVEN", "RELATION"}:
            for row in given_rows if given_rows is not None else json.loads(query[9:]):
                self.relations[row["id"]] = row
        elif stage == "CONTROL":
            row = json.loads(query[8:])
            props = row["properties"]
            self.controls[props["worldId"], row["kind"], props.get("scopeId", "")] = props
        elif stage == "match":
            values = dict((key, json.loads(value)) for key, value in re.findall(
                r'has (ontology-kind|ontology-world-id|ontology-scope-id) ("(?:\\.|[^"\\])*")', query,
            ))
            for key in list(self.controls):
                if key[1] != values["ontology-kind"]:
                    continue
                if "ontology-world-id" in values and key[0] != values["ontology-world-id"]:
                    continue
                if "ontology-scope-id" in values and key[2] != values["ontology-scope-id"]:
                    continue
                del self.controls[key]

    def driver_imports(self):
        if self.failure == "driver-missing":
            return None, "test driver missing"
        return (object, object, object, object, SimpleNamespace(WRITE="write")), None

    def driver_missing_result(self, error, graph):
        return {"status": "driver-missing", "reason": str(error)}

    def open_driver(self, imported):
        self.journal.append(["open-driver"])
        return self

    def close_driver(self, driver):
        assert driver is self
        self.journal.append(["close-driver"])

    def ensure_database(self, driver):
        self.journal.append(["ensure-database"])

    def ensure_schema(self, driver, imported):
        self.journal.append(["ensure-schema"])

    def with_typedb_retries(self, operation):
        try:
            return operation()
        except RuntimeError:
            if self.failure != "retry-node":
                raise
            self.journal.append(["retry"])
            return operation()

    def write_operation_timeout_seconds(self):
        return 0

    def write_transaction_options(self):
        return "write-options"

    def abox_node_batch_size(self, settings=None):
        return 2

    abox_relation_batch_size = abox_node_batch_size
    abox_write_transaction_query_count = abox_node_batch_size

    def write_query_max_bytes(self, settings=None):
        return 100000

    def scoped_abox_storage_reuse_plan(self, node_rows, relation_rows, assume_missing_storage=False):
        self.journal.append(["reuse-plan", assume_missing_storage])
        reused = self.failure == "reused"
        if reused:
            self.nodes = {row["id"]: row for row in node_rows}
        return {
            "status": "conflict" if self.failure == "identity-conflict" else "ok",
            "conflicts": [{"storageId": "node:0"}] if self.failure == "identity-conflict" else [],
            "storageLookupMode": "assume-missing" if assume_missing_storage else "verified",
            "nodeRows": list(node_rows), "relationRows": list(relation_rows),
            "nodeRowsToInsert": [] if reused else list(node_rows),
            "relationRowsToInsert": [] if reused else list(relation_rows),
            "reusedNodeRows": list(node_rows) if reused else [],
            "reusedRelationRows": list(relation_rows) if reused else [],
        }

    def scoped_abox_counts_by_scope(self, nodes, relations):
        return {"scope:a": {"entityCount": len(nodes), "relationCount": len(relations)}}

    def scoped_abox_relation_breakdown(self, rows):
        return {"HAS_VALUE": len(rows)}

    def batched_node_insert_queries(self, rows, updated_at, batch_size, max_query_bytes):
        self.journal.append(["node-plan", updated_at, batch_size, max_query_bytes])
        return ["NODE " + encode([row]) for row in rows]

    def given_relation_insert_plans(self, rows, updated_at, settings=None):
        self.journal.append(["relation-plan", updated_at])
        return [{"query": "RELATION " + encode([row]), "rows": [row]} if self.failure == "legacy-relations"
                else {"query": "GIVEN " + row["id"], "givenRows": [row], "rows": [row]} for row in rows]

    def batched_relation_insert_queries(self, rows, updated_at, batch_size, max_query_bytes):
        return ["RELATION " + encode(rows)]

    def current_state_storage_inventory(self, driver, imported, node_storage_ids=None, relation_storage_ids=None):
        self.journal.append(["inventory", node_storage_ids, relation_storage_ids])
        if self.failure == "inventory":
            raise RuntimeError("endpoint inventory failed")
        nodes = dict(self.nodes)
        if self.failure == "missing-endpoint":
            nodes.pop("node:1", None)
        return {"nodes": nodes}

    def missing_relation_endpoint_storage_ids(self, rows, nodes):
        return sorted({row[key] for row in rows for key in ["sourceStorageId", "targetStorageId"] if row[key] not in nodes})

    def missing_relation_endpoint_diagnostics(self, rows, storage_ids):
        return [{"storageId": storage_id} for storage_id in storage_ids]

    scoped_abox_control_delete_query = staticmethod(delete_query)

    def graph_insert_queries(self, graph):
        return ["CONTROL " + encode({"kind": entity.kind, "properties": entity.properties}) for entity in graph.entities]

    def scoped_manifest_metadata(self, manifest_id, world_id=""):
        self.journal.append(["manifest-metadata", manifest_id, world_id])
        return {"status": "incomplete" if self.failure == "incomplete" else "ok",
                "worldId": world_id, "worldviewManifestId": manifest_id}

    def active_abox_metadata(self, world_id=""):
        self.active_reads += 1
        self.journal.append(["active-read", world_id])
        if self.failure == "active-read" or (self.failure == "post-read" and self.commits):
            raise RuntimeError("active pointer unavailable")
        row = self.controls.get((world_id, "worldview-manifest-active-pointer", ""), {})
        return {"status": "ok" if row else "empty", "worldId": world_id,
                "worldviewManifestId": "abox-manifest:other" if self.failure == "active-changed" else row.get("manifest", "")}

    def pending_abox_activation(self, world_id=""):
        self.pending_reads += 1
        self.journal.append(["pending-read", world_id])
        if self.failure == "pending-read":
            raise RuntimeError("pending journal unavailable")
        row = self.controls.get((world_id, "abox-activation-pending", ""), {})
        if self.failure == "invalid-journal":
            return {"status": "invalid"}
        if not row:
            return {"status": "empty"}
        return {"status": "pending", "candidateAboxSnapshotId": row["manifest"],
                "previousAboxSnapshotId": row["previous"],
                "targetSymbols": [] if self.failure == "empty-target" else row["targetSymbols"],
                "activationStatus": "invalid-phase" if self.failure == "wrong-phase" else row["activationStatus"]}

    def scoped_manifest_control_delta(self, metadata, previous_metadata=None):
        return {"changedScopeIds": ["scope:a"], "replacedScopeIds": ["scope:a"], "replaceAllScopePointers": False}

    def scoped_manifest_control_graph(self, metadata, previous_metadata=None, pending_activation=False,
                                      inference_target_symbols=None, scope_ids=None):
        return control_graph(metadata["worldviewManifestId"], pending_activation, scope_ids)

    def inferencebox_recovery_metadata(self, world_id=""):
        self.journal.append(["inference-proof", world_id])
        if self.failure == "proof-read":
            raise RuntimeError("proof unavailable")
        return {"status": "ok", "nativeTypeDbReasoningCompleted": self.failure != "proof-incomplete",
                "nativeInferenceOutcome": "no-match" if self.failure == "no-match" else "matched",
                "sourceAboxSnapshotId": OLD if self.failure == "proof-stale" else NEW,
                "targetSymbols": [] if self.failure == "proof-target" else ["TEST"],
                "inferenceGenerationId": "inference:test"}

    def inferencebox_matches_pending_abox_activation(self, inferencebox, candidate_snapshot_id, target_symbols=None):
        self.journal.append(["proof-check", inferencebox, candidate_snapshot_id, target_symbols])
        return bool(inferencebox["generationAligned"] and inferencebox["nativeTypeDbReasoningCompleted"])

    def _abox_persistence_runtime(self):
        return self.implementation._abox_persistence_runtime(self)


def _delegate(name):
    def call(self, *args, **kwargs):
        return getattr(self.implementation, name)(self, *args, **kwargs)
    return call


for _name in METHODS:
    setattr(RecordingABoxStore, _name, _delegate(_name))


SCENARIOS = {
    "writer": ["success", "reused", "fresh", "identity-conflict", "node-write", "timeout", "retry-node",
               "given-fallback", "legacy-relations", "relation-write", "missing-endpoint", "inventory"],
    "activate": ["success", "incomplete", "driver-missing", "control-insert", "control-delete", "commit", "post-read"],
    "prepare": ["success", "empty", "already-ready", "invalid-journal", "empty-target", "wrong-phase",
                "active-changed", "pending-read", "active-read", "control-insert", "commit", "post-read"],
    "finalize": ["success", "no-match", "no-journal", "active-changed", "candidate-changed", "proof-read",
                 "proof-stale", "proof-target", "proof-incomplete", "commit", "control-delete", "driver-missing"],
}


def run_scenario(api, mode, scenario):
    store = RecordingABoxStore(api.TypeDBOntologyGraphRepository, scenario)
    trace = {}
    with ExitStack() as stack:
        stack.enter_context(patch.object(api, "runtime_settings", return_value={}))
        stack.enter_context(patch.object(api, "utc_now", return_value=NOW))
        stack.enter_context(patch("time.monotonic", return_value=0.0))
        try:
            if mode == "writer":
                nodes, relations = rows_fixture()
                result = store.write_persistence_rows(store, store.driver_imports(), nodes, relations,
                                                      telemetry=trace, assume_missing_storage=scenario == "fresh")
            elif mode == "activate":
                result = store.activate_scoped_abox_manifest(NEW, pending_activation=True,
                                                             inference_target_symbols=["TEST"], world_id=WORLD)
            elif mode == "prepare":
                if scenario != "empty":
                    store.stage_candidate()
                if scenario == "already-ready":
                    store.controls[WORLD, "worldview-manifest-active-pointer", ""]["manifest"] = NEW
                result = store.prepare_pending_abox_activation_for_inference(WORLD)
            else:
                store.stage_candidate()
                store.controls[WORLD, "worldview-manifest-active-pointer", ""]["manifest"] = NEW
                if scenario == "no-journal":
                    del store.controls[WORLD, "abox-activation-pending", ""]
                if scenario == "candidate-changed":
                    store.controls[WORLD, "abox-activation-pending", ""]["manifest"] = "abox-manifest:other"
                result = store.finalize_scoped_abox_manifest(NEW, OLD, world_id=WORLD)
        except Exception as error:
            result = {"raised": type(error).__name__, "reason": str(error)}
    return {"result": result, "trace": trace, "journal": store.journal, "controls": store.state(),
            "nodes": store.nodes, "relations": store.relations}


def contract_fingerprints(api):
    result = {}
    for mode, scenarios in SCENARIOS.items():
        for scenario in scenarios:
            content = encode(run_scenario(api, mode, scenario)).encode()
            result[mode + ":" + scenario] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    return result
