"""Evidence-bound observation explanations; no investment action authority.

Numbers are rendered from named facts. Free prose describes meaning and is
separately reviewed against those exact facts before customer publication.
"""
from datetime import datetime, timezone
import math
import re

from digital_twin.modules.decisions.contracts import narrative_presentation_errors
from digital_twin.modules.reasoning.contracts import content_hash, material_fact
from digital_twin.modules.ai_orchestration.domain.observation_wording import OBSERVATION_WORDING_VERSION


INSIGHT_VERSION = "observation-insight-v1"
CAUSAL_GUARD_VERSION = "observation-causality-v2"
SECTIONS = ("summary", "comparison", "hypothesis", "portfolioImpact", "counterEvidence", "notificationReason")
METRICS = {
    "currentPrice": ("현재가", "money"), "averagePrice": ("평균 매입가", "money"),
    "changeRate": ("전일 대비", "%"), "profitLossRate": ("평가 손익률", "%"),
    "positionWeight": ("계정 내 비중", "%"), "ma5": ("5일 평균 가격", "money"),
    "policyLimitRatio": ("관리 비중 기준", "%"), "strategyMaxPositionWeightPct": ("전략 최대 비중", "%"),
    "ma20": ("20일 평균 가격", "money"), "ma60": ("60일 평균 가격", "money"),
    "ma5Slope": ("5일 평균 가격 기울기", "%"), "ma20Slope": ("20일 평균 가격 기울기", "%"),
    "ma60Slope": ("60일 평균 가격 기울기", "%"), "ma5Distance": ("5일 평균 가격 이격", "%"),
    "ma20Distance": ("20일 평균 가격 이격", "%"), "ma60Distance": ("60일 평균 가격 이격", "%"),
    "volume": ("거래량", "주"), "volumeRatio": ("하루 평균 거래량 대비", "배"),
    "tradeStrength": ("체결 강도", "%"), "bidAskImbalance": ("호가 잔량 불균형", "%"),
    "foreignNetVolume": ("외국인 순매수", "주"), "institutionNetVolume": ("기관 순매수", "주"),
}
CERTAINTY = re.compile(r"확정[됐되적]|반드시|무조건|보장|틀림없|확실[히한].*(?:상승|하락|반등)")
NEGATED_CERTAINTY = re.compile(r"(?:확정|보장|단정)(?:(?:되|하)?지(?:는)?\s*않|적이지\s*않|(?:할|될)\s*수(?:는)?\s*없|(?:된|적이라는)\s*(?:것이\s*)?아니|(?:은|이)\s*없)")
NEGATED_INTERPRETATION = re.compile(r"확정적(?:인)?\s*(?:전환|추세|변화|신호|상승|하락)?(?:으로|이라고|이라고는)\s*(?:보|판단하|해석하)지(?:는)?\s*않(?:습니다|는다|는다거나|아요)?")
PERIOD = re.compile(r"(?<!\d)(5|20|60)일(?:선|\s*(?:이동)?평균)")
_PERIOD_NAME = r"(?:5|20|60)일(?:선|\s*(?:이동)?평균(?:\s*가격)?)"
_PERIOD_SERIES = _PERIOD_NAME + r"(?:\s*(?:과|와|및|,|·)\s*" + _PERIOD_NAME + r")*"
SLOPE_CLAIM = re.compile(_PERIOD_SERIES + r"\s*(?:의\s*)?기울기|기울기(?:의|가\s*(?:양수|음수|상승|하락)인)\s*" + _PERIOD_SERIES)


def instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def finite(value):
    try:
        return float(value) if not isinstance(value, bool) and math.isfinite(float(value)) else None
    except (TypeError, ValueError):
        return None


def field_value(fact, path):
    value = fact
    if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,180}", path):
        raise ValueError("invalid evidence field")
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError("evidence field missing")
        value = value[part]
    if value is None or value == "" or isinstance(value, (dict, list)):
        raise ValueError("evidence field must be a recorded scalar")
    return value


