"""Read only recorded source packets; no vendor refetch or arbitrary field evaluation."""

from datetime import timedelta
import hashlib
import json

from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.modules.model_registry.domain.experiment_observations import assess_requirement, freeze_dataset
from digital_twin.modules.model_registry.domain.ontology_evolution import timestamp


METRICS = {
    "source-packet": ("판단 시점 원천 자료", "packet", None),
    "price": ("현재가", "quote-currency", "current_price"),
    "volume": ("거래량", "shares", "volume"),
    "profitLossRate": ("보유 손익률", "percent", "profit_loss_rate"),
}
MAX_DATASET_BYTES = 2_000_000


def validate_dataset_size(dataset):
    if len(json.dumps(dataset, ensure_ascii=False).encode()) > MAX_DATASET_BYTES:
        raise ValueError("experiment-packet-size-limit")
    return dataset


def capabilities(cadence_seconds=180):
    return {key: {"supported": True, "label": value[0], "unit": value[1],
                  "collector": "verified-monitor-source", "cadenceSeconds": cadence_seconds,
                  "retentionMinutes": 1440} for key, value in METRICS.items()}


def source_packet(row):
    payload = _json_loads(row.get("payload_json"), {})
    # The existing source contract uses ensure_ascii=True and a full canonical hash.
    actual = hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True,
                                      separators=(",", ":"), default=str).encode()).hexdigest()
    if actual != row.get("fingerprint"):
        raise ValueError("source-packet-content-mismatch")
    if row.get("mode") != "live":
        raise ValueError("source-packet-not-live")
    return {"snapshotId": row["snapshot_id"], "accountId": row["account_id"],
            "generatedAt": row["generated_at"], "recordedAt": row["created_at"],
            "fingerprint": actual, "contract": row["contract_version"],
            "symbols": _json_loads(row.get("symbols_json"), []), "payload": payload}


def metric_sample(source, symbol, metric):
    observed_at, recorded_at = source["generatedAt"], source["recordedAt"]
    if metric == "source-packet":
        value = source["snapshotId"]
        currency = ""
    else:
        collections = [source["payload"].get(key) or {} for key in ("positions", "watchlist")]
        matches = [row for collection in collections
                   for row in (collection.values() if isinstance(collection, dict) else collection)
                   if isinstance(row, dict) and str(row.get("symbol") or "").upper() == symbol]
        if not matches:
            return None
        values = [row.get(METRICS[metric][2]) for row in matches]
        if any(value != values[0] for value in values):
            return None
        value = values[0]
        currency = str(matches[0].get("currency") or "")
        observed_at = matches[0].get("source_as_of")
        if not timestamp(observed_at):
            return None
        fetched = timestamp(matches[0].get("source_fetched_at"))
        if fetched and fetched > timestamp(recorded_at):
            recorded_at = fetched.isoformat()
        if metric == "profitLossRate" and not matches[0].get("quantity"):
            return None
        if metric == "price" and not currency:
            return None
        if type(value) not in {float, int}:
            return None
        if metric == "price" and value <= 0:
            return None
    return {"value": value, "unit": METRICS[metric][1], "currency": currency,
            "observedAt": observed_at, "recordedAt": recorded_at,
            "sourceSnapshotId": source["snapshotId"], "sourceFingerprint": source["fingerprint"]}


def read_dataset(connection, plan, boundary, captured_at):
    row = connection.execute(
        "SELECT snapshot_id, account_id, generated_at, created_at, mode, contract_version, "
        "fingerprint, symbols_json, payload_json FROM verified_reasoning_source_snapshots "
        "WHERE snapshot_id = %s AND account_id = %s",
        (boundary.get("snapshotId"), plan["accountId"]),
    ).fetchone()
    if not row:
        raise ValueError("historical-source-packet-unavailable")
    source = source_packet(row)
    if (boundary.get("fingerprint") and boundary["fingerprint"] != source["fingerprint"]
            or boundary.get("generatedAt") and timestamp(boundary["generatedAt"]) != timestamp(source["generatedAt"])):
        raise ValueError("source-boundary-mismatch")
    if timestamp(source["generatedAt"]) > timestamp(source["recordedAt"]):
        raise ValueError("source-clock-order-invalid")
    requirements = plan["observationRequirements"]["inputs"]
    lookback = max(item["lookbackMinutes"] for item in requirements)
    sources = [source]
    if 0 < lookback <= 1440:
        start = (timestamp(source["generatedAt"]) - timedelta(minutes=lookback)).isoformat().replace("+00:00", "Z")
        rows = connection.execute(
            "SELECT snapshot_id, account_id, generated_at, created_at, mode, contract_version, "
            "fingerprint, symbols_json, JSON_EXTRACT(payload_json, '$.positions') AS positions_json, "
            "JSON_EXTRACT(payload_json, '$.watchlist') AS watchlist_json FROM verified_reasoning_source_snapshots "
            "WHERE account_id = %s AND generated_at >= %s AND generated_at <= %s "
            "AND created_at <= %s ORDER BY generated_at LIMIT 1001",
            (plan["accountId"], start, source["generatedAt"], source["recordedAt"]),
        ).fetchall()
        if len(rows) > 1000:
            raise ValueError("experiment-input-read-limit")
        # Keep exact requested scalar observations, not every historical news/account archive.
        # The source hash identifies its immutable owner packet; the dataset hashes these copied values in full.
        sources = [{"snapshotId": row["snapshot_id"], "accountId": row["account_id"],
                    "generatedAt": row["generated_at"], "recordedAt": row["created_at"],
                    "fingerprint": row["fingerprint"],
                    "payload": {"positions": _json_loads(row.get("positions_json"), {}),
                                "watchlist": _json_loads(row.get("watchlist_json"), {})}}
                   for row in rows if row.get("mode") == "live"]
    coverage = []
    available = capabilities(plan["observationRequirements"].get("collectorCadenceSeconds", 180))
    for requirement in requirements:
        metric = requirement["metric"]
        samples = [metric_sample(item, plan["symbol"], metric) for item in sources] if metric in METRICS else []
        samples = [item for item in samples if item is not None]
        # Currency conversion must be explicit, not a splice of different instruments/units.
        if len({item["currency"] for item in samples}) > 1:
            samples = []
        coverage.append(assess_requirement(requirement, available.get(metric), samples,
                                          as_of=source["generatedAt"], known_at=source["recordedAt"], historical=True))
    return freeze_dataset(plan, source, coverage, captured_at=captured_at)
