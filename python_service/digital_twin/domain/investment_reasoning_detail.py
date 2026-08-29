"""Snapshot-bound detail for explaining one investment reasoning episode.

The live relation context is large and mutable.  This module freezes only the
facts, TypeDB relations, rule matches, and grounded conditions that participated
in a decision so the UI can explain the historical judgement without querying
the current graph generation.
"""

from __future__ import annotations

import json
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
