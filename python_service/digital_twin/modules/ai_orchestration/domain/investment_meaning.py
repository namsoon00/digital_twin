"""Source-bound economic interpretation, competing explanations and a real next task."""
from copy import deepcopy
import re

from .analyst_memory import substantive_ref, new_position_risk
from .insight_contract import resolve_ref, asserts_certainty, instant

VERSION = "investment-meaning-v1"
VALUE_ERROR = "새 사실의 경제적 의미·경쟁 설명·다음 판별 자료가 연결되지 않았습니다."
TEXT = {"type": "string", "minLength": 8, "maxLength": 240}


def enabled(packet):
    return packet.get("investmentMeaningPolicy") == VERSION


def schema():
    from .insight_schema import obj, choice, array
    refs = {**array({"$ref": "#/$defs/investmentMeaningEvidence"}), "maxItems": 4}
    return obj({"status": choice(["analyzed", "insufficient", "not-applicable"]),
        "question": TEXT, "mechanism": TEXT, "evidence": refs,
        "explanations": {**array(obj({"explanation": TEXT, "support": refs, "against": refs,
            "assessment": choice(["preferred", "possible", "weakened", "unresolved"]), "reason": TEXT})), "maxItems": 2},
        "expectation": obj({"status": choice(["compared", "unavailable", "not-required"]),
            "actual": refs, "expected": refs, "meaning": TEXT}),
        "nextCheck": obj({"question": TEXT, "source": TEXT, "supports": TEXT, "weakens": TEXT})})


def references(value):
    refs = list(value.get("evidence", []))
    for row in value.get("explanations", []):
        refs += row.get("support", []) + row.get("against", [])
    expectation = value.get("expectation", {})
    return refs + expectation.get("actual", []) + expectation.get("expected", [])


def text(value):
    from digital_twin.modules.decisions.contracts import narrative_presentation_errors
    if (not isinstance(value, str) or not 8 <= len(value.strip()) <= 240
            or re.search(r"\d", value) or asserts_certainty(value)
            or narrative_presentation_errors("NO_ACTION", [value])):
        raise ValueError("investment meaning requires bounded actionless prose without invented numbers")
    return value.strip()


def validate(value, packet):
    if not isinstance(value, dict) or set(value) != set(schema()["properties"]):
        raise ValueError("investment meaning requires the complete economic chain")
    if value["status"] not in {"analyzed", "insufficient", "not-applicable"}:
        raise ValueError("unknown investment meaning status")
    text(value["question"]); text(value["mechanism"])
    rows = value["explanations"]
    if not isinstance(rows, list) or len(rows) > 2:
        raise ValueError("investment meaning needs at most two competing explanations")
    groups = [value["evidence"]]
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"explanation", "support", "against", "assessment", "reason"}
                or row["assessment"] not in {"preferred", "possible", "weakened", "unresolved"}):
            raise ValueError("invalid competing explanation")
        text(row["explanation"]); text(row["reason"])
        groups += [row["support"], row["against"]]
        if row["assessment"] in {"preferred", "possible"} and not row["support"]:
            raise ValueError("a favored explanation requires source support")
        if row["assessment"] == "weakened" and not row["against"]:
            raise ValueError("a weakened explanation requires counter evidence")
    expectation = value["expectation"]
    if (not isinstance(expectation, dict) or set(expectation) != {"status", "actual", "expected", "meaning"}
            or expectation["status"] not in {"compared", "unavailable", "not-required"}):
        raise ValueError("expectation comparison requires explicit source availability")
    text(expectation["meaning"])
    groups += [expectation["actual"], expectation["expected"]]
    for refs in groups:
        if not isinstance(refs, list) or len(refs) > 4:
            raise ValueError("investment meaning source references must be bounded")
        for ref in refs:
            if not substantive_ref(packet, ref):
                raise ValueError("investment meaning requires usable source content, not prices or metadata")
    if expectation["status"] == "compared":
        if (not expectation["actual"] or not expectation["expected"]
                or any(ref in expectation["actual"] for ref in expectation["expected"])):
            raise ValueError("actual and expected need distinct captured source fields")
        for ref in expectation["expected"]:
            fact, _ = resolve_ref(packet, ref)
            from .research_evidence_use import historical_reference
            if not (fact.get("documentVerified") or historical_reference(fact) or fact.get("kind") == "analyst-revision"):
                raise ValueError("company guidance or market expectations need an original source, not an AI assumption")
    elif expectation["expected"]:
        raise ValueError("unavailable expectations cannot carry an asserted forecast")
    check = value["nextCheck"]
    if not isinstance(check, dict) or set(check) != {"question", "source", "supports", "weakens"}:
        raise ValueError("next evidence needs a source and discriminating outcomes")
    for field in check:
        text(check[field])
    if check["supports"] == check["weakens"]:
        raise ValueError("support and contradiction must be distinguishable")
    if value["status"] == "analyzed":
        if (not value["evidence"] or len(rows) != 2 or rows[0]["explanation"] == rows[1]["explanation"]
                or all(row["assessment"] == "unresolved" for row in rows)):
            raise ValueError("analyzed meaning needs source-backed competing explanations")
    return deepcopy(value)


