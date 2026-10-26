"""Lossless references for routing; original evidence remains frozen for authors."""
from copy import deepcopy

from digital_twin.modules.reasoning.contracts import content_hash, EvidenceContractError
from . import retrieval_view as previous, memory_reference
from .retrieval import ReadRequestError, issue

VERSION = "retrieval-routing-view-v2"
DETAIL_BYTES = previous.DETAIL_BYTES
size = previous.size


def project(current, context, issued=None):
    packet, routing = previous.project(current, context, issued)

    def reference(value, path):
        if issued is not None:
            issued.add(path)
        return previous.reference(value, path)

    def fact_view(fact, path):
        value = deepcopy(fact)
        # Keep every measured value, unit, period, quality issue and contrary
        # premise inline. Only bulky audit/calculation details become references.
        for key in ("metricProvenance", "comparisonEvidence", "derivedMetricEvidence",
                    "sourceReferences", "evidenceView"):
            if key in value and size(value[key]) > 512:
                value[key] = reference(value[key], path + "/" + memory_reference.escape(key))
        return value

    packet["facts"] = [fact_view(row, "/current/facts/" + str(index))
                       for index, row in enumerate(current.get("facts", []))]
    continuity = routing.get("continuity", {})
    for category in ("previousAnalyses", "researchResults"):
        for index, row in enumerate(continuity.get(category, [])):
            original = context["continuity"][category][index]
            path = "/continuity/" + category + "/" + str(index)
            # Administrative/service memories are still discoverable, but their
            # complete prose is not required to choose a financial source.
            if category == "researchResults" and row.get("kind") in {
                    "service-feedback", "rulebox-feedback"}:
                continuity[category][index] = {
                    **{key: deepcopy(row[key]) for key in
                       ("kind", "memoryId", "caseId", "status", "authority") if key in row},
                    "detail": reference(original, path)}
            elif size(original.get("omittedFields", [])) > 384:
                row["omittedFields"] = reference(original["omittedFields"], path + "/omittedFields")
    # Newly read passages remain visible. Repeated provenance inside report
    # results can be reopened exactly, without fetching a newer source.
    routing["readResults"] = []
    seen = set()
    for step_index, step in enumerate(context.get("trace", [])):
        for read_index, read in enumerate(step.get("reads", [])):
            original = read["result"]
            digest = content_hash(original)
            if digest in seen:
                continue
            seen.add(digest)
            result = deepcopy(original)
            if "facts" in result:
                prefix = "/retrieval/trace/" + str(step_index) + "/reads/" + str(read_index) + "/result/facts/"
                result["facts"] = [fact_view(row, prefix + str(index))
                                   for index, row in enumerate(original["facts"])]
            routing["readResults"].append({"request": deepcopy(read["request"]), "result": result})
    # Exact allowed arguments are already in outputSchema.requests. Keep the
    # descriptive indexes, not a second copy of the same enum lists.
    for key in ("readableMemorySections", "readableFacts", "readableContextPaths", "availableCursors"):
        routing.pop(key, None)
    routing["issuedArgumentsLocation"] = "outputSchema.requests"
    routing["routingViewVersion"] = VERSION
    return packet, routing


def _read(current, context, path):
    source = {"current": current, "continuity": context.get("continuity", {}), "retrieval": context}
    value = memory_reference.pointer(source, path)
    receipt = {"path": path, "contentHash": content_hash(value), "bytes": size(value),
               "authority": "captured-context-only"}
    if size(value) > DETAIL_BYTES:
        parts = memory_reference.children(value, path)
        return {**receipt, "status": "section-index" if parts else "context-budget", "sections": parts}
    return {**receipt, "status": "ok", "content": deepcopy(value)}


def paths(current, context):
    # Earlier steps may only use paths available before that step. A later
    # result cannot retroactively authorize a forged read in an older receipt.
    allowed = set()
    project(current, {**context, "trace": []}, allowed)
    prefix = []
    for step in context.get("trace", []):
        step_allowed = set(allowed)
        for read in step.get("reads", []):
            request, result = read["request"], read["result"]
            if request.get("tool") != "read_context":
                continue
            path = request.get("path")
            if set(request) != {"tool", "path"} or path not in step_allowed:
                raise EvidenceContractError("context read was not issued by the frozen routing view")
            expected = _read(current, context, path)
            if result.get("status") in {"repeated-read", "context-budget"} and "contentHash" not in result:
                expected = ({"status": "repeated-read", "facts": []} if result["status"] == "repeated-read" else
                            {"status": "context-budget", "facts": [], "reason": "read-audit-budget",
                             "omittedResultHash": content_hash(expected)})
            if result != expected:
                raise EvidenceContractError("invalid bounded context read receipt")
            allowed.update(row["path"] for row in result.get("sections", []))
        prefix.append(step)
        project(current, {**context, "trace": prefix}, allowed)
    return sorted(allowed)


def read(current, context, request):
    if set(request) != {"tool", "path"} or request.get("path") not in paths(current, context):
        raise ReadRequestError([issue("read_context", "현재 동결 입력이 발급한 path만 허용")])
    return _read(current, context, request["path"])
