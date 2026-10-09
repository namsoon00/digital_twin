"""Small mandatory historical context, independent of optional detailed recall."""
from copy import deepcopy
import json

from digital_twin.modules.reasoning.contracts import content_hash, EvidenceContractError
from .brain_management import required_case_memory


CONTINUITY_VERSION = "observation-continuity-v1"
MAX_CONTINUITY_BYTES = 40 * 1024
MARKER = "required-continuity"


def required_memory(row):
    return required_case_memory(row) or row.get("memoryRole") == MARKER


def _summary(row, fields):
    result = {key: deepcopy(row[key]) for key in fields if key in row}
    omitted = []
    for key, value in list(result.items()):
        if isinstance(value, str) and len(value) > 600:
            result[key] = value[:600]
            omitted.append(key)
    result.update(memoryRole=MARKER, sourceMemoryHash=content_hash(row),
                  authority="historical-context-only", truncatedFields=omitted)
    return result


def continuity_memory(history, research):
    """Keep a dated judgment and work-state summaries; detailed claims need recall."""
    analyses, memories = [], []
    if history:
        row = next((row for row in history if row.get("quality", {}).get("status")
                    in {"accepted", "observation-only"}), history[0])
        analyses.append(deepcopy(row) if required_memory(row) else _summary(row, ("executionInputId", "observedAt", "completedAt", "accountId", "symbol",
            "worldId", "summary", "hypothesis", "counterEvidence", "quality", "followUpConditions", "previousFacts")))
        # Quality and conditions retain their meaning whole. They are bounded by
        # the authored observation contract, never silently cut into false claims.
        analyses[0]["quality"] = {key: deepcopy(row.get("quality", {}).get(key)) for key in ("status", "errors")}
    for row in research:
        if required_memory(row):
            memories.append(deepcopy(row))
        elif row.get("kind") in {"business-thesis", "company-research-record"}:
            memories.append({**deepcopy(row), "memoryRole": MARKER, "authority": "historical-context-only"})
        elif row.get("kind") == "brain-case":
            memories.append(_summary(row, ("kind", "caseId", "accountId", "symbol", "worldId", "question",
                "capability", "status", "revision", "reviewDue", "nextCheckAt", "completionCriterion", "reason",
                "researchAttempts", "lastResearch", "researchRequest")))
        elif row.get("kind") == "service-feedback":
            memories.append(_summary(row, ("kind", "caseId", "category", "status", "problem", "proposal", "verification")))
        elif row.get("runId"):
            memories.append(_summary(row, ("kind", "runId", "status", "completedAt", "stopReason", "verifiedClaimCount")))
        elif row.get("kind") == "ontology-development":
            memories.append(_summary(row, ("kind", "requestId", "status", "question", "blockedReason", "completedAt", "cases")))
    value = {"version": CONTINUITY_VERSION, "previousAnalyses": analyses, "researchResults": memories}
    # Due cases have their own mandatory budget in final generation. All other
    # continuity is small and explicit; failure must never become silent amnesia.
    bounded = {**value, "researchResults": [row for row in memories if not required_case_memory(row)]}
    if len(json.dumps(bounded, ensure_ascii=False, allow_nan=False).encode()) > MAX_CONTINUITY_BYTES:
        raise EvidenceContractError("mandatory continuity exceeds context budget")
    return value


def merge_recalled(required, recalled):
    hashes = {content_hash(row) for row in required}
    return deepcopy(required) + [deepcopy(row) for row in recalled if content_hash(row) not in hashes]


def memory_count(rows):
    """A required summary and recalled details still represent one source row."""
    return len({row.get("sourceMemoryHash") or content_hash(row) for row in rows})


def judgment_continuity(envelope):
    return {"version": CONTINUITY_VERSION, "authority": "historical-context-only",
            "previousAnalyses": deepcopy([row for row in envelope["previousAnalyses"] if required_memory(row)])}


def continuous_review_prompt(packet, draft):
    from .observation_clock import citable_review_prompt
    return """이전 판단 비교 검토 계약:
draft.judgmentContinuity는 작성 단계와 동일한 서버 제공 과거 판단 요약입니다.
이 기록으로 이전에 무엇을 판단했고 어떤 자료가 부족했는지에 대한 메타데이터 서술을 검증하세요.
이전 판단의 유지·수정이라는 해석 변화와 실제 시장의 변화를 구별하세요.
요약 자체는 현재 시장 사실이나 인과관계의 증거가 아니며, 현재 사실은 current에서 확인해야 합니다.
마지막 발송이 없어도 이전 내부 분석은 있을 수 있습니다. 첫 발송과 첫 분석을 혼동하지 마세요.
truncatedFields가 있는 필드의 생략된 내용이나 기억에 없는 주장은 추정하지 마세요.
""" + citable_review_prompt(packet, draft)


def continuous_planning_prompt(packet, history, research, legacy_research=False):
    from .retrieval import directed_planning_prompt
    from .research_request import RESEARCH_INSTRUCTIONS, LEGACY_RESEARCH_INSTRUCTIONS
    return """판단 연속성 계약:
memoryRole=required-continuity는 이전 판단과 진행 작업의 필수 요약입니다. 현재 사실로 인용하지 마세요.
이전 판단의 유지·수정 이유를 comparison에 쓰세요. 자료가 없으면 비교 한계를 밝히세요.
이전 내부 분석과 마지막 발송은 다릅니다. 발송 이력이 없다는 이유로 첫 분석이라고 쓰지 마세요.
truncatedFields가 있으면 해당 필드는 발췌입니다. 상세 내용은 recall_memory에서 확인할 수 있습니다.
조사 완료나 verifiedClaimCount만으로 질문이 해결되었다고 판단하지 마세요. 현재 facts에서 답을 확인하세요.
거시 발표값은 대상 기간·단위·출처 시각을 그대로 사용하세요. 발표 일정은 발표 결과가 아닙니다.
""" + (LEGACY_RESEARCH_INSTRUCTIONS if legacy_research else RESEARCH_INSTRUCTIONS) + directed_planning_prompt(packet, history, research)
