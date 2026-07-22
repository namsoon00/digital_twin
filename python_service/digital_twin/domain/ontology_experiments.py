import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Tuple

from .ontology_contracts import PortfolioOntology
from .ontology_quality import build_ontology_quality_sample
from .ontology_rulebox_contracts import GraphInferenceRule
from .ontology_rulebox_governance import rulebox_rules_hash
from .portfolio import utc_now_iso


TRACE_RELATION_TYPES = {"HAS_INFERENCE_TRACE", "TRIGGERED_INFERENCE", "EXPLAINED_BY_TRACE"}


@dataclass
class OntologyExperiment:
    experiment_id: str
    title: str
    hypothesis: str
    symbols: List[str] = field(default_factory=list)
    candidate_rules: List[Dict[str, object]] = field(default_factory=list)
    baseline_rulebox: Dict[str, object] = field(default_factory=dict)
    target_world: Dict[str, str] = field(default_factory=dict)
    status: str = "draft"
    created_at: str = ""
    updated_at: str = ""
    last_result: Dict[str, object] = field(default_factory=dict)
    run_history: List[Dict[str, object]] = field(default_factory=list)
    last_snapshot_key: str = ""
    active_since: str = ""
    paused_at: str = ""
    validation_warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["id"] = payload.pop("experiment_id")
        payload["candidateRules"] = payload.pop("candidate_rules")
        payload["baselineRulebox"] = payload.pop("baseline_rulebox")
        payload["targetWorld"] = payload.pop("target_world")
        payload["createdAt"] = payload.pop("created_at")
        payload["updatedAt"] = payload.pop("updated_at")
        payload["lastResult"] = payload.pop("last_result")
        payload["runHistory"] = payload.pop("run_history")
        payload["lastSnapshotKey"] = payload.pop("last_snapshot_key")
        payload["activeSince"] = payload.pop("active_since")
        payload["pausedAt"] = payload.pop("paused_at")
        payload["validationWarnings"] = payload.pop("validation_warnings")
        return payload

    @staticmethod
    def from_dict(payload: Dict[str, object]):
        payload = dict(payload or {})
        return OntologyExperiment(
            experiment_id=str(payload.get("id") or payload.get("experimentId") or payload.get("experiment_id") or ""),
            title=str(payload.get("title") or "Ontology experiment"),
            hypothesis=str(payload.get("hypothesis") or ""),
            symbols=clean_symbols(payload.get("symbols") or []),
            candidate_rules=[
                dict(item)
                for item in (payload.get("candidateRules") or payload.get("candidate_rules") or [])
                if isinstance(item, dict)
            ],
            baseline_rulebox=dict(payload.get("baselineRulebox") or payload.get("baseline_rulebox") or {}),
            target_world={
                str(key): str(value)
                for key, value in dict(payload.get("targetWorld") or payload.get("target_world") or {}).items()
                if str(value or "").strip()
            },
            status=str(payload.get("status") or "draft"),
            created_at=str(payload.get("createdAt") or payload.get("created_at") or ""),
            updated_at=str(payload.get("updatedAt") or payload.get("updated_at") or ""),
            last_result=dict(payload.get("lastResult") or payload.get("last_result") or {}),
            run_history=[
                dict(item)
                for item in (payload.get("runHistory") or payload.get("run_history") or [])
                if isinstance(item, dict)
            ],
            last_snapshot_key=str(payload.get("lastSnapshotKey") or payload.get("last_snapshot_key") or ""),
            active_since=str(payload.get("activeSince") or payload.get("active_since") or ""),
            paused_at=str(payload.get("pausedAt") or payload.get("paused_at") or ""),
            validation_warnings=[
                str(item)
                for item in (payload.get("validationWarnings") or payload.get("validation_warnings") or [])
                if str(item or "").strip()
            ],
        )


