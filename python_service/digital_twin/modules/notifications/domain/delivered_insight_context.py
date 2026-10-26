"""Successful messages are conversation history, never new market evidence."""
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import re

VERSION = "delivered-insight-context-v1"
TYPES = ("aiObservation", "investmentInsight", "marketObservation", "researchProgress")
WINDOW_DAYS = 7
PER_TYPE = 2
BODY_BYTES = 6000


def instant(value):
    try:
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) if value.tzinfo else None
    except (TypeError, ValueError):
        return None


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def subject(job):
    context = job.context or {}
    symbol = str(context.get("rawSymbol") or context.get("symbol") or "").strip().upper()
    if job.message_type not in TYPES or not job.account_id or not symbol:
        return {}
    if context.get("accountId", job.account_id) != job.account_id:
        return {}
    return {"accountId": job.account_id, "symbol": symbol}


def excerpt(body):
    # Links can contain private preview tokens. Keep their label, not their URL.
    text = re.sub(r"<[^>]*>", "", str(body))
    text = html.unescape(text)
    text = re.sub(r"https?://[^\s<>\"']+", "[상세 링크]", text)
    encoded = text.strip().encode()
    return encoded[:BODY_BYTES].decode("utf-8", errors="ignore"), len(encoded) > BODY_BYTES


def context(rows, account_id, symbol, cutoff, *, query_limited=False):
    end = instant(cutoff)
    if not end or not account_id or not symbol:
        raise ValueError("delivered context requires an account, subject and cutoff")
    start = end - timedelta(days=WINDOW_DAYS)
    entries, seen, counts, heads, available = [], set(), {}, {}, 0
    for row in sorted(rows, key=lambda r: (r.get("deliveredAt", ""), r.get("jobId", "")), reverse=True):
        at = instant(row.get("deliveredAt"))
        kind, key = row.get("messageType"), row.get("jobId")
        if (row.get("status") != "delivered" or row.get("accountId") != account_id
                or row.get("symbol") != symbol or kind not in TYPES or not key
                or not at or not start <= at <= end or key in seen):
            continue
        seen.add(key)
        available += 1
        heads.setdefault(kind, key)
        if counts.get(kind, 0) >= PER_TYPE:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        body = row.get("body") or ""
        verified = bool(body and hashlib.sha256(body.encode()).hexdigest() == row.get("messageSha256"))
        text, truncated = excerpt(body) if verified else ("", False)
        entries.append({"jobId": key, "messageType": kind, "deliveredAt": row["deliveredAt"],
                        "messageSha256": row.get("messageSha256") or "", "text": text,
                        "textStatus": "excerpt" if truncated else "complete" if verified else "unavailable"})
    value = {"version": VERSION, "accountId": account_id, "symbol": symbol, "capturedAt": cutoff,
             "authority": "delivered-context-only", "heads": heads, "entries": entries,
             "coverage": {"windowDays": WINDOW_DAYS, "perTypeLimit": PER_TYPE,
                          "messageTypes": list(TYPES), "availableCount": available,
                          "bounded": query_limited or available > len(entries),
                          "missingBodies": sum(row["textStatus"] == "unavailable" for row in entries)}}
    value["fingerprint"] = identity(value)
    return value


def identity(value):
    # A new capture clock alone must not renew an unchanged analysis.
    return digest({k: v for k, v in value.items() if k not in {"capturedAt", "fingerprint"}})


def validate(value, account_id, symbol, cutoff):
    if (not isinstance(value, dict) or value.get("version") != VERSION
            or value.get("authority") != "delivered-context-only"
            or value.get("accountId") != account_id or value.get("symbol") != symbol
            or value.get("capturedAt") != cutoff or value.get("fingerprint") != identity(value)):
        raise ValueError("delivered context scope or identity mismatch")
    end = instant(cutoff)
    entries = value.get("entries")
    if not end or not isinstance(entries, list) or len(entries) > len(TYPES) * PER_TYPE:
        raise ValueError("invalid bounded delivery context")
    seen, heads, counts = set(), {}, {}
    for row in entries:
        at, kind, key = instant(row.get("deliveredAt")), row.get("messageType"), row.get("jobId")
        if (not key or key in seen or kind not in TYPES or not at
                or not end-timedelta(days=WINDOW_DAYS) <= at <= end
                or row.get("textStatus") not in {"complete", "excerpt", "unavailable"}
                or not isinstance(row.get("text"), str) or len(row["text"].encode()) > BODY_BYTES):
            raise ValueError("invalid delivered message reference")
        seen.add(key)
        counts[kind] = counts.get(kind, 0) + 1
        heads.setdefault(kind, key)
    if any(n > PER_TYPE for n in counts.values()) or heads != value.get("heads"):
        raise ValueError("delivered context head mismatch")
    return value
