"""Bounded source discovery by publication time, never by collection recency."""
from datetime import datetime, timezone
from .observation_evidence import canonical_json

VERSION = "observation-event-index-v1"
EVENTS = {"news-article", "research-evidence", "evidence:news", "evidence:filing",
          "evidence:disclosure", "fundamental-event"}
CONTEXT = {"relative-performance-observation", "macro-print", "interest-rate", "yield-curve", "fx-rate", "benchmark-index",
           "market-proxy-observation", "company", "company-relationship", "revenue-exposure",
           "customer-exposure", "supply-chain-exposure"}


def instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def build(facts, captured_at, quote_at=""):
    cutoff, quote = instant(captured_at), instant(quote_at)
    events, context = [], []
    for fact in facts:
        kind = fact.get("kind")
        if kind not in EVENTS | CONTEXT:
            continue
        # Fetch/ingestion clocks must not make an old corporate action recent.
        field = "publishedAt" if kind in EVENTS else next(
            (k for k in ("sourceAsOf", "asOf", "publishedAt") if instant(fact.get(k))), "")
        clock = instant(fact.get(field))
        if not cutoff or clock and clock > cutoff:
            continue
        row = {"factId": fact["id"], "category": fact["evidenceCategory"], "kind": kind,
               "sourceTime": fact.get(field, ""), "timeField": field,
               "timeKnown": clock is not None, "publishedAfterQuote": bool(clock and quote and clock > quote),
               "explicitlyIneligible": fact.get("judgementEvidenceUsable") is False,
               "title": str(fact.get("title") or fact.get("label") or "")[:180],
               "bytes": len(canonical_json(fact).encode()), "authority": "discovery-only-read-before-citing"}
        (events if kind in EVENTS else context).append(row)
    def order(row):
        at = instant(row["sourceTime"])
        return (row["explicitlyIneligible"], not row["timeKnown"], -(at.timestamp() if at else 0), row["factId"])
    events.sort(key=order)
    context.sort(key=order)
    return {"version": VERSION, "capturedAt": captured_at, "quoteAsOf": quote_at,
            "ordering": "eligible-first-publication-time-descending-unknown-last",
            "events": events[:12], "context": context[:12],
            "availableEvents": len(events), "availableContext": len(context),
            "omittedEvents": max(0, len(events)-12), "omittedContext": max(0, len(context)-12),
            "limitation": "Index is not evidence or an exhaustive search; original sources and comparable periods are required."}
