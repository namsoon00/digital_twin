"""Exact source excerpts without duplicated analysis or provider metadata."""
from copy import deepcopy
from .observation_evidence import EvidenceContractError, content_hash

VERSION = "exact-question-document-v1"
FIELDS = frozenset({
    "id", "kind", "label", "symbol", "accountId", "worldId", "sourceWorldId", "sourceSnapshotId", "sourceEntityId", "evidenceCategory",
    "ontologyBox", "tboxClass", "tboxClasses", "boundedContext", "source", "title", "url", "publishedAt", "observedAt",
    "documentHash", "documentVerified", "analysisReady", "evidenceLifecycleState", "evidenceGovernance",
    "promptEvidenceAdmission", "judgementEvidenceUsable", "investmentJudgmentEligible", "historicalDocumentReference",
    "questionSourceReference", "periodEnd", "frequency", "reportedValues", "metricUnits", "metricProvenance",
    "durationBases", "sourceReferences", "reportObservationId", "historicalReport", "sourceRevision",
    "publicationTimeKnown", "freshnessBasis", "freshnessObservedAt",
})


def document_view(fact):
    ref = fact.get("historicalDocumentReference") or {}
    structured = fact.get("questionSourceReference") or {}
    is_report = (structured.get("version") == "question-source-reference-v1" and structured.get("sourceKind") == "structured-report"
        and structured.get("reportObservationId") == fact.get("reportObservationId") and structured.get("periodEnd") == fact.get("periodEnd"))
    if fact.get("kind") != "research-evidence" or (not is_report and (ref.get("version") != "research-document-reference-v1"
            or ref.get("symbol") != fact.get("symbol") or ref.get("sourceUrl") != fact.get("url")
            or not ref.get("text") or ref.get("purpose") not in {"historical-period-comparison", "dated-issuer-document"})):
        raise EvidenceContractError("invalid question document selection")
    result = {key: deepcopy(value) for key, value in fact.items() if key in FIELDS}
    result["evidenceView"] = {"version": VERSION, "sourceFactHash": content_hash(fact),
        "authority": "exact-field-projection", "omittedFields": sorted(set(fact) - FIELDS),
        "omissionMeaning": "not-selected-not-missing"}
    return result
