"""Evidence-bound external transmission scenarios, never new market facts."""
from copy import deepcopy

from .insight_contract import resolve_ref
from .insight_schema import obj, array, choice, scalar_paths
from .research_evidence_use import usable_fact

VERSION = "external-company-influence-v1"
CATEGORIES = ("geopolitics", "interest-rates", "currency", "policy", "trade", "commodities",
              "supply-chain", "technology", "competition", "disaster", "other")
EVENT_KINDS = {"macro-print", "interest-rate", "yield-curve", "fx-rate", "benchmark-index",
               "market-proxy-observation", "research-evidence", "news-article", "evidence:news",
               "evidence:filing", "evidence:disclosure", "fundamental-event", "company-relationship"}
EXPOSURE_KINDS = {"company", "company-financial-state", "evidence:financial-fact", "evidence:filing",
                  "evidence:disclosure", "revenue-exposure", "customer-exposure", "supply-chain-exposure",
                  "company-relationship", "research-evidence", "news-article", "evidence:news"}
TEXT_FIELDS = ("transmission", "horizon", "assumptions", "counterEvidence")
METADATA = {"id", "kind", "symbol", "title", "label", "name", "source", "provider", "sourceUrl", "url",
            "sourceAsOf", "asOf", "observedAt", "publishedAt", "periodEnd", "sourceRevision",
            "sourceSnapshotId", "sourceWorldId", "sourceEntityId", "currency", "unit", "dataState",
            "freshnessStatus", "judgementEvidenceUsable", "documentVerified", "seriesId", "summary", "headline",
            "limitation", "evidenceCategory", "boundedContext", "ontologyBox", "tboxClass"}
AI_FIELDS = {"aiAnalysis", "aiSummary", "aiImpact", "analysis", "analysisResult"}
INSTRUCTIONS = """외부 사건의 기업 영향 검토:
현재 원문·거시 관측에서 중요한 사건이 있으면 externalInfluences에 최대 두 경로만 기록하세요. 전쟁·지정학, 금리, 환율, 정책·선거, 관세·무역, 원자재, 공급망, 기술, 경쟁, 재난 및 other를 다룰 수 있습니다. 모든 범주를 채우거나 현재 자료에 없는 사건을 상식·기억으로 만들지 마세요. 관련 사건이 없으면 빈 배열입니다.
각 경로는 category, eventEvidence, exposureEvidence, assessment, transmission, horizon, assumptions, counterEvidence, missingEvidence, questionIndex입니다. evidence는 현재 읽은 원천의 정확한 {period:current,factId,field}입니다. 뉴스 제목·AI 요약·주가 동행만으로 기업 영향을 확정하지 마세요. 사건 발생·시행 여부와 시나리오를 구분하고 출처 시점·대상 기간을 보존하세요.
transmission은 사건→전달 경로→해당 기업의 매출/원가/현금흐름/위험에 가능한 영향입니다. 금리는 부채 구조·차환·수요, 전쟁은 사업 지역·운송·에너지·제재 등 실제 기업 노출을 확인하세요. 업종 이름만으로 모든 기업에 같은 영향을 부여하지 마세요. horizon은 검토 기간, assumptions는 필요한 전제, counterEvidence는 반대 설명·상쇄 요인입니다. 정량 영향은 검증된 계산 근거가 있을 때만 인용하며 새 적정가·매매 행동을 만들지 마세요.
assessment=conditional은 기업 노출 원문까지 확인한 조건부 설명이며 인과 검증·적중 인증이 아닙니다. 사건과 기업 노출 근거를 summary/hypothesis에 연결하고 실제 설명에도 전제와 한계를 쓰세요. 노출이 미확인이면 assessment=unconfirmed, missingEvidence에 구체적인 부족분을 쓰고 사건 근거는 counterEvidence에 연결하세요. 미확인 경로를 발송의 핵심 근거로 승격하지 마세요. 다른 확인된 설명은 독립적으로 판단할 수 있습니다.
중요한 부족분은 기존 질문을 먼저 확인하고 필요할 때만 questions의 기존 두 개 한도 안에서 research를 제안합니다. questionIndex는 그 질문의 영부터 시작하는 인덱스이며, 기존 과제로 관리하거나 추가 조사가 불필요하면 -1입니다. questionPurposes에 사건의 근거와 답에 따라 달라질 기업 판단을 연결하세요. 새 범주·같은 사건의 문구 변경은 조사 횟수 초기화나 추가 호출의 근거가 아닙니다.
이전 externalInfluences는 당시 해석의 기억입니다. 현재 원문으로 다시 확인하고 유지·수정 이유를 comparison에 설명하세요. 알림은 기존 전달보다 새로운 의미가 있을 때만 제안합니다.
"""
READ_INSTRUCTIONS = """외부 영향 조회: macro/research/company의 발급된 자료 목록에서 현재 사건과 해당 기업 노출을 함께 확인하세요. 모든 범주를 순회하지 말고 중요한 최대 두 경로에 필요한 원문만 읽습니다. 노출 근거를 못 찾으면 미확인으로 남겨 다음 작성 단계가 조건부 설명과 조사 필요를 구분하게 하세요. 다른 사용 가능한 판단 전체를 불필요하게 defer하지 마세요. 이미 읽은 같은 원문을 반복 조회하지 않으며 호출·입력 한도는 그대로입니다.
"""


def enabled(packet):
    from .execution_lanes import market
    return packet.get("externalInfluencePolicy") == VERSION and market(packet)


