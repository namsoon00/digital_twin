import hashlib
import json
import re
from typing import Dict, Iterable, List

from .ontology_decision_policy import decision_stage_from_action, relation_stage_priority
from .ontology_rulebox_contracts import GRAPH_REASONER_VERSION, GraphInferenceRule, GraphRuleCondition, GraphRuleDerivation


def rulebox_rules_payload(rules: Iterable[GraphInferenceRule]) -> List[Dict[str, object]]:
    return [rule.to_dict() for rule in rules]


def rulebox_rules_hash(rules_payload: List[Dict[str, object]]) -> str:
    canonical_rules = sorted(
        [canonical_rulebox_rule(item) for item in (rules_payload or []) if isinstance(item, dict)],
        key=lambda item: str(item.get("rule_id") or item.get("ruleId") or ""),
    )
    encoded = json.dumps(canonical_rules, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def canonical_rulebox_rule(rule: Dict[str, object]) -> Dict[str, object]:
    result = canonical_json_value(rule)
    if not isinstance(result, dict):
        return {}
    if isinstance(result.get("conditions"), list):
        result["conditions"] = sorted(
            [canonical_json_value(item) for item in result.get("conditions") if isinstance(item, dict)],
            key=lambda item: str(item.get("condition_id") or item.get("conditionId") or ""),
        )
    if isinstance(result.get("derivations"), list):
        result["derivations"] = sorted(
            [canonical_rulebox_derivation(item, result) for item in result.get("derivations") if isinstance(item, dict)],
            key=lambda item: "|".join([
                str(item.get("relation_type") or item.get("relationType") or ""),
                str(item.get("target_key") or item.get("targetKey") or ""),
                str(item.get("target_kind") or item.get("targetKind") or ""),
                str(item.get("action_group") or item.get("actionGroup") or ""),
                str(item.get("action_level") or item.get("actionLevel") or ""),
            ]),
        )
    return result


def canonical_rulebox_derivation(derivation: Dict[str, object], rule: Dict[str, object] = None) -> Dict[str, object]:
    result = canonical_json_value(derivation)
    if not isinstance(result, dict):
        return {}
    rule = rule if isinstance(rule, dict) else {}
    action_group = str(result.get("action_group") or result.get("actionGroup") or rule.get("action_group") or rule.get("actionGroup") or "")
    action_level = str(result.get("action_level") or result.get("actionLevel") or rule.get("action_level") or rule.get("actionLevel") or "")
    if action_group:
        if "action_group" in result or "actionGroup" not in result:
            result["action_group"] = action_group
        else:
            result["actionGroup"] = action_group
    if action_level:
        if "action_level" in result or "actionLevel" not in result:
            result["action_level"] = action_level
        else:
            result["actionLevel"] = action_level
    decision_stage = str(result.get("decision_stage") or result.get("decisionStage") or "").strip()
    if not decision_stage:
        decision_stage = decision_stage_from_action(action_group, action_level)
    if decision_stage:
        if "decision_stage" in result or "decisionStage" not in result:
            result["decision_stage"] = decision_stage
        else:
            result["decisionStage"] = decision_stage
    stage_priority = result.get("stage_priority") if "stage_priority" in result else result.get("stagePriority")
    if not float_or_zero(stage_priority):
        stage_priority = relation_stage_priority({
            "decisionStage": decision_stage,
            "actionGroup": action_group,
            "actionLevel": action_level,
            "riskImpact": result.get("risk_impact") or result.get("riskImpact"),
            "supportImpact": result.get("support_impact") or result.get("supportImpact"),
        })
    if "stage_priority" in result or "stagePriority" not in result:
        result["stage_priority"] = canonical_json_value(stage_priority)
    else:
        result["stagePriority"] = canonical_json_value(stage_priority)
    return result


def canonical_json_value(value: object) -> object:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return round(value, 10)
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        return [canonical_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): canonical_json_value(value[key]) for key in sorted(value.keys(), key=str)}
    return value


def float_or_zero(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


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
    persisted_candidates: List[Dict[str, object]] = None,
) -> List[Dict[str, object]]:
    rules_payload = list(rules_payload or [])
    versions = list(versions or [])
    persisted_candidates = list(persisted_candidates or [])
    rule_ids = {
        str(item.get("rule_id") or item.get("ruleId") or "").strip()
        for item in rules_payload
        if isinstance(item, dict)
    }
    candidates: List[Dict[str, object]] = []

    for candidate in persisted_candidates:
        normalized = normalize_rule_change_candidate(candidate, existing_rule_ids=rule_ids)
        if normalized:
            candidates.append(normalized)

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

    return sorted(deduplicate_candidates(candidates), key=lambda item: (int(item.get("priority") or 0), str(item.get("id") or "")), reverse=True)


