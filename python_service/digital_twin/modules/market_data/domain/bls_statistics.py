"""Latest BLS statistical vintages, deliberately not original release results."""

import hashlib
import json
import math
import re
from datetime import datetime, timezone


BLS_API_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
BLS_SERIES = ("CUSR0000SA0", "CUUR0000SA0", "CES0000000001", "LNS14000000")


def _period(index):
    return str(index // 12) + "-" + str(index % 12 + 1).zfill(2)


def parse_bls_statistics(payload, now=None):
    if not isinstance(payload, dict) or payload.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError("BLS public API did not return successful statistics")
    current = now or datetime.now(timezone.utc)
    this_month = current.year * 12 + current.month - 1
    series = {}
    for row in (payload.get("Results") or {}).get("series") or []:
        identity = row.get("seriesID")
        if identity not in BLS_SERIES or identity in series:
            raise ValueError("Unexpected or duplicate BLS series")
        data = {}
        for point in row.get("data") or []:
            if not re.fullmatch(r"M(0[1-9]|1[0-2])", str(point.get("period") or "")):
                continue
            if str(point.get("value") or "").strip() == "-":
                continue
            index = int(point["year"]) * 12 + int(point["period"][1:]) - 1
            value = float(point["value"])
            if index >= this_month or not math.isfinite(value) or value < 0 or index in data:
                raise ValueError("Invalid, duplicate or future BLS observation")
            data[index] = value
        if not data:
            raise ValueError("BLS series has no usable monthly observations")
        series[identity] = data
    if set(series) != set(BLS_SERIES):
        raise ValueError("BLS response is missing requested series")
    results = {}
    definitions = [
        ("cpi", "cpi-mom", "소비자물가 전월 대비", BLS_SERIES[0], 1, "%", "(current / previous - 1) * 100"),
        ("cpi", "cpi-yoy", "소비자물가 전년 동월 대비", BLS_SERIES[1], 12, "%", "(current / yearAgo - 1) * 100"),
        ("employment", "payrolls", "비농업 고용 전월 대비", BLS_SERIES[2], 1, "명", "(current - previous) * 1000"),
        ("employment", "unemployment-rate", "실업률", BLS_SERIES[3], 0, "%", "reported level"),
    ]
    for indicator, key, label, identity, offset, unit, formula in definitions:
        points = series[identity]
        index = max(points)
        actual = points[index]
        prior = points.get(index - offset) if offset else None
        if offset and (prior is None or prior <= 0):
            raise ValueError("BLS comparison period missing")
        value = (actual - prior) * 1000 if key == "payrolls" else (actual / prior - 1) * 100 if offset else actual
        inputs = [{"seriesId": identity, "period": _period(index), "value": actual}]
        if offset:
            inputs.append({"seriesId": identity, "period": _period(index - offset), "value": prior})
        group = results.setdefault(indicator, {"referencePeriod": _period(index), "metrics": []})
        if group["referencePeriod"] != _period(index):
            raise ValueError("BLS indicator series reference periods disagree")
        group["metrics"].append({"key": key, "label": label, "actual": round(value, 4), "unit": unit,
            "basis": "calculated-from-latest-series" if offset else "latest-reported-series", "formula": formula,
            "inputs": inputs, "consensus": None})
    source_data = {identity: {_period(index): points[index] for index in sorted(points)[-25:]} for identity, points in series.items()}
    digest = hashlib.sha256(json.dumps(source_data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"version": "bls-statistical-vintage-v1", "source": "BLS Public Data API", "sourceUrl": BLS_API_URL,
            "sourceHash": digest, "sourceData": source_data, "indicators": results,
            "publicationTimeKnown": False, "originalReleaseVintage": False, "decisionAuthority": False}


def verified_bls_statistics(data, now=None):
    try:
        rows = [{"seriesID": key, "data": [{"year": period[:4], "period": "M" + period[5:], "value": value}
                for period, value in points.items()]} for key, points in data["sourceData"].items()]
        rebuilt = parse_bls_statistics({"status": "REQUEST_SUCCEEDED", "Results": {"series": rows}}, now)
        return rebuilt if all(data.get(key) == rebuilt[key] for key in ["version", "sourceUrl", "sourceHash", "indicators", "originalReleaseVintage"]) else None
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None
