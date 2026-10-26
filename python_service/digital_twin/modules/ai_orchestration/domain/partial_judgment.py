"""Verified parts may inform a bounded claim while the parent stays open."""
from copy import deepcopy
import hashlib

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from . import company_research_cycle as previous, question_answer

VERSION = "verified-partial-judgment-v1"
PROMPT_VERSION = "independent-observation-v28-partial-judgment"
REPAIR_VERSION = "independent-observation-repair-v24-partial-judgment"
REVIEW_VERSION = "independent-observation-review-v17-partial-judgment"
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION}
INSTRUCTIONS = """확인된 부분 답변의 판단 반영:
전체 질문의 완료 여부와 개별 사업 설명의 근거 충족 여부를 분리하세요. answerParts.confirmed의 현재 원문으로 확인한 사실이 특정 설명을 지지·약화하면 그 범위만 judgmentImpact=supports/weakens로 기록할 수 있습니다. reason에 어떤 설명이 왜 달라지는지, 남은 항목이 그 설명에 영향을 줄 수 있는 한계를 적으세요. summary와 hypothesis도 동일한 현재 근거의 실제 내용을 인용해야 합니다.
예를 들어 매출 비교는 확인했지만 현금흐름표가 없으면 매출 변화에 관한 설명은 가능하고 현금 회수 개선은 보류합니다. 다른 항목의 부족만으로 확인된 모든 답을 insufficient로 처리하지 마세요. 확인한 사실이 기존 설명을 바꾸지 않으면 unchanged, 해당 설명 자체의 근거가 부족하면 insufficient입니다. 판단 변경을 억지로 만들지 마세요.
부족한 원래 요구사항은 모두 유지하고 질문은 wait/research/blocked로 남기세요. 부분 답변은 answered·가설 검증 완료·적정가 산출 완료가 아닙니다. 미검증 가치평가, 인과 기여액, 매매 행동으로 확장하지 마세요. 같은 원문과 같은 답변을 새 질문·알림으로 반복하지 마세요.
"""


def enabled(packet):
    return (packet.get("partialJudgmentPolicy") == VERSION and previous.enabled(packet)
            and question_answer.enabled(packet))


def prompt(packet, history, research):
    return INSTRUCTIONS + previous.prompt(packet, history, research)


def review_prompt(packet, draft):
    return INSTRUCTIONS + previous.review_prompt(packet, draft)


schema, review_schema = previous.schema, previous.review_schema


def validate_impact(packet, resolution, result, facts):
    if not enabled(packet) or resolution["judgmentImpact"]["effect"] not in {"supports", "weakens"}:
        return
    from .research_evidence_use import usable_fact
    from .business_progress import business_ref
    confirmed = {key for part in resolution["answerParts"]["confirmed"] for key in part["evidenceIds"]}
    core = []
    for section in ("summary", "hypothesis"):
        core.append({row.get("factId") for row in result.get("claimEvidence", {}).get(section, [])
            if row.get("period") == "current" and row.get("factId") in confirmed
            and usable_fact(facts[row["factId"]], row.get("field", ""))
            and business_ref(row, facts[row["factId"]])})
    if not confirmed or not core[0].intersection(core[1]):
        raise ValueError("question judgment impact requires confirmed current evidence in its explanation")


def validate_input(envelope):
    from .execution_input import validate_execution_input, prompt_budget
    from .insight_repair import repair_prompt
    packet, version = envelope["current"], envelope["promptVersion"]
    if not enabled(packet):
        raise EvidenceContractError("partial judgment policy missing")
    reviewing = version == REVIEW_VERSION
    expected = review_prompt(packet, envelope["draft"]) if reviewing else prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        expected = repair_prompt(expected, envelope["repair"])
    if (expected != envelope["prompt"] or hashlib.sha256(expected.encode()).hexdigest() != envelope["promptHash"]
            or envelope["outputSchema"] != (review_schema() if reviewing else schema(packet, envelope["researchResults"]))
            or len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes"))):
        raise EvidenceContractError("partial judgment input changed")
    old = deepcopy(envelope)
    old["promptVersion"] = {PROMPT_VERSION: previous.PROMPT_VERSION, REPAIR_VERSION: previous.REPAIR_VERSION,
                            REVIEW_VERSION: previous.REVIEW_VERSION}[version]
    old["prompt"] = previous.review_prompt(packet, envelope["draft"]) if reviewing else previous.prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        old["prompt"] = repair_prompt(old["prompt"], envelope["repair"])
    old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
    validate_execution_input(old)
    return content_hash(envelope)
