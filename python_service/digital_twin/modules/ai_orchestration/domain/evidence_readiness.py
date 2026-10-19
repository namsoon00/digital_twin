"""Versioned bounded reads and operational renewal; never investment authority."""
from copy import deepcopy
from . import execution_lanes

VERSION = "bounded-evidence-readiness-v1"
PROMPT_VERSION = "independent-observation-v21-bounded-evidence"
REPAIR_VERSION = "independent-observation-repair-v17-bounded-evidence"
REVIEW_VERSION = "independent-observation-review-v10-bounded-evidence"


def enabled(packet):
    return packet.get("evidenceReadPolicy") == VERSION


def annotate(packet):
    if not execution_lanes.identified(packet):
        return packet
    return {**packet, "evidenceReadPolicy": VERSION}


def source_refresh_requirement(packet):
    if not enabled(packet) or not execution_lanes.market(packet):
        return None
    quotes = [row for row in packet.get("quoteAssessment", {}).get("quotes", [])
              if row.get("status") in {"stale", "missing-time", "invalid-time"}
              and row.get("referenceState") != "last-close"]
    if not quotes:
        return None
    return {"version": VERSION, "reasonCode": "unusable-current-quote-clock",
            "sourceSnapshotId": packet["sourceSnapshotId"],
            "quotes": [{key: deepcopy(row.get(key)) for key in
                        ("evidenceId", "sourceAsOf", "status", "maxAgeMinutes")} for row in quotes],
            "authority": "operational-source-renewal-only"}


def prompt(packet, history, research):
    from .knowledge_research import knowledge_prompt
    return ("동결된 자료의 범위에서만 설명하세요. dataRecovery의 갱신 예약은 새 시세나 새 근거가 아닙니다. "
            "오래된 시세를 현재 장중 상태로 표현하지 말고 원천 시점을 보존하세요. "
            "원래 공시 질문에는 그 기간의 원문·표제·단위·연결 범위와 누계/단일 기간을 확인하며, 현재 시세를 추가 완료 조건으로 만들지 마세요.\n"
            "기존 연구를 retain할 때 question/mechanism/assumption/alternative/invalidation은 동결 원문 그대로 유지합니다. "
            "설명이나 적용 범위를 바꾸려면 revise를 선택하고 현재 근거를 명시하세요. "
            "거시 자료는 해당 설명과 관련될 때만 정확한 현재 fact 참조로 연결하고, 자료가 포함됐다는 이유로 인과관계를 만들지 마세요.\n"
            + knowledge_prompt(packet, history, research))


def schema(packet, research):
    from .knowledge_research import knowledge_schema
    from digital_twin.modules.research_knowledge.contracts import exact_knowledge_proposal_schema
    value = knowledge_schema(packet, research)
    if packet.get("researchKnowledge"):
        from .research_evidence_use import usable_fact
        value["properties"]["knowledgeResearch"] = exact_knowledge_proposal_schema(
            packet["researchKnowledge"], [fact["id"] for fact in packet["facts"] if usable_fact(fact)])
        if execution_lanes.market(packet):
            value["properties"]["knowledgeResearch"]["properties"]["claims"]["maxItems"] = 0
    return value