def quality_errors(result):
    packet = result["input"]
    if not enabled(packet):
        return []
    try:
        value = validate(result.get("investmentMeaning"), packet)
        cited = result.get("claimEvidence", {})
        core = [ref for key in ("summary", "hypothesis", "portfolioImpact") for ref in cited.get(key, [])]
        if any(ref["factId"] not in result.get("evidenceIds", []) for ref in references(value)):
            raise ValueError("economic interpretation has unselected source evidence")
        if value["status"] == "analyzed":
            if any(ref not in core for ref in value["evidence"]):
                raise ValueError("economic mechanism must reach the customer explanation")
            if not any(row.get("question") == value["nextCheck"]["question"] and row.get("capability") == "observe"
                       for row in result.get("workQuestions", [])):
                raise ValueError("next evidence must be connected to an actual observation task")
        if (result.get("notification", {}).get("send") and value["status"] != "analyzed"
                and not new_position_risk(packet, core)):
            return [VALUE_ERROR]
    except (ValueError, KeyError, TypeError):
        return [VALUE_ERROR]
    return []


def review_errors(result, review):
    if not enabled(result["input"]):
        return []
    value = review.get("investmentMeaning", {}) if isinstance(review, dict) else {}
    if (not isinstance(value, dict) or set(value) != {"supported", "discriminating", "expectationsSupported", "followUpLinked", "reason"}
            or any(value.get(k) is not True for k in ("supported", "discriminating", "expectationsSupported", "followUpLinked"))
            or not isinstance(value.get("reason"), str) or not 8 <= len(value["reason"].strip()) <= 500):
        return ["경제적 연결·경쟁 설명·기대 비교·후속 검증을 독립 검토에서 확인하지 못했습니다."]
    return []


def presentation(result):
    if not enabled(result.get("input", {})):
        return {}
    value = validate(result.get("investmentMeaning"), result["input"])
    return value if value["status"] == "analyzed" else {}


def document_sources(packet, research):
    """Reviewed partial answers grant source reads, never question completion."""
    from .opinion_memory import current_question
    from .question_source_handoff import source_references
    result = {}
    for row in research:
        case = row
        if row.get("kind") == "research-context" and enabled(packet):
            case = row.get("body", {})
            reviewed = current_question(case)
            if reviewed.get("status") != "reviewed":
                continue
            if any(case.get(k) != packet.get(k) for k in ("accountId", "symbol", "worldId")):
                raise ValueError("reviewed source memory crossed subject scope")
            known_at, cutoff = instant(reviewed.get("at")), instant(packet.get("capturedAt"))
            if not known_at or not cutoff or known_at > cutoff:
                continue
        elif row.get("kind") != "brain-case":
            continue
        for source in source_references(case):
            if source.get("evidenceId"):
                result.setdefault(source["evidenceId"], []).append(case["caseId"])
    return result
