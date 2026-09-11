"""Synthetic candidate images and recovery journals, never account data."""

import copy
from dataclasses import asdict
import hashlib
import json

from digital_twin.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.domain.ontology_current_state import (
    CURRENT_STATE_ABOX_PERSISTENCE_MODE, LEGACY_CURRENT_STATE_ABOX_PERSISTENCE_MODE,
)
from digital_twin.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION


WORLD = "portfolio:fixture:owner"
SHARED_WORLD = "premise:fixture"
OLD = "manifest:fixture:old"
NEW = "manifest:fixture:new"
STATE = "symbol:AAA:state"
EVIDENCE = "symbol:AAA:evidence"
LINK = "link:symbol:AAA:evidence"


def node(key, scope, generation, **values):
    return {"id": key, "ontologyBox": "ABox", "worldId": WORLD, "scopeId": scope,
            "snapshotId": generation, "scopeGenerationId": generation, "propertiesJson": "{}", **values}


def image_fixture(api):
    active_stock = node("stock:AAA", STATE, "state-old", currentPrice=100)
    current_stock = node("stock:AAA", STATE, "state-new", currentPrice=102)
    news = node("article:published", EVIDENCE, "evidence-old", title="Published evidence")
    relation = {"source": "stock:AAA", "target": "article:published", "type": "HAS_EVIDENCE",
                "scopeId": LINK, "scopeType": "link", "worldId": WORLD, "ontologyBox": "ABox",
                "snapshotId": "link-old", "scopeGenerationId": "link-old", "propertiesJson": "{}"}
    for side, endpoint in [("source", active_stock), ("target", news)]:
        relation[side + "StorageId"] = api.ontology_storage_id(endpoint, endpoint["id"], "node")
    plan = [
        {"scopeId": STATE, "scopeType": "symbol", "generationId": "state-new", "logicalGenerationId": "logical-state-new", "fingerprint": "changed", "entityCount": 1, "relationCount": 0},
        {"scopeId": EVIDENCE, "scopeType": "symbol", "generationId": "evidence-old", "logicalGenerationId": "logical-evidence", "fingerprint": "same", "entityCount": 1, "relationCount": 0},
        {"scopeId": LINK, "scopeType": "link", "generationId": "link-new", "logicalGenerationId": "logical-link", "fingerprint": "same", "entityCount": 0, "relationCount": 1, "dependencyScopeIds": [STATE, EVIDENCE]},
    ]
    return {
        "current_node_rows": [current_stock, news], "current_relation_rows": [],
        "active_scope_rows": {"nodeRows": [news], "endpointNodeRows": [active_stock], "relationRows": [relation]},
        "physical_scope_plan": plan, "semantic_changed_scope_ids": [STATE],
        "physical_changed_scope_ids": [STATE, LINK], "deferred_scope_ids": [EVIDENCE],
        "candidate_manifest_id": NEW,
    }


class RecoveryStore:
    def __init__(self, implementation, failure=""):
        self.implementation = implementation
        self.address = "recovery.fixture.invalid:1729"
        self.pending = {"status": "pending", "candidateAboxSnapshotId": NEW, "previousAboxSnapshotId": OLD,
                        "targetSymbols": ["AAA"], "activationStatus": "pending-native-inference"}
        self.active = {"status": "ok", "aboxSnapshotId": NEW}
        self.marker = {"status": "ok", "nativeTypeDbReasoningCompleted": True, "nativeInferenceOutcome": "matched",
                       "sourceAboxSnapshotId": NEW, "targetSymbols": ["AAA"], "inferenceGenerationId": "inference:fixture"}
        self.failure = failure
        self.events = []

    def pending_abox_activation(self, world_id=""):
        self.events.append(["journal", world_id])
        if self.failure == "journal":
            raise RuntimeError("journal unavailable")
        return copy.deepcopy(self.pending)

    def active_abox_metadata(self, world_id=""):
        self.events.append(["active", world_id])
        if self.failure == "active":
            raise RuntimeError("active unavailable")
        return copy.deepcopy(self.active)

    def inferencebox_recovery_metadata(self, world_id=""):
        self.events.append(["marker", world_id])
        if self.failure == "marker":
            raise RuntimeError("marker unavailable")
        return copy.deepcopy(self.marker)

    def inferencebox_matches_pending_abox_activation(self, inferencebox, candidate_snapshot_id, target_symbols=None):
        return self.implementation.inferencebox_matches_pending_abox_activation(self, inferencebox, candidate_snapshot_id, target_symbols)

    def activate_abox_generation(self, snapshot_id, world_id=""):
        self.events.append(["restore-control", snapshot_id, world_id])
        if self.failure == "control":
            return {"status": "error", "reason": "control unavailable"}
        self.active = {"status": "ok", "aboxSnapshotId": snapshot_id}
        self.pending = {"status": "empty"}
        return {"status": "ok"}

    def finalize_abox_generation(self, active_snapshot_id, previous_snapshot_id="", world_id=""):
        self.events.append(["finalize", active_snapshot_id, previous_snapshot_id, world_id])
        if self.failure == "finalize":
            return {"status": "error", "reason": "finalization unavailable"}
        self.pending = {"status": "empty"}
        return {"status": "ok"}

    def run(self, world=WORLD, cap=0):
        method = self.implementation.recover_pending_abox_activation
        method = getattr(method, "__wrapped__", method)
        try:
            result = method(self, world, cap)
        except Exception as error:
            result = {"raised": type(error).__name__, "reason": str(error)}
        return {"result": result, "events": self.events, "pending": self.pending, "active": self.active}


