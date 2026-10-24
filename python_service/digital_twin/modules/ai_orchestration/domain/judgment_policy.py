"""Purpose-specific review and evidence lineage; no trading or truth authority."""
from copy import deepcopy

from digital_twin.modules.reasoning.contracts import content_hash, EvidenceContractError

VERSION = "judgment-quality-v1"
PROMPT_VERSION = "independent-observation-v24-purpose-quality"
REPAIR_VERSION = "independent-observation-repair-v20-purpose-quality"
REVIEW_VERSION = "independent-observation-review-v13-purpose-quality"
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION}

INSTRUCTIONS = """목적별 판단 품질 계약 (가격 확인 조건보다 우선):
source-dated는 확인된 원문·보고 기간의 사업/거시 설명입니다. 핵심 설명과 가설에 실제 읽은 자료를 인용하고, questions에 capability=observe로 다음 원문에서 무엇을 확인할지 구체적으로 남기면 observations/followUpConditions를 빈 배열로 둘 수 있습니다. 이는 자료 재검토 계획이며 가격 전망·가설 적중이 아닙니다. current-state의 시세 설명에는 기존 비교·확인 조건이 계속 필요합니다.
기업 가치는 사업의 수익·현금 창출, 핵심 가정과 경쟁 설명, 그 설명이 달라질 실적 조건으로 연결하세요. portfolioImpact에는 확인된 보유 비중·관리 기준의 의미를 연결하고, 대안을 비교할 원문이나 계정 근거가 없으면 비교 불가를 명시하세요. 다른 기업 수치·적정가·매매 행동을 만들어내지 마세요.
이전 질문의 답변이 supports/weakens/unchanged/insufficient 중 무엇인지 실제 인용 근거로 구분하세요. 답변 완료·검토 승인·지표 방향 관측을 가설 전체 검증으로 바꾸지 마세요. 기존 가설은 먼저 유지/수정/폐기를 검토하고 같은 질문이나 가설을 복제하지 마세요.
거시 자료는 현재 질문에 필요한 경우만 조회·선택하고 어떤 경로로 관련되는지 설명하세요. catalog에 있지만 읽지 않은 자료는 미확인이지 자료 부재가 아닙니다. 조회 결과가 충분하면 finish/assess로 작성에 넘기고 반복 조회하지 마세요.
"""


def enabled(packet):
    return packet.get("judgmentQualityPolicy") == VERSION


def prompt(packet, history, research):
    from .evidence_purpose import prompt as previous
    return INSTRUCTIONS + previous(packet, history, research)


def review_prompt(packet, draft):
    from .evidence_purpose import review_prompt as previous
    return INSTRUCTIONS + "\n자료 재검토 계획은 인용한 원문·질문과 연결되어야 합니다. 사업 설명에 불필요한 가격 조건을 요구하지 마세요.\n" + previous(packet, draft)


