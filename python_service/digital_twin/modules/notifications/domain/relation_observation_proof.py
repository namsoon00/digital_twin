"""Freeze and explain the native model inputs without reconstructing evidence."""

from copy import deepcopy
from datetime import datetime
import re

from digital_twin.modules.notifications.domain.alert_formatting import price_money
from digital_twin.modules.notifications.domain.observation_market_snapshot import clock, decimal, numeric


WINDOW_FIELDS = (
    "evidenceId", "sourceFeatureSnapshotId", "symbol", "knowledgeCutoffAt",
    "windowKey", "observedAt", "startAt", "endAt", "sampleCount",
    "hasSufficientHistory", "validObservationRatio", "staleObservationCount",
    "startPrice", "currentPrice", "priceChangePct", "recentPriceChangePct",
    "drawdownFromPeakPct", "reboundFromTroughPct", "priceVelocityChangePct",
    "volumeRatioEnd", "tradeStrengthEnd", "bidAskImbalanceEnd", "riskEventCount",
    "supportEventCount", "currency", "provider",
)


def capture_model_proof(properties, symbol):
    """Keep exact source versions and linked windows, including unusable ones."""
    ids = list(dict.fromkeys(value for value in properties.get("modelEvidenceIds") or [] if isinstance(value, str)))
    proof = {key: deepcopy(properties[key]) for key in (
        "sourceFeatureSnapshotId", "knowledgeCutoffAt", "releaseId",
    ) if isinstance(properties.get(key), str)}
    proof["modelEvidenceIds"] = ids
    proof["measuredFactIds"] = [value.split("#", 1)[1] for value in ids if value.startswith("stock:" + symbol + "#")]
    proof["sourceTemporalWindows"] = [
        {key: deepcopy(window[key]) for key in WINDOW_FIELDS
         if key in window and isinstance(window[key], (str, int, float, bool))}
        for window in properties.get("sourceTemporalWindows") or [] if isinstance(window, dict)
    ]
    return proof


def source_time(value):
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return at if at.tzinfo else None
    except (TypeError, ValueError):
        return None


def window_limitation(window, condition, symbol):
    identity = str(window.get("evidenceId") or "")
    parts = identity.split("|")
    if (identity not in (condition.get("modelEvidenceIds") or []) or len(parts) != 3
            or parts[0] != "stock:" + symbol or parts[1] != "HAS_TEMPORAL_WINDOW"
            or not parts[2].startswith("temporal-window:" + symbol + ":") or window.get("symbol") != symbol):
        return "기간 수치를 이 종목의 규칙 근거와 연결할 수 없어 수치 설명에서 제외했습니다."
    version = condition.get("sourceFeatureSnapshotId")
    if not version or window.get("sourceFeatureSnapshotId") != version:
        return "기간 수치의 원본 자료 버전이 일치하지 않아 수치 설명에서 제외했습니다."
    cutoff, window_at = source_time(condition.get("knowledgeCutoffAt")), source_time(window.get("knowledgeCutoffAt"))
    if cutoff is None or window_at is None:
        return "기간 수치의 기준 시각을 확인할 수 없어 수치 설명에서 제외했습니다."
    if window_at > cutoff:
        return "분석 기준 시각 이후의 기간 수치는 이번 가설의 설명에서 제외했습니다."
    return ""


def window_name(value):
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([MHD])", str(value or ""), re.IGNORECASE)
    if match:
        return match[1] + {"M": "분", "H": "시간", "D": "일"}[match[2].upper()] + " 구간"
    return "장중 구간" if value == "SESSION" else "저장된 관측 구간"


def window_paragraph(window, currency):
    """All returns are source measurements, not inferred causes or probabilities."""
    label = window_name(window.get("windowKey"))
    samples = numeric(window.get("sampleCount"))
    intro = label + ("에 저장된 가격 " + decimal(samples, 0) + "건" if samples is not None else "의 가격 기록")
    start, end = numeric(window.get("startPrice")), numeric(window.get("currentPrice"))
    change = numeric(window.get("priceChangePct"))
    text = intro
    if start is not None and end is not None and start > 0 and end > 0:
        text += "을 보면 가격은 " + price_money(start, currency) + " → " + price_money(end, currency)
        text += " (구간 등락 " + decimal(change, signed=True) + "%)" if change is not None else ""
        text += "로 기록됐습니다."
    elif change is not None:
        text += "을 보면 구간 등락률은 " + decimal(change, signed=True) + "%입니다."
    else:
        text += "을 사용했습니다."
    measurements = []
    for key, name, unit in (
        ("drawdownFromPeakPct", "구간 고점 대비", "%"),
        ("reboundFromTroughPct", "구간 저점 대비", "%"),
        ("recentPriceChangePct", "후반 가격 구간 등락", "%"),
        ("priceVelocityChangePct", "앞 구간 대비 후반 등락률 변화", "%p"),
    ):
        value = numeric(window.get(key))
        if value is not None:
            measurements.append(name + " " + decimal(value, signed=True) + unit)
    if measurements:
        text += " " + ", ".join(measurements) + "입니다."
    flow = []
    for key, name, unit in (("tradeStrengthEnd", "체결강도", ""), ("volumeRatioEnd", "거래량 비율", "배")):
        value = numeric(window.get(key))
        # Legacy windows store numeric zero when a feed did not provide flow.
        if value is not None and value > 0:
            flow.append(name + " " + decimal(value) + unit)
    if flow:
        text += " 이 기간 데이터에 저장된 마지막 값은 " + ", ".join(flow) + "입니다."
    text += " 기간 자료 기준은 " + clock(window.get("knowledgeCutoffAt")) + "입니다."
    if window.get("hasSufficientHistory") is not True:
        text += " 필요한 기간 관측이 충분히 확보됐는지는 미확인입니다." if window.get("hasSufficientHistory") is None else " 필요한 기간 관측이 부족한 참고 수치입니다."
    return text


def model_evidence_paragraphs(rules, symbol, currency):
    result, seen, windows = [], set(), 0
    relative_assets = []
    for rule in rules:
        for condition in rule.get("conditions") or []:
            for window in condition.get("sourceTemporalWindows") or []:
                key = (window.get("evidenceId"), window.get("sourceFeatureSnapshotId"), condition.get("sourceFeatureSnapshotId"))
                if key in seen:
                    continue
                seen.add(key)
                limitation = window_limitation(window, condition, symbol)
                if limitation:
                    result.append(limitation)
                    continue
                windows += 1
                if windows <= 3:
                    result.append(window_paragraph(window, str(window.get("currency") or currency)))
            for identity in condition.get("modelEvidenceIds") or []:
                prefix = "stock:" + symbol + "|HAS_RELATIVE_PERFORMANCE|relative-performance-observation:" + symbol + ":"
                if identity.startswith(prefix):
                    asset = identity[len(prefix):]
                    if re.fullmatch(r"[A-Z0-9.^_-]{1,20}", asset):
                        relative_assets.append("비트코인(BTC)" if asset == "BTC" else asset)
    if windows > 3:
        result.append("추가 기간 자료 " + str(windows - 3) + "개는 전체 근거에서 확인할 수 있습니다.")
    if relative_assets:
        result.append("이 규칙에는 " + "·".join(dict.fromkeys(relative_assets))
                      + "와의 상대 성과 비교가 연결돼 있습니다. 다만 비교 대상의 등락률과 상대 차이 수치는 이번 기록에 저장되지 않아 비교 강도를 수치로 설명할 수 없습니다.")
    return list(dict.fromkeys(result))
