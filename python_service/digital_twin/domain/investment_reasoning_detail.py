"""Snapshot-bound detail for explaining one investment reasoning episode.

The live relation context is large and mutable.  This module freezes only the
facts, TypeDB relations, rule matches, and grounded conditions that participated
in a decision so the UI can explain the historical judgement without querying
the current graph generation.
"""

from __future__ import annotations

import json
import hashlib
import math
import re
from typing import Dict, Iterable, List, Mapping, Optional


REASONING_DETAIL_VERSION = "investment-reasoning-detail-v2"
SUPPORTED_REASONING_DETAIL_VERSIONS = {
    "investment-reasoning-detail-v1",
    REASONING_DETAIL_VERSION,
}

FACT_LABELS = {
    "currentPrice": "현재가",
    "averagePrice": "평균매입가",
    "profitLossRate": "보유 수익률",
    "profitLossRateDeltaPct": "수익률 변화",
    "quantity": "보유 수량",
    "positionWeight": "종목 비중",
    "positionAccountWeight": "계좌 내 종목 비중",
    "priceChangeRate": "가격 변화율",
    "ma5": "5일 평균",
    "ma5Distance": "5일 평균 괴리",
    "ma20": "20일 평균",
    "ma20Distance": "20일 평균 괴리",
    "ma20Slope": "20일 평균 기울기",
    "ma60": "60일 평균",
    "ma60Distance": "60일 평균 괴리",
    "ma60Slope": "60일 평균 기울기",
    "trendCurve": "추세 곡률",
    "volume": "거래량",
    "volumeRatio": "평균 대비 거래량",
    "timeAdjustedVolumeRatio": "시간 보정 거래량",
    "tradeStrength": "체결강도",
    "buyVolume": "매수 체결량",
    "sellVolume": "매도 체결량",
    "bidAskImbalance": "호가 불균형",
    "foreignNetVolume": "외국인 순매수",
    "institutionNetVolume": "기관 순매수",
    "jointSmartMoneyInflow": "외국인·기관 합산 순매수",
    "usdKrwRate": "원·달러 환율",
    "macroDgs10": "미국 10년 금리",
    "macroDgs2": "미국 2년 금리",
    "btcPrice": "비트코인 가격",
    "btcChange24h": "비트코인 24시간 변화",
    "directNewsCount": "직접 관련 뉴스 수",
    "directRiskNewsCount": "직접 위험 뉴스 수",
    "marketValue": "종목 평가금액",
}

CORE_FACT_KEYS = tuple(FACT_LABELS)

RELATION_LABELS = {
    "HAS_INFERRED_RISK": "위험 관계",
    "HAS_INFERRED_SUPPORT": "우호 관계",
    "HAS_INFERENCE_TRACE": "추론 근거 연결",
    "HAS_SHARED_MARKET_PREMISE": "공통 시장 전제 연결",
    "HAS_TEMPORAL_WINDOW": "기간 관측 연결",
    "HAS_CAPITAL_FLOW_WINDOW": "외국인·기관 자금 흐름 연결",
    "BREAKS_LEVEL": "기준 가격 이탈",
    "REQUIRES_NEXT_CHECK": "다음 확인 필요",
}


def _mapping(value: object) -> Dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: object) -> List[object]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value] if value not in (None, "") else []


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _identity(item: Mapping[str, object], *keys: str) -> str:
    return next((_text(item.get(key)) for key in keys if _text(item.get(key))), "")


def _unique(values: Iterable[object], limit: int = 100) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        clean = _text(value)
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
        if len(result) >= limit:
            break
    return result


def _present(value: object) -> bool:
    if value in (None, "", [], {}):
        return False
    return not isinstance(value, float) or math.isfinite(value)


def _safe_value(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {
            str(key): _safe_value(current)
            for key, current in value.items()
            if _present(current)
        }
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(current) for current in value if _present(current)]
    return value


def _human_identifier(value: object) -> str:
    clean = _text(value)
    if not clean:
        return ""
    tail = clean.split(":")[-1]
    words = [item for item in re.split(r"[._-]+", tail) if item and item.lower() not in {"graph", "v1", "v2", "v3"}]
    return " ".join(words) if words else clean


def _relation_type_label(value: object) -> str:
    clean = _text(value).upper()
    return RELATION_LABELS.get(clean) or _human_identifier(clean) or "TypeDB 관계"


def _expected_text(condition: Mapping[str, object]) -> str:
    operator = _text(condition.get("operator"))
    expected = condition.get("expectedValue")
    if expected in (None, ""):
        expected = condition.get("value")
    if expected in (None, ""):
        expected = condition.get("threshold")
    if expected in (None, ""):
        expected = condition.get("targetPropertyFilters")
    if expected in (None, "", {}):
        return operator
    rendered = json.dumps(_safe_value(expected), ensure_ascii=False, sort_keys=True) if isinstance(expected, (dict, list)) else _text(expected)
    return " ".join(item for item in (operator, rendered) if item)


def _condition_rows(trace: Mapping[str, object], facts: Mapping[str, object]) -> List[Dict[str, object]]:
    result: List[Dict[str, object]] = []
    seen = set()
    raw_rows = []
    for key in ("matchedConditions", "conditionMatches", "ruleConditionShapes"):
        current = trace.get(key)
        raw_rows.extend(list(current.values()) if isinstance(current, Mapping) else _rows(current))
    rule_id = _identity(trace, "ruleId", "rule_id", "sourceRuleId")
    trace_id = _identity(trace, "id", "inferenceTraceId", "traceId")
    for index, value in enumerate(raw_rows):
        item = _mapping(value)
        if not item:
            item = {"conditionId": _text(value)}
        condition_id = _identity(item, "conditionId", "condition_id", "id") or f"condition:{index + 1}"
        field = _identity(item, "field", "property", "sourceField")
        relation_type = _identity(item, "relationType", "relation_type")
        key = (condition_id, field, relation_type)
        if key in seen:
            continue
        seen.add(key)
        observed = item.get("observedValue")
        if observed in (None, ""):
            observed = item.get("matchedValue")
        if observed in (None, "") and field:
            observed = facts.get(field)
        source_properties = _mapping(item.get("matchedSourceProperties") or item.get("sourceProperties"))
        target_properties = _mapping(item.get("matchedTargetProperties") or item.get("targetProperties"))
        label = _text(item.get("label") or item.get("conditionLabel"))
        if not label:
            label = FACT_LABELS.get(field) or _relation_type_label(relation_type) or _human_identifier(condition_id)
        result.append({
            "id": condition_id,
            "label": label or "성립 조건",
            "kind": _text(item.get("kind")) or ("relation" if relation_type else "fact"),
            "field": field,
            "relationType": relation_type,
            "role": _text(item.get("role")) or "required",
            "observedValue": _safe_value(observed),
            "expected": _expected_text(item),
            "source": _text(item.get("provider") or item.get("source")),
            "sourceUrl": _text(item.get("sourceUrl") or item.get("url")),
            "evidenceId": _text(item.get("evidenceId") or item.get("evidence_id")),
            "asOf": _text(item.get("observedAt") or item.get("sourceAsOf") or item.get("asOf")),
            "dataState": _text(item.get("dataState")),
            "freshnessStatus": _text(item.get("freshnessStatus")),
            "relationId": _text(item.get("relationId")),
            "sourceProperties": _safe_value(source_properties),
            "targetProperties": _safe_value(target_properties),
            "ruleIds": [rule_id] if rule_id else [],
            "traceIds": [trace_id] if trace_id else [],
        })
    return result


def _active_rule_rows(relation: Mapping[str, object]) -> List[Dict[str, object]]:
    result = []
    seen = set()
    for value in list(_rows(relation.get("activeRules"))) + list(_rows(relation.get("matchedRules"))):
        item = _mapping(value)
        rule_id = _identity(item, "ruleId", "rule_id", "sourceRuleId")
        if not rule_id or rule_id in seen:
            continue
        seen.add(rule_id)
        result.append({
            "id": rule_id,
            "label": _text(item.get("label") or item.get("name")) or _human_identifier(rule_id),
            "description": _text(item.get("description") or item.get("promptHint") or item.get("prompt_hint")),
            "evidenceRole": _text(item.get("evidenceRole") or item.get("polarity")) or "context",
            "reviewLevel": _text(item.get("reviewLabel") or item.get("reviewLevel") or item.get("review_level")),
            "dataState": _text(item.get("dataStateLabel") or item.get("dataState") or item.get("data_state")),
            "decisionStage": _text(item.get("decisionStage") or item.get("decision_stage")),
            "candidateAction": _text(item.get("candidateAction") or item.get("candidate_action") or item.get("primaryAction")),
            "referenceOnly": bool(item.get("referenceOnly") or item.get("reference_only")),
            "evidenceUsable": item.get("evidenceUsableForJudgement") is not False,
            "evidence": _unique(_rows(item.get("evidence")), 20),
            "traceIds": _unique(_rows(item.get("inferenceTraceId") or item.get("inference_trace_id")), 20),
            "relationIds": [],
            "conditions": [],
            "knowledgeBasis": _safe_value(_mapping(item.get("knowledgeBasis") or item.get("knowledge_basis"))),
        })
    return result


