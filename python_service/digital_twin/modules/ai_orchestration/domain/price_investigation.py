"""Keep the price question fixed and distinguish unsearched from unsupported."""
from copy import deepcopy

from digital_twin.modules.reasoning.contracts import EvidenceContractError, price_move_anchor
from . import market_explanation, external_influence
from .insight_schema import obj, array, choice

VERSION = "price-move-investigation-v1"


def enabled(packet):
    return packet.get("priceInvestigationPolicy") == VERSION and market_explanation.enabled(packet)


def validate_packet(packet):
    if not enabled(packet) or packet.get("priceMoveAnchor") != price_move_anchor(packet):
        raise EvidenceContractError("price investigation anchor changed")
    scope = packet.get("priceInvestigationScope", {})
    groups = scope.get("candidateGroups", {})
    if (scope.get("version") != "price-investigation-sources-v1" or set(groups) != {"events", "market"}
            or any(not isinstance(ids, list) or len(ids) > 12 or any(not isinstance(key, str) for key in ids)
                   for ids in groups.values())):
        raise EvidenceContractError("price investigation source scope missing")


def coverage(scope, seen, selected):
    return {name: {"status": "no-candidate-in-captured-index" if not ids else
                  "source-read" if set(ids).intersection(seen) else "not-read",
                  "readFactIds": sorted(set(ids).intersection(seen)),
                  "selectedFactIds": sorted(set(ids).intersection(selected))}
            for name, ids in scope["candidateGroups"].items()}


def completion_gaps(scope, seen, selected):
    return [name for name, row in coverage(scope, seen, selected).items()
            if row["status"] != "no-candidate-in-captured-index" and not row["selectedFactIds"]]


def schema(packet):
    quotes = [row["evidenceId"] for row in packet["priceMoveAnchor"]["quotes"] if row["datedReferenceEligible"]]
    return obj({"status": choice(["possible-explanation", "cause-unconfirmed"]),
                "anchorFactId": choice(quotes or ["unavailable-price-anchor"]),
                "sourceFactIds": {**array(choice([f["id"] for f in packet["facts"]])), "maxItems": 4}})


def validate(value, packet, result):
    validate_packet(packet)
    if (not isinstance(value, dict) or set(value) != {"status", "anchorFactId", "sourceFactIds"}
            or value["status"] not in {"possible-explanation", "cause-unconfirmed"}):
        raise ValueError("price explanation requires an explicit investigated outcome")
    quotes = {row["evidenceId"] for row in packet["priceMoveAnchor"]["quotes"] if row["datedReferenceEligible"]}
    if value["anchorFactId"] not in quotes or value["anchorFactId"] not in result["evidenceIds"]:
        raise ValueError("price explanation requires the frozen dated price anchor")
    if not any(ref.get("period") == "current" and ref.get("factId") == value["anchorFactId"]
               and ref.get("field") in {"currentPrice", "changeRate", "priceChangeRate"}
               for ref in result.get("claimEvidence", {}).get("summary", [])):
        raise ValueError("price explanation summary must cite its observed price")
    ids = value["sourceFactIds"]
    if (not isinstance(ids, list) or len(ids) > 4 or any(not isinstance(key, str) for key in ids)
            or len(set(ids)) != len(ids) or set(ids) - set(result["evidenceIds"])):
        raise ValueError("price explanation sources must be selected and cited")
    conditional = {ref["factId"] for row in result.get("externalInfluences", [])
                   if row["assessment"] == "conditional" for ref in row["eventEvidence"]}
    if value["status"] == "possible-explanation" and (not ids or set(ids) - conditional):
        raise ValueError("possible price explanation requires source-backed conditional company influence")
    derived = external_influence.AI_FIELDS | {"analysisSummary", "articleSummaryKo", "aiImpactLabelKo",
        "aiImpactPolarity", "disclosureAnalysis"}
    if value["status"] == "possible-explanation" and any(
            set(ref["field"].split(".")).intersection(derived)
            for row in result.get("externalInfluences", []) for ref in row["eventEvidence"] + row["exposureEvidence"]):
        raise ValueError("price explanation cannot use derived AI summaries as original evidence")
    return deepcopy(value)


def quality_errors(result):
    if not enabled(result["input"]):
        return []
    try:
        packet = result["input"]
        validate(result.get("priceExplanation"), packet, result)
        selected = {row["id"] for row in packet["facts"]}
        if completion_gaps(packet["priceInvestigationScope"], selected, selected):
            raise ValueError("price source survey incomplete")
    except (ValueError, TypeError, KeyError):
        return ["가격 변동의 기준 시세와 사건·시장 원문 조사, 가능한 설명의 근거를 확인해야 합니다."]
    return []


def review_errors(result, review):
    if not enabled(result["input"]):
        return []
    verdict = review.get("priceInvestigation", {}) if isinstance(review, dict) else {}
    if (not isinstance(verdict, dict) or verdict.get("supported") is not True
            or not isinstance(verdict.get("reason"), str) or not 8 <= len(verdict["reason"].strip()) <= 600):
        return ["설명할 시세 시점·사건 조사·대안과 미확인 원인의 구분을 독립 검토하지 못했습니다."]
    return []


