from typing import Dict, List

from .market_data import number
from .ontology_decision_state import REVIEW_LEVEL_RANK


def _list(value) -> List[Dict[str, object]]:
    return [item for item in value or [] if isinstance(item, dict)]


def _dict(value) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _symbol(value: Dict[str, object]) -> str:
    return str((value or {}).get("symbol") or "").upper()


def _display_name(value: Dict[str, object]) -> str:
    return str((value or {}).get("name") or (value or {}).get("displayName") or _symbol(value) or "-")


def _is_cash(value: Dict[str, object]) -> bool:
    return _symbol(value) == "CASH" or str((value or {}).get("source") or "").lower() == "cash" or str((value or {}).get("sector") or "") == "현금"


def _tone_for_status(status: str) -> str:
    normalized = str(status or "").lower()
    if normalized in {"ready", "ok", "fresh", "actual", "live"}:
        return "watch"
    if normalized in {"blocked", "missing", "stale", "mock", "demo"}:
        return "caution"
    return "hold"


def _data_quality(item: Dict[str, object]) -> str:
    return str(item.get("dataQuality") or ("actual" if number(item.get("currentPrice")) else "missing"))


def _api_source(item: Dict[str, object]) -> str:
    return str(item.get("quoteSource") or item.get("sourceApi") or item.get("source") or "unknown")


