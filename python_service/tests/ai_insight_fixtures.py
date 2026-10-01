"""Synthetic, credential-free independent observation examples."""
from digital_twin.modules.ai_orchestration.domain.planning import stamp, identity, validate_plan
from digital_twin.modules.ai_orchestration.domain.insight_quality import accept_review, REVIEW_VERSION
from digital_twin.modules.ai_orchestration.domain.insight_contract import INSIGHT_VERSION, SECTIONS
from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence
from digital_twin.modules.reasoning.domain.observation_evidence import EVIDENCE_PROTOCOL, EVIDENCE_PROFILE


def ref(field, period="current", fact_id="quote-1"):
    return {"factId": fact_id, "field": field, "period": period}


def packet():
    now = stamp()
    subject = {"accountId": "control-test", "symbol": "TEST", "name": "Test", "worldId": "portfolio:local:control-test"}
    facts, coverage = select_evidence([{
        "id": "quote-1", "kind": "stock", "symbol": "TEST", "currentPrice": 100, "changeRate": -1.2,
        "currency": "KRW", "quantity": 2, "averagePrice": 110, "profitLossRate": -9.09, "positionWeight": 10,
        "ma5": 98, "ma20": 108, "ma60": 90, "ma20Slope": -0.2, "ma60Slope": 0.5,
        "volume": 300, "volumeRatio": 0.001, "foreignNetVolume": -30,
        "source": "holding", "observationSource": "Example exchange", "sourceAsOf": now,
        "freshnessStatus": "fresh", "dataState": "partial", "judgementEvidenceUsable": True,
        "sourceEntityId": "quote", "sourceWorldId": subject["worldId"], "sourceSnapshotId": "snapshot-1",
    }])
    return {**subject, "protocolVersion": EVIDENCE_PROTOCOL, "profile": EVIDENCE_PROFILE,
            "sourceSnapshotId": "snapshot-1", "sourceSnapshots": {subject["worldId"]: "snapshot-1"},
            "capturedAt": now, "facts": facts, "coverage": coverage, "lastDeliveredNotification": {}}


def plan():
    return {
        "insightVersion": INSIGHT_VERSION,
        "summary": "단기 회복과 중기 약세가 엇갈려, 가격 반등만으로 기존 약세 설명을 바꾸기는 이릅니다.",
        "comparison": "첫 관찰이므로 이전 설명의 적중 여부를 평가할 수 없습니다.",
        "hypothesis": "중기 약세 안의 단기 반등일 수 있습니다. 중기 평균 가격을 회복하면 이 설명은 약해집니다.",
        "portfolioImpact": "매입가보다 낮은 가격에서 보유 중이므로, 반등이 손실 회복으로 이어지는지를 구분해 볼 의미가 있습니다.",
        "counterEvidence": "단기 평균 가격을 회복한 점은 약세 지속 설명에 반대되는 관측입니다.",
        "notification": {"send": True, "reason": "단기 회복과 중기 흐름이 엇갈려 보유 중인 손실의 회복 조건을 구분할 시점입니다."},
        "claimEvidence": {
            "summary": [ref("currentPrice"), ref("ma5"), ref("ma20")],
            "comparison": [ref("currentPrice")],
            "hypothesis": [ref("currentPrice"), ref("ma20")],
            "portfolioImpact": [ref("currentPrice"), ref("averagePrice"), ref("quantity")],
            "counterEvidence": [ref("currentPrice"), ref("ma5")],
            "notificationReason": [ref("currentPrice"), ref("ma5"), ref("ma20"), ref("averagePrice")],
        },
        "observations": [{"left": ref("currentPrice"), "operator": "gt", "right": ref("ma5")},
                         {"left": ref("currentPrice"), "operator": "lt", "right": ref("ma20")}],
        "followUpConditions": [{"description": "중기 평균 가격 회복으로 약세 설명이 약해지는지 확인합니다.",
            "left": ref("currentPrice"), "operator": "gt", "right": ref("ma20"), "effect": "weakens", "horizonMinutes": 1440}],
        "evidenceIds": ["quote-1"], "questions": [], "nextCheckMinutes": 180,
    }


def review():
    return {"version": REVIEW_VERSION, "sections": {key: {"supported": True, "reason": "합성 근거의 항목과 설명이 일치합니다."} for key in SECTIONS},
            "novelty": "new-meaning", "usefulness": "decision-context", "reason": "단기 회복과 보유 상황의 의미를 설명합니다."}


def observation():
    evidence = packet()
    result = {**validate_plan(plan(), evidence), "input": evidence, "observedAt": stamp(), "inputFingerprint": identity(evidence["facts"])}
    result["quality"] = accept_review(result, review(), "synthetic-review")
    return result


def persist_review(store, task, result):
    from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_review_input
    result["input"]["taskId"] = task["taskId"]
    envelope = freeze_review_input(result)
    input_id = store.save_execution_input(task, envelope)
    call_id = store.begin_call("independent-observation", envelope["promptHash"], task["taskId"], input_id)
    store.finish_call(call_id)
    result["quality"] = accept_review(result, review(), input_id)