def resolve_ref(packet, ref):
    if not isinstance(ref, dict) or set(ref) != {"factId", "field", "period"} or ref["period"] not in {"current", "baseline", "assessment"}:
        raise ValueError("invalid evidence reference")
    facts = packet.get("facts", []) if ref["period"] != "baseline" else (packet.get("lastDeliveredNotification") or {}).get("facts", [])
    fact = next((row for row in facts if row.get("id") == ref["factId"]), None)
    if not fact:
        raise ValueError("evidence reference not captured")
    if ref["period"] == "assessment":
        from digital_twin.modules.ai_orchestration.domain.observation_clock import verified_quote_assessments
        return fact, field_value(verified_quote_assessments(packet).get(ref["factId"], {}), ref["field"])
    value = field_value(fact, ref["field"])
    clock = instant(fact.get("sourceAsOf") or fact.get("asOf") or fact.get("publishedAt"))
    cutoff = instant(packet.get("capturedAt"))
    if clock and cutoff and clock > cutoff:
        raise ValueError("future evidence reference")
    return fact, value


def compare_values(left, operator, right):
    if finite(left) is None or finite(right) is None or operator not in {"gt", "lt", "gte", "lte"}:
        raise ValueError("invalid observable comparison")
    return {"gt": float(left) > float(right), "lt": float(left) < float(right),
            "gte": float(left) >= float(right), "lte": float(left) <= float(right)}[operator]


def comparable_refs(packet, left, right):
    if any(ref.get("period") == "assessment" for ref in (left, right)):
        raise ValueError("quote assessment is not a market comparison")
    first, _ = resolve_ref(packet, left)
    second, _ = resolve_ref(packet, right)
    first_unit = METRICS.get(left["field"], ("", left["field"]))[1]
    second_unit = METRICS.get(right["field"], ("", right["field"]))[1]
    if first_unit != second_unit or first_unit == "money" and (not first.get("currency") or first.get("currency") != second.get("currency")):
        raise ValueError("comparison unit mismatch")
    for ref, fact in ((left, first), (right, second)):
        if ref["field"] in {"policyLimitRatio", "strategyMaxPositionWeightPct"} and (finite(field_value(fact, ref["field"])) or 0) <= 0:
            raise ValueError("comparison policy limit not configured")
    if left["period"] != right["period"]:
        current, baseline = (first, second) if left["period"] == "current" else (second, first)
        clocks = [instant(row.get("sourceAsOf") or row.get("asOf") or row.get("publishedAt")) for row in (current, baseline)]
        if not all(clocks) or clocks[0] < clocks[1]:
            raise ValueError("comparison source chronology mismatch")


def section_text(result, section):
    return result.get("notification", {}).get("reason", "") if section == "notificationReason" else result.get(section, "")


def asserts_certainty(text):
    return bool(CERTAINTY.search(NEGATED_CERTAINTY.sub("", NEGATED_INTERPRETATION.sub("", text))))


def expand_period_names(text):
    """Read '5·20·60일선' as period names, without accepting arbitrary quantities."""
    pattern = r"(?<!\d)((?:(?:5|20|60)\s*[,·]\s*)+)(5|20|60)(일(?:선|\s*(?:이동)?평균(?:\s*가격)?))"
    return re.sub(pattern, lambda m: "·".join(period + m[3] for period in re.findall(r"\d+", m[1]) + [m[2]]), text)


def unsupported_cause_v1(sentence, section):
    """Historical guard retained for explicit, read-only judgment replay."""
    if not re.search(r"때문|원인으로|원인입니다|원인은", sentence):
        return False
    if re.search(r"(?:확정|단정|확인|판단|알).{0,12}(?:없|못|않|어렵)|가능|일 수|될 수", sentence):
        return False
    limitation = (section == "counterEvidence"
        and re.search(r"(?:부재|추정치|참고값|부족|누락).{0,30}때문에.{0,30}(?:신뢰도|비교|해석|검증).{0,12}(?:제한|어렵|불가|한계)", sentence)
        and not re.search(r"(?:상승|하락|반등|급등|급락|회복|발생)(?:했|하였|했습|한 것|했다)|(?:올랐|내렸|떨어졌)", sentence))
    return not limitation


