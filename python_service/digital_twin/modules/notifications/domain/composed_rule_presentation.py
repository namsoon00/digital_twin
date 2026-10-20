"""Factual, bounded rendering of the saved native composition read model."""

from digital_twin.modules.notifications.domain.observation_market_snapshot import decimal, numeric
from digital_twin.modules.notifications.domain.relation_change_presentation import FIELDS, OPERATORS, measurement
from digital_twin.modules.model_registry.contracts import verified_measurement_packet


MEASURES = {
    "relativeReturnPct": ("비교 자산 대비 등락률 차이", "%p"),
    "proxyChangeRate": ("비교 자산 등락", "%"),
    "stockChangeRate": ("종목 등락", "%"),
    "delta5dBp": ("최근 5개 관측 구간 금리 변화", "bp"),
    "delta1dBp": ("전일 금리 변화", "bp"),
    "priceChangePct": ("구간 가격 등락", "%"),
    "recentPriceChangePct": ("후반 구간 가격 등락", "%"),
    "priceVelocityChangePct": ("앞 구간 대비 후반 등락률 차이", "%p"),
    "drawdownFromPeakPct": ("구간 고점 대비", "%"),
    "reboundFromTroughPct": ("구간 저점 대비", "%"),
    **FIELDS,
}


def saved_composition(judgment):
    row = judgment.get("evidenceComposition") or {}
    return row if (row.get("version") == "native-evidence-composition-v1"
        and row.get("authority") == "typedb-materialized-synthesis"
        and all(row.get(k) == judgment.get(k) for k in
                ("accountId", "symbol", "worldId", "inferenceGenerationId", "sourceAboxSnapshotId"))) else {}


def _context(row):
    context = row.get("context") or {}
    window = context.get("windowKey")
    if context.get("comparisonWindow") == "session-change":
        return "전일 종가 대비 · " + str(context.get("proxySymbol") or "비교 자산")
    if context.get("rateSeriesId"):
        series = str(context["rateSeriesId"])
        return {"DGS10": "미국 10년 국채 금리", "DGS2": "미국 2년 국채 금리"}.get(series, series)
    if window:
        from digital_twin.modules.notifications.domain.relation_observation_proof import window_name
        return window_name(window)
    return ""


def _number(field, value, currency):
    if field in FIELDS:
        return measurement(field, value, currency)
    unit = MEASURES[field][1]
    if unit == "bp":
        return decimal(value / 100, signed=True) + "%p"
    return decimal(value, signed=unit in {"%", "%p", "bp"}) + unit


def _groups(composition):
    for group in composition.get("evidenceGroups") or []:
        packet = group.get("sourceMeasurements") or {}
        verified = verified_measurement_packet(packet, rule_id=group.get("ruleId"),
            symbol=composition.get("symbol"), release_id=packet.get("modelReleaseId"),
            feature_snapshot_id=packet.get("sourceFeatureSnapshotId"), cutoff=packet.get("knowledgeCutoffAt"))
        if verified:
            yield group, verified


def _display_field(row):
    field, context = row.get("field"), row.get("context") or {}
    if field == "value":
        if context.get("levelType") in {"ma5", "ma20", "ma60"}:
            return context["levelType"] + "Distance"
        return context.get("field") or field
    return field


