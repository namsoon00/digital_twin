"""Purpose and labels for dated observation prices, independent of send switches."""
from digital_twin.modules.reasoning.contracts import quote_clock_assessment


PRICE_PRESENTATION_VERSION = "observation-price-basis-v1"


def observation_price_basis(quote, assessment):
    status = assessment.get("status", "missing-time")
    timestamp = quote.get("sourceTimestampState", "")
    clock_label = "시세 기준"
    if timestamp in {"queried-at-fallback", "fetched-at-fallback"}:
        clock_label = "시세 조회 시각"
    elif timestamp == "websocket-received":
        clock_label = "시세 수신 시각"
    if status in {"missing-time", "invalid-time"}:
        purpose, label, reason = "unverified-reference", "시점 미확인 가격", "시세가 관측된 시각을 확인할 수 없어 현재 흐름을 설명하는 근거로 사용할 수 없습니다."
    elif status == "stale":
        purpose, label, reason = "historical-reference", "과거 참고 가격", "갱신 기준을 넘긴 시세입니다. 아래 해석은 해당 시점의 자료에 관한 것이며 현재 흐름은 새 시세로 확인해야 합니다."
    elif status == "unknown-budget":
        purpose, label, reason = "unverified-reference", "갱신 기준 미확인 가격", "시세의 갱신 기준을 확인하지 못해 현재 흐름을 설명하는 근거로 사용할 수 없습니다."
    elif timestamp in {"queried-at-fallback", "fetched-at-fallback"} or quote.get("sourceTimestampPresent") is False:
        purpose, label, reason = "unverified-reference", "조회 시점 참고 가격", "자료를 조회한 시각만 확인됩니다. 실제 시세 시각은 확인되지 않았습니다."
    elif assessment.get("referenceState") == "last-close":
        purpose, label, reason = "last-close-reference", "마감 참고 가격", "출처의 마감 참고값입니다. 아래 해석은 해당 시점의 자료에 관한 것이며 현재 체결가격을 뜻하지 않습니다."
    elif quote.get("judgementEvidenceUsable") is False:
        purpose, label, reason = "ineligible-reference", "참고 가격", "저장된 자료 품질 기준에서 판단 근거로 제외된 가격입니다. 갱신 기준 이내라도 판단에 사용할 수 있다는 뜻은 아닙니다."
    elif quote.get("judgementEvidenceUsable") is not True:
        purpose, label, reason = "unverified-reference", "참고 가격", "판단에 사용할 수 있는 자료인지 확인되지 않았습니다."
    else:
        purpose, label, reason = "recent-observation", "최근 관측 가격", "표시된 시각의 관측값이며 허용된 갱신 기준 안에 있습니다."
    return {"version": PRICE_PRESENTATION_VERSION, "purpose": purpose, "label": label, "reason": reason,
            "currentUseAllowed": purpose == "recent-observation", "sourceClockLabel": clock_label,
            **{key: assessment.get(key) for key in ("evidenceId", "sourceAsOf", "status", "ageMinutes", "maxAgeMinutes", "referenceState")}}


def price_basis_for_result(result, checked_at):
    packet = result["input"]
    quote = next((row for row in packet["facts"] if row.get("kind") == "stock"), {})
    assessment = quote_clock_assessment(packet["facts"], checked_at)
    row = next((row for row in assessment["quotes"] if row["evidenceId"] == quote.get("id")), {})
    return observation_price_basis(quote, row)
