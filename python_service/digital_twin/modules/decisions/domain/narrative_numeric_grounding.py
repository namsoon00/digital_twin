"""Validate displayed quantities, not digits embedded in provenance IDs."""

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Mapping


NUMBER = re.compile(r"(?<![0-9A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?")
MONEY = re.compile(
    r"(?<![0-9A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?\s*(?:조|억|만)"
    r"(?:\s*\d[\d,]*(?:\.\d+)?\s*(?:억|만))*"
    r"(?:\s*\d[\d,]*(?:\.\d+)?)?\s*(?=원|달러|USD\b|KRW\b)"
)
MONEY_PART = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(조|억|만)?")
MONEY_UNITS = {"조": Decimal("1e12"), "억": Decimal("1e8"), "만": Decimal("1e4"), "": Decimal(1)}
PERIOD = re.compile(r"^\s*(개월|시간|분기|일|년|분)(?![A-Za-z])")
ANNUAL_COUNT = re.compile(r"^\s*개\s*(?:회계)?연도")
NEGATIVE = re.compile(r"낮|밑[돌도]|하락|감소|하회|순매도|손실|줄[어었]|내[려렸리린릴림]|떨어|약세")
POSITIVE = re.compile(r"높|웃[돌도]|상승|증가|상회|순매수|늘[어었]|올[라랐]|오[르른를름]|강세")
SKIP_FIELDS = {
    "id", "evidenceId", "source", "sourceAsOf", "asOf", "generatedAt",
    "observedAt", "updatedAt", "createdAt", "referenceDate", "url",
    "relatedEvidenceIds", "ruleId", "hypothesisId", "hypothesisContractId",
    "releaseId", "inferenceGenerationId", "sourceAboxSnapshotId",
}


def _decimal(value):
    try:
        number = Decimal(str(value).replace(",", ""))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def _periods_from_field(field):
    # These are named observation windows, never quantities from an opaque ID.
    ma = re.fullmatch(r"(?:fact:)?ma(\d+)(?:Distance|Slope)?", field)
    if ma:
        return {(Decimal(ma.group(1)), "일")}
    change = re.fullmatch(r"(?:fact:)?\w*Change(\d+)([hd])", field)
    if change:
        return {(Decimal(change.group(1)), "시간" if change.group(2) == "h" else "일")}
    return set()


def _display_quantities(text):
    """Normalize Korean money units, preserving the last displayed precision."""
    consumed = 0
    for match in NUMBER.finditer(text):
        if match.start() < consumed:
            continue
        money = MONEY.match(text, match.start())
        if money:
            total, quantum, previous_unit = Decimal(0), Decimal(1), Decimal("Infinity")
            for part in MONEY_PART.finditer(money.group()):
                raw, unit = part.group(1).replace(",", ""), MONEY_UNITS[part.group(2) or ""]
                if unit >= previous_unit:
                    consumed = money.end()
                    yield money, None, quantum
                    break
                total += Decimal(raw) * unit
                quantum = unit.scaleb(-len(raw.split(".", 1)[1]) if "." in raw else 0)
                previous_unit = unit
            else:
                consumed = money.end()
                yield money, -total if money.group().startswith("-") else total, quantum
                continue
            if consumed == money.end():
                continue
        raw = match.group().replace(",", "")
        places = len(raw.split(".", 1)[1]) if "." in raw else 0
        yield match, _decimal(raw), Decimal(1).scaleb(-places)


def _evidence_quantities(rows):
    numbers, periods, annual_ends = set(), set(), set()

    def collect(value, field=""):
        periods.update(_periods_from_field(field))
        signed_change = bool(re.search(r"distance|growth|change|delta|netvolume|profitloss", field, re.I))
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key not in SKIP_FIELDS and not str(key).endswith(("Id", "Ids", "Hash", "Fingerprint")):
                    collect(child, str(key))
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect(child, field)
        elif isinstance(value, (int, float, Decimal)):
            number = _decimal(value)
            if number is not None:
                numbers.add((number, signed_change))
        elif isinstance(value, str):
            for match in NUMBER.finditer(value):
                number = _decimal(match.group())
                period = PERIOD.match(value[match.end():])
                if number is not None:
                    if period:
                        periods.add((number, period.group(1)))
                    else:
                        numbers.add((number, signed_change))

    for row in rows:
        if row.get("kind") == "financial-report" and isinstance(row.get("value"), Mapping):
            try:
                period_end = date.fromisoformat(str(row["value"].get("periodEnd") or ""))
            except ValueError:
                pass
            else:
                annual_ends.add(period_end)
                periods.add((Decimal(period_end.year), "년"))
        periods.update(_periods_from_field(str(row.get("evidenceId") or "")))
        field = str(row.get("field") or row.get("evidenceId") or "")
        collect(row.get("value"), field)
        collect(row.get("observedValue"), field)
        collect(row.get("label"))
    return numbers, periods, len(annual_ends)


def _displayed_signed_value(text, match, signed_change, value):
    raw = match.group().replace(",", "")
    if not signed_change:
        return value
    # A direction applies only to this quantity's immediate phrase. Do not
    # borrow a later clause's direction to excuse a wrong sign.
    tail = re.split(r"[,;.!?]|(?:이고|이며|지만|가운데)", text[match.end():], maxsplit=1)[0][:18]
    tail = re.split(r"\d", tail, maxsplit=1)[0]
    down, up = NEGATIVE.search(tail), POSITIVE.search(tail)
    direction = -1 if down and (not up or down.start() < up.start()) else 1 if up else 0
    if value is None:
        return None
    if direction and raw.startswith(("-", "+")):
        if value != 0 and (value < 0) != (direction < 0):
            return None
    elif direction and value >= 0:
        value *= direction
    return value


def ungrounded_narrative_numbers(text, evidence_rows):
    """Accept exact values or honest display rounding; never invent levels.

    Period labels are checked separately from quantities. Signed observations
    may be expressed as an unsigned magnitude plus a Korean direction, but
    opposite directions and invented precision still fail validation.
    """

    text = str(text or "")
    numbers, periods, annual_count = _evidence_quantities(evidence_rows)
    missing = []
    for match, number, quantum in _display_quantities(text):
        raw = match.group().replace(",", "")
        count_period = ANNUAL_COUNT.match(text[match.end():])
        if count_period:
            if not annual_count or number != annual_count:
                missing.append(raw + count_period.group().strip())
            continue
        period = PERIOD.match(text[match.end():])
        if period:
            if (number, period.group(1)) not in periods:
                missing.append(raw + period.group(1))
            continue
        matched = False
        for observed, signed_change in numbers:
            displayed = _displayed_signed_value(text, match, signed_change, number)
            if displayed is not None:
                try:
                    rounded = (observed / quantum).quantize(Decimal(1), rounding=ROUND_HALF_UP) * quantum
                except InvalidOperation:
                    continue
                if displayed == rounded:
                    # Rounding a tiny observation to zero is truthful. A
                    # rounded nonzero value must retain the observed sign.
                    matched = displayed == 0 or (displayed < 0) == (observed < 0)
                    if matched:
                        break
        if not matched:
            missing.append(raw)
    return missing
