"""A question's decision purpose and bounded evidence work, without new authority."""
from copy import deepcopy
from .insight_schema import obj, array, choice
from .research_evidence_use import usable_fact

VERSION = "question-purpose-v1"
INSTRUCTIONS = """질문 목적 계약:
새 research 질문마다 questionPurposes에 questionIndex, decisionRelevance, uncertainty, expectedInsight, evidenceTasks, evidenceIds를 기록하세요. questionIndex는 이번 questions 배열의 영부터 시작하는 인덱스입니다.
질문은 기업별 현재 근거에서 드러난 중요한 불확실성을 겨냥하세요. decisionRelevance에는 답에 따라 어떤 기업 설명·가치 평가·보유 위험 판단이 달라지는지, uncertainty에는 아직 구분하지 못한 설명, expectedInsight에는 서로 다른 답이 의미하는 차이를 쓰세요. 질문 종류를 채우거나 숫자를 반복 확인하는 것 자체는 목적이 아닙니다.
evidenceTasks는 원래 질문에 필요한 최대 세 개의 {target,period,answerCriterion}입니다. 수치·기간·단위·원문 확인은 이 자료 작업으로 좁히고, 같은 부족분을 새 질문으로 쪼개지 마세요. 이 배열은 추가 호출이나 병렬 수집을 승인하지 않으며 기존 한도 안에서 필요한 작업만 진행합니다.
현재 evidenceIds로 질문의 발생 이유를 연결하세요. 이미 있는 질문·부분 답·반대 근거·부족 항목을 먼저 읽으세요. 답이 판단을 바꿀 이유가 없거나 기존 질문의 부족분이면 새 질문을 만들지 말고 기존 과제를 유지하세요. 질문을 만들지 않는 것도 허용합니다.
questionSourceHandoff는 이미 읽은 질문 원문의 연결입니다. 해당 caseId의 factIds를 답변에 인용할 수 있지만 원래 researchScope의 대기 상태·미확인 조건은 그대로 유지됩니다. 원문을 전달했다는 사실은 답변 완료나 가설 검증이 아닙니다.
"""


def enabled(packet):
    return packet.get("questionPurposePolicy") == VERSION


def schema(packet):
    text = {"type": "string", "minLength": 8, "maxLength": 350}
    task = obj({key: {"type": "string", "minLength": 1, "maxLength": 250}
                for key in ("target", "period", "answerCriterion")})
    return {**array(obj({"questionIndex": {"type": "integer", "minimum": 0, "maximum": 1},
        "decisionRelevance": text, "uncertainty": text, "expectedInsight": text,
        "evidenceTasks": {**array(task), "minItems": 1, "maxItems": 3},
        "evidenceIds": {**array(choice([f["id"] for f in packet["facts"] if usable_fact(f)] or [""])),
                        "minItems": 1, "maxItems": 4}})), "maxItems": 2}


def validate(rows, packet, result):
    if not isinstance(rows, list) or len(rows) > 2:
        raise ValueError("question purposes must be bounded")
    needed = {i for i, row in enumerate(result.get("workQuestions", [])) if row["capability"] == "research"}
    facts = {f["id"] for f in packet["facts"] if usable_fact(f)}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(schema(packet)["items"]["properties"]):
            raise ValueError("invalid question purpose fields")
        index = row["questionIndex"]
        if type(index) is not int or index not in needed or index in seen:
            raise ValueError("purpose must name one new research question")
        seen.add(index)
        if any(not isinstance(row[k], str) or not 8 <= len(row[k].strip()) <= 350
               for k in ("decisionRelevance", "uncertainty", "expectedInsight")):
            raise ValueError("question requires decision relevance and uncertainty")
        ids = row["evidenceIds"]
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 4
                or any(not isinstance(k, str) or k not in facts for k in ids) or len(set(ids)) != len(ids)):
            raise ValueError("question purpose requires current usable evidence")
        tasks = row["evidenceTasks"]
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 3:
            raise ValueError("question requires bounded evidence tasks")
        for task in tasks:
            if (not isinstance(task, dict) or set(task) != {"target", "period", "answerCriterion"}
                    or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 250 for v in task.values())):
                raise ValueError("evidence task requires target, period and completion criterion")
        if len({tuple(task[k] for k in ("target", "period", "answerCriterion")) for task in tasks}) != len(tasks):
            raise ValueError("duplicate evidence tasks")
    if seen != needed:
        raise ValueError("every new research question requires its decision purpose")
    return deepcopy(rows)


def for_question(result, question, capability):
    if not enabled(result.get("input", {})) or capability != "research":
        return {}
    index = next((i for i, row in enumerate(result.get("workQuestions", []))
                  if row["question"] == question and row["capability"] == capability), -1)
    purpose = next((row for row in result.get("questionPurposes", []) if row["questionIndex"] == index), None)
    return ({"version": VERSION, **{k: deepcopy(v) for k, v in purpose.items() if k != "questionIndex"},
             "executionInputId": result["executionInputId"], "authority": "research-purpose-only"} if purpose else {})