def unsupported_cause(sentence, section):
    if not re.search(r"때문|원인으로|원인입니다|원인은", sentence):
        return False
    # A denial of causality or a reason for retaining an analysis is not an
    # asserted cause of a market move. Do not let a later caveat excuse an
    # actual price-movement assertion in the same sentence.
    asserted_move = re.search(
        r"(?:상승|하락|반등|급등|급락|회복|발생)(?:했|하였|했습|한 것|했다)|(?:올랐|내렸|떨어졌)", sentence)
    denied_cause = re.search(
        r"원인(?:으로|은).{0,35}(?:연결|확장|특정|설명|묶|좁히|보)(?:.{0,15})(?:없|않|어렵|어려|못)", sentence)
    analysis_reason = re.search(
        r"때문에.{0,45}(?:판단|해석|설명|관찰|신뢰도|비교|검증).{0,25}(?:유지|보류|유보|제한|어렵|약|넓히지|확장하지)", sentence)
    unresolved_cause = re.search(r"미해결 원인은.{0,35}(?:조사|확인|검토)", sentence)
    retained_analysis = re.search(r"(?:판단|해석|설명).{0,45}때문에.{0,12}(?:유지|보류|유보|제한)", sentence)
    observation_reason = re.search(r"때문에.{0,40}관찰할 이유(?:는|가).{0,12}(?:남아|있|없)", sentence)
    if not asserted_move and (denied_cause or analysis_reason or unresolved_cause or retained_analysis or observation_reason):
        return False
    if re.search(r"(?:확정|단정|확인|판단|알).{0,12}(?:없|못|않|어렵)|가능|일 수|될 수", sentence):
        return False
    # A stated data limitation is not a claim about what caused a price move.
    limitation = (section == "counterEvidence"
        and re.search(r"(?:부재|추정치|참고값|부족|누락).{0,30}때문에.{0,30}(?:신뢰도|비교|해석|검증).{0,12}(?:제한|어렵|불가|한계)", sentence)
        and not asserted_move)
    return not limitation


