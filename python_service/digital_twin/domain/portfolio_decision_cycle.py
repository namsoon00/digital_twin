"""Account-level policy candidate set without final investment judgement."""

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Dict, Iterable, List


PORTFOLIO_DECISION_CYCLE_VERSION = "portfolio-decision-cycle-v1"


def stable_cycle_id(portfolio_id: str, source_fingerprint: str, policy_version: str) -> str:
    raw = "|".join([str(portfolio_id or ""), str(source_fingerprint or ""), str(policy_version or "")])
    return "portfolio-decision-cycle:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class PortfolioActionCandidate:
    candidate_id: str
    candidate_type: str
    label: str
    affected_symbol: str = ""
    maximum_notional: float = 0.0
    before_metrics: Dict[str, object] = field(default_factory=dict)
    after_metrics: Dict[str, object] = field(default_factory=dict)
    policy_effects: List[str] = field(default_factory=list)
    required_relation_types: List[str] = field(default_factory=list)
    data_state: str = "partial"
    executable: bool = False

    @classmethod
    def create(cls, cycle_key: str, candidate_type: str, label: str, **values):
        symbol = str(values.get("affected_symbol") or values.get("affectedSymbol") or "").upper()
        return cls(
            candidate_id="portfolio-action-candidate:" + hashlib.sha256(
                (cycle_key + "|" + str(candidate_type) + "|" + symbol).encode("utf-8")
            ).hexdigest()[:24],
            candidate_type=str(candidate_type or "NO_ACTION").upper(),
            label=str(label or "상태 유지"),
            affected_symbol=symbol,
            maximum_notional=max(0.0, float(values.get("maximum_notional") or values.get("maximumNotional") or 0)),
            before_metrics=dict(values.get("before_metrics") or values.get("beforeMetrics") or {}),
            after_metrics=dict(values.get("after_metrics") or values.get("afterMetrics") or {}),
            policy_effects=list(values.get("policy_effects") or values.get("policyEffects") or []),
            required_relation_types=list(values.get("required_relation_types") or values.get("requiredRelationTypes") or []),
            data_state=str(values.get("data_state") or values.get("dataState") or "partial"),
            executable=bool(values.get("executable", False)),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["version"] = PORTFOLIO_DECISION_CYCLE_VERSION
        return payload


@dataclass(frozen=True)
class PortfolioDecisionCycle:
    cycle_id: str
    portfolio_id: str
    account_id: str
    policy_version: str
    source_snapshot_id: str
    candidates: List[PortfolioActionCandidate]
    data_state: str
    missing_data: List[str] = field(default_factory=list)
    created_at: str = ""

    @classmethod
    def create(cls, portfolio_id: str, account_id: str, policy_version: str, source_snapshot_id: str, candidates, **values):
        rows = list(candidates or [])
        return cls(
            cycle_id=stable_cycle_id(portfolio_id, source_snapshot_id, policy_version),
            portfolio_id=str(portfolio_id or ""),
            account_id=str(account_id or ""),
            policy_version=str(policy_version or ""),
            source_snapshot_id=str(source_snapshot_id or ""),
            candidates=rows,
            data_state=str(values.get("data_state") or values.get("dataState") or "partial"),
            missing_data=list(values.get("missing_data") or values.get("missingData") or []),
            created_at=str(values.get("created_at") or values.get("createdAt") or ""),
        )

    @property
    def fingerprint(self) -> str:
        raw = json.dumps([item.to_dict() for item in self.candidates], ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": PORTFOLIO_DECISION_CYCLE_VERSION,
            "cycleId": self.cycle_id,
            "portfolioId": self.portfolio_id,
            "accountId": self.account_id,
            "policyVersion": self.policy_version,
            "sourceSnapshotId": self.source_snapshot_id,
            "candidateFingerprint": self.fingerprint,
            "candidates": [item.to_dict() for item in self.candidates],
            "dataState": self.data_state,
            "missingData": list(self.missing_data),
            "createdAt": self.created_at,
        }
