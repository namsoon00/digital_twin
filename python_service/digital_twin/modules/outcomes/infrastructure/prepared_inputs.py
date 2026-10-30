"""Durable, revision-checked outcome inputs shared by subject jobs and workers.

No TTL grants validity. Reuse requires unchanged committed dependencies and a
cutoff interval containing no newly visible outcome or qualification receipt.
Coverage/overdue counts are deliberately outside this cache.
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import time

from digital_twin.modules.decisions.contracts import canonical_investment_timestamp, parse_investment_timestamp
from digital_twin.modules.outcomes.infrastructure.prepared_input_schema import CONTRACT

MAX_BYTES = 4 * 1024 * 1024


def revision_token(connection):
    row = connection.execute(
        "SELECT c.generation AS cache_generation, r.generation, r.revision "
        "FROM outcome_input_cache_control c LEFT JOIN outcome_input_revisions r ON r.scope_key = 'calibration-history' "
        "WHERE c.contract = %s", (CONTRACT,),
    ).fetchone()
    if not row:
        return ""
    return ":".join(str(row.get(key) or "0") for key in ("cache_generation", "generation", "revision"))


def next_visibility_boundary(connection, account_id, cutoff):
    # Account-wide bounds are conservative for symbol reads. The shadow loader
    # scopes by episode.account_id, so use that same join (not outcome metadata).
    row = connection.execute(
        "SELECT MIN(boundary) AS boundary FROM ("
        "SELECT MIN(observed_at) AS boundary FROM investment_decision_outcomes WHERE account_id = %s AND observed_at > %s "
        "UNION ALL SELECT MIN(o.observed_at) FROM investment_hypothesis_observation_outcomes o "
        "JOIN investment_hypothesis_observation_episodes e ON e.episode_id = o.observation_episode_id "
        "WHERE e.account_id = %s AND o.observed_at > %s) future",
        (account_id, cutoff, account_id, cutoff),
    ).fetchone() or {}
    boundary = str(row.get("boundary") or "")
    # Qualification timestamps can carry offsets, unlike canonical SQL clocks.
    # Parse them exactly as the qualification reader does, never compare raw JSON
    # timestamp strings. This scalar catalog read happens only on a cache miss.
    rows = connection.execute(
        "SELECT JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.evolution.assessment.evaluatedAt')) AS evaluated_at "
        "FROM hypothesis_development_cases WHERE status IN ('evolution-monitoring', 'strengthened')",
    ).fetchall()
    qualification_boundary = ""
    parsed_cutoff = parse_investment_timestamp(cutoff)
    for item in rows:
        stamp = canonical_investment_timestamp(item.get("evaluated_at"))
        if stamp and parse_investment_timestamp(stamp) > parsed_cutoff:
            if not qualification_boundary or parse_investment_timestamp(stamp) < parse_investment_timestamp(qualification_boundary):
                qualification_boundary = stamp
    return boundary, qualification_boundary


def prepared_input(*, connect, account_id, symbol, as_of, kind, parameters, load):
    started = time.perf_counter()
    receipt = {"status": "disabled", "kind": kind, "historyRead": True}
    cutoff = canonical_investment_timestamp(as_of)
    if not account_id or not cutoff:
        return load(), receipt
    key = sha256(json.dumps([CONTRACT, kind, str(account_id), str(symbol).upper(), parameters],
                            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    token = ""
    try:
        with connect() as connection:
            token = revision_token(connection)
            if token:
                row = connection.execute(
                    "SELECT revision_token, from_at, until_at, qualification_until_at, payload_json FROM prepared_outcome_inputs WHERE cache_key = %s",
                    (key,),
                ).fetchone()
                if (row and row["revision_token"] == token and row["from_at"] <= cutoff
                        and parse_investment_timestamp(row["from_at"]) <= parse_investment_timestamp(cutoff)
                        and (not row["until_at"] or cutoff < row["until_at"])
                        and (not row["qualification_until_at"] or parse_investment_timestamp(cutoff) < parse_investment_timestamp(row["qualification_until_at"]))):
                    value = json.loads(row["payload_json"])
                    # A concurrent commit between the lookup and payload read
                    # invalidates the hit, including on another worker process.
                    if revision_token(connection) == token:
                        return value, {"status": "hit", "kind": kind, "historyRead": False,
                                       "runtimeMs": int((time.perf_counter() - started) * 1000)}
            receipt["status"] = "miss" if token else "unavailable"
    except Exception:
        # Missing schema, a cache read failure or corrupt cache is never stale
        # authority. The authoritative loader below still propagates its errors.
        token = ""
        receipt["status"] = "unavailable"
    value = load()
    if token:
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if len(encoded.encode()) > MAX_BYTES:
                receipt["status"] = "oversize"
            else:
                with connect() as connection:
                    boundary, qualification_boundary = next_visibility_boundary(connection, account_id, cutoff)
                    if revision_token(connection) != token:
                        receipt["status"] = "changed-during-read"
                    else:
                        stamp = datetime.now(timezone.utc)
                        connection.execute(
                            "INSERT INTO prepared_outcome_inputs (cache_key, revision_token, from_at, until_at, qualification_until_at, payload_json, updated_at) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE revision_token = VALUES(revision_token), "
                            "from_at = VALUES(from_at), until_at = VALUES(until_at), qualification_until_at = VALUES(qualification_until_at), "
                            "payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)",
                            (key, token, cutoff, boundary, qualification_boundary, encoded, stamp.isoformat().replace("+00:00", "Z")),
                        )
                        # Bounded expiry is storage hygiene, never a validity rule.
                        connection.execute("DELETE FROM prepared_outcome_inputs WHERE updated_at < %s ORDER BY updated_at LIMIT 32",
                                           ((stamp - timedelta(days=1)).isoformat().replace("+00:00", "Z"),))
        except Exception:
            receipt["status"] = "write-unavailable"
    receipt["runtimeMs"] = int((time.perf_counter() - started) * 1000)
    return value, receipt
