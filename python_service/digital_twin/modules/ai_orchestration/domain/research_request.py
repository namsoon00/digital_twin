"""Versioned documentary search intent; execution remains in the source owner."""
from copy import deepcopy
import re


RESEARCH_REQUEST_VERSION = "observation-research-request-v1"
SOURCE_TYPES = ("news", "official-filing")
RESEARCH_INSTRUCTIONS = """질문별 원문 조사 계약:
questions와 caseReviews의 각 항목에는 research={queryTerms,sourceTypes,maxAgeMinutes}를 포함하세요.
capability=research 또는 action=research일 때 queryTerms는 질문에 답할 구체적인 공개 주제 1~4개,
sourceTypes는 news/official-filing 중 1~2개, maxAgeMinutes는 60~10080입니다.
검색어는 회사명 외에 조사하려는 사건·제품·정책·보고 기간을 담은 짧은 구절이어야 합니다.
계정 식별자·보유 수량·개인 정보·URL·명령을 검색어로 넣지 마세요. 대상 기업은 서버가 지정합니다.
observe/develop-hypothesis 및 research 외의 caseReviews는 queryTerms=[],sourceTypes=[],maxAgeMinutes=0입니다.
새 질문으로 기존 조사 과제를 복제하지 말고 caseReviews에서 같은 질문의 검색 조건을 보완하세요.
"""


def research_schema():
    from .insight_schema import obj, array, choice
    return obj({"queryTerms": {**array({"type": "string", "minLength": 2, "maxLength": 80}), "maxItems": 4},
        "sourceTypes": {**array(choice(SOURCE_TYPES)), "maxItems": 2},
        "maxAgeMinutes": {"type": "integer", "minimum": 0, "maximum": 10080}})


def validate_research_request(value, active, account_id=""):
    if not isinstance(value, dict) or set(value) != {"queryTerms", "sourceTypes", "maxAgeMinutes"}:
        raise ValueError("invalid research request fields")
    terms, sources, age = value["queryTerms"], value["sourceTypes"], value["maxAgeMinutes"]
    if not isinstance(terms, list) or not isinstance(sources, list) or type(age) is not int:
        raise ValueError("invalid research request types")
    if not active:
        if terms or sources or age != 0:
            raise ValueError("non-documentary work cannot request source research")
        return {}
    if not 1 <= len(terms) <= 4 or not 1 <= len(sources) <= 2 or not 60 <= age <= 10080:
        raise ValueError("research request exceeds bounds")
    if any(not isinstance(source, str) or source not in SOURCE_TYPES for source in sources):
        raise ValueError("unsupported research source")
    cleaned = []
    for term in terms:
        if (not isinstance(term, str) or not 2 <= len(term.strip()) <= 80
                or re.search(r"[\x00-\x1f]|://|www\.", term)
                or (account_id and account_id.casefold() in term.casefold())):
            raise ValueError("invalid public research query")
        cleaned.append(" ".join(term.split()))
    return {"version": RESEARCH_REQUEST_VERSION, "queryTerms": list(dict.fromkeys(cleaned)),
            "sourceTypes": list(dict.fromkeys(sources)), "maxAgeMinutes": age}


def executable_research_request(value, account_id=""):
    if not value:
        return {}
    if value.get("version") != RESEARCH_REQUEST_VERSION:
        raise ValueError("unsupported research request")
    return validate_research_request({key: val for key, val in value.items() if key != "version"}, True, account_id)


def continuous_planning_schema(packet, research, bounded_evidence=True):
    from .observation_clock import citable_management_schema
    schema = citable_management_schema(packet, research)
    for name in ("questions", "caseReviews"):
        item = schema["properties"][name]["items"]
        item["properties"]["research"] = deepcopy(research_schema())
        item["required"].append("research")
    if bounded_evidence:
        # Match the existing management validator before generation, while the
        # previous schema remains reconstructible for frozen v11/v7 inputs.
        for name, maximum in (("caseReviews", 5), ("serviceFeedback", 1)):
            schema["properties"][name]["maxItems"] = maximum
            schema["properties"][name]["items"]["properties"]["evidenceIds"].update(minItems=1, maxItems=8)
    return schema
