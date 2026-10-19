"""Subject-scoped read functions; cursors and page sizes belong to the runtime."""
from copy import deepcopy
import uuid

from digital_twin.modules.reasoning.contracts import content_hash
from digital_twin.modules.ai_orchestration.domain.retrieval import PAGE_SIZE, ReadRequestError, issue
from ..domain import memory_reference


class ObservationReadTools:
    def __init__(self, session, history, research, indexed=False):
        self.session = session
        self.memories = {"analyses": deepcopy(history), "memories": deepcopy(research)}
        self.indexed = indexed
        self.memory_index = memory_reference.index(history, research) if indexed else []
        self.memory_sources = {(category, "memory:" + content_hash(row)): deepcopy(row)
                               for category, rows in self.memories.items() for row in rows} if indexed else {}
        self.memory_paths = {(row["category"], row["memoryId"], part["path"])
                             for row in self.memory_index for part in row["sections"]}
        self.cursors = {}
        self.nonce = uuid.uuid4().hex
        self.known = {row["id"]: row["evidenceCategory"] for row in session.select([])["facts"]}

    def context(self):
        return {"pageSize": PAGE_SIZE, "pageSizeIsMaximum": True,
                "paginationVersion": "packet-budget-pages-v1",
                "availableCursors": [{"cursor": token, **row["scope"]} for token, row in self.cursors.items()],
                "knownFacts": [{"factId": key, "category": value} for key, value in self.known.items()]}

    def issued_context(self):
        groups = {}
        for category, memory_id, path in sorted(self.memory_paths):
            groups.setdefault((category, memory_id), []).append(path)
        return {"readableFacts": [{"factId": key, "category": value} for key, value in sorted(self.known.items())],
                "readableMemorySections": [{"category": category, "memoryId": memory_id, "paths": paths}
                                           for (category, memory_id), paths in groups.items()]}

    def resolve(self, request, index=0):
        scope = {key: value for key, value in request.items() if key != "cursor"}
        path = "requests[" + str(index) + "]"
        if request["tool"] == "read_memory":
            key = (request["category"], request["memoryId"], request["path"])
            if not self.indexed or key not in self.memory_paths:
                raise ReadRequestError([issue(path, "같은 세션·category의 memoryIndex 또는 section-index가 발급한 ID/path")])
            return {"scope": scope, "offset": 0}
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
        if scope["tool"] == "read_memory":
            source = self.memory_sources[(scope["category"], scope["memoryId"])]
            value = memory_reference.pointer(source, scope["path"])
            if memory_reference.size(value) > 24 * 1024:
                parts = memory_reference.children(value, scope["path"])
                return {"status": "section-index" if parts else "context-budget", "memoryKind": scope["category"],
                    "memoryId": scope["memoryId"], "path": scope["path"], "sections": parts[:32],
                    "omittedSectionCount": max(0, len(parts)-32), "contentHash": content_hash(value),
                    "bytes": memory_reference.size(value), "authority": "historical-context-only"}
            return {"memoryKind": scope["category"], "records": [{"kind": "memory-detail",
                "memoryId": scope["memoryId"], "sourceMemoryHash": content_hash(source),
                "path": scope["path"], "content": deepcopy(value), "contentHash": content_hash(value),
                "authority": "historical-context-only"}]}
        elif scope["tool"] == "recall_memory":
            source = self.memories[scope["category"]]
            end = min(offset + page_size, len(source))
            result = {"memoryKind": scope["category"], "authority": "historical-context-only",
                      "records": [memory_reference.brief(row, scope["category"]) for row in source[offset:end]]
                                 if self.indexed else deepcopy(source[offset:end]), "available": len(source),
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
        if result.get("status") == "section-index":
            self.memory_paths.update((result["memoryKind"], result["memoryId"], row["path"]) for row in result["sections"])