def insight_errors(result, packet, *, causal_guard_version=CAUSAL_GUARD_VERSION):
    if causal_guard_version not in {CAUSAL_GUARD_VERSION, "observation-causality-v1"}:
        raise ValueError("unknown observation causality guard")
    causal_guard = unsupported_cause if causal_guard_version == CAUSAL_GUARD_VERSION else unsupported_cause_v1
    errors = []
    if result.get("wordingVersion") not in {None, OBSERVATION_WORDING_VERSION}:
        errors.append("지원하지 않는 관찰 문장 계약입니다.")
    if result.get("insightVersion") != INSIGHT_VERSION:
        return ["새 설명 계약과 문장별 근거 연결이 없습니다."]
    citations = result.get("claimEvidence", {})
    if not isinstance(citations, dict) or set(citations) != set(SECTIONS):
        return ["문장별 근거 연결이 완전하지 않습니다."]
    for section in SECTIONS:
        text = section_text(result, section)
        refs = citations[section]
        if not isinstance(text, str) or not 8 <= len(text.strip()) <= 400 or not isinstance(refs, list) or not 1 <= len(refs) <= 8:
            errors.append(section + ": 설명 또는 근거가 부족합니다.")
            continue
        text = expand_period_names(text)
        # Quantities belong to the deterministic fact panel, never free prose.
        if re.search(r"\d", PERIOD.sub("평균 가격", text)):
            errors.append(section + ": 수치는 직접 쓰지 않고 근거 표시에 맡겨야 합니다.")
        if asserts_certainty(text) or narrative_presentation_errors("NO_ACTION", [text]):
            errors.append(section + ": 확정적 전망 또는 행동 지시가 포함됐습니다.")
        for sentence in re.split(r"[.!?。\n]", text):
            if causal_guard(sentence, section):
                errors.append(section + ": 관측 사실을 확인된 원인으로 단정할 수 없습니다.")
        resolved = []
        for ref in refs:
            try:
                fact, value = resolve_ref(packet, ref)
                resolved.append((ref, fact, value))
                if ref["period"] != "baseline" and fact["id"] not in result.get("evidenceIds", []):
                    raise ValueError("uncited field")
                if ref["period"] == "assessment" and section != "counterEvidence":
                    raise ValueError("quote assessment is limitation metadata only")
                if section != "counterEvidence" and (fact.get("judgementEvidenceUsable") is False or fact.get("valuationDecisionEligible") is False):
                    raise ValueError("reference-only evidence")
                if ref["field"] in {"foreignNetVolume", "institutionNetVolume"}:
                    participant = "foreign" if ref["field"].startswith("foreign") else "institution"
                    if (fact.get("investorFlowParticipantStatus") or {}).get(participant) in {"unsupported", "missing"}:
                        raise ValueError("unsupported flow")
            except (ValueError, KeyError, TypeError):
                errors.append(section + ": 해당 시점의 사용 가능한 근거 항목을 확인할 수 없습니다.")
        for claim in SLOPE_CLAIM.finditer(text):
            for period in PERIOD.findall(claim.group()):
                if not any(ref["field"] == "ma" + period + "Slope" for ref, _, _ in resolved):
                    errors.append(section + ": 평균 가격의 기울기 근거가 없습니다.")
    comparisons = result.get("observations", [])
    business = result.get("businessResearch") or {}
    minimum = 0 if business.get("theses") or business.get("reviews") else 1
    if not isinstance(comparisons, list) or not minimum <= len(comparisons) <= 6:
        errors.append("확인 가능한 관측 비교가 필요합니다.")
    else:
        for row in comparisons:
            try:
                if set(row) != {"left", "operator", "right"}:
                    raise ValueError("comparison shape")
                _, left = resolve_ref(packet, row["left"])
                _, right = resolve_ref(packet, row["right"])
                comparable_refs(packet, row["left"], row["right"])
                if not compare_values(left, row["operator"], right):
                    raise ValueError("comparison not true")
                for ref in (row["left"], row["right"]):
                    if ref["period"] == "current" and ref["factId"] not in result.get("evidenceIds", []):
                        raise ValueError("uncited comparison")
            except (ValueError, TypeError, KeyError):
                errors.append("관측 비교가 실제 항목·시점·수치와 일치하지 않습니다.")
    for row in result.get("followUpConditions", []):
        if re.search(r"\d", PERIOD.sub("평균 가격", expand_period_names(row.get("description", "")))) or asserts_certainty(row.get("description", "")):
            errors.append("확인 조건 설명에 직접 작성한 수치나 확정적 전망이 있습니다.")
        if result.get("wordingVersion") == OBSERVATION_WORDING_VERSION:
            text = row.get("description", "")
            if narrative_presentation_errors("NO_ACTION", [text]):
                errors.append("확인 조건의 의미에 행동 지시가 포함됐습니다.")
            if any(causal_guard(sentence, "hypothesis") for sentence in re.split(r"[.!?。\n]", text)):
                errors.append("확인 조건의 의미에서 관측 사실을 확인된 원인으로 단정할 수 없습니다.")
    return list(dict.fromkeys(errors))


def insight_fingerprint(result, packet):
    """Price jitter alone cannot turn the same relational explanation into news."""
    relations = []
    for row in result.get("observations", []):
        pair = []
        for key in ("left", "right"):
            ref = row[key]
            fact, _ = resolve_ref(packet, ref)
            pair.append((fact.get("kind"), fact.get("symbol"), ref["field"], ref["period"]))
        relations.append((pair, row["operator"]))
    documents = [material_fact(fact) for fact in packet.get("facts", [])
                 if fact.get("id") in result.get("evidenceIds", []) and fact.get("kind") != "stock"]
    transitions = [row["conditionId"] for row in packet.get("followUpEvaluations", []) if row.get("transitionVerified")]
    return content_hash({"version": INSIGHT_VERSION, "relations": sorted(relations, key=str),
                         "documents": sorted(documents, key=content_hash), "transitions": sorted(transitions)})


def narrative_digest(result):
    content = {key: result.get(key) for key in (
        "insightVersion", "summary", "comparison", "hypothesis", "portfolioImpact", "counterEvidence",
        "notification", "claimEvidence", "observations", "evidenceIds", "followUpConditions", "input")}
    # Legacy queued bodies keep their original review hash and rendering. New
    # presentation versions are part of the independently reviewed draft.
    if result.get("wordingVersion") is not None:
        content["wordingVersion"] = result["wordingVersion"]
    if "businessResearch" in result:
        content["businessResearch"] = result["businessResearch"]
    return content_hash(content)
