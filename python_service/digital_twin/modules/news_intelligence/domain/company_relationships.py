"""Conservative documentary relationships, with distinct assertion and entity identity.

This parser admits explicit issuer-owned lists/roles only. Co-mentions and causal
interpretations do not create edges. Unsupported prose stays an open research gap.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit

VERSION = "company-relationship-extraction-v1"
PATTERNS = (
    ("SUPPLIES_TO", "inbound", r"\b(?:our|the company's)\s+(?:principal |major |key )?suppliers?\s+(?:include|is|are)\s+(?P<names>[^\n;.!?]{2,180})"),
    ("SELLS_TO", "outbound", r"\b(?:our|the company's)\s+(?:principal |major |key )?customers?\s+(?:include|is|are)\s+(?P<names>[^\n;.!?]{2,180})"),
    ("COMPETES_WITH", "outbound", r"\b(?:our|the company's)\s+(?:principal |major |key )?competitors?\s+(?:include|is|are)\s+(?P<names>[^\n;.!?]{2,180})"),
    ("SUPPLIES_TO", "inbound", r"(?:당사|회사의)\s*(?:주요\s*)?공급업체(?:는|로는)\s*(?P<names>[^\n;.!?]{2,100}?)(?:입니다|이다|가 있다|이 있다)"),
    ("SELLS_TO", "outbound", r"(?:당사|회사의)\s*(?:주요\s*)?고객사(?:는|로는)\s*(?P<names>[^\n;.!?]{2,100}?)(?:입니다|이다|가 있다|이 있다)"),
    ("COMPETES_WITH", "outbound", r"(?:당사|회사의)\s*(?:주요\s*)?경쟁사(?:는|로는)\s*(?P<names>[^\n;.!?]{2,100}?)(?:입니다|이다|가 있다|이 있다)"),
)
NEGATION = re.compile(r"\b(?:no|not|former|potential|prospective|might|may|could|would|if|previously|ceased)\b|아니|과거|잠재|가능|종료|중단", re.I)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def clock(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)) else None
    except (TypeError, ValueError):
        return None


def normalized_name(value):
    # Punctuation/space/case only. Never strip corporate suffixes, share classes,
    # translate names or resolve subsidiaries to their parents.
    return "".join(char.casefold() for char in str(value) if char.isalnum())


def resolve_listed_company(name, candidates):
    exact = [row for row in candidates if row.get("active", True) and row.get("assetType", row.get("asset_type", "")) == "STOCK"
             and normalized_name(row.get("name")) == normalized_name(name)]
    unique = {(row.get("market"), row.get("symbol")): row for row in exact}
    if len(unique) != 1:
        return {"name": name, "status": "ambiguous" if unique else "unresolved", "symbol": "", "issuerId": ""}
    row = next(iter(unique.values()))
    if not row.get("symbol") or not row.get("market") or not row.get("sourceUrl", row.get("source_url")):
        return {"name": name, "status": "unresolved", "symbol": "", "issuerId": ""}
    return {"name": name, "status": "exact-listed-name", "symbol": row["symbol"], "market": row["market"],
            "issuerId": "listed-issuer:" + row["market"] + ":" + row["symbol"],
            "identitySource": row.get("sourceUrl", row.get("source_url")),
            "identityObservedAt": row.get("fetchedAt", row.get("fetched_at", ""))}


def extract_relationships(item, resolve, known_at):
    raw = item.raw_payload or {}
    text = str(raw.get("officialDocumentText") or "")
    known, published = clock(known_at), clock(item.published_at)
    url = str(item.url or raw.get("officialDocumentUrl") or "")
    source_refs = raw.get("sourceReferences") or []
    issuer = raw.get("documentIssuerIdentity") or {}
    issuer_bound = (issuer.get("symbol") == item.symbol and issuer.get("sourceUrl") == url
                    and issuer.get("verification") == "sec-discovered-issuer-document" and issuer.get("cik") and issuer.get("accessionNumber"))
    try:
        public_url = urlsplit(url)
    except ValueError:
        return {"version": VERSION, "status": "source-unavailable", "assertions": []}
    if (not known or not published or published > known or raw.get("documentVerified") is not True or not text
            or public_url.scheme != "https" or not public_url.hostname or public_url.username or public_url.password
            or not (issuer_bound or any(str(ref.get("subjectKey", "")).upper() == item.symbol.upper() and ref.get("revisionId") for ref in source_refs))):
        return {"version": VERSION, "status": "source-unavailable", "assertions": [], "reason": "verified-issuer-document-and-publication-required"}
    assertions = []
    body = text[:120000]
    for relation, direction, pattern in PATTERNS:
        for match in re.finditer(pattern, body, re.I):
            sentence_start = max(body.rfind(".", 0, match.start()), body.rfind("\n", 0, match.start())) + 1
            sentence_end = re.search(r"[.!?\n]", body[match.end():])
            end = match.end() + (sentence_end.start() if sentence_end else 0)
            context = body[sentence_start:max(end, match.end())]
            if NEGATION.search(context):
                continue
            names = re.split(r",\s*|\s+and\s+|\s*&\s*|\s+및\s+|\s*·\s*", match["names"])
            for name in names[:8]:
                name = name.strip(" ,:()\t")
                if (not 2 <= len(name) <= 100 or name.casefold() in {"inc", "ltd", "others", "other companies", "etc", "co", "corporation"}
                        or re.search(r"\b(?:which|that|who|including|such|as|with|for|and|are|is)\b|등의|등이|등을", name, re.I)
                        or not re.fullmatch(r"[A-Z가-힣][A-Za-z0-9가-힣 .&'()\-]*", name)):
                    continue
                entity = resolve(name)
                if entity.get("symbol") == item.symbol:
                    continue
                revision = str(raw.get("documentHash") or digest(text))
                assertion_id = digest([VERSION, item.evidence_id, revision, relation, direction, name, match.start(), {key: entity.get(key) for key in ("status", "symbol", "issuerId", "identitySource")}])
                assertions.append({"assertionId": assertion_id, "version": VERSION, "subjectSymbol": item.symbol,
                    "relationType": relation, "direction": direction, "counterparty": deepcopy(entity),
                    "sourceEvidenceId": item.evidence_id, "sourceRevision": revision, "sourceUrl": url,
                    "publishedAt": item.published_at, "firstKnownAt": known_at,
                    "reportingPeriod": raw.get("reportDate") or raw.get("periodEnd") or "",
                    "validFrom": "", "validTo": "", "currentness": "not-reconfirmed",
                    "assertionState": "source-stated", "identityStatus": entity["status"],
                    "excerpt": match[0], "excerptStart": match.start(), "excerptEnd": match.end(),
                    "exposure": {"value": None, "status": "not-disclosed"},
                    "investmentActionAuthority": False})
                if len(assertions) >= 12:
                    break
            if len(assertions) >= 12:
                break
        if len(assertions) >= 12:
            break
    return {"version": VERSION, "status": "extracted" if assertions else "no-supported-statement",
            "assertions": assertions, "textTruncated": len(text) > len(body),
            "scope": "explicit-issuer-lists-only", "coverage": "partial"}


def valid_assertions(item):
    """Projection checks provenance again, including retracted/changed source text."""
    raw = item.raw_payload or {}
    if (raw.get("documentVerified") is not True or item.lifecycle_state != "active"
            or raw.get("evidenceLifecycleState", "active") != "active"):
        return []
    rows = (raw.get("companyRelationships") or {}).get("assertions", [])
    identities = {row["counterparty"]["name"]: row["counterparty"] for row in rows
                  if isinstance(row, dict) and isinstance(row.get("counterparty"), dict) and row["counterparty"].get("name")}
    verified, result = {}, []
    for row in rows:
        if not isinstance(row, dict) or not clock(row.get("firstKnownAt")):
            continue
        known = row["firstKnownAt"]
        if known not in verified:
            verified[known] = extract_relationships(item, lambda name: identities.get(name, {
                "name": name, "status": "unresolved", "symbol": "", "issuerId": ""}), known)["assertions"]
        # Rebuild the complete claim, not just its excerpt: direction, dates,
        # issuer binding, source URL and assertion identity must still match.
        if row in verified[known]:
            result.append(deepcopy(row))
    return result
