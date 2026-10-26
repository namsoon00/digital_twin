"""One reviewed next question grows from a cited answer, never from a retry."""
from copy import deepcopy
import re

from . import question_answer
from .research_work import validate_contract, contract_key
from .brain_management import case_identity
from digital_twin.modules.reasoning.contracts import content_hash

VERSION = "answer-led-research-chain-v1"
MAX_DEPTH = 6
RELATIONS = ("cause", "counter-evidence", "persistence", "value-implication")
INSTRUCTIONS = """답에서 다음 질문으로 이어지는 연구 계약:
질문을 반복 점검하는 데서 멈추지 말고 이번 questionResolutions.answerParts.confirmed에서 확인한 답이 무엇을 새로 묻게 하는지 검토하세요. 원인(cause), 반대 설명·반증(counter-evidence), 지속 조건(persistence), 현금창출·기업 가치에 미치는 영향(value-implication) 중 실제로 중요한 다음 단계가 있을 때만 최대 한 질문을 만드세요. 네 단계를 기계적으로 순회하지 마세요.
questionContinuations={parentCaseId,answerIndex,questionIndex,relation,rationale,expectedInsight,contract}의 배열로 연결합니다. answerIndex는 해당 부모의 이번 confirmed 배열, questionIndex는 이번 questions 배열의 영부터 시작하는 인덱스입니다. 연결 대상은 capability=research이며 기존 research 요청에 구체적 공개 검색어·출처를 씁니다. contract={target,period,answerCriterion}는 하나의 확인 대상·명시적 기간·최소 필수 근거로 좁히세요. rationale에는 확인한 답에서 왜 다음 질문이 나오는지, expectedInsight에는 답에 따라 어떤 설명을 구분하거나 수정할 수 있는지 쓰세요.
이번 연구 평가에서 만드는 research 질문은 반드시 questionContinuations로 연결하세요. 새 질문이 필요 없거나 확인한 답이 없으면 두 배열을 비우세요. 단순 문장 바꾸기, 이미 답한 질문, 부모의 missingRequirements 보완을 새 질문으로 만들지 마세요. 부족 원문 재수집은 부모 과제에서 계속합니다.
부분 답변에서도 독립적으로 확인한 답에 기초한 다음 질문은 가능합니다. 부모의 미해결 조건·기간·조사 횟수는 그대로 유지하며 부모 전체가 해결됐다고 표시하지 마세요. 부모의 inquiryContinuation이 있으면 같은 부모에서 다시 분기하지 마세요. 한 사슬은 최대 여섯 후속 단계이며 inquiryLineage.depth가 6이면 추가 생성 없이 정리합니다.
inquiryLineage는 이전 질문과 당시 답변의 연구 이력입니다. 현재 사실이나 새로운 답변의 증거로 인용하지 말고 current.facts에서 다시 확인하세요. 이미 충분한 답이 있거나 다음 답이 설명을 바꾸지 못하면 질문을 억지로 늘리지 마세요. 질문 연결과 자료 조사는 매매 행동·수익성 검증이 아닙니다.
"""
REVIEW_INSTRUCTIONS = """후속 질문 검토:
questionContinuations마다 부모의 이번 confirmed 답과 현재 원문, 새 질문의 contract, rationale, expectedInsight를 함께 대조하세요. 현재 인용으로 확인되지 않은 답을 전제로 삼거나 부모의 부족분·기존 질문을 새 이름으로 반복하면 거부하세요. 부모의 일부 답으로 독립적으로 물을 수 있는 새 원인·반증·지속 조건·가치 영향을 허용하되, 미확인 전제를 사실로 확장하면 거부하세요. 새로운 답이 설명에 줄 차이가 구체적이어야 합니다. 질문을 많이 만드는 것은 성과가 아닙니다.
"""


def enabled(packet):
    from .evidence_purpose import enabled as purpose_enabled
    from .execution_lanes import market
    from .question_assessment import enabled as assessment_enabled
    return (packet.get("researchChainPolicy") == VERSION and question_answer.enabled(packet)
            and assessment_enabled(packet) and purpose_enabled(packet) and not market(packet))


def normalized(text):
    return re.sub(r"[\W_]+", "", text.casefold())


def schema(research):
    from .insight_schema import obj, array, choice
    text = {"type": "string", "minLength": 8, "maxLength": 400}
    parents = [r["caseId"] for r in research if r.get("kind") == "brain-case" and r.get("capability") == "research"]
    return {**array(obj({"parentCaseId": choice(parents or [""]),
        "answerIndex": {"type": "integer", "minimum": 0, "maximum": 2},
        "questionIndex": {"type": "integer", "minimum": 0, "maximum": 1},
        "relation": choice(RELATIONS), "rationale": text, "expectedInsight": text,
        "contract": obj({k: {"type": "string", "minLength": 2, "maxLength": 300}
                         for k in ("target", "period", "answerCriterion")})})), "maxItems": 1}


