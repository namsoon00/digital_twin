"""Lifecycle-aware changes to the inference-eligible evidence set.

Evidence rows are durable audit records.  A TypeDB generation, however,
must only receive evidence that is currently eligible for investment
inference.  This module describes the boundary between those two concerns
without making an investment judgement itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional

from .fact_changes import fact_revision_id, fact_signature


ACTIVE_EVIDENCE_LIFECYCLE_STATES = {"active"}
EVIDENCE_DELTA_TRANSITIONS = {
    "added",
    "modified",
    "promotion",
    "demotion",
    "expiration",
    "retraction",
    "supersession",
    "reactivation",
}


def clean_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def clean_lifecycle_state(value: object, fallback: str = "active") -> str:
    state = str(value or fallback).strip().lower()
    return state or fallback


def evidence_is_active(lifecycle_state: object) -> bool:
    return clean_lifecycle_state(lifecycle_state) in ACTIVE_EVIDENCE_LIFECYCLE_STATES


def evidence_content_signature(evidence) -> str:
    """Return a stable signature for one evidence fact, excluding collection clocks."""
    if evidence is None:
        return ""
    payload = getattr(evidence, "raw_payload", {})
    payload = dict(payload or {}) if isinstance(payload, Mapping) else {}
    # Lifecycle is carried by the transition itself.  Its audit timestamp must
    # not make an unchanged active fact look like a new inference input.
    payload.pop("evidenceLifecycleState", None)
    payload.pop("evidenceLifecycleChangedAt", None)
    return fact_signature({
        "evidenceId": str(getattr(evidence, "evidence_id", "") or "").strip(),
        "symbol": clean_symbol(getattr(evidence, "symbol", "")),
        "kind": str(getattr(evidence, "kind", "") or "").strip(),
        "source": str(getattr(evidence, "source", "") or "").strip(),
        "title": str(getattr(evidence, "title", "") or "").strip(),
        "summary": str(getattr(evidence, "summary", "") or "").strip(),
        "url": str(getattr(evidence, "url", "") or "").strip(),
        # The storage contract normalizes an omitted publication timestamp to
        # the observation timestamp. Mirror that invariant before comparing an
        # incoming object with the persisted row.
        "publishedAt": str(
            getattr(evidence, "published_at", "")
            or getattr(evidence, "observed_at", "")
            or ""
        ).strip(),
        "polarity": str(getattr(evidence, "polarity", "context") or "context").strip(),
        "sourceTrustState": str(getattr(evidence, "source_trust_state", "unknown") or "unknown").strip(),
        "materialityState": str(getattr(evidence, "materiality_state", "context") or "context").strip(),
        "dataState": str(getattr(evidence, "data_state", "partial") or "partial").strip(),
        "validationState": str(getattr(evidence, "validation_state", "conditional") or "conditional").strip(),
        "payload": payload,
    })


def inference_eligible(evidence, lifecycle_state: object = "active", settings: Dict[str, object] = None) -> bool:
    """Whether an evidence item belongs to the active investment fact set.

    Materiality only decides admission to the inference fact set.  It does not
    determine the final investment action; TypeDB schema functions remain the
    only judgement path.
    """
    if evidence is None or not evidence_is_active(lifecycle_state):
        return False
    # Imported lazily to keep the lifecycle contract independent from the
    # evidence collection/application layer.
    from .materiality import evidence_materiality

    return bool(evidence_materiality(evidence, settings or {}).passed)


def eligible_evidence_set_revision(symbol: object, evidence_signatures: Iterable[object]) -> str:
    """Return the revision of the *current* eligible fact set for one symbol."""
    clean_signatures = sorted({str(value or "").strip() for value in evidence_signatures or [] if str(value or "").strip()})
    return fact_revision_id(
        "EligibleResearchEvidenceSet",
        clean_symbol(symbol),
        {"eligibleEvidenceSignatures": clean_signatures},
    )


@dataclass(frozen=True)
class EvidenceDelta:
    evidence_id: str
    symbol: str
    transition: str
    previous_lifecycle_state: str = ""
    lifecycle_state: str = "active"
    previous_eligible: bool = False
    eligible: bool = False
    previous_signature: str = ""
    signature: str = ""
    occurred_at: str = ""
    reason: str = ""
    eligible_set_revision: str = ""

    @property
    def changes_inference_eligible_set(self) -> bool:
        if self.previous_eligible != self.eligible:
            return True
        return bool(self.eligible and self.previous_signature != self.signature)

    def with_eligible_set_revision(self, revision: str) -> "EvidenceDelta":
        return EvidenceDelta(
            evidence_id=self.evidence_id,
            symbol=self.symbol,
            transition=self.transition,
            previous_lifecycle_state=self.previous_lifecycle_state,
            lifecycle_state=self.lifecycle_state,
            previous_eligible=self.previous_eligible,
            eligible=self.eligible,
            previous_signature=self.previous_signature,
            signature=self.signature,
            occurred_at=self.occurred_at,
            reason=self.reason,
            eligible_set_revision=str(revision or ""),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "evidenceId": self.evidence_id,
            "symbol": self.symbol,
            "transition": self.transition,
            "previousLifecycleState": self.previous_lifecycle_state,
            "lifecycleState": self.lifecycle_state,
            "previousEligible": self.previous_eligible,
            "eligible": self.eligible,
            "previousSignature": self.previous_signature,
            "signature": self.signature,
            "occurredAt": self.occurred_at,
            "reason": self.reason,
            "changesInferenceEligibleSet": self.changes_inference_eligible_set,
            "eligibleEvidenceSetRevision": self.eligible_set_revision,
            "factFamilies": ["evidence"],
        }


def evidence_delta(
    previous,
    current,
    *,
    previous_lifecycle_state: object = "",
    lifecycle_state: object = "active",
    transition: str = "",
    occurred_at: str = "",
    reason: str = "",
    settings: Dict[str, object] = None,
) -> EvidenceDelta:
    """Build a lifecycle transition while preserving the prior fact identity."""
    before_state = clean_lifecycle_state(previous_lifecycle_state or ("active" if previous is not None else ""), "")
    after_state = clean_lifecycle_state(lifecycle_state, "active")
    before_signature = evidence_content_signature(previous)
    signature = evidence_content_signature(current)
    subject = current if current is not None else previous
    evidence_id = str(getattr(subject, "evidence_id", "") or "").strip()
    symbol = clean_symbol(getattr(subject, "symbol", ""))
    previous_eligible = inference_eligible(previous, before_state, settings) if previous is not None else False
    eligible = inference_eligible(current, after_state, settings) if current is not None else False

    if not transition:
        if previous is None and current is not None:
            transition = "added"
        elif previous is not None and current is None:
            transition = "retraction"
        elif before_state and before_state != "active" and after_state == "active":
            transition = "reactivation"
        elif not previous_eligible and eligible:
            transition = "promotion" if before_state else "added"
        elif previous_eligible and not eligible:
            transition = "demotion"
        else:
            transition = "modified"
    transition = str(transition or "modified").strip().lower()
    if transition not in EVIDENCE_DELTA_TRANSITIONS:
        transition = "modified"
    return EvidenceDelta(
        evidence_id=evidence_id,
        symbol=symbol,
        transition=transition,
        previous_lifecycle_state=before_state,
        lifecycle_state=after_state,
        previous_eligible=previous_eligible,
        eligible=eligible,
        previous_signature=before_signature,
        signature=signature,
        occurred_at=str(occurred_at or ""),
        reason=str(reason or ""),
    )


def eligible_set_revisions_for_deltas(
    deltas: Iterable[EvidenceDelta],
    eligible_signatures_by_symbol: Mapping[str, Iterable[object]],
) -> Dict[str, str]:
    affected_symbols = {
        clean_symbol(delta.symbol)
        for delta in deltas or []
        if clean_symbol(delta.symbol) and delta.changes_inference_eligible_set
    }
    return {
        symbol: eligible_evidence_set_revision(symbol, eligible_signatures_by_symbol.get(symbol) or [])
        for symbol in sorted(affected_symbols)
    }


@dataclass
class EvidenceMutation:
    """One transactional source mutation and its inference-relevant delta."""

    written_count: int = 0
    expired_count: int = 0
    retracted_count: int = 0
    changed_symbols: List[str] = field(default_factory=list)
    changed_items: List[object] = field(default_factory=list)
    deltas: List[EvidenceDelta] = field(default_factory=list)
    eligible_set_revisions: Dict[str, str] = field(default_factory=dict)

    @property
    def lifecycle_changed_count(self) -> int:
        return int(self.expired_count or 0) + int(self.retracted_count or 0)

    @property
    def inference_changed_symbols(self) -> List[str]:
        return sorted({
            clean_symbol(delta.symbol)
            for delta in self.deltas
            if clean_symbol(delta.symbol) and delta.changes_inference_eligible_set
        })

    def with_revisions(self) -> "EvidenceMutation":
        revisions = dict(self.eligible_set_revisions or {})
        self.deltas = [
            delta.with_eligible_set_revision(revisions.get(clean_symbol(delta.symbol), ""))
            for delta in self.deltas
        ]
        return self

    def to_dict(self) -> Dict[str, object]:
        return {
            "writtenCount": int(self.written_count or 0),
            "expiredCount": int(self.expired_count or 0),
            "retractedCount": int(self.retracted_count or 0),
            "changedSymbols": list(self.changed_symbols or []),
            "inferenceChangedSymbols": self.inference_changed_symbols,
            "evidenceDeltas": [delta.to_dict() for delta in self.deltas],
            "factRevisionsBySymbol": dict(self.eligible_set_revisions or {}),
        }