def _graph_rows(relation: Mapping[str, object]) -> tuple:
    graph = _mapping(relation.get("graphStoreInference"))
    typedb = _mapping(relation.get("typedbInference"))
    relations = graph.get("relations") or typedb.get("relations") or []
    traces = graph.get("traces") or typedb.get("traces") or []
    return [_mapping(item) for item in relations if isinstance(item, Mapping)], [_mapping(item) for item in traces if isinstance(item, Mapping)]


def _hypothesis_rows(hypothesis_set: Mapping[str, object], selected_id: str = "") -> List[Dict[str, object]]:
    rows = []
    for value in hypothesis_set.get("hypotheses") or []:
        item = _mapping(value)
        hypothesis_id = _identity(item, "hypothesisId", "hypothesis_id", "id")
        if not hypothesis_id:
            continue
        rows.append({
            "id": hypothesis_id,
            "label": _text(item.get("templateLabel") or item.get("template_label")) or "투자 가설",
            "claim": _text(item.get("claim")),
            "stance": _text(item.get("stance")) or "context",
            "horizon": _text(item.get("horizon")) or "multi-horizon",
            "state": _text(item.get("evidenceState") or item.get("evidence_state")) or "unresolved",
            "stateLabel": _text(item.get("evidenceStateLabel") or item.get("evidence_state_label")) or "확인 중",
            "selected": bool(selected_id and hypothesis_id == selected_id),
            "candidateAction": _text(item.get("candidateAction") or item.get("candidate_action")),
            "supportingRuleIds": _unique(_rows(item.get("supportingRuleIds") or item.get("supporting_rule_ids")), 50),
            "counterRuleIds": _unique(_rows(item.get("counterRuleIds") or item.get("counter_rule_ids")), 50),
            "supportingEvidenceIds": _unique(_rows(item.get("supportingEvidenceIds") or item.get("supporting_evidence_ids")), 100),
            "counterEvidenceIds": _unique(_rows(item.get("counterEvidenceIds") or item.get("counter_evidence_ids")), 100),
            "traceIds": _unique(_rows(item.get("causalPathIds") or item.get("causal_path_ids")), 50),
            "assumptions": _unique(_rows(item.get("assumptions")), 20),
            "invalidationConditions": _unique(_rows(item.get("invalidationConditions") or item.get("invalidation_conditions")), 20),
        })
    return rows


def reasoning_detail_snapshot(
    relation_context: Mapping[str, object],
    hypothesis_set: Optional[Mapping[str, object]] = None,
    validated_response: Optional[Mapping[str, object]] = None,
) -> Dict[str, object]:
    """Freeze the decision-relevant subset of one TypeDB relation context."""

    relation = _mapping(relation_context)
    facts = _mapping(relation.get("facts"))
    response = _mapping(validated_response)
    hypotheses = _mapping(hypothesis_set)
    selected_id = _text(response.get("selectedHypothesisId") or response.get("selected_hypothesis_id"))
    hypothesis_rows = _hypothesis_rows(hypotheses, selected_id)
    relevant_rule_ids = set(
        rule_id
        for item in hypothesis_rows
        for rule_id in list(item.get("supportingRuleIds") or []) + list(item.get("counterRuleIds") or [])
    )
    graph_relations, graph_traces = _graph_rows(relation)
    if relevant_rule_ids:
        graph_relations = [item for item in graph_relations if _identity(item, "ruleId", "rule_id", "sourceRuleId") in relevant_rule_ids]
        graph_traces = [item for item in graph_traces if _identity(item, "ruleId", "rule_id", "sourceRuleId") in relevant_rule_ids]

    rules = _active_rule_rows(relation)
    if relevant_rule_ids:
        rules = [item for item in rules if item["id"] in relevant_rule_ids]
    known_rule_ids = {item["id"] for item in rules}
    for item in hypothesis_rows:
        for rule_id in list(item.get("supportingRuleIds") or []) + list(item.get("counterRuleIds") or []):
            if rule_id in known_rule_ids:
                continue
            known_rule_ids.add(rule_id)
            rules.append({
                "id": rule_id,
                "label": item["label"] if rule_id in item.get("supportingRuleIds", []) else _human_identifier(rule_id),
                "description": item.get("claim") or "",
                "evidenceRole": "support" if rule_id in item.get("supportingRuleIds", []) else "counter",
                "reviewLevel": "",
                "dataState": "",
                "decisionStage": "",
                "candidateAction": item.get("candidateAction") or "",
                "referenceOnly": False,
                "evidenceUsable": True,
                "evidence": [],
                "traceIds": [],
                "relationIds": [],
                "conditions": [],
                "knowledgeBasis": {},
            })

    traces = []
    fact_rows = []
    for trace in graph_traces:
        trace_id = _identity(trace, "id", "inferenceTraceId", "traceId")
        rule_id = _identity(trace, "ruleId", "rule_id", "sourceRuleId")
        conditions = _condition_rows(trace, facts)
        fact_rows.extend(conditions)
        traces.append({
            "id": trace_id,
            "ruleId": rule_id,
            "label": _text(trace.get("label") or trace.get("ruleLabel")) or _human_identifier(rule_id),
            "matched": True,
            "dataState": _text(trace.get("dataState")),
            "freshnessStatus": _text(trace.get("freshnessStatus")),
            "evidenceUsable": trace.get("evidenceUsableForJudgement") is not False,
            "matchedConditionIds": _unique(_rows(trace.get("matchedConditionIds")), 100),
            "evidenceRelationIds": _unique(_rows(trace.get("evidenceRelationIds")), 100),
            "conditions": conditions,
        })

    relation_rows = []
    for item in graph_relations:
        relation_id = _identity(item, "id", "relationId", "relation_id")
        relation_type = _identity(item, "type", "relationType", "relation_type")
        rule_id = _identity(item, "ruleId", "rule_id", "sourceRuleId")
        relation_rows.append({
            "id": relation_id,
            "type": relation_type,
            "label": _text(item.get("label") or item.get("targetLabel") or item.get("aiInfluenceLabel")) or _relation_type_label(relation_type),
            "source": _text(item.get("source")),
            "sourceLabel": _text(item.get("sourceLabel")),
            "target": _text(item.get("target")),
            "targetLabel": _text(item.get("targetLabel")),
            "ruleId": rule_id,
            "polarity": _text(item.get("polarity") or item.get("evidenceRole")) or "context",
            "decisionStage": _text(item.get("decisionStage")),
            "actionGroup": _text(item.get("actionGroup")),
            "reviewLevel": _text(item.get("reviewLevel")),
            "dataState": _text(item.get("dataState")),
            "freshnessStatus": _text(item.get("freshnessStatus")),
            "evidenceUsable": item.get("evidenceUsableForJudgement") is not False,
            "referenceOnly": bool(item.get("referenceOnly") or item.get("reference_only")),
        })

    rule_by_id = {item["id"]: item for item in rules}
    for trace in traces:
        rule = rule_by_id.get(trace.get("ruleId"))
        if not rule:
            continue
        rule["traceIds"] = _unique(list(rule.get("traceIds") or []) + [trace.get("id")], 100)
        rule["conditions"] = list(rule.get("conditions") or []) + list(trace.get("conditions") or [])
    for item in relation_rows:
        rule = rule_by_id.get(item.get("ruleId"))
        if rule:
            rule["relationIds"] = _unique(list(rule.get("relationIds") or []) + [item.get("id")], 100)

    if not fact_rows:
        for key in CORE_FACT_KEYS:
            if not _present(facts.get(key)):
                continue
            fact_rows.append({
                "id": "fact:" + key,
                "label": FACT_LABELS[key],
                "kind": "fact",
                "field": key,
                "relationType": "",
                "role": "context",
                "observedValue": _safe_value(facts.get(key)),
                "expected": "",
                "source": _text(facts.get("quoteSource") or facts.get("source")),
                "asOf": _text(facts.get("updatedAt") or facts.get("observedAt")),
                "dataState": _text(facts.get("dataState")),
                "freshnessStatus": "",
                "relationId": "",
                "sourceProperties": {},
                "targetProperties": {},
                "ruleIds": list(relevant_rule_ids),
                "traceIds": [],
            })

    snapshot = {
        "version": REASONING_DETAIL_VERSION,
        "snapshotState": "exact",
        "snapshotStateLabel": "판단 당시 추론 상세",
        "snapshotReason": "판단 생성 시점의 TypeDB 관계 컨텍스트에서 고정 저장했습니다.",
        "recordCompleteness": "exact",
        "limitations": [],
        "sourceAboxSnapshotId": _text(relation.get("sourceAboxSnapshotId")),
        "inferenceGenerationId": _text(relation.get("inferenceGenerationId")),
        "inferenceGenerationAt": _text(relation.get("inferenceGenerationAt")),
        "graphStore": _text(relation.get("graphStore")) or "typedb",
        "facts": fact_rows,
        "relations": relation_rows,
        "rules": rules,
        "traces": traces,
        "hypotheses": hypothesis_rows,
    }
    snapshot["counts"] = {
        "facts": len(fact_rows),
        "relations": len(relation_rows),
        "rules": len(rules),
        "traces": len(traces),
        "hypotheses": len(hypothesis_rows),
    }
    return snapshot


