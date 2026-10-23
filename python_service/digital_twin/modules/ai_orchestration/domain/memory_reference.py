"""Small historical briefs and exact, scoped pointers to captured memory."""
from copy import deepcopy
import json

from digital_twin.modules.reasoning.contracts import content_hash, EvidenceContractError
from .continuity import MARKER, MAX_CONTINUITY_BYTES, _summary

VERSION = "indexed-continuity-v1"
INSTRUCTIONS = """기억 조회 계약:
continuity는 필수 과거 판단·원래 질문·작업 상태의 요약이며 원문 전체가 아닙니다.
memoryIndex의 memoryId와 sections를 보고 필요한 부분만 read_memory(category,memoryId,path)로 읽으세요.
큰 부분은 section-index와 하위 경로를 반환합니다. 반환된 경로만 이어서 읽을 수 있습니다.
recall_memory는 요약 목록입니다. 원문을 다시 자동 첨부하지 않습니다.
omittedFields와 요약의 발췌는 자료 부재·반증이 아닙니다. 원문을 읽지 않았다면 상세 내용을 추정하지 마세요.
과거 설명·답변은 현재 사실이 아닙니다. 원래 질문의 기간·완료 기준과 반대 근거를 지키고 현재 facts로 검증하세요.
"""


def size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())


def brief(row, category):
    if row.get("memoryBriefVersion") == VERSION or row.get("kind") == "memory-detail":
        return deepcopy(row)
    if category == "analyses":
        fields = ("executionInputId", "observedAt", "completedAt", "accountId", "symbol", "worldId",
                  "summary", "hypothesis", "counterEvidence", "followUpConditions", "companyValue")
    elif row.get("kind") == "brain-case":
        fields = ("kind", "caseId", "accountId", "symbol", "worldId", "question", "capability", "status",
                  "revision", "reviewDue", "nextCheckAt", "completionCriterion", "researchAttempts", "questionContract")
    elif row.get("kind") == "business-thesis":
        # Checkpoint baselines are an exact deterministic observation contract.
        fields = ("kind", "caseId", "accountId", "symbol", "worldId", "revision", "status", "reviewDue",
                  "question", "mechanism", "assumption", "alternative", "invalidation", "missingEvidence",
                  "contract", "createdAt", "nextCheckAt", "expiresAt", "observations")
    elif row.get("kind") == "research-context":
        fields = ("kind", "caseId", "question", "status", "authority", "reviewedAssessment")
    else:
        fields = ("kind", "caseId", "runId", "requestId", "version", "accountId", "symbol", "worldId", "status",
                  "question", "problem", "proposal", "verification", "category", "completedAt", "stopReason",
                  "verifiedClaimCount", "blockedReason", "unavailableSources", "coverage", "use",
                  "originalQuestions", "currentQuestions", "previousInterpretation")
    result = _summary(row, fields)
    result.update(memoryBriefVersion=VERSION, memoryId="memory:" + content_hash(row),
                  detailsAvailable=True, omittedFields=sorted(set(row) - set(fields)))
    if category == "analyses":
        result["quality"] = {"status": row.get("quality", {}).get("status"),
                             "errorCount": len(row.get("quality", {}).get("errors") or [])}
    if row.get("kind") == "brain-case":
        if row.get("questionAssessment"):
            result["questionAssessment"] = {key: deepcopy(row["questionAssessment"][key]) for key in
                ("status", "missingRequirements", "judgmentImpact", "hypothesisDisposition") if key in row["questionAssessment"]}
        # Preserve exact question requirements, but omit answer prose/provider audits.
        for key in ("answerResolution", "evidenceProgress"):
            source = row.get(key) or {}
            result[key] = {field: deepcopy(source[field]) for field in
                ("version", "status", "missingRequirements", "resumeOn", "authority") if field in source}
        for key in ("hypothesisResolution", "developmentProgress"):
            source = row.get(key) or {}
            result[key] = {field: deepcopy(source[field]) for field in
                ("disposition", "state", "status", "qualification", "taskId", "requestId", "thesisId", "blockedReason") if field in source}
        research = row.get("lastResearch") or {}
        result["lastResearch"] = {field: deepcopy(research[field]) for field in ("taskId", "requestedAt") if field in research}
        answer = row.get("latestSourceAnswer") or research.get("result", {}).get("documentaryAnswer") or {}
        result["sourceAnswerStatus"] = answer.get("status", "unavailable")
    return result


def continuity(history, research):
    previous = next((row for row in history if row.get("quality", {}).get("status") in
                     {"accepted", "observation-only"}), history[0] if history else None)
    value = {"version": VERSION, "previousAnalyses": ([brief(previous, "analyses")] if previous else []) +
             [deepcopy(row) for row in history if row.get("kind") == "memory-detail"],
             "researchResults": [brief(row, "memories") for row in research]}
    required = {**value, "previousAnalyses": [row for row in value["previousAnalyses"] if row.get("kind") != "memory-detail"],
                "researchResults": [row for row in value["researchResults"] if row.get("kind") != "memory-detail"]}
    if size(required) > MAX_CONTINUITY_BYTES:
        # Original question/checkpoint contracts cannot be silently shortened.
        raise EvidenceContractError("mandatory memory contracts exceed context budget")
    return value


def escape(key):
    return str(key).replace("~", "~0").replace("/", "~1")


def children(value, path=""):
    items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else []
    return [{"path": path + "/" + escape(key), "bytes": size(item)} for key, item in items]


def pointer(row, path):
    value = row
    for key in path[1:].split("/"):
        key = key.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def index(history, research):
    return [{"memoryId": "memory:" + content_hash(row), "category": category,
        **{key: deepcopy(row[key]) for key in ("kind", "caseId", "runId", "status", "revision") if key in row},
        "bytes": size(row), "sections": children(row)}
        for category, rows in (("analyses", history), ("memories", research)) for row in rows]


def historical_facts(rows):
    """Only prior facts actually present in the frozen author input."""
    result = []
    for row in rows:
        values = row.get("previousFacts", [])
        if row.get("kind") == "memory-detail":
            path, value = row.get("path", ""), row.get("content")
            if path == "/previousFacts" and isinstance(value, list):
                values = value
            elif path.startswith("/previousFacts/") and isinstance(value, dict) and value.get("id"):
                values = [value]
        result.extend(deepcopy([fact for fact in values[:20] if isinstance(fact, dict)]))
    return result
