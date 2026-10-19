"""Original-question progress and its authored impact, separate from source receipts."""
from copy import deepcopy
from digital_twin.modules.reasoning.contracts import content_hash

VERSION = "original-question-assessment-v1"
INSTRUCTIONS = """원래 질문의 답변과 판단 영향 계약:
사용자에게 보여줄 답변·부족 항목·판단 영향은 한국어로 작성하세요. 원문 인용·제목·고유 식별자는 원래 표기를 유지하세요.
questionWorkPolicy가 original-question-assessment-v1이면 원래 질문을 부분적으로 평가할 수 있어도 action=assess로 평가를 진행하세요.
assess는 내부 질문 평가이며 고객 알림을 발송하지 않습니다. 선택한 근거가 있는 waiting 질문도 caseReviews에서 wait/research/blocked로 검토하세요.
researchScope.assessmentOnly=true이면 finish도 원래 질문의 내부 평가입니다. notification.send=false이며 질문 답변을 위해 새 가격 확인 조건이나 사업 가설을 억지로 만들지 마세요.
질문 전체의 기준이 충족되지 않으면 answered/dismissed로 닫지 마세요. reason에는 이번에 확인한 부분 답변을 쓰고 원래 기간·지표·완료 기준과 부족 항목을 유지하세요.
questionResolutions.missingRequirements에는 questionScopes.missingEvidence를 그대로 포함하고 최종 검토에서 발견한 부족분을 보충하세요. answered는 빈 배열이고 wait/research/blocked는 구체적 부족분이 필요합니다.
sourceAnswer.status=answered는 앞선 조사자의 평가입니다. 현재 원문과 충돌하면 질문의 부족분을 기록하고 이 표시를 답변 완료 근거로 사용하지 마세요.
questionResolutions의 judgmentImpact={effect,reason}에 답변이 이번 설명에 미친 영향을 기록하세요.
effect는 supports/weakens/unchanged/insufficient이며 reason은 확인 근거에 따라 설명을 유지·수정한 이유입니다. 검토하지 않은 이전 판단과 비교했다고 주장하지 마세요.
부분 답변·자료 부재·가설 제안은 예측 검증이 아닙니다. 같은 자료로 같은 질문을 되풀이하거나 다른 문장으로 다시 등록하지 마세요.
"""


def enabled(packet):
    return packet.get("questionWorkPolicy") == VERSION


def prepare(research, inventory):
    """Polling, prices and our own assessment prose cannot renew documentary work."""
    result = []
    for original in research:
        row = deepcopy(original)
        if row.get("kind") == "brain-case" and row.get("capability") == "research":
            from .question_evidence import source_identity
            row["questionWorkKey"] = content_hash({"version": VERSION, "caseId": row["caseId"],
                "contract": row.get("questionContract", {}), "sourceAnswer": source_identity(row), "inventory": inventory})
            row["questionWorkUnchanged"] = any((row.get(key) or {}).get("workKey") == row["questionWorkKey"]
                                             for key in ("questionAssessment", "evidenceProgress"))
        result.append(row)
    return result


def validate_impact(value):
    if (not isinstance(value, dict) or set(value) != {"effect", "reason"}
            or value["effect"] not in {"supports", "weakens", "unchanged", "insufficient"}
            or not isinstance(value["reason"], str) or not 8 <= len(value["reason"].strip()) <= 500):
        raise ValueError("question resolution requires authored judgment impact")
    return {"effect": value["effect"], "reason": value["reason"].strip()}


def assessment(case, job, result, review, resolution, now):
    packet = result["input"]
    if not enabled(packet) or case.get("capability") != "research":
        return None
    quality = result.get("researchQuality") or {}
    if quality.get("status") != "accepted" or not quality.get("reviewInputId"):
        raise ValueError("question assessment requires independent research review")
    captured = next((row for row in packet.get("questionEvidenceIndex", []) if row["caseId"] == case["caseId"]), None)
    if not captured or captured.get("questionContract") != case.get("questionContract"):
        raise ValueError("question assessment requires its original captured contract")
    scope = next((row for row in packet["researchScope"]["questions"] if row["caseId"] == case["caseId"]), None)
    if not scope:
        raise ValueError("question assessment requires a frozen read scope")
    selected = set(scope["selectedFactIds"])
    if not selected.intersection(review["evidenceIds"]):
        raise ValueError("question assessment must cite its own selected source evidence")
    return {"version": VERSION, "status": review["action"] if review["action"] in {"answered", "dismissed"} else "partial",
        "workKey": captured["questionWorkKey"], "questionContract": deepcopy(captured["questionContract"]),
        "answer": review["reason"], "missingRequirements": deepcopy(resolution["missingRequirements"]),
        "sourceIdentity": captured["sourceAnswer"]["sourceIdentity"],
        "evidenceIds": list(review["evidenceIds"]), "sourceSnapshotId": packet["sourceSnapshotId"],
        "sources": [{"factId": fact["id"], "sourceUrl": fact.get("url", ""), "title": fact.get("title") or fact.get("label", ""),
            "documentHash": (fact.get("historicalDocumentReference") or {}).get("documentHash", ""),
            "periodEnd": (fact.get("historicalDocumentReference") or fact).get("periodEnd", "")}
            for fact in packet["facts"] if fact["id"] in review["evidenceIds"]],
        "taskId": job["taskId"], "executionInputId": result["executionInputId"], "at": now,
        "researchReviewInputId": quality["reviewInputId"],
        "judgmentImpact": deepcopy(resolution["judgmentImpact"]),
        "judgment": {key: deepcopy(result[key]) for key in ("summary", "hypothesis", "counterEvidence")},
        "hypothesisDisposition": resolution["disposition"], "authority": "question-assessment-only",
        "qualification": "not-empirically-qualified", "resumeOn": ["changed-source-evidence", "changed-question-contract"]}


def disputed_source_answer(case):
    row = case.get("questionAssessment") or {}
    if (row.get("version") != VERSION or row.get("status") != "partial" or not row.get("missingRequirements")
            or not row.get("researchReviewInputId")
            or row.get("questionContract") != case.get("questionContract")):
        return False
    from .question_evidence import source_identity
    return row.get("sourceIdentity") == content_hash(source_identity(case))