def _decision_by_symbol(items: List[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    result: Dict[str, Dict[str, object]] = {}
    for item in items:
        symbol = _symbol(item)
        if symbol and symbol not in result:
            result[symbol] = item
    return result


def _graph_context(item: Dict[str, object]) -> Dict[str, object]:
    context = _dict(item.get("ontologyRelationContext"))
    decision = _dict(context.get("decision"))
    plan = _dict(context.get("executionPlan"))
    return {
        "graphStoreUsed": bool(context.get("graphStoreUsed")),
        "blocked": bool(context.get("blocked")) or str(decision.get("actionLevel") or "") == "blocked",
        "basis": str(decision.get("basis") or item.get("decisionBasis") or ""),
        "decisionStage": str(decision.get("decisionStage") or plan.get("decisionStage") or ""),
        "reviewLevel": str(decision.get("reviewLevel") or context.get("reviewLevel") or "observe"),
        "dataState": str(decision.get("dataState") or context.get("dataState") or "partial"),
        "validationState": str(decision.get("validationState") or context.get("validationState") or "conditional"),
        "reason": str(context.get("reason") or ""),
        "missingData": _list(context.get("missingData")),
        "nextChecks": list(plan.get("nextChecks") or [])[:5] if isinstance(plan.get("nextChecks"), list) else [],
        "blockedActions": list(plan.get("blockedActions") or [])[:5] if isinstance(plan.get("blockedActions"), list) else [],
    }


def _action_queue(decisions: List[Dict[str, object]], positions: List[Dict[str, object]], watchlist: List[Dict[str, object]]) -> List[Dict[str, object]]:
    source_by_symbol = {_symbol(item): item for item in positions + watchlist if _symbol(item)}
    rows = []
    for item in decisions:
        symbol = _symbol(item)
        source = source_by_symbol.get(symbol, {})
        graph = _graph_context(item)
        rows.append({
            "symbol": symbol,
            "name": _display_name(item) or _display_name(source),
            "source": str(item.get("source") or source.get("source") or ""),
            "market": str(item.get("market") or source.get("market") or ""),
            "sector": str(item.get("sector") or source.get("sector") or ""),
            "decision": str(item.get("decision") or "판단 대기"),
            "tone": str(item.get("tone") or ("caution" if graph["blocked"] else "hold")),
            "reviewLevel": str(item.get("reviewLevel") or graph.get("reviewLevel") or "observe"),
            "dataState": str(item.get("dataState") or graph.get("dataState") or "partial"),
            "validationState": str(item.get("validationState") or graph.get("validationState") or "conditional"),
            "profitLossRate": round(number(item.get("profitLossRate")) or 0, 2),
            "dataQuality": _data_quality(source),
            "apiSource": _api_source(source),
            "updatedAt": str(source.get("updatedAt") or ""),
            "graph": graph,
            "reasons": list(item.get("reasons") or [])[:4] if isinstance(item.get("reasons"), list) else [],
            "triggers": list(item.get("triggers") or [])[:5] if isinstance(item.get("triggers"), list) else [],
        })
    rows.sort(key=lambda row: (
        0 if row["graph"]["blocked"] else 1,
        -REVIEW_LEVEL_RANK.get(str(row.get("reviewLevel") or "observe"), 1),
        row["name"],
    ))
    return rows


def _data_sources(positions: List[Dict[str, object]], watchlist: List[Dict[str, object]], toss: Dict[str, object]) -> List[Dict[str, object]]:
    rows = []
    for item in positions + watchlist:
        symbol = _symbol(item)
        if not symbol:
            continue
        quality = _data_quality(item)
        rows.append({
            "symbol": symbol,
            "name": _display_name(item),
            "source": _api_source(item),
            "quality": quality,
            "tone": _tone_for_status(quality),
            "updatedAt": str(item.get("updatedAt") or ""),
            "status": str(item.get("quoteStatus") or item.get("quoteMessage") or ""),
            "isMock": quality.lower() in {"mock", "demo"} or str(toss.get("mode") or "").lower() in {"mock", "demo", "preview"},
        })
    return rows


def _money_flow(snapshot: Dict[str, object], positions: List[Dict[str, object]], watchlist: List[Dict[str, object]]) -> Dict[str, object]:
    portfolio = _dict(snapshot.get("portfolio"))
    markets = _list(portfolio.get("markets"))
    sectors = _list(portfolio.get("sectors"))
    external = _dict(_dict(snapshot.get("toss")).get("externalSignals"))
    buckets = []
    for item in markets[:8]:
        label = str(item.get("label") or item.get("key") or "시장")
        total = number(item.get("total"))
        buckets.append({
            "key": str(item.get("key") or label),
            "label": label,
            "value": round(total, 2),
            "caption": "투자 " + str(round(number(item.get("invested")))) + " / 현금 " + str(round(number(item.get("cash")))),
            "source": "portfolio",
            "tone": "watch" if total else "hold",
        })
    if external.get("crypto"):
        buckets.append({"key": "crypto", "label": "Crypto", "value": 0, "caption": "외부 신호 연결", "source": "externalSignals.crypto", "tone": "watch"})
    emerging = []
    if any(str(item.get("market") or "").upper() in {"KR", "KOSPI", "KOSDAQ"} for item in positions + watchlist):
        emerging.append({
            "title": "한국 시장 접근성 변화",
            "description": "국내 종목 노출이 있어 해외 투자자 접근성·환율·공시 신호를 함께 확인해야 합니다.",
            "source": "portfolio-market-exposure",
            "status": "watch",
        })
    if any(str(item.get("currency") or "").upper() == "USD" for item in positions + watchlist):
        emerging.append({
            "title": "달러 자산 흐름",
            "description": "미국 주식·달러 현금 노출이 있어 환율과 금리 신호를 같이 봅니다.",
            "source": "portfolio-currency-exposure",
            "status": "watch",
        })
    if sectors:
        top = sectors[0]
        emerging.append({
            "title": str(top.get("sector") or "섹터") + " 집중 흐름",
            "description": "포트폴리오 내 최대 섹터 비중 " + str(top.get("ratio") or 0) + "%입니다.",
            "source": "portfolio-sector-exposure",
            "status": "watch" if number(top.get("ratio")) < 35 else "caution",
        })
    return {"buckets": buckets, "emergingFlows": emerging[:6]}


def _graph_gate(snapshot: Dict[str, object], decisions: List[Dict[str, object]]) -> Dict[str, object]:
    decision = _dict(snapshot.get("tossDecision"))
    strategy = _dict(decision.get("ontologyStrategy"))
    graph_summary = _dict(_dict(decision.get("investmentAnalysis")).get("graphSummary"))
    blocked = [_graph_context(item) for item in decisions if _graph_context(item).get("blocked")]
    graph_store_used = any(_graph_context(item).get("graphStoreUsed") for item in decisions)
    relation_count = int(number(strategy.get("relationCount")) or number(graph_summary.get("relationCount")) or 0)
    entity_count = int(number(strategy.get("entityCount")) or number(graph_summary.get("entityCount")) or 0)
    status = "blocked" if blocked else ("ready" if graph_store_used else "unverified")
    reason = (
        blocked[0].get("reason") if blocked
        else "그래프 추론 기반 판단이 가능합니다." if graph_store_used
        else "TypeDB InferenceBox 사용 기록이 없어 그래프 기반 판단을 확정할 수 없습니다."
    )
    return {
        "status": status,
        "tone": "caution" if status != "ready" else "watch",
        "graphStoreUsed": graph_store_used,
        "blockedCount": len(blocked),
        "relationCount": relation_count,
        "entityCount": entity_count,
        "reason": reason,
        "requiredSource": "typedbInferenceBox",
        "nextChecks": (blocked[0].get("nextChecks") if blocked else []) or ["TypeDB native rule 실행 상태 확인", "InferenceBox 관계 확인", "데이터 신선도 확인"],
    }


def build_investment_analysis(snapshot: Dict[str, object]) -> Dict[str, object]:
    snapshot = _dict(snapshot)
    toss = _dict(snapshot.get("toss"))
    raw_positions = _list(toss.get("positions"))
    positions = [item for item in raw_positions if not _is_cash(item)]
    watchlist = _list(toss.get("watchlist") or toss.get("watchlistQuotes"))
    decisions = _list(_dict(snapshot.get("tossDecision")).get("items"))
    queue = _action_queue(decisions, positions, watchlist)
    data_sources = _data_sources(positions, watchlist, toss)
    graph_gate = _graph_gate(snapshot, decisions)
    checklist = _list(snapshot.get("checklist"))
    actual_count = len([item for item in data_sources if not item.get("isMock") and item.get("quality") != "missing"])
    mock_count = len([item for item in data_sources if item.get("isMock")])
    blocked_count = len([item for item in queue if item.get("graph", {}).get("blocked")])
    ready = str(toss.get("mode") or "").lower() == "live" and not blocked_count and graph_gate["status"] == "ready"
    return {
        "contract": "investment-analysis-read-model-v1",
        "generatedAt": str(snapshot.get("generatedAt") or ""),
        "mode": str(toss.get("mode") or snapshot.get("dataMode") or ""),
        "status": str(toss.get("status") or ""),
        "board": {
            "title": "오늘의 투자 판단판",
            "state": "ready" if ready else "blocked",
            "tone": "watch" if ready else "caution",
            "summary": "그래프 추론과 데이터 신선도를 통과한 뒤 액션 후보를 검토합니다." if ready else "투자 판단은 보류하고 데이터와 InferenceBox 상태를 먼저 확인합니다.",
            "metrics": [
                {"label": "보유", "value": len(positions), "caption": "holding"},
                {"label": "관심", "value": len(watchlist), "caption": "watchlist"},
                {"label": "액션 후보", "value": len(queue), "caption": "queue"},
                {"label": "추론 보류", "value": blocked_count, "caption": "blocked"},
            ],
            "checklist": checklist,
        },
        "accountFocus": {
            "accountCount": 1 if toss else 0,
            "holdingCount": len(positions),
            "watchCount": len(watchlist),
            "symbols": sorted({_symbol(item) for item in positions + watchlist if _symbol(item)}),
        },
        "actionQueue": queue,
        "dataLineage": {
            "actualCount": actual_count,
            "mockCount": mock_count,
            "items": data_sources,
        },
        "moneyFlow": _money_flow(snapshot, positions, watchlist),
        "graphGate": graph_gate,
    }
