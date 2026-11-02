"""Explicit use of dated research memory; no new facts or investment authority."""
from copy import deepcopy
import math

from digital_twin.modules.reasoning.contracts import content_hash
from .insight_contract import resolve_ref
from .research_evidence_use import usable_fact

VERSION = "analyst-memory-v1"
EFFECTS = ("strengthened", "weakened", "unchanged", "unresolved", "not-relevant")
KINDS = ("business", "valuation", "portfolio", "market-context", "correction", "none")
VALUE_ERROR = "기업·가치·구체적인 위험에 더하는 근거가 없어 가격 설명은 관찰 기록으로 보관합니다."


def enabled(packet):
    return packet.get("analystMemoryPolicy") == VERSION


def prepare(packet, research):
    """Index already captured memories; never copy their claims into facts."""
    if not enabled(packet):
        return packet
    rows = []
    for n, row in enumerate(packet.get("reviewedQuestionContext", [])[:4]):
        rows.append({"memoryId": "question:" + row["caseId"], "kind": "reviewed-question",
            "path": "reviewedQuestionContext." + str(n), "memoryHash": content_hash(row),
            "sourceFactIds": sorted({r["factId"] for r in row.get("sources", []) if r.get("factId")}
                | {r["factId"] for r in row.get("basis", []) if r.get("factId")})})
    theses = [r for r in research if r.get("kind") == "business-thesis"]
    for row in theses[:3]:
        if any(row.get(k, packet.get(k)) != packet.get(k) for k in ("accountId", "symbol", "worldId")):
            raise ValueError("analyst memory crossed subject scope")
        rows.append({"memoryId": "thesis:" + row["caseId"], "kind": "business-thesis",
            "caseId": row["caseId"], "memoryHash": content_hash(row),
            "path": "businessThesisMemory[caseId=" + row["caseId"] + "]",
            "sourceFactIds": sorted({r["factId"] for r in row.get("contract", {}).get("checkpoints", []) if r.get("factId")})})
    return {**packet, "analystMemory": {"version": VERSION, "items": rows,
        "authority": "historical-context-requires-current-source-check",
        "omittedCount": max(0, len(packet.get("reviewedQuestionContext", [])) - 4) + max(0, len(theses) - 3)}}


def protected_ids(packet, seen):
    if not enabled(packet):
        return set()
    linked = {key for row in packet.get("analystMemory", {}).get("items", []) for key in row["sourceFactIds"]}
    # These baseline views already passed complete-input admission. A later
    # model selection cannot silently remove the business basis to fit a quote.
    baseline = [key for key in packet.get("businessEvidence", {}).get("factIds", [])
                if key in seen and seen[key].get("kind") not in {"stock", "crypto-asset"}
                and seen[key].get("evidenceCategory") not in {"quality", "technical"}][:2]
    return {key for key in (linked | set(baseline)) & set(seen) if usable_fact(seen[key])}


def source_state(packet):
    facts = {f["id"]: f for f in packet.get("facts", [])}
    return [{"memoryId": r["memoryId"], "currentFactIds": [k for k in r["sourceFactIds"] if k in facts],
        "unreadFactIds": [k for k in r["sourceFactIds"] if k not in facts]}
        for r in packet.get("analystMemory", {}).get("items", [])]


def validate(value, packet):
    if not isinstance(value, dict) or set(value) != {"memories", "insightKind", "meaning", "counterEvidence"}:
        raise ValueError("analyst assessment requires memory use and investment meaning")
    if value["insightKind"] not in KINDS or any(not isinstance(value[k], str) or not 8 <= len(value[k].strip()) <= 500
                                             for k in ("meaning", "counterEvidence")):
        raise ValueError("analyst meaning must be bounded")
    rows = value["memories"]
    expected = {r["memoryId"] for r in packet.get("analystMemory", {}).get("items", [])}
    if (not isinstance(rows, list) or len(rows) != len(expected) or any(not isinstance(r, dict) for r in rows)
            or {r.get("memoryId") for r in rows} != expected):
        raise ValueError("every captured analyst memory needs an explicit disposition")
    from digital_twin.modules.decisions.contracts import narrative_presentation_errors
    for r in rows:
        if (set(r) != {"memoryId", "effect", "reason", "evidence", "nextEvidence"} or r["effect"] not in EFFECTS
                or any(not isinstance(r[k], str) or not 8 <= len(r[k].strip()) <= 500 for k in ("reason", "nextEvidence"))
                or not isinstance(r["evidence"], list) or len(r["evidence"]) > 4
                or (r["effect"] in {"strengthened", "weakened"} and not r["evidence"])):
            raise ValueError("invalid analyst memory disposition")
        for ref in r["evidence"]:
            fact, _ = resolve_ref(packet, ref)
            if ref["period"] != "current" or not usable_fact(fact, ref["field"]):
                raise ValueError("memory impact needs usable current source evidence")
    texts = [value["meaning"], value["counterEvidence"], *[r[k] for r in rows for k in ("reason", "nextEvidence")]]
    if narrative_presentation_errors("NO_ACTION", texts):
        raise ValueError("analyst memory cannot grant investment actions")
    return deepcopy(value)


