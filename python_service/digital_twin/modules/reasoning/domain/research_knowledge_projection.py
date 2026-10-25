"""Research-memory graph read model using the canonical TBox/ABox vocabulary.

These nodes assert the existence and lineage of research, not market truth.
They cannot enter RuleBox candidates or the current source evidence inventory.
"""
from .ontology_contracts import PortfolioOntology
from .ontology_schema import add_entity, add_relation


def research_knowledge_projection(context):
    graph = PortfolioOntology(context["worldId"])
    common = {"source": "research_knowledge", "accountId": context["accountId"], "symbol": context["symbol"],
              "worldId": context["worldId"], "asOf": context["asOf"], "authority": "research-only",
              "reasoningEligible": False, "investmentJudgmentEligible": False, "assertionRole": "research-audit"}
    def node(kind, key, label, cls, properties=None):
        scoped_key = context["accountId"] + ":" + context["worldId"] + ":" + context["symbol"] + ":" + key
        return add_entity(graph, kind, scoped_key, label, {**common, "tboxClass": cls, **(properties or {})})
    def edge(source, target, relation):
        add_relation(graph, source, target, relation, properties={"source": "research_knowledge", "authority": "research-only", "reasoningEligible": False})
    policy = node("research-service-policy", context["policy"]["id"], "근거 기반 투자 연구 목적", "ServiceResearchPolicy")
    company = node("company-research-model", context["accountId"] + ":" + context["worldId"] + ":" + context["symbol"],
                   context["symbol"] + " 기업 연구", "CompanyResearchModel",
                   {"sourceFactIds": context.get("companyModel", {}).get("sourceFactIds", [])})
    frameworks = {}
    for method in context["frameworks"]:
        framework = node("research-framework", method["id"] + ":" + str(method["version"]), method["label"], "ResearchFramework",
                         {"purpose": method["purpose"], "frameworkVersion": method["version"], "contentHash": method["contentHash"]})
        frameworks[(method["id"], method["version"])] = framework
        edge(policy, framework, "USES_RESEARCH_FRAMEWORK")
        for reference in method["references"]:
            source = node("knowledge-method-reference", reference["url"], reference["title"], "KnowledgeMethodReference",
                          {"url": reference["url"], "evidenceRole": "methodology-only"})
            edge(framework, source, "HAS_METHODOLOGY_REFERENCE")
    for row in context["claims"][:3]:
        claim = node("research-knowledge-claim", row["claimId"], row["question"], "ResearchKnowledgeClaim",
                     {"purpose": row["purpose"], "status": row["status"], "qualification": row["qualification"]})
        revision = node("research-knowledge-revision", row["revisionId"], "연구 지식 개정", "ResearchKnowledgeRevision",
                        {"revision": row["revision"], "knownAt": row["knownAt"], "executionInputId": row["executionInputId"]})
        edge(company, claim, "HAS_RESEARCH_KNOWLEDGE_CLAIM")
        method_key = (row["frameworkId"], row["frameworkVersion"])
        if method_key not in frameworks:
            frameworks[method_key] = node("research-framework", row["frameworkId"] + ":" + str(row["frameworkVersion"]),
                "판단 당시 연구 틀", "ResearchFramework", {"frameworkVersion": row["frameworkVersion"],
                    "contentHash": row.get("frameworkHash", ""), "referenceState": "historical-method-version"})
        edge(claim, frameworks[method_key], "USES_RESEARCH_FRAMEWORK")
        edge(claim, revision, "HAS_RESEARCH_KNOWLEDGE_REVISION")
        if row.get("previousRevisionId"):
            previous = node("research-knowledge-revision", row["previousRevisionId"], "이전 연구 개정", "ResearchKnowledgeRevision")
            edge(revision, previous, "SUPERSEDES")
        for evidence in row.get("evidence", []):
            ref = node("research-evidence-reference", row["claimId"] + ":" + evidence["factId"], "판단 당시 근거 참조", "Evidence",
                       {"factId": evidence["factId"], "sourceSnapshotId": evidence["sourceSnapshotId"], "contentHash": evidence["contentHash"],
                        "independenceKey": evidence.get("independenceKey", evidence["contentHash"]), "evidenceRole": "historical-reference"})
            for role in evidence.get("roles", [evidence["role"]]):
                edge(claim, ref, "CONTRADICTS" if role == "contradicts" else "SUPPORTED_BY")
        assessment = row.get("currentAssessment") or row.get("assessment") or {}
        if assessment.get("assessmentId"):
            quantitative = [{key: observation[key] for key in (
                "contractVersion", "checkpointId", "metric", "status", "expectedLow", "expectedHigh",
                "targetPeriodStart", "targetPeriodEnd", "expectationKind", "rangeDeviation", "observedAt")
                if key in observation} for observation in assessment.get("observations", [])
                if observation.get("contractVersion") == "quantitative-business-checkpoint-v1"]
            assessed = node("research-knowledge-assessment", assessment["assessmentId"], "후속 지표 관측", "ResearchKnowledgeAssessment",
                            {"status": assessment["status"], "qualification": assessment["qualification"],
                             **({"quantitativeObservations": quantitative} if quantitative else {})})
            edge(claim, assessed, "HAS_RESEARCH_ASSESSMENT")
            proposal = assessment.get("methodProposal") or {}
            if proposal:
                proposed = node("research-method-proposal", proposal["id"], "검증 대기 연구 방법 제안", "ResearchMethodProposal",
                                {"status": "proposed", "adopted": False})
                edge(assessed, proposed, "PROPOSES_RESEARCH_METHOD")
    for row in context.get("progress", [])[:2]:
        task = node("research-read-progress", row["progressId"], "자료 조회 진행 기록", "ResearchTask",
                    {"status": row["status"], "knownAt": row["knownAt"], "recordRole": "read-progress-only",
                     "qualification": "not-an-answer-or-thesis", "executionInputIds": row["executionInputIds"]})
        edge(company, task, "HAS_RESEARCH_PLAN")
    # Full rendering would duplicate the global TBox and all source facts.
    return {"version": "research-knowledge-projection-v1", "worldId": context["worldId"],
            "projectedClaimIds": [row["claimId"] for row in context["claims"][:3]],
            "omittedClaimIds": [row["claimId"] for row in context["claims"][3:]],
            "entities": [row.to_dict() for row in graph.entities], "relations": [row.to_dict() for row in graph.relations],
            "authority": "research-reference-only", "actionAuthority": "none"}