def deduplicate_candidates(candidates: List[Dict[str, object]]) -> List[Dict[str, object]]:
    result: List[Dict[str, object]] = []
    seen = set()
    for candidate in candidates or []:
        candidate_id = str(candidate.get("id") or "").strip()
        proposed = candidate.get("proposedRule") if isinstance(candidate.get("proposedRule"), dict) else {}
        proposed_id = str(proposed.get("rule_id") or proposed.get("ruleId") or "").strip()
        key = candidate_id or proposed_id or str(candidate.get("title") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(candidate)
    return result


def build_rule_change_candidate_prompt(context: Dict[str, object]) -> str:
    payload = compact_candidate_context(context or {})
    contract = {
        "candidates": [
            {
                "title": "string",
                "rationale": "why this ontology relation is useful",
                "expectedEffect": "how this changes AI opinions or alert quality",
                "risk": "false positive or data risk",
                "requiresData": ["existing ABox relation or missing data"],
                "priority": 0,
                "proposedRule": {
                    "rule_id": "graph.example.context.v1",
                    "label": "Korean label",
                    "version": "ai-candidate-v1",
                    "source_kind": "stock",
                    "enabled": False,
                    "action_group": "alertReview",
                    "action_level": "watch",
                    "prompt_hint": "Korean prompt hint",
                    "conditions": [],
                    "derivations": [],
                },
            }
        ]
    }
    return "\n".join([
        "너는 자동매매 시스템이 아니라 투자 온톨로지 RuleBox 설계 리뷰어다.",
        "목표: 현재 TypeDB RuleBox, InferenceBox, 최근 데이터 변경, 알림 근거를 보고 새로운 RuleChangeCandidate만 제안한다.",
        "제약:",
        "- 매수/매도 지시를 만들지 말고 관계 후보만 제안한다.",
        "- proposedRule.enabled는 반드시 false다.",
        "- ABox에 없는 데이터가 필요하면 proposedRule을 비우고 requiresData에 적는다.",
        "- relation_type, condition field, target filters는 제공된 RuleBox/InferenceBox/TBox에서 확인 가능한 형태를 우선 사용한다.",
        "- derivations에는 decision_stage와 stage_priority를 포함한다.",
        "- 중복 rule_id를 만들지 않는다.",
        "- 응답은 설명 없이 JSON 하나만 반환한다.",
        "JSON 계약:",
        json.dumps(contract, ensure_ascii=False, indent=2),
        "입력 컨텍스트:",
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
    ])


def compact_candidate_context(context: Dict[str, object]) -> Dict[str, object]:
    rulebox = context.get("ruleBox") if isinstance(context.get("ruleBox"), dict) else {}
    inferencebox = context.get("inferenceBox") if isinstance(context.get("inferenceBox"), dict) else {}
    return {
        "trigger": context.get("trigger") or "manual",
        "symbols": list(context.get("symbols") or [])[:30],
        "ruleBox": {
            "ruleCount": rulebox.get("ruleCount"),
            "relationTypes": list(rulebox.get("relationTypes") or [])[:40],
            "rules": [
                {
                    "rule_id": item.get("rule_id") or item.get("ruleId"),
                    "label": item.get("label"),
                    "enabled": item.get("enabled"),
                    "action_group": item.get("action_group") or item.get("actionGroup"),
                    "action_level": item.get("action_level") or item.get("actionLevel"),
                    "conditionCount": len(item.get("conditions") or []),
                    "derivationTypes": [
                        derivation.get("relation_type") or derivation.get("relationType")
                        for derivation in (item.get("derivations") or [])[:4]
                    ],
                }
                for item in list(rulebox.get("rules") or [])[:30]
                if isinstance(item, dict)
            ],
        },
        "inferenceBox": {
            "status": inferencebox.get("status"),
            "relationCount": inferencebox.get("relationCount"),
            "relations": [
                {
                    "type": item.get("type"),
                    "ruleId": item.get("ruleId"),
                    "polarity": item.get("polarity"),
                    "decisionStage": item.get("decisionStage"),
                    "stagePriority": item.get("stagePriority"),
                    "label": item.get("label") or item.get("aiInfluenceLabel"),
                }
                for item in list(inferencebox.get("relations") or [])[:40]
                if isinstance(item, dict)
            ],
        },
        "recentEvents": list(context.get("recentEvents") or [])[:20],
        "alerts": list(context.get("alerts") or [])[:20],
        "materialityAssessments": list(context.get("materialityAssessments") or [])[:20],
        "existingCandidates": [
            {"id": item.get("id"), "status": item.get("status"), "title": item.get("title")}
            for item in list(rulebox.get("changeCandidates") or [])[:20]
            if isinstance(item, dict)
        ],
    }


def rule_change_candidates_from_text(text: str, context: Dict[str, object] = None) -> List[Dict[str, object]]:
    payload = json_object_from_text(text)
    raw_candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    rulebox = (context or {}).get("ruleBox") if isinstance((context or {}).get("ruleBox"), dict) else {}
    existing_rule_ids = {
        str(item.get("rule_id") or item.get("ruleId") or "").strip()
        for item in (rulebox.get("rules") or [])
        if isinstance(item, dict)
    }
    candidates = [
        normalize_rule_change_candidate(item, existing_rule_ids=existing_rule_ids, source="ai-rule-candidate")
        for item in raw_candidates
        if isinstance(item, dict)
    ]
    return [item for item in candidates if item]


def json_object_from_text(text: str) -> Dict[str, object]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            decoded = json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return {}
    return decoded if isinstance(decoded, dict) else {}


def normalize_rule_change_candidate(
    candidate: Dict[str, object],
    existing_rule_ids: Iterable[str] = None,
    source: str = "",
) -> Dict[str, object]:
    candidate = dict(candidate or {})
    existing = {str(item or "").strip() for item in (existing_rule_ids or []) if str(item or "").strip()}
    proposed = candidate.get("proposedRule") if isinstance(candidate.get("proposedRule"), dict) else None
    warnings: List[str] = [str(item) for item in (candidate.get("validationWarnings") or []) if str(item or "").strip()]
    normalized_rule = None
    proposed_rule_id = ""
    if proposed:
        proposed = dict(proposed)
        proposed["enabled"] = False
        proposed.setdefault("version", "ai-candidate-v1")
        proposed.setdefault("source_kind", "ai")
        proposed_rule_id = str(proposed.get("rule_id") or proposed.get("ruleId") or "").strip()
        try:
            normalized_rule = GraphInferenceRule.from_dict(proposed).to_dict()
            normalized_rule["enabled"] = False
        except ValueError as error:
            warnings.append("proposedRule invalid: " + str(error))
            normalized_rule = None
    status = str(candidate.get("status") or "").strip() or ("candidate" if normalized_rule else "data-required")
    if proposed_rule_id and proposed_rule_id in existing:
        status = "covered"
        warnings.append("same rule_id already exists in RuleBox")
    payload = {
        "id": str(candidate.get("id") or "") or candidate_id(candidate, normalized_rule),
        "title": str(candidate.get("title") or (normalized_rule or {}).get("label") or "AI 관계 후보"),
        "status": status,
        "priority": int(float(candidate.get("priority") or (74 if normalized_rule else 58))),
        "source": str(source or candidate.get("source") or "ai-rule-candidate"),
        "rationale": str(candidate.get("rationale") or ""),
        "expectedEffect": str(candidate.get("expectedEffect") or ""),
        "risk": str(candidate.get("risk") or ""),
        "action": str(candidate.get("action") or ("append-disabled-rule" if normalized_rule else "data-required")),
        "requiresData": [str(item) for item in (candidate.get("requiresData") or []) if str(item or "").strip()],
        "proposedRule": normalized_rule,
        "validationWarnings": dedupe_strings(warnings),
    }
    if not payload["id"].startswith(("candidate.", "governance.", "ai-candidate:")):
        payload["id"] = "ai-candidate:" + payload["id"]
    return payload


def candidate_id(candidate: Dict[str, object], proposed_rule: Dict[str, object] = None) -> str:
    basis = {
        "title": candidate.get("title"),
        "rationale": candidate.get("rationale"),
        "ruleId": (proposed_rule or {}).get("rule_id") or (proposed_rule or {}).get("ruleId"),
    }
    encoded = json.dumps(basis, ensure_ascii=False, sort_keys=True)
    return "ai-candidate:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def dedupe_strings(values: Iterable[str]) -> List[str]:
    result = []
    seen = set()
    for value in values or []:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


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
        rule_id="graph.factor.position_crowding.v1",
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
