"""Operational research coverage; no company rating or investment authority."""
from copy import deepcopy
import hashlib
import json

VERSION = "company-preparation-v1"
LABELS = {"complete": "확인 완료", "partial": "부분 확보", "waiting": "대기", "failed": "재처리 대기", "blocked": "확인 필요"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def assess_preparation(subject, payload, relationships, ai, previous, now):
    ai = deepcopy(ai)
    first_analysis = deepcopy(previous.get("firstAnalysis") or {}) if previous.get("worldId") == subject.get("worldId") else {}
    if ai.get("status") == "complete" and ai.get("sourceSnapshotId"):
        first_analysis = first_analysis or deepcopy(ai)
    elif ai.get("status") == "complete":
        ai.update(status="waiting", detail="TypeDB 근거가 연결된 분석 결과 확인 대기")
    elif first_analysis and ai.get("status") == "waiting":
        ai["detail"] = "최초 분석 기록 유지 · 후속 관측 대기"
    evidence = payload.get("companyReportEvidence") or {}
    reports = list(evidence.get("annualFinancials") or []) + list(evidence.get("recentFinancials") or [])
    official = [metric for report in reports for metric in report.get("metrics", [])
                if metric.get("official") and metric.get("sourceReferences")]
    keys = {metric.get("key") for metric in official}
    checks = [
        ("business", "사업 설명", bool(evidence.get("businessDescription"))),
        ("recent", "최근 공식 재무", any(metric.get("official") and metric.get("sourceReferences")
             for report in evidence.get("recentFinancials", []) for metric in report.get("metrics", []))),
        ("comparison", "동일 기준 전년 비교", any(metric.get("comparison") for metric in official)),
        ("cash", "현금흐름", "operatingCashFlow" in keys),
        ("debt", "차입금", "totalDebt" in keys),
        ("documents", "공식 문서 본문", bool((evidence.get("coverage") or {}).get("verifiedDocuments"))),
    ]
    valuation = payload.get("valuation") or {}
    # Valuation remains a reference calculation owned by the valuation module.
    checks.append(("valuation", "가치평가 입력", (valuation.get("dataReadiness") or {}).get("status") == "ready"))
    gaps = [label + " 자료 확인 필요" for _, label, ready in checks if not ready]
    if relationships.get("unresolvedNames"):
        gaps.append("관계 기업 식별 필요: " + ", ".join(relationships["unresolvedNames"][:5]))
    if not relationships.get("documentCount"):
        gaps.append("공급사·고객사·경쟁사를 확인할 공식 문서 필요")
    periods = sorted({row.get("period", "") for row in reports if row.get("period")})
    stages = [
        {"key": "registered", "label": "관리 대상 등록", "status": "complete", "detail": " · ".join("보유" if role == "holding" else "관심" for role in subject["roles"])},
        {"key": "collection", "label": "자료 수집", "status": "complete" if subject.get("worldId") and reports else "partial" if subject.get("worldId") else "waiting", "detail": "모니터 반영 " + (subject.get("observedAt") or "대기")},
        {"key": "company", "label": "기업 조사", "status": "complete" if all(row[2] for row in checks) else "partial" if evidence else "waiting", "detail": " / ".join(gaps[:7]) or "필수 자료 항목 확보; 기업 가치 판단과 구분"},
        {"key": "relationships", "label": "관계 연결", "status": relationships.get("status", "waiting"), "detail": relationships.get("detail", "공식 문서 확인 대기")},
        {"key": "graph", "label": "최초 TypeDB 근거 읽기", "status": "complete" if first_analysis.get("sourceSnapshotId") else "waiting", "detail": "근거 읽기 확인 " + (first_analysis.get("observedAt") or "대기")},
        {"key": "analysis", "label": "중앙 AI 최초 분석", "status": "complete" if first_analysis else ai.get("status", "waiting"), "detail": ("최초 분석 확인 " + str(first_analysis.get("observedAt")) + " · " if first_analysis else "") + ai.get("detail", "최초 분석 대기")},
    ]
    all_ready = all(row["status"] == "complete" for row in stages)
    state = "complete" if all_ready else "partial" if reports else "waiting"
    basis = {"gaps": gaps, "periods": periods}
    record = {"version": VERSION, "accountId": subject["accountId"], "symbol": subject["symbol"],
              "name": subject.get("name") or subject["symbol"], "roles": list(subject["roles"]),
              "worldId": subject.get("worldId", ""), "status": state, "label": LABELS[state],
              "createdAt": previous.get("createdAt") or now, "checkedAt": now,
              "stages": stages, "requirements": [{"key": key, "label": label, "ready": ready} for key, label, ready in checks],
              "gaps": gaps, "periods": periods, "researchBasis": fingerprint(basis),
              "relationships": relationships, "ai": ai,
              "boundary": "자료 확보와 처리 상태입니다. 관계 조사 범위는 공식 문서의 직접 연결이며, 투자 적합성이나 전체 공급망 조사 완료를 뜻하지 않습니다."}
    record["firstAnalysis"] = first_analysis
    material = {key: record[key] for key in ("worldId", "status", "roles", "gaps", "periods")}
    material["stages"] = [(row["key"], row["status"]) for row in stages]
    material["relationshipCounts"] = [relationships.get(key, 0) for key in ("documentCount", "resolvedCount", "unresolvedCount")]
    record["fingerprint"] = fingerprint(material)
    history = list(previous.get("history") or [])
    if previous.get("fingerprint") != record["fingerprint"]:
        history.append({"at": now, "status": state, "stages": material["stages"], "gaps": gaps[:8]})
    record["history"] = history[-8:]
    return record


def relationship_events(mutation):
    """Commit evidence enrichment and its existing TypeDB handoff together."""
    from .integration_events import research_evidence_collected_event
    from digital_twin.modules.reasoning.contracts import ontology_reasoning_requested_event
    value = mutation.to_dict()
    if not int(value.get("writtenCount") or 0):
        return []
    symbols = list(value.get("changedSymbols") or [])
    revisions = dict(value.get("factRevisionsBySymbol") or {})
    source = research_evidence_collected_event({**value, "source": "company-relationship-preparation",
        "status": "enriched", "symbols": symbols, "savedCount": value["writtenCount"],
        "alertEligibleItems": [], "alertEligibleSymbols": [], "alertEligibleCount": 0})
    # Cached historical documents remain factual relationship context. They
    # gain no new publication clock or direct investment notification authority.
    request = ontology_reasoning_requested_event(source, "company-relationship-preparation", symbols=symbols,
        changed_count=len(symbols), observed_count=value["writtenCount"], fact_types=["ResearchEvidence", "Company", "ExtractedClaim"],
        fact_revisions_by_symbol=revisions, evidence_deltas=value.get("evidenceDeltas", []),
        changed_fields_by_symbol={symbol: ["external.researchEvidence"] for symbol in symbols},
        reason="보유·관심 기업의 기존 공식 문서에서 확인한 직접 관계와 출처를 반영합니다.")
    return [source, request] if symbols else [source]
