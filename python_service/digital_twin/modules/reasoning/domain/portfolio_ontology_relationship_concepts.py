"""Source assertions become ABox research leads; resolved names alone get edges."""
from digital_twin.modules.news_intelligence.contracts import valid_company_relationship_assertions
from .ontology_schema import add_entity, add_relation
from .ontology_contracts import entity_id


def add_company_relationship_concepts(graph, stock_id, event_id, item):
    for row in valid_company_relationship_assertions(item):
        counterpart = row["counterparty"]
        props = {**row, "symbol": item.symbol, "tboxClass": "ExtractedClaim",
            "tboxClasses": ["ResearchEvidence", "ExtractedClaim"], "ontologyBox": "ABox",
            "boundedContext": "observation-data", "source": item.source, "sourceAsOf": row["publishedAt"],
            "judgementEvidenceUsable": True, "validationState": "conditional", "dataState": "partial",
            "freshnessStatus": "historical", "missingData": ["current-relationship-reconfirmation", "economic-exposure"]}
        claim_id = add_entity(graph, "company-relationship", row["assertionId"],
                              counterpart["name"] + " · 문서에 명시된 기업 관계", props)
        add_relation(graph, stock_id, claim_id, "HAS_OBSERVATION", evidence_ids=[item.evidence_id], properties={"source": "company-relationship-research"})
        add_relation(graph, claim_id, event_id, "HAS_PROVENANCE", evidence_ids=[item.evidence_id], properties={"sourceRevision": row["sourceRevision"]})
        if counterpart["status"] != "exact-listed-name":
            continue
        subject = entity_id("company", item.symbol)
        if not any(entity.entity_id == subject for entity in graph.entities):
            subject = add_entity(graph, "company", item.symbol, item.symbol, {"symbol": item.symbol, "tboxClass": "Company"})
        other = add_entity(graph, "company", counterpart["issuerId"], counterpart["name"], {
            "tboxClass": "Company", "listedSymbol": counterpart["symbol"], "issuerId": counterpart["issuerId"],
            "identitySource": counterpart["identitySource"], "identityStatus": counterpart["status"]})
        source, target = (other, subject) if row["direction"] == "inbound" else (subject, other)
        add_relation(graph, source, target, row["relationType"], evidence_ids=[item.evidence_id], properties={
            "source": "company-relationship-research", "assertionId": row["assertionId"],
            "sourceRevision": row["sourceRevision"], "sourceUrl": row["sourceUrl"], "publishedAt": row["publishedAt"],
            "firstKnownAt": row["firstKnownAt"], "reportingPeriod": row["reportingPeriod"],
            "assertionState": "source-stated", "currentness": "not-reconfirmed", "validationState": "conditional",
            "investmentActionAuthority": False})
