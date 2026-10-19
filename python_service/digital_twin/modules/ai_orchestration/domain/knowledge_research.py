"""AI-facing research contract over the independently owned knowledge domain."""
from digital_twin.modules.research_knowledge.contracts import knowledge_proposal_schema, validate_knowledge_proposals
from .research_evidence_use import usable_fact

INSTRUCTIONS = """전문지식과 지속 연구 계약:
current.researchKnowledge는 버전이 있는 연구 방법과 과거 설명·반론·관측 결과입니다. 현재 시장 사실로 인용하지 마세요.
knowledgeResearch={claims,coverageNote}를 반환하세요. claims는 최대 세 개이며 frameworkId에 연결된 목적과 적용 조건을 지키세요.
각 claim은 claimId,disposition(create/retain/revise/retire),question,mechanism,assumption,alternative,invalidation,reason,evidenceIds,counterEvidenceIds,missingEvidence,businessThesisIndex,reviewAfterDays입니다.
새 주장은 claimId를 빈 문자열로 쓰고 기존 설명은 캡처된 claimId로 유지·수정·철회하세요. 이전 반증이나 실패를 무시하고 같은 설명을 다시 등록하지 마세요.
근거는 current.facts의 사용 가능한 ID만 인용하세요. 방법론 문헌·과거 AI 설명·TypeDB 조건 일치는 예측 성공이나 인과성의 증거가 아닙니다.
coverage는 자료의 존재만 나타내며 질문의 답이 아닙니다. 부족한 자료와 서로 다른 기간·단위·회계 범위를 명시하세요.
필수 근거가 빠졌다면 missingEvidence에 남기고 기존 questions 계약으로 조사 우선순위를 정하세요. 계좌의 사적 자료를 웹 검색어로 보내지 마세요.
evidenceState의 not-in-selected-input은 현재 선택 자료에 없다는 뜻이며 반증이나 철회가 아닙니다. 현재 부적격·변경 자료는 같은 시점의 근거로 재검토하세요.
currentAssessment는 원래 사업 계약에 대한 이번 코드 관측입니다. 실패 관측의 methodProposal을 다음 연구에 참고하되 다른 기업에 검증된 법칙으로 확대하지 마세요.
businessThesisIndex는 이번 businessResearch.theses의 인덱스이며 관련이 없으면 -1입니다. 사업 지표 방향만으로 가격 예측이나 위험 관리의 성공을 판정하지 마세요.
설명 문장에 숫자나 매매 지시를 넣지 말고 수치는 원래 구조화된 근거로 보존하세요. 연구 방법 개선은 검증 대기 제안이며 규칙 변경 권한이 아닙니다.
"""


def knowledge_prompt(packet, history, research):
    from .question_resolution import resolution_prompt
    from .scoped_research import instructions
    from .memory_reference import VERSION, INSTRUCTIONS as memory_instructions
    from .execution_lanes import instructions as lane_instructions, market
    knowledge = ("연구 방법과 과거 설명은 현재 사실이 아닙니다. knowledgeResearch는 claims=[]와 coverageNote만 반환하세요.\n"
                 if market(packet) else INSTRUCTIONS) if packet.get("researchKnowledge") else ""
    return lane_instructions(packet) + (memory_instructions if packet.get("memoryReadPolicy") == VERSION else "") + instructions(packet) + knowledge + resolution_prompt(packet, history, research)


def knowledge_schema(packet, research):
    from .question_resolution import resolution_schema
    schema = resolution_schema(packet, research)
    if packet.get("researchKnowledge"):
        schema["properties"]["knowledgeResearch"] = knowledge_proposal_schema(packet["researchKnowledge"], [row["id"] for row in packet["facts"]])
        schema["required"].append("knowledgeResearch")
    from .scoped_retrieval import AUTHOR_SCOPE_POLICY
    if packet.get("researchScope", {}).get("assessmentPolicy") == AUTHOR_SCOPE_POLICY:
        for key in ("caseReviews", "questionResolutions"):
            schema["properties"][key]["maxItems"] = 0
    from .execution_lanes import market
    if market(packet):
        schema["properties"]["caseReviews"]["maxItems"] = min(5, sum(row.get("kind") == "brain-case" for row in research))
        schema["properties"]["questionResolutions"]["maxItems"] = min(5, sum(row.get("kind") == "brain-case" and row.get("capability") == "develop-hypothesis" for row in research))
        if "knowledgeResearch" in schema["properties"]:
            schema["properties"]["knowledgeResearch"]["properties"]["claims"]["maxItems"] = 0
    return schema


def validate_research(value, packet, business_theses=()):
    from .insight_contract import asserts_certainty
    from .validation_diagnostic import PlanValidationError
    from digital_twin.modules.decisions.contracts import narrative_presentation_errors
    from .execution_lanes import market
    if market(packet) and isinstance(value, dict) and value.get("claims"):
        raise PlanValidationError("market-research-write", "knowledgeResearch.claims", "시장 관찰은 연구 기억을 읽기만 하므로 claims=[]입니다.")
    usable = {row["id"] for row in packet["facts"] if usable_fact(row)}
    try:
        result = validate_knowledge_proposals(value, packet.get("researchKnowledge"), packet, usable, business_theses)
    except ValueError as error:
        raise PlanValidationError("research-knowledge-contract", "knowledgeResearch",
            "동결된 연구 틀·주장 ID·현재 사용 가능한 근거를 참조하고, 설명·반론·자료 부족·기존 가설 인덱스를 출력 계약에 맞게 작성하세요.") from error
    if result:
        for claim in result["claims"]:
            texts = [claim[key] for key in ("question", "mechanism", "assumption", "alternative", "invalidation", "reason")]
            if any(asserts_certainty(text) for text in texts) or narrative_presentation_errors("NO_ACTION", texts):
                raise PlanValidationError("research-knowledge-language", "knowledgeResearch.claims",
                    "연구 설명은 매매 지시나 확정적 예측 없이 가정·반론·확인 조건을 보존해야 합니다.")
            if claim["purpose"] == "company-value" and claim["evidenceIds"]:
                ids = set(claim["evidenceIds"])
                if not any(row["id"] in ids and row.get("evidenceCategory") in {"company", "research", "valuation"} for row in packet["facts"]):
                    raise PlanValidationError("research-knowledge-business-evidence", "knowledgeResearch.claims.evidenceIds",
                        "기업 가치 설명은 회사·원문·가치평가 근거에 연결하고 자료가 없으면 부족한 근거를 명시하세요.")
    return result


def knowledge_review_prompt(packet, draft):
    from .question_resolution import resolution_review_prompt
    from .scoped_research import instructions
    extra = "knowledgeResearch의 연결 과정·적용 범위·반론·철회 이유를 hypothesis 항목에서 함께 검토하세요. 방법론·과거 설명을 현재 사실이나 검증된 예측으로 승격하면 거부하세요.\n"
    return instructions(packet) + (extra if packet.get("researchKnowledge") else "") + resolution_review_prompt(packet, draft)
