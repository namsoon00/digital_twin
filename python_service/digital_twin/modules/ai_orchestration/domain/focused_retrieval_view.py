"""Read delivery prose and source-pointer details only when routing needs them."""
from copy import deepcopy
from digital_twin.modules.reasoning.contracts import content_hash, EvidenceContractError
from . import efficient_retrieval_view as previous, retrieval_view, memory_reference
from .retrieval import ReadRequestError, issue


def project(current, context, issued=None):
    packet, routing = previous.project(current, context, issued)
    def ref(value, path):
        if issued is not None:
            issued.add(path)
        return retrieval_view.reference(value, path)
    for index, row in enumerate(packet.get("deliveredNotificationContext", {}).get("entries", [])):
        original = current["deliveredNotificationContext"]["entries"][index]
        if retrieval_view.size(original.get("text", "")) > 512:
            row["text"] = ref(original["text"], "/current/deliveredNotificationContext/entries/" + str(index) + "/text")
    keep = {"facts", "summary", "hypothesis", "counterEvidence", "portfolioImpact", "comparison", "followUpConditions"}
    for key, value in current.get("lastDeliveredNotification", {}).items():
        if key not in keep and retrieval_view.size(value) > 512:
            packet["lastDeliveredNotification"][key] = ref(value, "/current/lastDeliveredNotification/" + memory_reference.escape(key))
    for index, row in enumerate(packet.get("reviewedQuestionContext", [])):
        for key in ("sources", "basis"):
            if retrieval_view.size(row.get(key, [])) > 768:
                row[key] = ref(current["reviewedQuestionContext"][index][key],
                    "/current/reviewedQuestionContext/" + str(index) + "/" + key)
    routing["routingViewVersion"] = "retrieval-routing-view-v3"
    return packet, routing


def paths(current, context):
    allowed = set()
    project(current, {**context, "trace": []}, allowed)
    prefix = []
    for step in context.get("trace", []):
        step_allowed = set(allowed)
        for item in step.get("reads", []):
            request, result = item["request"], item["result"]
            if request.get("tool") != "read_context":
                continue
            path = request.get("path")
            if set(request) != {"tool", "path"} or path not in step_allowed:
                raise EvidenceContractError("context read was not issued by focused routing")
            expected = previous._read(current, context, path)
            if result.get("status") in {"repeated-read", "context-budget"} and "contentHash" not in result:
                expected = ({"status": "repeated-read", "facts": []} if result["status"] == "repeated-read" else
                    {"status": "context-budget", "facts": [], "reason": "read-audit-budget", "omittedResultHash": content_hash(expected)})
            if result != expected:
                raise EvidenceContractError("focused context receipt changed")
            allowed.update(row["path"] for row in result.get("sections", []))
        prefix.append(step)
        project(current, {**context, "trace": prefix}, allowed)
    return sorted(allowed)


def read(current, context, request):
    if set(request) != {"tool", "path"} or request.get("path") not in paths(current, context):
        raise ReadRequestError([issue("read_context", "currently issued focused context path")])
    return previous._read(current, context, request["path"])
