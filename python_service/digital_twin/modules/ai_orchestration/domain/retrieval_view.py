"""Small routing views with exact reads from the same immutable input."""
from copy import deepcopy
import json

from digital_twin.modules.reasoning.contracts import content_hash, EvidenceContractError
from . import memory_reference
from .retrieval import ReadRequestError, issue

VERSION = "retrieval-routing-view-v1"
DETAIL_BYTES = 24 * 1024


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())


def reference(value, path):
    return {"readContextPath": path, "bytes": size(value), "contentHash": content_hash(value),
            "detailNotLoaded": True}


def project(current, context, issued=None):
    """Keep source facts, counter-evidence and original question contracts whole."""
    def omitted(value, path):
        if issued is not None:
            issued.add(path)
        return reference(value, path)

    packet, routing = deepcopy(current), deepcopy(context)
    baseline = packet.get("lastDeliveredNotification")
    if baseline and size(baseline.get("facts", [])) > 512:
        baseline["facts"] = omitted(baseline["facts"], "/current/lastDeliveredNotification/facts")
    knowledge = packet.get("researchKnowledge")
    if knowledge and size(knowledge) > 2048:
        packet["researchKnowledge"] = {
            **{key: deepcopy(knowledge[key]) for key in ("version", "status", "authority") if key in knowledge},
            "sections": [{"field": key, **omitted(value, "/current/researchKnowledge/" + memory_reference.escape(key))}
                         for key, value in knowledge.items() if key not in {"version", "status", "authority"}]}
    # The exact permitted paths already occur in readableMemorySections and the
    # output schema. Do not send the same long list a third time in memoryIndex.
    routing["memoryIndex"] = [{key: deepcopy(value) for key, value in row.items() if key != "sections"}
                              for row in context.get("memoryIndex", [])]
    continuity = routing.get("continuity", {})
    for index, row in enumerate(continuity.get("researchResults", [])):
        # Original question/checkpoint requirements and all counter-premises are
        # routing constraints, never replaced by a lossy text excerpt.
        protected = {"kind", "caseId", "question", "status", "revision", "reviewDue", "nextCheckAt",
                     "completionCriterion", "questionContract", "contract", "missingEvidence",
                     "mechanism", "assumption", "alternative", "invalidation", "counterEvidence",
                     "summary", "hypothesis", "memoryId", "authority", "answerResolution",
                     "questionAssessment", "evidenceProgress", "hypothesisResolution", "reviewedAssessment",
                     "observations", "originalQuestions", "currentQuestions"}
        for key, value in list(row.items()):
            if key not in protected and size(value) > 512:
                row[key] = omitted(value, "/continuity/researchResults/" + str(index) + "/" + memory_reference.escape(key))
    # Keep tool responses once, in a working set; the trace carries decisions
    # and receipts instead of retransmitting the same documents each round.
    routing["readResults"] = []
    routing["trace"] = []
    seen = set()
    for step in context.get("trace", []):
        brief = {key: deepcopy(value) for key, value in step.items() if key not in {"response", "reads"}}
        brief["reads"] = []
        for read in step.get("reads", []):
            result = read["result"]
            digest = content_hash(result)
            brief["reads"].append({"request": deepcopy(read["request"]), "resultHash": digest,
                                   "status": result.get("status", "ok")})
            if digest not in seen:
                seen.add(digest)
                routing["readResults"].append({"request": deepcopy(read["request"]), "result": deepcopy(result)})
        routing["trace"].append(brief)
    routing["routingViewVersion"] = VERSION
    return packet, routing


def initial_paths(current, context):
    # Only paths issued by this projection count. Source documents may contain
    # arbitrary JSON, including keys resembling our reference objects.
    issued = set()
    project(current, {**context, "trace": []}, issued)
    return issued


def _read(current, context, path):
    source = {"current": current, "continuity": context.get("continuity", {})}
    value = memory_reference.pointer(source, path)
    receipt = {"path": path, "contentHash": content_hash(value), "bytes": size(value),
               "authority": "captured-context-only"}
    if size(value) > DETAIL_BYTES:
        parts = memory_reference.children(value, path)
        return {**receipt, "status": "section-index" if parts else "context-budget", "sections": parts}
    return {**receipt, "status": "ok", "content": deepcopy(value)}


def paths(current, context):
    allowed = initial_paths(current, context)
    for step in context.get("trace", []):
        for read in step.get("reads", []):
            request, result = read["request"], read["result"]
            if request.get("tool") != "read_context":
                continue
            path = request.get("path")
            if set(request) != {"tool", "path"} or path not in allowed:
                raise EvidenceContractError("context read was not issued by the frozen routing view")
            if result.get("status") in {"repeated-read", "context-budget"} and "contentHash" not in result:
                expected = ({"status": "repeated-read", "facts": []} if result["status"] == "repeated-read" else
                            {"status": "context-budget", "facts": [], "reason": "read-audit-budget",
                             "omittedResultHash": content_hash(_read(current, context, path))})
                if result != expected:
                    raise EvidenceContractError("invalid bounded context read receipt")
                continue  # Audited runtime limit; it grants no new paths.
            if result != _read(current, context, path):
                raise EvidenceContractError("context read does not match frozen source")
            allowed.update(row["path"] for row in result.get("sections", []))
    return sorted(allowed)


def read(current, context, request):
    if set(request) != {"tool", "path"} or request.get("path") not in paths(current, context):
        raise ReadRequestError([issue("read_context", "현재 동결 입력이 발급한 path만 허용")])
    return _read(current, context, request["path"])
