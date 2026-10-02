"""Bounded ABox reads; planner source indexes are not evidence inventories."""
import json
from contextlib import nullcontext, contextmanager
from contextvars import ContextVar

from digital_twin.modules.reasoning.domain.observation_evidence import EvidenceContractError, MACRO_KINDS
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
from digital_twin.modules.reasoning.application.observation_evidence.reads import read_evidence_stage
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string, typedb_value_match
from .observation_inventory import scoped_candidates


_CAPTURE_METADATA = ContextVar("observation_capture_metadata", default=())


class TypeDBObservationEvidenceSource:
    def __init__(self, repository):
        self.repository = repository

    @contextmanager
    def capture(self):
        # Explicit capability lookup avoids treating optional mock/legacy
        # attributes as a native consistency guarantee.
        scope = getattr(type(self.repository), "read_snapshot_scope", None)
        with scope(self.repository) if callable(scope) else nullcontext():
            # Cache only inside a real native snapshot, never between captures
            # or across repositories/threads. Exceptions clear the cache too.
            token = _CAPTURE_METADATA.set((*_CAPTURE_METADATA.get(), (self, {}))) if callable(scope) else None
            try:
                yield
            finally:
                if token is not None:
                    _CAPTURE_METADATA.reset(token)

    def metadata(self, world_id):
        cache = next((cache for source, cache in reversed(_CAPTURE_METADATA.get()) if source is self), None)
        if cache is None:
            return self.repository.active_abox_metadata(world_id)
        if world_id not in cache:
            cache[world_id] = self.repository.active_abox_metadata(world_id)
        return cache[world_id]

    def snapshot_id(self, world_id):
        return self.repository.active_abox_snapshot_id(world_id)

    def candidates(self, world_id, symbol):
        metadata = read_evidence_stage("inventory-metadata", lambda: self.metadata(world_id))
        if (isinstance(metadata, dict) and metadata.get("status") == "ok"
                and metadata.get("scopedAboxManifestVersion") == SCOPED_ABOX_MANIFEST_VERSION
                and metadata.get("scopeGenerationIds")):
            return scoped_candidates(self.repository, world_id, symbol, metadata)
        return self.legacy_candidates(world_id, symbol)

    def legacy_candidates(self, world_id, symbol):
        repository = self.repository
        scope = read_evidence_stage("inventory-membership", lambda: repository.active_abox_members_clause([("$n", "observationEvidence")], world_id))
        # Mixing the symbol and macro-kind predicates in one disjunction makes
        # the native active-scope join expensive. Keep the same inventory as
        # two bounded reads; the reader fences the snapshot before and after.
        selectors = (("subject", "$n has ontology-symbol " + typedb_string(symbol) + ";"),
                     ("macro", typedb_value_match("$n", "ontology-kind", MACRO_KINDS, "==", "observationMacroKind")))
        rows = {}
        for stage, selector in selectors:
            raw = read_evidence_stage("inventory-" + stage, lambda: repository.read_rows(
                "match " + scope + " $n isa ontology-node, has ontology-id $id, "
                "has ontology-kind $kind, has ontology-label $label, has ontology-json $json; " + selector + " limit 2001;",
                ["id", "kind", "label", "json"], label="observation-evidence.inventory." + stage))
            if len(raw) > 2000:
                raise EvidenceContractError("observation inventory exceeds bounded read contract")
            for row in raw:
                value = {**json.loads(row["json"]), **{key: row[key] for key in ("id", "kind", "label")}}
                if row["id"] in rows and rows[row["id"]] != value:
                    raise EvidenceContractError("conflicting observation inventory")
                rows[row["id"]] = value
            if len(rows) > 2000:
                raise EvidenceContractError("observation inventory exceeds bounded read contract")
        # Include direct evidence endpoints without ontology-symbol, in both
        # relation directions. Never traverse through another stock.
        stock_ids = [row["id"] for row in rows.values() if row.get("kind") == "stock"]
        if not stock_ids:
            return list(rows.values())
        stock_scope = read_evidence_stage("stock-membership", lambda: repository.active_abox_members_clause(
            [("$s", "observationStock")], world_id))
        stock_rows = read_evidence_stage("stock-storage", lambda: repository.read_rows(
            "match " + stock_scope + " $s isa ontology-node, has ontology-id $id, has ontology-storage-id $storageId; "
            + typedb_value_match("$s", "ontology-id", stock_ids, "==", "observationStockId") + " limit 2001;",
            ["id", "storageId"], label="observation-evidence.stock-storage"))
        if (len(stock_rows) > 2000 or {row["id"] for row in stock_rows} != set(stock_ids)
                or any(not row.get("storageId") for row in stock_rows)):
            raise EvidenceContractError("active stock storage identity incomplete")
        scope = read_evidence_stage("linked-membership", lambda: repository.active_abox_members_clause(
            [("$r", "observationLink")], world_id))
        # Resolve exact physical endpoints through active edges, then validate
        # endpoint membership separately in the same native snapshot. Joining
        # three independent scope pointers in one query multiplies its cost.
        anchor = typedb_value_match("$s", "ontology-storage-id", [row["storageId"] for row in stock_rows], "==", "observationStockStorageId")
        linked_ids = set()
        for direction, ends in (("outgoing", "source: $s, target: $n"), ("incoming", "source: $n, target: $s")):
            query = ("match " + scope + " $s isa ontology-node, has ontology-kind \"stock\"; " + anchor
                + " $r isa ontology-assertion, links (" + ends + "); "
                "$n isa ontology-node, has ontology-storage-id $storageId;")
            linked = read_evidence_stage("linked-" + direction, lambda: repository.read_rows(
                query + " limit 2001;", ["storageId"], label="observation-evidence.linked." + direction))
            linked_ids.update(row["storageId"] for row in linked)
            if len(linked) > 2000 or len(linked_ids) > 2000:
                raise EvidenceContractError("linked observation inventory exceeds bounded read contract")
        if not linked_ids:
            return list(rows.values())
        active = read_evidence_stage("endpoint-generation", lambda: repository.active_abox_metadata(world_id))
        generations = (active.get("scopeGenerationIds") or {}) if isinstance(active, dict) else {}
        scoped = bool(generations) and active.get("scopedAboxManifestVersion") == SCOPED_ABOX_MANIFEST_VERSION
        if scoped:
            # The immutable manifest supplies physical generations. Validate
            # the exact (world, scope, generation) after indexed ID reads;
            # rejoining scope pointers for each endpoint is needlessly costly.
            scope = ('$n has ontology-box "ABox", has ontology-world-id ' + typedb_string(world_id)
                     + ', has ontology-scope-id $scopeId, has ontology-snapshot-id $generationId;')
        else:
            scope = read_evidence_stage("endpoint-membership", lambda: repository.active_abox_members_clause(
                [("$n", "observationLinked")], world_id))
        ordered_ids = sorted(linked_ids)
        for offset in range(0, len(ordered_ids), 64):
            query = ("match " + scope
                + " $n isa ontology-node, has ontology-id $id, has ontology-kind $kind, has ontology-label $label, has ontology-json $json; "
                + typedb_value_match("$n", "ontology-storage-id", ordered_ids[offset:offset + 64], "==", "observationLinkedStorageId"))
            linked = read_evidence_stage("linked-facts", lambda: repository.read_rows(
                query + " limit 2001;", ["id", "kind", "label", "json"] + (["scopeId", "generationId"] if scoped else []),
                label="observation-evidence.linked.facts"))
            if len(linked) > 2000:
                raise EvidenceContractError("linked observation inventory exceeds bounded read contract")
            for row in linked:
                if scoped and generations.get(row["scopeId"]) != row["generationId"]:
                    continue
                value = {**json.loads(row["json"]), **{key: row[key] for key in ("id", "kind", "label")}}
                if value.get("symbol") and value["symbol"] != symbol and value["kind"] not in MACRO_KINDS:
                    continue
                if row["id"] in rows and rows[row["id"]] != value:
                    raise EvidenceContractError("conflicting observation inventory")
                rows[row["id"]] = value
        return list(rows.values())