def validate(rows, packet, research, result):
    if not isinstance(rows, list) or len(rows) > 1:
        raise ValueError("research chain requires at most one next question")
    cases = {r["caseId"]: r for r in research if r.get("kind") == "brain-case"}
    resolutions = {r["caseId"]: r for r in result.get("questionResolutions", [])}
    reviews = {r["caseId"]: r for r in result.get("caseReviews", [])}
    questions = result.get("workQuestions", [])
    linked, accepted = set(), []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"parentCaseId", "answerIndex", "questionIndex", "relation", "rationale", "expectedInsight", "contract"}:
            raise ValueError("invalid research chain fields")
        parent = cases.get(row["parentCaseId"], {})
        resolution = resolutions.get(row["parentCaseId"], {})
        parts = resolution.get("answerParts", {}).get("confirmed", [])
        index, answer_index = row["questionIndex"], row["answerIndex"]
        if (parent.get("capability") != "research" or not parent.get("questionContract")
                or any(parent.get(k) != packet.get(k) for k in ("accountId", "symbol", "worldId"))
                or reviews.get(row["parentCaseId"], {}).get("action") == "dismissed"
                or type(index) is not int or not 0 <= index < len(questions)
                or type(answer_index) is not int or not 0 <= answer_index < len(parts)
                or questions[index]["capability"] != "research" or not questions[index].get("researchRequest")
                or row["relation"] not in RELATIONS):
            raise ValueError("next question requires its own current cited parent answer")
        if parent.get("inquiryContinuation") or parent.get("inquiryLineage", {}).get("depth", 0) >= MAX_DEPTH:
            raise ValueError("research chain already continued or reached its depth limit")
        for field in ("rationale", "expectedInsight"):
            if not isinstance(row[field], str) or not 8 <= len(row[field].strip()) <= 400:
                raise ValueError("research chain requires an explicit next insight")
        validate_contract(row["contract"])
        contract = deepcopy(row["contract"])
        old = [r for r in cases.values()] + parent.get("inquiryLineage", {}).get("path", [])
        question = questions[index]["question"]
        ancestry = parent.get("inquiryLineage", {})
        if (content_hash(normalized(question)) in ancestry.get("ancestorQuestionKeys", [])
                or contract_key(contract) in ancestry.get("ancestorContractKeys", [])):
            raise ValueError("next question repeats a previous chain step")
        if any(normalized(question) == normalized(r.get("question", "")) or
               (r.get("questionContract") and contract_key(contract) == contract_key(r["questionContract"])) or
               r.get("contractKey") == contract_key(contract) for r in old):
            raise ValueError("next question repeats an existing question or completion contract")
        # Question answer validation has already proved the exact selected
        # usable fact references; keep the answer index, never model-copied prose.
        linked.add(index)
        accepted.append({**deepcopy(row), "contract": contract})
    if {i for i, q in enumerate(questions) if q["capability"] == "research"} != linked:
        raise ValueError("research assessment questions require answer lineage")
    return accepted


def lineage(parent, continuation, result):
    resolution = next(r for r in result["questionResolutions"] if r["caseId"] == parent["caseId"])
    answer = deepcopy(resolution["answerParts"]["confirmed"][continuation["answerIndex"]])
    previous = parent.get("inquiryLineage", {})
    path = deepcopy(previous.get("path", [])) + [{"caseId": parent["caseId"], "question": parent["question"],
        "contractKey": contract_key(parent["questionContract"]), "answer": answer["text"]}]
    return {"version": VERSION, "rootCaseId": previous.get("rootCaseId", parent["caseId"]),
        "parentCaseId": parent["caseId"], "depth": previous.get("depth", 0) + 1, "path": path,
        "parentAnswer": answer, "parentExecutionInputId": result["executionInputId"],
        "researchReviewInputId": result["researchQuality"]["reviewInputId"],
        "relation": continuation["relation"], "rationale": continuation["rationale"],
        "expectedInsight": continuation["expectedInsight"], "authority": "historical-research-lineage-only"}


def child_id(packet, question):
    return case_identity(packet["accountId"], packet["symbol"], packet["worldId"], "research", question)


def memory_lineage(value):
    """Keep the immediate answer plus duplicate keys; full history is readable."""
    result = deepcopy(value)
    path = result.get("path", [])
    if len(path) > 1:
        result["ancestorQuestionKeys"] = [content_hash(normalized(row["question"])) for row in path]
        result["ancestorContractKeys"] = [row["contractKey"] for row in path]
        result["omittedAncestorCount"] = len(path) - 1
        result["path"] = path[-1:]
    # The immediate answer already occurs in parentAnswer. Explanation prose
    # and full ancestry stay in the exact readable memory, not five mandatory
    # copies competing with current source evidence for the 40 KB budget.
    for row in result.get("path", []):
        row.pop("answer", None)
    result.pop("rationale", None)
    result.pop("expectedInsight", None)
    result["detailsAvailable"] = True
    return result
