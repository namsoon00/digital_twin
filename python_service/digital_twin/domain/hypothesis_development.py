"""Governed lifecycle for turning an AI hypothesis into a validated rule candidate."""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List

from .portfolio import utc_now_iso


HYPOTHESIS_DEVELOPMENT_STATUSES = {
    "proposed",
    "screening",
    "needs-data",
    "rejected",
    "compiled",
    "validating",
    "validated",
    "contradicted",
    "needs-revision",
    "approval-required",
    "deployed",
    "observing",
    "strengthened",
    "weakened",
    "invalidated",
    "retired",
    "rolled-back",
    "blocked",
}

TERMINAL_HYPOTHESIS_DEVELOPMENT_STATUSES = {
    "rejected",
    "retired",
    "rolled-back",
}

NON_CAUSAL_PROPOSAL_TERMS = {
    "evidence sufficiency",
    "insufficient evidence",
    "temporary co-movement",
    "data gap",
    "verification check",
    "근거 충분성",
    "근거 부족",
    "자료 부족",
    "일시적 동행",
    "검증 확인",
}


def clean_text(value: object, limit: int = 2000) -> str:
    text = " ".join(str(value or "").split())
    if limit > 3 and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def clean_list(values: Iterable[object], limit: int = 100, item_limit: int = 500) -> List[str]:
    source = values if isinstance(values, (list, tuple, set)) else ([] if values in (None, "") else [values])
    result: List[str] = []
    seen = set()
    for value in source or []:
        text = clean_text(value, item_limit)
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
        if len(result) >= limit:
            break
    return result


