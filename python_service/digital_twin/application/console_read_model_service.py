"""Build bounded read models for the investor and operator web consoles.

The source stores remain authoritative.  This service only projects persisted
facts into page-sized payloads and never runs market collection or reasoning.
"""

from collections import Counter
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Mapping, Optional


CONSOLE_READ_MODEL_VERSION = "console-read-model-v2"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: object) -> List[Dict[str, object]]:
    return [dict(item) for item in value or [] if isinstance(item, Mapping)] if isinstance(value, (list, tuple)) else []


def _text(value: object) -> str:
    return str(value or "").strip()


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _iso_timestamp(value: object) -> float:
    text = _text(value)
    if not text:
        return 0.0
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        compact = "".join(character for character in text if character.isdigit())
        try:
            return datetime.strptime(compact[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
        except (TypeError, ValueError):
            return 0.0


def _first(mapping: Mapping[str, object], *keys: str):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


class ConsoleReadModelService:
    """Project existing operational payloads into stable page contracts."""

    def __init__(self, symbol_repository=None):
        self.symbol_repository = symbol_repository
        self._symbol_cache: Dict[str, Dict[str, object]] = {}

    def _symbol_detail(self, symbol: object, current: Optional[Mapping[str, object]] = None) -> Dict[str, object]:
        normalized = _text(symbol).upper()
        current_payload = _mapping(current)
        current_name = _text(_first(current_payload, "name", "symbolName", "displayName"))
        resolved = self._symbol_cache.get(normalized)
        if resolved is None:
            resolved = {}
            repository = self.symbol_repository
            if normalized and repository is not None and hasattr(repository, "get"):
                try:
                    value = repository.get(normalized)
                    if isinstance(value, Mapping):
                        resolved = dict(value)
                    elif value is not None and hasattr(value, "to_dict"):
                        resolved = dict(value.to_dict() or {})
                    elif value is not None:
                        resolved = {
                            "name": getattr(value, "name", ""),
                            "market": getattr(value, "market", ""),
                            "currency": getattr(value, "currency", ""),
                            "sector": getattr(value, "sector", ""),
                        }
                except Exception:  # noqa: BLE001 - display metadata must not block a console read.
                    resolved = {}
            self._symbol_cache[normalized] = resolved
        resolved_name = _text(resolved.get("name"))
        name = current_name if current_name and current_name.upper() != normalized else resolved_name
        return {
            "symbol": normalized,
            "name": name or normalized,
            "market": _text(_first(current_payload, "market", "exchange")) or _text(resolved.get("market")),
            "currency": _text(current_payload.get("currency")) or _text(resolved.get("currency")),
            "sector": _text(current_payload.get("sector")) or _text(resolved.get("sector")),
        }

    def enrich_decision_cases(self, payload: Mapping[str, object]) -> Dict[str, object]:
        result = dict(payload or {})
        items = []
        for row in _rows(result.get("items")):
            row.update(self._symbol_detail(row.get("symbol"), row))
            items.append(row)
        result["items"] = items
        result["count"] = len(items)
        return result

    def decision_heads(self, payload: Mapping[str, object]) -> Dict[str, object]:
        """Return list-sized decision heads; complete cases stay behind the detail API."""
        enriched = self.enrich_decision_cases(payload)
        result = {
            key: enriched.get(key)
            for key in ["version", "status", "readOnly", "accountId", "symbol", "summary", "operatorView"]
            if key in enriched
        }
        items = []
        for row in _rows(enriched.get("items")):
            explanation = _mapping(row.get("explanation"))
            attention = _mapping(row.get("attention"))
            outcome = _mapping(row.get("outcome"))
            items.append({
                key: row.get(key)
                for key in [
                    "version", "detailType", "caseId", "episodeId", "subjectCaseId", "batchCaseId",
                    "accountId", "symbol", "name", "market",
                    "currency", "sector", "status", "caseStatus", "phase", "phaseLabel",
                    "readinessState", "readinessLabel", "headline", "nextAction", "decidedAt", "updatedAt",
                ]
            } | {
                "facts": {"dataState": _text(_mapping(row.get("facts")).get("dataState"))},
                "decision": _mapping(row.get("decision")),
                "outcome": {
                    key: outcome.get(key)
                    for key in ["state", "count", "latestObservedAt", "returnPct"]
                    if key in outcome
                },
                "statusDimensions": [
                    {
                        key: dimension.get(key)
                        for key in ["id", "label", "state", "reason", "effect"]
                        if key in dimension
                    }
                    for dimension in _rows(row.get("statusDimensions"))
                ],
                "attention": {
                    "state": _text(attention.get("state")),
                    "label": _text(attention.get("label")),
                    "userActionable": bool(attention.get("userActionable")),
                    "userReviewable": bool(attention.get("userReviewable")),
                    "userAttentionRequired": bool(attention.get("userAttentionRequired")),
                    "issues": _rows(attention.get("issues"))[:1],
                },
                "explanation": {
                    "primaryCause": _mapping(explanation.get("primaryCause")),
                },
                # The bounded subject case contains only the selected TypeDB
                # generation, competing hypotheses, and trace identifiers. It
                # is required to open the decision detail without shipping the
                # full episode/evidence payload in the list response.
                "subjectDecisionCase": _mapping(row.get("subjectDecisionCase")),
            })
        result["items"] = items
        result["count"] = len(items)
        result["detailPathTemplate"] = "/api/decisions/{caseId}"
        return result

    def dashboard_summary(
        self,
        snapshot: Mapping[str, object],
        lifecycle: Mapping[str, object],
        cases: Mapping[str, object],
        calendar: Mapping[str, object],
    ) -> Dict[str, object]:
        compact_cases = self.enrich_decision_cases(cases)
        decisions = _rows(compact_cases.get("items"))
        portfolio = self.portfolio(lifecycle, "summary", snapshot=snapshot)
        tasks = []
        for item in decisions:
            attention = _mapping(item.get("attention"))
            decision = _mapping(item.get("decision"))
            action = _text(decision.get("action")).upper() or "HOLD"
            actionable = bool(attention.get("userActionable")) or action in {"BUY", "ADD", "SELL", "TRIM", "AVOID"}
            reviewable = bool(attention.get("userReviewable"))
            if not actionable and not reviewable:
                continue
            tasks.append({
                "id": _text(item.get("caseId") or item.get("episodeId")),
                "symbol": _text(item.get("symbol")).upper(),
                "name": _text(item.get("name")) or _text(item.get("symbol")).upper(),
                "action": action,
                "headline": _text(item.get("headline")),
                "nextAction": _text(item.get("nextAction")),
                "attentionState": _text(attention.get("state")) or "action",
                "taskType": "trade-review" if actionable else "evidence-review",
                "updatedAt": _text(item.get("updatedAt") or item.get("decidedAt")),
                "detailPath": "/?tab=modeling&detail=investment-case&detailKey=" + _text(item.get("caseId") or item.get("episodeId")),
            })
        tasks.sort(key=lambda item: (_iso_timestamp(item.get("updatedAt")), item.get("symbol", "")), reverse=True)

        blocker_groups: Dict[str, Dict[str, object]] = {}
        labels = {
            "data": "원천 데이터",
            "inference": "관계 추론",
            "ai": "AI 판단",
            "decision": "현재 의견",
            "integrity": "판단 기록",
            "outcome": "결과 관측",
        }
        for item in decisions:
            for dimension in _rows(item.get("statusDimensions")):
                state = _text(dimension.get("state")).lower()
                if state in {"", "pass"}:
                    continue
                key = _text(dimension.get("id")) or "decision"
                group = blocker_groups.setdefault(key, {
                    "id": key,
                    "label": labels.get(key, _text(dimension.get("label")) or "근거 점검"),
                    "count": 0,
                    "symbols": [],
                    "stateCounts": Counter(),
                    "reason": "",
                    "effect": "",
                    "updatedAt": "",
                })
                group["count"] += 1
                symbol = _text(item.get("symbol")).upper()
                if symbol and symbol not in group["symbols"]:
                    group["symbols"].append(symbol)
                group["stateCounts"][state] += 1
                if not group["reason"]:
                    group["reason"] = _text(dimension.get("reason"))
                    group["effect"] = _text(dimension.get("effect"))
                if _iso_timestamp(item.get("updatedAt")) >= _iso_timestamp(group.get("updatedAt")):
                    group["updatedAt"] = _text(item.get("updatedAt"))
        blockers = []
        severity = {"error": 0, "blocked": 1, "warning": 2, "pending": 3}
        for group in blocker_groups.values():
            states = dict(group.pop("stateCounts"))
            group["states"] = states
            group["state"] = sorted(states, key=lambda value: severity.get(value, 9))[0] if states else "warning"
            group["symbolCount"] = len(group["symbols"])
            group["symbols"] = group["symbols"][:6]
            blockers.append(group)
        blockers.sort(key=lambda item: (severity.get(item.get("state"), 9), -int(item.get("count") or 0)))

        now_timestamp = datetime.now(timezone.utc).timestamp()
        events = [
            row for row in _rows(calendar.get("events"))
            if _text(row.get("status")).lower() not in {"inactive", "rejected", "cancelled"}
            and _iso_timestamp(row.get("startsAt") or row.get("localDate")) >= now_timestamp - 86400
        ]
        events.sort(key=lambda row: _iso_timestamp(row.get("startsAt") or row.get("localDate")))
        return {
            "version": CONSOLE_READ_MODEL_VERSION,
            "generatedAt": _text(snapshot.get("generatedAt")) or datetime.now(timezone.utc).isoformat(),
            "dataFreshness": _mapping(snapshot.get("dataFreshness")),
            "account": _mapping(_mapping(snapshot.get("toss")).get("account")),
            "portfolio": portfolio.get("summary", {}),
            "tasks": tasks[:3],
            "taskSummary": {
                "actionable": len(tasks),
                "displayed": min(3, len(tasks)),
                "decisionCount": len(decisions),
            },
            "blockerGroups": blockers[:3],
            "blockerSummary": {
                "groups": len(blockers),
                "affectedDecisions": sum(1 for item in decisions if _text(item.get("readinessState")) != "pass"),
            },
            "upcomingEvents": events[:4],
            "calendarSummary": _mapping(calendar.get("summary")),
            "sourceStatus": {
                "snapshot": _mapping(snapshot.get("readModel")),
                "portfolio": _text(lifecycle.get("status")) or "unavailable",
                "decisions": _text(cases.get("status")) or "unavailable",
            },
        }

    def portfolio(
        self,
        lifecycle: Mapping[str, object],
        view: str = "summary",
        snapshot: Optional[Mapping[str, object]] = None,
        subject_case: Optional[Mapping[str, object]] = None,
    ) -> Dict[str, object]:
        lifecycle = _mapping(lifecycle)
        snapshot = _mapping(snapshot)
        toss = _mapping(snapshot.get("toss"))
        current_portfolio = _mapping(snapshot.get("portfolio")) or _mapping(toss.get("portfolio"))
        current_positions = {
            _text(row.get("symbol")).upper(): row
            for row in _rows(toss.get("positions"))
            if _text(row.get("symbol"))
        }
        state = _mapping(lifecycle.get("portfolioState"))
        checkpoint = _mapping(lifecycle.get("snapshotCheckpoint"))
        risk = _mapping(lifecycle.get("portfolioRiskSnapshot"))
        exposure = _mapping(lifecycle.get("exposureSnapshot"))
        rebalance = _mapping(lifecycle.get("rebalanceProposal"))
        cycle = _mapping(lifecycle.get("portfolioDecisionCycle"))
        positions = []
        for row in _rows(state.get("positions")):
            current = current_positions.get(_text(row.get("symbol")).upper())
            if current:
                for key in [
                    "marketValueKrw", "accountValueKrw", "accountValueBasis",
                    "brokerGrossValueKrw", "brokerNetValueKrw", "markToMarketValueKrw",
                    "brokerMarketValueKrw", "brokerMarketValueAfterCostKrw",
                    "markToMarketValueKrw", "valuationSnapshotId", "valuationFxSource",
                    "valuationFxState", "valuationFxAsOf", "currentPrice", "profitLossRate",
                ]:
                    if current.get(key) not in (None, ""):
                        row[key] = current[key]
            row.update(self._symbol_detail(row.get("symbol"), row))
            positions.append(row)
        positions.sort(key=lambda item: _number(item.get("currentWeightPct")), reverse=True)
        exposure_metrics = _rows(exposure.get("metrics"))
        policy_breaches = [item for item in exposure_metrics if _number(item.get("policyDeltaPct")) > 0]
        candidates = _rows(cycle.get("candidates"))
        action_plans = _rows(lifecycle.get("actionPlans"))
        risk_breach_count = sum(
            1 for key in ("volatilityPolicyDeltaPct", "drawdownPolicyDeltaPct")
            if _number(_mapping(lifecycle.get("rebalanceState")).get(key)) > 0
        )
        interpretation = self._portfolio_interpretation(
            lifecycle,
            subject_case,
            policy_breaches,
            candidates,
            full=view == "interpretation",
        )
        summary = {
            "portfolioId": _text(lifecycle.get("portfolioId") or state.get("portfolioId")),
            "status": _text(lifecycle.get("status")) or "unavailable",
            "observedAt": _text(checkpoint.get("observedAt") or state.get("observedAt")),
            "total": _number(checkpoint.get("portfolioTotal")),
            "cash": _number(checkpoint.get("cashBalance")),
            "cashWeightPct": _number(state.get("cashWeightPct")),
            "positionCount": len(positions),
            "periodReturnPct": _number(risk.get("periodReturnPct")),
            "annualizedVolatilityPct": _number(risk.get("annualizedVolatilityPct")),
            "maximumDrawdownPct": _number(risk.get("maximumDrawdownPct")),
            "policyBreachCount": len(policy_breaches) + risk_breach_count,
            "exposureBreachCount": len(policy_breaches),
            "riskBreachCount": risk_breach_count,
            "rebalanceStatus": _text(rebalance.get("status")) or "not-ready",
            "reconciliationStatus": _text(_mapping(lifecycle.get("reconciliation")).get("status")) or "unknown",
            "interpretationStatus": _text(interpretation.get("status")) or "unavailable",
        }
        if current_portfolio:
            current_total = _first(current_portfolio, "accountEquityTotal", "account_equity_total", "total")
            current_cash = _first(current_portfolio, "cash")
            current_invested = _first(current_portfolio, "invested")
            if current_total is not None:
                summary["total"] = _number(current_total)
            if current_cash is not None:
                summary["cash"] = _number(current_cash)
            if current_invested is not None:
                summary["invested"] = _number(current_invested)
            summary.update({
                "observedAt": _text(snapshot.get("generatedAt")) or summary["observedAt"],
                "valuationSnapshotId": _text(_first(current_portfolio, "valuationSnapshotId", "valuation_snapshot_id")),
                "valuationBasis": _text(_first(current_portfolio, "valuationBasis", "valuation_basis")) or "legacy-unknown",
                "brokerComparableTotal": _number(_first(current_portfolio, "brokerComparableTotal", "broker_comparable_total")),
                "brokerGrossTotal": _number(_first(current_portfolio, "brokerGrossTotal", "broker_gross_total")),
                "brokerNetTotal": _number(_first(current_portfolio, "brokerNetTotal", "broker_net_total")),
                "markToMarketTotal": _number(_first(current_portfolio, "markToMarketTotal", "mark_to_market_total")),
                "valuation": _mapping(current_portfolio.get("valuation")),
                "valuationSource": "current-account-snapshot",
            })
        else:
            summary.update({
                "valuationSnapshotId": _text(checkpoint.get("valuationSnapshotId")),
                "valuationBasis": _text(checkpoint.get("valuationBasis")) or "legacy-unknown",
                "brokerComparableTotal": _number(checkpoint.get("brokerComparableTotal")),
                "brokerGrossTotal": _number(checkpoint.get("brokerGrossTotal")),
                "brokerNetTotal": _number(checkpoint.get("brokerNetTotal")),
                "markToMarketTotal": _number(checkpoint.get("markToMarketTotal")),
                "valuation": {},
                "valuationSource": "portfolio-lifecycle-checkpoint",
            })
        base = {
            "version": CONSOLE_READ_MODEL_VERSION,
            "view": view,
            "summary": summary,
        }
        if view in {"summary", "rebalance", "interpretation"}:
            base["interpretation"] = interpretation
        if view == "positions":
            base.update({"positions": positions, "count": len(positions)})
        elif view == "rebalance":
            base.update({
                "proposal": {
                    "proposalId": _text(rebalance.get("proposalId")),
                    "status": _text(rebalance.get("status")),
                    "createdAt": _text(rebalance.get("createdAt")),
                    "drifts": _rows(rebalance.get("drifts")),
                    "scenarios": _rows(rebalance.get("scenarios")),
                    "recommendedScenarioId": _text(rebalance.get("recommendedScenarioId")),
                },
                "candidates": candidates,
                "actionPlans": [self._action_plan_head(item) for item in action_plans[:8]],
                "policyBreaches": policy_breaches,
                "risk": {
                    key: risk.get(key)
                    for key in [
                        "dataState", "observedAt", "sampleCount", "periodReturnPct",
                        "annualizedVolatilityPct", "maximumDrawdownPct",
                        "maximumPairwiseCorrelation", "missingData",
                    ]
                },
            })
        elif view == "interpretation":
            base.update({
                "proposal": {
                    "proposalId": _text(rebalance.get("proposalId")),
                    "status": _text(rebalance.get("status")),
                    "createdAt": _text(rebalance.get("createdAt")),
                    "scenarioCount": len(_rows(rebalance.get("scenarios"))),
                    "candidateCount": len(candidates),
                },
            })
        elif view == "activity":
            ledger = _rows(lifecycle.get("ledgerEntries"))
            activity = _rows(lifecycle.get("recentActivityEpisodes"))
            reviews = _rows(lifecycle.get("decisionReviews"))
            base.update({
                "ledgerSummary": _mapping(lifecycle.get("ledgerSummary")),
                "reconciliation": {
                    key: _mapping(lifecycle.get("reconciliation")).get(key)
                    for key in ["reconciliationId", "status", "differenceCount", "createdAt", "sourceSnapshotAt"]
                },
                "ledgerEntries": [self._ledger_head(item) for item in ledger[:40]],
                "activityEpisodes": [self._activity_head(item) for item in activity[:20]],
                "actionPlans": [self._action_plan_head(item) for item in action_plans[:20]],
                "decisionReviews": [self._decision_review_head(item) for item in reviews[:20]],
                "counts": {
                    "ledger": len(ledger),
                    "activity": len(activity),
                    "actionPlans": len(action_plans),
                    "decisionReviews": len(reviews),
                },
            })
        else:
            base.update({
                "positions": positions,
                "exposures": exposure_metrics,
                "policyBreaches": policy_breaches,
                "risk": {
                    key: risk.get(key)
                    for key in [
                        "dataState", "observedAt", "sampleCount", "periodReturnPct",
                        "annualizedVolatilityPct", "maximumDrawdownPct",
                        "maximumPairwiseCorrelation", "missingData",
                    ]
                },
                "rebalance": {
                    "proposalId": _text(rebalance.get("proposalId")),
                    "status": _text(rebalance.get("status")),
                    "createdAt": _text(rebalance.get("createdAt")),
                    "scenarioCount": len(_rows(rebalance.get("scenarios"))),
                    "candidateCount": len(candidates),
                },
            })
        return base

    @staticmethod
    def _portfolio_interpretation(
        lifecycle: Mapping[str, object],
        subject_case: Optional[Mapping[str, object]],
        policy_breaches: List[Mapping[str, object]],
        candidates: List[Mapping[str, object]],
        full: bool = False,
    ) -> Dict[str, object]:
        lifecycle = _mapping(lifecycle)
        case = _mapping(subject_case)
        state = _mapping(lifecycle.get("rebalanceState"))
        cycle = _mapping(lifecycle.get("portfolioDecisionCycle"))
        checkpoint = _mapping(lifecycle.get("snapshotCheckpoint"))
        proposal = _mapping(lifecycle.get("rebalanceProposal"))
        risk = _mapping(lifecycle.get("portfolioRiskSnapshot"))
        synthesis = _mapping(case.get("synthesis"))
        judgment = _mapping(case.get("aiJudgment"))
        publication = _mapping(case.get("publication"))
        explanation = _mapping(publication.get("explanationSnapshot"))

        current_revision = _text(state.get("revision") or cycle.get("cycleId") or checkpoint.get("balanceFingerprint"))
        interpreted_revision = _text(case.get("sourceAboxSnapshotId"))
        current_at = _text(checkpoint.get("observedAt") or state.get("observedAt"))
        interpreted_at = _text(case.get("updatedAt") or publication.get("createdAt"))
        current_epoch = _iso_timestamp(current_at)
        interpreted_epoch = _iso_timestamp(interpreted_at)
        revision_state = "unknown"
        if current_epoch and interpreted_epoch:
            revision_state = "current" if interpreted_epoch + 300 >= current_epoch else "stale"

        has_ai = bool(
            _text(judgment.get("result_id") or judgment.get("resultId"))
            or _text(judgment.get("rationale"))
        )
        case_status = _text(case.get("stage")).lower()
        if has_ai:
            status = "ready" if revision_state != "stale" else "stale"
        elif case:
            status = "observation" if revision_state != "stale" else "stale"
        else:
            status = "pending"

        drivers = []
        exposure_labels = {
            "position": "종목 비중",
            "sector": "섹터 비중",
            "currency": "통화 비중",
            "cash": "현금 비중",
        }
        sorted_breaches = sorted(
            policy_breaches,
            key=lambda item: _number(item.get("policyDeltaPct")),
            reverse=True,
        )
        for row in sorted_breaches:
            kind = _text(row.get("exposure_type") or row.get("exposureType")).lower()
            key = _text(row.get("key"))
            delta = _number(row.get("policyDeltaPct"))
            drivers.append({
                "id": kind + ":" + key,
                "label": (key + " " if key else "") + exposure_labels.get(kind, kind or "배분 정책"),
                "value": ("%.2f%%p 초과" % delta),
                "detail": "저장된 계좌 노출이 투자 정책 한도를 벗어났습니다.",
                "tone": "danger",
                "source": "portfolio-exposure",
            })
        for key, label in (("volatilityPolicyDeltaPct", "변동성 한도"), ("drawdownPolicyDeltaPct", "낙폭 한도")):
            delta = _number(state.get(key))
            if delta > 0:
                drivers.append({
                    "id": "risk:" + key,
                    "label": label,
                    "value": "%.2f%%p 초과" % delta,
                    "detail": "저장된 위험 시계열과 포트폴리오 정책을 비교한 결과입니다.",
                    "tone": "danger",
                    "source": "portfolio-risk",
                })

        breach_count = len(drivers)
        if breach_count:
            headline = "정책 이탈 %d건, 리밸런싱 검토가 필요합니다." % breach_count
            rationale = "가장 큰 이탈부터 시나리오의 비용과 제약을 비교하세요. 자동 주문은 실행하지 않습니다."
        else:
            headline = "현재 포트폴리오는 저장된 정책 한도 안에 있습니다."
            rationale = "새 계좌 스냅샷과 위험 시계열이 들어오면 같은 기준으로 다시 평가합니다."

        missing_data = []
        for value in list(cycle.get("missingData") or []) + list(risk.get("missingData") or []) + list(
            synthesis.get("missing_data") or synthesis.get("missingData") or []
        ):
            text = _text(value)
            if text and text not in missing_data:
                missing_data.append(text)
        next_checks = []
        for value in list(judgment.get("next_observations") or judgment.get("nextObservations") or []) + list(
            synthesis.get("next_checks") or synthesis.get("nextChecks") or []
        ):
            text = _text(value)
            if text and text not in next_checks:
                next_checks.append(text)
        if not next_checks:
            if missing_data:
                next_checks.append("누락된 위험·벤치마크 시계열 보완")
            if candidates:
                next_checks.append("리밸런싱 시나리오의 비용과 실행 제약 검토")

        invalidation = []
        for value in list(judgment.get("reversal_conditions") or judgment.get("reversalConditions") or []) + list(
            synthesis.get("reversal_conditions") or synthesis.get("reversalConditions") or []
        ):
            text = _text(value)
            if text and text not in invalidation:
                invalidation.append(text)
        conflicts = []
        for value in judgment.get("opposing_evidence_ids") or judgment.get("opposingEvidenceIds") or []:
            text = _text(value)
            if text:
                conflicts.append({"label": "반대 근거", "detail": text})
        rejected_reason = _text(judgment.get("rejected_candidate_reason") or judgment.get("rejectedCandidateReason"))
        if rejected_reason:
            conflicts.insert(0, {"label": "후보 조정", "detail": rejected_reason})

        ai_rationale = _text(judgment.get("rationale") or explanation.get("reason"))
        status_labels = {
            "ready": "최신 AI 해석",
            "stale": "이전 AI 해석" if has_ai else "이전 추론 기록",
            "observation": "AI 실행 없음",
            "pending": "해석 대기",
        }
        limit = 12 if full else 4
        return {
            "contract": "portfolio-interpretation-v1",
            "status": status,
            "statusLabel": status_labels.get(status, "해석 상태 확인"),
            "headline": headline,
            "rationale": rationale,
            "action": _text(judgment.get("action") or synthesis.get("execution_action") or synthesis.get("executionAction") or "NO_ACTION"),
            "confidence": _number(judgment.get("confidence")),
            "dataState": _text(state.get("dataState") or cycle.get("dataState") or synthesis.get("data_state") or synthesis.get("dataState")) or "unknown",
            "generatedAt": interpreted_at,
            "drivers": drivers[:limit],
            "conflicts": conflicts[:limit],
            "missingData": missing_data[:limit],
            "nextChecks": next_checks[:limit],
            "invalidationConditions": invalidation[:limit],
            "ai": {
                "executed": has_ai,
                "current": has_ai and revision_state != "stale",
                "status": "executed" if has_ai else "not-run",
                "rationale": ai_rationale,
                "model": _text(judgment.get("model")),
                "validationState": _text(judgment.get("validation_state") or judgment.get("validationState")),
                "selectedHypothesisId": _text(judgment.get("selected_hypothesis_id") or judgment.get("selectedHypothesisId")),
                "supportingEvidenceIds": list(judgment.get("supporting_evidence_ids") or judgment.get("supportingEvidenceIds") or [])[:limit],
                "opposingEvidenceIds": list(judgment.get("opposing_evidence_ids") or judgment.get("opposingEvidenceIds") or [])[:limit],
            },
            "revision": {
                "state": revision_state,
                "current": current_revision,
                "interpreted": interpreted_revision,
                "currentObservedAt": current_at,
                "interpretedAt": interpreted_at,
                "comparisonBasis": "observed-at-window",
            },
            "sources": [
                {"kind": "calculation", "label": "계좌 원장·위험 시계열", "id": _text(checkpoint.get("valuationSnapshotId"))},
                {"kind": "typedb", "label": "TypeDB 추론", "id": _text(case.get("inferenceGenerationId"))},
                {"kind": "ai", "label": "저장된 AI 판단", "id": _text(judgment.get("result_id") or judgment.get("resultId")), "used": has_ai},
            ],
            "trace": {
                "subjectCaseId": _text(case.get("subjectCaseId")),
                "stage": case_status,
                "publicationOutcome": _text(publication.get("outcomeKind")),
                "sourceAboxSnapshotId": interpreted_revision,
                "inferenceGenerationId": _text(case.get("inferenceGenerationId")),
                "deploymentId": _text(case.get("deploymentId")),
                "releaseFingerprint": _text(case.get("releaseFingerprint")),
                "proposalId": _text(proposal.get("proposalId")),
                "cycleId": _text(cycle.get("cycleId")),
            },
        }

    def market_instruments(self, snapshot: Mapping[str, object]) -> Dict[str, object]:
        toss = _mapping(snapshot.get("toss"))
        combined = []
        for source_key, source_label in (("positions", "holding"), ("watchlist", "watchlist"), ("watchlistQuotes", "watchlist")):
            for row in _rows(toss.get(source_key)):
                row.setdefault("source", source_label)
                combined.append(row)
        deduplicated: Dict[str, Dict[str, object]] = {}
        for row in combined:
            symbol = _text(row.get("symbol")).upper()
            if not symbol:
                continue
            key = (_text(row.get("market")) or "-") + ":" + symbol
            previous = deduplicated.get(key)
            if previous and _text(previous.get("source")) == "holding":
                continue
            deduplicated[key] = row
        instruments = []
        for row in deduplicated.values():
            detail = self._symbol_detail(row.get("symbol"), row)
            coverage = _mapping(_first(row, "marketSignalCoverage", "market_signal_coverage"))
            investor = _mapping(coverage.get("investor"))
            instruments.append({
                **detail,
                "source": _text(row.get("source")) or "watchlist",
                "currentPrice": _number(_first(row, "currentPrice", "current_price")),
                "changeRate": _number(_first(row, "changeRate", "change_rate")),
                "quantity": _number(row.get("quantity")),
                "marketValueKrw": _number(_first(row, "accountValueKrw", "account_value_krw", "marketValueKrw", "market_value_krw")),
                "accountValueBasis": _text(_first(row, "accountValueBasis", "account_value_basis")) or "legacy-unknown",
                "valuationSnapshotId": _text(_first(row, "valuationSnapshotId", "valuation_snapshot_id")),
                "profitLossRate": _number(_first(row, "profitLossRate", "profit_loss_rate")),
                "ma5": _number(row.get("ma5")),
                "ma20": _number(row.get("ma20")),
                "ma60": _number(row.get("ma60")),
                "volume": _number(row.get("volume")),
                "volumeRatio": _number(_first(row, "volumeRatio", "volume_ratio")),
                "tradeStrength": _number(_first(row, "tradeStrength", "trade_strength")),
                "foreignBuyVolume": _number(_first(row, "foreignBuyVolume", "foreign_buy_volume")),
                "foreignSellVolume": _number(_first(row, "foreignSellVolume", "foreign_sell_volume")),
                "foreignNetVolume": _number(_first(row, "foreignNetVolume", "foreign_net_volume")),
                "institutionBuyVolume": _number(_first(row, "institutionBuyVolume", "institution_buy_volume")),
                "institutionSellVolume": _number(_first(row, "institutionSellVolume", "institution_sell_volume")),
                "institutionNetVolume": _number(_first(row, "institutionNetVolume", "institution_net_volume")),
                "marketSignalCoverage": {"investor": investor} if investor else {},
                "investorStatus": _text(investor.get("status")) or "unknown",
                "investorReason": _text(investor.get("reason")),
                "freshnessStatus": _text(_first(row, "freshnessStatus", "freshness_status")),
                "quoteStatus": _text(_first(row, "quoteStatus", "quote_status")),
                "quoteSource": _text(_first(row, "quoteSource", "quote_source")),
                "updatedAt": _text(_first(row, "updatedAt", "updated_at", "sourceAsOf", "source_as_of")),
            })
        instruments.sort(key=lambda item: (
            0 if item.get("source") == "holding" else 1,
            -_number(item.get("marketValueKrw")),
            item.get("symbol", ""),
        ))
        return {
            "version": CONSOLE_READ_MODEL_VERSION,
            "generatedAt": _text(snapshot.get("generatedAt")),
            "dataFreshness": _mapping(snapshot.get("dataFreshness")),
            "items": instruments,
            "summary": {
                "total": len(instruments),
                "holdings": sum(1 for item in instruments if item.get("source") == "holding"),
                "watchlist": sum(1 for item in instruments if item.get("source") != "holding"),
                "stale": sum(1 for item in instruments if item.get("freshnessStatus") in {"stale", "unavailable"}),
            },
        }

    def market_evidence(self, payload: Mapping[str, object], limit: int = 12) -> Dict[str, object]:
        eligible = []
        for row in _rows(payload.get("items")):
            if row.get("displayEligible") is False:
                continue
            if _text(row.get("kind")).lower() == "financial-fact" and not _text(row.get("url")):
                continue
            eligible.append(self._evidence_head(row))
        eligible.sort(
            key=lambda item: _iso_timestamp(item.get("publishedAt") or item.get("observedAt")),
            reverse=True,
        )
        bounded = max(1, min(100, int(limit or 12)))
        return {
            "version": CONSOLE_READ_MODEL_VERSION,
            "items": eligible[:bounded],
            "totalEligible": len(eligible),
            "sourceTotal": int(payload.get("total") or len(_rows(payload.get("items")))),
            "summary": _mapping(payload.get("summary")),
            "articleAnalysis": _mapping(payload.get("articleAnalysis")),
            "officialAnalysis": _mapping(payload.get("officialAnalysis")),
        }

    def _evidence_head(self, row: Mapping[str, object]) -> Dict[str, object]:
        detail = self._symbol_detail(row.get("symbol"), row)
        payload = _mapping(row.get("payload"))
        keys = [
            "evidenceId", "symbol", "kind", "source", "title", "summary", "url", "observedAt",
            "publishedAt", "polarity", "evidenceRole", "relationScope", "eventType", "relevanceState",
            "sourceTrustState", "materialityState", "dataState", "validationState", "analysisSummary",
            "articleSummaryKo", "originalTitle", "translatedTitleKo", "sourceLanguage", "translationStatus",
            "summaryQualityState", "analysisStatus", "articleReadStatus", "stockImpactLabel",
            "stockImpactPolarity", "stockImpactReasonKo", "displayEligible", "detailPath", "publisher",
            "publisherDomain", "alertEligible", "reasoningEligible", "promptEvidenceAdmission",
            "eligibilityAudit", "officialDocumentState", "metadataVerified", "documentVerified",
            "analysisReady", "documentHash", "documentCharCount", "officialDocumentPreview",
            "disclosureDocumentQuality", "documentLifecycle", "disclosureAnalysis", "sourceRevision",
            "sourceAsOf", "sourceFetchedAt", "sourceDocuments", "disclosureCategory",
        ]
        projected = {key: row.get(key) for key in keys if key in row}
        projected.update(detail)
        projected["articleSummaryQuality"] = {
            "state": _text(_mapping(row.get("articleSummaryQuality")).get("state")),
            "issues": list(_mapping(row.get("articleSummaryQuality")).get("issues") or [])[:3],
        }
        projected["payload"] = {
            key: payload.get(key)
            for key in ["name", "relevanceState", "sourceTrustState", "materialityState", "validationState"]
            if key in payload
        }
        return projected

    @staticmethod
    def _ledger_head(row: Mapping[str, object]) -> Dict[str, object]:
        return {
            key: row.get(key)
            for key in [
                "entry_id", "entryId", "entry_type", "entryType", "symbol", "quantity", "amount",
                "currency", "unit_price", "unitPrice", "fee", "source", "source_reference",
                "sourceReference", "occurred_at", "occurredAt",
            ]
            if key in row
        }

    @staticmethod
    def _activity_head(row: Mapping[str, object]) -> Dict[str, object]:
        return {
            key: row.get(key)
            for key in [
                "episodeId", "classification", "title", "summary", "symbols", "cashDelta",
                "estimatedNotional", "confidence", "executable", "observedAt", "previousObservedAt",
            ]
            if key in row
        }

    @staticmethod
    def _action_plan_head(row: Mapping[str, object]) -> Dict[str, object]:
        return {
            key: row.get(key)
            for key in [
                "plan_id", "planId", "action", "status", "created_at", "createdAt", "expires_at",
                "expiresAt", "decision_episode_id", "decisionEpisodeId", "portfolio_id", "portfolioId",
            ]
            if key in row
        } | {"orderIntentCount": len(row.get("order_intents") or row.get("orderIntents") or [])}

    @staticmethod
    def _decision_review_head(row: Mapping[str, object]) -> Dict[str, object]:
        return {
            key: row.get(key)
            for key in [
                "review_id", "reviewId", "decision_episode_id", "decisionEpisodeId", "reviewed_at",
                "reviewedAt", "evidence_still_valid", "evidenceStillValid", "execution_compliant",
                "executionCompliant", "policy_compliant", "policyCompliant", "selected_hypothesis_status",
                "selectedHypothesisStatus",
            ]
            if key in row
        }

    def operations_health(self, payloads: Mapping[str, object]) -> Dict[str, object]:
        realtime = _mapping(payloads.get("realtime"))
        external = _mapping(payloads.get("external"))
        reasoning = _mapping(payloads.get("reasoning"))
        engine = _mapping(payloads.get("engine"))
        time_series = _mapping(payloads.get("timeSeries"))
        storage = _mapping(payloads.get("storage"))
        providers = _rows(external.get("providers"))
        now_epoch = datetime.now(timezone.utc).timestamp()
        failed_providers = [item for item in providers if _text(item.get("state")).lower() not in {"healthy", "ok", "ready"}]
        pending = int(reasoning.get("effectivePendingCount") or reasoning.get("pendingCount") or 0)
        processing = int(reasoning.get("processingCount") or 0)
        notification_summary = _mapping(realtime.get("notificationJobs"))
        ai_summary = _mapping(realtime.get("aiInferenceQueue"))
        ai_actionable_failures = int(
            ai_summary.get("actionableFailedCount")
            if ai_summary.get("actionableFailedCount") is not None
            else ai_summary.get("failedCount") or 0
        )
        ai_effective_status = _text(ai_summary.get("effectiveAiStatus") or "healthy").lower()
        ai_effective_window_hours = int(ai_summary.get("effectiveAiWindowHours") or 24)
        notification_actionable_failures = int(
            notification_summary.get("actionable_failed")
            if notification_summary.get("actionable_failed") is not None
            else notification_summary.get("failed") or 0
        )
        reasoning_status = _text(reasoning.get("status") or reasoning.get("state")).lower()
        reasoning_queue_health = _text(_mapping(_mapping(reasoning.get("legacyReasoningQueue")).get("queueHealth")).get("status")).lower()
        reasoning_oldest_at = _text(reasoning.get("oldestRequestAt"))
        reasoning_oldest_age_seconds = max(
            0,
            int(now_epoch - _iso_timestamp(reasoning_oldest_at)),
        ) if _iso_timestamp(reasoning_oldest_at) else 0
        reasoning_healthy = pending == 0 and processing == 0 and (
            reasoning_status in {"active", "healthy", "ready", "idle"}
            or reasoning_queue_health in {"healthy", "ready", "ok"}
        )
        reasoning_state = (
            "critical"
            if reasoning_status in {"critical", "failed", "blocked"}
            or reasoning_oldest_age_seconds > 10 * 60
            else "healthy" if reasoning_healthy
            else "warning"
        )
        monitoring = _mapping(realtime.get("monitoring"))
        monitor_snapshot = _mapping(monitoring.get("snapshot"))
        monitor_updated_at = _text(
            monitor_snapshot.get("occurredAt")
            or monitor_snapshot.get("generatedAt")
            or monitor_snapshot.get("updatedAt")
            or monitoring.get("generatedAt")
        )
        monitor_updated_epoch = _iso_timestamp(monitor_updated_at)
        monitor_age_seconds = max(0, int(now_epoch - monitor_updated_epoch)) if monitor_updated_epoch else 0
        monitor_state = (
            "warning"
            if _text(realtime.get("storeWarning"))
            or not monitoring
            or not monitor_updated_epoch
            or monitor_age_seconds > 15 * 60
            else "healthy"
        )
        ai_states = _mapping(ai_summary.get("states"))
        ai_oldest_at = min(
            (
                _text(_mapping(ai_states.get(state)).get("oldestAt"))
                for state in ("pending", "retry", "processing")
                if _text(_mapping(ai_states.get(state)).get("oldestAt"))
            ),
            default="",
        )
        ai_oldest_epoch = _iso_timestamp(ai_oldest_at)
        ai_oldest_age_seconds = max(0, int(now_epoch - ai_oldest_epoch)) if ai_oldest_epoch else 0
        notification_suppression_categories = _mapping(notification_summary.get("suppression_categories"))
        suppression_parts = []
        for key, label in (
            ("unchanged_decision", "동일 판단"),
            ("baseline", "기준선"),
            ("duplicate_or_cooldown", "중복·쿨다운"),
            ("data_guard", "자료 보호"),
            ("other_policy", "기타 정책"),
        ):
            count = int(notification_suppression_categories.get(key) or 0)
            if count:
                suppression_parts.append(label + " " + str(count))
        time_series_control = _mapping(time_series.get("control"))
        active_backend_id = _text(time_series_control.get("activeBackendId") or time_series_control.get("active_backend_id"))
        time_series_health = _mapping(_mapping(time_series.get("health")).get(active_backend_id))
        time_series_state = self._generic_health_state(time_series_health or (time_series if not active_backend_id else {}))
        if time_series_state == "unknown" and active_backend_id:
            active_deployment = next(
                (item for item in _rows(time_series.get("deployments")) if _text(item.get("backendId")) == active_backend_id),
                {},
            )
            time_series_state = self._generic_health_state(_mapping(active_deployment.get("health")) or active_deployment)
        engine_control = _mapping(engine.get("control"))
        active_deployment_id = _text(
            engine_control.get("activeDeploymentId")
            or engine_control.get("active_deployment_id")
            or engine_control.get("deliveryDeploymentId")
            or engine_control.get("delivery_deployment_id")
        )
        active_engine = _mapping(engine.get("activeDeployment")) or next(
            (item for item in _rows(engine.get("deployments")) if _text(item.get("deploymentId")) == active_deployment_id),
            {},
        )
        engine_guard = _mapping(_mapping(active_engine.get("health")).get("executionGuard"))
        engine_state = self._generic_health_state(active_engine or engine)
        platform_status = _text(engine.get("status")).lower()
        if platform_status in {"unavailable", "blocked", "critical"}:
            engine_state = "critical"
        elif platform_status == "degraded" and engine_state != "critical":
            engine_state = "warning"
        guard_state = _text(engine_guard.get("status")).lower()
        if guard_state in {"failed", "error", "critical", "blocked"}:
            engine_state = "critical"
        elif guard_state and guard_state not in {"ready", "healthy", "ok"}:
            engine_state = "warning"
        storage_stage = _text(storage.get("mysqlCapacityStage")).lower()
        storage_state = (
            "critical" if storage_stage in {"critical", "core-only"}
            or _text(storage.get("status")).lower() == "critical-low-disk"
            else "warning" if storage_stage in {"maintenance", "warning", "restricted"}
            or _text(storage.get("status")).lower() in {"guarded-low-disk", "pressure", "unavailable"}
            else "healthy"
        )
        rows = [
            {
                "id": "monitoring",
                "label": "계좌·시장 모니터",
                "state": monitor_state,
                "detail": (
                    "최근 모니터링 이벤트를 읽었습니다."
                    if monitor_state == "healthy"
                    else "최근 모니터링 스냅샷이 없거나 15분 이상 갱신되지 않았습니다."
                ),
                "updatedAt": monitor_updated_at,
            },
            {
                "id": "external-data",
                "label": "외부 데이터 수집",
                "state": "healthy" if providers and not failed_providers else ("warning" if providers else "unknown"),
                "detail": f"공급자 {len(providers)}개 · 확인 필요 {len(failed_providers)}개",
                "updatedAt": max((_text(item.get("updatedAt")) for item in providers), default=""),
            },
            {
                "id": "reasoning",
                "label": "TypeDB 관계 추론",
                "state": reasoning_state,
                "detail": (
                    f"대기 {pending}건 · 처리 {processing}건 · 배포 {_text(reasoning.get('deploymentId')) or '확인 필요'}"
                    + (f" · 최장 {reasoning_oldest_age_seconds}초" if reasoning_oldest_age_seconds else "")
                ),
                "updatedAt": reasoning_oldest_at,
            },
            {
                "id": "ai",
                "label": "AI 판단 대기열",
                "state": (
                    "critical" if ai_actionable_failures or ai_effective_status == "critical" or ai_oldest_age_seconds > 10 * 60
                    else "warning" if ai_effective_status == "degraded"
                    else "healthy"
                ),
                "detail": (
                    f"대기 {int(ai_summary.get('pendingCount') or 0)}건 · 처리 {int(ai_summary.get('processingCount') or 0)}건"
                    f" · 최근 {ai_effective_window_hours}시간 실효 AI {int(ai_summary.get('effectiveAiAuthoredCount') or 0)}/{int(ai_summary.get('effectiveAiEligibleCount') or 0)}건"
                    f" · 폴백 {int(ai_summary.get('effectiveAiFallbackCount') or 0)}건 · 현재 실패 {ai_actionable_failures}건"
                    f" · 누적 실패 {int(ai_summary.get('historicalFailedCount') or ai_summary.get('failedCount') or 0)}건"
                    + (f" · 최장 {ai_oldest_age_seconds}초" if ai_oldest_age_seconds else "")
                ),
                "updatedAt": ai_oldest_at or _text(ai_summary.get("effectiveAiLatestAt")),
            },
            {
                "id": "notifications",
                "label": "알림 전달",
                "state": "warning" if notification_actionable_failures else "healthy",
                "detail": (
                    f"현재 대기 {int(notification_summary.get('pending') or 0) + int(notification_summary.get('awaiting_ai') or 0)}건"
                    f" · 처리 {int(notification_summary.get('processing') or 0)}건"
                    f" · 현재 실패 {notification_actionable_failures}건"
                    f" · 누적 실패 {int(notification_summary.get('historical_failed') or notification_summary.get('failed') or 0)}건"
                    f" · 누적 완료 {int(notification_summary.get('done') or 0)}건"
                    f" · 정책 억제 {int(notification_summary.get('intentional_suppressed') or notification_summary.get('suppressed') or 0)}건"
                    + (" (" + " · ".join(suppression_parts) + ")" if suppression_parts else "")
                ),
                "updatedAt": "",
            },
            {
                "id": "time-series",
                "label": "시계열 플랫폼",
                "state": time_series_state,
                "detail": f"활성 백엔드 {active_backend_id or '미선택'} · {_text(time_series_health.get('status')) or '상태 확인'}",
                "updatedAt": _text(time_series_control.get("updatedAt") or time_series_control.get("updated_at")),
            },
            {
                "id": "reasoning-engine",
                "label": "추론 엔진 배포",
                "state": engine_state,
                "detail": f"활성 배포 {active_deployment_id or '미선택'}" + (f" · {guard_state}" if guard_state else ""),
                "updatedAt": _text(active_engine.get("updatedAt") or active_engine.get("updated_at")),
            },
            {
                "id": "storage",
                "label": "운영 저장공간",
                "state": storage_state,
                "detail": (
                    f"MySQL {storage.get('mysqlSizeMb') or 0}MB / {storage.get('mysqlLimitMb') or 0}MB"
                    f" · 회수 가능 {storage.get('mysqlReclaimableMb') or 0}MB"
                    f" · TypeDB {storage.get('typedbSizeMb') or 0}MB"
                ),
                "updatedAt": "",
            },
        ]
        counts = Counter(item["state"] for item in rows)
        return {
            "version": CONSOLE_READ_MODEL_VERSION,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "state": "critical" if counts.get("critical") else ("warning" if counts.get("warning") or counts.get("unknown") else "healthy"),
            "summary": dict(counts),
            "components": rows,
            "queues": {
                "reasoning": {
                    "pending": pending,
                    "processing": processing,
                    "retrying": int(reasoning.get("retryingCount") or 0),
                    "oldestRequestAt": _text(reasoning.get("oldestRequestAt")),
                },
                "ai": ai_summary,
                "notifications": notification_summary,
                "engine": {
                    "status": _text(engine.get("status")),
                    "reasons": list(engine.get("reasons") or []),
                    "control": engine_control,
                    "deployments": _mapping(engine.get("queues")),
                    "workerLiveness": _mapping(engine.get("workerLiveness")),
                    "activeDeployment": _mapping(engine.get("activeDeployment")),
                    "deliveryDeployment": _mapping(engine.get("deliveryDeployment")),
                    "candidateDeployment": _mapping(engine.get("candidateDeployment")),
                },
            },
            "providers": providers,
            "storage": storage,
        }

    @staticmethod
    def _generic_health_state(payload: Mapping[str, object]) -> str:
        if not payload:
            return "unknown"
        value = _text(payload.get("status") or payload.get("state") or payload.get("health")).lower()
        if value in {"healthy", "ready", "active", "ok", "running", "promoted"}:
            return "healthy"
        if value in {"failed", "error", "critical", "unavailable", "blocked"}:
            return "critical"
        return "warning"
