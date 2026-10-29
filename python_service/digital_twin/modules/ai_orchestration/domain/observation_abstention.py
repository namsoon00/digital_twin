"""Bounded source delivery and abstention reasons; never publication authority."""
from copy import deepcopy
import hashlib

from .research_evidence_use import usable_fact

VERSION = "observation-abstention-v1"


def enabled(packet):
    return packet.get("abstentionPolicy") == VERSION


def original_fields(fact):
    fields = [key for key in ("officialDocumentText", "articleText", "body", "text")
              if isinstance(fact.get(key), str) and fact[key].strip()]
    for key in ("historicalDocumentReference", "questionSourceReference", "articleSourceReference"):
        ref = fact.get(key) or {}
        if not isinstance(ref, dict):
            continue
        text = ref.get("text")
        if (isinstance(text, str) and text.strip() and ref.get("passageTextHash")
                == hashlib.sha256(text.encode()).hexdigest()):
            fields.append(key + ".text")
    return fields


def source_delivery(packet):
    """Assess the actual writer facts, not read flags or collector summaries."""
    facts = {row["id"]: row for row in packet.get("facts", [])}
    groups = packet.get("priceInvestigationScope", {}).get("candidateGroups", {})
    result = {}
    for group in ("events", "market"):
        ids = groups.get(group, [])
        selected = [facts[key] for key in ids if key in facts]
        usable = [fact for fact in selected if usable_fact(fact)]
        original = [fact["id"] for fact in usable if original_fields(fact)]
        status = ("no-candidate" if not ids else "not-delivered" if not selected else
                  "ineligible" if not usable else "metadata-only" if group == "events" and not original
                  else "delivered")
        result[group] = {"status": status, "selectedFactIds": [f["id"] for f in selected],
                         "originalTextFactIds": original}
    return {"version": VERSION, "groups": result,
            "scope": "captured-candidates-only; content presence is not relevance or causal proof"}


def annotate(packet):
    return {**packet, "abstentionPolicy": VERSION, "sourceDelivery": source_delivery(packet)}


def validate_packet(packet):
    if not enabled(packet) or packet.get("sourceDelivery") != source_delivery(packet):
        raise ValueError("writer source delivery does not match frozen facts")


REASONS = {
    "execution-failed": "조회 또는 검토 실행을 완료하지 못했습니다. 의미 변화가 없다는 판단이 아닙니다.",
    "output-invalid": "설명과 근거 검증을 통과하지 못해 보류했습니다.",
    "source-not-delivered": "선택한 조사 자료가 작성 입력에 전달되지 않아 추가 의미를 판단하지 못했습니다.",
    "source-content-missing": "사건 자료의 원문이 작성 입력에 없어 추가 설명을 뒷받침하지 못했습니다.",
    "source-unavailable": "확보된 자료 목록에서 필요한 사건·시장 근거를 확인하지 못했습니다.",
    "evidence-insufficient": "확인한 자료로는 판단을 보완할 근거가 충분하지 않습니다.",
    "cause-unconfirmed": "확인한 자료로 가격 변동 원인을 특정하지 못했고, 별도로 알릴 변화도 제시되지 않았습니다.",
    "review-needed": "중요한 변화가 누락됐을 가능성이 있어 보류 판단을 재검토해야 합니다.",
    "repetition": "확인한 범위에서 이미 전달한 설명의 반복으로 판단해 기록만 남겼습니다.",
    "no-material-change": "확인한 범위에서 기존 설명이나 위험 판단을 바꿀 근거를 찾지 못했습니다.",
    "not-requested": "AI가 발송을 요청하지 않았습니다. 구체적인 사유는 원래 판단 기록을 확인해야 합니다.",
}


def assessment(result):
    if result.get("notification", {}).get("send") is not False:
        return {}
    packet = result.get("input") or {}
    groups = source_delivery(packet)["groups"] if enabled(packet) else {}
    review = result.get("abstentionReview") or {}
    statuses = {row["status"] for row in groups.values()}
    change = result.get("judgmentChange") or {}
    contribution = result.get("notificationContribution") or {}
    if result.get("failure") or review.get("status") in {"failed", "budget-wait", "execution-wait", "unavailable", "context-budget"}:
        code = "execution-failed"
    elif result.get("quality", {}).get("status") == "rejected":
        code = "output-invalid"
    elif "not-delivered" in statuses:
        code = "source-not-delivered"
    elif "metadata-only" in statuses:
        code = "source-content-missing"
    elif statuses.intersection({"no-candidate", "ineligible"}):
        code = "source-unavailable"
    elif change.get("impact") == "insufficient" or packet.get("retrieval", {}).get("missingEvidence"):
        code = "evidence-insufficient"
    elif review.get("decision") == "reconsider" and review.get("resolution") != "reauthored":
        code = "review-needed"
    elif result.get("priceExplanation", {}).get("status") == "cause-unconfirmed":
        code = "cause-unconfirmed"
    elif contribution.get("kind") == "repetition":
        code = "repetition"
    elif enabled(packet) and contribution.get("kind") == "no-added-value":
        code = "no-material-change"
    else:
        code = "not-requested"
    return {"version": VERSION, "reasonCode": code, "reason": REASONS[code],
            "authorReason": result.get("notification", {}).get("reason", ""),
            "sourceDelivery": deepcopy(groups), "authority": "diagnostic-only"}


INSTRUCTIONS = """보류 판단의 근거와 조사 범위:
sourceDelivery는 이번 작성 입력에 실제 전달된 자료의 상태입니다. 원문 선택 이력·bodyRead·기존 AI 요약은 원문 열람을 대신하지 않습니다.
no-candidate는 제한된 목록에서 미확보, not-delivered는 전달 누락, metadata-only는 원문 부재입니다. 이를 충분히 조사한 결과 변화가 없음으로 표현하지 마세요.
notification.reason에 반복, 자료 부족, 원인 미확인을 구분하고 확인한 범위를 명시하세요. 부족한 자료는 기존 questions 경로에서 중복을 확인해 구체적으로 요청합니다.
가격 원인을 모른다는 사실과 중요한 위험 변화는 별개입니다. 검증된 위험 변화·가설 반증·근거 강도 변화가 이미 전달되지 않았다면 원인 미확인을 밝히고 추가 가치를 평가하세요.
결론 유지나 같은 원문도 새 해석을 막지 않습니다. 기업 가치 가정, 사건의 기업 영향 경로, 반대 근거, 보유 영향이 달라졌는지 기존 전달 내용과 비교하세요. 근거가 없는 원인을 만들어 발송하지 마세요.
"""