def exact_measurements(current, previous, events, currency, limit=4):
    now, old = saved_composition(current), saved_composition(previous)
    causes = {rid for event in events for rid in event.get("sourceRuleIds") or []}
    before = {}
    for group, packet in _groups(old):
        for row in packet.get("measurements") or []:
            before[(group["ruleId"], packet["modelReleaseId"], row["conditionId"],
                    row["evidenceId"], row["field"], _context(row))] = row
    choices = []
    for group, packet in _groups(now):
        for row in packet.get("measurements") or []:
            field, value = _display_field(row), numeric(row.get("observedValue"))
            if field not in MEASURES or value is None:
                continue
            prior = before.get((group["ruleId"], packet["modelReleaseId"], row["conditionId"],
                                row["evidenceId"], row["field"], _context(row)))
            changed = prior is not None and numeric(prior.get("observedValue")) != value
            label = MEASURES[field][0]
            text = (_context(row) + " · " if _context(row) else "") + label + " "
            text += (_number(field, prior["observedValue"], currency) + " → ") if changed else ""
            text += _number(field, value, currency)
            expected = numeric(row.get("expectedValue"))
            if expected is not None:
                text += " / 기준 " + _number(field, expected, currency) + " " + OPERATORS.get(row.get("operator"), str(row.get("operator") or ""))
            if prior and not changed:
                text += " (유지)"
            elif not prior:
                text += " (이전 비교값 없음)"
            context, provenance = row.get("context") or {}, row.get("provenance") or {}
            if field == "relativeReturnPct":
                stock, proxy = numeric(context.get("stockChangeRate")), numeric(context.get("proxyChangeRate"))
                if stock is not None and proxy is not None:
                    text += " [종목 " + decimal(stock, signed=True) + "%, 비교 " + decimal(proxy, signed=True) + "%]"
            at = provenance.get("sourceAsOf") or provenance.get("observedAt")
            # Source timestamps belong to the detail receipt; macro day-only
            # observations need their date in the concise message as well.
            if isinstance(at, str) and len(at) == 10:
                text += " · " + at + " 자료"
            # Show one source value once. Each rule's distinct threshold remains
            # in its full receipt; the displayed threshold belongs to this row.
            semantic = (field, row.get("evidenceId"), _context(row), value)
            choices.append((group["ruleId"] not in causes, not changed, group["ruleId"],
                            list(MEASURES).index(field), semantic, text))
    # Give each rule one measurement before taking additional fields from it.
    # No interpretation is based on the number of clauses.
    ordered, seen, used = [], set(), set()
    choices.sort(key=lambda c: c[:4])
    for first_pass in (True, False):
        for cause, unchanged, rid, field, semantic, text in choices:
            if semantic in seen or (first_pass and rid in used):
                continue
            ordered.append(text)
            seen.add(semantic)
            used.add(rid)
            if len(ordered) == limit:
                return ordered
    return ordered


def composition_explanation(judgment):
    packet = saved_composition(judgment)
    rows, relationships = packet.get("compositions") or [], packet.get("relationships") or []
    text = []
    policy = [r for r in rows if r.get("policyRuleId")]
    market = [r for r in rows if not r.get("policyRuleId")]
    text.extend(r["label"] for r in policy)
    for row in market:
        text.append(row["label"])
    if any(r.get("differentHorizons") and r.get("opposingDirections") for r in relationships):
        text.append("서로 다른 기간의 조건입니다. 같은 기간의 상승·하락 판단으로 합치지 않습니다.")
    elif any(r.get("opposingDirections") for r in relationships):
        text.append("반대 방향의 조건이 함께 있어 한쪽 전망으로 확정하지 않습니다.")
    if any(r.get("sharedObservation") for r in relationships):
        text.append("일부 규칙은 같은 관측을 사용하므로 근거가 여러 개라는 이유로 판단을 강화하지 않습니다.")
    return " ".join(dict.fromkeys(text))


def composition_next(judgment, events, currency):
    packet = saved_composition(judgment)
    policies = [r for r in packet.get("compositions") or [] if r.get("policyRuleId")]
    if policies:
        return ""  # The policy renderer uses the saved numeric release targets.
    values = exact_measurements(judgment, {}, events, currency, limit=2)
    if values:
        # Check continuation of the authored predicates; this is not an
        # invented inverse rule or a promise of automated follow-up.
        clauses = [v.split(" (이전 비교값 없음)", 1)[0] for v in values]
        return "다음 자료에서 " + " · ".join(clauses) + " 조건의 유지 여부를 확인합니다."
    return ""
