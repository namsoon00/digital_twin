"""Source-bound business theses. Observed metrics are not investment outcomes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import re

from digital_twin.modules.reasoning.contracts import content_hash

VERSION = "business-research-v1"
ACTIVE = ("tracking", "needs-review", "data-needed")
TEXT_FIELDS = ("question", "mechanism", "assumption", "alternative", "invalidation")


def clock(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)) else None
    except (TypeError, ValueError):
        return None


def day(value):
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def financial_metrics(packet):
    """Retain exact report basis/receipts; never infer units or disclosure time."""
    result = []
    cutoff = clock(packet.get("capturedAt"))
    if not cutoff:
        return result
    for fact in packet.get("facts", []):
        if fact.get("symbol") != packet.get("symbol") or fact.get("historicalReport") is not True:
            continue
        frequency = fact.get("frequency")
        if frequency not in {"annual", "quarterly"} or not fact.get("reportObservationId"):
            continue
        observed, published = clock(fact.get("observedAt")), clock(fact.get("publishedAt"))
        period = day(fact.get("periodEnd"))
        if not observed or observed > cutoff or not period or period > cutoff.date() or (published and published > cutoff):
            continue
        for metric, value in (fact.get("reportedValues") or {}).items():
            basis = (fact.get("metricProvenance") or {}).get(metric) or {}
            refs = basis.get("sourceReferences") or []
            if (not numeric(value) or not all(basis.get(key) for key in ("provider", "currency", "scope", "durationBasis"))
                    or not refs or any(not ref.get("datasetId") or not ref.get("revisionId")
                        or str(ref.get("subjectKey", "")).upper() != packet["symbol"].upper()
                        or not clock(ref.get("fetchedAt")) or clock(ref["fetchedAt"]) > cutoff for ref in refs)):
                continue
            result.append({"factId": fact["id"], "metric": metric, "value": value,
                "symbol": packet["symbol"], "frequency": frequency, "periodEnd": fact["periodEnd"],
                "periodStart": basis.get("periodStart", ""), "basis": {key: basis[key] for key in ("provider", "currency", "scope", "durationBasis")},
                "reportObservationId": fact["reportObservationId"], "publishedAt": fact.get("publishedAt", ""),
                "observedAt": fact["observedAt"], "sourceReferences": deepcopy(refs),
                "sourceSnapshotId": fact.get("sourceSnapshotId", "")})
    return sorted(result, key=lambda row: (row["periodEnd"], row["factId"], row["metric"]), reverse=True)


def business_schema(packet, research):
    from .research_request import continuous_planning_schema
    from .insight_schema import obj, choice, array
    schema = continuous_planning_schema(packet, research)
    string = {"type": "string", "minLength": 8, "maxLength": 500}
    ids = [row["id"] for row in packet.get("facts", [])] or [""]
    evidence = {**array(choice(ids)), "minItems": 1, "maxItems": 6}
    checkpoints = {**array(obj({"factId": choice(sorted({row["factId"] for row in financial_metrics(packet)}) or [""]),
        "metric": choice(sorted({row["metric"] for row in financial_metrics(packet)}) or [""]),
        "direction": choice(["increase", "decrease"]), "meaning": string})), "maxItems": 3}
    thesis = obj({**{key: deepcopy(string) for key in TEXT_FIELDS}, "evidenceIds": evidence,
        "missingEvidence": {**array(string), "maxItems": 3}, "horizonDays": {"type": "integer", "minimum": 90, "maximum": 730},
        "checkpoints": checkpoints})
    memories = [row for row in research if row.get("kind") == "business-thesis"]
    review = obj({"thesisId": choice([row["caseId"] for row in memories] or [""]),
        "disposition": choice(["retain", "revise", "retire"]), "reason": string,
        "evidenceIds": evidence})
    schema["properties"]["businessResearch"] = obj({"theses": {**array(thesis), "maxItems": 2},
        "reviews": {**array(review), "maxItems": 4}, "coverageNote": string})
    schema["required"].append("businessResearch")
    return schema


def validate_business(value, packet, research):
    if value is None:
        return {}  # Historical contracts remain replayable.
    if not isinstance(value, dict) or set(value) != {"theses", "reviews", "coverageNote"}:
        raise ValueError("invalid business research contract")
    facts = {row["id"]: row for row in packet.get("facts", [])}
    metrics = {(row["factId"], row["metric"]): row for row in financial_metrics(packet)}
    memories = {row["caseId"]: row for row in research if row.get("kind") == "business-thesis"}

    def text(raw):
        from .insight_contract import asserts_certainty
        from digital_twin.modules.decisions.contracts import narrative_presentation_errors
        if (not isinstance(raw, str) or not 8 <= len(raw.strip()) <= 500
                or re.search(r"\d", raw) or asserts_certainty(raw) or narrative_presentation_errors("NO_ACTION", [raw])):
            raise ValueError("business research requires bounded actionless explanation")
        return raw.strip()

    def evidence(ids):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 6 or any(not isinstance(key, str) or key not in facts for key in ids):
            raise ValueError("business research requires captured evidence")
        return list(dict.fromkeys(ids))

    theses, reviews = value["theses"], value["reviews"]
    if not isinstance(theses, list) or len(theses) > 2 or not isinstance(reviews, list) or len(reviews) > 4:
        raise ValueError("business research exceeds work budget")
    result = {"coverageNote": text(value["coverageNote"]), "theses": [], "reviews": []}
    for item in theses:
        if not isinstance(item, dict) or set(item) != set(TEXT_FIELDS) | {"evidenceIds", "missingEvidence", "horizonDays", "checkpoints"}:
            raise ValueError("incomplete business thesis")
        ids = evidence(item["evidenceIds"])
        usable = [facts[key] for key in ids if facts[key].get("evidenceCategory") in {"company", "research", "valuation"}
                  and facts[key].get("judgementEvidenceUsable") is not False and facts[key].get("valuationDecisionEligible") is not False]
        if not usable:
            raise ValueError("business thesis cannot be based on prices alone")
        horizon = item["horizonDays"]
        if type(horizon) is not int or not 90 <= horizon <= 730:
            raise ValueError("invalid business horizon")
        missing = item["missingEvidence"]
        checks = item["checkpoints"]
        if not isinstance(missing, list) or len(missing) > 3 or not isinstance(checks, list) or len(checks) > 3:
            raise ValueError("invalid business checkpoints")
        row = {**{key: text(item[key]) for key in TEXT_FIELDS}, "evidenceIds": ids,
            "missingEvidence": [text(entry) for entry in missing], "horizonDays": horizon, "checkpoints": []}
        seen = set()
        for check in checks:
            if not isinstance(check, dict) or set(check) != {"factId", "metric", "direction", "meaning"}:
                raise ValueError("invalid report checkpoint")
            key = (check["factId"], check["metric"])
            if key not in metrics or key in seen or check["factId"] not in ids or check["direction"] not in {"increase", "decrease"}:
                raise ValueError("checkpoint must bind an exact reported metric")
            baseline = metrics[key]
            if horizon < 365 or not day(baseline["periodStart"]):
                raise ValueError("same-season checkpoint requires report duration and annual horizon")
            # A newly fetched older report cannot become a convenient weak baseline.
            latest = max(entry["periodEnd"] for entry in metrics.values()
                         if all(entry[field] == baseline[field] for field in ("metric", "frequency", "basis")))
            if baseline["periodEnd"] != latest:
                raise ValueError("checkpoint baseline must be latest comparable report")
            row["checkpoints"].append({**deepcopy(check), "meaning": text(check["meaning"]), "baseline": deepcopy(baseline),
                "comparison": "next-year-same-period", "qualification": "metric-observation-only"})
            seen.add(key)
        if not checks and not missing:
            raise ValueError("unmeasurable thesis must state missing evidence")
        result["theses"].append(row)
    reviewed = set()
    for item in reviews:
        if not isinstance(item, dict) or set(item) != {"thesisId", "disposition", "reason", "evidenceIds"}:
            raise ValueError("invalid business review")
        key = item["thesisId"]
        if key not in memories or key in reviewed or item["disposition"] not in {"retain", "revise", "retire"}:
            raise ValueError("business review requires captured thesis")
        if any(memories[key].get(field) != packet.get(field) for field in ("accountId", "symbol", "worldId")):
            raise ValueError("business review scope mismatch")
        if item["disposition"] == "revise" and not result["theses"]:
            raise ValueError("revision requires a replacement thesis")
        result["reviews"].append({**deepcopy(item), "reason": text(item["reason"]),
            "evidenceIds": evidence(item["evidenceIds"]), "expectedRevision": memories[key]["revision"]})
        reviewed.add(key)
    released = sum(item["disposition"] in {"revise", "retire"} for item in result["reviews"])
    if len(memories) - released + len(result["theses"]) > 2:
        raise ValueError("at most two active business contracts; review existing contracts first")
    if {key for key, row in memories.items() if row.get("reviewDue")} - reviewed:
        raise ValueError("changed business thesis requires explicit review")
    return result


def raw_business(value):
    """Recheck the model-authored portion against the frozen input at commit."""
    result = deepcopy(value)
    for row in result.get("theses", []):
        row["checkpoints"] = [{key: check[key] for key in ("factId", "metric", "direction", "meaning")} for check in row["checkpoints"]]
    for row in result.get("reviews", []):
        row.pop("expectedRevision", None)
    return result


def checkpoint_observation(check, packet, registered_at, expires_at):
    baseline = check["baseline"]
    registered, captured, expires = clock(registered_at), clock(packet["capturedAt"]), clock(expires_at)
    result = {"metric": baseline["metric"], "baseline": deepcopy(baseline), "direction": check["direction"],
              "status": "awaiting-report", "qualification": "metric-observation-only"}
    if not all((registered, captured, expires)) or captured < registered:
        return {**result, "status": "invalid-clock"}
    if captured > expires:
        return {**result, "status": "expired-unobserved"}
    candidates = []
    for current in financial_metrics(packet):
        if any(current[field] != baseline[field] for field in ("symbol", "metric", "frequency", "basis")):
            continue
        period, before = day(current["periodEnd"]), day(baseline["periodEnd"])
        published, observed = clock(current["publishedAt"]), clock(current["observedAt"])
        if not published or not observed or min(published, observed) <= registered:
            continue
        if current["reportObservationId"] == baseline["reportObservationId"] or period <= before:
            continue
        if not 330 <= (period - before).days <= 400:
            continue
        starts = (day(current["periodStart"]), day(baseline["periodStart"]))
        if not all(starts) or abs((period - starts[0]).days - (before - starts[1]).days) > 8:
            continue
        # An old period newly backfilled after registration is not a forward test.
        if period <= registered.date():
            continue
        candidates.append(current)
    if not candidates:
        return result
    current = min(candidates, key=lambda row: (row["publishedAt"], row["periodEnd"], row["factId"]))
    delta = current["value"] - baseline["value"]
    reached = delta > 0 if check["direction"] == "increase" else delta < 0
    return {**result, "status": "direction-observed" if reached else "direction-not-observed",
            "current": deepcopy(current), "change": delta, "observedAt": packet["capturedAt"]}


def evaluate_thesis(case, packet):
    previous = case.get("observations", [])
    results = []
    for index, check in enumerate(case["contract"]["checkpoints"]):
        old = previous[index] if index < len(previous) else {}
        # Keep the first actual observation, including a miss, immutable. Later
        # report restatements are current facts for review, not a rewritten test.
        results.append(deepcopy(old) if old.get("status") in {"direction-observed", "direction-not-observed"}
                       else checkpoint_observation(check, packet, case["createdAt"], case["expiresAt"]))
    return results


def thesis_case(job, result, thesis, now):
    key = content_hash([VERSION, job["accountId"], job["symbol"], job["worldId"], thesis])
    evidence = [deepcopy(row) for row in result["input"]["facts"] if row["id"] in thesis["evidenceIds"]]
    return {"caseId": key, **{key: job[key] for key in ("accountId", "symbol", "worldId")},
        "kind": "business-thesis", "status": "tracking" if thesis["checkpoints"] else "data-needed", "revision": 0,
        "question": thesis["question"], "contract": deepcopy(thesis), "createdAt": now,
        "expiresAt": (clock(now) + timedelta(days=thesis["horizonDays"])).isoformat().replace("+00:00", "Z"),
        "nextCheckAt": (clock(now) + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
        "origin": {"taskId": job["taskId"], "executionInputId": result["executionInputId"],
            "capturedAt": result["input"]["capturedAt"], "sourceSnapshots": result["input"]["sourceSnapshots"],
            "evidence": evidence}, "observations": [], "reason": "사업 가설을 등록하고 다음 공시와 반증을 기다립니다.",
        "authority": "research-only", "qualification": "not-empirically-qualified"}


def business_prompt(packet, history, research):
    from .observation_wording import readable_planning_prompt
    return BUSINESS_INSTRUCTIONS + readable_planning_prompt(packet, history, research)


BUSINESS_INSTRUCTIONS = """장기 사업 연구 계약 (아래의 단기 가격 확인 규칙보다 우선):
사업의 변화가 매출·이익·현금으로 연결되는 과정을 분석하세요. 가격/평균선은 맥락이며 사업 가설의 검증값이 아닙니다.
current.businessEvidence에 기본으로 읽은 사업 자료와 누락을 표시합니다. 현재 facts의 보고 기간·원문·단위·정정 여부를 구별하세요.
businessResearch={theses,reviews,coverageNote}를 작성하세요. 자료 부족이면 없는 원인을 만들지 말고 누락과 구체적인 조사 질문을 남기세요.
businessResearch의 설명 문장에는 수치를 직접 쓰지 마세요. 수치·기간·단위는 구조화된 기준과 자료 패널에서 표시합니다.
theses는 최대 두 개의 연구 가설입니다. question(사업 질문), mechanism(연결 과정), assumption(필요 가정), alternative(경쟁 설명),
invalidation(철회 조건), evidenceIds(현재 사업 근거), missingEvidence(부족 자료), horizonDays(90~730), checkpoints를 담습니다.
checkpoint={factId,metric,direction:increase|decrease,meaning}은 historicalReport=true인 공식 reportedValues의 최신 보고 수치에만 연결하세요.
동일 보고 기준의 다음 해 같은 기간 수치를 비교하므로 horizonDays>=365입니다. 분기와 누적·연간 실적, 환율·범위가 다른 수치는 섞지 마세요.
측정할 사업 지표가 없으면 checkpoints=[]와 missingEvidence를 기록하고 questions로 원문을 조사하세요. 가상 기준값을 만들지 마세요.
kind=business-thesis 기억은 원래 계약과 당시 기준, 코드가 확인한 다음 실적, 이전 검토입니다. 과거 해석을 새 사실로 인용하지 마세요.
reviewDue=true인 가설은 reviews에서 모두 retain/revise/retire 중 하나와 현재 evidenceIds·reason을 남기세요.
revise는 원래 가설을 덮어쓰지 않고 새 theses로 교체합니다. 동일 가설은 reviews로 유지하고 새로 복제하지 마세요. 활성 가설은 합계 두 개까지이며 더 만들려면 기존 것을 종료하거나 교체하세요.
관측한 지표 방향은 연결 과정 중 한 지점입니다. 수익률 적중, 원인 입증, 가설 전체의 성공 확률로 바꾸지 마세요.
사업 theses 또는 기존 thesis reviews가 있으면 followUpConditions는 빈 배열이어도 됩니다. 가격 조건을 억지로 작성하지 마세요.
사업 근거가 있으면 observations 역시 빈 배열일 수 있습니다. comparison에는 사업 해석의 유지·변경과 아직 확인 못한 연결을 설명하세요.
기업 관계 자료가 없고 사업 연결을 설명하려면 questions에 구체적인 공급사·고객사·경쟁사의 공식 공시 조사 질문과 검색어를 작성하세요.
기업 관계 자료는 source-stated 관계이며 수혜 판정이 아닙니다. sourceName만 있는 상대방의 상장 코드나 모회사 관계를 추정하지 마세요.
관계의 보고 시점과 현재 지속 여부는 다릅니다. 연결된 기업은 추가 조사 후보이고 매수 추천이나 자동 관심종목 등록 대상이 아닙니다.
"""


def business_review_prompt(packet, draft):
    from .observation_wording import readable_review_prompt
    return BUSINESS_INSTRUCTIONS + "\n독립 검토에서 businessResearch의 연결 과정·가정·경쟁 설명·반증·인용을 hypothesis 항목으로 함께 검토하세요.\n" + readable_review_prompt(packet, draft)
