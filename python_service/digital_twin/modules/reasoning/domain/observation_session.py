"""Read-only, source-fenced inventory for model-directed evidence retrieval."""
from copy import deepcopy

from .observation_evidence import (
    CATEGORIES, MACRO_KINDS, PROMPT_STORAGE_METADATA, EvidenceContractError, canonical_json,
    content_hash, source_clock, validate_evidence_packet, quote_clock_assessment, EVIDENCE_PROFILE, CRYPTO_EVIDENCE_PROFILE, OBSERVATION_QUOTE_KINDS,
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

    def question_document_index(self, sources, document_views=False):
        """Resolve source-owner IDs only against this frozen graph inventory."""
        rows = []
        for fact in self._facts.values():
            ref = fact.get("historicalDocumentReference") or fact.get("questionSourceReference") or {}
            if ref.get("evidenceId") not in sources:
                continue
            rows.append({"factId": fact["id"], "category": fact["evidenceCategory"],
                         "evidenceId": ref["evidenceId"], "caseIds": sources[ref["evidenceId"]],
                         "periodEnd": ref["periodEnd"], "publishedAt": ref["publishedAt"],
                         **({"sourceKind": ref["sourceKind"], "limitation": ref["limitation"],
                             "sourceRevision": ref["sourceRevision"]} if ref.get("sourceKind") else
                            {"documentHash": ref["documentHash"]}),
                         "bytes": len(canonical_json(fact).encode()),
                         **({"viewBytes": len(canonical_json(self.read_document(fact["id"])["facts"][0]).encode())}
                            if document_views else {})})
        return sorted(rows, key=lambda row: row["factId"])[:8]

    def question_work_inventory(self):
        """Source changes can resume questions; quote ticks and our prose cannot."""
        records = []
        for fact in self._facts.values():
            ref = fact.get("historicalDocumentReference") or fact.get("questionSourceReference")
            if ref:
                state = {name: {key: value for key, value in (fact.get(name) or {}).items()
                    if key not in {"checkedAt", "ageMinutes"}} for name in ("evidenceGovernance", "promptEvidenceAdmission")}
                reference = {key: value for key, value in ref.items() if key != "observedAt"}
                if reference.get("filingHistory"):
                    from digital_twin.modules.news_intelligence.contracts import filing_history_material
                    reference["filingHistory"] = filing_history_material(reference["filingHistory"])
                records.append({"id": fact["id"], "reference": reference, "state": state})
            elif fact.get("kind") == "company-financial-state":
                from .observation_evidence import material_fact
                records.append(material_fact(fact))
        return sorted(content_hash(row) for row in records)

    def business_baseline(self, question_documents=(), document_views=False, period_priority=False):
        """Reserve report history and documentary context before discretionary reads."""
        if period_priority:
            from .observation_business_baseline import baseline
            return baseline(self, list(self._facts.values()), question_documents, self._packet["capturedAt"])
        rows = sorted(self._facts.values(), key=lambda row: (-source_clock(row), row["id"]))
        groups = [[self._facts[row["factId"]] for row in question_documents]] + [
            [row for row in rows if row.get("historicalReport") and row.get("frequency") == frequency]
            for frequency in ("annual", "quarterly")]
        groups += [[row for row in rows if row.get("kind") == "company-relationship"],
                   [row for row in rows if row.get("documentVerified")],
                   [row for row in rows if row.get("kind") == "company" or row.get("evidenceCategory") == "valuation"]]
        selected, omitted, used = [], [], 0
        ordered = ([row for row in groups[0]] if document_views else []) + [group[index]
            for index in range(4) for group in (groups[1:] if document_views else groups) if index < len(group)]
        for row in ordered:
            if row["id"] in selected:
                continue
            view = (self.read_document(row["id"])["facts"][0] if document_views and
                    (row.get("historicalDocumentReference") or row.get("questionSourceReference")) else row)
            size = len(canonical_json(view).encode())
            if used + size > (32000 if document_views else 26000):
                omitted.append(row["id"])
            else:
                selected.append(row["id"])
                used += size
        return {"version": "business-evidence-reservation-v2" if document_views else "business-evidence-reservation-v1", "factIds": selected,
                "omittedByBudget": omitted, "bytes": used,
                **({"viewFactIds": [key for key in selected if self._facts[key].get("historicalDocumentReference")
                                   or self._facts[key].get("questionSourceReference")]} if document_views else {}),
                "policy": "reported-business-history-before-discretionary-reads"}

    def company_value_baseline(self, *, diagnostics=False):
        from .observation_value import value_baseline
        return value_baseline(list(self._facts.values()), diagnostics=diagnostics)

    def packet(self):
        return deepcopy(self._packet)

    def with_reasoning_binding(self, binding):
        from .observation_binding import VERSION
        if binding.get("version") != VERSION:
            raise EvidenceContractError("unknown observation graph binding")
        return ObservationEvidenceSession({**self._packet, "reasoningBinding": deepcopy(binding)}, self._facts.values())

    def catalog(self):
        return {"sourceSnapshots": deepcopy(self._packet["sourceSnapshots"]),
                "coverage": deepcopy(self._packet["coverage"]), "ordering": "source-time-descending-then-id"}

    def event_index(self):
        from .observation_event_index import build
        quote = next((row for row in self._packet["facts"] if row["kind"] in OBSERVATION_QUOTE_KINDS
                      and row.get("symbol") == self._packet["symbol"]), {})
        return build(self._facts.values(), self._packet["capturedAt"], quote.get("sourceAsOf") or quote.get("asOf", ""))

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

    def report_index(self, period_priority=False):
        from .observation_report_view import report_metrics
        from .observation_business_baseline import ordered_reports
        return [{"factId": row["id"], "periodEnd": row.get("periodEnd"), "frequency": row.get("frequency"),
                 "metrics": report_metrics(row), "bytes": len(canonical_json(row).encode())}
                for row in (ordered_reports(self._facts.values()) if period_priority else
                            sorted(self._facts.values(), key=lambda x: (-source_clock(x), x["id"])))
                if row.get("kind") == "company-financial-state"][:32]

    def read_report(self, fact_id, metrics):
        from .observation_report_view import report_view
        if fact_id not in self._facts:
            raise EvidenceContractError("unknown report evidence")
        return {"facts": [report_view(self._facts[fact_id], metrics)], "nextCursor": None,
                "sourceSnapshots": deepcopy(self._packet["sourceSnapshots"])}

    def read_document(self, fact_id):
        from .observation_document_view import document_view
        if fact_id not in self._facts:
            raise EvidenceContractError("unknown document evidence")
        value = document_view(self._facts[fact_id])
        value["evidenceCategory"] = self._facts[fact_id]["evidenceCategory"]
        return {"facts": [value], "nextCursor": None, "sourceSnapshots": deepcopy(self._packet["sourceSnapshots"])}

    def select(self, fact_ids, views=None, *, macro_baseline=True):
        """Keep quote and the v2 bounded macro baseline alongside requested facts."""
        selected = set(fact_ids) | {row["id"] for row in self._packet["facts"] if row["kind"] in OBSERVATION_QUOTE_KINDS
            or (macro_baseline and self._packet["profile"] in {EVIDENCE_PROFILE, CRYPTO_EVIDENCE_PROFILE} and row["evidenceCategory"] == "macro")}
        if selected - self._facts.keys():
            raise EvidenceContractError("unknown selected evidence")
        packet = self.packet()
        packet["facts"] = [deepcopy(row) for row in self._facts.values() if row["id"] in selected]
        if views:
            from .observation_report_view import report_view
            from .observation_document_view import document_view, VERSION as document_version
            for index, row in enumerate(packet["facts"]):
                view = views.get(row["id"])
                if view is not None and view != row:
                    from .observation_value import value_view, VERSION as value_version, DIAGNOSTIC_VERSION as diagnostic_version
                    if view.get("evidenceView", {}).get("version") in {value_version, diagnostic_version}:
                        exact = value_view(row, diagnostics=view["evidenceView"]["version"] == diagnostic_version)
                    elif view.get("evidenceView", {}).get("version") == document_version:
                        exact = {**document_view(row), "evidenceCategory": row["evidenceCategory"]}
                    else:
                        exact = report_view(row, view.get("evidenceView", {}).get("metrics"))
                    if exact != view:
                        raise EvidenceContractError("report view changed captured evidence")
                    packet["facts"][index] = deepcopy(exact)
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
