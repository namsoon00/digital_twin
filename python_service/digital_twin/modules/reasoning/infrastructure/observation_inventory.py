"""Read active identities first, then hydrate each physical fact once."""

import json

from digital_twin.modules.reasoning.application.observation_evidence.reads import read_evidence_stage
from digital_twin.modules.reasoning.domain.observation_evidence import EvidenceContractError, MACRO_KINDS
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string, typedb_value_match
from digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses import typedb_active_scoped_abox_member_clause


def scoped_candidates(repository, world_id, symbol, metadata):
    # Scope pointers select actual native rows. Planner/source indexes cannot
    # substitute for the complete evidence inventory.
    members = typedb_active_scoped_abox_member_clause("$n", "observationEvidence", world_id=world_id)
    selectors = (("subject", "$n has ontology-symbol " + typedb_string(symbol) + ";"),
                 ("macro", typedb_value_match("$n", "ontology-kind", MACRO_KINDS, "==", "observationMacroKind")))
    inventory, logical = {}, {}
    for stage, selector in selectors:
        rows = read_evidence_stage("inventory-" + stage, lambda: repository.read_rows(
            "match " + members + " $n isa ontology-node, has ontology-id $id, "
            "has ontology-storage-id $storageId, has ontology-kind $kind; " + selector + " limit 2001;",
            ["id", "storageId", "kind"], label="observation-evidence.inventory." + stage))
        if len(rows) > 2000:
            raise EvidenceContractError("observation inventory exceeds bounded read contract")
        for row in rows:
            storage_id, logical_id = row.get("storageId"), row.get("id")
            if not storage_id or not logical_id:
                raise EvidenceContractError("active evidence storage identity incomplete")
            if ((logical_id in logical and logical[logical_id] != storage_id)
                    or (storage_id in inventory and inventory[storage_id] != row)):
                raise EvidenceContractError("conflicting observation inventory")
            logical[logical_id] = storage_id
            inventory[storage_id] = row
        if len(inventory) > 2000:
            raise EvidenceContractError("observation inventory exceeds bounded read contract")

    stocks = sorted(key for key, row in inventory.items() if row["kind"] == "stock")
    linked_ids = set()
    if stocks:
        links = typedb_active_scoped_abox_member_clause("$r", "observationLink", world_id=world_id)
        anchor = typedb_value_match("$s", "ontology-storage-id", stocks, "==", "observationStockStorageId")
        for direction, ends in (("outgoing", "source: $s, target: $n"), ("incoming", "source: $n, target: $s")):
            rows = read_evidence_stage("linked-" + direction, lambda: repository.read_rows(
                "match " + links + ' $s isa ontology-node, has ontology-kind "stock"; ' + anchor
                + " $r isa ontology-assertion, links (" + ends + "); "
                "$n isa ontology-node, has ontology-storage-id $storageId; limit 2001;",
                ["storageId"], label="observation-evidence.linked." + direction))
            linked_ids.update(row["storageId"] for row in rows)
            if len(rows) > 2000 or len(linked_ids) > 2000:
                raise EvidenceContractError("linked observation inventory exceeds bounded read contract")

    generations = metadata["scopeGenerationIds"]
    identities = sorted(set(inventory) | linked_ids)
    facts, hydrated = {}, set()
    for offset in range(0, len(identities), 64):
        chunk = identities[offset:offset + 64]
        rows = read_evidence_stage("facts", lambda: repository.read_rows(
            'match $n isa ontology-node, has ontology-box "ABox", has ontology-world-id '
            + typedb_string(world_id) + ", has ontology-storage-id $storageId, has ontology-id $id, "
            "has ontology-kind $kind, has ontology-label $label, has ontology-json $json, "
            "has ontology-scope-id $scopeId, has ontology-snapshot-id $generationId; "
            + typedb_value_match("$n", "ontology-storage-id", chunk, "==", "observationFactStorageId")
            + " limit " + str(len(chunk) + 1) + ";",
            ["storageId", "id", "kind", "label", "json", "scopeId", "generationId"],
            label="observation-evidence.facts"))
        for row in rows:
            identity = row["storageId"]
            if identity not in chunk or identity in hydrated:
                raise EvidenceContractError("conflicting observation physical identity")
            hydrated.add(identity)
            if generations.get(row["scopeId"]) != row["generationId"]:
                if identity in inventory:
                    raise EvidenceContractError("active evidence generation mismatch")
                continue
            expected = inventory.get(identity)
            if expected and (expected["id"] != row["id"] or expected["kind"] != row["kind"]):
                raise EvidenceContractError("active evidence identity mismatch")
            value = {**json.loads(row["json"]), **{key: row[key] for key in ("id", "kind", "label")}}
            if value.get("symbol") and value["symbol"] != symbol and value["kind"] not in MACRO_KINDS:
                continue
            if row["id"] in facts and facts[row["id"]] != value:
                raise EvidenceContractError("conflicting observation inventory")
            facts[row["id"]] = value
    if set(inventory) - hydrated:
        raise EvidenceContractError("active evidence physical facts incomplete")
    return list(facts.values())
