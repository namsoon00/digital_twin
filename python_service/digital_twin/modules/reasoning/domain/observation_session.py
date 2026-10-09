"""Read-only, source-fenced inventory for model-directed evidence retrieval."""
from copy import deepcopy

from .observation_evidence import (
    CATEGORIES, MACRO_KINDS, PROMPT_STORAGE_METADATA, EvidenceContractError, canonical_json,
    content_hash, source_clock, validate_evidence_packet, quote_clock_assessment, EVIDENCE_PROFILE,
)


class ObservationEvidenceSession:
    """No database transaction survives capture; all reads use the same inventory."""

    def __init__(self, packet, candidates):
        validate_evidence_packet(packet)
        self._packet = deepcopy(packet)
        categories = {kind: category.name for category in CATEGORIES for kind in category.kinds}
        self._facts = {}
        for row in candidates:
            if (packet["sourceSnapshots"].get(row.get("sourceWorldId")) != row.get("sourceSnapshotId")
                    or (row.get("accountId") and row["accountId"] != packet["accountId"])
                    or (row.get("symbol") and row["symbol"] != packet["symbol"] and row.get("kind") not in MACRO_KINDS)):
                raise EvidenceContractError("retrieval inventory scope mismatch")
            category = categories.get(row["kind"])
            if category:
                compact = {**deepcopy({key: value for key, value in row.items()
                    if key not in PROMPT_STORAGE_METADATA}), "evidenceCategory": category}
                if row["id"] in self._facts and self._facts[row["id"]] != compact:
                    raise EvidenceContractError("conflicting retrieval evidence identity")
                self._facts[row["id"]] = compact

    def business_baseline(self):
        """Reserve report history and documentary context before discretionary reads."""
        rows = sorted(self._facts.values(), key=lambda row: (-source_clock(row), row["id"]))
        groups = [
            [row for row in rows if row.get("historicalReport") and row.get("frequency") == frequency]
            for frequency in ("annual", "quarterly")]
        groups += [[row for row in rows if row.get("kind") == "company-relationship"],
                   [row for row in rows if row.get("documentVerified")],
                   [row for row in rows if row.get("kind") == "company" or row.get("evidenceCategory") == "valuation"]]
        selected, omitted, used = [], [], 0
        for index in range(4):
            for group in groups:
                if index >= len(group):
                    continue
                row = group[index]
                if row["id"] in selected:
                    continue
                size = len(canonical_json(row).encode())
                if used + size > 26000:
                    omitted.append(row["id"])
                else:
                    selected.append(row["id"])
                    used += size
        return {"version": "business-evidence-reservation-v1", "factIds": selected,
                "omittedByBudget": omitted, "bytes": used,
                "policy": "reported-business-history-before-discretionary-reads"}

    def packet(self):
        return deepcopy(self._packet)

    def catalog(self):
        return {"sourceSnapshots": deepcopy(self._packet["sourceSnapshots"]),
                "coverage": deepcopy(self._packet["coverage"]), "ordering": "source-time-descending-then-id"}

    def read(self, category, kind="", offset=0, limit=4, fact_id=""):
        if category not in {row.name for row in CATEGORIES}:
            raise EvidenceContractError("unknown evidence category")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 8:
            raise EvidenceContractError("invalid evidence page")
        if fact_id and fact_id not in self._facts:
            raise EvidenceContractError("unknown evidence identity")
        rows = sorted((row for row in self._facts.values()
                       if row["evidenceCategory"] == category and (not kind or row["kind"] == kind)
                       and (not fact_id or row["id"] == fact_id)), key=lambda row: (-source_clock(row), row["id"]))
        result, omitted, used = [], [], 0
        page = rows[offset:offset + limit]
        for row in page:
            size = len(canonical_json(row).encode())
            if used + size > 32 * 1024:
                omitted.append({"id": row["id"], "hash": content_hash(row), "bytes": size, "reason": "tool-byte-budget"})
            else:
                result.append(deepcopy(row))
                used += size
        end = offset + len(page)
        return {"facts": result, "available": len(rows), "nextOffset": end if end < len(rows) else None,
                "omitted": omitted, "sourceSnapshots": deepcopy(self._packet["sourceSnapshots"])}

    def select(self, fact_ids):
        """Keep quote and the v2 bounded macro baseline alongside requested facts."""
        selected = set(fact_ids) | {row["id"] for row in self._packet["facts"] if row["kind"] == "stock"
            or (self._packet["profile"] == EVIDENCE_PROFILE and row["evidenceCategory"] == "macro")}
        if selected - self._facts.keys():
            raise EvidenceContractError("unknown selected evidence")
        packet = self.packet()
        packet["facts"] = [deepcopy(row) for row in self._facts.values() if row["id"] in selected]
        for category, coverage in packet["coverage"].items():
            if category == "unclassified":
                continue
            facts = [row for row in packet["facts"] if row["evidenceCategory"] == category]
            coverage.update(included=len(facts), excluded=coverage["available"] - len(facts))
            coverage["status"] = "missing" if not coverage["available"] else "partial" if coverage["excluded"] else "complete"
            coverage["exclusionReasons"] = {"not-retrieved": coverage["excluded"]} if coverage["excluded"] else {}
            for kind, counts in coverage["kinds"].items():
                counts["included"] = sum(row["kind"] == kind for row in facts)
        packet["includedFactCount"] = len(packet["facts"])
        packet["quoteAssessment"] = quote_clock_assessment(packet["facts"], packet["capturedAt"])
        validate_evidence_packet(packet)
        return packet
