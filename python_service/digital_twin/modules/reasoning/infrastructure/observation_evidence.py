"""Bounded ABox reads; planner source indexes are not evidence inventories."""
import json

from digital_twin.modules.reasoning.domain.observation_evidence import EvidenceContractError, MACRO_KINDS
from digital_twin.modules.reasoning.application.observation_evidence.reads import read_evidence_stage
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
        scope = read_evidence_stage("linked-membership", lambda: repository.active_abox_members_clause(
            [("$s", "observationSubject"), ("$n", "observationLinked"), ("$r", "observationLink")], world_id))
        query = ("match " + scope + " $s isa ontology-node, has ontology-kind \"stock\", has ontology-symbol "
            + typedb_string(symbol) + "; $r isa ontology-assertion; "
            "{ $r links (source: $s, target: $n); } or { $r links (source: $n, target: $s); }; "
            "$n isa ontology-node, has ontology-id $id, has ontology-kind $kind, has ontology-label $label, has ontology-json $json;")
        linked = read_evidence_stage("linked", lambda: repository.read_rows(query + " limit 2001;", ["id", "kind", "label", "json"], label="observation-evidence.linked"))
        if len(linked) > 2000:
            raise EvidenceContractError("linked observation inventory exceeds bounded read contract")
        for row in linked:
            value = {**json.loads(row["json"]), **{key: row[key] for key in ("id", "kind", "label")}}
            if value.get("symbol") and value["symbol"] != symbol and value["kind"] not in MACRO_KINDS:
                continue
            rows[row["id"]] = value
        return list(rows.values())
