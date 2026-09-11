"""Research Evidence HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.web.adapters.research_evidence import delete_research_evidence_payload
from digital_twin.infrastructure.web.adapters.research_evidence import research_evidence_detail_payload
from digital_twin.infrastructure.web.adapters.research_evidence import research_evidence_payload
from digital_twin.infrastructure.web.adapters.research_evidence import revalidate_research_evidence_payload
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from typing import Callable
import re
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class ResearchEvidenceRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    delete_research_evidence_payload: Callable[..., object] = delete_research_evidence_payload
    research_evidence_detail_payload: Callable[..., object] = research_evidence_detail_payload
    research_evidence_payload: Callable[..., object] = research_evidence_payload
    revalidate_research_evidence_payload: Callable[..., object] = revalidate_research_evidence_payload

    def route_research_evidence(self, request, path: str, query: Query):
        if path == "/api/research-evidence" and request.command == "GET":
            return request.send_payload(200, self.research_evidence_payload(query))

        if path == "/api/research-evidence/revalidate" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 근거 검증 상태를 갱신할 수 없습니다."):
                return
            return request.send_payload(200, self.revalidate_research_evidence_payload(request.read_json_body()))

        research_evidence_match = re.match(r"^/api/research-evidence/([^/]+)$", path)
        if research_evidence_match and request.command == "GET":
            payload = self.research_evidence_detail_payload(urllib.parse.unquote(research_evidence_match.group(1)))
            return request.send_payload(200 if payload.get("item") else 404, payload or {"error": "리서치 근거를 찾지 못했습니다."})
        return NOT_HANDLED

    def route_delete_research_evidence(self, request, path: str, query: Query):
        evidence_match = re.match(r"^/api/research-evidence/([^/]+)$", path)
        if evidence_match and request.command == "DELETE":
            if not request.ensure_writable("공유 모드에서는 저장된 리서치 근거를 변경할 수 없습니다."):
                return
            evidence_id = urllib.parse.unquote(evidence_match.group(1))
            return request.send_payload(200, self.delete_research_evidence_payload(evidence_id, query))
        return NOT_HANDLED
