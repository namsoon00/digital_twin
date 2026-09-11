"""Workspace HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.web.adapters.workspace import DOMAIN_TYPES
from digital_twin.infrastructure.web.adapters.workspace import MEMORY_CATEGORIES
from digital_twin.infrastructure.web.adapters.workspace import chat_payload
from digital_twin.infrastructure.web.adapters.workspace import normalize_amount
from digital_twin.infrastructure.web.adapters.workspace import normalize_item_fields
from digital_twin.infrastructure.web.adapters.workspace import patch_item
from digital_twin.infrastructure.web.adapters.workspace import read_store
from digital_twin.infrastructure.web.adapters.workspace import save_store
from digital_twin.infrastructure.web.adapters.workspace import snapshot_payload
from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import new_id
from digital_twin.infrastructure.web.common import now
from digital_twin.infrastructure.web.events import new_domain_event
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from digital_twin.platform.domain.event_types import APP_ITEM_REMOVED
from digital_twin.platform.domain.event_types import APP_ITEM_UPDATED
from digital_twin.platform.domain.event_types import APP_MEMORY_RECORDED
from digital_twin.platform.domain.event_types import APP_MEMORY_REMOVED
from digital_twin.platform.domain.event_types import APP_MEMORY_UPDATED
from digital_twin.platform.domain.event_types import APP_PROFILE_UPDATED
from typing import Callable
import re


@dataclass(frozen=True)
class WorkspaceRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    chat_payload: Callable[..., object] = chat_payload
    new_domain_event: Callable[..., object] = new_domain_event
    read_store: Callable[..., object] = read_store
    save_store: Callable[..., object] = save_store
    snapshot_payload: Callable[..., object] = snapshot_payload

    def route_bootstrap(self, request, path: str, query: Query):
        if path == "/api/bootstrap" and request.command == "GET":
            return request.send_payload(200, self.snapshot_payload())
        return NOT_HANDLED

    def route_profile(self, request, path: str, query: Query):
        if path == "/api/profile" and request.command == "PUT":
            body = request.read_json_body()
            if not body.get("ownerName") or not body.get("assistantName"):
                return request.send_payload(400, {"error": "이름과 비서 이름은 필요합니다."})
            store = self.save_store(lambda draft: draft.update({"profile": {**draft["profile"], **body}}))
            self.new_domain_event(
                APP_PROFILE_UPDATED,
                "profile",
                {
                    "ownerName": store["profile"].get("ownerName"),
                    "assistantName": store["profile"].get("assistantName"),
                },
            )
            return request.send_payload(200, {"profile": store["profile"]})

        if path == "/api/chat" and request.command == "POST":
            if request.share_access().shared:
                return request.send_payload(403, {"error": "로컬 AI 실행은 이 컴퓨터에서 직접 접속할 때만 사용할 수 있습니다."})
            return request.send_payload(200, self.chat_payload(request.read_json_body()))
        return NOT_HANDLED

    def route_memories(self, request, path: str, query: Query):
        if path == "/api/memories":
            if request.command == "GET":
                return request.send_payload(200, {"memories": self.read_store()["memories"]})
            if request.command == "POST":
                body = request.read_json_body()
                content = configured(body.get("content"))
                if not content:
                    return request.send_payload(400, {"error": "기억 내용을 입력하세요."})
                stamped = now()
                memory = {
                    "id": new_id("mem"),
                    "content": content,
                    "category": body.get("category") if body.get("category") in MEMORY_CATEGORIES else "other",
                    "status": "candidate" if body.get("status") == "candidate" else "approved",
                    "importance": max(1, min(5, int(body.get("importance") or 3))),
                    "source": "manual",
                    "createdAt": stamped,
                    "updatedAt": stamped,
                }
                store = self.save_store(lambda draft: draft["memories"].insert(0, memory))
                self.new_domain_event(
                    APP_MEMORY_RECORDED,
                    memory["id"],
                    {"memoryId": memory["id"], "category": memory["category"], "source": "manual"},
                )
                return request.send_payload(200, {"memory": memory, "memories": store["memories"]})

        memory_match = re.match(r"^/api/memories/([^/]+)$", path)
        if memory_match and request.command == "PATCH":
            memory_id = memory_match.group(1)
            body = request.read_json_body()

            def mutate(draft):
                next_memories = []
                for memory in draft["memories"]:
                    if memory.get("id") == memory_id:
                        updated = {**memory, **body, "updatedAt": now()}
                        if body.get("content"):
                            updated["content"] = configured(body.get("content"))
                        next_memories.append(updated)
                    else:
                        next_memories.append(memory)
                draft["memories"] = next_memories

            store = self.save_store(mutate)
            self.new_domain_event(APP_MEMORY_UPDATED, memory_id, {"memoryId": memory_id})
            return request.send_payload(200, {"memories": store["memories"]})
        if memory_match and request.command == "DELETE":
            memory_id = memory_match.group(1)
            store = self.save_store(lambda draft: draft.update({"memories": [memory for memory in draft["memories"] if memory.get("id") != memory_id]}))
            self.new_domain_event(APP_MEMORY_REMOVED, memory_id, {"memoryId": memory_id})
            return request.send_payload(200, {"memories": store["memories"]})

        if path == "/api/items":
            if request.command == "GET":
                return request.send_payload(200, {"items": self.read_store()["items"]})
            if request.command == "POST":
                body = request.read_json_body()
                title = configured(body.get("title"))
                if body.get("type") not in DOMAIN_TYPES or not title:
                    return request.send_payload(400, {"error": "유형과 제목을 입력하세요."})
                stamped = now()
                item = {
                    "id": new_id("item"),
                    "type": body.get("type"),
                    "title": title,
                    "status": configured(body.get("status")) or "open",
                    "date": configured(body.get("date")),
                    "amount": normalize_amount(body.get("amount")),
                    "currency": configured(body.get("currency")),
                    "ticker": configured(body.get("ticker")).upper(),
                    "location": configured(body.get("location")),
                    "notes": configured(body.get("notes")),
                    "fields": normalize_item_fields(body.get("fields")),
                    "createdAt": stamped,
                    "updatedAt": stamped,
                }
                store = self.save_store(lambda draft: draft["items"].insert(0, item))
                self.new_domain_event(
                    APP_ITEM_UPDATED,
                    item["id"],
                    {"itemId": item["id"], "type": item["type"], "status": item["status"]},
                )
                return request.send_payload(200, {"item": item, "items": store["items"]})

        item_match = re.match(r"^/api/items/([^/]+)$", path)
        if item_match and request.command == "PATCH":
            item_id = item_match.group(1)
            body = request.read_json_body()
            store = self.save_store(lambda draft: draft.update({"items": [patch_item(item, body) if item.get("id") == item_id else item for item in draft["items"]]}))
            self.new_domain_event(APP_ITEM_UPDATED, item_id, {"itemId": item_id, "patched": True})
            return request.send_payload(200, {"items": store["items"]})
        if item_match and request.command == "DELETE":
            item_id = item_match.group(1)
            store = self.save_store(lambda draft: draft.update({"items": [item for item in draft["items"] if item.get("id") != item_id]}))
            self.new_domain_event(APP_ITEM_REMOVED, item_id, {"itemId": item_id})
            return request.send_payload(200, {"items": store["items"]})
        return NOT_HANDLED
