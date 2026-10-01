"""Bounded ABox reads; planner source indexes are not evidence inventories."""
import json

from digital_twin.modules.reasoning.domain.observation_evidence import EvidenceContractError, MACRO_KINDS
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string, typedb_value_match


class TypeDBObservationEvidenceSource:
    def __init__(self, repository):
        self.repository = repository

    def metadata(self, world_id):
        return self.repository.active_abox_metadata(world_id)

    def snapshot_id(self, world_id):
        return self.repository.active_abox_snapshot_id(world_id)

    def candidates(self, world_id, symbol):
        repository = self.repository
        scope = repository.active_abox_members_clause([("$n", "observationEvidence")], world_id)
        selector = "{ $n has ontology-symbol " + typedb_string(symbol) + "; } or { " + typedb_value_match(
            "$n", "ontology-kind", MACRO_KINDS, "==", "observationMacroKind") + " }; "
        raw = repository.read_rows("match " + scope + " $n isa ontology-node, has ontology-id $id, "
            "has ontology-kind $kind, has ontology-label $label, has ontology-json $json; " + selector + " limit 2001;",
            ["id", "kind", "label", "json"], label="observation-evidence.inventory")
        if len(raw) > 2000:
            raise EvidenceContractError("observation inventory exceeds bounded read contract")
        rows = {row["id"]: {**json.loads(row["json"]), **{key: row[key] for key in ("id", "kind", "label")}}
                for row in raw}
        # Include direct evidence endpoints without ontology-symbol, in both
        # relation directions. Never traverse through another stock.
        scope = repository.active_abox_members_clause(
            [("$s", "observationSubject"), ("$n", "observationLinked"), ("$r", "observationLink")], world_id)
        query = ("match " + scope + " $s isa ontology-node, has ontology-kind \"stock\", has ontology-symbol "
            + typedb_string(symbol) + "; $r isa ontology-assertion; "
            "{ $r links (source: $s, target: $n); } or { $r links (source: $n, target: $s); }; "
            "$n isa ontology-node, has ontology-id $id, has ontology-kind $kind, has ontology-label $label, has ontology-json $json;")
        linked = repository.read_rows(query + " limit 2001;", ["id", "kind", "label", "json"], label="observation-evidence.linked")
        if len(linked) > 2000:
            raise EvidenceContractError("linked observation inventory exceeds bounded read contract")
        for row in linked:
            value = {**json.loads(row["json"]), **{key: row[key] for key in ("id", "kind", "label")}}
            if value.get("symbol") and value["symbol"] != symbol and value["kind"] not in MACRO_KINDS:
                continue
            rows[row["id"]] = value
        return list(rows.values())
