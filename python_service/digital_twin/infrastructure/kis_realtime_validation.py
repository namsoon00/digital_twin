"""KIS wire validation and stage-local provenance, before quote persistence."""

import math
import re
from typing import Dict


KIS_REALTIME_VALIDATION_VERSION = "kis-realtime-validated-v1"
KIS_REALTIME_STAGE_FIELDS = {
    "ccnl": (
        "currentPrice", "changeRate", "volume", "volumeRatio", "tradingValue",
        "tradeStrength", "buyVolume", "sellVolume",
    ),
    "orderbook": ("orderbookBidVolume", "orderbookAskVolume", "bidAskImbalance", "volume"),
}


def valid_kis_wire_row(row: Dict[str, object], stage: str) -> bool:
    # Do not pad an arbitrary quantity into a plausible six-digit stock code.
    if not re.fullmatch(r"[0-9]{6}", str(row.get("mksc_shrn_iscd") or "")):
        return False
    clock = str(row.get("stck_cntg_hour" if stage == "ccnl" else "bsop_hour") or "")
    if not re.fullmatch(r"[0-9]{6}", clock):
        return False
    if int(clock[:2]) > 23 or int(clock[2:4]) > 59 or int(clock[4:]) > 59:
        return False
    nonnegative = (
        ("stck_prpr", "wghn_avrg_stck_prc", "stck_oprc", "stck_hgpr", "stck_lwpr",
         "askp1", "bidp1", "cntg_vol", "acml_vol", "acml_tr_pbmn", "cttr",
         "seln_cntg_csnu", "shnu_cntg_csnu", "seln_cntg_smtn", "shnu_cntg_smtn",
         "total_askp_rsqn", "total_bidp_rsqn", "prdy_vol_vrss_acml_vol_rate")
        if stage == "ccnl" else
        tuple(key for key in row if key.startswith(("askp", "bidp")))
        + ("acml_vol", "total_askp_rsqn", "total_bidp_rsqn", "antc_cnpr", "antc_cnqn", "antc_vol")
    )
    try:
        for field in nonnegative + ("prdy_ctrt",):
            value = row.get(field)
            if value in (None, ""):
                continue
            numeric = float(value)
            if not math.isfinite(numeric) or (field in nonnegative and numeric < 0):
                return False
        if stage == "ccnl":
            price = float(row.get("stck_prpr") or 0)
            low = float(row.get("stck_lwpr") or 0)
            high = float(row.get("stck_hgpr") or 0)
            if price <= 0 or not price.is_integer():
                return False
            if (low > 0 and price < low) or (high > 0 and price > high):
                return False
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def is_websocket_stage(stage: Dict[str, object]) -> bool:
    return stage.get("transport") == "websocket" or stage.get("cadence") == "websocket"


def validated_websocket_stage(payload: Dict[str, object], stage: str) -> bool:
    coverage = payload.get("marketSignalCoverage") or {}
    item = (coverage.get(stage) or {}) if isinstance(coverage, dict) else {}
    if not isinstance(item, dict) or item.get("validationVersion") != KIS_REALTIME_VALIDATION_VERSION:
        return False
    values = item.get("values")
    if not isinstance(values, dict) or values.get("symbol") != payload.get("symbol"):
        return False
    if not re.fullmatch(r"[0-9]{6}", str(values.get("symbol") or "")):
        return False
    try:
        for key in KIS_REALTIME_STAGE_FIELDS.get(stage, ()):
            if key not in values:
                continue
            value = float(values[key])
            if not math.isfinite(value) or (key not in {"changeRate", "bidAskImbalance"} and value < 0):
                return False
        if stage == "ccnl" and float(values.get("currentPrice") or 0) <= 0:
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    return bool(item.get("fetchedAt"))


def has_unvalidated_websocket_stage(payload: Dict[str, object]) -> bool:
    coverage = payload.get("marketSignalCoverage") or {}
    for stage in KIS_REALTIME_STAGE_FIELDS:
        item = coverage.get(stage) if isinstance(coverage, dict) else None
        if isinstance(item, dict) and is_websocket_stage(item) and not validated_websocket_stage(payload, stage):
            return True
    return False