def hypothesis_development_fingerprint(
    account_id: object,
    symbol: object,
    claim: object,
    causal_path: Iterable[object],
) -> str:
    seed = json.dumps(
        {
            "accountId": clean_text(account_id, 191).casefold(),
            "symbol": clean_text(symbol, 64).upper(),
            "claim": clean_text(claim, 2000).casefold(),
            "causalPath": [item.casefold() for item in clean_list(causal_path, 20, 500)],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def hypothesis_case_id(fingerprint: str) -> str:
    return "hypothesis-case:" + str(fingerprint or "")[:24]


def validation_gate(
    gate_id: str,
    label: str,
    status: str = "pending",
    detail: str = "",
    blocking: bool = True,
    evidence: Dict[str, object] = None,
) -> Dict[str, object]:
    return {
        "id": clean_text(gate_id, 96),
        "label": clean_text(label, 160),
        "status": clean_text(status or "pending", 40),
        "detail": clean_text(detail, 1000),
        "blocking": bool(blocking),
        "evidence": dict(evidence or {}),
    }


def default_validation_gates() -> List[Dict[str, object]]:
    return [
        validation_gate("structure", "가설 구조"),
        validation_gate("evidence", "근거 품질"),
        validation_gate("deduplication", "중복·기존 규칙"),
        validation_gate("typedb-preview", "TypeDB 후보 실행"),
        validation_gate("current-replay", "현재 ABox 재생"),
        validation_gate("historical-coverage", "과거 자료 범위"),
        validation_gate("holdout-observation", "제안 후 관측"),
        validation_gate("counterevidence", "반증 가능성"),
        validation_gate("decision-impact", "판단 영향", blocking=False),
        validation_gate("policy-safety", "정책 안전"),
    ]


def validation_gate_map(gates: Iterable[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    return {
        str(item.get("id") or ""): dict(item)
        for item in gates or []
        if isinstance(item, dict) and str(item.get("id") or "")
    }


def merged_validation_gates(
    current: Iterable[Dict[str, object]],
    updates: Iterable[Dict[str, object]],
) -> List[Dict[str, object]]:
    rows = validation_gate_map(current or default_validation_gates())
    order = [item["id"] for item in default_validation_gates()]
    for update in updates or []:
        if not isinstance(update, dict) or not str(update.get("id") or ""):
            continue
        gate_id = str(update.get("id"))
        rows[gate_id] = {**rows.get(gate_id, {}), **dict(update)}
        if gate_id not in order:
            order.append(gate_id)
    return [rows[item] for item in order if item in rows]


def validation_summary(gates: Iterable[Dict[str, object]]) -> Dict[str, object]:
    rows = [dict(item) for item in gates or [] if isinstance(item, dict)]
    blocking = [item for item in rows if bool(item.get("blocking", True))]
    blocked = [item for item in blocking if str(item.get("status") or "") in {"blocked", "failed", "contradicted"}]
    pending = [item for item in blocking if str(item.get("status") or "pending") in {"pending", "needs-data", "not-run"}]
    passed = [item for item in blocking if str(item.get("status") or "") == "passed"]
    status = "validated" if blocking and len(passed) == len(blocking) else ("blocked" if blocked else "pending")
    return {
        "status": status,
        "gateCount": len(rows),
        "blockingGateCount": len(blocking),
        "passedCount": len(passed),
        "pendingCount": len(pending),
        "blockedCount": len(blocked),
        "blockedGateIds": [str(item.get("id") or "") for item in blocked],
        "pendingGateIds": [str(item.get("id") or "") for item in pending],
    }


@dataclass
class HypothesisDevelopmentCase:
    case_id: str
    fingerprint: str
    account_id: str
    symbol: str
    title: str
    claim: str
    causal_path: List[str] = field(default_factory=list)
    supporting_evidence_ids: List[str] = field(default_factory=list)
    counter_evidence_ids: List[str] = field(default_factory=list)
    required_evidence_types: List[str] = field(default_factory=list)
    invalidation_conditions: List[str] = field(default_factory=list)
    source_proposal_ids: List[str] = field(default_factory=list)
    source_question_ids: List[str] = field(default_factory=list)
    inference_generation_ids: List[str] = field(default_factory=list)
    status: str = "proposed"
    stage: str = "proposal"
    classification: str = "causal-mechanism"
    candidate_id: str = ""
    candidate_rule: Dict[str, object] = field(default_factory=dict)
    experiment_id: str = ""
    validation_gates: List[Dict[str, object]] = field(default_factory=default_validation_gates)
    validation_summary_payload: Dict[str, object] = field(default_factory=dict)
    decision_impact: Dict[str, object] = field(default_factory=dict)
    deployment: Dict[str, object] = field(default_factory=dict)
    blocked_reason: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        rename = {
            "case_id": "caseId",
            "account_id": "accountId",
            "causal_path": "causalPath",
            "supporting_evidence_ids": "supportingEvidenceIds",
            "counter_evidence_ids": "counterEvidenceIds",
            "required_evidence_types": "requiredEvidenceTypes",
            "invalidation_conditions": "invalidationConditions",
            "source_proposal_ids": "sourceProposalIds",
            "source_question_ids": "sourceQuestionIds",
            "inference_generation_ids": "inferenceGenerationIds",
            "candidate_id": "candidateId",
            "candidate_rule": "candidateRule",
            "experiment_id": "experimentId",
            "validation_gates": "validationGates",
            "validation_summary_payload": "validationSummary",
            "decision_impact": "decisionImpact",
            "blocked_reason": "blockedReason",
            "created_at": "createdAt",
            "updated_at": "updatedAt",
        }
        for source, target in rename.items():
            payload[target] = payload.pop(source)
        if not payload["validationSummary"]:
            payload["validationSummary"] = validation_summary(payload["validationGates"])
        payload["contract"] = "hypothesis-development-case-v1"
        return payload

    @staticmethod
    def from_dict(payload: Dict[str, object]):
        payload = dict(payload or {})
        gates = [dict(item) for item in payload.get("validationGates") or payload.get("validation_gates") or [] if isinstance(item, dict)]
        case = HypothesisDevelopmentCase(
            case_id=clean_text(payload.get("caseId") or payload.get("case_id"), 191),
            fingerprint=clean_text(payload.get("fingerprint"), 64),
            account_id=clean_text(payload.get("accountId") or payload.get("account_id"), 191),
            symbol=clean_text(payload.get("symbol"), 64).upper(),
            title=clean_text(payload.get("title"), 255),
            claim=clean_text(payload.get("claim"), 4000),
            causal_path=clean_list(payload.get("causalPath") or payload.get("causal_path"), 20, 500),
            supporting_evidence_ids=clean_list(payload.get("supportingEvidenceIds") or payload.get("supporting_evidence_ids"), 200, 191),
            counter_evidence_ids=clean_list(payload.get("counterEvidenceIds") or payload.get("counter_evidence_ids"), 200, 191),
            required_evidence_types=clean_list(payload.get("requiredEvidenceTypes") or payload.get("required_evidence_types"), 40, 160),
            invalidation_conditions=clean_list(payload.get("invalidationConditions") or payload.get("invalidation_conditions"), 20, 500),
            source_proposal_ids=clean_list(payload.get("sourceProposalIds") or payload.get("source_proposal_ids"), 100, 191),
            source_question_ids=clean_list(payload.get("sourceQuestionIds") or payload.get("source_question_ids"), 100, 191),
            inference_generation_ids=clean_list(payload.get("inferenceGenerationIds") or payload.get("inference_generation_ids"), 100, 191),
            status=clean_text(payload.get("status") or "proposed", 40),
            stage=clean_text(payload.get("stage") or "proposal", 40),
            classification=clean_text(payload.get("classification") or "causal-mechanism", 80),
            candidate_id=clean_text(payload.get("candidateId") or payload.get("candidate_id"), 191),
            candidate_rule=dict(payload.get("candidateRule") or payload.get("candidate_rule") or {}),
            experiment_id=clean_text(payload.get("experimentId") or payload.get("experiment_id"), 191),
            validation_gates=gates or default_validation_gates(),
            validation_summary_payload=dict(payload.get("validationSummary") or payload.get("validation_summary") or {}),
            decision_impact=dict(payload.get("decisionImpact") or payload.get("decision_impact") or {}),
            deployment=dict(payload.get("deployment") or {}),
            blocked_reason=clean_text(payload.get("blockedReason") or payload.get("blocked_reason"), 1000),
            created_at=clean_text(payload.get("createdAt") or payload.get("created_at") or utc_now_iso(), 40),
            updated_at=clean_text(payload.get("updatedAt") or payload.get("updated_at") or utc_now_iso(), 40),
        )
        if case.status not in HYPOTHESIS_DEVELOPMENT_STATUSES:
            case.status = "blocked"
            case.blocked_reason = case.blocked_reason or "unknown-development-status"
        return case

    @staticmethod
    def from_proposal(payload: Dict[str, object], inference_generation_id: str = ""):
        proposal = dict(payload or {})
        fingerprint = hypothesis_development_fingerprint(
            proposal.get("accountId"),
            proposal.get("symbol"),
            proposal.get("claim"),
            proposal.get("causalPath") or [],
        )
        return HypothesisDevelopmentCase(
            case_id=hypothesis_case_id(fingerprint),
            fingerprint=fingerprint,
            account_id=clean_text(proposal.get("accountId"), 191),
            symbol=clean_text(proposal.get("symbol"), 64).upper(),
            title=clean_text(proposal.get("title") or proposal.get("claim"), 255),
            claim=clean_text(proposal.get("claim"), 4000),
            causal_path=clean_list(proposal.get("causalPath"), 20, 500),
            supporting_evidence_ids=clean_list(proposal.get("supportingEvidenceIds"), 200, 191),
            counter_evidence_ids=clean_list(proposal.get("counterEvidenceIds"), 200, 191),
            required_evidence_types=clean_list(proposal.get("requiredEvidenceTypes"), 40, 160),
            invalidation_conditions=clean_list(proposal.get("invalidationConditions"), 20, 500),
            source_proposal_ids=clean_list([proposal.get("proposalId")], 100, 191),
            source_question_ids=clean_list([proposal.get("sourceQuestionId")], 100, 191),
            inference_generation_ids=clean_list([inference_generation_id], 100, 191),
        )

    def merge_proposal(self, payload: Dict[str, object], inference_generation_id: str = "") -> None:
        proposal = dict(payload or {})
        self.supporting_evidence_ids = clean_list(self.supporting_evidence_ids + list(proposal.get("supportingEvidenceIds") or []), 200, 191)
        self.counter_evidence_ids = clean_list(self.counter_evidence_ids + list(proposal.get("counterEvidenceIds") or []), 200, 191)
        self.required_evidence_types = clean_list(self.required_evidence_types + list(proposal.get("requiredEvidenceTypes") or []), 40, 160)
        self.invalidation_conditions = clean_list(self.invalidation_conditions + list(proposal.get("invalidationConditions") or []), 20, 500)
        self.source_proposal_ids = clean_list(self.source_proposal_ids + [proposal.get("proposalId")], 100, 191)
        self.source_question_ids = clean_list(self.source_question_ids + [proposal.get("sourceQuestionId")], 100, 191)
        self.inference_generation_ids = clean_list(self.inference_generation_ids + [inference_generation_id], 100, 191)
        self.updated_at = utc_now_iso()

    def transition(self, status: str, stage: str = "", reason: str = "") -> None:
        normalized = clean_text(status, 40)
        if normalized not in HYPOTHESIS_DEVELOPMENT_STATUSES:
            raise ValueError("지원하지 않는 가설 개발 상태입니다: " + normalized)
        self.status = normalized
        if stage:
            self.stage = clean_text(stage, 40)
        self.blocked_reason = clean_text(reason, 1000) if normalized in {"blocked", "needs-data", "needs-revision", "contradicted", "rejected"} else ""
        self.updated_at = utc_now_iso()

    def update_gates(self, updates: Iterable[Dict[str, object]]) -> None:
        self.validation_gates = merged_validation_gates(self.validation_gates, updates)
        self.validation_summary_payload = validation_summary(self.validation_gates)
        self.updated_at = utc_now_iso()


def screen_hypothesis_case(case: HypothesisDevelopmentCase) -> Dict[str, object]:
    compact = (case.title + " " + case.claim).casefold()
    non_causal = next((term for term in NON_CAUSAL_PROPOSAL_TERMS if term in compact), "")
    issues = []
    needs_data = []
    if not case.claim:
        issues.append("claim-missing")
    if len(case.causal_path) < 2:
        issues.append("causal-path-too-short")
    if not case.invalidation_conditions:
        issues.append("invalidation-condition-missing")
    if not case.supporting_evidence_ids:
        needs_data.append("supporting-evidence-missing")
    if non_causal:
        issues.append("non-causal-proposal:" + non_causal)
    status = "passed"
    classification = "causal-mechanism"
    if non_causal:
        status = "rejected"
        classification = "data-or-verification-constraint"
    elif issues:
        status = "needs-revision"
    elif needs_data:
        status = "needs-data"
    return {
        "status": status,
        "classification": classification,
        "issues": issues,
        "needsData": needs_data,
        "gate": validation_gate(
            "structure",
            "가설 구조",
            "passed" if status == "passed" else ("needs-data" if status == "needs-data" else "blocked"),
            ", ".join(issues + needs_data) or "원인·경로·반증 조건이 구조 계약을 충족했습니다.",
            True,
            {"causalPathLength": len(case.causal_path), "invalidationConditionCount": len(case.invalidation_conditions)},
        ),
    }


def hypothesis_decision_impact(candidate_rule: Dict[str, object]) -> Dict[str, object]:
    derivations = [dict(item) for item in (candidate_rule or {}).get("derivations") or [] if isinstance(item, dict)]
    actions = sorted({str(item.get("candidate_action") or item.get("candidateAction") or "").upper() for item in derivations if str(item.get("candidate_action") or item.get("candidateAction") or "")})
    effects = sorted({str(item.get("decision_effect") or item.get("decisionEffect") or "").lower() for item in derivations if str(item.get("decision_effect") or item.get("decisionEffect") or "")})
    if any(action in {"BUY", "ADD", "TRIM", "SELL", "AVOID"} for action in actions):
        influence = "action-changing"
    elif any(effect in {"block", "constrain", "defer"} for effect in effects):
        influence = "action-disambiguation"
    else:
        influence = "explanation-only"
    return {
        "influence": influence,
        "candidateActions": actions,
        "decisionEffects": effects,
        "requiresDeploymentApproval": True,
    }