RECOVERY_SCENARIOS = [
    "matched", "no-match", "incomplete", "foreign-source", "partial-targets", "journal-error", "active-error",
    "marker-error", "finalize-error", "empty-journal", "invalid-journal", "missing-active", "initial-staged",
    "staged", "staged-oversized", "staged-oversized-control-error", "active-oversized", "active-oversized-control-error",
    "stale-journal", "stale-control-error", "initial-empty-target", "lost-target", "initial-unproven",
    "shared-staged", "shared-unproven", "shared-control-error", "shared-matched", "malformed-marker", "invalid-cap",
]


def recovery_scenario(api, scenario, world=WORLD):
    failure = {"journal-error": "journal", "active-error": "active", "marker-error": "marker", "finalize-error": "finalize"}.get(scenario, "")
    store = RecoveryStore(api.TypeDBOntologyGraphRepository, failure)
    cap = 0
    if "control-error" in scenario:
        store.failure = "control"
    if scenario == "no-match":
        store.marker["nativeInferenceOutcome"] = "no-match"
    if scenario in {"incomplete", "shared-unproven", "shared-control-error", "initial-unproven"}:
        store.marker["nativeTypeDbReasoningCompleted"] = False
    if scenario == "foreign-source":
        store.marker["sourceAboxSnapshotId"] = OLD
    if scenario == "partial-targets":
        store.marker["targetSymbols"] = ["BBB"]
    if scenario == "empty-journal":
        store.pending = {"status": "empty"}
    if scenario == "invalid-journal":
        store.pending = {"status": "error"}
    if scenario in {"missing-active", "initial-staged"}:
        store.active = {"status": "empty"}
    if scenario in {"initial-staged", "initial-empty-target", "initial-unproven"}:
        store.pending["previousAboxSnapshotId"] = ""
    if scenario in {"staged", "initial-staged", "staged-oversized", "staged-oversized-control-error", "shared-staged"}:
        store.pending["activationStatus"] = "staged-native-inference"
        if scenario != "initial-staged":
            store.active["aboxSnapshotId"] = OLD
    if "oversized" in scenario:
        store.pending["targetSymbols"] = ["AAA", "BBB"]
        cap = 1
    if scenario in {"stale-journal", "stale-control-error"}:
        store.active["aboxSnapshotId"] = "manifest:later"
    if scenario in {"initial-empty-target", "lost-target"}:
        store.pending["targetSymbols"] = []
    if scenario.startswith("shared-"):
        world = SHARED_WORLD
    if scenario == "malformed-marker":
        store.marker = []
    if scenario == "invalid-cap":
        cap = "invalid"
    return store.run(world, cap)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def contract_fingerprints(api):
    repo = api.TypeDBOntologyGraphRepository
    contracts = {"recovery/" + name: fingerprint(recovery_scenario(api, name)) for name in RECOVERY_SCENARIOS}
    contracts["recovery/empty-world"] = fingerprint(recovery_scenario(api, "matched", ""))
    image = image_fixture(api)
    active = {"scopeGenerationIds": {STATE: "state-old", EVIDENCE: "evidence-old", LINK: "link-old"},
              "scopeFingerprints": {STATE: "old", EVIDENCE: "same", LINK: "same"},
              "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION, "persistenceMode": CURRENT_STATE_ABOX_PERSISTENCE_MODE}
    graph = PortfolioOntology("synthetic-candidate", worldview={"scopePlan": image["physical_scope_plan"], "aboxSnapshotId": NEW,
                             "persistenceMode": CURRENT_STATE_ABOX_PERSISTENCE_MODE, "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION})
    graph.entities = [OntologyEntity("stock:AAA", "AAA", "stock", properties={"ontologyBox": "ABox", "aboxScopeId": STATE, "snapshotId": "logical-old"})]
    contracts["plan/normalized"] = fingerprint(repo.scoped_abox_plan(graph))
    for current in [True, False]:
        for migration in ["", "full"]:
            for mode in [CURRENT_STATE_ABOX_PERSISTENCE_MODE, LEGACY_CURRENT_STATE_ABOX_PERSISTENCE_MODE]:
                args = (image["physical_scope_plan"], active, current, migration, mode)
                key = f"selection/{current}/{migration}/{mode}"
                semantic = repo.scoped_abox_semantic_changed_scope_ids(*args)
                changed = repo.scoped_abox_changed_scope_ids(*args)
                physical = repo.current_state_physical_scope_plan(image["physical_scope_plan"], active, changed, WORLD, mode, "transition:fixture")
                contracts[key] = fingerprint({"semantic": semantic, "changed": changed, "physical": physical,
                    "graph": asdict(repo.current_state_physical_graph(graph, physical)),
                    "rebind": repo.scoped_abox_rebind_only_relation_scope_ids(physical, semantic, changed)})
    for root in [None, [], [STATE], [EVIDENCE]]:
        contracts["selection/roots/" + str(root)] = fingerprint(repo.scoped_abox_changed_scope_ids(
            image["physical_scope_plan"], active, True, relation_rebind_root_scope_ids=root))
    contracts["selection/active-reuse"] = fingerprint(repo.scoped_abox_active_reuse_scope_ids(
        image["physical_scope_plan"], active["scopeGenerationIds"], [STATE, LINK], [], [LINK]))
    for patch in [{}, {"replacementSymbols": ["AAA"]}, {"replacementSymbols": ["BBB"]},
                  {"replacementSymbols": ["AAA"], "retiredScopeIds": [EVIDENCE]}]:
        contracts["selection/index/" + fingerprint(patch)] = fingerprint(repo.scoped_abox_native_index_reuse_scope_ids(
            patch, active["scopeGenerationIds"], [STATE, LINK], image["physical_scope_plan"]))

    for variant in ["rebind", "missing-active-relation", "missing-active-endpoint", "missing-current-node", "wrong-count", "wrong-generation", "semantic", "semantic-missing-endpoint", "duplicate-relation"]:
        data = copy.deepcopy(image)
        if variant == "missing-active-relation":
            data["active_scope_rows"]["relationRows"] = []
        if variant == "missing-active-endpoint":
            data["active_scope_rows"]["relationRows"][0]["targetStorageId"] = ""
            data["active_scope_rows"]["nodeRows"] = []
        if variant == "missing-current-node":
            data["current_node_rows"] = data["current_node_rows"][1:]
        if variant == "wrong-count":
            data["physical_scope_plan"][0]["entityCount"] = 2
        if variant in {"semantic", "semantic-missing-endpoint", "wrong-generation", "duplicate-relation"}:
            data["semantic_changed_scope_ids"] = [STATE, LINK]
            row = copy.deepcopy(data["active_scope_rows"]["relationRows"][0])
            row.update(snapshotId="link-new", scopeGenerationId="link-new")
            if variant == "wrong-generation":
                row.update(snapshotId="link-wrong", scopeGenerationId="link-wrong")
            if variant == "semantic-missing-endpoint":
                data["active_scope_rows"]["nodeRows"] = []
                data["current_node_rows"][1]["snapshotId"] = "evidence-future"
                data["current_node_rows"][1]["scopeGenerationId"] = "evidence-future"
            data["current_relation_rows"] = [row, copy.deepcopy(row)] if variant == "duplicate-relation" else [row]
        contracts["rows/" + variant] = fingerprint(repo.scoped_abox_candidate_persistence_rows(**data))
    for owner_kind, row in [("node", image["current_node_rows"][0]), ("relation", image["active_scope_rows"]["relationRows"][0])]:
        for world in [WORLD, "portfolio:fixture:another", ""]:
            for generation in ["", "generation:one", "generation:two"]:
                payload = {**row, "worldId": world, "snapshotId": generation}
                contracts[f"identity/{owner_kind}/{world}/{generation}"] = fingerprint({
                    "identity": repo.scoped_abox_storage_identity(payload, owner_kind),
                    "content": api.ontology_row_content_fingerprint(payload, owner_kind),
                })
    return contracts
