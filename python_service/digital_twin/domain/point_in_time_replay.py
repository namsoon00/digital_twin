"""Point-in-time contracts for read-only investment decision replay.

Historical replay must distinguish facts known at a decision from observations
recorded later.  This module owns that semantic boundary without importing a
database or a reasoning engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from typing import Dict, Iterable, List, Mapping, Protocol, Sequence

from .investment_brain import canonical_investment_timestamp, parse_investment_timestamp


POINT_IN_TIME_CONTRACT_VERSION = "decision-point-in-time-contract-v1"
DECISION_REPLAY_ENVELOPE_VERSION = "decision-replay-envelope-v1"
STRICT_REPLAY_MODE = "strict-replay"

# These fields state when a fact was knowable.  Future deadlines such as
# expiresAt/targetAt are intentionally absent because they may follow a valid
# decision while still being known at that decision.
KNOWLEDGE_TIMESTAMP_FIELDS = frozenset({
    "acceptedat",
    "analyzedat",
    "askedat",
    "availableat",
    "collectedat",
    "createdat",
    "derivedat",
    "effectiveat",
    "fetchedat",
    "generatedat",
    "indicatorasof",
    "ingestedat",
    "observedat",
    "priceasof",
    "publishedat",
    "receivedat",
    "recordedat",
    "receiptdate",
    "sourceasof",
    "sourcefetchedat",
    "sourceobservedat",
    "updatedat",
    "valuationasof",
})


def _mapping(value: object) -> Dict[str, object]:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        payload = value.to_dict()
        return dict(payload or {}) if isinstance(payload, Mapping) else {}
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _knowledge_timestamp(value: object):
    parsed = parse_investment_timestamp(value)
    if parsed:
        text = str(value or "").strip()
        precision = "date" if len(text) == 10 and text[4:5] == "-" else "timestamp"
        return parsed, canonical_investment_timestamp(value), precision
    text = str(value or "").strip()
    if len(text) == 15 and text[8:9] == "T":
        try:
            parsed = datetime.strptime(text, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None, "", "invalid"
        return parsed, parsed.isoformat().replace("+00:00", "Z"), "timestamp"
    if len(text) == 8 and text.isdigit():
        try:
            parsed = datetime.strptime(text, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None, "", "invalid"
        return parsed, parsed.isoformat().replace("+00:00", "Z"), "date"
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        return parsed, parsed.isoformat().replace("+00:00", "Z"), "timestamp"
    return None, "", "invalid"


def _timestamp_entries(value: object, path: str = "facts", limit: int = 5000) -> List[Dict[str, object]]:
    entries: List[Dict[str, object]] = []

    def visit(item: object, current_path: str) -> None:
        if len(entries) >= limit:
            return
        if isinstance(item, Mapping):
            for key, child in item.items():
                key_text = str(key)
                child_path = current_path + "." + key_text
                if key_text.lower() in KNOWLEDGE_TIMESTAMP_FIELDS and child not in (None, ""):
                    entries.append({"path": child_path, "field": key_text, "value": child})
                if isinstance(child, (Mapping, list, tuple)):
                    visit(child, child_path)
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, current_path + "[" + str(index) + "]")

    visit(value, path)
    return entries


def point_in_time_assessment(
    payload: object,
    decision_at: object,
    *,
    snapshot_anchored: bool = False,
    sample_limit: int = 40,
) -> Dict[str, object]:
    """Audit nested fact clocks against one immutable decision cutoff."""

    cutoff = parse_investment_timestamp(decision_at)
    entries = _timestamp_entries(payload)
    future: List[Dict[str, object]] = []
    invalid: List[Dict[str, object]] = []
    accepted = 0
    coarse = 0
    latest_accepted = ""
    if cutoff:
        for entry in entries:
            parsed, canonical, precision = _knowledge_timestamp(entry.get("value"))
            if not parsed:
                invalid.append(dict(entry))
                continue
            if precision == "date" and snapshot_anchored:
                # The immutable decision snapshot proves the value was already
                # known. A date-only publisher field cannot establish an
                # intraday ordering, so record reduced precision instead of
                # inventing a timestamp.
                accepted += 1
                coarse += 1
                continue
            if parsed > cutoff:
                future.append({
                    **entry,
                    "canonicalValue": canonical,
                    "reason": "knowledge-timestamp-after-decision",
                })
                continue
            accepted += 1
            if canonical > latest_accepted:
                latest_accepted = canonical
    status = "passed"
    if not cutoff:
        status = "blocked-invalid-decision-time"
    elif future or invalid:
        status = "blocked-temporal-violation"
    elif not entries and not snapshot_anchored:
        status = "blocked-unanchored-timestamps"
    elif not entries:
        status = "passed-snapshot-anchor-only"
    return {
        "contractVersion": POINT_IN_TIME_CONTRACT_VERSION,
        "status": status,
        "passed": status.startswith("passed"),
        "decisionAt": canonical_investment_timestamp(decision_at),
        "snapshotAnchored": bool(snapshot_anchored),
        "checkedTimestampCount": len(entries),
        "acceptedTimestampCount": accepted,
        "coarseTimestampCount": coarse,
        "futureTimestampCount": len(future),
        "invalidTimestampCount": len(invalid),
        "latestAcceptedTimestamp": latest_accepted,
        "futureTimestampSamples": future[:max(1, int(sample_limit or 1))],
        "invalidTimestampSamples": invalid[:max(1, int(sample_limit or 1))],
        "futureInformationAllowed": False,
    }


def observations_as_of(
    rows: Iterable[object],
    cutoff_at: object,
    *,
    timestamp_fields: Sequence[str] = ("observedAt",),
) -> Dict[str, object]:
    """Partition mutable observations without silently accepting unknown clocks."""

    cutoff = parse_investment_timestamp(cutoff_at)
    included: List[Dict[str, object]] = []
    future: List[Dict[str, object]] = []
    invalid: List[Dict[str, object]] = []
    missing: List[Dict[str, object]] = []
    for value in rows or []:
        row = _mapping(value)
        timestamp_value = next((row.get(key) for key in timestamp_fields if row.get(key) not in (None, "")), "")
        if not timestamp_value:
            missing.append(row)
            continue
        observed = parse_investment_timestamp(timestamp_value)
        if not cutoff or not observed:
            invalid.append(row)
        elif observed <= cutoff:
            included.append(row)
        else:
            future.append(row)
    return {
        "cutoffAt": canonical_investment_timestamp(cutoff_at),
        "included": included,
        "futureExcluded": future,
        "invalidExcluded": invalid,
        "missingTimestampExcluded": missing,
        "includedCount": len(included),
        "futureExcludedCount": len(future),
        "invalidExcludedCount": len(invalid),
        "missingTimestampExcludedCount": len(missing),
    }


def replay_engine_manifest(episode: object) -> Dict[str, object]:
    row = _mapping(episode)
    facts = _mapping(row.get("factsAtDecision"))
    outcome_contract = _mapping(facts.get("hypothesisOutcomeContract"))
    hypothesis_set = _mapping(row.get("hypothesisSet"))
    manifest = {
        "engineVersion": str(row.get("engineVersion") or ""),
        "inferenceGenerationId": str(row.get("inferenceGenerationId") or ""),
        "sourceAboxSnapshotId": str(row.get("sourceAboxSnapshotId") or ""),
        "mandateVersion": str(row.get("mandateVersion") or ""),
        "hypothesisSetVersion": str(hypothesis_set.get("version") or ""),
        "outcomeContractVersion": str(outcome_contract.get("contractVersion") or ""),
        "outcomeContractFingerprint": str(outcome_contract.get("contractFingerprint") or ""),
        "valuationModelVersion": str(facts.get("valuationModelVersion") or ""),
    }
    exact_input_fields = ("engineVersion", "inferenceGenerationId", "sourceAboxSnapshotId")
    comparison_fields = ("tboxFingerprint", "ruleboxFingerprint", "promptVersion", "modelVersion")
    for field_name in comparison_fields:
        manifest[field_name] = str(
            row.get(field_name)
            or facts.get(field_name)
            or _mapping(facts.get("engineManifest")).get(field_name)
            or ""
        )
    manifest["missingExactInputFields"] = [key for key in exact_input_fields if not manifest.get(key)]
    manifest["missingEngineComparisonFields"] = [key for key in comparison_fields if not manifest.get(key)]
    manifest["manifestFingerprint"] = _fingerprint({
        key: value for key, value in manifest.items() if not key.startswith("missing") and key != "manifestFingerprint"
    })
    return manifest


@dataclass(frozen=True)
class DecisionReplayEnvelope:
    episode_id: str
    account_id: str
    symbol: str
    decision_at: str
    knowledge_cutoff_at: str
    original_decision: Mapping[str, object]
    facts_at_decision: Mapping[str, object]
    engine_manifest: Mapping[str, object]
    temporal_assessment: Mapping[str, object]
    replay_mode: str = STRICT_REPLAY_MODE
    contract_version: str = DECISION_REPLAY_ENVELOPE_VERSION
    input_fingerprint: str = field(default="")

    @classmethod
    def create(cls, episode: object, replay_mode: str = STRICT_REPLAY_MODE) -> "DecisionReplayEnvelope":
        row = _mapping(episode)
        decision_at = canonical_investment_timestamp(row.get("decidedAt"))
        knowledge_cutoff_at = canonical_investment_timestamp(row.get("recordedAt") or decision_at)
        facts = _mapping(row.get("factsAtDecision"))
        manifest = replay_engine_manifest(row)
        temporal = point_in_time_assessment(
            facts,
            knowledge_cutoff_at,
            snapshot_anchored=bool(facts),
        )
        original_decision = {
            key: row.get(key)
            for key in (
                "action", "reviewLevel", "dataState", "validationState", "decisionReadiness",
                "selectedHypothesisId", "hypothesisComparisonState", "hypothesisSelectionSource",
                "decisionSummary", "investmentView", "executionDecision", "status", "source",
            )
            if row.get(key) not in (None, "", [], {})
        }
        core = {
            "contractVersion": DECISION_REPLAY_ENVELOPE_VERSION,
            "replayMode": str(replay_mode or STRICT_REPLAY_MODE),
            "episodeId": str(row.get("episodeId") or ""),
            "accountId": str(row.get("accountId") or ""),
            "symbol": str(row.get("symbol") or "").upper(),
            "decisionAt": decision_at,
            "knowledgeCutoffAt": knowledge_cutoff_at,
            "originalDecision": original_decision,
            "factsAtDecision": facts,
            "engineManifest": manifest,
        }
        return cls(
            episode_id=core["episodeId"],
            account_id=core["accountId"],
            symbol=core["symbol"],
            decision_at=decision_at,
            knowledge_cutoff_at=knowledge_cutoff_at,
            original_decision=original_decision,
            facts_at_decision=facts,
            engine_manifest=manifest,
            temporal_assessment=temporal,
            replay_mode=core["replayMode"],
            input_fingerprint=_fingerprint(core),
        )

    def replay_class(self) -> str:
        if not self.decision_at or not self.facts_at_decision:
            return "audit-only"
        if not bool(self.temporal_assessment.get("passed")):
            return "blocked-temporal-violation"
        if self.engine_manifest.get("missingExactInputFields"):
            return "partial-replay"
        return "exact-input-replay"

    def to_dict(self, include_facts: bool = False) -> Dict[str, object]:
        payload = {
            "contractVersion": self.contract_version,
            "replayMode": self.replay_mode,
            "replayClass": self.replay_class(),
            "episodeId": self.episode_id,
            "accountId": self.account_id,
            "symbol": self.symbol,
            "decisionAt": self.decision_at,
            "knowledgeCutoffAt": self.knowledge_cutoff_at,
            "originalDecision": dict(self.original_decision),
            "engineManifest": dict(self.engine_manifest),
            "pointInTime": dict(self.temporal_assessment),
            "factsFingerprint": _fingerprint(self.facts_at_decision),
            "factFieldCount": len(self.facts_at_decision),
            "inputFingerprint": self.input_fingerprint,
            "engineComparisonReady": not bool(self.engine_manifest.get("missingEngineComparisonFields")),
        }
        if include_facts:
            payload["factsAtDecision"] = dict(self.facts_at_decision)
        return payload


class DecisionReplayEnginePort(Protocol):
    """Versioned engine boundary for later V1/V2/V3 comparison runners."""

    def engine_id(self) -> str:
        ...

    def replay(self, envelope: DecisionReplayEnvelope) -> Mapping[str, object]:
        ...
