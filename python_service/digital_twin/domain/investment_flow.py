"""Read-side contract for tracing an investment judgement end to end.

The source stores remain authoritative.  This module only normalizes their
identifiers and states so user and operator views describe the same flow.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, is_dataclass
from typing import Dict, Iterable, List, Mapping


INVESTMENT_FLOW_VERSION = "investment-flow-v2"

FLOW_STAGES = (
    ("source", "원천 데이터"),
    ("evidence", "근거"),
    ("relation", "관계"),
    ("hypothesis", "가설"),
    ("inference", "추론"),
    ("decision", "판단"),
)

FLOW_STAGE_LABELS = dict(FLOW_STAGES)
FLOW_STATE_LABELS = {
    "pass": "준비됨",
    "warning": "확인 필요",
    "blocked": "판단 차단",
    "error": "운영 오류",
    "pending": "처리 대기",
}
FLOW_STATE_RANK = {"pass": 0, "pending": 1, "warning": 2, "blocked": 3, "error": 4}


def item_dict(value: object) -> Dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        return dict(value.to_dict() or {})
    if is_dataclass(value):
        return asdict(value)
    return {}


def text(value: object) -> str:
    return str(value or "").strip()


def values(value: object) -> List[object]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def unique_texts(items: Iterable[object], limit: int = 200) -> List[str]:
    result: List[str] = []
    for item in items or []:
        current = text(item)
        if current and current not in result:
            result.append(current)
        if len(result) >= limit:
            break
    return result


def investment_flow_id(account_id: object, symbol: object, episode_id: object) -> str:
    seed = "|".join([text(account_id) or "default", text(symbol).upper(), text(episode_id)])
    return "flow:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def canonical_validation_state(value: object, data_state: object = "") -> str:
    state = text(value).lower().replace("_", "-")
    if state in {"ready", "verified", "valid", "passed", "pass", "sufficient", "approved"}:
        return "pass"
    if state in {"blocked", "invalid", "rejected", "insufficient", "failed"}:
        return "blocked"
    if state in {"error", "unavailable"}:
        return "error"
    if state in {"pending", "queued", "running", "processing"}:
        return "pending"
    if text(data_state).lower() in {"insufficient", "missing", "unavailable"}:
        return "blocked"
    return "warning"


def stage_payload(stage_id: str, state: str, detail: str, **extra) -> Dict[str, object]:
    normalized = state if state in FLOW_STATE_LABELS else "warning"
    return {
        "id": stage_id,
        "label": FLOW_STAGE_LABELS.get(stage_id, stage_id),
        "state": normalized,
        "stateLabel": FLOW_STATE_LABELS[normalized],
        "detail": text(detail),
        **extra,
    }


def episode_hypotheses(episode: Mapping[str, object]) -> List[Dict[str, object]]:
    hypothesis_set = item_dict(episode.get("hypothesisSet") or episode.get("hypothesis_set"))
    return [item_dict(item) for item in hypothesis_set.get("hypotheses") or [] if item_dict(item)]


def hypothesis_id(hypothesis: Mapping[str, object]) -> str:
    return text(
        hypothesis.get("hypothesisId")
        or hypothesis.get("hypothesis_id")
        or hypothesis.get("marketHypothesisId")
        or hypothesis.get("accountHypothesisOverlayId")
    )


def hypothesis_rule_ids(hypotheses: Iterable[Mapping[str, object]]) -> List[str]:
    return unique_texts(
        rule_id
        for hypothesis in hypotheses or []
        for key in ("supportingRuleIds", "counterRuleIds", "sourceRuleIds")
        for rule_id in values(hypothesis.get(key))
    )


def hypothesis_relation_ids(hypotheses: Iterable[Mapping[str, object]]) -> List[str]:
    return unique_texts(
        relation_id
        for hypothesis in hypotheses or []
        for key in (
            "causalPathIds",
            "causal_path_ids",
            "marketRelationTypes",
            "market_relation_types",
            "accountRelationTypes",
            "account_relation_types",
        )
        for relation_id in values(hypothesis.get(key))
    )


def notification_status(job: object) -> str:
    return text(item_dict(job).get("status")).lower()


def notification_items(jobs: Iterable[object]) -> List[Dict[str, object]]:
    result = []
    for job in jobs or []:
        payload = item_dict(job)
        job_id = text(payload.get("jobId") or payload.get("job_id"))
        if not job_id:
            continue
        result.append({
            "id": job_id,
            "jobId": job_id,
            "messageType": text(payload.get("messageType") or payload.get("message_type")),
            "status": text(payload.get("status")),
            "createdAt": text(payload.get("createdAt") or payload.get("created_at")),
            "updatedAt": text(payload.get("updatedAt") or payload.get("updated_at")),
        })
    return result


def delivery_projection(notices: Iterable[Mapping[str, object]]) -> Dict[str, object]:
    rows = [item_dict(item) for item in notices or [] if item_dict(item)]
    states = {notification_status(item) for item in rows}
    if states & {"failed", "error"}:
        state, label = "failed", "전송 실패"
    elif states & {"done", "sent", "delivered"}:
        state, label = "sent", "전송 완료"
    elif states & {"pending", "processing", "awaiting_ai", "queued"}:
        state, label = "pending", "전송 대기"
    elif states & {"suppressed", "skipped", "cancelled", "canceled"}:
        state, label = "suppressed", "정책상 미발송"
    else:
        state, label = "not-required", "알림 조건 없음"
    return {
        "state": state,
        "label": label,
        "expected": bool(rows),
        "count": len(rows),
        "detail": str(len(rows)) + "개 전달 기록" if rows else "의미 있는 변화가 없어 알림을 만들지 않았습니다.",
    }


def decision_flow_projection(episode_value: object, jobs: Iterable[object] = None) -> Dict[str, object]:
    episode = item_dict(episode_value)
    episode_id = text(episode.get("episodeId") or episode.get("episode_id"))
    account_id = text(episode.get("accountId") or episode.get("account_id")) or "default"
    symbol = text(episode.get("symbol")).upper()
    flow_id = investment_flow_id(account_id, symbol, episode_id)
    data_state = text(episode.get("dataState") or episode.get("data_state")) or "partial"
    validation_state = canonical_validation_state(
        episode.get("validationState") or episode.get("validation_state"),
        data_state,
    )
    source_snapshot_id = text(
        episode.get("sourceAboxSnapshotId")
        or episode.get("source_abox_snapshot_id")
        or item_dict(episode.get("factsAtDecision")).get("sourceSnapshotId")
    )
    facts = item_dict(episode.get("factsAtDecision") or episode.get("facts_at_decision"))
    engine_manifest = item_dict(facts.get("engineManifest") or facts.get("engine_manifest"))
    evidence_ids = unique_texts(
        list(values(episode.get("evidenceIds") or episode.get("evidence_ids")))
        + list(values(episode.get("counterEvidenceIds") or episode.get("counter_evidence_ids")))
    )
    hypotheses = episode_hypotheses(episode)
    selected_id = text(episode.get("selectedHypothesisId") or episode.get("selected_hypothesis_id"))
    selected = next((item for item in hypotheses if hypothesis_id(item) == selected_id), {})
    rule_ids = hypothesis_rule_ids(hypotheses)
    relation_ids = hypothesis_relation_ids(hypotheses)
    inference_generation_id = text(
        episode.get("inferenceGenerationId") or episode.get("inference_generation_id")
    )
    abstention = item_dict(episode.get("decisionAbstention") or episode.get("decision_abstention"))
    guardrails = [
        item_dict(item)
        for item in episode.get("decisionGuardrails") or episode.get("decision_guardrails") or []
        if item_dict(item)
    ]
    notices = notification_items(jobs or [])

    source_ok = bool(source_snapshot_id or facts)
    source_state = "pass" if source_ok else (
        "blocked" if data_state in {"insufficient", "missing", "unavailable"} else "warning"
    )
    evidence_state = "pass" if evidence_ids else ("blocked" if validation_state == "blocked" else "warning")
    relation_state = "pass" if relation_ids else ("warning" if inference_generation_id else "blocked")
    hypothesis_state = "pass" if selected_id else ("warning" if hypotheses else "blocked")
    inference_state = "pass" if inference_generation_id else "blocked"
    decision_state = "blocked" if abstention else "pass"
    delivery = delivery_projection(notices)
    assurance = {
        "state": validation_state,
        "label": FLOW_STATE_LABELS[validation_state],
        "detail": text(episode.get("decisionSummary") or episode.get("decision_summary")) or "근거 점검 상태를 확인하세요.",
        "guardrailCount": len(guardrails),
    }
    stages = [
        stage_payload("source", source_state, source_snapshot_id or ("판단 시점 사실이 저장됨" if facts else "판단 시점 원천 스냅샷 연결 필요"), refId=source_snapshot_id),
        stage_payload("evidence", evidence_state, str(len(evidence_ids)) + "개 근거 연결" if evidence_ids else "판단 근거 연결 필요", count=len(evidence_ids)),
        stage_payload("relation", relation_state, str(len(relation_ids)) + "개 관계 경로 · " + str(len(rule_ids)) + "개 규칙" if relation_ids else "실제 관계 경로 연결 필요", count=len(relation_ids), ruleCount=len(rule_ids)),
        stage_payload("hypothesis", hypothesis_state, text(selected.get("claim") or selected.get("label")) or ("후보 가설 " + str(len(hypotheses)) + "개" if hypotheses else "선택 가능한 가설 없음"), count=len(hypotheses), refId=selected_id),
        stage_payload("inference", inference_state, inference_generation_id or "추론 세대 연결 필요", refId=inference_generation_id),
        stage_payload("decision", decision_state, text(episode.get("decisionSummary") or episode.get("decision_summary")) or text(episode.get("action")) or "판단 보류", refId=episode_id),
    ]
    readiness_stages = list(stages) + [{"id": "assurance", "label": "근거 점검", **assurance}]
    worst = max(readiness_stages, key=lambda item: FLOW_STATE_RANK.get(text(item.get("state")), 2))
    blocking = {}
    if worst.get("state") != "pass":
        blocking = next(
            (item for item in readiness_stages if item["state"] in {"error", "blocked"}),
            next(
                (item for item in readiness_stages if item["state"] in {"warning", "pending"}),
                {},
            ),
        )
    next_actions = {
        "source": "최신 데이터와 출처를 확인하세요.",
        "evidence": "지지·반박 근거를 추가로 확인하세요.",
        "relation": "종목 관계와 적용 규칙을 다시 분석하세요.",
        "hypothesis": "경쟁 가설과 반대 근거를 비교하세요.",
        "assurance": "부족한 근거와 차단 조건을 확인하세요.",
        "inference": "TypeDB 추론 세대를 재확인하세요.",
        "decision": "판단 보류 사유와 무효화 조건을 확인하세요.",
    }
    return {
        "version": INVESTMENT_FLOW_VERSION,
        "flowId": flow_id,
        "episodeId": episode_id,
        "accountId": account_id,
        "symbol": symbol,
        "name": text(episode.get("subjectName") or episode.get("subject_name")) or symbol,
        "action": text(episode.get("action")) or "HOLD",
        "reviewLevel": text(episode.get("reviewLevel") or episode.get("review_level")),
        "dataState": data_state,
        "validationState": validation_state,
        "validationLabel": FLOW_STATE_LABELS[validation_state],
        "readinessState": text(worst.get("state")),
        "readinessLabel": FLOW_STATE_LABELS.get(text(worst.get("state")), "확인 필요"),
        "blockingStage": text(blocking.get("id")),
        "blockingStageLabel": text(blocking.get("label")) or "확인 완료",
        "blockingReason": text(blocking.get("detail")) or "판단에 필요한 흐름이 연결되었습니다.",
        "nextAction": next_actions.get(
            text(blocking.get("id")),
            "판단과 무효화 조건의 변화를 계속 관찰하세요.",
        ),
        "decidedAt": text(episode.get("decidedAt") or episode.get("decided_at")),
        "updatedAt": text(episode.get("updatedAt") or episode.get("updated_at") or episode.get("decidedAt") or episode.get("decided_at")),
        "sourceAboxSnapshotId": source_snapshot_id,
        "inferenceGenerationId": inference_generation_id,
        "selectedHypothesisId": selected_id,
        "modelRelease": {
            "deploymentId": text(engine_manifest.get("deploymentId")),
            "releaseFingerprint": text(engine_manifest.get("releaseFingerprint")),
            "reasoningEngineVersion": text(engine_manifest.get("reasoningEngineVersion")),
            "tboxFingerprint": text(engine_manifest.get("tboxFingerprint")),
            "ruleboxFingerprint": text(engine_manifest.get("ruleboxFingerprint")),
            "promptVersion": text(engine_manifest.get("promptVersion")),
            "modelVersion": text(engine_manifest.get("modelVersion")),
        },
        "evidenceIds": evidence_ids,
        "relationIds": relation_ids,
        "ruleIds": rule_ids,
        "hypotheses": hypotheses,
        "guardrails": guardrails,
        "abstention": abstention,
        "notifications": notices,
        "assurance": assurance,
        "delivery": delivery,
        "stages": stages,
        "raw": episode,
    }


def flow_nodes_and_links(projection: Mapping[str, object]) -> Dict[str, object]:
    flow_id = text(projection.get("flowId"))
    nodes = []
    links = []
    prior_id = ""
    for stage in projection.get("stages") or []:
        row = item_dict(stage)
        stage_id = text(row.get("id"))
        node_id = flow_id + ":" + stage_id
        nodes.append({
            "id": node_id,
            "type": stage_id,
            "label": text(row.get("label")),
            "state": text(row.get("state")),
            "detail": text(row.get("detail")),
            "refId": text(row.get("refId")),
        })
        if prior_id:
            links.append({"source": prior_id, "target": node_id, "type": "FLOWS_TO"})
        prior_id = node_id
    return {"nodes": nodes, "links": links}
