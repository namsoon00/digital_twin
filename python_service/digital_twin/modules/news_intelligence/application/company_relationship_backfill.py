"""Recheck bounded cached official documents using the existing evidence writer."""
from ..domain.company_relationships import valid_assertions


class CompanyRelationshipBackfill:
    def __init__(self, source, enrich, persist, clock):
        self.source, self.enrich, self.persist, self.clock = source, enrich, persist, clock

    def __call__(self, subject):
        documents = list(self.source(subject["symbol"]))
        enriched = self.enrich(documents, self.clock())
        changed = []
        assertions, checked = [], 0
        for old, item in zip(documents, enriched):
            before = (old.raw_payload or {}).get("companyRelationships") or {}
            after = (item.raw_payload or {}).get("companyRelationships") or {}
            if not after:
                continue
            # Assertion IDs exclude polling time; keep the original proven
            # excerpt and first-known clock when the source has not changed.
            prior = {row["assertionId"]: row for row in valid_assertions(old)}
            after["assertions"] = [prior.get(row["assertionId"], row) for row in after.get("assertions", [])]
            if after != before:
                changed.append(item)
            if after.get("status") in {"extracted", "no-supported-statement"}:
                checked += 1
            assertions.extend(valid_assertions(item))
        if changed:
            self.persist(subject, changed)
        resolved = [row for row in assertions if row.get("identityStatus") == "exact-listed-name"]
        unresolved = sorted({row["counterparty"]["name"] for row in assertions if row.get("identityStatus") != "exact-listed-name"})
        state = "complete" if checked and checked == len(documents) and not unresolved else "partial" if documents else "waiting"
        detail = "공식 문서 " + str(checked) + "건 확인 · 연결 " + str(len(resolved)) + "건 · 기업 식별 대기 " + str(len(unresolved)) + "건"
        if checked and not assertions:
            detail += " · 지원하는 명시적 관계 문장 미확인(관계 부재를 뜻하지 않음)"
        return {"status": state, "detail": detail, "documentCount": checked, "selectedDocumentCount": len(documents),
                "resolvedCount": len(resolved), "unresolvedCount": len(unresolved), "unresolvedNames": unresolved[:8],
                "sourceEvidenceIds": [item.evidence_id for item in documents], "scope": "latest-four-official-documents-explicit-one-hop"}