def validate_input(envelope):
    import hashlib
    from . import evidence_purpose, company_value
    from .execution_input import validate_execution_input, prompt_budget
    from .insight_repair import repair_prompt
    packet, phase = envelope["current"], envelope["promptVersion"]
    if not enabled(packet):
        raise EvidenceContractError("purpose quality policy missing")
    reviewing = phase == REVIEW_VERSION
    expected = review_prompt(packet, envelope["draft"]) if reviewing else prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if phase == REPAIR_VERSION:
        expected = repair_prompt(expected, envelope["repair"])
    if (expected != envelope["prompt"] or hashlib.sha256(expected.encode()).hexdigest() != envelope["promptHash"]
            or len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes"))):
        raise EvidenceContractError("purpose quality input changed")
    old = deepcopy(envelope)
    contract = company_value if company_value.enabled(packet) else evidence_purpose
    old["promptVersion"] = {PROMPT_VERSION: contract.PROMPT_VERSION, REPAIR_VERSION: contract.REPAIR_VERSION,
                            REVIEW_VERSION: contract.REVIEW_VERSION}[phase]
    old["prompt"] = (evidence_purpose.review_prompt(packet, envelope["draft"]) if reviewing else
                     evidence_purpose.prompt(packet, envelope["previousAnalyses"], envelope["researchResults"]))
    if phase == REPAIR_VERSION:
        old["prompt"] = repair_prompt(old["prompt"], envelope["repair"])
    old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
    validate_execution_input(old)
    return content_hash(envelope)


def documentary_review(result, packet):
    """A dated, cited documentary explanation can use source-triggered questions.

    Derive the plan from existing bounded observe questions. It cannot mark a
    source question answered or evaluate a numeric condition without its contract.
    """
    if not enabled(packet) or packet.get("analysisPurpose") != "source-dated":
        return {}
    from .business_research import clock as instant
    from .research_evidence_use import usable_fact
    cutoff = instant(packet.get("capturedAt"))
    documents = {}
    for row in packet.get("facts", []):
        date = row.get("publishedAt") or row.get("sourceAsOf") or row.get("asOf")
        clock = instant(date)
        if (clock and cutoff and clock <= cutoff and usable_fact(row)
                and (row.get("historicalReport") or row.get("documentVerified")
                     or row.get("kind") in {"macro-print", "company-relationship"})):
            documents[row["id"]] = row
    cited = set()
    for section in ("summary", "hypothesis"):
        refs = {ref.get("factId") for ref in result.get("claimEvidence", {}).get(section, []) if ref.get("period") == "current"}
        if not refs.intersection(documents).intersection(result.get("evidenceIds", [])):
            return {}
        cited.update(refs)
    ids = sorted(cited.intersection(documents).intersection(result.get("evidenceIds", [])))
    questions = [row["question"] for row in result.get("workQuestions", result.get("questions", []))
                 if isinstance(row, dict) and row.get("capability") == "observe"
                 and isinstance(row.get("question"), str) and 8 <= len(row["question"].strip()) <= 500]
    if not ids or not questions:
        return {}
    return {"version": VERSION, "questions": questions[:2],
            "basis": [{"factId": key, "hash": content_hash(documents[key]),
                       "sourceAsOf": documents[key].get("publishedAt") or documents[key].get("sourceAsOf") or documents[key].get("asOf")}
                      for key in ids], "resumeOn": "changed-source-evidence",
            "qualification": "source-review-plan-not-prediction", "authority": "no-action"}


def judgment_links(result):
    """Persist the answer → impact → review → next observation trail, no score."""
    packet = result["input"]
    facts = {row["id"]: row for row in packet.get("facts", [])}
    resolutions = result.get("questionResolutions", [])
    business = result.get("businessResearch") or {}
    ids = set(result.get("evidenceIds", []))
    for row in [*resolutions, *business.get("theses", []), *business.get("reviews", [])]:
        ids.update(row.get("evidenceIds", []))
    questions = []
    for row in resolutions:
        source = row.get("sourceQuestion", {})
        # The full source receipt already lives in questionResolutions. Link to
        # that exact record rather than duplicating large past research bodies.
        questions.append({**{key: deepcopy(row[key]) for key in
            ("caseId", "judgmentImpact", "missingRequirements", "disposition", "targetIndex", "evidenceIds") if key in row},
            "sourceQuestion": {key: deepcopy(source[key]) for key in ("caseId", "revision", "questionContract") if key in source},
            "sourceQuestionHash": content_hash(source)})
    return {"version": VERSION, "executionInputId": result["executionInputId"],
            "reviewInputIds": [value for value in (result.get("quality", {}).get("reviewInputId"),
                               result.get("researchQuality", {}).get("reviewInputId")) if value],
            "sourceSnapshotId": packet["sourceSnapshotId"],
            "evidence": [{"factId": key, "hash": content_hash(facts[key])} for key in sorted(ids) if key in facts],
            "questions": questions,
            "thesisReviews": deepcopy(business.get("reviews", [])),
            "followUpObservations": [{key: deepcopy(row[key]) for key in
                ("conditionId", "status", "transitionVerified", "observedAt") if key in row}
                for row in result.get("followUpEvaluations", [])],
            "quality": result.get("quality", {}).get("status"),
            "researchQuality": result.get("researchQuality", {}).get("status", "not-required"),
            "authority": "audit-lineage-only", "qualification": "not-empirically-qualified"}
