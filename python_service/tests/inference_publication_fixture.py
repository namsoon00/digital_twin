"""Credential-free transaction recorder for publication extraction contracts."""

from contextlib import ExitStack
import hashlib
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology


WORLD = "portfolio:publication-test:alpha"
OTHER_WORLD = "portfolio:publication-test:beta"
NOW = "2026-01-02T03:04:05Z"
METHODS = (
    "write_inferencebox_graph", "inference_generation_candidate_summary",
    "validate_inference_generation_candidate", "activate_inference_generation",
    "prune_inferencebox_generations",
)


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def graph_fixture(empty=False):
    graph = PortfolioOntology("publication-test")
    graph.worldview = {
        "worldId": WORLD, "accountId": "publication-test", "tenantId": "test",
        "worldType": "PortfolioWorld", "inferenceGenerationId": "generation:new",
        "inferenceGenerationAt": NOW, "sourceAboxSnapshotId": "abox:source",
        "nativeInferenceEvaluationComplete": True,
        "nativeInferenceOutcome": "no-match" if empty else "matched",
    }
    if not empty:
        graph.entities = [
            OntologyEntity("reference:test", "Reference", "inference-reference", {"ontologyBox": "InferenceBox"}),
            OntologyEntity("trace:test", "Trace", "inference-trace", {"ontologyBox": "InferenceBox"}),
        ]
        graph.relations = [OntologyRelation("reference:test", "trace:test", "HAS_INFERENCE", properties={"ontologyBox": "InferenceBox"})]
    return graph


class RecordingTransaction:
    def __init__(self, store, mode, options):
        self.store = store
        self.mode = mode
        self.options = options
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
            marker = json.loads(query[7:]) if stage == "MARKER" else {}
            failure = self.store.failure
            if stage == "NODE" and failure in {"node-write", "timeout", "retry-node"}:
                if failure != "retry-node" or not self.store.retried:
                    self.store.retried = True
                    raise RuntimeError("deadline exceeded" if failure == "timeout" else "node write failed")
            if stage == "GIVEN" and failure in {"given-fallback", "relation-write"}:
                raise RuntimeError("given row write failed")
            if stage == "RELATION" and failure == "relation-write":
                raise RuntimeError("relation write failed")
            if marker.get("kind") == "inference-generation-candidate" and failure == "candidate-marker":
                raise RuntimeError("candidate marker write failed")
            if marker.get("kind") == "inference-generation" and failure == "activation-insert":
                raise RuntimeError("active marker write failed")
            if stage == "match" and failure == "prune-write":
                raise RuntimeError("prune write failed")
            self.pending.append([query, given_rows])
            return []
        return SimpleNamespace(resolve=resolve)

    def commit(self):
        self.store.journal.append(["commit-attempt"])
        if self.store.failure == "activation-commit" and any(
            query.startswith("MARKER ") and json.loads(query[7:]).get("kind") == "inference-generation"
            for query, _ in self.pending
        ):
            raise RuntimeError("activation commit failed")
        for query, given_rows in self.pending:
            self.store.apply(query, given_rows)
        self.committed = True
        self.store.journal.append(["commit"])