def _fallback_fact_rows(facts: Mapping[str, object], rule_ids: Iterable[str]) -> List[Dict[str, object]]:
    rows = []
    rule_ids = list(rule_ids)
    source = _text(facts.get("quoteSource") or facts.get("source"))
    as_of = _text(facts.get("updatedAt") or facts.get("observedAt"))
    for key in CORE_FACT_KEYS:
        value = facts.get(key)
        if not _present(value):
            continue
        rows.append({
            "id": "fact:" + key,
            "label": FACT_LABELS[key],
            "kind": "fact",
            "field": key,
            "role": "context",
            "observedValue": _safe_value(value),
            "expected": "",
            "source": source,
            "asOf": as_of,
            "dataState": _text(facts.get("dataState")),
            "freshnessStatus": "",
            "ruleIds": rule_ids,
            "traceIds": [],
        })
    valuation = _mapping(facts.get("companyValuationContext"))
    for key, value in _mapping(valuation.get("metrics")).items():
        if not _present(value):
            continue
        rows.append({
            "id": "fact:valuation:" + str(key),
            "label": "기업가치 · " + _human_identifier(key),
            "kind": "company-fact",
            "field": "valuation." + str(key),
            "role": "context",
            "observedValue": _safe_value(value),
            "expected": "",
            "source": ", ".join(_unique(_rows(valuation.get("sourceProviders")), 10)),
            "asOf": _text(valuation.get("sourceAsOf") or valuation.get("priceAsOf")),
            "dataState": _text(valuation.get("dataState")),
            "freshnessStatus": "",
            "ruleIds": [item for item in rule_ids if "valuation" in item or "company" in item],
            "traceIds": [],
        })
    disclosure = _mapping(facts.get("dartDisclosure"))
    if disclosure:
        rows.append({
            "id": "fact:dart-disclosure",
            "label": "공시",
            "kind": "event-fact",
            "field": "dartDisclosure",
            "role": "event",
            "observedValue": _text(disclosure.get("reportName")) or "공시 본문 확인",
            "expected": "",
            "source": _text(disclosure.get("provider")) or "OpenDART",
            "asOf": _text(disclosure.get("receiptDate") or disclosure.get("fetchedAt")),
            "dataState": "sufficient" if _text(disclosure.get("documentTextQuality")) == "body" else "partial",
            "freshnessStatus": "",
            "ruleIds": [item for item in rule_ids if "disclosure" in item or "event" in item],
            "traceIds": [],
            "detail": _text(disclosure.get("documentTextPreview"))[:800],
        })
    return rows


def reasoning_detail_from_episode(
    episode: Mapping[str, object],
    scenarios: Iterable[Mapping[str, object]],
    guardrails: Iterable[Mapping[str, object]],
) -> Dict[str, object]:
    """Read a frozen snapshot or reconstruct the bounded detail for legacy rows."""

    facts = _mapping(episode.get("factsAtDecision") or episode.get("facts_at_decision"))
    frozen = _mapping(facts.get("reasoningDetailSnapshot"))
    if frozen.get("version") in SUPPORTED_REASONING_DETAIL_VERSIONS:
        return frozen

    scenario_rows = [_mapping(item) for item in scenarios if isinstance(item, Mapping)]
    guardrail_rows = [_mapping(item) for item in guardrails if isinstance(item, Mapping)]
    rule_roles: Dict[str, str] = {}
    rule_labels: Dict[str, str] = {}
    for scenario in scenario_rows:
        support_ids = _unique(_rows(scenario.get("supportingRuleIds")), 100)
        counter_ids = _unique(_rows(scenario.get("counterRuleIds")), 100)
        for rule_id in support_ids:
            rule_roles.setdefault(rule_id, "support")
            rule_labels.setdefault(rule_id, _text(scenario.get("title")) or _human_identifier(rule_id))
        for rule_id in counter_ids:
            rule_roles.setdefault(rule_id, "counter")
    for guardrail in guardrail_rows:
        for rule_id in _unique(_rows(guardrail.get("sourceRuleIds")), 100):
            rule_roles.setdefault(rule_id, "constraint")
            rule_labels.setdefault(rule_id, _text(guardrail.get("label")) or _human_identifier(rule_id))
    rule_ids = list(rule_roles)
    rules = [{
        "id": rule_id,
        "label": rule_labels.get(rule_id) or _human_identifier(rule_id),
        "description": next((_text(item.get("claim")) for item in scenario_rows if rule_id in item.get("ruleIds", [])), ""),
        "evidenceRole": rule_roles.get(rule_id, "context"),
        "reviewLevel": "",
        "dataState": "",
        "decisionStage": "",
        "candidateAction": next((_text(item.get("candidateAction")) for item in scenario_rows if rule_id in item.get("ruleIds", [])), ""),
        "referenceOnly": False,
        "evidenceUsable": True,
        "evidence": [],
        "traceIds": _unique(path for item in scenario_rows if rule_id in item.get("ruleIds", []) for path in item.get("relationIds", [])),
        "relationIds": [],
        "conditions": [],
        # A legacy episode only preserved hypothesis-level knowledge.  Do not
        # present that material as if it were the exact basis of every rule.
        "knowledgeBasis": {},
        "knowledgeBasisScope": "unavailable-in-legacy-episode",
    } for rule_id in rule_ids]

    relations = []
    traces = []
    for scenario in scenario_rows:
        related_rules = list(scenario.get("ruleIds") or [])
        for path_id in scenario.get("relationIds") or []:
            traces.append({
                "id": path_id,
                "ruleId": related_rules[0] if related_rules else "",
                "label": _text(scenario.get("title")) or "TypeDB 추론 경로",
                "matched": True,
                "dataState": "",
                "freshnessStatus": "",
                "evidenceUsable": True,
                "matchedConditionIds": list(scenario.get("accountConditionIds") or []) + list(scenario.get("marketConditionIds") or []),
                "evidenceRelationIds": list(scenario.get("supportingEvidenceIds") or []),
                "conditions": [{
                    "id": condition_id,
                    "label": _human_identifier(condition_id),
                    "kind": "condition",
                    "field": "",
                    "relationType": "",
                    "role": "required",
                    "observedValue": None,
                    "expected": "",
                    "source": "",
                    "asOf": "",
                    "dataState": "",
                    "freshnessStatus": "",
                    "ruleIds": related_rules,
                    "traceIds": [path_id],
                    "verified": False,
                } for condition_id in list(scenario.get("accountConditionIds") or []) + list(scenario.get("marketConditionIds") or [])],
            })
        for relation_type in _unique(list(scenario.get("accountRelationTypes") or []) + list(scenario.get("marketRelationTypes") or []), 100):
            relation_id = "relation-type:" + relation_type + ":" + _text(scenario.get("id"))
            relations.append({
                "id": relation_id,
                "type": relation_type,
                "label": _relation_type_label(relation_type),
                "source": _text(episode.get("symbol")),
                "sourceLabel": _text(episode.get("subjectName") or episode.get("subject_name")),
                "target": _text(scenario.get("title")),
                "targetLabel": _text(scenario.get("title")),
                "ruleId": related_rules[0] if related_rules else "",
                "polarity": _text(scenario.get("stance")) or "context",
                "decisionStage": "",
                "actionGroup": "",
                "reviewLevel": "",
                "dataState": "",
                "freshnessStatus": "",
                "evidenceUsable": True,
                "referenceOnly": False,
            })

    fact_rows = _fallback_fact_rows(facts, rule_ids)
    hypotheses = [{
        "id": _text(item.get("id")),
        "label": _text(item.get("title")),
        "claim": _text(item.get("claim")),
        "stance": _text(item.get("stance")),
        "horizon": _text(item.get("horizon")),
        "state": _text(item.get("state")),
        "stateLabel": _text(item.get("stateLabel")),
        "selected": bool(item.get("selected")),
        "candidateAction": _text(item.get("candidateAction")),
        "supportingRuleIds": list(item.get("supportingRuleIds") or []),
        "counterRuleIds": list(item.get("counterRuleIds") or []),
        "supportingEvidenceIds": list(item.get("supportingEvidenceIds") or []),
        "counterEvidenceIds": list(item.get("counterEvidenceIds") or []),
        "traceIds": list(item.get("relationIds") or []),
        "assumptions": list(item.get("assumptions") or []),
        "invalidationConditions": list(item.get("invalidationConditions") or []),
    } for item in scenario_rows]
    return {
        "version": REASONING_DETAIL_VERSION,
        "snapshotState": "reconstructed",
        "snapshotStateLabel": "저장된 판단에서 복원",
        "snapshotReason": "이전 판단에는 상세 추론 스냅샷이 없어 DecisionEpisode의 사실·가설·규칙 연결에서 복원했습니다. 당시 TypeDB의 미저장 속성은 확정하지 않습니다.",
        "recordCompleteness": "partial",
        "limitations": [
            "개별 규칙의 실제 관측값과 연산자는 당시 저장되지 않아 확정할 수 없습니다.",
            "가설 수준의 연구 근거를 개별 규칙의 지식 근거로 사용하지 않습니다.",
        ],
        "sourceAboxSnapshotId": _text(episode.get("sourceAboxSnapshotId") or episode.get("source_abox_snapshot_id")),
        "inferenceGenerationId": _text(episode.get("inferenceGenerationId") or episode.get("inference_generation_id")),
        "inferenceGenerationAt": "",
        "graphStore": "typedb",
        "facts": fact_rows,
        "relations": relations,
        "rules": rules,
        "traces": traces,
        "hypotheses": hypotheses,
        "counts": {
            "facts": len(fact_rows),
            "relations": len(relations),
            "rules": len(rules),
            "traces": len(traces),
            "hypotheses": len(hypotheses),
        },
    }