def source_reference(packet, ref, kinds):
    fact, value = resolve_ref(packet, ref)
    if (ref["period"] != "current" or fact.get("kind") not in kinds
            or not usable_fact(fact, ref["field"])
            or set(ref["field"].split(".")).intersection(AI_FIELDS)
            or ref["field"].split(".")[-1] in METADATA or isinstance(value, bool)):
        raise ValueError("external influence requires usable current source fields")
    return fact


def reference_schema(packet, kinds, company=False):
    groups = {}
    for fact in packet.get("facts", []):
        if company and fact.get("symbol") not in {None, "", packet["symbol"]}:
            continue
        fields = []
        for field in scalar_paths(fact):
            try:
                source_reference(packet, {"period": "current", "factId": fact["id"], "field": field}, kinds)
            except ValueError:
                continue
            fields.append(field)
        if fields:
            groups.setdefault(tuple(sorted(fields)), []).append(fact["id"])
    variants = [obj({"period": choice(["current"]), "factId": choice(sorted(set(ids))), "field": choice(fields)})
                for fields, ids in sorted(groups.items())]
    # Keep a valid structured-output schema even when only the empty paths array is usable.
    if not variants:
        return obj({"period": choice(["current"]), "factId": choice(["unavailable-evidence"]), "field": choice(["unavailable"])})
    return variants[0] if len(variants) == 1 else {"anyOf": variants}


def schema(packet):
    text = {"type": "string", "minLength": 8, "maxLength": 400}
    return {**array(obj({"category": choice(CATEGORIES), "assessment": choice(["conditional", "unconfirmed"]),
        "eventEvidence": {**array(reference_schema(packet, EVENT_KINDS)), "minItems": 1, "maxItems": 3},
        "exposureEvidence": {**array(reference_schema(packet, EXPOSURE_KINDS, company=True)), "maxItems": 3},
        **{key: deepcopy(text) for key in TEXT_FIELDS},
        "missingEvidence": {**array(deepcopy(text)), "maxItems": 3},
        "questionIndex": {"type": "integer", "minimum": -1, "maximum": 1}})), "maxItems": 2}


def validate(rows, packet, result):
    if not isinstance(rows, list) or len(rows) > 2:
        raise ValueError("external influences require at most two source-backed paths")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != set(schema(packet)["items"]["properties"])
                or row["category"] not in CATEGORIES or row["assessment"] not in {"conditional", "unconfirmed"}):
            raise ValueError("invalid external influence contract")
        if any(not isinstance(row[k], str) or not 8 <= len(row[k].strip()) <= 400 for k in TEXT_FIELDS):
            raise ValueError("external influence requires mechanism, horizon, assumptions and alternatives")
        gaps = row["missingEvidence"]
        if not isinstance(gaps, list) or len(gaps) > 3 or any(not isinstance(v, str) or not 8 <= len(v.strip()) <= 400 for v in gaps):
            raise ValueError("external influence requires bounded missing evidence")
        for field, kinds in (("eventEvidence", EVENT_KINDS), ("exposureEvidence", EXPOSURE_KINDS)):
            refs = row[field]
            if not isinstance(refs, list) or len(refs) > 3 or (field == "eventEvidence" and not refs):
                raise ValueError("external influence source references missing")
            for ref in refs:
                fact = source_reference(packet, ref, kinds)
                if ref["factId"] not in result["evidenceIds"]:
                    raise ValueError("external influence source must be cited")
                if field == "exposureEvidence" and fact.get("symbol") not in {None, "", packet["symbol"]}:
                    raise ValueError("external exposure belongs to a different company")
        key = (row["category"], tuple(sorted((r["factId"], r["field"]) for r in row["eventEvidence"])))
        if key in seen:
            raise ValueError("duplicate external influence path")
        seen.add(key)
        citations = result.get("claimEvidence", {})
        if row["assessment"] == "conditional":
            refs = row["eventEvidence"] + row["exposureEvidence"]
            if not row["exposureEvidence"] or any(r not in citations.get("summary", []) + citations.get("hypothesis", []) for r in refs):
                raise ValueError("conditional company impact requires cited company exposure")
        else:
            if not gaps or any(r not in citations.get("counterEvidence", []) for r in row["eventEvidence"]):
                raise ValueError("unconfirmed company impact requires explicit source limits")
            if result.get("notification", {}).get("send") and any(r in (result.get("judgmentChange") or {}).get("evidence", []) for r in row["eventEvidence"]):
                raise ValueError("unconfirmed company impact cannot authorize a notification")
        index = row["questionIndex"]
        if type(index) is not int or not -1 <= index < len(result.get("workQuestions", [])):
            raise ValueError("external influence question index is invalid")
        if index >= 0:
            purpose = next((p for p in result.get("questionPurposes", []) if p["questionIndex"] == index), {})
            if (not gaps or result["workQuestions"][index]["capability"] != "research"
                    or not set(purpose.get("evidenceIds", [])).intersection(r["factId"] for r in row["eventEvidence"])):
                raise ValueError("external influence research must preserve event evidence and decision purpose")
    return deepcopy(rows)


def quality_errors(result):
    if not enabled(result["input"]):
        return []
    try:
        validate(result.get("externalInfluences"), result["input"], result)
    except (ValueError, TypeError, KeyError):
        return ["외부 사건과 기업 노출의 근거·가정·미확인 조건을 다시 확인해야 합니다."]
    return []


def review_errors(result, review):
    if not enabled(result["input"]):
        return []
    item = review.get("externalInfluences", {}) if isinstance(review, dict) else {}
    if (not isinstance(item, dict) or item.get("supported") is not True or not isinstance(item.get("reason"), str)
            or not 8 <= len(item["reason"].strip()) <= 600):
        return ["외부 사건의 기업 영향과 한계를 독립 검토에서 확인하지 못했습니다."]
    return []