class RecordingPublicationStore:
    address = "test-driver"
    database = "isolated-publication-test"

    def __init__(self, implementation, failure=""):
        self.implementation = implementation
        self.failure = failure
        self.retried = False
        self.journal = []
        self.nodes = {}
        self.relations = {}
        self.candidate_marker = None
        self.active_by_world = {WORLD: "generation:old", OTHER_WORLD: "generation:other"}
        self.pruned = []
        self.source_abox = "abox:source"

    def transaction(self, database, mode, options=None):
        assert database == self.database
        return RecordingTransaction(self, mode, options)

    def apply(self, query, given_rows):
        if query.startswith("NODE "):
            for row in json.loads(query[5:]):
                self.nodes[row["id"]] = row
        elif query.startswith("RELATION ") or query.startswith("GIVEN "):
            for row in given_rows if given_rows is not None else json.loads(query[9:]):
                self.relations[row["id"]] = row
        elif query.startswith("MARKER "):
            row = json.loads(query[7:])
            if row["kind"] == "inference-generation-candidate":
                self.candidate_marker = row
            else:
                self.active_by_world[row["worldId"]] = row["snapshotId"]
        elif "has ontology-snapshot-id" in query:
            generation = re.search(r'has ontology-snapshot-id ("(?:\\.|[^"\\])*")', query)
            world = re.search(r'has ontology-world-id ("(?:\\.|[^"\\])*")', query)
            self.pruned.append([json.loads(generation[1]), json.loads(world[1]) if world else ""])
        elif 'has ontology-kind "inference-generation"' in query:
            world = re.search(r'has ontology-world-id ("(?:\\.|[^"\\])*")', query)
            self.active_by_world.pop(json.loads(world[1]) if world else "", None)

    def driver_imports(self):
        if self.failure == "driver-missing":
            return None, "test driver unavailable"
        return (object, object, object, object, SimpleNamespace(READ="read", WRITE="write")), None

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

    def read_transaction_options(self):
        return "read-options"

    def inferencebox_write_transaction_query_count(self):
        return 2

    def write_query_max_bytes(self, settings=None):
        return 100000

    def inferencebox_relation_batch_size(self, settings=None):
        return 2

    def inferencebox_given_relation_batch_size(self, settings=None):
        return 2

    def node_rows(self, graph):
        return [{"id": item.entity_id, "kind": item.kind, "ontologyBox": item.properties["ontologyBox"]} for item in graph.entities]

    def rows_for_relations(self, graph):
        return [{"id": str(index), "source": item.source, "target": item.target, "ontologyBox": item.properties["ontologyBox"]} for index, item in enumerate(graph.relations)]

    def support_relation_rows(self, graph):
        return []

    def batched_node_insert_queries(self, rows, updated_at, batch_size, max_query_bytes):
        self.journal.append(["node-plan", updated_at, batch_size, max_query_bytes])
        return ["NODE " + encode([row]) for row in rows]

    def inferencebox_given_relation_insert_plans(self, rows, updated_at, settings=None):
        if not rows:
            return []
        if self.failure == "legacy-relations":
            return [{"query": "RELATION " + encode(rows), "rows": rows}]
        return [{"query": "GIVEN relations", "givenRows": rows, "rows": rows}]

    def batched_relation_insert_queries(self, rows, updated_at, batch_size, max_query_bytes):
        return ["RELATION " + encode(rows)]

    def node_insert_query(self, row, updated_at):
        self.journal.append(["marker-plan", updated_at, row["kind"]])
        return "MARKER " + encode(row)

    def read_rows_in_transaction(self, tx, query, columns, label=""):
        self.journal.append(["read", query, columns, label])
        if self.failure == "validation-read":
            raise RuntimeError("candidate summary unavailable")
        if label.endswith("entities"):
            return [{"count": len(self.nodes) + bool(self.candidate_marker) - (self.failure == "validation-count")}]
        if label.endswith("relations"):
            return [{"count": len(self.relations)}]
        if label.endswith("traces"):
            return [{"count": sum(row["kind"] == "inference-trace" for row in self.nodes.values())}]
        return [{"json": self.candidate_marker["propertiesJson"]}] if self.candidate_marker else []

    def active_abox_snapshot_id(self, world_id=""):
        self.journal.append(["active-abox", world_id])
        return "abox:changed" if self.failure == "validation-source" else self.source_abox

    def read_inference_generation_records(self, published_only=True, world_id=""):
        self.journal.append(["generation-records", published_only, world_id])
        if self.failure == "prune-read":
            raise RuntimeError("generation read failed")
        return [
            {"generationId": "generation:old", "latestAt": "2026-01-01", "publicationStatus": "active"},
            {"generationId": "generation:new", "latestAt": NOW, "publicationStatus": "active"},
            {"generationId": "generation:orphan", "latestAt": NOW, "publicationStatus": "candidate"},
        ]

    def _inference_publication_runtime(self):
        return self.implementation._inference_publication_runtime(self)


def _delegate(name):
    def call(self, *args, **kwargs):
        return getattr(self.implementation, name)(self, *args, **kwargs)
    return call


for _name in METHODS:
    setattr(RecordingPublicationStore, _name, _delegate(_name))


SCENARIOS = (
    "success", "fresh", "retry-node", "given-fallback", "legacy-relations",
    "node-write", "timeout", "relation-write", "candidate-marker",
    "validation-read", "validation-count", "validation-source",
    "activation-insert", "activation-commit", "driver-missing", "disabled",
    "empty-complete", "empty-incomplete", "stable-lease",
    "prune", "prune-read", "prune-write",
)


def run_scenario(api, scenario):
    graph = graph_fixture(empty=scenario.startswith("empty"))
    if scenario == "fresh":
        graph.worldview["freshInferenceGeneration"] = True
    if scenario == "empty-incomplete":
        graph.worldview["nativeInferenceEvaluationComplete"] = False
    if scenario == "stable-lease":
        graph.worldview.update(sourceAboxValidatedUnderWriteLease=True, sourceAboxGenerationValid=True)
    store = RecordingPublicationStore(api.TypeDBOntologyGraphRepository, scenario)
    if scenario == "disabled":
        store.address = ""
    with ExitStack() as stack:
        stack.enter_context(patch.object(api, "runtime_settings", return_value={"typedbInferenceBoxNodeBatchSize": 1}))
        stack.enter_context(patch.object(api, "utc_now", return_value=NOW))
        stack.enter_context(patch("time.monotonic", return_value=0.0))
        if scenario.startswith("prune"):
            store.active_by_world[WORLD] = "generation:new"
            result = store.prune_inferencebox_generations("generation:new", keep_count=1, world_id=WORLD)
        else:
            result = store.write_inferencebox_graph(graph)
    return {
        "result": result, "journal": store.journal,
        "activeByWorld": store.active_by_world, "pruned": store.pruned,
    }


def contract_fingerprints(api):
    result = {}
    for scenario in SCENARIOS:
        payload = run_scenario(api, scenario)
        encoded = encode(payload).encode()
        result[scenario] = {"bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}
    return result
