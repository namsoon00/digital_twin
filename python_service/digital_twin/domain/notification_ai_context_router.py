"""Route a full decision audit into one relevance-bounded AI decision core.

The full world snapshot remains persisted for audit.  This module only selects
facts already connected to the current TypeDB decision synthesis; it never
scores evidence or chooses an investment action.
"""

from __future__ import annotations

import json
from typing import Dict, Iterable, List, Tuple

from .notification_narrative import (
    build_decision_core_evidence_ledger,
    compact_narrative_claim_evidence_contract,
    narrative_claim_evidence_contract,
)
from .prompt_evidence_admission import assess_prompt_evidence


AI_DECISION_CONTEXT_ROUTE_VERSION = "notification-ai-context-route-v6"
AI_DECISION_CORE_VERSION = "investment-ai-decision-core-v5"

RESEARCH_INSIGHT_FACT_LABELS = (
    ("priceChangeRate", "가격 변화율"),
    ("ma20Distance", "20일 평균 괴리"),
    ("ma60Distance", "60일 평균 괴리"),
    ("profitLossRate", "보유 수익률"),
    ("foreignNetVolume", "외국인 순매수"),
    ("institutionNetVolume", "기관 순매수"),
    ("tradeStrength", "체결강도"),
    ("currentPrice", "현재가"),
    ("volumeRatio", "평균 대비 거래량"),
)


CORE_FACT_KEYS = (
    "currentPrice", "averagePrice", "profitLossRate", "profitLossRateDeltaPct",
    "quantity", "sellableQuantity", "marketValue", "positionWeight", "sectorWeight",
    "volume", "volumeRatio", "timeAdjustedVolumeRatio", "tradeStrength",
    "buyVolume", "sellVolume", "bidAskImbalance", "foreignNetVolume",
    "institutionNetVolume", "individualNetVolume", "smartMoneyNetVolume",
    "ma5", "ma20", "ma60", "ma5Distance", "ma20Distance", "ma60Distance",
    "ma20Slope", "ma60Slope", "priceChangeRate", "currency", "market",
    "macroDgs10", "macroDgs2", "macroDff", "macroYieldSpread10y2y",
    "macroDgs10DeltaBp", "macroDgs2DeltaBp", "macroYieldSpreadDeltaBp",
    "usdKrwRate", "usdKrwDeltaPct", "usdKrw7dDeltaPct",
    "btcPrice", "btcChange24h", "btcChange7d",
)

