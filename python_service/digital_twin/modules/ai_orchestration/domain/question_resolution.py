"""A question's explicit route to a thesis or experiment, with frozen lineage."""
from copy import deepcopy

VERSION = "question-resolution-v1"
DISPOSITIONS = ("business-thesis", "experiment", "defer", "not-applicable")
INSTRUCTIONS = """질문에서 가설로 이어지는 계약:
questionResolutions 배열로 이번 caseReviews에서 검토하는 research/develop-hypothesis 과제의 가설화 판단을 모두 기록하세요.
각 항목은 caseId, disposition, targetIndex, reason, evidenceIds입니다.
disposition=business-thesis이면 이번 businessResearch.theses의 인덱스(영부터)를 targetIndex로 연결하세요.
disposition=experiment이면 이번 questions 중 capability=develop-hypothesis인 질문 목록의 인덱스를 연결하세요. 최대 하나입니다.
자료 수집 완료나 질문 답변만으로 가설 검증이 끝난 것은 아닙니다. 현재 근거로 연결 과정·반증을 설명할 수 있을 때만 가설을 만드세요.
가설화할 근거가 부족하면 defer, 사실 확인만 필요한 질문이면 not-applicable이고 targetIndex=-1입니다. reason에 구체적인 판단 근거를 적으세요.
이미 연결된 가설/실험이 진행 중이면 같은 요청을 다시 만들지 말고 defer로 진행 상태와 다음 확인을 설명하세요.
이전 hypothesisResolution과 developmentProgress는 원래 질문의 처리 기록입니다. blockedReason을 다음 조사/판단에 반영하세요.
가설 등록·후보 생성·실험 처리 완료는 경험적 검증이나 운영 규칙 승격을 뜻하지 않습니다.
"""


def resolution_prompt(packet, history, research):
    from .business_research import business_prompt
    return INSTRUCTIONS + business_prompt(packet, history, research)


def resolution_schema(packet, research):
    from .business_research import business_schema
    from .insight_schema import obj, array, choice
    schema = business_schema(packet, research)
    cases = [row["caseId"] for row in research if row.get("kind") == "brain-case"
             and row.get("capability") in {"research", "develop-hypothesis"}]
    schema["properties"]["questionResolutions"] = {**array(obj({
        "caseId": choice(cases or [""]), "disposition": choice(DISPOSITIONS),
        "targetIndex": {"type": "integer", "minimum": -1, "maximum": 1},
        "reason": {"type": "string", "minLength": 8, "maxLength": 500},
        "evidenceIds": {**array(choice([row["id"] for row in packet.get("facts", [])] or [""])), "minItems": 1, "maxItems": 8},
    })), "maxItems": 5}
    schema["required"].append("questionResolutions")
    return schema


def validate_resolutions(value, packet, research, result, required=False):
    cases = {row["caseId"]: row for row in research if row.get("kind") == "brain-case"}
    reviews = {row["caseId"]: row for row in result.get("caseReviews", [])}
    needed = {key for key in reviews if cases[key].get("capability") in {"research", "develop-hypothesis"}}
    if value is None and not required:
        return []  # Older authored results remain readable.
    rows = [] if value is None else value
    if not isinstance(rows, list) or len(rows) > 5:
        raise ValueError("invalid question resolutions")
    known = {row["id"]: row for row in packet["facts"]}
    accepted, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"caseId", "disposition", "targetIndex", "reason", "evidenceIds"}:
            raise ValueError("invalid question resolution fields")
        key, disposition, index = row["caseId"], row["disposition"], row["targetIndex"]
        if key not in needed or key in seen or disposition not in DISPOSITIONS or type(index) is not int:
            raise ValueError("resolution requires one reviewed question")
        case = cases[key]
        if any(case.get(field) != packet.get(field) for field in ("accountId", "symbol", "worldId")):
            raise ValueError("question resolution scope mismatch")
        if not isinstance(row["reason"], str) or not 8 <= len(row["reason"].strip()) <= 500:
            raise ValueError("question resolution requires a reason")
        ids = row["evidenceIds"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 8 or any(not isinstance(key, str) or key not in known for key in ids):
            raise ValueError("question resolution requires current evidence")
        if disposition in {"business-thesis", "experiment"}:
            if any(known[key].get("judgementEvidenceUsable") is False or known[key].get("valuationDecisionEligible") is False for key in ids):
                raise ValueError("reference-only evidence cannot originate a hypothesis")
            targets = (result.get("businessResearch") or {}).get("theses", []) if disposition == "business-thesis" else result.get("developmentQuestions", [])
            if not 0 <= index < len(targets):
                raise ValueError("question resolution target missing")
            if reviews[key]["action"] in {"blocked", "dismissed", "research"}:
                raise ValueError("unresolved work cannot simultaneously advance to a hypothesis")
        elif index != -1:
            raise ValueError("deferred question cannot name a target")
        source = {"caseId": key, "revision": case["revision"], "question": case.get("question", ""),
                  "research": deepcopy(case.get("lastResearch", {}))}
        accepted.append({**deepcopy(row), "reason": row["reason"].strip(), "sourceQuestion": source})
        seen.add(key)
    if needed - seen:
        raise ValueError("reviewed research questions require a hypothesis disposition")
    return accepted


def raw_resolutions(rows):
    return [{key: deepcopy(value) for key, value in row.items() if key != "sourceQuestion"} for row in rows]


def resolution_review_prompt(packet, draft):
    from .business_research import business_review_prompt
    return INSTRUCTIONS + "독립 검토에서는 questionResolutions의 원래 질문·선택한 가설·현재 인용 근거가 서로 부합하는지도 hypothesis 항목으로 검토하세요.\n" + business_review_prompt(packet, draft)
