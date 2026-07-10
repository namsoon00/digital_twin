import hashlib
import json
from typing import Dict, Iterable, List

from .ontology_rulebox_contracts import GRAPH_REASONER_VERSION, GraphInferenceRule, GraphRuleCondition, GraphRuleDerivation


def rulebox_rules_payload(rules: Iterable[GraphInferenceRule]) -> List[Dict[str, object]]:
    return [rule.to_dict() for rule in rules]


def rulebox_rules_hash(rules_payload: List[Dict[str, object]]) -> str:
    encoded = json.dumps(rules_payload or [], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def rulebox_version_payload(
    rules: Iterable[GraphInferenceRule],
    created_at: str,
    change_reason: str = "",
    author: str = "",
    status: str = "saved",
) -> Dict[str, object]:
    rules_payload = rulebox_rules_payload(rules)
    rules_hash = rulebox_rules_hash(rules_payload)
    rule_count = len(rules_payload)
    condition_count = sum(len(item.get("conditions") or []) for item in rules_payload)
    derivation_count = sum(len(item.get("derivations") or []) for item in rules_payload)
    short_hash = rules_hash[:12]
    time_token = "".join(ch for ch in str(created_at or "") if ch.isalnum())[:24] or short_hash
    return {
        "id": "rulebox-version:" + short_hash + ":" + time_token,
        "label": "RuleBox " + short_hash,
        "versionLabel": short_hash,
        "rulesHash": rules_hash,
        "shortHash": short_hash,
        "ruleCount": rule_count,
        "conditionCount": condition_count,
        "derivationCount": derivation_count,
        "createdAt": created_at,
        "changeReason": str(change_reason or "").strip(),
        "author": str(author or "local-admin").strip() or "local-admin",
        "status": str(status or "saved"),
        "engineVersion": GRAPH_REASONER_VERSION,
        "rulesJson": json.dumps(rules_payload, ensure_ascii=False, sort_keys=True),
    }


def rulebox_governance_candidates(
    rules_payload: List[Dict[str, object]],
    versions: List[Dict[str, object]] = None,
) -> List[Dict[str, object]]:
    rules_payload = list(rules_payload or [])
    versions = list(versions or [])
    rule_ids = {
        str(item.get("rule_id") or item.get("ruleId") or "").strip()
        for item in rules_payload
        if isinstance(item, dict)
    }
    candidates: List[Dict[str, object]] = []

    if not versions:
        candidates.append({
            "id": "governance.baseline-version.v1",
            "title": "현재 RuleBox 기준선 버전 기록",
            "status": "review",
            "priority": 92,
            "source": "rulebox-governance",
            "rationale": "규칙을 운영 자산으로 다루려면 저장 시점, 해시, 변경 이유가 남아야 합니다.",
            "action": "save-current-version",
            "requiresData": [],
            "proposedRule": None,
        })

    if missing_decision_policy_count(rules_payload):
        candidates.append({
            "id": "governance.decision-policy-complete.v1",
            "title": "파생 관계 판단 단계 정책 보강",
            "status": "review",
            "priority": 88,
            "source": "rulebox-governance",
            "rationale": "decisionStage와 stagePriority가 없는 파생 관계는 AI 판단 라우팅이 약해집니다.",
            "action": "complete-decision-policy",
            "requiresData": [],
            "proposedRule": None,
            "affectedDerivationCount": missing_decision_policy_count(rules_payload),
        })

    for candidate in curated_rule_candidates():
        proposed = candidate.get("proposedRule") if isinstance(candidate.get("proposedRule"), dict) else {}
        rule_id = str(proposed.get("rule_id") or proposed.get("ruleId") or "").strip()
        if rule_id and rule_id in rule_ids:
            candidate = dict(candidate)
            candidate["status"] = "covered"
            candidate["rationale"] = "동일 rule_id가 이미 RuleBox에 있습니다."
        candidates.append(candidate)

    return sorted(candidates, key=lambda item: (int(item.get("priority") or 0), str(item.get("id") or "")), reverse=True)


def missing_decision_policy_count(rules_payload: List[Dict[str, object]]) -> int:
    count = 0
    for rule in rules_payload or []:
        if not isinstance(rule, dict):
            continue
        for derivation in rule.get("derivations") or []:
            if not isinstance(derivation, dict):
                continue
            if not (derivation.get("decision_stage") or derivation.get("decisionStage")):
                count += 1
            elif not (derivation.get("stage_priority") or derivation.get("stagePriority")):
                count += 1
    return count


def curated_rule_candidates() -> List[Dict[str, object]]:
    return [
        {
            "id": "candidate.factor-concentration-context.v1",
            "title": "팩터 집중 노출 컨텍스트",
            "status": "candidate",
            "priority": 76,
            "source": "ai-relation-candidate",
            "rationale": "보유 종목이 특정 팩터에 크게 묶이면 가격 신호 하나보다 포트폴리오 노출과 함께 봐야 합니다.",
            "action": "append-disabled-rule",
            "requiresData": ["HAS_FACTOR_EXPOSURE", "positionWeight"],
            "proposedRule": factor_concentration_candidate_rule().to_dict(),
        },
        {
            "id": "candidate.peer-sector-news-context.v1",
            "title": "피어·섹터 뉴스 컨텍스트",
            "status": "candidate",
            "priority": 72,
            "source": "ai-relation-candidate",
            "rationale": "직접 종목 뉴스가 아니어도 피어·섹터 이벤트가 투자 논리의 확인 항목이 될 수 있습니다.",
            "action": "append-disabled-rule",
            "requiresData": ["HAS_EXTERNAL_SIGNAL", "relationScope=peer|sector", "materialityScore"],
            "proposedRule": peer_sector_news_candidate_rule().to_dict(),
        },
        {
            "id": "candidate.data-quality-gate.v1",
            "title": "데이터 품질 게이트 고도화",
            "status": "data-required",
            "priority": 64,
            "source": "ai-relation-candidate",
            "rationale": "데이터 품질 점수는 ABox에 있지만 현재 네이티브 조건 엔진의 target numeric 필터는 valueNumber 중심입니다.",
            "action": "extend-abox-schema",
            "requiresData": ["data-quality.valueNumber 또는 qualityScore 조건 필터"],
            "proposedRule": None,
        },
    ]


def factor_concentration_candidate_rule() -> GraphInferenceRule:
    return GraphInferenceRule(
        rule_id="graph.factor.concentration.context.v1",
        label="보유 종목 + 높은 팩터 노출 -> 팩터 집중 점검",
        version="candidate-v1",
        source_kind="stock",
        action_group="factorRisk",
        action_level="review",
        enabled=False,
        prompt_hint="팩터 노출은 단독 매도 신호가 아니라 같은 방향으로 움직일 수 있는 포트폴리오 리스크로 설명합니다.",
        conditions=[
            GraphRuleCondition(
                "holding-source",
                "subject_property",
                "보유 종목입니다.",
                field="source",
                operator="==",
                value="holding",
            ),
            GraphRuleCondition(
                "factor-exposure",
                "relation",
                "팩터 노출 관계가 포트폴리오 관점에서 의미 있습니다.",
                relation_type="HAS_FACTOR_EXPOSURE",
                target_kind="factor",
                min_weight=0.25,
            ),
        ],
        derivations=[
            GraphRuleDerivation(
                relation_type="REQUIRES_NEXT_CHECK",
                target_kind="next-check",
                target_key="{symbol}:factor-concentration-review",
                target_label="{displayName} 팩터 집중 점검",
                tbox_class="NextCheck",
                tbox_classes=["NextCheck", "ExposureAssessment", "FactorExposure"],
                polarity="context",
                weight=0.68,
                belief_label="팩터 노출이 커서 포트폴리오 리스크를 함께 확인해야 합니다.",
                ai_influence_label="팩터 집중 점검",
                action_group="factorRisk",
                action_level="review",
                decision_stage="FACTOR_CROWDING",
                stage_priority=32,
            )
        ],
    )


def peer_sector_news_candidate_rule() -> GraphInferenceRule:
    return GraphInferenceRule(
        rule_id="graph.news.peer_sector.material_context.v1",
        label="피어·섹터 중요 뉴스 -> 컨텍스트 재확인",
        version="candidate-v1",
        source_kind="stock",
        action_group="alertReview",
        action_level="watch",
        enabled=False,
        prompt_hint="피어·섹터 뉴스는 직접 매수·매도 판단보다 내 종목 투자 논리와 연결되는지 먼저 설명합니다.",
        conditions=[
            GraphRuleCondition(
                "peer-sector-material-event",
                "relation",
                "피어 또는 섹터 범위의 중요 외부 신호입니다.",
                relation_type="HAS_EXTERNAL_SIGNAL",
                target_kind="research-evidence",
                target_property_filters={
                    "relationScope": ["peer", "sector"],
                    "materialityPassed": True,
                    "minMaterialityScore": 65,
                },
                min_weight=0.25,
            )
        ],
        derivations=[
            GraphRuleDerivation(
                relation_type="REQUIRES_NEXT_CHECK",
                target_kind="next-check",
                target_key="{symbol}:peer-sector-news-review",
                target_label="{displayName} 피어·섹터 뉴스 영향 점검",
                tbox_class="NextCheck",
                tbox_classes=["NextCheck", "NewsEvent", "PeerContext"],
                polarity="context",
                weight=0.66,
                belief_label="피어·섹터 뉴스가 투자 논리에 영향을 줄 수 있어 연결성을 확인합니다.",
                ai_influence_label="피어·섹터 뉴스 컨텍스트",
                action_group="alertReview",
                action_level="watch",
                decision_stage="SECTOR_NEWS",
                stage_priority=28,
            )
        ],
    )
