"""Subject-scoped read functions; cursors and page sizes belong to the runtime."""
from copy import deepcopy
import uuid

from digital_twin.modules.reasoning.contracts import content_hash
from digital_twin.modules.ai_orchestration.domain.retrieval import PAGE_SIZE, ReadRequestError, issue


class ObservationReadTools:
    def __init__(self, session, history, research):
        self.session = session
        self.memories = {"analyses": deepcopy(history), "memories": deepcopy(research)}
        self.cursors = {}
        self.nonce = uuid.uuid4().hex
        self.known = {row["id"]: row["evidenceCategory"] for row in session.select([])["facts"]}

    def context(self):
        return {"pageSize": PAGE_SIZE, "pageSizeIsMaximum": True,
                "paginationVersion": "packet-budget-pages-v1",
                "availableCursors": [{"cursor": token, **row["scope"]} for token, row in self.cursors.items()],
                "knownFacts": [{"factId": key, "category": value} for key, value in self.known.items()]}

    def resolve(self, request, index=0):
        scope = {key: value for key, value in request.items() if key != "cursor"}
        path = "requests[" + str(index) + "]"
        cursor = request.get("cursor", "")
        if cursor and (cursor not in self.cursors or self.cursors[cursor]["scope"] != scope):
            raise ReadRequestError([issue(path + ".cursor", "현재 세션의 같은 도구·분류·kind 결과가 발급한 nextCursor 또는 빈 문자열")])
        if request["tool"] == "read_fact" and self.known.get(request["factId"]) != request["category"]:
            raise ReadRequestError([issue(path + ".factId", "knownFacts에 있는 같은 category의 factId")])
        if request["tool"] == "query_facts" and request["kind"]:
            kinds = self.session.catalog()["coverage"][request["category"]]["kinds"]
            if request["kind"] not in kinds:
                raise ReadRequestError([issue(path + ".kind", ["", *kinds])])
        return {"scope": scope, "offset": self.cursors[cursor]["offset"] if cursor else 0}

    def preview(self, plan, page_size=PAGE_SIZE):
        """Read the immutable local inventory without issuing a cursor."""
        scope, offset = plan["scope"], plan["offset"]
        if scope["tool"] == "recall_memory":
            source = self.memories[scope["category"]]
            end = min(offset + page_size, len(source))
            result = {"memoryKind": scope["category"], "authority": "historical-context-only",
                      "records": deepcopy(source[offset:end]), "available": len(source),
                      "nextOffset": end if end < len(source) else None}
        else:
            result = self.session.read(scope["category"], scope.get("kind", ""), offset,
                                       page_size, scope.get("factId", ""))
        return result

    def read(self, plan, page_size=PAGE_SIZE):
        result = self.preview(plan, page_size)
        scope = plan["scope"]
        next_offset = result.pop("nextOffset", None)
        result["nextCursor"] = None
        if next_offset is not None:
            token = "page:" + content_hash([self.nonce, scope, next_offset])[:32]
            self.cursors[token] = {"scope": deepcopy(scope), "offset": next_offset}
            result["nextCursor"] = token
        return result

    def admit(self, result):
        self.known.update({row["id"]: row["evidenceCategory"] for row in result.get("facts", [])})