READ_INSTRUCTIONS = """가격 변동 조사 우선 계약:
이번 목표는 priceMoveAnchor의 시세 상황과 변동 배경을 설명하는 것입니다. 오래된 재무 설명으로 목표를 바꾸지 마세요.
sourceAsOf와 제공된 changeRate/priceChangeRate를 유지하세요. 비교 시작가·기간이 명시되지 않았으면 알 수 없다고 표시합니다. 발송 시각은 시세 시각이 아닙니다.
시세가 오래됐어도 datedReferenceEligible이면 purpose=source-dated로 그 시점의 가격을 조사할 수 있습니다. current-state나 현재 매매 타이밍으로 확대하지 마세요.
먼저 priceInvestigationScope.candidateGroups의 events와 market에서 각각 관련 후보 한 건을 read_fact로 읽으세요. 이미 current.facts/readResults에서 읽은 것은 반복하지 않습니다. 두 그룹을 첫 read의 최대 두 요청으로 확인하고 필요한 보충 조회 후 마지막 호출에 finish/defer합니다.
후보가 있는 그룹의 원문을 하나도 읽지 않고 재무·이동평균만으로 finish할 수 없습니다. 읽은 대표 근거는 selectedFactIds에 남겨 작성자가 한계도 확인하게 합니다. 원문 열람은 인과관계 확인이나 동일 기간 시장 비교 성공을 뜻하지 않습니다.
기본 선택된 재무 보고서를 이번 설명에 모두 사용할 필요는 없습니다. businessEvidence.reportRequirements의 최신 기간을 충족하지 못하는 과거 보고서는 최신 보고를 읽거나, 이번 설명에 쓰지 않을 경우 selectedFactIds에서 제외하세요. 오래된 보고를 선택해 두고 그로 인해 가격 조사 전체를 보류하지 마세요. 재무 수치를 주장하려면 해당 기간·원천 검증은 여전히 필요합니다.
eventIndex는 최근 일주일의 제한된 동결 자료 탐색이며 등락률 비교 기간이 아닙니다. 목록 부재, 원문 미조회, 원문은 읽었지만 관련 원인을 확인 못함을 구분합니다. 제목은 원문 증거가 아니며 수집 시각을 공개 시각으로 쓰지 않습니다.
sourceContent가 metadata-or-derived-summary이면 read_fact로 얻는 것은 원천의 메타데이터·기존 해석이며 기사 본문이 아닙니다. bodyRead=true도 이번 입력에 본문이 있다는 뜻은 아닙니다. 발급된 원문이 있으면 직접 읽고, 없으면 원인 미확인과 필요한 원문 조사를 기록하세요. 기존 AI 요약을 새 인과 근거로 순환 인용하지 마세요.
동일 원문 중복은 독립 확인이 아닙니다. publishedAfterQuote인 기사는 이후 설명일 뿐 그 당시 알려진 원인으로 소급할 수 없습니다. 공개 시각이 불명확하면 시점 관계도 미확인입니다.
후보를 읽었으나 원인을 특정하지 못해도 유효한 시세가 있으면 조사 한계를 밝히는 설명으로 마칠 수 있습니다. 원인 단정에 필요한 보충 자료는 optionalEvidence, 고정된 시세 관찰 자체가 불가능한 경우만 missingEvidence입니다. 후보를 전혀 읽지 않은 것을 optionalEvidence로 우회하지 마세요.
호출·토큰·질문 수 한도는 그대로입니다. 다음 질문은 중요하고 아직 조사하지 않은 부족분만 기존 질문과 중복 확인 후 제안합니다.
"""

INSTRUCTIONS = """가격 변동 설명의 고정 목표:
summary 첫 문장은 priceMoveAnchor 시점의 상승·하락 또는 가격 상태를 설명합니다. 숫자와 시각의 직접 표기는 기존 근거 표시 규칙을 따르고 claimEvidence.summary에 해당 시세의 currentPrice/changeRate/priceChangeRate를 인용하세요. 이번 변동에 대해 확인한 배경과 확인하지 못한 원인을 구분하고 과거 재무 요약만 쓰고 끝내지 마세요.
priceExplanation에는 anchorFactId, status, sourceFactIds를 기록합니다. status=possible-explanation은 externalInfluences의 conditional 경로가 뒷받침하는 가능한 배경이며 인과 확정이 아닙니다. 해당 사건 원문 ID를 sourceFactIds에 기록합니다. 근거를 연결하지 못하면 cause-unconfirmed이며 이를 본문에 명확히 밝힙니다.
priceInvestigationCoverage는 어떤 동결 후보를 읽었는지만 증명합니다. source-read는 원인 확인이 아닙니다. no-candidate-in-captured-index는 전체 웹에 자료가 없다는 뜻이 아니며 not-read는 조사를 못 마친 상태입니다.
analysisSummary·articleSummaryKo·disclosureAnalysis 같은 기존 AI 해석은 원문 인용을 대체할 수 없습니다. 원문이 입력에 없으면 메타데이터만 확인했다고 밝혀야 하며 필요한 원문을 기존 질문 조사 경로로 요청하세요.
hypothesis에는 사건→기업 노출→가능한 가격 영향과 필요한 전제를, counterEvidence에는 다른 설명·상쇄 요인·동일 기간 시장 비교의 한계를 씁니다. 과거 실적이나 이동평균으로 이번 움직임의 원인을 단정하지 마세요.
source-dated에서도 가격 시점에 대한 질문을 유지합니다. 이후 공개 자료는 이후 보도라고 표시하며 당시 알려진 원인으로 쓰지 마세요. 비교 기준이 없으면 임의 기간 수익률을 만들지 않습니다.
최신 시세는 기존 수집·그래프 반영으로 다음 관찰에서 받습니다. 현재 동결 가격을 새 가격으로 바꾸거나 이전 알림보다 최신이라고 주장하지 마세요.
원인 미확인·같은 자료의 재서술만으로 알림을 제안하지 마세요. 중요한 추가 조사만 기존 questions 한도에서 만들고 이미 진행한 질문을 반복하지 않습니다.
"""
