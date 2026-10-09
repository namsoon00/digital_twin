"""Enrich source documents with bounded, auditable corporate research leads."""
from copy import deepcopy
from ..domain.company_relationships import extract_relationships, resolve_listed_company


class CompanyRelationshipResearch:
    def __init__(self, symbols):
        self.symbols = symbols

    def __call__(self, items, known_at):
        cache = {}
        def resolve(name):
            if name not in cache:
                rows = self.symbols.search(name, limit=20)
                cache[name] = resolve_listed_company(name, [row.to_dict() for row in rows])
            return cache[name]
        results = []
        for item in items:
            if not (item.raw_payload or {}).get("officialDocumentText"):
                results.append(item)
                continue
            updated = deepcopy(item)
            updated.raw_payload["companyRelationships"] = extract_relationships(updated, resolve, known_at)
            results.append(updated)
        return results