VALUATION_MARKERS = (
    "valuation", "fair_value", "fundamental", "financial", "earnings",
    "company_value", "기업가치", "밸류", "실적", "재무",
)
EXTERNAL_EVIDENCE_MARKERS = (
    "news", "disclosure", "filing", "research", "event_risk",
    "뉴스", "공시", "리서치",
)


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _clean(value: object, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[:max(1, int(limit or 1))]


def _sentence_text(value: object, limit: int = 320) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    candidate = text[: max(1, limit - 3)].rstrip()
    for separator in (". ", "다. ", "; ", " / ", " "):
        position = candidate.rfind(separator)
        if position >= max(24, int(limit * 0.55)):
            candidate = candidate[: position + (1 if separator != " " else 0)].rstrip()
            break
    return candidate.rstrip(".,;/ ") + "..."


def _selected(value: object, fields: Iterable[str]) -> Dict[str, object]:
    row = _mapping(value)
    return {
        key: row.get(key)
        for key in fields
        if row.get(key) not in (None, "", [], {})
    }


def _unique(values: Iterable[object], limit: int = 24) -> List[str]:
    rows: List[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            rows.append(text)
        if len(rows) >= max(1, int(limit or 1)):
            break
    return rows


def _unique_all(values: Iterable[object]) -> List[str]:
    rows: List[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            rows.append(text)
    return rows


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


def _bounded_detail(
    value: object,
    *,
    string_limit: int = 160,
    list_limit: int = 6,
    dict_limit: int = 16,
) -> object:
    """Bound audit detail without changing decision-contract identifiers."""

    if isinstance(value, dict):
        return {
            str(key): _bounded_detail(
                current,
                string_limit=string_limit,
                list_limit=list_limit,
                dict_limit=dict_limit,
            )
            for key, current in list(value.items())[: max(1, int(dict_limit or 1))]
            if current not in (None, "", [], {})
        }
    if isinstance(value, (list, tuple, set)):
        return [
            _bounded_detail(
                current,
                string_limit=string_limit,
                list_limit=list_limit,
                dict_limit=dict_limit,
            )
            for current in list(value)[: max(1, int(list_limit or 1))]
        ]
    if isinstance(value, str):
        return value[: max(1, int(string_limit or 1))]
    return value


def _bounded_detail_bytes(value: object, maximum_bytes: int) -> object:
    """Fit non-contract audit detail to a measured UTF-8 budget."""

    budget = max(96, int(maximum_bytes or 96))
    for string_limit, list_limit, dict_limit in (
        (120, 6, 12),
        (72, 4, 8),
        (40, 3, 5),
        (24, 2, 3),
    ):
        candidate = _bounded_detail(
            value,
            string_limit=string_limit,
            list_limit=list_limit,
            dict_limit=dict_limit,
        )
        if _json_bytes(candidate) <= budget:
            return candidate
    if isinstance(value, dict):
        return {
            "retainedKeys": [str(key) for key in list(value)[:4]],
            "detailOmitted": True,
        }
    if isinstance(value, (list, tuple, set)):
        return {
            "itemCount": len(value),
            "detailOmitted": True,
        }
    return _clean(value, max(12, budget // 3))


def _minimum_transition_detail(value: object) -> Dict[str, object]:
    source = _mapping(value)
    if not source:
        return {}
    row = _selected(
        source,
        (
            "version", "status", "material", "userObservable", "changeKind",
            "previousState", "currentState", "occurredAt", "observedAt",
            "reason", "summary", "transitionReason", "hypothesisId",
            "sourceEventNames", "kinds", "reasons", "materialRevisionKeys",
            "matchedConditions", "changedFields", "facts",
        ),
    )
    evidence_delta = _mapping(source.get("evidenceDelta"))
    if evidence_delta:
        row["evidenceDelta"] = _bounded_detail_bytes(evidence_delta, 420)
    return _bounded_detail_bytes(row, 900)


def _minimum_evidence_ledger_rows(value: object, limit: int) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        row = _selected(
            item,
            (
                "evidenceId", "role", "kind", "label", "value", "source",
                "sourceAsOf", "fetchedAt", "freshness", "ruleIds",
                "hypothesisIds", "relatedEvidenceIds", "sourceFactIds",
                "modelEvidenceIds", "sourceFeatureSnapshotId", "modelReleaseId",
                "featureSummary", "judgementEligible",
            ),
        )
        for key in (
            "ruleIds", "hypothesisIds", "relatedEvidenceIds", "sourceFactIds",
            "modelEvidenceIds",
        ):
            if key in row:
                row[key] = _unique_all(row.get(key) or [])[:8]
        for key in ("value", "featureSummary"):
            if key in row:
                row[key] = _bounded_detail_bytes(row[key], 420)
        if row:
            rows.append(row)
        if len(rows) >= max(1, int(limit or 1)):
            break
    return rows


def _minimum_hypothesis_row(value: object) -> Dict[str, object]:
    item = _mapping(value)
    row = _selected(
        item,
        (
            "hypothesisId", "templateId", "familyId", "stance",
            "candidateAction", "horizon", "predictionTarget",
            "expectedDirection", "expectedOutcome", "outcomeMetric",
            "evidenceState", "verificationStatus", "approvalStatus",
            "scopeState", "inferenceGenerationId", "decisionEligible",
            "referenceOnly", "researchOnly",
        ),
    )
    row["claim"] = _sentence_text(item.get("claim"), 100)
    for key in (
        "supportingRuleIds", "supportingEvidenceIds", "counterEvidenceIds",
    ):
        values = _unique_all(item.get(key) or [])
        if values:
            row[key] = values
    invalidation = _unique(item.get("invalidationConditions") or [], 1)
    if invalidation:
        row["invalidationConditions"] = invalidation
    claim_contract = _selected(
        item.get("claimContract"),
        (
            "claimContractId", "claimType", "expectedDirection",
            "decisionAuthority",
        ),
    )
    if claim_contract:
        row["claimContract"] = claim_contract
    qualification = _selected(
        item.get("qualification"),
        (
            "status", "decisionAuthority", "reason", "actionReturnAvailable",
            "decisiveOutcomeCount", "directionalHitRate",
            "averageActionAdjustedReturnPct",
        ),
    )
    if qualification:
        if qualification.get("reason"):
            qualification["reason"] = _sentence_text(
                qualification.get("reason"),
                120,
            )
        row["qualification"] = qualification
    return row


def _lineage_rows(value: object, fields: Iterable[str], limit: int) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        row = _selected(item, fields)
        if row:
            rows.append(row)
        if len(rows) >= max(1, int(limit or 1)):
            break
    return rows


def _minimum_reasoning_lineage(value: object, *, emergency: bool = False) -> Dict[str, object]:
    """Keep the selected subject proof when the AI request is budget constrained."""

    lineage = _mapping(value)
    if not lineage:
        return {}
    identity = _mapping(lineage.get("identity"))
    integrity = _mapping(lineage.get("integrity"))
    proof = _mapping(lineage.get("proof"))
    selected_rule_id = str(identity.get("selectedRuleId") or "").strip()
    raw_rules = [item for item in proof.get("rules") or [] if isinstance(item, dict)]
    selected_rules = [
        item for item in raw_rules
        if str(item.get("id") or "").strip() == selected_rule_id or item.get("selected") is True
    ]
    retained_rules = selected_rules[:1] or raw_rules[:1]
    retained_rule_ids = {
        str(item.get("id") or "").strip()
        for item in retained_rules
        if str(item.get("id") or "").strip()
    }
    raw_traces = [item for item in proof.get("traces") or [] if isinstance(item, dict)]
    retained_traces = [
        item for item in raw_traces
        if str(item.get("ruleId") or "").strip() in retained_rule_ids
    ][:1 if emergency else 2]
    retained_trace_ids = {
        str(item.get("id") or "").strip()
        for item in retained_traces
        if str(item.get("id") or "").strip()
    }
    raw_facts = [item for item in proof.get("facts") or [] if isinstance(item, dict)]
    retained_facts = [
        item for item in raw_facts
        if retained_rule_ids.intersection(str(entry or "").strip() for entry in item.get("ruleIds") or [])
        or retained_trace_ids.intersection(str(entry or "").strip() for entry in item.get("traceIds") or [])
    ][:2 if emergency else 4]
    retained_fact_ids = {
        str(item.get("id") or "").strip()
        for item in retained_facts
        if str(item.get("id") or "").strip()
    }
    raw_relations = [item for item in proof.get("relations") or [] if isinstance(item, dict)]
    retained_relations = [
        item for item in raw_relations
        if str(item.get("ruleId") or "").strip() in retained_rule_ids
        or str(item.get("traceId") or "").strip() in retained_trace_ids
        or str(item.get("source") or "").strip() in retained_fact_ids
    ][:1 if emergency else 3]
    fact_rows = _lineage_rows(
        retained_facts,
        (
            "id", "conditionId", "label", "kind", "field", "relationType",
            "role", "observedValue", "expected", "result", "source", "asOf",
            "freshnessStatus", "targetId", "sourceFactIds", "evidenceIds",
            "ruleIds", "traceIds", "targetProperties",
        ),
        2 if emergency else 4,
    )
    for item in fact_rows:
        for key in ("observedValue", "targetProperties"):
            if key in item:
                item[key] = _bounded_detail_bytes(item[key], 520)
        for key in ("sourceFactIds", "evidenceIds", "ruleIds", "traceIds"):
            if key in item:
                item[key] = _unique_all(item.get(key) or [])[:8]
    return {
        "version": lineage.get("version"),
        "status": lineage.get("status"),
        "judgementEligible": lineage.get("judgementEligible"),
        "identity": _selected(
            identity,
            (
                "subjectCaseId", "batchCaseId", "accountId", "symbol",
                "deploymentId", "releaseId", "releaseFingerprint",
                "tboxReleaseId", "tboxFingerprint",
                "ruleboxReleaseId", "ruleboxFingerprint",
                "modelSignalReleaseId", "promptReleaseId",
                "sourceAboxSnapshotId", "inferenceGenerationId",
                "candidateFingerprint", "selectedRuleId", "selectedHypothesisId",
                "eligibleHypothesisIds", "executionEligibleHypothesisIds",
                "referenceHypothesisIds",
            ),
        ),
        "integrity": {
            **_selected(integrity, ("state", "includedRuleEvaluationCount", "excludedForeignSubjectEvaluationCount", "invalidRuleEvaluationCount")),
            "issues": _lineage_rows(integrity.get("issues"), ("code", "state", "detail"), 1 if emergency else 3),
        },
        "proof": {
            **_selected(proof, ("sourceAboxSnapshotId", "inferenceGenerationId", "recordCompleteness")),
            "facts": fact_rows,
            "relations": _lineage_rows(
                retained_relations,
                ("id", "type", "label", "source", "target", "ruleId", "traceId", "polarity", "evidenceUsable"),
                1 if emergency else 3,
            ),
            "rules": _lineage_rows(
                retained_rules,
                ("id", "label", "description", "evidenceRole", "selected", "decisionEligible", "candidateAction", "traceIds", "relationIds"),
                1,
            ),
            "traces": _lineage_rows(
                retained_traces,
                ("id", "proofId", "ruleId", "label", "matched", "selected", "decisionEligible", "evidenceUsable", "matchedConditionIds", "evidenceRelationIds"),
                1 if emergency else 2,
            ),
        },
    }


def _minimum_research_reasoning_lineage(
    value: object,
    supporting_rule_ids: Iterable[object] = (),
) -> Dict[str, object]:
    """Keep a verified proof attestation without duplicating ledger facts."""

    lineage = _mapping(value)
    identity = _mapping(lineage.get("identity"))
    integrity = _mapping(lineage.get("integrity"))
    proof = _mapping(lineage.get("proof") or lineage.get("reasoning"))
    requested_rule_ids = set(_unique_all(supporting_rule_ids))
    raw_rules = [item for item in proof.get("rules") or [] if isinstance(item, dict)]
    available_rule_ids = {
        str(item.get("id") or item.get("ruleId") or "").strip()
        for item in raw_rules
        if str(item.get("id") or item.get("ruleId") or "").strip()
    }
    rule_ids = sorted(
        requested_rule_ids.intersection(available_rule_ids)
        or requested_rule_ids
        or available_rule_ids
    )[:12]
    traces = [
        item
        for item in proof.get("traces") or []
        if isinstance(item, dict)
        and (
            not rule_ids
            or str(item.get("ruleId") or "").strip() in rule_ids
        )
    ][:12]
    trace_ids = sorted({
        str(item.get("id") or item.get("traceId") or "").strip()
        for item in traces
        if str(item.get("id") or item.get("traceId") or "").strip()
    })
    relation_ids = sorted({
        str(item.get("id") or "").strip()
        for item in proof.get("relations") or []
        if isinstance(item, dict)
        and str(item.get("id") or "").strip()
        and (
            not rule_ids
            or str(item.get("ruleId") or "").strip() in rule_ids
            or str(item.get("traceId") or "").strip() in trace_ids
        )
    })[:12]
    issue_codes = _unique_all(
        item.get("code")
        for item in integrity.get("issues") or []
        if isinstance(item, dict)
    )[:6]
    return {
        "version": lineage.get("version"),
        "status": lineage.get("status"),
        "judgementEligible": False,
        "attestationVersion": "reasoning-lineage-attestation-v1",
        "identity": _selected(
            identity,
            (
                "subjectCaseId", "symbol", "sourceAboxSnapshotId",
                "inferenceGenerationId", "candidateSetId",
                "candidateFingerprint",
            ),
        ),
        "integrity": {
            **_selected(integrity, ("state", "label")),
            "issueCodes": issue_codes,
        },
        "proof": {
            **_selected(
                proof,
                (
                    "sourceAboxSnapshotId", "inferenceGenerationId",
                    "recordCompleteness",
                ),
            ),
            "ruleIds": rule_ids,
            "traceIds": trace_ids,
            "relationIds": relation_ids,
            "factCount": len([item for item in proof.get("facts") or [] if isinstance(item, dict)]),
            "ruleCount": len(raw_rules),
            "traceCount": len([item for item in proof.get("traces") or [] if isinstance(item, dict)]),
            "relationCount": len([item for item in proof.get("relations") or [] if isinstance(item, dict)]),
            "evidencePathAttested": bool(rule_ids and (trace_ids or relation_ids)),
        },
    }


def _minimum_research_review_core(value: object) -> Dict[str, object]:
    """Remove execution-only duplication while preserving research provenance."""

    core = _mapping(value)
    hypothesis_set = _mapping(core.get("hypothesisSet"))
    hypotheses = []
    for item in hypothesis_set.get("hypotheses") or []:
        if not isinstance(item, dict):
            continue
        qualification = _mapping(item.get("qualification"))
        claim_contract = _mapping(item.get("claimContract"))
        hypotheses.append({
            **_selected(
                item,
                (
                    "hypothesisId", "familyId", "stance", "candidateAction",
                    "horizon", "evidenceState", "researchOnly",
                ),
            ),
            "claim": _sentence_text(item.get("claim"), 120),
            "supportingRuleIds": _unique_all(item.get("supportingRuleIds") or []),
            "supportingEvidenceIds": _unique_all(
                item.get("supportingEvidenceIds") or []
            ),
            "counterEvidenceIds": _unique_all(
                item.get("counterEvidenceIds") or []
            ),
            "invalidationConditions": _unique(
                [
                    _sentence_text(value, 80)
                    for value in item.get("invalidationConditions") or []
                ],
                1,
            ),
            "claimContract": _selected(
                claim_contract,
                ("claimType", "decisionAuthority"),
            ),
            "qualification": {
                **_selected(
                    qualification,
                    (
                        "status", "actionReturnAvailable",
                        "decisiveOutcomeCount", "directionalHitRate",
                        "averageActionAdjustedReturnPct",
                    ),
                ),
                "reason": _sentence_text(qualification.get("reason"), 100),
            },
        })
    required_evidence_ids = {
        str(evidence_id or "").strip()
        for hypothesis in hypotheses
        for key in ("supportingEvidenceIds", "counterEvidenceIds")
        for evidence_id in hypothesis.get(key) or []
        if str(evidence_id or "").strip()
    }
    ledger = []
    contextual_count = 0
    for item in core.get("evidenceLedger") or []:
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("evidenceId") or "").strip()
        required = evidence_id in required_evidence_ids
        if not required and contextual_count >= 4:
            continue
        if not required:
            contextual_count += 1
        row = _selected(
            item,
            (
                "evidenceId", "role", "kind", "judgementEligible",
            ),
        )
        row["label"] = _sentence_text(item.get("label"), 32)
        if (
            str(row.get("kind") or "") in {"fact", "derived"}
            and item.get("value") not in (None, "")
            and not isinstance(item.get("value"), (dict, list, tuple))
        ):
            row["value"] = item.get("value")
        ledger.append(row)
    retained_observed_fact_count = len([
        item for item in ledger
        if str(item.get("kind") or "") in {"fact", "derived"}
    ])
    if retained_observed_fact_count < 2:
        existing_ids = {str(item.get("evidenceId") or "") for item in ledger}
        facts = _mapping(core.get("facts"))
        for key, label in RESEARCH_INSIGHT_FACT_LABELS:
            evidence_id = "fact:" + key
            value = facts.get(key)
            if evidence_id in existing_ids or value in (None, ""):
                continue
            ledger.append({
                "evidenceId": evidence_id,
                "role": "context",
                "kind": "derived",
                "label": label,
                "value": value,
                "judgementEligible": True,
            })
            existing_ids.add(evidence_id)
            retained_observed_fact_count += 1
            if retained_observed_fact_count >= 2:
                break
    claim_contract = compact_narrative_claim_evidence_contract()
    decision = _mapping(core.get("decision"))
    return {
        "schemaVersion": core.get("schemaVersion"),
        "reviewMode": core.get("reviewMode"),
        "notificationIntent": core.get("notificationIntent"),
        "subject": core.get("subject"),
        "question": core.get("question"),
        "decision": {
            "typeDbDecision": _selected(
                decision.get("typeDbDecision"),
                ("primaryAction", "judgementBlocked", "selectedRuleId"),
            ),
            "actionEnvelope": _selected(
                decision.get("actionEnvelope"),
                (
                    "executionAction", "executionDisposition", "allowedActions",
                    "blockedActions", "judgementBlocked", "selectedRuleId",
                ),
            ),
            "readiness": _selected(
                decision.get("readiness"),
                (
                    "state", "eligibleHypothesisCount", "eligibleFamilyCount",
                    "referenceHypothesisCount",
                ),
            ),
        },
        "reasoningTrigger": _minimum_transition_detail(
            core.get("reasoningTrigger") or {}
        ),
        "relationLifecycle": _minimum_transition_detail(
            core.get("relationLifecycle") or {}
        ),
        "facts": _selected(core.get("facts"), CORE_FACT_KEYS),
        "hypothesisSet": {
            **_selected(
                hypothesis_set,
                (
                    "subjectSymbol", "inferenceGenerationId", "comparisonMode",
                ),
            ),
            "decisionEvidenceSummary": _selected(
                hypothesis_set.get("decisionEvidenceSummary"),
                (
                    "totalHypothesisCount", "eligibleHypothesisCount",
                    "eligibleFamilyCount", "referenceHypothesisCount",
                ),
            ),
            "hypotheses": hypotheses,
        },
        "reasoningLineage": _minimum_research_reasoning_lineage(
            core.get("reasoningLineage") or {},
            [
                rule_id
                for hypothesis in hypotheses
                for rule_id in hypothesis.get("supportingRuleIds") or []
            ],
        ),
        "evidenceLedger": ledger,
        "narrativeClaimContract": claim_contract,
        "dataLimits": [
            _bounded_detail_bytes(item, 260)
            for item in list(core.get("dataLimits") or [])[:3]
        ],
        "routingAudit": {
            "version": AI_DECISION_CONTEXT_ROUTE_VERSION,
            "status": "minimum-research-review-contract",
        },
    }


def _reasoning_lineage_for_core(
    value: object,
    *,
    subject_symbol: object,
    selected_rule_id: object,
) -> Dict[str, object]:
    """Validate and route one immutable TBox-to-AI subject lineage."""

    lineage = _mapping(value)
    if not lineage:
        return {}
    identity = _mapping(lineage.get("identity"))
    integrity = _mapping(lineage.get("integrity"))
    proof = _mapping(lineage.get("proof") or lineage.get("reasoning"))
    expected_symbol = str(subject_symbol or "").strip().upper()
    lineage_symbol = str(identity.get("symbol") or "").strip().upper()
    expected_rule_id = str(selected_rule_id or identity.get("selectedRuleId") or "").strip()
    issues = [
        _selected(item, ("code", "state", "detail", "expected", "actual", "ruleId", "ruleIds"))
        for item in integrity.get("issues") or []
        if isinstance(item, dict)
    ][:8]
    blocked = str(integrity.get("state") or "").strip().lower() == "blocked"
    if expected_symbol and lineage_symbol != expected_symbol:
        blocked = True
        issues.append({
            "code": "AI_LINEAGE_SUBJECT_MISMATCH",
            "state": "blocked",
            "detail": "AI 입력 종목과 저장 추론 계보의 종목이 일치하지 않습니다.",
            "expected": expected_symbol,
            "actual": lineage_symbol,
        })
    source_abox_snapshot_id = str(identity.get("sourceAboxSnapshotId") or "").strip()
    inference_generation_id = str(identity.get("inferenceGenerationId") or "").strip()
    if (
        source_abox_snapshot_id
        and str(proof.get("sourceAboxSnapshotId") or "").strip() != source_abox_snapshot_id
    ) or (
        inference_generation_id
        and str(proof.get("inferenceGenerationId") or "").strip() != inference_generation_id
    ):
        blocked = True
        issues.append({
            "code": "AI_LINEAGE_SNAPSHOT_MISMATCH",
            "state": "blocked",
            "detail": "ABox 스냅샷 또는 추론 세대가 AI 입력 계보와 일치하지 않습니다.",
        })

    raw_rules = [item for item in proof.get("rules") or [] if isinstance(item, dict)]
    if expected_rule_id and not any(str(item.get("id") or "").strip() == expected_rule_id for item in raw_rules):
        blocked = True
        issues.append({
            "code": "AI_LINEAGE_SELECTED_RULE_MISSING",
            "state": "blocked",
            "detail": "선택 규칙의 저장 증거를 AI 입력 계보에서 찾지 못했습니다.",
            "ruleId": expected_rule_id,
        })

    if blocked:
        proof = {
            "sourceAboxSnapshotId": proof.get("sourceAboxSnapshotId"),
            "inferenceGenerationId": proof.get("inferenceGenerationId"),
            "recordCompleteness": "blocked",
            "facts": [],
            "relations": [],
            "rules": [],
            "traces": [],
        }
    return {
        "version": lineage.get("version"),
        "status": "integrity-blocked" if blocked else lineage.get("status"),
        "judgementEligible": not blocked,
        "identity": _selected(
            identity,
            (
                "subjectCaseId", "batchCaseId", "accountId", "symbol",
                "deploymentId", "releaseId", "releaseFingerprint",
                "tboxReleaseId", "tboxFingerprint",
                "ruleboxReleaseId", "ruleboxFingerprint",
                "modelSignalReleaseId", "promptReleaseId", "sourceAboxSnapshotId",
                "inferenceGenerationId", "synthesisId", "candidateSetId",
                "candidateFingerprint", "selectedRuleId", "selectedHypothesisId",
                "eligibleHypothesisIds", "executionEligibleHypothesisIds",
                "referenceHypothesisIds",
            ),
        ),
        "integrity": {
            **_selected(
                integrity,
                (
                    "state", "label", "includedRuleEvaluationCount",
                    "excludedForeignSubjectEvaluationCount", "invalidRuleEvaluationCount",
                ),
            ),
            "state": "blocked" if blocked else integrity.get("state") or "warning",
            "issues": issues,
        },
        "proof": {
            **_selected(proof, ("sourceAboxSnapshotId", "inferenceGenerationId", "recordCompleteness")),
            "limitations": [_sentence_text(item, 180) for item in list(proof.get("limitations") or [])[:6]],
            "facts": _lineage_rows(
                proof.get("facts"),
                (
                    "id", "conditionId", "label", "kind", "field", "relationType",
                    "role", "observedValue", "expected", "result", "source", "asOf",
                    "freshnessStatus", "targetId", "targetKind", "targetProperties",
                    "sourceFactIds", "evidenceIds", "ruleIds", "traceIds",
                ),
                12,
            ),
            "relations": _lineage_rows(
                proof.get("relations"),
                ("id", "type", "label", "source", "target", "targetLabel", "ruleId", "traceId", "polarity", "freshnessStatus", "evidenceUsable"),
                12,
            ),
            "rules": _lineage_rows(
                proof.get("rules"),
                ("id", "label", "description", "evidenceRole", "selected", "decisionEligible", "candidateAction", "traceIds", "relationIds", "knowledgeBasis"),
                8,
            ),
            "traces": _lineage_rows(
                proof.get("traces"),
                ("id", "proofId", "ruleId", "label", "matched", "selected", "decisionEligible", "evidenceUsable", "matchedConditionIds", "evidenceRelationIds"),
                8,
            ),
        },
        "hypothesisObservations": _lineage_rows(
            lineage.get("hypothesisObservations"),
            ("hypothesisId", "claimContractId", "selectionSource", "qualification", "observationState"),
            8,
        ),
    }


def _rule_linked_fact_keys(rules: List[Dict[str, object]], drivers: List[Dict[str, object]]) -> List[str]:
    keys: List[str] = []
    for rule in rules:
        evidence_state = _mapping(rule.get("evidenceState"))
        keys.extend(evidence_state.get("appliedFactFields") or [])
        for requirement in rule.get("ruleRequiredFacts") or []:
            text = str(requirement or "").strip()
            if ":field:" in text.lower():
                keys.append(text.lower().split(":field:", 1)[1].split(":", 1)[0])
            elif text:
                keys.append(text)
    for driver in drivers:
        keys.extend(driver.get("dataKeys") or [])
    return _unique(keys, 48)


def _active_rule_rows(inference: Dict[str, object], envelope: Dict[str, object]) -> List[Dict[str, object]]:
    hypothesis_set = _mapping(inference.get("hypothesisSet"))
    hypotheses = [item for item in hypothesis_set.get("hypotheses") or [] if isinstance(item, dict)]
    linked_ids = _unique([
        envelope.get("selectedRuleId"),
        *(envelope.get("drivingRuleIds") or []),
        *(envelope.get("blockingRuleIds") or []),
        *(envelope.get("constraintRuleIds") or []),
        *(envelope.get("supportRuleIds") or []),
        *[
            rule_id
            for hypothesis in hypotheses
            for rule_id in hypothesis.get("supportingRuleIds") or []
        ],
    ], 24)
    rows: List[Dict[str, object]] = []
    seen_rule_ids = set()
    for item in inference.get("activeRules") or []:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("ruleId") or "").strip()
        if not rule_id or rule_id in seen_rule_ids:
            continue
        evidence_state = _mapping(item.get("evidenceState"))
        if evidence_state.get("evidenceUsableForJudgement") is False:
            continue
        if str(evidence_state.get("inferenceEligibilityStatus") or "eligible") != "eligible":
            continue
        if linked_ids and rule_id not in linked_ids:
            continue
        row = _selected(
            item,
            (
                "ruleId", "label", "relationType", "evidenceRole", "dataState",
                "reviewLevel", "ruleRequiredFacts", "claimContract",
                "qualification", "knowledgeBasis",
            ),
        )
        claim_contract = _mapping(item.get("claimContract"))
        if claim_contract:
            row["claimContract"] = _selected(
                claim_contract,
                (
                    "claimContractId", "claimType", "statement", "predictionTarget",
                    "expectedDirection", "expectedOutcome", "defaultHorizon",
                    "outcomeMetric", "falsificationContract", "decisionAuthority",
                ),
            )
        qualification = _mapping(item.get("qualification"))
        if qualification:
            row["qualification"] = _selected(
                qualification,
                (
                    "status", "decisionAuthority", "reason", "decisiveOutcomeCount",
                    "directionalHitRate", "averageActionAdjustedReturnPct",
                ),
            )
        knowledge_basis = _mapping(item.get("knowledgeBasis"))
        if knowledge_basis:
            row["knowledgeBasis"] = _selected(
                knowledge_basis,
                (
                    "ruleKind", "theoryFamily", "thesisFamily",
                    "decisionEligibility", "evidenceIndependenceKey",
                    "validationStatus", "decisionAuthority",
                ),
            )
        applied = _unique(
            evidence_state.get("appliedFactFields")
            or item.get("appliedFactFields")
            or item.get("applied_fact_fields")
            or [],
            12,
        )
        if applied:
            row["appliedFactFields"] = applied
        if row:
            seen_rule_ids.add(rule_id)
            rows.append(row)
    priority = {rule_id: index for index, rule_id in enumerate(linked_ids)}
    rows.sort(key=lambda item: (priority.get(str(item.get("ruleId") or ""), len(priority)), str(item.get("ruleId") or "")))
    return rows[:8]


def _hypothesis_rows(inference: Dict[str, object]) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    source = _mapping(inference.get("hypothesisSet"))
    source_summary = _mapping(source.get("decisionEvidenceSummary"))
    eligible_ids = set(_unique_all(
        source.get("eligibleHypothesisIds")
        or source_summary.get("eligibleHypothesisIds")
        or []
    ))
    reference_ids = set(_unique_all(
        source.get("referenceHypothesisIds")
        or source_summary.get("referenceHypothesisIds")
        or []
    ))
    lineage = _mapping(inference.get("lineage"))
    lineage_proof = _mapping(lineage.get("proof") or lineage.get("reasoning"))
    lineage_rule_ids = {
        str(item.get("id") or item.get("ruleId") or "").strip()
        for item in lineage_proof.get("rules") or []
        if isinstance(item, dict)
        and str(item.get("id") or item.get("ruleId") or "").strip()
    }
    known_rule_ids = {
        str(item.get("ruleId") or "").strip()
        for item in inference.get("activeRules") or []
        if (
            isinstance(item, dict)
            and str(item.get("ruleId") or "").strip()
            and _mapping(item.get("evidenceState")).get("evidenceUsableForJudgement") is not False
            and str(_mapping(item.get("evidenceState")).get("inferenceEligibilityStatus") or "eligible") == "eligible"
        )
    } | lineage_rule_ids
    rows: List[Dict[str, object]] = []
    for item in source.get("hypotheses") or []:
        if not isinstance(item, dict) or not str(item.get("hypothesisId") or "").strip():
            continue
        row = _selected(
            item,
            (
                "hypothesisId", "templateId", "familyId", "causalSignature",
                "familySource", "mergedRuleCount", "stance", "evidenceState",
                "verificationStatus", "approvalStatus", "scopeState", "horizon",
                "marketHypothesisId", "accountHypothesisOverlayId",
                "candidateAction", "predictionTarget", "expectedDirection",
                "expectedOutcome", "outcomeMetric", "falsificationContract",
                "inferenceGenerationId", "claimContract", "qualification",
            ),
        )
        row["claim"] = _sentence_text(item.get("claim"), 320)
        for key in (
            "supportingRuleIds", "supportingEvidenceIds", "counterEvidenceIds",
            "causalPathIds", "requiredEvidenceTypes", "invalidationConditions",
        ):
            values = (
                _unique_all(item.get(key) or [])
                if key in {
                    "supportingRuleIds",
                    "supportingEvidenceIds",
                    "counterEvidenceIds",
                }
                else _unique(item.get(key) or [], 5)
            )
            if values:
                row[key] = values
        if not row.get("supportingRuleIds"):
            template_id = str(row.get("templateId") or "").strip()
            template_rule_id = (
                template_id.split("hypothesis-template:", 1)[1]
                if template_id.startswith("hypothesis-template:")
                else ""
            )
            if template_rule_id in known_rule_ids:
                row["supportingRuleIds"] = [template_rule_id]
        if not set(row.get("supportingRuleIds") or []).intersection(known_rule_ids):
            continue
        hypothesis_id = str(row.get("hypothesisId") or "").strip()
        row["decisionEligible"] = hypothesis_id in eligible_ids
        row["referenceOnly"] = hypothesis_id in reference_ids
        row["researchOnly"] = bool(
            hypothesis_id in reference_ids and hypothesis_id not in eligible_ids
        )
        rows.append(row)
        if len(rows) >= 12:
            break
    available_count = len(rows)
    try:
        required_minimum = max(1, int(float(str(source.get("minimumComparisonCount") or 1))))
    except (TypeError, ValueError):
        required_minimum = 1
    metadata = _selected(
        source,
        (
            "hypothesisSetId", "questionId", "subjectSymbol", "inferenceGenerationId",
            "scopeVersion", "createdAt", "comparisonMode", "reviewHypothesisIds",
            "eligibleHypothesisIds", "referenceHypothesisIds",
        ),
    )
    metadata["comparisonRequired"] = bool(rows)
    metadata["minimumComparisonCount"] = min(required_minimum, available_count) if available_count else 0
    metadata["requiredMinimumComparisonCount"] = required_minimum
    retained_ids = {
        str(item.get("hypothesisId") or "").strip()
        for item in rows
        if str(item.get("hypothesisId") or "").strip()
    }
    retained_eligible_ids = sorted(retained_ids.intersection(eligible_ids))
    retained_reference_ids = sorted(retained_ids.intersection(reference_ids))
    metadata["decisionEvidenceSummary"] = {
        "totalHypothesisCount": available_count,
        "eligibleHypothesisCount": len(retained_eligible_ids),
        "eligibleFamilyCount": len({
            str(item.get("familyId") or item.get("causalSignature") or item.get("hypothesisId") or "").strip()
            for item in rows
            if str(item.get("hypothesisId") or "").strip() in set(retained_eligible_ids)
        }),
        "referenceHypothesisCount": len(retained_reference_ids),
        "eligibleHypothesisIds": retained_eligible_ids,
        "referenceHypothesisIds": retained_reference_ids,
    }
    return metadata, rows


def _evidence_assertion_rows(
    inference: Dict[str, object],
    hypotheses: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    referenced_ids = {
        str(evidence_id or "").strip()
        for hypothesis in hypotheses
        for key in ("supportingEvidenceIds", "counterEvidenceIds")
        for evidence_id in hypothesis.get(key) or []
        if str(evidence_id or "").strip()
    }
    rows = []
    for item in inference.get("evidenceAssertions") or []:
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("evidenceId") or "").strip()
        if not evidence_id or (referenced_ids and evidence_id not in referenced_ids):
            continue
        row = _selected(
            item,
            (
                "version", "evidenceId", "ruleId", "label", "kind", "polarity",
                "value", "source", "sourceAsOf", "fetchedAt", "freshness",
                "relationType", "conditionId", "evidenceIndependenceKey",
                "relatedFactIds", "sourceFactIds", "modelEvidenceIds",
                "sourceFeatureSnapshotId", "modelReleaseId", "featureSummary",
                "judgementEligible",
            ),
        )
        if row:
            rows.append(row)
    return rows[:32]


def _market_evidence_profile(value: object, facts: Dict[str, object]) -> Dict[str, object]:
    profile = _mapping(value)
    if not profile:
        return {}
    capabilities = {}
    for key, raw in sorted(_mapping(profile.get("capabilities")).items()):
        state = _selected(raw, ("state", "freshnessStatus", "latencyStatus", "judgementEvidenceUsable"))
        if str(state.get("state") or "").lower() in {"notapplicable", "not-applicable"}:
            continue
        if state:
            capabilities[str(key)] = state
    observable = [
        key for key in _unique(profile.get("observableFollowUpFields") or [], 24)
        if key in facts
    ]
    payload = _selected(
        profile,
        (
            "profileKey", "label", "market", "currency", "dataState",
            "judgementEvidenceUsable", "requiredCapabilities", "confirmationCapabilities",
        ),
    )
    if observable:
        payload["observableFollowUpFields"] = observable
    if capabilities:
        payload["capabilities"] = capabilities
    return payload


def _relation_facts(current: Dict[str, object], rules: List[Dict[str, object]], drivers: List[Dict[str, object]]) -> Dict[str, object]:
    facts = _mapping(current.get("relationFacts"))
    casefold_keys = {str(key).casefold(): key for key in facts}
    requested = list(CORE_FACT_KEYS)
    for key in _rule_linked_fact_keys(rules, drivers):
        original = casefold_keys.get(str(key).casefold())
        if original:
            requested.append(original)
    payload = {
        key: facts.get(key)
        for key in _unique(requested, 40)
        if facts.get(key) not in (None, "", [], {})
        and not isinstance(facts.get(key), (dict, list))
    }
    market_profile = _market_evidence_profile(facts.get("marketEvidenceProfile"), facts)
    if market_profile:
        payload["marketEvidenceProfile"] = market_profile
    return payload


def _temporal_evidence(current: Dict[str, object]) -> Dict[str, object]:
    summary = _mapping(current.get("temporalEvidenceSummary"))
    matched_keys = _unique(summary.get("matchedWindowKeys") or [], 12)
    matched = {
        key.upper() for key in matched_keys
    }
    windows = []
    for item in current.get("temporalWindows") or []:
        if not isinstance(item, dict):
            continue
        window_key = str(item.get("windowKey") or "").upper().strip()
        if window_key not in matched:
            continue
        windows.append(_selected(
            item,
            (
                "windowKey", "lookbackDays", "lookbackMinutes", "sampleCount",
                "hasSufficientHistory", "startPrice", "currentPrice", "priceChangePct",
                "drawdownFromPeakPct", "reboundFromTroughPct", "priceVelocityChangePct",
                "volumeRatioEnd", "tradeStrengthEnd", "bidAskImbalanceEnd",
                "smartMoneyDataState", "smartMoneyNetLatest", "smartMoneyNetCumulative",
                "smartMoneyNetAmountCumulative", "smartMoneyTradingValueRatioPct",
                "smartMoneyFlowPersistenceRatio", "smartMoneyFlowAcceleration",
                "smartMoneyFlowDirection", "smartMoneyFlowBasis",
            ),
        ))
    return {
        "loadedWindowCount": summary.get("loadedWindowCount") or len(current.get("temporalWindows") or []),
        "matchedWindowCount": len(windows),
        "matchedWindowKeys": matched_keys,
        "windows": windows[:8],
        "evidenceRole": "rule-matched-only",
    }


def _marker_relevant(rules: List[Dict[str, object]], hypotheses: List[Dict[str, object]], markers: Tuple[str, ...]) -> bool:
    text = " ".join(
        str(value or "")
        for row in [*rules, *hypotheses]
        for value in row.values()
        if isinstance(value, str)
    ).casefold()
    return any(marker.casefold() in text for marker in markers)


def _company_context(current: Dict[str, object], rules: List[Dict[str, object]], hypotheses: List[Dict[str, object]], facts: Dict[str, object]) -> Tuple[Dict[str, object], Dict[str, object]]:
    company = _mapping(current.get("companyContext"))
    if not company:
        return {}, {}
    relevant = bool(facts.get("valuationDecisionEligible")) or _marker_relevant(rules, hypotheses, VALUATION_MARKERS)
    profile = _selected(company.get("profile"), ("sector", "industry", "country", "exchange"))
    coverage = _selected(company.get("coverage"), ("dataState", "officialSource", "financialPeriods", "valuationFields"))
    if not relevant:
        return {}, {
            "role": "reference-only",
            "company": _selected(company, ("symbol", "companyName", "judgmentUse")),
            "profile": profile,
            "coverage": coverage,
        }
    financials = _mapping(company.get("latestFinancials"))
    financial_fields = (
        "period", "revenue", "revenueGrowthPct", "operatingIncome",
        "operatingIncomeGrowthPct", "operatingMarginPct", "netIncome",
        "netIncomeGrowthPct", "freeCashFlow",
    )
    return {
        **_selected(company, ("symbol", "companyName", "factRevision", "materialRevision", "judgmentUse")),
        "profile": profile,
        "valuation": _selected(
            company.get("valuation"),
            (
                "peRatio", "forwardPE", "pbr", "pegRatio", "trailingEPS",
                "returnOnEquityPct", "dividendYieldPct", "enterpriseToEbitda",
            ),
        ),
        "latestFinancials": {
            "annual": [_selected(item, financial_fields) for item in list(financials.get("annual") or [])[:1] if isinstance(item, dict)],
            "quarterly": [_selected(item, financial_fields) for item in list(financials.get("quarterly") or [])[:1] if isinstance(item, dict)],
        },
        "coverage": coverage,
    }, {}


def _continuity_delta(value: object) -> Dict[str, object]:
    packet = _mapping(value)
    previous = _mapping(packet.get("previousDecision"))
    selected = _mapping(packet.get("selectedHypothesis"))
    followups = []
    for item in packet.get("followUpConditions") or []:
        if not isinstance(item, dict):
            continue
        row = _selected(
            item,
            ("field", "operator", "threshold", "purpose", "status", "observedValue", "onSatisfied"),
        )
        if row.get("onSatisfied"):
            row["onSatisfied"] = _sentence_text(row.get("onSatisfied"), 120)
        followups.append(row)
    previous_payload = _selected(previous, ("action", "decisionReadiness", "decidedAt"))
    previous_summary = _sentence_text(previous.get("decisionSummary"), 180)
    if previous_summary:
        previous_payload["summary"] = previous_summary
    payload = {
        "status": packet.get("status"),
        "previousDecision": previous_payload,
        "previousSelectedHypothesisId": selected.get("hypothesisId") or previous.get("selectedHypothesisId"),
        "followUpConditions": followups[:2],
        "observationState": _selected(packet.get("observationState"), ("userAction", "outcome", "followUp", "causalityClaimed")),
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _external_evidence(
    brief: Dict[str, object],
    rules: List[Dict[str, object]],
    hypotheses: List[Dict[str, object]],
) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    if not _marker_relevant(rules, hypotheses, EXTERNAL_EVIDENCE_MARKERS):
        return [], {"evaluatedCount": 0, "eligibleCount": 0, "reasonCounts": {}}
    evidence = _mapping(brief.get("evidence"))
    reference_at = _mapping(brief.get("subject")).get("referenceDate")
    linked_ids = {
        str(value or "")
        for hypothesis in hypotheses
        for key in ("supportingEvidenceIds", "counterEvidenceIds")
        for value in hypothesis.get(key) or []
        if str(value or "")
    }
    rows: List[Dict[str, object]] = []
    seen = set()
    evaluated_count = 0
    eligible_count = 0
    reason_counts: Dict[str, int] = {}
    excluded_ids: List[str] = []

    def append_row(row: Dict[str, object], directly_linked: bool) -> None:
        nonlocal evaluated_count, eligible_count
        identity = str(row.get("evidenceId") or row.get("title") or row.get("reportName") or "").strip()
        if not identity or identity.casefold() in seen:
            return
        seen.add(identity.casefold())
        evaluated_count += 1
        admission = assess_prompt_evidence(
            row,
            kind=row.get("kind"),
            published_at=row.get("publishedAt") or row.get("receiptDate") or row.get("seenDate"),
            observed_at=row.get("observedAt"),
            now=reference_at,
            directly_linked=directly_linked,
        ).to_dict()
        if not admission.get("promptEligible"):
            excluded_ids.append(identity)
            for reason in admission.get("reasonCodes") or []:
                reason_counts[str(reason)] = int(reason_counts.get(str(reason)) or 0) + 1
            return
        eligible_count += 1
        disclosure_analysis = _mapping(row.get("disclosureAnalysis"))
        if disclosure_analysis:
            row["disclosureAnalysis"] = {
                "status": disclosure_analysis.get("status"),
                "version": disclosure_analysis.get("version"),
                "summary": disclosure_analysis.get("summary"),
                "impactSummary": disclosure_analysis.get("impactSummary"),
                "uncertaintySummary": disclosure_analysis.get("uncertaintySummary"),
                "confirmedFacts": list(disclosure_analysis.get("confirmedFacts") or [])[:4],
                "materialNumbers": list(disclosure_analysis.get("materialNumbers") or [])[:12],
                "documentDates": list(disclosure_analysis.get("documentDates") or [])[:8],
                "watchItems": list(disclosure_analysis.get("watchItems") or [])[:4],
            }
        row["evidenceUse"] = "action" if directly_linked and admission.get("usage") == "decision" else "rule-scoped-reference"
        row["promptAdmission"] = admission
        rows.append(row)

    for item in evidence.get("researchEvidence") or []:
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("evidenceId") or "")
        directly_linked = bool(evidence_id and evidence_id in linked_ids)
        append_row(
            _selected(
                item,
                (
                    "evidenceId", "kind", "title", "summary", "evidenceRole", "polarity",
                    "validationState", "dataState", "source", "publishedAt", "observedAt", "url",
                    "sourceTrustState", "investmentJudgmentEligible", "verificationStatus",
                    "entityResolutionStatus", "decisionInlineEligible", "displayEligible",
                    "alertEligible", "reasoningEligible", "officialDocumentState",
                    "documentVerified", "analysisReady", "reportName", "receiptDate",
                    "sourceAsOf", "sourceRevision", "documentHash", "disclosureAnalysis",
                    "documentVerificationState", "documentAnalysisState",
                    "evidenceEligibilityState",
                ),
            ),
            directly_linked,
        )
        if len(rows) >= 3:
            break
    for item in evidence.get("newsHeadlines") or []:
        if isinstance(item, dict) and len(rows) < 3:
            legacy_news = _selected(
                item,
                (
                    "evidenceId", "title", "summary", "stockImpactLabel", "domain",
                    "seenDate", "publishedAt", "observedAt", "url",
                    "investmentJudgmentEligible", "decisionInlineEligible", "reasoningEligible",
                ),
            )
            legacy_news["kind"] = "news"
            append_row(legacy_news, False)
    disclosure = _mapping(evidence.get("disclosure"))
    if disclosure and len(rows) < 3:
        legacy_disclosure = _selected(
            disclosure,
            (
                "evidenceId", "reportName", "receiptDate", "provider", "url",
                "validationState", "dataState", "investmentJudgmentEligible",
                "officialDocumentState", "documentVerified", "analysisReady",
            ),
        )
        legacy_disclosure["kind"] = "disclosure"
        append_row(legacy_disclosure, False)
    return [row for row in rows if row][:3], {
        "evaluatedCount": evaluated_count,
        "eligibleCount": eligible_count,
        "excludedCount": max(0, evaluated_count - eligible_count),
        "reasonCounts": dict(sorted(reason_counts.items())),
        "excludedEvidenceIds": excluded_ids[:5],
    }


def _portfolio_policy(brief: Dict[str, object]) -> Dict[str, object]:
    scope = _mapping(brief.get("decisionPolicyScope"))
    if str(scope.get("portfolioRebalancePolicy") or "").lower() not in {"included", "true", "1"}:
        return {}
    account_policy = _mapping(brief.get("accountPolicy"))
    lifecycle = _mapping(account_policy.get("portfolioLifecycle"))
    mandate = _selected(
        lifecycle.get("mandate"),
        (
            "profile", "max_position_weight_pct", "max_sector_weight_pct",
            "fx_exposure_review_pct", "cash_floor_pct", "loss_budget_pct",
        ),
    )
    rebalance_state = _selected(
        lifecycle.get("rebalanceState"),
        ("status", "breachKeys", "maximumNotionalBySymbol", "revision"),
    )
    exposure = _mapping(lifecycle.get("exposureSnapshot"))
    metrics = [
        _selected(
            item,
            ("exposure_type", "key", "ratio_pct", "policy_limit_pct", "policyDeltaPct"),
        )
        for item in exposure.get("metrics") or []
        if isinstance(item, dict)
    ]
    payload = {
        "actionPolicy": account_policy.get("actionPolicy"),
        "mandate": mandate,
        "exposureSnapshot": {
            **_selected(exposure, ("observedAt", "dataState")),
            "metrics": [item for item in metrics if item][:12],
        },
        "rebalanceState": rebalance_state,
        "rebalanceProposal": _selected(lifecycle.get("rebalanceProposal"), ("status", "reason", "revision")),
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _missing_data(value: object, company_relevant: bool, linked_fact_keys: List[str]) -> List[Dict[str, object]]:
    rows = []
    linked = " ".join(linked_fact_keys).casefold()
    for item in value or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get(key) or "") for key in ("key", "label", "effect")).casefold()
        valuation_gap = any(marker.casefold() in text for marker in VALUATION_MARKERS)
        generally_relevant = any(token in text for token in ("price", "volume", "fresh", "수급", "가격", "거래", "신선"))
        linked_relevant = any(key.casefold() in text for key in linked_fact_keys if key)
        if valuation_gap and not company_relevant:
            continue
        if linked and not (valuation_gap or generally_relevant or linked_relevant):
            continue
        rows.append(_selected(item, ("key", "label", "effect", "state")))
    return [row for row in rows if row][:4]


def route_notification_ai_decision_context(brief: Dict[str, object]) -> Tuple[Dict[str, object], Dict[str, object]]:
    current = _mapping(brief.get("currentSituation"))
    inference = _mapping(brief.get("inference"))
    decision_state = _mapping(brief.get("decisionState"))
    envelope = _mapping(decision_state.get("actionEnvelope"))
    hypothesis_metadata, hypotheses = _hypothesis_rows(inference)
    evidence_assertions = _evidence_assertion_rows(inference, hypotheses)
    rules = _active_rule_rows(inference, envelope)
    drivers = []
    seen_drivers = set()
    for item in inference.get("decisionDrivers") or []:
        if not isinstance(item, dict):
            continue
        row = _selected(item, ("category", "direction", "evidenceRole", "label", "dataKeys", "summary"))
        if row.get("summary"):
            row["summary"] = _sentence_text(row.get("summary"), 160)
        key = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        if row and key not in seen_drivers:
            seen_drivers.add(key)
            drivers.append(row)
        if len(drivers) >= 4:
            break
    facts = _relation_facts(current, rules, drivers)
    linked_fact_keys = _rule_linked_fact_keys(rules, drivers)
    company, company_reference = _company_context(current, rules, hypotheses, _mapping(current.get("relationFacts")))
    external_evidence, evidence_admission_audit = _external_evidence(brief, rules, hypotheses)
    temporal = _temporal_evidence(current)
    data_coverage = _mapping(brief.get("dataCoverage"))
    assessment = _mapping(brief.get("assessmentBundle"))
    reasoning_trigger = _mapping(current.get("reasoningDeliveryTrigger"))
    relation_lifecycle = _mapping(current.get("relationLifecycleTransition"))
    reasoning_lineage = _reasoning_lineage_for_core(
        inference.get("lineage"),
        subject_symbol=_mapping(brief.get("subject")).get("symbol"),
        selected_rule_id=envelope.get("selectedRuleId"),
    )
    core = {
        "schemaVersion": AI_DECISION_CORE_VERSION,
        "reviewMode": brief.get("reviewMode"),
        "notificationIntent": brief.get("notificationIntent"),
        "question": _selected(brief.get("question"), ("questionId", "intent", "horizon", "text")),
        "subject": _selected(brief.get("subject"), ("symbol", "name", "market", "targetRole", "referenceDate")),
        "decision": {
            "previousAction": _mapping(decision_state.get("previousFinalDecision")).get("action"),
            "previousInsight": _mapping(
                _mapping(decision_state.get("previousInvestmentInsight")).get("insightAssessment")
            ),
            "precomputedActionCandidate": decision_state.get("precomputedActionCandidate"),
            "typeDbDecision": _selected(decision_state.get("decision"), ("primaryAction", "decisionEffect", "judgementBlocked", "targetRole")),
            "actionEnvelope": _selected(envelope, (
                "status", "investmentViewAction", "executionAction", "executionDisposition",
                "allowedActions", "blockedActions", "aiAllowedActions",
                "judgementBlocked", "selectedRuleId", "drivingRuleIds", "blockingRuleIds",
                "portfolioConstraintRuleIds", "executionConstraintRuleIds", "dataQualityRuleIds",
                "assessmentBundleVersion", "targetRole",
            )),
            "transition": _selected(decision_state.get("decisionTransition"), ("kind", "changed", "material", "previousAction", "currentAction", "summary")),
            "readiness": _selected(
                decision_state.get("systemReadiness"),
                (
                    "status", "state", "evaluated", "minimumEligibleFamilyCount",
                    "eligibleHypothesisCount", "eligibleFamilyCount",
                    "referenceHypothesisCount", "selectedCoreInferenceEligible",
                ),
            ),
            "investmentOpinionStatus": _mapping(assessment.get("investmentOpinion")).get("status"),
            "executionReadinessStatus": _mapping(assessment.get("executionReadiness")).get("status"),
            "recommendedPlanStatus": _mapping(assessment.get("recommendedPlan")).get("status"),
        },
        "continuityDelta": _continuity_delta(brief.get("decisionContinuity")),
        "reasoningTrigger": reasoning_trigger,
        "relationLifecycle": relation_lifecycle,
        "facts": facts,
        "temporalEvidence": temporal,
        "companyEvidence": company,
        "rules": rules,
        "reasoningLineage": reasoning_lineage,
        "hypothesisSet": {
            **hypothesis_metadata,
            "hypotheses": hypotheses,
        },
        "externalEvidence": external_evidence,
        "background": company_reference,
        "dataLimits": _missing_data(data_coverage.get("missingData"), bool(company), linked_fact_keys),
        "policyScope": _selected(brief.get("decisionPolicyScope"), ("name", "portfolioRebalancePolicy")),
        "portfolioPolicy": _portfolio_policy(brief),
    }
    core["evidenceLedger"] = build_decision_core_evidence_ledger(
        facts=facts,
        rules=rules,
        hypotheses=hypotheses,
        evidence_assertions=evidence_assertions,
        temporal=temporal,
        external_evidence=external_evidence,
        data_limits=core.get("dataLimits") or [],
        reference_date=_mapping(brief.get("subject")).get("referenceDate"),
    )
    if reasoning_trigger:
        core["evidenceLedger"].insert(0, {
            "evidenceId": "transition:reasoning-trigger",
            "role": "context",
            "kind": "decision-transition",
            "label": "현재 TypeDB 추론을 시작한 검증된 시장·근거 변화",
            "value": reasoning_trigger,
            "source": ", ".join(reasoning_trigger.get("sourceEventNames") or [])
            or "reasoning-source-event",
            "sourceAsOf": reasoning_trigger.get("observedAt")
            or _mapping(brief.get("subject")).get("referenceDate"),
            "freshness": "source-bound",
            "ruleIds": [],
            "hypothesisIds": [],
            "judgementEligible": True,
            "detail": "",
        })
    if relation_lifecycle:
        core["evidenceLedger"].insert(0, {
            "evidenceId": "transition:relation-lifecycle",
            "role": "context",
            "kind": "decision-transition",
            "label": "TypeDB 가설 관계의 현재 수명주기 변화",
            "value": relation_lifecycle,
            "source": "TypeDB",
            "sourceAsOf": relation_lifecycle.get("occurredAt")
            or _mapping(brief.get("subject")).get("referenceDate"),
            "freshness": "inference-generation-bound",
            "ruleIds": [],
            "hypothesisIds": [],
            "judgementEligible": True,
            "detail": "",
        })
    transition = _mapping(_mapping(core.get("decision")).get("transition"))
    if transition and str(transition.get("kind") or "").strip():
        core["evidenceLedger"].insert(0, {
            "evidenceId": "transition:decision",
            "role": "context",
            "kind": "decision-transition",
            "label": "저장된 이전 판단과 현재 판단의 비교",
            "value": transition,
            "source": "decision-history",
            "sourceAsOf": _mapping(brief.get("subject")).get("referenceDate"),
            "freshness": "snapshot-bound",
            "ruleIds": [],
            "hypothesisIds": [],
            "judgementEligible": True,
            "detail": "",
        })
    core["narrativeClaimContract"] = narrative_claim_evidence_contract(
        core["evidenceLedger"]
    )
    core = {key: value for key, value in core.items() if value not in (None, "", [], {})}
    route_audit = {
        "version": AI_DECISION_CONTEXT_ROUTE_VERSION,
        "status": "routed",
        "included": {
            "hypothesisCount": len(hypotheses),
            "ruleCount": len(rules),
            "factCount": len(facts),
            "matchedTemporalWindowCount": len(temporal.get("windows") or []),
            "externalEvidenceCount": len(external_evidence),
            "actionExternalEvidenceCount": len([
                item for item in external_evidence if item.get("evidenceUse") == "action"
            ]),
            "referenceExternalEvidenceCount": len([
                item for item in external_evidence if item.get("evidenceUse") != "action"
            ]),
            "companyEvidence": bool(company),
            "companyReferenceOnly": bool(company_reference),
            "continuityDelta": bool(core.get("continuityDelta")),
            "evidenceLedgerCount": len(core.get("evidenceLedger") or []),
            "evidenceAssertionCount": len(evidence_assertions),
            "reasoningLineage": bool(reasoning_lineage),
            "reasoningLineageIntegrity": _mapping(reasoning_lineage.get("integrity")).get("state"),
        },
        "excluded": {
            "unmatchedTemporalWindowCount": max(0, len(current.get("temporalWindows") or []) - len(temporal.get("windows") or [])),
            "referenceHypothesisCount": len(_mapping(inference.get("hypothesisSet")).get("referenceHypotheses") or []),
            "nonApplicableCapabilityCount": len([
                item for item in _mapping(_mapping(current.get("relationFacts")).get("marketEvidenceProfile")).get("unavailableCapabilities") or []
                if isinstance(item, dict) and str(item.get("state") or "").lower() in {"notapplicable", "not-applicable"}
            ]),
            "unlinkedResearchEvidenceCount": max(0, len(_mapping(brief.get("evidence")).get("researchEvidence") or []) - len(external_evidence)),
        },
        "evidenceAdmission": evidence_admission_audit,
        "fullDecisionBriefRetainedForAudit": True,
    }
    core["routingAudit"] = route_audit
    return core, route_audit


def _is_research_review_core(value: object) -> bool:
    core = _mapping(value)
    comparison_mode = str(
        _mapping(core.get("hypothesisSet")).get("comparisonMode") or ""
    ).strip().lower()
    review_mode = str(core.get("reviewMode") or "").strip().lower()
    notification_intent = str(
        core.get("notificationIntent") or ""
    ).strip().lower()
    return bool(
        comparison_mode == "research-only"
        or review_mode == "context-narrative"
        or notification_intent in {"context-observation", "review-observation"}
    )


def fit_notification_ai_decision_core(core: Dict[str, object], budget_bytes: int) -> Dict[str, object]:
    """Reduce reference detail without truncating the hypothesis evidence contract."""

    budget = max(6 * 1024, int(budget_bytes or 6 * 1024))
    fitted = json.loads(json.dumps(core, ensure_ascii=False, default=str))
    required_evidence_ids = {
        evidence_id
        for item in _mapping(fitted.get("hypothesisSet")).get("hypotheses") or []
        if isinstance(item, dict)
        for key in ("supportingEvidenceIds", "counterEvidenceIds")
        for evidence_id in _unique_all(item.get(key) or [])
    }

    def compact_ledger(limit: int) -> List[Dict[str, object]]:
        rows = [
            item for item in fitted.get("evidenceLedger") or []
            if isinstance(item, dict)
        ]
        required_rows = [
            item for item in rows
            if str(item.get("evidenceId") or "") in required_evidence_ids
        ]
        target = max(max(1, int(limit or 1)), len(required_rows))
        selected_ids = {
            str(item.get("evidenceId") or "") for item in required_rows
        }
        for item in rows:
            evidence_id = str(item.get("evidenceId") or "")
            if len(selected_ids) >= target:
                break
            if evidence_id:
                selected_ids.add(evidence_id)
        return [
            item for item in rows
            if str(item.get("evidenceId") or "") in selected_ids
        ]

    if _json_bytes(fitted) <= budget:
        return fitted
    if _is_research_review_core(fitted):
        research_core = _minimum_research_review_core(fitted)
        if _json_bytes(research_core) <= budget:
            return research_core
    fitted.pop("background", None)
    compact_routing_audit = _mapping(fitted.get("routingAudit"))
    fitted["routingAudit"] = {
        "version": compact_routing_audit.get("version"),
        "status": "reference-trimmed",
    }
    fitted["evidenceLedger"] = [
        _selected(
            item,
            (
                "evidenceId", "role", "kind", "label", "value", "source",
                "sourceAsOf", "fetchedAt", "freshness", "ruleIds",
                "hypothesisIds", "relatedEvidenceIds", "sourceFactIds",
                "modelEvidenceIds", "sourceFeatureSnapshotId", "modelReleaseId",
                "featureSummary", "judgementEligible",
            ),
        )
        for item in compact_ledger(10)
        if isinstance(item, dict)
    ]
    fitted["narrativeClaimContract"] = narrative_claim_evidence_contract(
        fitted["evidenceLedger"]
    )
    if _json_bytes(fitted) <= budget:
        fitted["routingAudit"]["status"] = "reference-trimmed"
        return fitted
    fitted["externalEvidence"] = list(fitted.get("externalEvidence") or [])[:2]
    fitted["decisionDrivers"] = list(fitted.get("decisionDrivers") or [])[:4]
    if _json_bytes(fitted) <= budget:
        fitted["routingAudit"]["status"] = "reference-trimmed"
        return fitted
    for rule in fitted.get("rules") or []:
        if isinstance(rule, dict):
            rule["evidence"] = list(rule.get("evidence") or [])[:1]
    fitted["externalEvidence"] = list(fitted.get("externalEvidence") or [])[:1]
    fitted["evidenceLedger"] = compact_ledger(6)
    fitted["narrativeClaimContract"] = narrative_claim_evidence_contract(
        fitted["evidenceLedger"]
    )
    if _json_bytes(fitted) <= budget:
        fitted["routingAudit"]["status"] = "reference-trimmed"
        return fitted
    fitted["hypothesisSet"]["hypotheses"] = [
        {
            **_selected(item, (
                "hypothesisId", "templateId", "familyId", "stance", "candidateAction",
                "predictionTarget", "expectedDirection", "expectedOutcome", "outcomeMetric",
                "evidenceState", "verificationStatus", "approvalStatus", "scopeState", "horizon",
                "inferenceGenerationId", "decisionEligible", "referenceOnly", "researchOnly",
                "claimContract", "qualification",
            )),
            "claim": _sentence_text(item.get("claim"), 140),
            "supportingRuleIds": _unique_all(item.get("supportingRuleIds") or []),
            "supportingEvidenceIds": _unique_all(item.get("supportingEvidenceIds") or []),
            "counterEvidenceIds": _unique_all(item.get("counterEvidenceIds") or []),
            "invalidationConditions": _unique(item.get("invalidationConditions") or [], 1),
        }
        for item in fitted.get("hypothesisSet", {}).get("hypotheses") or []
        if isinstance(item, dict)
    ]
    for item in fitted["hypothesisSet"]["hypotheses"]:
        item["claimContract"] = _selected(
            _mapping(item.get("claimContract")),
            ("claimContractId", "claimType", "statement", "expectedDirection", "decisionAuthority"),
        )
        item["qualification"] = _selected(
            _mapping(item.get("qualification")),
            (
                "status", "decisionAuthority", "reason", "actionReturnAvailable",
                "decisiveOutcomeCount", "directionalHitRate",
                "averageActionAdjustedReturnPct",
            ),
        )
    fitted["evidenceLedger"] = compact_ledger(4)
    fitted["narrativeClaimContract"] = narrative_claim_evidence_contract(
        fitted["evidenceLedger"]
    )
    fitted["dataLimits"] = list(fitted.get("dataLimits") or [])[:3]
    fitted["reasoningLineage"] = _minimum_reasoning_lineage(
        fitted.get("reasoningLineage")
    )
    if _json_bytes(fitted) <= budget:
        fitted["routingAudit"]["status"] = "decision-contract-compacted"
        return fitted

    # A retry deliberately runs with a smaller prompt. Preserve the complete
    # hypothesis identity surface while reducing verbose current-state blocks
    # to the fields required to compare action, evidence, continuity, and
    # valuation. The full decision brief remains in the immutable audit store.
    facts = _mapping(fitted.get("facts"))
    fitted["facts"] = _selected(facts, CORE_FACT_KEYS)
    decision = _mapping(fitted.get("decision"))
    fitted["decision"] = {
        **_selected(
            decision,
            (
                "previousAction", "precomputedActionCandidate",
                "investmentOpinionStatus", "executionReadinessStatus",
                "recommendedPlanStatus",
            ),
        ),
        "typeDbDecision": _selected(
            decision.get("typeDbDecision"),
            ("primaryAction", "judgementBlocked", "selectedRuleId"),
        ),
        "actionEnvelope": _selected(
            decision.get("actionEnvelope"),
            (
                "status", "investmentViewAction", "executionAction",
                "executionDisposition", "allowedActions", "blockedActions",
                "aiAllowedActions",
                "judgementBlocked", "selectedRuleId", "drivingRuleIds",
                "executionConstraintRuleIds", "dataQualityRuleIds", "targetRole",
            ),
        ),
        "transition": _selected(
            decision.get("transition"),
            ("kind", "changed", "material", "previousAction", "currentAction", "summary"),
        ),
        "readiness": _selected(
            decision.get("readiness"),
            (
                "status", "state", "evaluated", "eligibleHypothesisCount",
                "eligibleFamilyCount", "selectedCoreInferenceEligible",
            ),
        ),
    }
    continuity = _mapping(fitted.get("continuityDelta"))
    previous = _mapping(continuity.get("previousDecision"))
    fitted["continuityDelta"] = {
        "status": continuity.get("status"),
        "previousDecision": {
            **_selected(previous, ("action", "decisionReadiness", "decidedAt")),
            "summary": _sentence_text(previous.get("summary"), 96),
        },
        "previousSelectedHypothesisId": continuity.get("previousSelectedHypothesisId"),
        "followUpConditions": [{
            **_selected(
                item,
                ("field", "operator", "threshold", "purpose", "status"),
            ),
            "onSatisfied": _sentence_text(item.get("onSatisfied"), 80),
        } for item in list(continuity.get("followUpConditions") or [])[:1]
          if isinstance(item, dict)],
    }
    company = _mapping(fitted.get("companyEvidence"))
    fitted["companyEvidence"] = {
        **_selected(
            company,
            ("symbol", "companyName", "profile", "valuation", "coverage"),
        ),
    }
    hypothesis_set = _mapping(fitted.get("hypothesisSet"))
    fitted["hypothesisSet"] = {
        **_selected(
            hypothesis_set,
            (
                "hypothesisSetId", "questionId", "subjectSymbol",
                "inferenceGenerationId", "scopeVersion", "comparisonRequired",
                "minimumComparisonCount", "requiredMinimumComparisonCount",
                "decisionEvidenceSummary", "comparisonMode", "reviewHypothesisIds",
                "eligibleHypothesisIds", "referenceHypothesisIds",
            ),
        ),
        "hypotheses": [
            _minimum_hypothesis_row(item)
            for item in list(hypothesis_set.get("hypotheses") or [])
            if isinstance(item, dict)
        ],
    }
    fitted["rules"] = [
        {
            **_selected(
                item,
                ("ruleId", "label", "evidenceRole", "appliedFactFields"),
            ),
            "claimContract": _selected(
                _mapping(item.get("claimContract")),
                (
                    "claimContractId", "thesisFamily", "expectedDirection",
                    "decisionAuthority",
                ),
            ),
            "qualification": _selected(
                _mapping(item.get("qualification")),
                ("status", "decisionAuthority"),
            ),
        }
        for item in list(fitted.get("rules") or [])[:3]
        if isinstance(item, dict)
    ]
    fitted["reasoningTrigger"] = _minimum_transition_detail(
        fitted.get("reasoningTrigger") or {},
    )
    fitted["relationLifecycle"] = _minimum_transition_detail(
        fitted.get("relationLifecycle") or {},
    )
    temporal = _mapping(fitted.get("temporalEvidence"))
    fitted["temporalEvidence"] = {
        **_selected(
            temporal,
            (
                "loadedWindowCount", "matchedWindowCount", "matchedWindowKeys",
                "evidenceRole",
            ),
        ),
        "windows": [
            _bounded_detail_bytes(item, 600)
            for item in list(temporal.get("windows") or [])[:3]
            if isinstance(item, dict)
        ],
    }
    fitted["companyEvidence"] = _bounded_detail_bytes(
        fitted.get("companyEvidence") or {},
        1200,
    )
    fitted["externalEvidence"] = [
        _bounded_detail_bytes(item, 1200)
        for item in list(fitted.get("externalEvidence") or [])[:1]
        if isinstance(item, dict)
    ]
    fitted["decisionDrivers"] = [
        _bounded_detail_bytes(item, 700)
        for item in list(fitted.get("decisionDrivers") or [])[:2]
        if isinstance(item, dict)
    ]
    fitted["dataLimits"] = [
        _bounded_detail_bytes(item, 420)
        for item in list(fitted.get("dataLimits") or [])[:2]
    ]
    fitted["portfolioPolicy"] = _bounded_detail_bytes(
        fitted.get("portfolioPolicy") or {},
        1000,
    )
    fitted["reasoningLineage"] = _minimum_reasoning_lineage(
        fitted.get("reasoningLineage"),
        emergency=True,
    )
    fitted["evidenceLedger"] = _minimum_evidence_ledger_rows(
        compact_ledger(3),
        max(3, len(required_evidence_ids)),
    )
    fitted["narrativeClaimContract"] = compact_narrative_claim_evidence_contract()
    fitted["routingAudit"] = {
        "status": "minimum-decision-contract",
    }
    if _json_bytes(fitted) <= budget:
        return fitted
    if _is_research_review_core(fitted):
        research_core = _minimum_research_review_core(fitted)
        if _json_bytes(research_core) <= budget:
            return research_core
    raise ValueError(
        "AI decision core cannot preserve TypeDB hypotheses within "
        + str(budget)
        + " bytes (minimum contract requires "
        + str(_json_bytes(fitted))
        + " bytes)"
    )
