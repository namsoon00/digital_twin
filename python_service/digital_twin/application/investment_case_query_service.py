"""Query persisted decisions through the canonical investment-case contract."""

from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Mapping, Tuple

from ..domain.investment_case import (
    INVESTMENT_CASE_VERSION,
    investment_case_history_item,
    investment_case_id,
    investment_case_snapshot,
    parse_investment_case_id,
)
from ..domain.investment_analysis import investment_decision_key
from ..domain.investment_flow import (
    FLOW_STAGE_LABELS,
    FLOW_STATE_LABELS,
    decision_flow_projection,
    item_dict,
    text,
)
from .investment_flow_query_service import InvestmentFlowQueryService


class InvestmentCaseQueryService:
    """Build user cases from MySQL-backed DecisionEpisodes only.

    TypeDB remains authoritative for semantic inference, but no TypeDB query is
    executed here.  Trace data is read lazily from identifiers frozen into the
    persisted episode.
    """

    def __init__(
        self,
        decision_episode_store,
        notification_job_store=None,
        hypothesis_lifecycle_store=None,
        monitor_store=None,
        evidence_repository=None,
        investment_domain_store=None,
        symbol_repository=None,
        subject_case_repository=None,
    ):
        self.decision_episode_store = decision_episode_store
        self.notification_job_store = notification_job_store
        self.monitor_store = monitor_store
        self.evidence_repository = evidence_repository
        self.investment_domain_store = investment_domain_store
        self.symbol_repository = symbol_repository
        self.subject_case_repository = subject_case_repository
        self.flow_service = InvestmentFlowQueryService(
            decision_episode_store=decision_episode_store,
            notification_job_store=notification_job_store,
            hypothesis_lifecycle_store=hypothesis_lifecycle_store,
        )

    def list_cases(
        self,
        account_id: str = "",
        symbol: str = "",
        limit: int = 100,
        include_operator: bool = False,
    ) -> Dict[str, object]:
        rows = self.flow_service._episodes(  # The flow adapter owns the optimized head-reader fallback.
            account_id,
            symbol,
            max(1, min(500, int(limit or 100))),
        )
        latest = []
        seen = set()
        for row in rows:
            payload = item_dict(row)
            key = (
                text(payload.get("accountId") or payload.get("account_id")) or "default",
                text(payload.get("symbol")).upper(),
            )
            if not key[1] or key in seen:
                continue
            seen.add(key)
            latest.append(self._with_canonical_subject(payload))

        episode_ids = [text(row.get("episodeId") or row.get("episode_id")) for row in latest]
        jobs_by_episode = self.flow_service._jobs_by_episode(episode_ids)
        snapshots = [
            investment_case_snapshot(
                row,
                jobs_by_episode.get(text(row.get("episodeId") or row.get("episode_id")), []),
            )
            for row in latest
        ]
        snapshots.sort(key=lambda item: (item.updated_at, item.episode_id), reverse=True)
        items = [item.to_dict(compact=True) for item in snapshots]
        subject_items = self._latest_subject_case_items(account_id, symbol, limit)
        for subject_item in subject_items:
            scope = (
                text(subject_item.get("accountId")) or "default",
                text(subject_item.get("symbol")).upper(),
            )
            existing = next((item for item in items if (
                text(item.get("accountId")) or "default",
                text(item.get("symbol")).upper(),
            ) == scope), None)
            if existing and text(existing.get("updatedAt")) >= text(subject_item.get("updatedAt")):
                continue
            if existing:
                items.remove(existing)
            items.append(subject_item)
        items.sort(
            key=lambda item: (text(item.get("updatedAt") or item.get("decidedAt")), text(item.get("symbol"))),
            reverse=True,
        )
        status_counts = Counter(text(item.get("status")) or "active" for item in items)
        readiness_counts = Counter(text(item.get("readinessState")) or "warning" for item in items)
        attention_counts = Counter(
            text(item_dict(item.get("attention")).get("state")) or "review"
            for item in items
        )
        phase_counts = Counter(text(item.get("phase")) or "case" for item in items)
        decision_state_counts = Counter(
            text(next((row for row in item.get("statusDimensions") or [] if row.get("id") == "decision"), {}).get("state")) or "warning"
            for item in items
        )
        data_state_counts = Counter(
            text(next((row for row in item.get("statusDimensions") or [] if row.get("id") == "data"), {}).get("state")) or "warning"
            for item in items
        )
        inference_state_counts = Counter(
            text(next((row for row in item.get("statusDimensions") or [] if row.get("id") == "inference"), {}).get("state")) or "warning"
            for item in items
        )
        ai_state_counts = Counter(
            text(next((row for row in item.get("statusDimensions") or [] if row.get("id") == "ai"), {}).get("state")) or "warning"
            for item in items
        )
        operator_view = {"loaded": False, "stages": [], "issues": []}
        if include_operator:
            flow_items = [
                decision_flow_projection(
                    row,
                    jobs_by_episode.get(text(row.get("episodeId") or row.get("episode_id")), []),
                )
                for row in latest
            ]
            operator_view = {"loaded": True, **self._operator_view(flow_items)}
        return {
            "version": INVESTMENT_CASE_VERSION,
            "status": "ok",
            "readOnly": True,
            "accountId": account_id,
            "symbol": symbol.upper(),
            "count": len(items),
            "summary": {
                "total": len(items),
                "ready": readiness_counts.get("pass", 0),
                "review": sum(readiness_counts.get(value, 0) for value in ("warning", "pending")),
                "blocked": sum(readiness_counts.get(value, 0) for value in ("blocked", "error")),
                "awaitingOutcome": sum(
                    1 for item in items
                    if item_dict(item.get("outcome")).get("state") == "pending"
                ),
                "statuses": dict(status_counts),
                "readiness": dict(readiness_counts),
                "phases": dict(phase_counts),
                # Compatibility for clients that still read the old validation summary.
                "validation": dict(readiness_counts),
                "attentionRequired": sum(
                    attention_counts.get(value, 0) for value in ("action", "review")
                ),
                "actionRequired": attention_counts.get("action", 0),
                "reviewRequired": attention_counts.get("review", 0),
                "blockedRequired": attention_counts.get("blocked", 0),
                "systemRequired": attention_counts.get("system", 0),
                "attention": dict(attention_counts),
                "dimensions": {
                    "decision": dict(decision_state_counts),
                    "data": dict(data_state_counts),
                    "inference": dict(inference_state_counts),
                    "ai": dict(ai_state_counts),
                },
            },
            "items": items,
            "operatorView": operator_view,
        }

    def _latest_subject_case_items(
        self,
        account_id: str,
        symbol: str,
        limit: int,
    ) -> List[Dict[str, object]]:
        latest = getattr(self.subject_case_repository, "latest", None)
        if not callable(latest):
            return []
        try:
            rows = list(latest(account_id, symbol, max(1, min(200, int(limit or 100) * 2))) or [])
        except Exception:  # noqa: BLE001 - the canonical DecisionEpisode read path remains available.
            return []
        result = []
        seen = set()
        for subject_case in rows:
            payload = subject_case.to_dict() if hasattr(subject_case, "to_dict") else item_dict(subject_case)
            scope = (
                text(payload.get("accountId")) or "default",
                text(payload.get("symbol")).upper(),
            )
            if not scope[1] or scope in seen:
                continue
            seen.add(scope)
            result.append(self._subject_case_item(payload))
        return result

    def _subject_case_item(self, payload: Mapping[str, object]) -> Dict[str, object]:
        case = item_dict(payload)
        synthesis = item_dict(case.get("synthesis"))
        candidate_set = item_dict(case.get("candidateSet"))
        final = item_dict(case.get("finalDecision"))
        judgment = item_dict(case.get("aiJudgment"))
        stage = text(case.get("stage")).upper() or "READY"
        symbol = text(case.get("symbol")).upper()
        action = text(
            final.get("action")
            or synthesis.get("investment_view_action")
            or synthesis.get("investmentViewAction")
            or synthesis.get("graph_candidate_action")
            or synthesis.get("graphCandidateAction")
            or "NO_ACTION"
        ).upper()
        blocked = stage == "BLOCKED" or bool(synthesis.get("judgement_blocked") or synthesis.get("judgementBlocked"))
        has_final = bool(final)
        has_candidate = bool(
            candidate_set.get("eligibleHypothesisIds")
            or synthesis.get("eligible_hypothesis_ids")
            or synthesis.get("eligibleHypothesisIds")
            or (action and action != "NO_ACTION")
        )
        if blocked:
            attention_state, attention_label = "blocked", "판단 보류"
        elif has_final and action in {"BUY", "ADD", "TRIM", "SELL", "AVOID"}:
            attention_state, attention_label = "action", "행동 검토"
        elif has_candidate:
            attention_state, attention_label = "review", "TypeDB 후보 검토"
        else:
            attention_state, attention_label = "observe", "관찰 유지"
        readiness = "blocked" if blocked else "pass" if has_final else "warning"
        next_checks = list(synthesis.get("next_checks") or synthesis.get("nextChecks") or [])
        missing_data = list(candidate_set.get("missingData") or [])
        hypotheses = [
            {
                "hypothesisId": text(item.get("hypothesisId") or item.get("hypothesis_id")),
                "label": text(item.get("label") or item.get("claim") or item.get("familyId")),
                "candidateAction": text(item.get("candidateAction") or item.get("candidate_action")).upper(),
                "supportingRuleIds": list(item.get("supportingRuleIds") or item.get("supporting_rule_ids") or []),
                "supportingEvidenceIds": list(item.get("supportingEvidenceIds") or item.get("supporting_evidence_ids") or []),
                "invalidationConditions": list(item.get("invalidationConditions") or item.get("invalidation_conditions") or []),
            }
            for item in candidate_set.get("hypotheses") or []
            if isinstance(item, Mapping)
        ][:8]
        selected_rule = text(synthesis.get("selected_rule_id") or synthesis.get("selectedRuleId"))
        candidate_label = action if action != "NO_ACTION" else "관계 관찰"
        headline = (
            "AI가 확정한 " + candidate_label + " 의견"
            if has_final
            else "TypeDB가 " + candidate_label + " 후보를 생성했으며 최종 행동은 아직 확정하지 않았습니다."
            if has_candidate
            else "TypeDB 추론은 완료됐지만 행동을 바꿀 가설은 성립하지 않았습니다."
        )
        scope_account = text(case.get("accountId")) or "default"
        canonical = self._with_canonical_subject({
            "accountId": scope_account,
            "symbol": symbol,
            "subjectName": symbol,
        })
        return {
            "version": INVESTMENT_CASE_VERSION,
            "detailType": "subject-decision-case",
            "caseId": "",
            "episodeId": "",
            "subjectCaseId": text(case.get("subjectCaseId")),
            "batchCaseId": text(case.get("batchCaseId")),
            "accountId": scope_account,
            "symbol": symbol,
            "name": text(canonical.get("subjectName") or canonical.get("name")) or symbol,
            "status": "active" if not blocked else "blocked",
            "caseStatus": "active" if not blocked else "blocked",
            "phase": "case",
            "phaseLabel": "최신 TypeDB 추론",
            "readinessState": readiness,
            "readinessLabel": "판단 가능" if has_final else "최종 판단 전" if not blocked else "판단 보류",
            "headline": headline,
            "nextAction": text(next_checks[0] if next_checks else "다음 사실 변경에서 같은 가설과 반대 근거를 다시 비교합니다."),
            "decidedAt": text(case.get("completedAt") or case.get("updatedAt")),
            "updatedAt": text(case.get("updatedAt") or case.get("createdAt")),
            "facts": {"dataState": text(synthesis.get("data_state") or synthesis.get("dataState")) or "partial"},
            "signals": {},
            "decision": {
                "action": action if has_final else "HOLD",
                "candidateAction": action,
                "reviewLevel": text(synthesis.get("review_level") or synthesis.get("reviewLevel")) or "check",
                "dataState": text(synthesis.get("data_state") or synthesis.get("dataState")) or "partial",
                "validationState": "ready" if has_final else "conditional",
                "state": readiness,
                "stateLabel": "AI 최종 판단" if has_final else "TypeDB 후보",
            },
            "outcome": {"state": "pending", "count": 0},
            "statusDimensions": [
                {"id": "inference", "label": "관계 추론", "state": "pass", "stateLabel": "완료", "reason": "현재 TypeDB 세대의 관계와 가설을 저장했습니다."},
                {"id": "ai", "label": "AI 판단", "state": "pass" if judgment else "pending", "stateLabel": "완료" if judgment else "대기", "reason": "AI 최종 의견이 저장됐습니다." if judgment else "TypeDB 후보와 알림 중요도 조건을 통과하면 AI가 최종 비교합니다."},
                {"id": "decision", "label": "현재 의견", "state": readiness, "stateLabel": "확정" if has_final else "후보", "reason": headline},
                {"id": "data", "label": "판단 자료", "state": "warning" if missing_data else "pass", "stateLabel": "일부 확인" if missing_data else "사용 가능", "reason": text(missing_data[0] if missing_data else "현재 가설 평가에 사용한 자료가 기록되어 있습니다.")},
            ],
            "attention": {
                "state": attention_state,
                "label": attention_label,
                "category": "investment" if attention_state in {"action", "observe"} else "review",
                "userActionable": attention_state == "action",
                "userReviewable": attention_state == "review",
                "userAttentionRequired": attention_state in {"action", "review"},
                "investmentAction": action,
                "issueCount": len(missing_data),
                "issues": [],
                "primaryIssue": {},
            },
            "explanation": {
                "primaryCause": {
                    "title": selected_rule or "TypeDB 경쟁 가설",
                    "summary": headline,
                    "effect": "최종 AI 판단 전에는 주문 행동으로 사용하지 않습니다." if not has_final else "검증된 최종 행동 의견입니다.",
                },
            },
            "subjectDecisionCase": {
                "stage": stage,
                "sourceAboxSnapshotId": text(case.get("sourceAboxSnapshotId")),
                "inferenceGenerationId": text(case.get("inferenceGenerationId")),
                "candidateFingerprint": text(candidate_set.get("fingerprint")),
                "selectedRuleId": selected_rule,
                "candidateAction": action,
                "allowedActions": list(candidate_set.get("allowedActions") or []),
                "blockedActions": list(candidate_set.get("blockedActions") or []),
                "missingData": missing_data[:8],
                "nextChecks": next_checks[:8],
                "hypotheses": hypotheses,
            },
        }

    def detail(self, case_id: str) -> Dict[str, object]:
        episode, snapshot = self._resolve(case_id)
        if not episode or not snapshot:
            return self._not_found(case_id)
        episode_id = snapshot.episode_id
        jobs = self.flow_service._jobs_by_episode([episode_id]).get(episode_id, [])
        snapshot = investment_case_snapshot(self._with_canonical_subject(item_dict(episode)), jobs)
        payload = snapshot.to_dict(compact=False)
        payload["liveComparison"] = self._live_comparison(snapshot)
        payload["evidence"] = self._evidence_detail(snapshot, payload.get("evidence") or {})
        payload["activity"] = self._activity_detail(episode, snapshot)
        payload.update({
            "status": "ok",
            "readOnly": True,
            "requestedKey": text(case_id),
            "resolvedFromLegacyKey": text(case_id) not in {snapshot.case_id, snapshot.episode_id},
            "canonicalUrl": f"/?tab=modeling&detail=investment-case&detailKey={snapshot.episode_id}",
            "availableViews": ["summary", "current", "evidence", "reasoning", "history"],
            "historyEndpoint": f"/api/investment-cases/{snapshot.case_id}/history",
            "traceEndpoint": f"/api/investment-cases/{snapshot.case_id}/trace",
        })
        return payload

    def history(self, case_id: str, limit: int = 30) -> Dict[str, object]:
        episode, head = self._resolve(case_id)
        if not episode or not head:
            return self._not_found(case_id)
        bounded_limit = max(1, min(200, int(limit or 30)))
        rows = self._history_rows(head.account_id, head.symbol, bounded_limit)
        if not rows and head.account_id == "default":
            rows = [
                row for row in self._history_rows("", head.symbol, bounded_limit)
                if investment_case_snapshot(row).account_id == "default"
            ]
        if not rows:
            rows = [episode]
        episode_ids = [text(item_dict(row).get("episodeId") or item_dict(row).get("episode_id")) for row in rows]
        jobs_by_episode = self.flow_service._jobs_by_episode(episode_ids)
        snapshots = [
            investment_case_snapshot(
                self._with_canonical_subject(item_dict(row)),
                jobs_by_episode.get(text(item_dict(row).get("episodeId") or item_dict(row).get("episode_id")), []),
            )
            for row in rows
        ]
        snapshots.sort(key=lambda item: (item.decided_at, item.episode_id), reverse=True)
        items = [
            investment_case_history_item(
                snapshot,
                snapshots[index + 1] if index + 1 < len(snapshots) else None,
            )
            for index, snapshot in enumerate(snapshots)
        ]
        feedback = self._feedback_by_episode([item.episode_id for item in snapshots])
        for item in items:
            item["activitySummary"] = self._activity_summary(feedback.get(text(item.get("episodeId")), {}))
        return {
            "version": INVESTMENT_CASE_VERSION,
            "status": "ok",
            "readOnly": True,
            "caseId": head.case_id,
            "accountId": head.account_id,
            "symbol": head.symbol,
            "count": len(items),
            "items": items,
        }

    def _with_canonical_subject(self, payload: Mapping[str, object]) -> Dict[str, object]:
        row = dict(payload or {})
        symbol = text(row.get("symbol")).upper()
        current = text(row.get("subjectName") or row.get("subject_name") or row.get("name"))
        if not symbol or (current and current.upper() != symbol):
            return row
        repository = self.symbol_repository
        if repository is None or not hasattr(repository, "get"):
            return row
        try:
            resolved = repository.get(symbol)
        except Exception:  # noqa: BLE001 - display metadata cannot block decision history.
            return row
        if isinstance(resolved, Mapping):
            name = text(resolved.get("name"))
        elif resolved is not None and hasattr(resolved, "to_dict"):
            name = text((resolved.to_dict() or {}).get("name"))
        else:
            name = text(getattr(resolved, "name", ""))
        if name and name.upper() != symbol:
            row["subjectName"] = name
            row["subject_name"] = name
            row["name"] = name
        return row

    @staticmethod
    def _number(value: object):
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def _latest_monitor_snapshot(self, account_id: str) -> Dict[str, object]:
        if not self.monitor_store:
            return {}
        try:
            if hasattr(self.monitor_store, "load_previous"):
                rows = self.monitor_store.load_previous()
            else:
                rows = dict(getattr(self.monitor_store, "payload", {}).get("previous") or {})
        except Exception:
            return {}
        account = text(account_id) or "default"
        return item_dict(rows.get(account) or rows.get(""))

    def _live_comparison(self, snapshot) -> Dict[str, object]:
        state = self._latest_monitor_snapshot(snapshot.account_id)
        symbol = text(snapshot.symbol).upper()
        live = item_dict(item_dict(state.get("positions")).get(symbol))
        scope = "holding"
        if not live:
            live = item_dict(item_dict(state.get("watchlist")).get(symbol))
            scope = "watchlist"
        if not live:
            return {
                "version": "investment-case-live-comparison-v1",
                "status": "unavailable",
                "label": "최신 상태 없음",
                "reason": "최신 모니터 스냅샷에서 이 종목을 찾지 못했습니다.",
                "rows": [],
            }
        fields = (
            ("currentPrice", "current_price", "현재가"),
            ("profitLossRate", "profit_loss_rate", "수익률"),
            ("quantity", "quantity", "보유 수량"),
            ("priceChangeRate", "change_rate", "당일 등락률"),
            ("ma5", "ma5", "5일 평균"),
            ("ma20", "ma20", "20일 평균"),
            ("ma60", "ma60", "60일 평균"),
            ("volume", "volume", "거래량"),
            ("volumeRatio", "volume_ratio", "평균 대비 거래량"),
            ("tradeStrength", "trade_strength", "체결강도"),
            ("bidAskImbalance", "bid_ask_imbalance", "호가 불균형"),
            ("foreignNetVolume", "foreign_net_volume", "외국인 순매수"),
            ("institutionNetVolume", "institution_net_volume", "기관 순매수"),
        )
        frozen = {
            text(item.get("field")): item
            for group in snapshot.current_state.get("groups") or []
            for item in group.get("items") or []
            if isinstance(item, Mapping)
        }
        rows = []
        for field, source_key, label in fields:
            current_value = live.get(source_key)
            if current_value in (None, ""):
                continue
            decision_value = item_dict(frozen.get(field)).get("value")
            current_number = self._number(current_value)
            decision_number = self._number(decision_value)
            delta = current_number - decision_number if current_number is not None and decision_number is not None else None
            delta_pct = (
                delta / abs(decision_number) * 100
                if delta is not None and decision_number not in (None, 0)
                else None
            )
            rows.append({
                "field": field,
                "label": label,
                "decisionValue": decision_value,
                "currentValue": current_value,
                "delta": round(delta, 6) if delta is not None else None,
                "deltaPct": round(delta_pct, 4) if delta_pct is not None else None,
                "source": text(live.get("quote_source") or live.get("source")),
                "sourceAsOf": text(live.get("source_as_of") or live.get("updated_at")),
                "freshnessStatus": text(live.get("freshness_status") or live.get("source_timestamp_state")),
            })
        changed_count = sum(1 for item in rows if item.get("delta") not in (None, 0))
        return {
            "version": "investment-case-live-comparison-v1",
            "status": "current" if rows else "unavailable",
            "label": "최신 상태와 비교" if rows else "비교할 최신 값 없음",
            "scope": scope,
            "asOf": text(state.get("generatedAt")) or text(live.get("updated_at")),
            "decisionAsOf": snapshot.decided_at,
            "source": text(live.get("quote_source") or live.get("source")),
            "freshnessStatus": text(live.get("freshness_status") or live.get("source_timestamp_state")),
            "changedCount": changed_count,
            "rows": rows,
        }

    def _evidence_detail(self, snapshot, evidence: Mapping[str, object]) -> Dict[str, object]:
        payload = dict(evidence or {})
        support_ids = list(payload.get("supportingIds") or [])
        counter_ids = list(payload.get("counterIds") or [])
        requested = list(dict.fromkeys(support_ids + counter_ids))
        resolved = {}
        if self.evidence_repository and requested:
            try:
                rows = self.evidence_repository.latest(
                    symbol=snapshot.symbol,
                    limit=max(100, min(500, len(requested) * 20)),
                    include_inactive=True,
                )
            except (TypeError, AttributeError):
                try:
                    rows = self.evidence_repository.latest(symbol=snapshot.symbol, limit=200)
                except Exception:
                    rows = []
            except Exception:
                rows = []
            for value in rows or []:
                row = item_dict(value)
                evidence_id = text(row.get("evidenceId") or row.get("evidence_id"))
                if evidence_id in requested:
                    resolved[evidence_id] = row
        records = []
        for evidence_id in requested:
            row = resolved.get(evidence_id, {})
            role = "support" if evidence_id in support_ids else "counter"
            source_as_of = text(row.get("publishedAt") or row.get("observedAt"))
            title = text(row.get("title") or row.get("analysisSummary") or evidence_id)
            url = text(row.get("url"))
            family_key = text(row.get("sourceOrigin") or row.get("source")) + "|" + (url or title)
            records.append({
                "id": evidence_id,
                "role": role,
                "roleLabel": "지지" if role == "support" else "반박",
                "useState": "used",
                "useStateLabel": "판단에 사용",
                "resolutionState": "resolved" if row else "identifier-only",
                "title": title,
                "summary": text(row.get("articleSummaryKo") or row.get("summary") or row.get("analysisSummary")),
                "kind": text(row.get("kind")) or "graph-evidence",
                "source": text(row.get("source")) or "DecisionEpisode",
                "sourcePublisher": text(row.get("sourcePublisher")),
                "sourceAsOf": source_as_of,
                "observedAt": text(row.get("observedAt")),
                "publishedAt": text(row.get("publishedAt")),
                "url": url,
                "polarity": text(row.get("polarity")),
                "dataState": text(row.get("dataState")) or "unknown",
                "validationState": text(row.get("validationState")) or "unknown",
                "lifecycleState": text(row.get("lifecycleState")) or "unknown",
                "excludedReason": text(row.get("excludedReason")),
                "sourceTrustState": text(row.get("sourceTrustState")),
                "materialityState": text(row.get("materialityState")),
                "independenceKey": family_key or evidence_id,
            })
        payload["records"] = records
        payload["resolvedCount"] = sum(1 for item in records if item["resolutionState"] == "resolved")
        payload["identifierOnlyCount"] = len(records) - int(payload["resolvedCount"])
        payload["independentFamilyCount"] = len({item["independenceKey"] for item in records})
        return payload

    def _feedback_by_episode(self, episode_ids: Iterable[str]) -> Dict[str, Dict[str, object]]:
        clean = [text(value) for value in episode_ids if text(value)]
        result = {value: {} for value in clean}
        if not self.investment_domain_store or not clean:
            return result
        try:
            execution = self.investment_domain_store.execution_feedback_for_decisions(clean)
        except Exception:
            execution = {}
        try:
            lifecycle = self.investment_domain_store.lifecycle_feedback_for_decisions(clean)
        except Exception:
            lifecycle = {}
        for episode_id in clean:
            result[episode_id] = {
                **item_dict(execution.get(episode_id)),
                **item_dict(lifecycle.get(episode_id)),
            }
        return result

    @staticmethod
    def _activity_summary(feedback: Mapping[str, object]) -> Dict[str, object]:
        row = dict(feedback or {})
        return {
            "actionPlanCount": len(row.get("actionPlans") or []),
            "executionCount": len(row.get("executionEpisodes") or []),
            "fillCount": len(row.get("fills") or []),
            "attributionCount": len(row.get("performanceAttributions") or []),
            "reviewCount": len(row.get("decisionReviews") or []),
        }

    def _activity_detail(self, episode: object, snapshot) -> Dict[str, object]:
        episode_payload = item_dict(episode)
        feedback = self._feedback_by_episode([snapshot.episode_id]).get(snapshot.episode_id, {})
        portfolio_id = text(episode_payload.get("portfolioId") or episode_payload.get("portfolio_id"))
        continuity = {}
        if self.investment_domain_store and hasattr(self.investment_domain_store, "decision_continuity_context"):
            try:
                continuity = self.investment_domain_store.decision_continuity_context(
                    portfolio_id,
                    snapshot.account_id if snapshot.account_id != "default" else text(episode_payload.get("accountId")),
                    snapshot.symbol,
                    snapshot.episode_id,
                )
            except Exception:
                continuity = {}
        merged = {**feedback, **item_dict(continuity)}
        timeline = []
        for item in merged.get("actionObservations") or []:
            row = item_dict(item)
            timeline.append({"type": "position-change", "at": text(row.get("observedAt")), "label": "보유 수량 변화", "detail": text(row.get("correspondence") or row.get("observedDirection")), "payload": row})
        for item in merged.get("fills") or []:
            row = item_dict(item)
            timeline.append({"type": "fill", "at": text(row.get("executedAt")), "label": "실제 체결", "detail": " ".join(value for value in [text(row.get("side")), text(row.get("quantity")), text(row.get("price"))] if value), "payload": row})
        for item in snapshot.outcome.get("items") or []:
            row = item_dict(item)
            timeline.append({"type": "outcome", "at": text(row.get("observedAt")), "label": "사후 결과", "detail": text(row.get("selectedHypothesisStatus")), "payload": row})
        for item in merged.get("performanceAttributions") or []:
            row = item_dict(item)
            timeline.append({"type": "attribution", "at": text(row.get("observedAt") or row.get("observed_at")), "label": "성과 귀속", "detail": text(row.get("horizonMinutes") or row.get("horizon_minutes")), "payload": row})
        for item in merged.get("decisionReviews") or []:
            row = item_dict(item)
            timeline.append({"type": "review", "at": text(row.get("reviewedAt") or row.get("reviewed_at")), "label": "판단 사후 검토", "detail": text(row.get("selectedHypothesisStatus") or row.get("selected_hypothesis_status")), "payload": row})
        timeline.sort(key=lambda item: text(item.get("at")), reverse=True)
        summary = self._activity_summary(merged)
        summary["actionObservationCount"] = len(merged.get("actionObservations") or [])
        summary["outcomeCount"] = int(snapshot.outcome.get("count") or 0)
        return {
            "version": "investment-case-activity-v1",
            "status": "observed" if timeline else "pending",
            "summary": summary,
            "currentPosition": item_dict(merged.get("currentPosition")),
            "actionObservations": [item_dict(item) for item in merged.get("actionObservations") or []],
            "actionPlans": [item_dict(item) for item in merged.get("actionPlans") or []],
            "executionEpisodes": [item_dict(item) for item in merged.get("executionEpisodes") or []],
            "fills": [item_dict(item) for item in merged.get("fills") or []],
            "performanceAttributions": [item_dict(item) for item in merged.get("performanceAttributions") or []],
            "decisionReviews": [item_dict(item) for item in merged.get("decisionReviews") or []],
            "timeline": timeline,
            "causalityClaimed": False,
            "causalityNote": "보유 수량 변화나 체결은 관측 사실이며, 이 판단을 따랐다는 인과관계는 사용자 확인 없이 확정하지 않습니다.",
        }

    def trace(self, case_id: str) -> Dict[str, object]:
        _episode, snapshot = self._resolve(case_id)
        if not snapshot:
            return self._not_found(case_id)
        payload = self.flow_service.detail(snapshot.episode_id)
        if payload.get("status") != "ok":
            return self._not_found(case_id)
        payload["caseExplanation"] = dict(snapshot.explanation)
        payload["statusDimensions"] = [dict(item) for item in snapshot.status_dimensions]
        payload["evidence"] = dict(snapshot.evidence)
        return {
            "version": INVESTMENT_CASE_VERSION,
            "status": "ok",
            "readOnly": True,
            "audience": "operator",
            "caseId": snapshot.case_id,
            "episodeId": snapshot.episode_id,
            "accountId": snapshot.account_id,
            "symbol": snapshot.symbol,
            "trace": payload,
        }

    def _resolve(self, case_id: str) -> Tuple[object, object]:
        key = text(case_id)
        if not key:
            return None, None
        if key.startswith("decision-episode:") or key.startswith("episode:"):
            episode = self.flow_service._episode(key)
            return self._resolved_pair(episode)
        identity = parse_investment_case_id(key)
        if identity:
            rows = self.flow_service._episodes(identity["accountId"], identity["symbol"], 1)
            for row in rows:
                if investment_case_snapshot(row).case_id == key:
                    return self._resolved_pair(row)

            # "default" is the public account label, while older persisted rows
            # may still contain an empty account id. Resolve by the canonical id
            # instead of making a stale link fail after a read-model migration.
            rows = self.flow_service._episodes("", identity["symbol"], 500)
            for row in rows:
                if investment_case_snapshot(row).case_id == key:
                    return self._resolved_pair(row)

        # Investment-flow links existed before the stable case contract. Keep
        # those URLs usable so an open mobile tab survives an app deployment.
        if key.startswith("flow:"):
            for row in self.flow_service._episodes("", "", 500):
                payload = item_dict(row)
                if text(payload.get("flowId") or payload.get("flow_id")) == key:
                    return self._resolved_pair(row)
        if key.startswith("decision:"):
            # Early action-list links were hashes over account, symbol, and an
            # optional episode id. Resolve them against persisted heads so the
            # link remains usable after the mutable action queue is refreshed.
            for row in self.flow_service._episodes("", "", 500):
                snapshot = investment_case_snapshot(row)
                candidates = {
                    investment_decision_key(snapshot.account_id, snapshot.symbol, snapshot.episode_id),
                    investment_decision_key(snapshot.account_id, snapshot.symbol, ""),
                }
                if key in candidates:
                    return self._resolved_pair(row)
        return None, None

    def _resolved_pair(self, row: object) -> Tuple[object, object]:
        if not row:
            return None, None
        snapshot = investment_case_snapshot(row)
        episode = self.flow_service._episode(snapshot.episode_id) or row
        return episode, investment_case_snapshot(episode)

    def _history_rows(self, account_id: str, symbol: str, limit: int) -> List[object]:
        reader = getattr(self.decision_episode_store, "list", None)
        if not callable(reader):
            return []
        try:
            return list(reader(account_id=account_id, symbol=symbol, limit=limit) or [])
        except TypeError:
            return list(reader(account_id, symbol, limit) or [])

    def _operator_view(self, flow_items: Iterable[Dict[str, object]]) -> Dict[str, object]:
        stage_counts = defaultdict(Counter)
        issues = []
        for item in flow_items:
            for stage in item.get("stages") or []:
                stage_counts[text(stage.get("id"))][text(stage.get("state")) or "warning"] += 1
            if item.get("readinessState") != "pass":
                issues.append({
                    "caseId": investment_case_id(item.get("accountId"), item.get("symbol")),
                    "episodeId": item.get("episodeId"),
                    "accountId": item.get("accountId"),
                    "symbol": item.get("symbol"),
                    "name": item.get("name"),
                    "stage": item.get("blockingStage"),
                    "stageLabel": item.get("blockingStageLabel"),
                    "state": item.get("readinessState"),
                    "reason": item.get("blockingReason"),
                    "nextAction": item.get("nextAction"),
                    "updatedAt": item.get("updatedAt"),
                })
        stages = []
        for stage_id, label in FLOW_STAGE_LABELS.items():
            counts = stage_counts.get(stage_id, Counter())
            state = "error" if counts.get("error") else (
                "blocked" if counts.get("blocked") else (
                    "warning" if counts.get("warning") else (
                        "pending" if counts.get("pending") else "pass"
                    )
                )
            )
            stages.append({
                "id": stage_id,
                "label": label,
                "state": state,
                "stateLabel": FLOW_STATE_LABELS[state],
                "affectedCount": sum(counts.get(value, 0) for value in ("error", "blocked", "warning", "pending")),
                "counts": dict(counts),
            })
        return {"stages": stages, "issues": issues}

    def _not_found(self, case_id: str) -> Dict[str, object]:
        return {
            "version": INVESTMENT_CASE_VERSION,
            "status": "not-found",
            "readOnly": True,
            "caseId": text(case_id),
            "error": "투자 케이스가 갱신되었거나 현재 목록에 없습니다. 목록을 새로고침해 주세요.",
        }
