"""Versioned evidence delivery contract, independent of matched investment rules.

Selection, coverage accounting and change identity share this policy. Unknown
fields remain material by default; only named storage/polling metadata is inert.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math


EVIDENCE_PROTOCOL = "observation-evidence-v1"
LEGACY_EVIDENCE_PROFILE = "independent-observation-v1"
MACRO_EVIDENCE_PROFILE = "independent-observation-v2-macro-prints"
BUSINESS_EVIDENCE_PROFILE = "independent-observation-v3-business-research"
PORTFOLIO_EVIDENCE_PROFILE = "independent-observation-v5-portfolio-context"
EVIDENCE_PROFILE = "independent-observation-v6-market-comparison"
CRYPTO_EVIDENCE_PROFILE = "independent-observation-v4-crypto"
OBSERVATION_QUOTE_KINDS = ("stock", "crypto-asset")
EVIDENCE_PACKET_MAX_BYTES = 96000


class EvidenceContractError(ValueError):
    """Only authored, non-sensitive contract diagnostics cross this boundary."""
    def __init__(self, reason):
        super().__init__(reason)
        self.code = "evidence-contract:" + reason.replace(" ", "-")


class EvidenceReadError(RuntimeError):
    """Safe read-stage identity without a query, account or provider message."""
    def __init__(self, stage, error):
        self.code = "evidence-read:" + stage + ":" + type(error).__name__
        super().__init__(self.code)


class EvidenceNotReady(EvidenceContractError):
    """A required published generation is absent; no model failure occurred."""
    def __init__(self, world_role, metadata):
        super().__init__(world_role + " graph not ready")
        self.conditions = []
        if metadata.get("status") != "ok":
            self.conditions.append("metadata-not-ready")
        if not metadata.get("aboxSnapshotId"):
            self.conditions.append("snapshot-missing")
        self.metadata_status = metadata.get("status") if metadata.get("status") in {"ok", "empty", "incomplete"} else "unknown"


class CryptoQuoteNotReady(EvidenceNotReady):
    """An older native crypto generation lacks the newly required quote fields."""
    def __init__(self):
        EvidenceContractError.__init__(self, "crypto quote projection not ready")
        self.conditions = ["crypto-quote-fields-missing"]
        self.metadata_status = "incomplete"


def quote_clock_assessment(facts, checked_at):
    """Assess source age without changing frozen ABox facts or delivery policy.

    Capture/fetch time cannot renew an old quote. Last-close is retained as a
    source reference, never inferred from a cached market-session label.
    """
    def instant(value):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
        except (TypeError, ValueError, OverflowError):
            return None

    checked = instant(checked_at)
    if checked is None:
        raise EvidenceContractError("quote assessment clock invalid")
    rows = []
    for fact in facts:
        if fact.get("kind") not in OBSERVATION_QUOTE_KINDS:
            continue
        source = fact.get("sourceAsOf") or fact.get("asOf") or ""
        observed = instant(source)
        age = (checked - observed).total_seconds() / 60 if observed else None
        try:
            maximum = float(fact.get("maxAgeMinutes"))
            if not math.isfinite(maximum) or maximum <= 0 or isinstance(fact.get("maxAgeMinutes"), bool):
                maximum = None
        except (TypeError, ValueError, OverflowError):
            maximum = None
        status = ("missing-time" if not source else "invalid-time" if observed is None or age < 0
                  else "unknown-budget" if maximum is None else "stale" if age > maximum else "fresh")
        rows.append({"evidenceId": fact["id"], "sourceAsOf": source,
            "ageMinutes": round(age, 3) if age is not None else None,
            "maxAgeMinutes": maximum, "status": status,
            "sourceFreshnessStatus": str(fact.get("freshnessStatus") or "unknown"),
            "referenceState": str(fact.get("freshnessReferenceState") or "")})
    return {"version": "observation-quote-clock-v1", "checkedAt": checked_at,
            "policy": "advisory", "quotes": sorted(rows, key=lambda row: row["evidenceId"])}


@dataclass(frozen=True)
class EvidenceCategory:
    name: str
    kinds: tuple
    byte_budget: int
    required: bool = False


PORTFOLIO_CONTEXT_KINDS = ("portfolio", "portfolio-risk-snapshot", "sector-exposure", "currency-exposure", "cash-exposure")

CATEGORIES = (
    EvidenceCategory("quote", OBSERVATION_QUOTE_KINDS, 9000, True),
    EvidenceCategory("valuation", ("earnings-scenario-observation", "valuation-assessment", "valuation-input-bundle",
        "multiple-band-observation", "valuation-calculation-trace", "valuation-assumption", "valuation-metric",
        "valuation-input-observation", "fair-value-estimate", "relative-valuation", "margin-of-safety", "valuation-review"), 28000),
    EvidenceCategory("company", ("company", "company-financial-state", "evidence:financial-fact", "evidence:filing", "evidence:disclosure",
        "revenue-exposure", "customer-exposure", "supply-chain-exposure", "company-relationship", "fundamental-event", "earnings-calendar-event", "analyst-revision", "company-governance-state", "position-risk-metric", *PORTFOLIO_CONTEXT_KINDS), 10000),
    EvidenceCategory("research", ("research-evidence", "news-article", "article-ai-analysis", "evidence:news"), 10000),
    EvidenceCategory("macro", ("macro-print", "interest-rate", "yield-curve", "fx-rate", "benchmark-index", "market-proxy-observation"), 8000),
    EvidenceCategory("technical", ("temporal-window", "trend-observation", "technical-metric", "price-bar", "price-metric", "price-path", "relative-performance-observation"), 6000),
    EvidenceCategory("flow", ("flow-metric", "liquidity-profile", "volume-profile", "exit-capacity"), 6000),
    EvidenceCategory("quality", ("data-quality", "data-availability-assessment", "coverage-gap", "missing-data",
        "data-latency", "temporal-coverage-gap", "data-quality-status"), 4000),
)
MACRO_KINDS = next(category.kinds for category in CATEGORIES if category.name == "macro")
INERT_METADATA = frozenset({
    "id", "updatedAt", "capturedAt", "fetchedAt", "sourceFetchedAt", "sourceSnapshotId", "sourceWorldId",
    "aboxSnapshotId", "snapshotId", "manifestId", "worldviewManifestId", "scopeGenerationId", "physicalGenerationId",
    "logicalScopeGenerationId", "projectionRunId", "materialFingerprint", "freshnessAgeMinutes",
    "marketSessionLocalTime", "propertiesJson", "proposedRuleJson",
})
PROMPT_STORAGE_METADATA = INERT_METADATA - {"id", "sourceSnapshotId", "sourceWorldId"}


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def source_packet_bytes(packet):
    """The source-owner budget excludes separately validated research memory."""
    return len(canonical_json({key: value for key, value in packet.items() if key != "researchKnowledge"}).encode())


def content_hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def material_fact(fact):
    # Deliberately top-level only: dates, revisions and eligibility inside a
    # financial bundle are part of its semantic contract, never stripped.
    ignored = INERT_METADATA
    if fact.get("kind") in OBSERVATION_QUOTE_KINDS:
        # A new quote observation time alone is polling activity. Freshness,
        # source-clock availability and every observed value still participate.
        ignored = ignored | {"sourceAsOf", "asOf", "valuationFxAsOf"}
    return {key: value for key, value in fact.items() if key not in ignored}


def evidence_change_identity(packet, research, questions=()):
    facts = sorted((material_fact(row) for row in packet.get("facts", [])), key=canonical_json)
    value = {"protocol": packet.get("protocolVersion", EVIDENCE_PROTOCOL),
        "profile": packet.get("profile", EVIDENCE_PROFILE), "accountId": packet.get("accountId"),
        "symbol": packet.get("symbol"), "worldId": packet.get("worldId"), "facts": facts,
        "coverage": packet.get("coverage", {}), "research": research, "questions": list(questions)}
    if "quoteAssessment" in packet:
        # A fresh/stale transition is material; another minute of ageing is not
        # a new source event and must not cause an AI call on every poll.
        value["quoteAssessment"] = {"version": packet["quoteAssessment"]["version"], "quotes": [
            {key: row[key] for key in ("evidenceId", "status", "maxAgeMinutes", "referenceState")}
            for row in packet["quoteAssessment"]["quotes"]]}
    return content_hash(value)


def quote_use_assessment(packet):
    """Source-owned quote eligibility; collection clocks cannot renew a quote."""
    facts = {row["id"]: row for row in packet.get("facts", [])}
    result = []
    for row in packet.get("quoteAssessment", {}).get("quotes", []):
        fact = facts.get(row["evidenceId"], {})
        verified_time = (fact.get("sourceTimestampPresent") is not False
            and fact.get("sourceTimestampState") not in {"queried-at-fallback", "fetched-at-fallback"})
        result.append({"evidenceId": row["evidenceId"], "sourceAsOf": row["sourceAsOf"],
            "currentStateEligible": (row["status"] == "fresh" and row.get("referenceState") != "last-close"
                and fact.get("judgementEvidenceUsable") is True and verified_time),
            "datedReferenceEligible": row["status"] in {"fresh", "stale", "unknown-budget"} and verified_time,
            "reasonCode": row["status"]})
    return result


def unavailable_quote_wait_identity(packet):
    """Only after a current-quote wait: another unusable tick is not readiness.

    Other source inventories (including excluded facts) remain material. A
    usable quote immediately releases the gate; no validity window is extended.
    """
    quotes = packet.get("quoteAssessment", {}).get("quotes", [])
    if not quotes or any(row["currentStateEligible"] for row in quote_use_assessment(packet)):
        return ""
    view = {**packet, "facts": [row for row in packet.get("facts", []) if row["kind"] not in OBSERVATION_QUOTE_KINDS],
            "coverage": {key: value for key, value in packet.get("coverage", {}).items() if key != "quote"}}
    view.pop("quoteAssessment", None)
    return content_hash({"nonQuoteEvidence": evidence_change_identity(view, [], []),
        "quoteRequirements": sorted([{key: row.get(key) for key in ("evidenceId", "maxAgeMinutes", "referenceState")}
                                     for row in quotes], key=canonical_json)})


def source_clock(row):
    for key in ("sourceAsOf", "asOf", "observedAt", "publishedAt"):
        try:
            value = datetime.fromisoformat(str(row.get(key) or "").replace("Z", "+00:00"))
            return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
        except (ValueError, TypeError, OverflowError):
            pass
    return float("-inf")


def select_evidence(candidates):
    """Every discovered fact is included or counted with a reason and digest.

    Budgets bound the prompt, not the underlying evidence inventory. An omitted
    fact's material change still changes the packet identity. Whole facts are
    omitted explicitly; financial payloads are never silently truncated.
    """
    by_id = {}
    for row in candidates:
        if not row.get("id") or not row.get("kind"):
            raise EvidenceContractError("evidence identity missing")
        if row["id"] in by_id and by_id[row["id"]] != row:
            raise EvidenceContractError("conflicting evidence identity")
        by_id[row["id"]] = row
    selected, coverage, classified = [], {}, set()
    for category in CATEGORIES:
        rows = [row for row in by_id.values() if row["kind"] in category.kinds]
        # Round-robin by kind protects EPS, assessments and input bundles from
        # a high-volume kind taking the entire category budget.
        groups = [[row for row in sorted(rows, key=lambda item: (-source_clock(item), item["id"])) if row["kind"] == kind]
                  for kind in category.kinds]
        ordered = [group[index] for index in range(max((len(group) for group in groups), default=0))
                   for group in groups if index < len(group)]
        used, included, excluded = 0, [], []
        for source in ordered:
            classified.add(source["id"])
            compact = {key: value for key, value in source.items() if key not in PROMPT_STORAGE_METADATA}
            compact["evidenceCategory"] = category.name
            size = len(canonical_json(compact).encode())
            if used + size > category.byte_budget:
                excluded.append(source)
                continue
            selected.append(compact)
            included.append(source)
            used += size
        if category.required and not included:
            raise EvidenceContractError("required evidence category missing: " + category.name)
        coverage[category.name] = {
            "available": len(rows), "included": len(included), "excluded": len(excluded),
            "status": "missing" if not rows else "partial" if excluded else "complete",
            "exclusionReasons": {"context-budget": len(excluded)} if excluded else {},
            "inventoryHash": content_hash(sorted((material_fact(row) for row in rows), key=canonical_json)),
            "kinds": {kind: {"available": sum(row["kind"] == kind for row in rows),
                              "included": sum(row["kind"] == kind for row in included)}
                      for kind in category.kinds if any(row["kind"] == kind for row in rows)},
        }
    unknown = [row for key, row in by_id.items() if key not in classified]
    coverage["unclassified"] = {"available": len(unknown), "included": 0, "excluded": len(unknown),
        "status": "unsupported" if unknown else "complete",
        "exclusionReasons": {"profile-not-supported": len(unknown)} if unknown else {},
        "kinds": {kind: {"available": sum(row["kind"] == kind for row in unknown), "included": 0}
                  for kind in sorted({row["kind"] for row in unknown})},
        "inventoryHash": content_hash(sorted((material_fact(row) for row in unknown), key=canonical_json))}
    return selected, coverage


def validate_evidence_packet(packet):
    if packet.get("protocolVersion") != EVIDENCE_PROTOCOL or packet.get("profile") not in {EVIDENCE_PROFILE, PORTFOLIO_EVIDENCE_PROFILE, BUSINESS_EVIDENCE_PROFILE, MACRO_EVIDENCE_PROFILE, LEGACY_EVIDENCE_PROFILE, CRYPTO_EVIDENCE_PROFILE}:
        raise EvidenceContractError("unsupported observation evidence contract")
    if not all(packet.get(key) for key in ("accountId", "symbol", "worldId", "sourceSnapshotId", "capturedAt")):
        raise EvidenceContractError("observation evidence ownership missing")
    snapshots = packet.get("sourceSnapshots", {})
    if snapshots.get(packet["worldId"]) != packet["sourceSnapshotId"]:
        raise EvidenceContractError("observation snapshot mismatch")
    coverage = packet.get("coverage", {})
    if set(coverage) != {c.name for c in CATEGORIES} | {"unclassified"}:
        raise EvidenceContractError("evidence coverage contract incomplete")
    facts = packet.get("facts", [])
    if len({row.get("id") for row in facts}) != len(facts):
        raise EvidenceContractError("duplicate evidence identity")
    for fact in facts:
        if fact.get("kind") == "relative-performance-observation" and packet["profile"] != EVIDENCE_PROFILE:
            raise EvidenceContractError("market comparison requires v6 observation profile")
        if fact.get("kind") == "crypto-asset" and packet["profile"] != CRYPTO_EVIDENCE_PROFILE:
            raise EvidenceContractError("crypto quote requires v4 observation profile")
        if packet["profile"] == LEGACY_EVIDENCE_PROFILE and fact.get("kind") == "macro-print":
            raise EvidenceContractError("macro print requires v2 observation profile")
        if snapshots.get(fact.get("sourceWorldId")) != fact.get("sourceSnapshotId"):
            raise EvidenceContractError("fact snapshot mismatch")
        if fact.get("accountId") and fact["accountId"] != packet["accountId"]:
            raise EvidenceContractError("fact account mismatch")
        if fact.get("symbol") and fact["symbol"] != packet["symbol"] and fact.get("kind") not in MACRO_KINDS:
            raise EvidenceContractError("fact subject mismatch")
    for category, row in coverage.items():
        if row["available"] != row["included"] + row["excluded"]:
            raise EvidenceContractError("unaccounted evidence loss")
        if row["included"] != sum(fact.get("evidenceCategory") == category for fact in facts):
            raise EvidenceContractError("coverage does not match delivered facts")
        if sum(kind["available"] for kind in row["kinds"].values()) != row["available"] or sum(
                kind["included"] for kind in row["kinds"].values()) != row["included"]:
            raise EvidenceContractError("evidence kind coverage mismatch")
    if packet["profile"] == CRYPTO_EVIDENCE_PROFILE:
        quotes = [fact for fact in facts if fact.get("kind") == "crypto-asset" and fact.get("symbol") == packet["symbol"]]
        try:
            valid_quote = len(quotes) == 1 and bool(quotes[0].get("currency")) and not isinstance(quotes[0].get("currentPrice"), bool) and math.isfinite(float(quotes[0]["currentPrice"])) and float(quotes[0]["currentPrice"]) > 0
        except (KeyError, ValueError, TypeError):
            valid_quote = False
        if not valid_quote:
            raise EvidenceContractError("crypto source quote fields unavailable")
    if not coverage["quote"]["included"]:
        raise EvidenceContractError("required quote evidence missing")
    if "quoteAssessment" in packet and packet["quoteAssessment"] != quote_clock_assessment(facts, packet["capturedAt"]):
        raise EvidenceContractError("quote assessment does not match source clock")
    source_packet = packet
    if "researchKnowledge" in packet:
        from digital_twin.modules.research_knowledge.contracts import validate_knowledge_context
        try:
            validate_knowledge_context(packet["researchKnowledge"], packet)
        except (ValueError, KeyError, TypeError) as error:
            raise EvidenceContractError("invalid bounded research knowledge context") from error
        # Knowledge is separately budgeted historical/methodological context.
        # It is never added to the source facts or their coverage inventory.
        source_packet = {key: value for key, value in packet.items() if key != "researchKnowledge"}
    if source_packet_bytes(source_packet) > EVIDENCE_PACKET_MAX_BYTES:
        raise EvidenceContractError("observation evidence exceeds context budget")
