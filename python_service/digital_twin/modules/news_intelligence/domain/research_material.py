"""Content identity for research scheduling, never evidence or review authority.

Audit identities retain immutable source revisions. Scheduling compares the
source's content separately so another fetch of the same report is not new work.
"""
import hashlib
import json


VERSION = "research-material-v1"


def research_material_fingerprint(packet):
    values = packet.get("reportedValues") or {}
    passages = packet.get("documentPassages") or []
    material = {key: packet.get(key) for key in (
        "symbol", "kind", "source", "sourceUrl", "periodEnd", "frequency",
        "durationBases", "metricUnits", "documentType", "reportDate", "documentScope",
    ) if packet.get(key) not in (None, "", [], {})}
    if values:
        material["reportedValues"] = values
        material["metricProvenance"] = {metric: {key: row.get(key) for key in (
            "provider", "currency", "scope", "durationBasis", "period", "periodStart", "derived",
        ) if row.get(key) not in (None, "")} for metric, row in (packet.get("metricProvenance") or {}).items()}
    if passages:
        material["passages"] = sorted({str(row.get("quote") or "") for row in passages if row.get("quote")})
    if packet.get("sourceTextHash"):
        material["sourceTextHash"] = packet["sourceTextHash"]
    if packet.get("filingHistory"):
        from .filing_history import history_material
        material["filingHistory"] = history_material(packet["filingHistory"])
    if not values and not passages and not packet.get("sourceTextHash"):
        # A headline or an AI paraphrase cannot reopen a stalled source question.
        return ""
    return hashlib.sha256(json.dumps({"version": VERSION, **material}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def research_source_packet(item):
    """Use source-owned values only; omit collection clocks and AI summaries."""
    raw = item.raw_payload or {}
    body = str(raw.get("articleText") or raw.get("officialDocumentText") or "")
    return {"symbol": item.symbol, "kind": item.kind, "source": item.source, "sourceUrl": item.url,
        **{key: raw.get(key) for key in ("periodEnd", "frequency", "durationBases", "reportedValues",
            "metricUnits", "metricProvenance", "documentPassages", "reportDate")},
        "periodEnd": raw.get("periodEnd") or raw.get("reportPeriodEnd") or "",
        "documentType": raw.get("officialDocumentType", ""),
        "documentScope": raw.get("officialDocumentScope", ""),
        "sourceTextHash": hashlib.sha256(body.encode()).hexdigest() if body else "",
        **({"filingHistory": raw["filingHistory"]} if raw.get("filingHistory") else {})}