SUBJECT_REASONING_LINEAGE_VERSION = "subject-reasoning-lineage-v1"


def _first(item: Mapping[str, object], *keys: str, default=None):
    for key in keys:
        if key in item and item.get(key) not in (None, ""):
            return item.get(key)
    return default


def _stable_suffix(value: object, length: int = 20) -> str:
    encoded = json.dumps(
        _safe_value(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:length]


def _subject_trace_symbol(trace_id: object) -> str:
    match = re.match(r"^inference-trace:([^:]+):", _text(trace_id), re.IGNORECASE)
    return match.group(1).upper() if match else ""


def _identifier_mentions_symbol(identifier: object, symbol: str) -> bool:
    value = _text(identifier).upper()
    normalized = _text(symbol).upper()
    return bool(normalized and re.search(r"(?:^|:)" + re.escape(normalized) + r"(?:[:]|$)", value))


def _subject_evaluation_state(
    evaluation: Mapping[str, object],
    *,
    account_id: str,
    symbol: str,
    source_abox_snapshot_id: str,
    inference_generation_id: str,
) -> tuple[str, str]:
    """Return included, foreign, or invalid for one frozen batch proof."""

    proof = _mapping(evaluation.get("proof") or evaluation.get("matchProof"))
    evaluation_account = _text(_first(evaluation, "account_id", "accountId")) or "default"
    evaluation_snapshot = _text(_first(evaluation, "source_abox_snapshot_id", "sourceAboxSnapshotId"))
    evaluation_generation = _text(_first(evaluation, "inference_generation_id", "inferenceGenerationId"))
    expected_account = _text(account_id) or "default"
    if evaluation_account != expected_account:
        return "invalid", "account-id-mismatch"
    if source_abox_snapshot_id and evaluation_snapshot != source_abox_snapshot_id:
        return "invalid", "source-abox-snapshot-id-mismatch"
    if inference_generation_id and evaluation_generation != inference_generation_id:
        return "invalid", "inference-generation-id-mismatch"

    trace_id = _text(_first(proof, "trace_id", "traceId", "id"))
    trace_symbol = _subject_trace_symbol(trace_id)
    if trace_symbol:
        return ("included", "trace-symbol-match") if trace_symbol == symbol else ("foreign", "trace-symbol-mismatch")
    proof_subject = _text(_first(proof, "subject_id", "subjectId", "sourceId")).upper()
    if proof_subject:
        return ("included", "proof-subject-match") if proof_subject == symbol else ("foreign", "proof-subject-mismatch")
    target_ids = [
        _first(_mapping(condition), "target_id", "targetId")
        for condition in proof.get("conditions") or proof.get("matchedConditions") or []
        if isinstance(condition, Mapping)
    ]
    target_ids = [value for value in target_ids if _text(value)]
    if target_ids:
        return (
            ("included", "condition-target-match")
            if any(_identifier_mentions_symbol(value, symbol) for value in target_ids)
            else ("foreign", "condition-target-mismatch")
        )
    return "invalid", "subject-lineage-unverifiable"


def _subject_hypothesis_row(
    item: Mapping[str, object],
    *,
    selected_hypothesis_id: str,
    selected_rule_id: str,
    observations: Iterable[Mapping[str, object]],
) -> Dict[str, object]:
    hypothesis_id = _text(_first(item, "hypothesis_id", "hypothesisId", "id"))
    support_rules = _unique(_rows(_first(item, "supporting_rule_ids", "supportingRuleIds", default=[])), 50)
    counter_rules = _unique(_rows(_first(item, "counter_rule_ids", "counterRuleIds", default=[])), 50)
    support_evidence = _unique(_rows(_first(item, "supporting_evidence_ids", "supportingEvidenceIds", default=[])), 100)
    counter_evidence = _unique(_rows(_first(item, "counter_evidence_ids", "counterEvidenceIds", default=[])), 100)
    trace_ids = _unique(_rows(_first(item, "causal_trace_ids", "causalTraceIds", "causal_path_ids", "causalPathIds", default=[])), 50)
    knowledge = _mapping(_first(item, "knowledge_basis", "knowledgeBasis", default={}))
    contract = _mapping(_first(item, "claim_contract", "claimContract", default={}))
    qualification = _mapping(item.get("qualification"))
    claim_contract_id = _text(_first(contract, "claimContractId", "claim_contract_id"))
    matched_observations = [
        _mapping(row)
        for row in observations or []
        if isinstance(row, Mapping)
        and (
            (claim_contract_id and _text(_first(row, "claimContractId", "claim_contract_id")) == claim_contract_id)
            or _text(_first(row, "hypothesisId", "hypothesis_id")) == hypothesis_id
        )
    ]
    compact_observations = [{
        "episodeId": _text(_first(row, "episodeId", "episode_id")),
        "candidateSetId": _text(_first(row, "candidateSetId", "candidate_set_id")),
        "hypothesisId": _text(_first(row, "hypothesisId", "hypothesis_id")),
        "claimContractId": _text(_first(row, "claimContractId", "claim_contract_id")),
        "sourceAboxSnapshotId": _text(_first(row, "sourceAboxSnapshotId", "source_abox_snapshot_id")),
        "inferenceGenerationId": _text(_first(row, "inferenceGenerationId", "inference_generation_id")),
        "observedFromAt": _text(_first(row, "observedFromAt", "observed_from_at")),
        "independenceBucket": _text(_first(row, "independenceBucket", "independence_bucket")),
        "candidateAction": _text(_first(row, "candidateAction", "candidate_action")).upper(),
        "status": _text(row.get("status")),
    } for row in matched_observations]
    ai_selected = bool(selected_hypothesis_id and hypothesis_id == selected_hypothesis_id)
    typedb_leading = bool(not selected_hypothesis_id and selected_rule_id in support_rules)
    label = _text(_first(item, "template_label", "templateLabel", "label")) or _human_identifier(support_rules[0] if support_rules else hypothesis_id)
    claim = _text(item.get("claim")) or label
    return {
        "id": hypothesis_id,
        "label": label,
        "title": label,
        "claim": claim,
        "stance": _text(item.get("stance")) or "context",
        "horizon": _text(item.get("horizon")) or "multi-horizon",
        "state": _text(_first(item, "evidence_state", "evidenceState")) or "unresolved",
        "stateLabel": _text(_first(item, "evidence_state_label", "evidenceStateLabel")) or "확인 중",
        "verificationStatus": _text(_first(item, "verification_status", "verificationStatus")),
        "selected": ai_selected or typedb_leading,
        "selectionSource": "ai" if ai_selected else "typedb-synthesis" if typedb_leading else "alternative",
        "candidateAction": _text(_first(item, "candidate_action", "candidateAction")).upper(),
        "supportingRuleIds": support_rules,
        "counterRuleIds": counter_rules,
        "ruleIds": _unique([*support_rules, *counter_rules], 100),
        "supportingEvidenceIds": support_evidence,
        "counterEvidenceIds": counter_evidence,
        "supportCount": len(support_evidence),
        "counterCount": len(counter_evidence),
        "traceIds": trace_ids,
        "relationIds": trace_ids,
        "assumptions": _unique(_rows(item.get("assumptions")), 20),
        "invalidationConditions": _unique(_rows(_first(item, "invalidation_conditions", "invalidationConditions", default=[])), 20),
        "decisionEligibility": _text(knowledge.get("decisionEligibility") or contract.get("decisionAuthority")),
        "knowledgeBasis": _safe_value(knowledge),
        "plainLanguageBasis": _text(knowledge.get("plainLanguageBasis")),
        "claimContract": _safe_value(contract),
        "claimContractId": claim_contract_id,
        "qualification": _safe_value(qualification),
        "observationState": {
            "status": _text(matched_observations[0].get("status")) if matched_observations else "not-linked",
            "sampleCount": len(matched_observations),
            "episodes": compact_observations[:8],
        },
    }


def _subject_condition_row(
    condition: Mapping[str, object],
    *,
    rule_id: str,
    trace_id: str,
    proof_id: str,
    evidence_role: str,
) -> Dict[str, object]:
    condition_id = _text(_first(condition, "condition_id", "conditionId", "id"))
    field = _text(condition.get("field"))
    relation_type = _text(_first(condition, "relation_type", "relationType"))
    observed = _first(condition, "observed_value", "observedValue")
    target_properties = _mapping(_first(condition, "target_properties", "targetProperties", "matchedTargetProperties", default={}))
    source_properties = _mapping(_first(condition, "source_properties", "sourceProperties", "matchedSourceProperties", default={}))
    if not target_properties and isinstance(observed, Mapping):
        target_properties = dict(observed)
    source_fact_ids = _unique(_rows(_first(condition, "source_fact_ids", "sourceFactIds", default=[])), 64)
    evidence_ids = _unique([
        *_rows(_first(condition, "evidence_ids", "evidenceIds", default=[])),
        *source_fact_ids,
    ], 100)
    target_id = _text(_first(condition, "target_id", "targetId"))
    label = (
        FACT_LABELS.get(field)
        or _text(target_properties.get("signalType"))
        or _relation_type_label(relation_type)
        or _human_identifier(condition_id)
        or "성립 조건"
    )
    fact_identity = source_fact_ids[0] if source_fact_ids else target_id or (
        "condition-fact:" + _stable_suffix([field, observed, relation_type, target_properties])
    )
    return {
        "id": fact_identity,
        "conditionId": condition_id,
        "label": label,
        "kind": "model-signal" if target_properties.get("releaseId") else _text(condition.get("kind")) or "fact",
        "field": field,
        "relationType": relation_type,
        "role": evidence_role,
        "observedValue": _safe_value(observed),
        "expected": _expected_text({
            "operator": condition.get("operator"),
            "expectedValue": _first(condition, "expected_value", "expectedValue"),
        }),
        "result": _text(condition.get("result")) or "matched",
        "source": _text(condition.get("source")),
        "sourceUrl": _text(_first(condition, "source_url", "sourceUrl")),
        "asOf": _text(_first(condition, "source_as_of", "sourceAsOf", "observedAt")),
        "freshnessStatus": _text(_first(condition, "freshness", "freshnessStatus")),
        "relationId": _text(_first(condition, "relation_id", "relationId")),
        "targetId": target_id,
        "targetKind": _text(_first(condition, "target_kind", "targetKind")),
        "sourceProperties": _safe_value(source_properties),
        "targetProperties": _safe_value(target_properties),
        "sourceFactIds": source_fact_ids,
        "evidenceIds": evidence_ids,
        "proofId": proof_id,
        "ruleIds": [rule_id],
        "traceIds": [trace_id] if trace_id else [],
    }


def _merge_subject_fact(existing: Dict[str, object], current: Mapping[str, object]) -> Dict[str, object]:
    if not existing:
        return dict(current)
    existing["ruleIds"] = _unique([*(existing.get("ruleIds") or []), *(current.get("ruleIds") or [])], 100)
    existing["traceIds"] = _unique([*(existing.get("traceIds") or []), *(current.get("traceIds") or [])], 100)
    existing["evidenceIds"] = _unique([*(existing.get("evidenceIds") or []), *(current.get("evidenceIds") or [])], 100)
    if existing.get("role") != current.get("role"):
        existing["role"] = "mixed"
    return existing


def subject_reasoning_lineage(
    subject_case: Mapping[str, object],
    reasoning_case: Mapping[str, object],
    ai_insight_episode: Mapping[str, object] = None,
    hypothesis_observations: Iterable[Mapping[str, object]] = (),
) -> Dict[str, object]:
    """Build one immutable subject path across TBox, ABox, rules, hypotheses and AI.

    A reasoning batch can contain several securities. Every included proof must
    therefore match the subject account, ABox snapshot, inference generation,
    and trace subject. Anything else is excluded before it reaches either the
    web detail or the AI prompt.
    """

    subject = _mapping(subject_case)
    batch = _mapping(reasoning_case)
    ai_episode = _mapping(ai_insight_episode)
    candidate = _mapping(_first(subject, "candidate_set", "candidateSet", default={}))
    synthesis = _mapping(subject.get("synthesis"))
    final = _mapping(_first(subject, "final_decision", "finalDecision", default={}))
    inference = _mapping(_first(batch, "inference_result", "inferenceResult", default={}))
    release_manifest = _mapping(
        _first(batch, "release_manifest", "releaseManifest", default={})
    )
    account_id = _text(_first(subject, "account_id", "accountId")) or "default"
    symbol = _text(subject.get("symbol")).upper()
    source_abox_snapshot_id = _text(_first(subject, "source_abox_snapshot_id", "sourceAboxSnapshotId"))
    inference_generation_id = _text(_first(subject, "inference_generation_id", "inferenceGenerationId"))
    selected_rule_id = _text(_first(synthesis, "selected_rule_id", "selectedRuleId"))
    ai_insight = _mapping(ai_episode.get("insight"))
    selected_hypothesis_id = _text(
        _first(final, "selected_hypothesis_id", "selectedHypothesisId")
        or _first(ai_insight, "selected_hypothesis_id", "selectedHypothesisId")
    )
    research_lead_hypothesis_id = _text(
        _first(
            ai_insight,
            "research_lead_hypothesis_id",
            "researchLeadHypothesisId",
        )
    )
    ai_hypothesis_reviews = [
        _mapping(item)
        for item in ai_insight.get("hypotheses") or []
        if isinstance(item, Mapping)
    ]
    ai_reviews_by_id = {
        _identity(item, "hypothesisId", "hypothesis_id", "id"): item
        for item in ai_hypothesis_reviews
        if _identity(item, "hypothesisId", "hypothesis_id", "id")
    }
    raw_hypotheses = [
        _mapping(item)
        for item in candidate.get("hypotheses") or []
        if isinstance(item, Mapping)
    ]
    observations = [
        _mapping(item)
        for item in hypothesis_observations or []
        if isinstance(item, Mapping)
    ]
    hypotheses = [
        _subject_hypothesis_row(
            item,
            selected_hypothesis_id=selected_hypothesis_id,
            selected_rule_id=selected_rule_id,
            observations=observations,
        )
        for item in raw_hypotheses
    ]
    for hypothesis in hypotheses:
        hypothesis_id = _text(hypothesis.get("id"))
        hypothesis["researchLead"] = bool(
            research_lead_hypothesis_id
            and hypothesis_id == research_lead_hypothesis_id
        )
        if hypothesis["researchLead"] and not hypothesis.get("selected"):
            hypothesis["selectionSource"] = "ai-research-lead"
        hypothesis["aiReview"] = _safe_value(
            ai_reviews_by_id.get(hypothesis_id) or {}
        )
    hypothesis_rules = {
        rule_id
        for item in hypotheses
        for rule_id in item.get("ruleIds") or []
    }
    constraint_rule_ids = set(_unique([
        *_rows(_first(synthesis, "portfolio_constraint_rule_ids", "portfolioConstraintRuleIds", default=[])),
        *_rows(_first(synthesis, "execution_constraint_rule_ids", "executionConstraintRuleIds", default=[])),
    ], 100))
    quality_rule_ids = set(_unique(_rows(_first(synthesis, "data_quality_rule_ids", "dataQualityRuleIds", default=[])), 100))
    relevant_rule_ids = set(hypothesis_rules) | constraint_rule_ids | quality_rule_ids
    if selected_rule_id:
        relevant_rule_ids.add(selected_rule_id)

    issues: List[Dict[str, object]] = []
    limitations: List[str] = []
    identity_checks = {
        "candidateAccountId": _text(_first(candidate, "account_id", "accountId")) or account_id,
        "candidateSymbol": _text(candidate.get("symbol")).upper() or symbol,
        "candidateAboxSnapshotId": _text(_first(candidate, "source_abox_snapshot_id", "sourceAboxSnapshotId")) or source_abox_snapshot_id,
        "candidateInferenceGenerationId": _text(_first(candidate, "inference_generation_id", "inferenceGenerationId")) or inference_generation_id,
        "synthesisAccountId": _text(_first(synthesis, "account_id", "accountId")) or account_id,
        "synthesisSymbol": _text(synthesis.get("symbol")).upper() or symbol,
        "synthesisAboxSnapshotId": _text(_first(synthesis, "source_abox_snapshot_id", "sourceAboxSnapshotId")) or source_abox_snapshot_id,
        "synthesisInferenceGenerationId": _text(_first(synthesis, "inference_generation_id", "inferenceGenerationId")) or inference_generation_id,
    }
    expected_identity = {
        "candidateAccountId": account_id,
        "candidateSymbol": symbol,
        "candidateAboxSnapshotId": source_abox_snapshot_id,
        "candidateInferenceGenerationId": inference_generation_id,
        "synthesisAccountId": account_id,
        "synthesisSymbol": symbol,
        "synthesisAboxSnapshotId": source_abox_snapshot_id,
        "synthesisInferenceGenerationId": inference_generation_id,
    }
    for key, expected in expected_identity.items():
        actual = identity_checks.get(key)
        if expected and actual != expected:
            issues.append({
                "code": "LINEAGE_IDENTITY_MISMATCH",
                "state": "blocked",
                "detail": key + "가 현재 종목 추론 식별자와 일치하지 않습니다.",
                "expected": expected,
                "actual": actual,
            })

    included = []
    foreign_count = 0
    invalid_count = 0
    for raw in inference.get("rule_evaluations") or inference.get("ruleEvaluations") or []:
        if not isinstance(raw, Mapping):
            continue
        evaluation = _mapping(raw)
        rule_id = _text(_first(evaluation, "rule_id", "ruleId"))
        if relevant_rule_ids and rule_id not in relevant_rule_ids:
            continue
        state, reason = _subject_evaluation_state(
            evaluation,
            account_id=account_id,
            symbol=symbol,
            source_abox_snapshot_id=source_abox_snapshot_id,
            inference_generation_id=inference_generation_id,
        )
        if state == "foreign":
            foreign_count += 1
            continue
        if state == "invalid":
            invalid_count += 1
            issues.append({
                "code": "UNVERIFIABLE_RULE_PROOF",
                "state": "warning",
                "detail": rule_id + " 규칙 증거를 현재 종목에 안전하게 연결할 수 없습니다.",
                "reason": reason,
            })
            continue
        included.append(evaluation)

    facts_by_id: Dict[str, Dict[str, object]] = {}
    relations_by_id: Dict[str, Dict[str, object]] = {}
    rules_by_id: Dict[str, Dict[str, object]] = {}
    traces: List[Dict[str, object]] = []
    for evaluation in included:
        proof = _mapping(evaluation.get("proof") or evaluation.get("matchProof"))
        rule_id = _text(_first(evaluation, "rule_id", "ruleId") or _first(proof, "rule_id", "ruleId"))
        trace_id = _text(_first(proof, "trace_id", "traceId", "id"))
        proof_id = _text(_first(proof, "proof_id", "proofId"))
        if rule_id in constraint_rule_ids:
            evidence_role = "constraint"
        elif rule_id in quality_rule_ids:
            evidence_role = "data-quality"
        elif any(rule_id in item.get("counterRuleIds", []) for item in hypotheses):
            evidence_role = "counter"
        else:
            evidence_role = "support"
        related_hypotheses = [item for item in hypotheses if rule_id in item.get("ruleIds", [])]
        condition_rows = []
        for raw_condition in proof.get("conditions") or proof.get("matchedConditions") or []:
            condition = _mapping(raw_condition)
            if _text(condition.get("kind")) == "any-condition-group":
                continue
            row = _subject_condition_row(
                condition,
                rule_id=rule_id,
                trace_id=trace_id,
                proof_id=proof_id,
                evidence_role=evidence_role,
            )
            observed = row.get("observedValue")
            if observed in (None, "", {}, []) and not row.get("targetId"):
                if _text(condition.get("role") or "required").lower() != "optional":
                    limitations.append(
                        rule_id + "의 " + (row.get("label") or row.get("conditionId")) + " 조건은 성립 표시만 있고 관측값 계보가 없습니다."
                    )
                continue
            condition_rows.append(row)
            fact_id = _text(row.get("id"))
            facts_by_id[fact_id] = _merge_subject_fact(facts_by_id.get(fact_id, {}), row)
            if row.get("relationType"):
                relation_id = row.get("relationId") or (
                    "proof-relation:" + _stable_suffix([proof_id, row.get("conditionId"), row.get("targetId")])
                )
                relations_by_id[relation_id] = {
                    "id": relation_id,
                    "type": row.get("relationType"),
                    "label": row.get("label"),
                    "source": symbol,
                    "sourceLabel": symbol,
                    "target": row.get("targetId"),
                    "targetLabel": _text(_mapping(row.get("targetProperties")).get("signalType")) or _text(row.get("observedValue")) or row.get("targetId"),
                    "ruleId": rule_id,
                    "traceId": trace_id,
                    "polarity": evidence_role,
                    "dataState": _text(_mapping(row.get("targetProperties")).get("dataState")),
                    "freshnessStatus": row.get("freshnessStatus"),
                    "evidenceUsable": bool(evaluation.get("decision_eligible") or evaluation.get("decisionEligible")),
                    "referenceOnly": not bool(evaluation.get("decision_eligible") or evaluation.get("decisionEligible")),
                }
        trace = {
            "id": trace_id,
            "proofId": proof_id,
            "ruleId": rule_id,
            "label": _human_identifier(rule_id),
            "matched": bool(evaluation.get("matched", True)),
            "selected": rule_id == selected_rule_id,
            "decisionEligible": bool(evaluation.get("decision_eligible") or evaluation.get("decisionEligible")),
            "dataState": "sufficient" if condition_rows else "partial",
            "freshnessStatus": next((_text(item.get("freshnessStatus")) for item in condition_rows if _text(item.get("freshnessStatus"))), ""),
            "evidenceUsable": bool(evaluation.get("decision_eligible") or evaluation.get("decisionEligible")),
            "matchedConditionIds": [item.get("conditionId") for item in condition_rows],
            "evidenceRelationIds": _unique(_rows(_first(proof, "evidence_ids", "evidenceIds", default=[])), 100),
            "conditions": condition_rows,
        }
        traces.append(trace)
        knowledge = next((_mapping(item.get("knowledgeBasis")) for item in related_hypotheses if item.get("knowledgeBasis")), {})
        description = _text(knowledge.get("plainLanguageBasis")) or next((_text(item.get("claim")) for item in related_hypotheses), "")
        rule = rules_by_id.setdefault(rule_id, {
            "id": rule_id,
            "label": _human_identifier(rule_id),
            "description": description,
            "evidenceRole": evidence_role,
            "selected": rule_id == selected_rule_id,
            "decisionEligible": bool(evaluation.get("decision_eligible") or evaluation.get("decisionEligible")),
            "candidateAction": next((_text(item.get("candidateAction")) for item in related_hypotheses), ""),
            "traceIds": [],
            "relationIds": [],
            "conditions": [],
            "knowledgeBasis": _safe_value(knowledge),
        })
        rule["traceIds"] = _unique([*(rule.get("traceIds") or []), trace_id], 100)
        rule["relationIds"] = _unique([
            *(rule.get("relationIds") or []),
            *(item["id"] for item in relations_by_id.values() if item.get("ruleId") == rule_id),
        ], 100)
        rule["conditions"] = [*(rule.get("conditions") or []), *condition_rows]

    rules = list(rules_by_id.values())
    facts = list(facts_by_id.values())
    relations = list(relations_by_id.values())
    proof_rule_ids = {item.get("id") for item in rules}
    missing_rule_ids = sorted(rule_id for rule_id in hypothesis_rules if rule_id not in proof_rule_ids)
    if missing_rule_ids:
        issues.append({
            "code": "HYPOTHESIS_RULE_PROOF_MISSING",
            "state": "blocked" if selected_rule_id in missing_rule_ids else "warning",
            "detail": "가설이 참조한 규칙 증거를 현재 종목의 저장 세대에서 찾지 못했습니다: " + ", ".join(missing_rule_ids[:6]),
            "ruleIds": missing_rule_ids,
        })
    if selected_rule_id and selected_rule_id not in proof_rule_ids:
        issues.append({
            "code": "SELECTED_RULE_PROOF_MISSING",
            "state": "blocked",
            "detail": "선택 규칙의 관측 사실과 실행 trace가 없어 연결된 투자 설명으로 사용할 수 없습니다.",
            "ruleId": selected_rule_id,
        })
    if limitations:
        issues.append({
            "code": "CONDITION_VALUE_LINEAGE_PARTIAL",
            "state": "warning",
            "detail": limitations[0],
            "affectedCount": len(limitations),
        })

    publication_mode = _text(ai_episode.get("publicationMode"))
    ai_authored = bool(ai_episode.get("aiAuthored"))
    contract_passed = bool(ai_episode.get("publicationContractPassed"))
    current_ai = bool(
        ai_episode
        and _text(ai_episode.get("subjectCaseId")) == _text(_first(subject, "subject_case_id", "subjectCaseId"))
        and _text(ai_episode.get("inferenceGenerationId")) == inference_generation_id
        and _text(ai_episode.get("candidateFingerprint")) == _text(candidate.get("fingerprint"))
    )
    ai_status = (
        "ai-authored" if current_ai and ai_authored and contract_passed
        else "typedb-fallback" if current_ai and publication_mode == "typedb-fallback"
        else "contract-failed" if current_ai and ai_episode and not contract_passed
        else "previous-generation" if ai_episode
        else "not-run"
    )
    ai_summary = _text(ai_insight.get("summary") or ai_insight.get("investmentView"))
    ai_action = _text(ai_insight.get("action")).upper() or "NO_ACTION"

    scenarios = []
    paths = []
    for hypothesis in hypotheses:
        rule_ids = set(hypothesis.get("ruleIds") or [])
        path_facts = [item for item in facts if rule_ids.intersection(item.get("ruleIds") or [])]
        path_relations = [item for item in relations if item.get("ruleId") in rule_ids]
        path_rules = [item for item in rules if item.get("id") in rule_ids]
        path_traces = [item for item in traces if item.get("ruleId") in rule_ids]
        ai_review = _mapping(hypothesis.get("aiReview"))
        scenarios.append({
            **hypothesis,
            "ruleIds": list(hypothesis.get("ruleIds") or []),
            "relationIds": [item.get("id") for item in path_relations],
        })
        paths.append({
            "id": hypothesis.get("id") + ":lineage",
            "title": hypothesis.get("title"),
            "selected": bool(hypothesis.get("selected")),
            "researchLead": bool(hypothesis.get("researchLead")),
            "selectionSource": hypothesis.get("selectionSource"),
            "eligibility": hypothesis.get("decisionEligibility") or "unknown",
            "nodes": [
                {"layer": "fact", "label": "ABox 관측 사실", "refIds": [item.get("id") for item in path_facts], "items": path_facts},
                {"layer": "relation", "label": "ABox 관계", "refIds": [item.get("id") for item in path_relations], "items": path_relations},
                {"layer": "rule", "label": "RuleBox 성립 규칙", "refIds": [item.get("id") for item in path_rules], "items": path_rules, "traces": path_traces},
                {"layer": "hypothesis", "label": "검증 중인 투자 가설", "refIds": [hypothesis.get("id")], "items": [hypothesis]},
                {
                    "layer": "decision",
                    "label": "AI 분석" if ai_status == "ai-authored" else "TypeDB 결과" if ai_status == "typedb-fallback" else "현재 판단 단계",
                    "refIds": [_text(ai_episode.get("episodeId"))] if ai_episode else [],
                    "items": [{
                        "id": _text(ai_episode.get("episodeId")) or "ai-not-run",
                        "label": ai_status,
                        "reason": _text(ai_review.get("reasoning")) or ai_summary or (
                            "AI 모델 실행에 실패해 TypeDB 결과만 보존했습니다."
                            if ai_status == "typedb-fallback"
                            else "AI가 아직 이 추론 세대를 분석하지 않았습니다."
                        ),
                        "selectedHypothesisId": selected_hypothesis_id,
                        "researchLeadHypothesisId": research_lead_hypothesis_id,
                        "hypothesisId": hypothesis.get("id"),
                        "hypothesisVerdict": _text(ai_review.get("verdict")),
                        "action": ai_action,
                        "publicationMode": publication_mode,
                        "aiAuthored": ai_authored,
                        "publicationContractPassed": contract_passed,
                        "contractFailureCode": _text(ai_episode.get("contractFailureCode")),
                        "abstained": ai_action == "NO_ACTION",
                    }],
                },
            ],
            "inferenceGenerationId": inference_generation_id,
        })

    supporting_causes = [{
        "id": item.get("id"),
        "layer": "hypothesis",
        "role": "support",
        "status": item.get("state"),
        "title": item.get("title"),
        "summary": item.get("plainLanguageBasis") or item.get("claim"),
        "effect": "후보 행동 " + (item.get("candidateAction") or "관찰") + " · 지지 근거 " + str(item.get("supportCount") or 0) + "개",
    } for item in hypotheses if item.get("selected") or item.get("supportCount")]
    constraint_causes = [{
        "id": item.get("id"),
        "layer": "rule",
        "role": "constraint",
        "status": "matched",
        "title": item.get("label"),
        "summary": item.get("description") or "현재 행동 범위를 제한하는 규칙이 성립했습니다.",
        "effect": "현재 행동 후보와 별도로 적용되는 제약입니다.",
    } for item in rules if item.get("evidenceRole") in {"constraint", "data-quality"}]
    change_conditions = _unique([
        condition
        for item in hypotheses
        for condition in item.get("invalidationConditions") or []
    ], 12)
    type_db_actions = _unique(
        (item.get("candidateAction") for item in hypotheses if item.get("candidateAction")),
        12,
    )
    comparison = {
        "state": (
            "typedb-and-ai-research"
            if ai_status == "ai-authored" and research_lead_hypothesis_id
            else "typedb-and-ai"
            if ai_status == "ai-authored"
            else ai_status
        ),
        "label": (
            "TypeDB 가설과 AI 연구 비교 완료"
            if ai_status == "ai-authored" and research_lead_hypothesis_id
            else "TypeDB 추론과 AI 분석 연결 완료" if ai_status == "ai-authored"
            else "AI 실패 · TypeDB 대체 해석" if ai_status == "typedb-fallback"
            else "TypeDB 추론만 완료" if ai_status == "not-run"
            else "AI 분석 계약 확인 필요"
        ),
        "comparable": ai_status == "ai-authored",
        "typeDbCandidateActions": type_db_actions,
        "aiFinalAction": ai_action,
        "selectedHypothesisId": selected_hypothesis_id,
        "researchLeadHypothesisId": research_lead_hypothesis_id,
        "hypothesisComparisonState": _text(
            ai_insight.get("hypothesisComparisonState")
        ),
        "reason": ai_summary or (
            "AI가 작성한 분석이 아니므로 TypeDB 후보와 AI 의견을 동일하게 표시하지 않습니다."
            if ai_status == "typedb-fallback"
            else "현재 세대의 TypeDB 가설과 규칙 증거를 먼저 확인합니다."
        ),
    }

    fact_times = sorted(_text(item.get("asOf")) for item in facts if _text(item.get("asOf")))
    current_items = [{
        "id": item.get("id"),
        "field": item.get("field") or item.get("conditionId"),
        "label": item.get("label"),
        "value": item.get("observedValue"),
        "source": item.get("source"),
        "sourceAsOf": item.get("asOf"),
        "freshnessStatus": item.get("freshnessStatus"),
        "role": item.get("role"),
    } for item in facts]
    blocking_issues = [item for item in issues if item.get("state") == "blocked"]
    integrity_state = "blocked" if blocking_issues else "warning" if issues or limitations else "pass"
    reasoning = {
        "version": REASONING_DETAIL_VERSION,
        "snapshotState": "exact" if included else "unavailable",
        "snapshotStateLabel": "종목별 저장 추론 계보" if included else "종목별 추론 계보 없음",
        "snapshotReason": "배치 추론에서 현재 계정·종목·ABox·세대가 모두 일치하는 증거만 연결했습니다.",
        "recordCompleteness": "exact" if included and not blocking_issues else "partial",
        "limitations": _unique(limitations, 20),
        "sourceAboxSnapshotId": source_abox_snapshot_id,
        "inferenceGenerationId": inference_generation_id,
        "inferenceGenerationAt": _text(_first(subject, "completed_at", "completedAt", "updated_at", "updatedAt")),
        "graphStore": "typedb",
        "facts": facts,
        "relations": relations,
        "rules": rules,
        "traces": traces,
        "hypotheses": hypotheses,
        "counts": {
            "facts": len(facts),
            "relations": len(relations),
            "rules": len(rules),
            "traces": len(traces),
            "hypotheses": len(hypotheses),
        },
    }
    evidence_ids = _unique([
        evidence_id
        for item in facts
        for evidence_id in item.get("evidenceIds") or []
    ], 200)
    evidence_records = [{
        "id": evidence_id,
        "role": next((item.get("role") for item in facts if evidence_id in (item.get("evidenceIds") or [])), "context"),
        "roleLabel": "연결 근거",
        "useState": "used",
        "useStateLabel": "추론에 사용",
        "resolutionState": "lineage-linked",
        "title": next((item.get("label") for item in facts if evidence_id in (item.get("evidenceIds") or [])), evidence_id),
        "summary": "ABox 관측값과 RuleBox 조건을 통해 현재 가설에 연결된 근거입니다.",
        "kind": "reasoning-lineage",
        "source": next((item.get("source") for item in facts if evidence_id in (item.get("evidenceIds") or [])), "TypeDB"),
        "sourceAsOf": next((item.get("asOf") for item in facts if evidence_id in (item.get("evidenceIds") or [])), ""),
    } for evidence_id in evidence_ids]
    return {
        "version": SUBJECT_REASONING_LINEAGE_VERSION,
        "status": "ok" if not blocking_issues else "integrity-blocked",
        "identity": {
            "subjectCaseId": _text(_first(subject, "subject_case_id", "subjectCaseId")),
            "batchCaseId": _text(_first(subject, "batch_case_id", "batchCaseId")),
            "accountId": account_id,
            "symbol": symbol,
            "deploymentId": _text(_first(batch, "deployment_id", "deploymentId")),
            "releaseFingerprint": _text(_first(batch, "release_fingerprint", "releaseFingerprint")),
            "releaseId": _text(_first(release_manifest, "release_id", "releaseId")),
            "tboxReleaseId": _text(
                _first(release_manifest, "tbox_release_id", "tboxReleaseId")
            ),
            "tboxFingerprint": _text(
                _first(release_manifest, "tbox_fingerprint", "tboxFingerprint")
            ),
            "ruleboxReleaseId": _text(
                _first(release_manifest, "rulebox_release_id", "ruleboxReleaseId")
            ),
            "ruleboxFingerprint": _text(
                _first(release_manifest, "rulebox_fingerprint", "ruleboxFingerprint")
            ),
            "modelSignalReleaseId": _text(
                _first(release_manifest, "model_signal_release_id", "modelSignalReleaseId")
            ),
            "promptReleaseId": _text(
                _first(release_manifest, "prompt_release_id", "promptReleaseId")
            ),
            "sourceAboxSnapshotId": source_abox_snapshot_id,
            "inferenceGenerationId": inference_generation_id,
            "synthesisId": _text(_first(synthesis, "synthesis_id", "synthesisId")),
            "candidateSetId": _text(_first(candidate, "candidate_set_id", "candidateSetId")),
            "candidateFingerprint": _text(candidate.get("fingerprint")),
            "eligibleHypothesisIds": _unique(
                _rows(_first(candidate, "eligible_hypothesis_ids", "eligibleHypothesisIds", default=[])),
                100,
            ),
            "executionEligibleHypothesisIds": _unique(
                _rows(_first(candidate, "execution_eligible_hypothesis_ids", "executionEligibleHypothesisIds", default=[])),
                100,
            ),
            "referenceHypothesisIds": _unique(
                _rows(_first(candidate, "reference_hypothesis_ids", "referenceHypothesisIds", default=[])),
                100,
            ),
            "selectedRuleId": selected_rule_id,
            "selectedHypothesisId": selected_hypothesis_id,
            "researchLeadHypothesisId": research_lead_hypothesis_id,
            "aiInsightEpisodeId": _text(ai_episode.get("episodeId")),
            "aiPromptReleaseId": _text(ai_episode.get("promptVersion")),
        },
        "integrity": {
            "state": integrity_state,
            "label": "계보 연결 완료" if integrity_state == "pass" else "계보 일부 확인 필요" if integrity_state == "warning" else "계보 연결 차단",
            "issues": issues,
            "includedRuleEvaluationCount": len(included),
            "excludedForeignSubjectEvaluationCount": foreign_count,
            "invalidRuleEvaluationCount": invalid_count,
        },
        "reasoning": reasoning,
        "scenarios": scenarios,
        "explanation": {
            "primaryCause": supporting_causes[0] if supporting_causes else {
                "id": "typedb-no-qualified-hypothesis",
                "layer": "hypothesis",
                "role": "constraint",
                "status": "incomplete",
                "title": "검증 가능한 가설 없음",
                "summary": "현재 종목의 규칙 증거와 연결된 가설을 찾지 못했습니다.",
                "effect": "투자 행동을 만들지 않습니다.",
            },
            "supportingCauses": supporting_causes[:6],
            "counterCauses": [],
            "constraints": constraint_causes[:8],
            "dataGaps": list(candidate.get("data_gaps") or candidate.get("dataGaps") or []),
            "changeConditions": change_conditions,
            "causalPaths": paths,
            "comparison": comparison,
        },
        "currentState": {
            "snapshotState": reasoning["snapshotState"],
            "snapshotStateLabel": reasoning["snapshotStateLabel"],
            "asOf": fact_times[-1] if fact_times else reasoning["inferenceGenerationAt"],
            "groups": [{
                "id": "reasoning-lineage-facts",
                "label": "추론에 실제 사용한 관측값",
                "items": current_items,
            }] if current_items else [],
        },
        "freshness": {
            "decisionAsOf": _text(_first(subject, "completed_at", "completedAt", "updated_at", "updatedAt")),
            "sourceAsOf": fact_times[-1] if fact_times else "",
            "inferenceAsOf": reasoning["inferenceGenerationAt"],
            "updatedAt": _text(_first(subject, "updated_at", "updatedAt")),
            "snapshotState": reasoning["snapshotState"],
            "snapshotStateLabel": reasoning["snapshotStateLabel"],
        },
        "evidence": {
            "supportingIds": _unique([evidence_id for item in hypotheses for evidence_id in item.get("supportingEvidenceIds") or []], 200),
            "counterIds": _unique([evidence_id for item in hypotheses for evidence_id in item.get("counterEvidenceIds") or []], 200),
            "records": evidence_records,
            "resolvedCount": len(evidence_records),
            "identifierOnlyCount": 0,
        },
        "ai": {
            "status": ai_status,
            "currentGeneration": current_ai,
            "publicationMode": publication_mode,
            "aiAuthored": ai_authored,
            "publicationContractPassed": contract_passed,
            "contractFailureCode": _text(ai_episode.get("contractFailureCode")),
            "episodeId": _text(ai_episode.get("episodeId")),
            "model": _text(ai_episode.get("model")),
            "reasoningEffort": _text(ai_episode.get("reasoningEffort")),
            "promptVersion": _text(ai_episode.get("promptVersion")),
            "summary": ai_summary,
            "action": ai_action,
            "hypothesisComparisonState": _text(
                ai_insight.get("hypothesisComparisonState")
            ),
            "hypotheses": _safe_value(ai_hypothesis_reviews),
            "researchLeadHypothesisId": research_lead_hypothesis_id,
            "unresolvedQuestions": _safe_value(
                ai_insight.get("unresolvedQuestions") or []
            ),
            "epistemicSummary": _text(ai_insight.get("epistemicSummary")),
            "causalChain": _safe_value(ai_insight.get("causalChain") or []),
        },
        "traceRefs": {
            "subjectCaseId": _text(_first(subject, "subject_case_id", "subjectCaseId")),
            "batchCaseId": _text(_first(subject, "batch_case_id", "batchCaseId")),
            "sourceAboxSnapshotId": source_abox_snapshot_id,
            "inferenceGenerationId": inference_generation_id,
            "selectedHypothesisId": selected_hypothesis_id,
            "ruleIds": sorted(proof_rule_ids),
            "modelRelease": {
                "deploymentId": _text(_first(batch, "deployment_id", "deploymentId")),
                "releaseId": _text(_first(release_manifest, "release_id", "releaseId")),
                "releaseFingerprint": _text(
                    _first(batch, "release_fingerprint", "releaseFingerprint")
                ),
                "tboxReleaseId": _text(
                    _first(release_manifest, "tbox_release_id", "tboxReleaseId")
                ),
                "tboxFingerprint": _text(
                    _first(release_manifest, "tbox_fingerprint", "tboxFingerprint")
                ),
                "ruleboxReleaseId": _text(
                    _first(release_manifest, "rulebox_release_id", "ruleboxReleaseId")
                ),
                "ruleboxFingerprint": _text(
                    _first(release_manifest, "rulebox_fingerprint", "ruleboxFingerprint")
                ),
                "modelSignalReleaseId": _text(
                    _first(release_manifest, "model_signal_release_id", "modelSignalReleaseId")
                ),
                "promptReleaseId": _text(
                    _first(release_manifest, "prompt_release_id", "promptReleaseId")
                ),
                "aiPromptReleaseId": _text(ai_episode.get("promptVersion")),
                "lineageLabel": "TBox → ABox → RuleBox → InferenceBox → 가설 → AI",
            },
        },
    }
