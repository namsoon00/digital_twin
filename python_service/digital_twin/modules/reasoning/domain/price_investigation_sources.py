"""Discovery and price clocks from one immutable ABox, never live quote repair."""
from copy import deepcopy
from datetime import timedelta

from .observation_event_index import build as legacy_index, instant, EVENTS
from .observation_evidence import canonical_json, quote_use_assessment

VERSION = "price-investigation-sources-v1"
MARKET_KINDS = {"benchmark-index", "market-proxy-observation"}


def price_move_anchor(packet):
    facts = {row["id"]: row for row in packet.get("facts", [])}
    quotes = []
    for use in quote_use_assessment(packet):
        fact = facts[use["evidenceId"]]
        quotes.append({**use, "values": {key: deepcopy(fact[key]) for key in (
            "currentPrice", "changeRate", "priceChangeRate", "currency", "marketSessionStatus",
            "marketSessionLabel", "freshnessReferenceState", "observationSource") if key in fact}})
    return {"version": VERSION, "quotes": quotes,
            "comparisonBasis": "provider-reported; comparison start and base price are not established by this anchor",
            "authority": "exact-frozen-quote-only; delivery and collection clocks are not price clocks"}


def _identity(fact):
    ref = fact.get("historicalDocumentReference") or fact.get("questionSourceReference") or {}
    # Only exact source identities collapse duplicates. Similar titles do not.
    return (fact.get("documentHash") or ref.get("documentHash") or ref.get("evidenceId")
            or fact.get("url") or fact.get("sourceUrl") or fact["id"])


def content_state(fact):
    if any(isinstance(fact.get(key), str) and fact[key].strip()
           for key in ("officialDocumentText", "articleText", "body", "text")) or any(
            isinstance(fact.get(key), dict) and fact[key].get("text")
            for key in ("historicalDocumentReference", "questionSourceReference")):
        return "source-text-present"
    if fact.get("kind") not in EVENTS:
        return "context-record; verify-source-fields"
    return "metadata-or-derived-summary; original-text-not-present"


def build(facts, packet):
    facts = list(facts)
    anchor = price_move_anchor(packet)
    times = [instant(row["sourceAsOf"]) for row in anchor["quotes"] if row["datedReferenceEligible"]]
    quote = max((at for at in times if at), default=None)
    capture = instant(packet["capturedAt"])
    # Seven days is a discovery window, not a return period or a freshness rule.
    lower = (quote or capture) - timedelta(days=7)
    kept, excluded, duplicates = [], {}, 0
    events = {}
    for original in facts:
        fact = deepcopy(original)
        if fact.get("kind") not in EVENTS:
            kept.append(fact)
            continue
        classes = set(fact.get("tboxClasses", [])) | {fact.get("tboxClass")}
        financial = bool(fact.get("reportedValues") or fact.get("financialReport"))
        reason = ("price-observation" if "PriceObservation" in classes else
                  "financial-snapshot" if fact.get("sourceKind") == "unofficial-yahoo-finance-wrapper" else
                  "financial-snapshot" if financial and fact.get("publicationTimeKnown") is not True else "")
        at = instant(fact.get("publishedAt")) if fact.get("publicationTimeKnown") is not False else None
        if not reason and at and (at < lower or at > capture):
            reason = "outside-discovery-window"
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        if fact.get("publicationTimeKnown") is False:
            fact["publishedAt"] = ""  # Discovery metadata only; never changes a source read.
        identity = _identity(fact)
        prior = events.get(identity)
        if prior:
            duplicates += 1
            # Prefer the richer source record over its headline-only graph alias.
            def rank(row):
                return (row.get("judgementEvidenceUsable") is not False,
                        row.get("bodyRead") is True, row.get("kind") == "research-evidence",
                        len(canonical_json(row)), row["id"])
            if rank(fact) <= rank(prior):
                continue
        events[identity] = fact
    all_index = legacy_index(kept + list(events.values()), packet["capturedAt"],
                             quote.isoformat() if quote else "")
    # Reserve comparison slots; unrelated company/macro rows cannot hide them.
    market = legacy_index([row for row in kept if row.get("kind") in MARKET_KINDS],
                          packet["capturedAt"], quote.isoformat() if quote else "")["context"][:4]
    context = market + [row for row in all_index["context"] if row["factId"] not in {x["factId"] for x in market}]
    all_index.update(version=VERSION, context=context[:12], excludedEvents=excluded,
                     duplicateEvents=duplicates, discoveryStart=lower.isoformat(),
                     discoveryWindowIsReturnPeriod=False)
    all_index["omittedContext"] = max(0, all_index["availableContext"] - len(all_index["context"]))
    by_id = {fact["id"]: fact for fact in facts}
    eligible = [row for row in all_index["events"] if not row["explicitlyIneligible"]]
    def documented_event(row):
        fact = by_id[row["factId"]]
        classes = set(fact.get("tboxClasses", [])) | {fact.get("tboxClass")}
        return row["timeKnown"] and (fact["kind"] in {"news-article", "evidence:news", "evidence:filing", "evidence:disclosure"}
            or bool(classes.intersection({"NewsEvent", "NewsArticle", "DisclosureEvent", "DisclosureFiling", "EarningsEvent"})))
    # A generic company document in the baseline cannot satisfy a survey when
    # an actual, source-dated news/filing candidate is waiting to be read.
    focused = [row for row in eligible if documented_event(row)] or eligible
    groups = {"events": [row["factId"] for row in focused],
              "market": [row["factId"] for row in market if not row["explicitlyIneligible"]]}
    for row in all_index["events"] + all_index["context"]:
        row["bytes"] = len(canonical_json(by_id[row["factId"]]).encode())
        row["sourceContent"] = content_state(by_id[row["factId"]])
    all_index["candidateGroups"] = groups
    scope = {"version": VERSION, "candidateGroups": groups,
             "availableEvents": all_index["availableEvents"], "omittedEvents": all_index["omittedEvents"],
             "excludedEvents": excluded, "duplicateEvents": duplicates,
             "authority": "bounded-captured-source-survey; not an exhaustive web search"}
    return anchor, all_index, scope
