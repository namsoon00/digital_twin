"""An additive current-observation cohort; legacy history rules stay frozen."""
from dataclasses import replace

from .ontology_rulebox_contracts import GraphRuleCondition

SUFFIX = ".current-flow.v3"
COHORT = {
    "graph.loss_smart_money.defense.v1": "손실 구간 · 외국인·기관 동반 순매수 추정",
    "graph.investor_flow.smart_money_outflow_risk.v1": "외국인·기관 동반 순매도 추정",
    "graph.flow.accumulation.entry.v1": "매수 체결과 매수 대기 물량 우위 확인",
    "graph.flow.recovery_confirmed_by_smart_money.v1": "이동평균 가격 이상과 외국인·기관 합산 순매수 추정",
    "graph.flow.price_up_smart_money_outflow.divergence.v1": "가격 상승 중 외국인·기관 합산 순매도 추정",
}
FIELDS = {"foreignNetVolume", "institutionNetVolume", "individualNetVolume",
          "smartMoneyNetVolume", "tradeStrength", "bidAskImbalance"}


def current_flow_rules(source_rules):
    result = []
    for source in source_rules:
        if source.rule_id not in COHORT:
            continue
        rid = source.rule_id + SUFFIX
        conditions = []
        for condition in source.conditions:
            # The legacy investor profile requires AI strong-evidence quality
            # and rejects every official intraday estimate. This new model
            # admits estimates only through exact field/source validity below;
            # it never changes the legacy profile or empirical qualification.
            if condition.condition_id == "market-evidence-investorFlow-unavailable":
                continue
            filters = dict(condition.target_property_filters or {})
            field = condition.field if condition.kind == "subject_property" else filters.get("field")
            if field in FIELDS:
                if condition.kind == "subject_property":
                    filters = {"field": field, "value": {"operator": condition.operator, "value": condition.value}}
                filters.update(dataScope="verified-current-flow-v1", judgementEvidenceUsable=True)
                condition = replace(condition, kind="relation", field="", operator="==", value=None,
                                    relation_type="HAS_TRADE_FLOW", target_kind="flow-metric",
                                    target_property_filters=filters, hypothesis_scope="market")
            conditions.append(condition)
        quote_filters = {"judgementEvidenceUsable": True, "minClose": 0.000001}
        if any(c.kind == "subject_property" and c.field == "priceChangeRate" for c in conditions):
            quote_filters["priceChangeUsable"] = True
        conditions.append(GraphRuleCondition("current-flow-price-source", "relation", "현재 시세의 원천 자료가 유효합니다.",
            relation_type="HAS_PRICE", target_kind="price-bar", hypothesis_scope="market",
            evidence_group_key="price-path",
            target_property_filters=quote_filters))
        claim = source.resolved_claim_contract
        basis = source.resolved_knowledge_basis
        result.append(replace(source, rule_id=rid, label=COHORT[source.rule_id], version="current-flow-contract-v3",
            conditions=conditions, enabled=True,
            derivations=[replace(d, target_key=d.target_key + SUFFIX,
                target_label="{displayName} " + COHORT[source.rule_id],
                belief_label=COHORT[source.rule_id] + " 조건을 확인했습니다. 이후 가격 반응은 별도 검증합니다.",
                ai_influence_label=COHORT[source.rule_id]) for d in source.derivations],
            knowledge_basis=replace(basis, basis_origin="source-clocked-current-flow-v3",
                plain_language_basis="현재 원천에서 확인한 수급·체결·호가 조건과 이후 가격 반응의 관계를 검증합니다. 누적 매집이나 성공 확률을 뜻하지 않습니다."),
            claim_contract=replace(claim, claim_contract_id="rule-claim:" + rid, rule_id=rid,
                statement=COHORT[source.rule_id] + " 이후 가격 반응을 검증하는 가설", rule_fingerprint=""),
            hypothesis_lifecycle=replace(source.resolved_hypothesis_lifecycle(),
                formation_condition_ids=[c.condition_id for c in conditions if c.role not in {"not", "negative", "exclude"}],
                next_data_requirements=["새 수급 원천값과 가격 회복·괴리·동반 매도 조건을 다시 확인"])))
    return result