def clean_symbols(values: Iterable[object]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").upper().strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def experiment_id_for(payload: Dict[str, object], stamp: str = "") -> str:
    encoded = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256((encoded + (stamp or utc_now_iso())).encode("utf-8")).hexdigest()[:12]
    return "ontology-exp-" + digest


def compact_rulebox_snapshot(snapshot: Dict[str, object]) -> Dict[str, object]:
    snapshot = dict(snapshot or {})
    rules = [dict(item) for item in (snapshot.get("rules") or []) if isinstance(item, dict)]
    return {
        "status": str(snapshot.get("status") or ""),
        "configured": bool(snapshot.get("configured")),
        "source": str(snapshot.get("source") or ""),
        "engineVersion": str(snapshot.get("engineVersion") or ""),
        "ruleCount": len(rules) if rules else int(snapshot.get("ruleCount") or 0),
        "conditionCount": sum(len(item.get("conditions") or []) for item in rules) if rules else int(snapshot.get("conditionCount") or 0),
        "derivationCount": sum(len(item.get("derivations") or []) for item in rules) if rules else int(snapshot.get("derivationCount") or 0),
        "relationTypes": sorted(set(str(item) for item in (snapshot.get("relationTypes") or []) if str(item or "").strip())),
        "rulesHash": rulebox_rules_hash(rules) if rules else "",
    }


def rule_payloads_from_snapshot(snapshot: Dict[str, object]) -> List[Dict[str, object]]:
    return [dict(item) for item in (snapshot or {}).get("rules") or [] if isinstance(item, dict)]


def normalize_candidate_rules(
    payload: Dict[str, object],
    rulebox_snapshot: Dict[str, object],
) -> Tuple[List[Dict[str, object]], List[str]]:
    payload = dict(payload or {})
    existing_rule_ids = {
        str(item.get("rule_id") or item.get("ruleId") or "").strip()
        for item in rule_payloads_from_snapshot(rulebox_snapshot)
        if isinstance(item, dict)
    }
    candidate_payloads = collect_candidate_rule_payloads(payload, rulebox_snapshot)
    normalized: List[Dict[str, object]] = []
    warnings: List[str] = []
    seen = set()
    for index, raw_rule in enumerate(candidate_payloads):
        try:
            rule = GraphInferenceRule.from_dict(raw_rule)
        except ValueError as error:
            warnings.append("candidate[" + str(index) + "] invalid: " + str(error))
            continue
        rule_payload = rule.to_dict()
        rule_payload["enabled"] = False
        rule_id = str(rule_payload.get("rule_id") or "")
        if rule_id in existing_rule_ids:
            warnings.append("candidate rule duplicates current RuleBox rule_id: " + rule_id)
        if rule_id in seen:
            warnings.append("candidate rule repeated in experiment payload: " + rule_id)
            continue
        seen.add(rule_id)
        for derivation in rule_payload.get("derivations") or []:
            if not isinstance(derivation, dict):
                continue
            if not str(derivation.get("decision_stage") or derivation.get("decisionStage") or "").strip():
                warnings.append("candidate rule " + rule_id + " has derivation without decision_stage")
            if not str(derivation.get("evidence_role") or derivation.get("evidenceRole") or derivation.get("polarity") or "").strip():
                warnings.append("candidate rule " + rule_id + " has derivation without evidence_role")
        normalized.append(rule_payload)
    return normalized, sorted(set(warnings))


def collect_candidate_rule_payloads(payload: Dict[str, object], rulebox_snapshot: Dict[str, object]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for key in ["rules", "candidateRules"]:
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(dict(item) for item in value if isinstance(item, dict))
    if isinstance(payload.get("proposedRule"), dict):
        rows.append(dict(payload.get("proposedRule") or {}))
    candidate_ids = clean_id_values(payload.get("candidateIds") or payload.get("candidateId"))
    if candidate_ids:
        for candidate in (rulebox_snapshot or {}).get("changeCandidates") or []:
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(candidate.get("id") or "").strip()
            proposed = candidate.get("proposedRule") if isinstance(candidate.get("proposedRule"), dict) else {}
            proposed_id = str(proposed.get("rule_id") or proposed.get("ruleId") or "").strip()
            if candidate_id in candidate_ids or proposed_id in candidate_ids:
                rows.append(dict(proposed))
    return rows


def clean_id_values(value: object) -> set:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    return {str(item or "").strip() for item in values if str(item or "").strip()}


def graph_rules_from_payloads(rules: Iterable[Dict[str, object]], force_enabled: bool = False) -> List[GraphInferenceRule]:
    result = []
    for payload in rules or []:
        if not isinstance(payload, dict):
            continue
        next_payload = dict(payload)
        if force_enabled:
            next_payload["enabled"] = True
        try:
            result.append(GraphInferenceRule.from_dict(next_payload))
        except ValueError:
            continue
    return result


def rulebox_metrics(rules: Iterable[Dict[str, object]]) -> Dict[str, object]:
    rows = [dict(item) for item in rules or [] if isinstance(item, dict)]
    rule_ids = []
    relation_types = []
    decision_stages = []
    tbox_classes = []
    for rule in rows:
        rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "").strip()
        if rule_id:
            rule_ids.append(rule_id)
        for derivation in rule.get("derivations") or []:
            if not isinstance(derivation, dict):
                continue
            relation_type = str(derivation.get("relation_type") or derivation.get("relationType") or "").strip()
            if relation_type:
                relation_types.append(relation_type)
            decision_stage = str(derivation.get("decision_stage") or derivation.get("decisionStage") or "").strip()
            if decision_stage:
                decision_stages.append(decision_stage)
            tbox_class = str(derivation.get("tbox_class") or derivation.get("tboxClass") or "").strip()
            if tbox_class:
                tbox_classes.append(tbox_class)
            for value in derivation.get("tbox_classes") or derivation.get("tboxClasses") or []:
                text = str(value or "").strip()
                if text:
                    tbox_classes.append(text)
    return {
        "ruleIds": sorted(set(rule_ids)),
        "ruleCount": len(rows),
        "enabledRuleCount": len([item for item in rows if item.get("enabled") is not False]),
        "conditionCount": sum(len(item.get("conditions") or []) for item in rows),
        "derivationCount": sum(len(item.get("derivations") or []) for item in rows),
        "relationTypes": sorted(set(relation_types)),
        "decisionStages": sorted(set(decision_stages)),
        "tboxClasses": sorted(set(tbox_classes)),
        "rulesHash": rulebox_rules_hash(rows),
    }


def inference_metrics_from_graph(graph: PortfolioOntology) -> Dict[str, object]:
    inference_entities = [
        item
        for item in graph.entities or []
        if str((item.properties or {}).get("ontologyBox") or "") == "InferenceBox"
    ]
    inference_relations = [
        item
        for item in graph.relations or []
        if str((item.properties or {}).get("ontologyBox") or "") == "InferenceBox"
    ]
    derived_relations = [item for item in inference_relations if item.relation_type not in TRACE_RELATION_TYPES]
    rule_ids = []
    relation_types = []
    decision_stages = []
    symbols = []
    for relation in derived_relations:
        props = relation.properties or {}
        relation_types.append(relation.relation_type)
        if str(props.get("ruleId") or "").strip():
            rule_ids.append(str(props.get("ruleId")).strip())
        if str(props.get("decisionStage") or "").strip():
            decision_stages.append(str(props.get("decisionStage")).strip())
    for entity in inference_entities:
        props = entity.properties or {}
        if str(props.get("symbol") or "").strip():
            symbols.append(str(props.get("symbol")).upper().strip())
    sample = build_ontology_quality_sample(graph, source="ontology-lab")
    return {
        "entityCount": len(inference_entities),
        "relationCount": len(inference_relations),
        "derivedRelationCount": len(derived_relations),
        "traceCount": len([item for item in inference_entities if item.kind == "inference-trace"]),
        "ruleIds": sorted(set(rule_ids)),
        "relationTypes": sorted(set(relation_types)),
        "decisionStages": sorted(set(decision_stages)),
        "symbols": sorted(set(symbols)),
        "quality": {
            "overallState": sample.overall_state,
            "dataState": sample.data_state,
            "contextState": sample.context_state,
            "reasoningState": sample.reasoning_state,
            "relationState": sample.relation_state,
            "validationState": sample.validation_state,
            "dataGapCount": sample.data_gap_count,
            "actionRequiredCount": sample.action_required_count,
        },
    }


def run_experiment_on_graph(
    facts_graph: PortfolioOntology,
    baseline_rules: Iterable[Dict[str, object]],
    candidate_rules: Iterable[Dict[str, object]],
) -> Dict[str, object]:
    baseline_metrics = inference_metrics_from_graph(facts_graph)
    candidate_metrics = inference_metrics_from_graph(facts_graph)
    candidate_rule_metrics = rulebox_metrics(candidate_rules)
    return {
        "portfolioId": facts_graph.portfolio_id,
        "status": "requires-typedb-materialization",
        "reason": "Local Python graph inference was removed. Candidate rules must be saved to TypeDB schema functions and materialized before inference deltas are available.",
        "baseline": baseline_metrics,
        "candidate": candidate_metrics,
        "delta": inference_metric_delta(baseline_metrics, candidate_metrics),
        "materialization": {
            "required": True,
            "engine": "typedb-native-schema-functions",
            "candidateRuleCount": int(candidate_rule_metrics.get("ruleCount") or 0),
            "candidateRuleIds": list(candidate_rule_metrics.get("ruleIds") or []),
        },
    }


def inference_metric_delta(baseline: Dict[str, object], candidate: Dict[str, object]) -> Dict[str, object]:
    return {
        "entityCount": int(candidate.get("entityCount") or 0) - int(baseline.get("entityCount") or 0),
        "relationCount": int(candidate.get("relationCount") or 0) - int(baseline.get("relationCount") or 0),
        "derivedRelationCount": int(candidate.get("derivedRelationCount") or 0) - int(baseline.get("derivedRelationCount") or 0),
        "traceCount": int(candidate.get("traceCount") or 0) - int(baseline.get("traceCount") or 0),
        "newRuleIds": sorted(set(candidate.get("ruleIds") or []) - set(baseline.get("ruleIds") or [])),
        "newRelationTypes": sorted(set(candidate.get("relationTypes") or []) - set(baseline.get("relationTypes") or [])),
        "newDecisionStages": sorted(set(candidate.get("decisionStages") or []) - set(baseline.get("decisionStages") or [])),
        "qualityStateChanged": (
            str((baseline.get("quality") or {}).get("overallState") or "")
            != str((candidate.get("quality") or {}).get("overallState") or "")
        ),
        "baselineQualityState": str((baseline.get("quality") or {}).get("overallState") or ""),
        "candidateQualityState": str((candidate.get("quality") or {}).get("overallState") or ""),
    }


def summarize_experiment_result(
    experiment: OntologyExperiment,
    baseline_rules: List[Dict[str, object]],
    graph_runs: List[Dict[str, object]],
) -> Dict[str, object]:
    candidate_metrics = rulebox_metrics(experiment.candidate_rules)
    baseline_metrics = rulebox_metrics(baseline_rules)
    aggregate_delta = aggregate_graph_deltas(graph_runs)
    readiness = promotion_readiness(experiment.validation_warnings, aggregate_delta, len(graph_runs))
    proposed_changes = ontology_change_proposal(candidate_metrics, baseline_metrics, aggregate_delta)
    recommendations = experiment_recommendations(
        experiment.validation_warnings,
        proposed_changes,
        aggregate_delta,
        len(graph_runs),
        readiness,
    )
    return {
        "status": "completed",
        "experimentId": experiment.experiment_id,
        "title": experiment.title,
        "hypothesis": experiment.hypothesis,
        "symbols": list(experiment.symbols or []),
        "sandbox": {
            "mutatedOperationalRuleBox": False,
            "mutatedTypeDB": False,
            "graphRunCount": len(graph_runs),
        },
        "rulebox": {
            "baseline": baseline_metrics,
            "candidate": candidate_metrics,
            "delta": {
                "ruleCount": int(candidate_metrics.get("ruleCount") or 0),
                "conditionCount": int(candidate_metrics.get("conditionCount") or 0),
                "derivationCount": int(candidate_metrics.get("derivationCount") or 0),
                "newRelationTypes": sorted(set(candidate_metrics.get("relationTypes") or []) - set(baseline_metrics.get("relationTypes") or [])),
                "newDecisionStages": sorted(set(candidate_metrics.get("decisionStages") or []) - set(baseline_metrics.get("decisionStages") or [])),
            },
        },
        "inference": {
            "aggregateDelta": aggregate_delta,
            "graphRuns": graph_runs,
        },
        "proposedOntologyChanges": proposed_changes,
        "promotionReadiness": readiness,
        "recommendations": recommendations,
        "findings": experiment_findings(experiment.validation_warnings, aggregate_delta, len(graph_runs), readiness),
        "validationWarnings": list(experiment.validation_warnings or []),
        "completedAt": utc_now_iso(),
    }


def ontology_change_proposal(
    candidate_metrics: Dict[str, object],
    baseline_metrics: Dict[str, object],
    aggregate_delta: Dict[str, object],
) -> Dict[str, object]:
    candidate_rule_ids = list(candidate_metrics.get("ruleIds") or [])
    candidate_relation_types = list(candidate_metrics.get("relationTypes") or [])
    candidate_decision_stages = list(candidate_metrics.get("decisionStages") or [])
    baseline_relation_types = set(baseline_metrics.get("relationTypes") or [])
    baseline_decision_stages = set(baseline_metrics.get("decisionStages") or [])
    return {
        "ruleIds": candidate_rule_ids,
        "relationTypes": candidate_relation_types,
        "newRelationTypes": sorted(set(candidate_relation_types) - baseline_relation_types),
        "decisionStages": candidate_decision_stages,
        "newDecisionStages": sorted(set(candidate_decision_stages) - baseline_decision_stages),
        "tboxClasses": list(candidate_metrics.get("tboxClasses") or []),
        "inferenceRuleIds": list(aggregate_delta.get("newRuleIds") or []),
        "inferenceDecisionStages": list(aggregate_delta.get("newDecisionStages") or []),
        "inferenceRelationTypes": list(aggregate_delta.get("newRelationTypes") or []),
    }


def experiment_recommendations(
    warnings: List[str],
    proposed_changes: Dict[str, object],
    aggregate_delta: Dict[str, object],
    graph_run_count: int,
    readiness: Dict[str, object],
) -> List[Dict[str, object]]:
    recommendations: List[Dict[str, object]] = []
    derived_count = int(aggregate_delta.get("derivedRelationCount") or 0)
    requires_typedb_materialization = int(aggregate_delta.get("requiresTypeDbMaterializationCount") or 0) > 0
    readiness_status = str(readiness.get("status") or "needs-review")
    rule_ids = list(proposed_changes.get("ruleIds") or [])
    relation_types = list(proposed_changes.get("relationTypes") or [])
    new_relation_types = list(proposed_changes.get("newRelationTypes") or [])
    new_decision_stages = sorted(
        set(proposed_changes.get("newDecisionStages") or [])
        | set(proposed_changes.get("inferenceDecisionStages") or [])
    )
    if graph_run_count <= 0:
        recommendations.append(
            recommendation_payload(
                "collect-abox-data",
                "high",
                "최근 ABox 스냅샷 확보 후 재실행",
                "실제 모니터 스냅샷이 없어 후보 규칙이 투자 그래프에서 만드는 차이를 비교하지 못했습니다.",
                "모니터링 스냅샷을 먼저 수집한 뒤 같은 실험을 다시 실행합니다.",
                proposed_changes,
                {"graphRunCount": graph_run_count, "derivedRelationDelta": derived_count},
            )
        )
    if warnings:
        recommendations.append(
            recommendation_payload(
                "fix-candidate-structure",
                "high",
                "후보 규칙 구조 경고 해결",
                "구조 경고 " + str(len(warnings)) + "개가 있어 운영 RuleBox 승격 전에 수정이 필요합니다.",
                "candidateRules의 condition, derivation, decision_stage, evidence_role을 보완합니다.",
                proposed_changes,
                {"warnings": list(warnings or [])[:6]},
            )
        )
    if derived_count > 0:
        promote = readiness_status == "promote-candidate"
        recommendations.append(
            recommendation_payload(
                "promote-rule" if promote else "review-rule-promotion",
                "high" if promote else "medium",
                "후보 RuleBox 규칙 " + ("승격" if promote else "승격 검토"),
                "샌드박스 비교에서 새 파생 관계 " + str(derived_count) + "개가 만들어졌습니다.",
                "운영 RuleBox에 " + (", ".join(rule_ids) if rule_ids else "후보 규칙") + " 추가를 검토합니다.",
                proposed_changes,
                {
                    "derivedRelationDelta": derived_count,
                    "promotionStatus": readiness_status,
                },
            )
        )
    elif graph_run_count > 0 and requires_typedb_materialization:
        recommendations.append(
            recommendation_payload(
                "run-typedb-materialization",
                "high",
                "TypeDB materialization으로 후보 규칙 검증",
                "Python 로컬 추론이 제거되어 후보 규칙의 실제 파생 관계 수는 TypeDB schema function 실행 후에만 확인할 수 있습니다.",
                "후보 규칙을 검토 승인한 뒤 TypeDB RuleBox에 저장하고 run_rulebox로 실제 InferenceBox 결과를 확인합니다.",
                proposed_changes,
                {
                    "graphRunCount": graph_run_count,
                    "derivedRelationDelta": derived_count,
                    "requiresTypeDbMaterializationCount": aggregate_delta.get("requiresTypeDbMaterializationCount"),
                },
            )
        )
    elif graph_run_count > 0:
        recommendations.append(
            recommendation_payload(
                "refine-rule-conditions",
                "medium",
                "후보 규칙 조건 보완",
                "현재 스냅샷에서는 후보 규칙이 새 파생 관계를 만들지 못했습니다.",
                "조건 임계값, 대상 심볼, 필요한 ABox 관계를 조정한 새 실험을 만듭니다.",
                proposed_changes,
                {"graphRunCount": graph_run_count, "derivedRelationDelta": derived_count},
            )
        )
    if new_relation_types:
        recommendations.append(
            recommendation_payload(
                "register-relation-types",
                "medium",
                "새 관계 타입 등록",
                "후보 규칙이 현재 RuleBox에 없는 관계 타입을 사용합니다.",
                "TBox/관계 카탈로그에 " + ", ".join(new_relation_types) + " 의미와 방향성을 추가합니다.",
                proposed_changes,
                {"newRelationTypes": new_relation_types},
            )
        )
    elif relation_types and derived_count > 0:
        recommendations.append(
            recommendation_payload(
                "reuse-existing-relation-types",
                "low",
                "기존 관계 타입 재사용",
                "새 relation type 없이 기존 관계 어휘로 의미 있는 추론 차이를 만들었습니다.",
                "관계 타입 추가보다 RuleBox 규칙과 decision stage 추가를 우선 검토합니다.",
                proposed_changes,
                {"relationTypes": relation_types},
            )
        )
    if new_decision_stages:
        recommendations.append(
            recommendation_payload(
                "register-decision-stages",
                "medium",
                "새 decision stage 등록",
                "후보 규칙이 기존 운영 추론에 없던 판단 단계를 만들었습니다.",
                "DecisionStage 카탈로그와 설명 문맥에 " + ", ".join(new_decision_stages) + "를 추가합니다.",
                proposed_changes,
                {"newDecisionStages": new_decision_stages},
            )
        )
    return recommendations[:6]


def recommendation_payload(
    recommendation_type: str,
    priority: str,
    title: str,
    reason: str,
    action: str,
    proposal: Dict[str, object],
    evidence: Dict[str, object],
) -> Dict[str, object]:
    seed = json.dumps(
        {
            "type": recommendation_type,
            "title": title,
            "proposal": proposal,
            "evidence": evidence,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "id": "ontology-rec-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12],
        "type": recommendation_type,
        "priority": priority,
        "title": title,
        "reason": reason,
        "action": action,
        "proposal": dict(proposal or {}),
        "evidence": dict(evidence or {}),
    }


def aggregate_graph_deltas(graph_runs: List[Dict[str, object]]) -> Dict[str, object]:
    result = {
        "entityCount": 0,
        "relationCount": 0,
        "derivedRelationCount": 0,
        "traceCount": 0,
        "qualityStateChangedCount": 0,
        "requiresTypeDbMaterializationCount": 0,
        "newRuleIds": [],
        "newRelationTypes": [],
        "newDecisionStages": [],
    }
    for run in graph_runs or []:
        materialization = run.get("materialization") if isinstance(run.get("materialization"), dict) else {}
        if materialization.get("required"):
            result["requiresTypeDbMaterializationCount"] += 1
        delta = run.get("delta") if isinstance(run.get("delta"), dict) else {}
        for key in ["entityCount", "relationCount", "derivedRelationCount", "traceCount"]:
            result[key] += int(delta.get(key) or 0)
        if bool(delta.get("qualityStateChanged")):
            result["qualityStateChangedCount"] += 1
        for key in ["newRuleIds", "newRelationTypes", "newDecisionStages"]:
            result[key].extend(str(item) for item in (delta.get(key) or []) if str(item or "").strip())
    for key in ["newRuleIds", "newRelationTypes", "newDecisionStages"]:
        result[key] = sorted(set(result[key]))
    return result


def promotion_readiness(warnings: List[str], aggregate_delta: Dict[str, object], graph_run_count: int) -> Dict[str, object]:
    if warnings:
        status = "needs-review"
        validation_state = "blocked"
        data_state = "partial" if graph_run_count > 0 else "insufficient"
    elif graph_run_count <= 0:
        status = "needs-data"
        validation_state = "conditional"
        data_state = "insufficient"
    elif int(aggregate_delta.get("requiresTypeDbMaterializationCount") or 0) > 0:
        status = "needs-materialization"
        validation_state = "conditional"
        data_state = "sufficient"
    elif int(aggregate_delta.get("derivedRelationCount") or 0) > 0:
        status = "promote-candidate"
        validation_state = "ready"
        data_state = "sufficient"
    else:
        status = "needs-review"
        validation_state = "conditional"
        data_state = "sufficient"
    return {
        "status": status,
        "validationState": validation_state,
        "dataState": data_state,
        "reason": readiness_reason(status),
    }


def readiness_reason(status: str) -> str:
    if status == "promote-candidate":
        return "후보 규칙이 샌드박스 그래프에서 새 추론 관계를 만들었고 구조 경고가 없습니다."
    if status == "needs-data":
        return "비교할 최근 ABox 스냅샷이 없어 구조 검증만 수행했습니다."
    if status == "needs-materialization":
        return "후보 규칙을 TypeDB에서 실행한 결과가 아직 없어 승격 판단을 보류합니다."
    return "후보 규칙을 승격하기 전에 경고, 데이터 커버리지, 추론 변화량을 검토해야 합니다."


def experiment_findings(
    warnings: List[str],
    aggregate_delta: Dict[str, object],
    graph_run_count: int,
    readiness: Dict[str, object],
) -> List[str]:
    findings = []
    if graph_run_count <= 0:
        findings.append("최근 모니터 스냅샷이 없어 실제 ABox 리플레이를 수행하지 못했습니다.")
    if int(aggregate_delta.get("derivedRelationCount") or 0) > 0:
        findings.append("후보 규칙이 새 파생 관계 " + str(aggregate_delta.get("derivedRelationCount")) + "개를 만들었습니다.")
    elif int(aggregate_delta.get("requiresTypeDbMaterializationCount") or 0) > 0:
        findings.append("Python 로컬 추론이 제거되어 후보 규칙의 파생 관계 수는 TypeDB materialization 후 확인합니다.")
    else:
        findings.append("후보 규칙이 현재 스냅샷에서는 새 파생 관계를 만들지 않았습니다.")
    if aggregate_delta.get("newRelationTypes"):
        findings.append("새 관계 타입: " + ", ".join(aggregate_delta.get("newRelationTypes") or []))
    if warnings:
        findings.append("구조 경고 " + str(len(warnings)) + "개가 있어 승격 전 검토가 필요합니다.")
    findings.append("승격 판단: " + str(readiness.get("status") or "needs-review"))
    return findings