def substantive_ref(packet, ref):
    """Source meaning is reviewed by AI; metadata/price facts cannot pass alone."""
    fact, value = resolve_ref(packet, ref)
    from .external_influence import AI_FIELDS
    if (ref["period"] != "current" or not usable_fact(fact, ref["field"])
            or fact.get("kind") in {"stock", "crypto-asset"}
            or fact.get("evidenceCategory") in {"technical", "quality"}
            or set(ref["field"].split(".")).intersection(AI_FIELDS | {
                "id", "title", "label", "name", "source", "sourceAsOf", "asOf", "publishedAt", "periodEnd", "status", "freshnessStatus",
                "sourceUrl", "url", "metricUnits", "metricProvenance", "sourceReferences", "sourceRevision", "reportObservationId",
                "currentPrice", "price", "changeRate", "priceChangeRate", "ma5", "ma20", "ma60", "ma20Slope", "ma60Slope", "clockSkewMinutes",
                "investmentJudgmentEligible", "judgementEvidenceUsable", "analysisSummary", "articleSummaryKo"})):
        return False
    return value is not None


def new_position_risk(packet, refs):
    before = {f["id"]: f for f in (packet.get("lastDeliveredNotification") or {}).get("facts", [])}
    cited = {(r.get("factId"), r.get("field")) for r in refs if r.get("period") == "current"}
    for f in packet.get("facts", []):
        old = before.get(f["id"], {})
        values = [f.get("positionWeight"), old.get("positionWeight"), f.get("strategyMaxPositionWeightPct"), old.get("strategyMaxPositionWeightPct")]
        if (f.get("kind") == "stock" and usable_fact(f) and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) for v in values) and values[2] == values[3] and values[0] > values[2] >= values[1]
                and {(f["id"], "positionWeight"), (f["id"], "strategyMaxPositionWeightPct")} <= cited):
            return True
    return False


def meaningful(result):
    packet = result["input"]
    refs = [r for section in ("summary", "hypothesis", "portfolioImpact") for r in result.get("claimEvidence", {}).get(section, [])]
    return any(substantive_ref(packet, ref) for ref in refs) or new_position_risk(packet, refs)


def quality_errors(result):
    if not enabled(result["input"]):
        return []
    try:
        value = validate(result.get("analystAssessment"), result["input"])
        if result.get("notification", {}).get("send") and (value["insightKind"] == "none" or not meaningful(result)):
            return [VALUE_ERROR]
        cited = result.get("evidenceIds", [])
        if any(ref["factId"] not in cited for row in value["memories"] for ref in row["evidence"]):
            return ["기억이 판단에 미친 영향을 이번 판단의 원천 근거에 연결해야 합니다."]
    except (ValueError, KeyError, TypeError):
        return ["질문·기업 가설의 기억을 이번 판단과 어떻게 대조했는지 확인해야 합니다."]
    return []


def review_errors(result, review):
    if not enabled(result["input"]):
        return []
    value = review.get("analystAssessment", {}) if isinstance(review, dict) else {}
    expected = [r["memoryId"] for r in result["input"].get("analystMemory", {}).get("items", [])]
    if (value.get("supported") is not True or value.get("substantive") is not True
            or not isinstance(value.get("memoryIds"), list) or sorted(value["memoryIds"]) != sorted(expected)
            or not isinstance(value.get("reason"), str) or not 8 <= len(value["reason"].strip()) <= 500):
        return ["기억·원문·판단의 연결과 고객에게 더하는 의미를 독립 검토에서 확인하지 못했습니다."]
    return []


def reconsideration_supported(result, verdict):
    if not enabled(result["input"]):
        return True
    refs = verdict.get("evidence", [])
    return any(substantive_ref(result["input"], r) for r in refs) or new_position_risk(result["input"], refs)
