from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, List

from .alert_formatting import compact_number, money, price_money, signed_number, signed_pct
from .data_freshness import data_freshness_required, freshness_from_position, freshness_record
from .external_signal_deltas import external_signals_with_deltas
from .market_data import number
from .message_types import (
    DEFAULT_ALERT_RULES,
    DEFAULT_ALERT_THRESHOLDS,
    DEFAULT_CADENCE,
    INVESTMENT_INSIGHT,
    MIN_CADENCE_MINUTES,
    ONTOLOGY_INFERENCE_MISSING,
    WATCHLIST_ONTOLOGY_SIGNAL,
)
from .ontology_inference_context import inferencebox_from_snapshot, relation_contexts_from_snapshot
from .ontology_insights import build_investment_insight_events, relation_news_event_key_suffix, split_operational_and_investment_events
from .ontology_relation_reasoning import decision_action_group_for_label, relation_rule_context_summary_lines, relation_thresholds_from_settings
from .parsing import parse_assignments
from .portfolio import AccountSnapshot, AlertEvent, Position, status_has_account_data_failure
from .portfolio_calculations import DEFAULT_FX_RATES, fx_rates_with_external_signals, value_in_base
from .repositories import MonitorStateRepository
from .strategy import StrategyModel, decisions_for_positions
from .notification_ai_context import is_graph_backed_relation_context
from .strategy_alerts import StrategyAlertMixin
from .external_signal_alerts import ExternalSignalAlertMixin
from .monitoring_position_context import MonitoringPositionContextMixin
from .monitoring_sample_data import MonitoringSampleDataMixin


DEFAULT_THRESHOLDS = DEFAULT_ALERT_THRESHOLDS


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def ontology_quality_event_metadata(snapshot: AccountSnapshot, min_score: float) -> Dict[str, object]:
    metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
    ontology = metadata.get("ontology") if isinstance(metadata.get("ontology"), dict) else {}
    projection = ontology.get("neo4j") if isinstance(ontology.get("neo4j"), dict) else ontology.get("projection")
    if not isinstance(projection, dict) or "qualityScore" not in projection:
        return {}
    if projection.get("qualityScore") in (None, ""):
        return {}
    score = number(projection.get("qualityScore"))
    status = "passed" if score >= float(min_score or 0) else "limited"
    return {
        "score": round(score, 2),
        "minScore": round(float(min_score or 0), 2),
        "status": status,
        "qualitySampleId": str(projection.get("qualitySampleId") or ""),
        "source": "ontologyProjection",
        "reason": "온톨로지 품질 점수가 알림 판단 기준 이상입니다." if status == "passed" else "온톨로지 품질 점수가 알림 판단 기준보다 낮아 판단 강도를 제한합니다.",
    }


def ontology_inference_event_metadata(snapshot: AccountSnapshot) -> Dict[str, object]:
    metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
    ontology = metadata.get("ontology") if isinstance(metadata.get("ontology"), dict) else {}
    projection = ontology.get("neo4j") if isinstance(ontology.get("neo4j"), dict) else ontology.get("projection")
    if not isinstance(projection, dict):
        return {}
    inference = projection.get("inferenceBox") if isinstance(projection.get("inferenceBox"), dict) else {}
    if not inference:
        return {}
    relations = [
        {
            "type": str(item.get("type") or ""),
            "ruleId": str(item.get("ruleId") or ""),
            "label": str(item.get("label") or item.get("targetLabel") or ""),
            "polarity": str(item.get("polarity") or ""),
            "riskImpact": item.get("riskImpact"),
            "supportImpact": item.get("supportImpact"),
            "nativeNeo4jReasoned": bool(item.get("nativeNeo4jReasoned")),
        }
        for item in (inference.get("relations") or [])[:8]
        if isinstance(item, dict)
    ]
    traces = [
        {
            "ruleId": str(item.get("ruleId") or ""),
            "label": str(item.get("label") or ""),
            "confidence": item.get("confidence"),
            "matchedConditionIds": list(item.get("matchedConditionIds") or [])[:8] if isinstance(item.get("matchedConditionIds"), list) else [],
            "nativeNeo4jReasoned": bool(item.get("nativeNeo4jReasoned")),
        }
        for item in (inference.get("traces") or [])[:8]
        if isinstance(item, dict)
    ]
    rulebox_execution = projection.get("ruleboxExecution") if isinstance(projection.get("ruleboxExecution"), dict) else {}
    return {
        "source": "neo4jInferenceBox",
        "status": str(inference.get("status") or projection.get("status") or ""),
        "projectionMode": str(projection.get("projectionMode") or ""),
        "ruleboxExecutionStatus": str(rulebox_execution.get("status") or ""),
        "neo4jNativeReasoningUsed": bool(inference.get("neo4jNativeReasoningUsed")),
        "entityCount": int(number(inference.get("entityCount")) or 0),
        "relationCount": int(number(inference.get("relationCount")) or 0),
        "traceCount": int(number(inference.get("traceCount")) or 0),
        "nativeRelationCount": int(number(inference.get("nativeRelationCount")) or 0),
        "relations": relations,
        "traces": traces,
    }


class RealtimeMonitor(MonitoringSampleDataMixin, MonitoringPositionContextMixin, StrategyAlertMixin, ExternalSignalAlertMixin):
    def __init__(self, settings: Dict[str, str] = None):
        settings = settings or {}
        self.settings = dict(settings)
        self.rules = parse_assignments(settings.get("alertRules", ""), DEFAULT_ALERT_RULES)
        self.thresholds = parse_assignments(settings.get("alertThresholds", ""), DEFAULT_THRESHOLDS)
        self.relation_thresholds = relation_thresholds_from_settings(settings)
        self.cadence = parse_assignments(settings.get("alertCadenceMinutes", ""), DEFAULT_CADENCE)
        self.fx_rates = {
            str(key).upper(): float(value or 0)
            for key, value in parse_assignments(settings.get("fxRates", ""), DEFAULT_FX_RATES).items()
        }
        self.base_fx_rates = dict(self.fx_rates)
        self.strategy_model = StrategyModel(settings)

    def use_external_fx_rates(self, external_signals: Dict[str, object] = None) -> None:
        self.fx_rates = fx_rates_with_external_signals(self.base_fx_rates, external_signals)

    def enabled(self, rule: str) -> bool:
        return self.rules.get(rule, 1) != 0

    def rule_cadence_minutes(self, rule: str) -> int:
        value = int(self.cadence.get(rule, DEFAULT_CADENCE.get(rule, MIN_CADENCE_MINUTES)) or 0)
        return max(MIN_CADENCE_MINUTES, value)

    def dispatch_cadence_minutes(self, event: AlertEvent) -> int:
        if event.rule == INVESTMENT_INSIGHT:
            raw = self.settings.get("notificationCooldownMinutes")
            if str(raw or "").strip():
                try:
                    value = int(float(str(raw).strip()))
                except ValueError:
                    value = self.rule_cadence_minutes(event.rule)
                return max(MIN_CADENCE_MINUTES, value)
        return self.rule_cadence_minutes(event.rule)

    def dispatch_cadence_key(self, event: AlertEvent) -> str:
        if event.rule == INVESTMENT_INSIGHT:
            insight = event.metadata.get("ontologyInsight") if isinstance(event.metadata, dict) else {}
            if isinstance(insight, dict) and str(insight.get("cadenceKey") or "").strip():
                return str(insight.get("cadenceKey"))
            if str(event.key or "").strip():
                return ":".join(["cadence", "python", event.account_id, event.rule, event.key])
        return event.cadence_key()

    def ontology_quality_min_score(self) -> float:
        raw = self.settings.get("notificationOntologyQualityMinScore") or self.settings.get("ontologyNotificationQualityMinScore") or 55
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 55.0

    def criteria(self, setting: str, detected: str = "") -> List[str]:
        lines = []
        if str(setting or "").strip():
            lines.append("설정: " + str(setting).strip())
        if str(detected or "").strip():
            lines.append("감지: " + str(detected).strip())
        return lines

    def threshold_text(self, key: str, suffix: str = "") -> str:
        return compact_number(float(self.thresholds.get(key, DEFAULT_THRESHOLDS.get(key, 0)) or 0)) + suffix

    def model_score_phrase(self, side: str, score: float) -> str:
        value = round(float(score or 0), 1)
        if side == "buy":
            if value >= 85:
                label = "강한 매수 후보"
            elif value >= 74:
                label = "매수 후보"
            elif value >= 60:
                label = "관찰 후보"
            else:
                label = "매수 기준 미달"
        else:
            if value >= 85:
                label = "강한 매도 압력"
            elif value >= 72:
                label = "분할매도 압력"
            elif value >= 60:
                label = "리스크 관찰"
            else:
                label = "매도 기준 미달"
        return label + " (" + compact_number(value) + "점)"

    def decision_score_phrase(self, label: object, score: object) -> str:
        text = str(label or "-").strip() or "-"
        return text + " (" + compact_number(float(score or 0)) + "점)"

    def decision_action_group(self, label: object) -> str:
        return decision_action_group_for_label(label)

    def enabled_signal_events(self, events: List[AlertEvent]) -> List[AlertEvent]:
        return [event for event in events or [] if self.enabled(event.rule)]

    def meaningful_decision_change(self, current_decision: Dict[str, object], previous_decision: Dict[str, object], pressure_delta: float) -> bool:
        current_label = str(current_decision.get("decision") or "").strip()
        previous_label = str(previous_decision.get("decision") or "").strip()
        if not current_label or not previous_label or current_label == previous_label:
            return False
        if self.decision_action_group(current_label) != self.decision_action_group(previous_label):
            return True
        label_buffer = float(self.thresholds.get("monitorDecisionLabelBuffer", 5) or 0)
        return abs(float(pressure_delta or 0)) >= label_buffer

    def events_for_snapshot(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        self.use_external_fx_rates(snapshot.external_signals)
        raw_events: List[AlertEvent] = []
        snapshot = self.snapshot_with_external_signal_deltas(snapshot, previous or {})
        signal_snapshot = snapshot
        decision_snapshot = self.snapshot_with_strategy_scores(snapshot)
        has_account_data = decision_snapshot.has_live_account_data()
        inference_missing_events: List[AlertEvent] = []
        raw_events.extend(self.connection_events(decision_snapshot, previous))
        raw_events.extend(self.heartbeat_events(decision_snapshot))
        if has_account_data:
            inference_missing_events = self.ontology_inference_missing_events(decision_snapshot)
            raw_events.extend(inference_missing_events)
            if not inference_missing_events:
                raw_events.extend(self.ontology_signal_events(signal_snapshot))
        raw_events.extend(self.external_signal_events(signal_snapshot, previous or {}))
        if has_account_data and not inference_missing_events:
            raw_events.extend(self.holding_timing_events(decision_snapshot))
        raw_events = self.attach_data_freshness(decision_snapshot, raw_events)
        system_events, signal_events = split_operational_and_investment_events(raw_events)
        signal_events = self.enabled_signal_events(signal_events)
        if inference_missing_events:
            signal_events = []
        events = [*system_events, *build_investment_insight_events(decision_snapshot, signal_events)]
        return [event for event in self.stamp_events(decision_snapshot, events) if self.enabled(event.rule)]

    def snapshot_with_external_signal_deltas(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> AccountSnapshot:
        previous_signals = previous.get("externalSignals") if isinstance(previous, dict) and isinstance(previous.get("externalSignals"), dict) else {}
        if not previous_signals or not isinstance(snapshot.external_signals, dict):
            return snapshot
        snapshot.external_signals = external_signals_with_deltas(snapshot.external_signals, previous_signals)
        return snapshot

    def type_check_events_for_snapshot(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        self.use_external_fx_rates(snapshot.external_signals)
        events: List[AlertEvent] = []
        snapshot = self.snapshot_with_strategy_scores(snapshot)
        events.extend(self.connection_events(snapshot, {"status": "이전 연결 상태"}))
        events.extend(self.heartbeat_events(snapshot))
        inference_missing_events = self.only_rule(ONTOLOGY_INFERENCE_MISSING, self.ontology_inference_missing_events(snapshot))
        events.extend(inference_missing_events)
        watchlist_snapshot = self.snapshot_with_sample_watchlist(snapshot)
        ontology_watchlist_snapshot = self.snapshot_with_sample_watchlist_ontology_signal(watchlist_snapshot)
        events.extend(self.only_rule(WATCHLIST_ONTOLOGY_SIGNAL, self.ontology_signal_events(ontology_watchlist_snapshot)))

        external_snapshot = self.snapshot_with_sample_external_signals(snapshot)
        events.extend(self.external_signal_events(external_snapshot, {}))
        timing_events = self.holding_timing_events(snapshot)
        if not timing_events and snapshot.decisions:
            timing_snapshot = replace(snapshot, decisions=[
                replace(snapshot.decisions[0], tone="caution", decision=snapshot.decisions[0].decision or "조건부 보유"),
                *snapshot.decisions[1:],
            ])
            timing_events = self.holding_timing_events(timing_snapshot)
        events.extend(self.only_rule("holdingTiming", timing_events))
        events = self.attach_data_freshness(snapshot, events)
        investment_insights = build_investment_insight_events(snapshot, self.enabled_signal_events(events))
        events.extend(self.only_rule(INVESTMENT_INSIGHT, investment_insights))
        return self.unique_rules([event for event in self.stamp_events(snapshot, events) if self.enabled(event.rule)])

    def attach_data_freshness(self, snapshot: AccountSnapshot, events: List[AlertEvent]) -> List[AlertEvent]:
        state = snapshot.to_monitor_state()
        positions: Dict[str, Dict[str, object]] = {}
        for group_key in ["positions", "watchlist"]:
            group = state.get(group_key) if isinstance(state.get(group_key), dict) else {}
            for symbol, item in group.items():
                if isinstance(item, dict):
                    positions[str(symbol or "").upper()] = item
        for event in events:
            event.metadata = dict(event.metadata or {})
            event.metadata.setdefault("dataFreshnessRequired", data_freshness_required(event.rule))
            if event.metadata.get("dataFreshness"):
                continue
            symbol = str(event.symbol or "").upper()
            position = positions.get(symbol)
            if position:
                event.metadata["dataFreshness"] = freshness_from_position(position, event.rule, self.settings)
            elif data_freshness_required(event.rule):
                event.metadata["dataFreshness"] = freshness_record(
                    "accountSnapshot",
                    event.rule,
                    settings=self.settings,
                    source_fetched_at=snapshot.generated_at,
                    data_quality=snapshot.mode,
                )
        return events

    def stamp_events(self, snapshot: AccountSnapshot, events: List[AlertEvent]) -> List[AlertEvent]:
        generated_at = str(snapshot.generated_at or "").strip()
        formula_metadata = self.notification_formula_metadata()
        ontology_quality = ontology_quality_event_metadata(snapshot, self.ontology_quality_min_score())
        ontology_inference = ontology_inference_event_metadata(snapshot)
        for event in events:
            if generated_at:
                event.generated_at = generated_at
            if formula_metadata:
                event.metadata.update({key: value for key, value in formula_metadata.items() if key not in event.metadata})
            if ontology_quality:
                event.metadata.setdefault("ontologyQuality", ontology_quality)
            if ontology_inference:
                event.metadata.setdefault("ontologyInference", ontology_inference)
        return events

    def snapshot_with_strategy_scores(self, snapshot: AccountSnapshot) -> AccountSnapshot:
        if not snapshot.has_live_account_data():
            return snapshot
        inference_contexts = relation_contexts_from_snapshot(snapshot, getattr(self.strategy_model, "settings", {}) if self.strategy_model else {})
        snapshot.decisions = decisions_for_positions(
            snapshot.positions,
            snapshot.portfolio,
            self.strategy_model,
            external_signals=snapshot.external_signals,
            relation_contexts_by_symbol=inference_contexts,
            require_inference_context=True,
        )
        return snapshot

    def notification_formula_metadata(self) -> Dict[str, object]:
        keys = ["notificationScoreFormula"]
        return {
            key: str(self.settings.get(key) or "").strip()
            for key in keys
            if str(self.settings.get(key) or "").strip()
        }

    def only_rule(self, rule: str, events: List[AlertEvent]) -> List[AlertEvent]:
        return [event for event in events if event.rule == rule]

    def unique_rules(self, events: List[AlertEvent]) -> List[AlertEvent]:
        seen = set()
        unique: List[AlertEvent] = []
        for event in events:
            if event.rule in seen:
                continue
            seen.add(event.rule)
            unique.append(event)
        return unique

    def toss_diagnostics(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        metadata = dict(getattr(snapshot, "metadata", {}) or {})
        toss = metadata.get("toss") if isinstance(metadata.get("toss"), dict) else {}
        return dict(toss or {})

    def toss_failure_stage(self, snapshot: AccountSnapshot) -> str:
        toss = self.toss_diagnostics(snapshot)
        stage_failures = toss.get("stageFailures") if isinstance(toss.get("stageFailures"), dict) else {}
        if stage_failures:
            return str(next(reversed(stage_failures.keys())) or "")
        status = str(snapshot.status or "")
        marker = "Toss "
        suffix = " 단계 실패"
        if marker in status and suffix in status:
            return status.split(marker, 1)[1].split(suffix, 1)[0].strip()
        return ""

    def connection_failure_streak(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> int:
        if snapshot.mode == "live" and not status_has_account_data_failure(snapshot.status):
            return 0
        previous_metadata = dict((previous or {}).get("metadata") or {})
        previous_streak = int(float(previous_metadata.get("connectionFailureStreak") or 0))
        previous_failed = (
            str((previous or {}).get("mode") or "").strip().lower() != "live"
            or status_has_account_data_failure((previous or {}).get("status"))
        )
        return (previous_streak if previous_failed else 0) + 1

    def set_connection_failure_streak(self, snapshot: AccountSnapshot, streak: int) -> None:
        metadata = dict(getattr(snapshot, "metadata", {}) or {})
        metadata["connectionFailureStreak"] = int(streak or 0)
        snapshot.metadata = metadata

    def connection_events(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        failure_streak = self.connection_failure_streak(snapshot, previous)
        self.set_connection_failure_streak(snapshot, failure_streak)
        if snapshot.mode != "live":
            repeated = failure_streak >= 2
            severity = "ALERT" if repeated else "WATCH"
            stage = self.toss_failure_stage(snapshot) or "-"
            toss = self.toss_diagnostics(snapshot)
            auth_refreshes = int(float(toss.get("authRefreshes") or 0))
            status_line = "연속 인증 실패" if repeated else "일시 인증 실패"
            retry_line = "재시도 access token 재발급 " + str(auth_refreshes) + "회" if auth_refreshes else ""
            lines = [
                "상태 " + status_line,
                "연속 실패 " + str(failure_streak) + "회",
                "실패 단계 " + stage,
            ]
            if retry_line:
                lines.append(retry_line)
            lines.append(snapshot.status or snapshot.mode)
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                severity,
                "monitorConnection",
                ":".join([snapshot.account_id, "connection", snapshot.mode, "repeated" if repeated else "single", snapshot.status]),
                "연결 상태",
                lines,
                criteria=self.criteria(
                    "토스 연결 모드가 live가 아니며 " + ("2회 이상 연속 실패할 때 주의로 보냅니다" if repeated else "1회성 실패는 관찰로 보냅니다"),
                    "연속 실패 " + str(failure_streak) + "회, stage=" + stage + ", mode=" + str(snapshot.mode or "-") + ", status=" + str(snapshot.status or "-"),
                ),
                metadata={
                    "connectionFailureStreak": failure_streak,
                    "tossFailureStage": stage,
                    "tossAuthRefreshes": auth_refreshes,
                },
            ))
        previous_status = previous.get("status") if previous else ""
        if previous_status and previous_status != snapshot.status:
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "WATCH",
                "monitorConnection",
                ":".join([snapshot.account_id, "connection-change", snapshot.status]),
                "연결 상태 변화",
                ["이전 " + str(previous_status), "현재 " + snapshot.status],
                criteria=self.criteria(
                    "직전 스냅샷의 토스 연결 상태와 현재 상태가 다를 때",
                    "이전 " + str(previous_status) + ", 현재 " + snapshot.status,
                ),
            ))
        return events

    def heartbeat_events(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        return [AlertEvent(
            snapshot.account_id,
            snapshot.account_label,
            "INFO",
            "monitorHeartbeat",
            ":".join([snapshot.account_id, "heartbeat", snapshot.generated_at]),
            "실시간 모니터링",
            [
                "모니터링 정상 작동",
                "상태 " + (snapshot.status or snapshot.mode),
                "보유 " + str(len([item for item in snapshot.positions if not item.is_cash()])) + "개",
                "평가 " + money(snapshot.portfolio.invested, "KRW"),
            ],
            criteria=self.criteria(
                "실시간 모니터링 워커 생존 확인 주기",
                "상태 " + (snapshot.status or snapshot.mode) + ", 보유 " + str(len([item for item in snapshot.positions if not item.is_cash()])) + "개",
            ),
        )]

    def ontology_inference_missing_events(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        positions = [item for item in snapshot.positions or [] if getattr(item, "symbol", "") and not item.is_cash()]
        if not snapshot.has_live_account_data() or not positions:
            return []
        inference_contexts = relation_contexts_from_snapshot(
            snapshot,
            getattr(self.strategy_model, "settings", {}) if self.strategy_model else {},
        )
        if inference_contexts:
            return []

        reason_code, reason, inference_status = self.ontology_inference_missing_reason(snapshot)
        relation_count = int(number(inference_status.get("relationCount")) or 0)
        trace_count = int(number(inference_status.get("traceCount")) or 0)
        entity_count = int(number(inference_status.get("entityCount")) or 0)
        native_used = bool(inference_status.get("neo4jNativeReasoningUsed"))
        status_text = str(inference_status.get("status") or "missing").strip() or "missing"
        lines = [
            "상태 온톨로지 추론 결과 없음",
            "판단 차단 매수·매도 판단은 생성하지 않았습니다",
            "원인 " + reason,
            "추론 상태 status=" + status_text + ", relations=" + str(relation_count) + ", traces=" + str(trace_count),
            "보유 " + str(len(positions)) + "개",
            "확인 행동 Neo4j 연결, RuleBox 저장 상태, 온톨로지 추론 워커 점검",
        ]
        inference_metadata = ontology_inference_event_metadata(snapshot)
        if not inference_metadata:
            inference_metadata = {
                "source": "neo4jInferenceBox",
                "status": status_text,
                "projectionMode": str(inference_status.get("projectionMode") or ""),
                "ruleboxExecutionStatus": str(inference_status.get("ruleboxExecutionStatus") or ""),
                "neo4jNativeReasoningUsed": native_used,
                "entityCount": entity_count,
                "relationCount": relation_count,
                "traceCount": trace_count,
                "nativeRelationCount": int(number(inference_status.get("nativeRelationCount")) or 0),
                "relations": [],
                "traces": [],
            }
        inference_metadata = dict(inference_metadata)
        inference_metadata.update({
            "missing": True,
            "missingReasonCode": reason_code,
            "missingReason": reason,
        })
        return [AlertEvent(
            snapshot.account_id,
            snapshot.account_label,
            "WATCH",
            ONTOLOGY_INFERENCE_MISSING,
            ":".join([snapshot.account_id, "ontology-inference-missing", reason_code]),
            "온톨로지 추론 상태",
            lines,
            criteria=self.criteria(
                "실계좌 데이터와 보유 종목이 있는데 Neo4j InferenceBox 관계 추론을 사용할 수 없을 때",
                reason + ", 보유 " + str(len(positions)) + "개, relationCount=" + str(relation_count) + ", traceCount=" + str(trace_count),
            ),
            metadata={
                "blockedInvestmentJudgment": True,
                "missingInferenceBox": True,
                "missingInferenceReasonCode": reason_code,
                "missingInferenceReason": reason,
                "positionCount": len(positions),
                "ontologyInference": inference_metadata,
            },
        )]

    def ontology_inference_missing_reason(self, snapshot: AccountSnapshot):
        metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
        ontology = metadata.get("ontology") if isinstance(metadata.get("ontology"), dict) else {}
        projection = ontology.get("neo4j") if isinstance(ontology.get("neo4j"), dict) else ontology.get("projection")
        if not isinstance(projection, dict) or not projection:
            return "missingProjection", "Neo4j 온톨로지 투영 결과가 없습니다", {
                "status": "missing",
                "projectionMode": "",
                "ruleboxExecutionStatus": "",
            }
        inference = inferencebox_from_snapshot(snapshot)
        rulebox_execution = projection.get("ruleboxExecution") if isinstance(projection.get("ruleboxExecution"), dict) else {}
        status = str((inference or {}).get("status") or projection.get("status") or "").strip()
        common = {
            "status": status or ("empty" if isinstance(inference, dict) else "missing"),
            "projectionMode": str(projection.get("projectionMode") or ""),
            "ruleboxExecutionStatus": str(rulebox_execution.get("status") or ""),
            "neo4jNativeReasoningUsed": bool((inference or {}).get("neo4jNativeReasoningUsed")) if isinstance(inference, dict) else False,
            "entityCount": int(number((inference or {}).get("entityCount")) or 0) if isinstance(inference, dict) else 0,
            "relationCount": int(number((inference or {}).get("relationCount")) or 0) if isinstance(inference, dict) else 0,
            "traceCount": int(number((inference or {}).get("traceCount")) or 0) if isinstance(inference, dict) else 0,
            "nativeRelationCount": int(number((inference or {}).get("nativeRelationCount")) or 0) if isinstance(inference, dict) else 0,
        }
        if not inference:
            return "missingInferenceBox", "Neo4j InferenceBox 응답이 없습니다", common
        if status and status.lower() not in {"ok", "partial"}:
            return "inferenceBoxStatusBlocked", "InferenceBox 상태가 " + status + "입니다", common
        relations = inference.get("relations") if isinstance(inference.get("relations"), list) else []
        traces = inference.get("traces") if isinstance(inference.get("traces"), list) else []
        if not bool(inference.get("neo4jNativeReasoningUsed")) and not relations:
            return "nativeReasoningMissing", "Neo4j 네이티브 추론 관계가 아직 없습니다", common
        if not relations and not traces:
            return "emptyInferenceBox", "InferenceBox 관계와 근거가 0개입니다", common
        return "positionInferenceMissing", "보유 종목과 연결된 InferenceBox 관계가 없습니다", common

    def holding_timing_events(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        positions = {item.symbol.upper(): item.to_dict() for item in snapshot.positions if not item.is_cash()}
        loss_threshold = float(self.relation_thresholds.get("lossRateLow", -8.0) or -8.0)
        loss_buffer = abs(float(self.relation_thresholds.get("lossRateBufferPct", 1.0) or 0.0))
        forced_loss_threshold = loss_threshold - loss_buffer
        for item in snapshot.decisions:
            position = positions.get(item.symbol.upper()) or item.to_dict()
            decision_phrase = self.decision_score_phrase(item.decision, item.exit_pressure)
            decision_state = item.to_dict()
            relation_context = self.relation_context_from_decision(decision_state)
            if not is_graph_backed_relation_context(relation_context):
                continue
            relation_score = float((relation_context.get("decision") or {}).get("score") or relation_context.get("signalStrength") or item.exit_pressure or 0)
            if item.tone not in {"danger", "caution"} and item.profit_loss_rate > forced_loss_threshold and relation_score < 55:
                continue
            prompt_context = self.prompt_context_from_decision(decision_state)
            relation_lines = self.relation_context_lines(decision_state)
            ontology_lines = self.ontology_context_lines(decision_state)
            active_lines = self.active_investment_opinion_lines(decision_state)
            event_key_parts = [snapshot.account_id, "timing", item.symbol, item.decision]
            news_event_suffix = relation_news_event_key_suffix(relation_context)
            if news_event_suffix:
                event_key_parts.append(news_event_suffix)
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "ALERT" if item.tone == "danger" else "WATCH",
                "holdingTiming",
                ":".join(event_key_parts),
                item.name,
                ["상태 " + decision_phrase, *self.holding_price_lines(position, snapshot.portfolio), self.flow_context_line(position), self.investor_context_line(position), self.trend_context_line(position), self.holding_action_line(item.decision, item.profit_loss_rate)] + relation_lines + ontology_lines + active_lines,
                item.symbol,
                criteria=self.criteria(
                    "관계 규칙이 위험/주의 상태로 성립하거나 손익률이 손실 기준 "
                    + compact_number(loss_threshold)
                    + "%에서 완충 "
                    + compact_number(loss_buffer)
                    + "%p 이상 더 악화될 때",
                    "상태 " + decision_phrase + ", 수익률 " + signed_pct(item.profit_loss_rate) + (", " + " · ".join(relation_lines[:2]) if relation_lines else ""),
                ),
                metadata={
                    "holdingDecision": item.decision,
                    "holdingDecisionBasis": item.decision_basis,
                    "holdingDecisionScore": round(float(item.exit_pressure or 0), 1),
                    "profitLossRate": round(float(item.profit_loss_rate or 0), 2),
                    "ontologyRelationContext": relation_context,
                    "ontologyPromptContext": prompt_context,
                    "ontologyOpinion": dict(item.ontology_opinion or {}),
                    "ontologyWorldview": dict(item.ontology_worldview or {}),
                    "activeInvestmentOpinion": dict(item.active_investment_opinion or {}),
                    "ontologyReviewContext": dict(item.ai_context or {}),
                },
            ))
        return events

    def apply_cadence(self, events: List[AlertEvent], store: MonitorStateRepository, force: bool = False) -> List[AlertEvent]:
        if force:
            return events
        filtered: List[AlertEvent] = []
        now = now_ms()
        for event in events:
            minutes = self.dispatch_cadence_minutes(event)
            sent_at = store.sent.get(self.dispatch_cadence_key(event))
            if not sent_at:
                filtered.append(event)
                continue
            try:
                previous = datetime.fromisoformat(str(sent_at).replace("Z", "+00:00")).timestamp() * 1000
            except ValueError:
                filtered.append(event)
                continue
            if now - previous >= minutes * 60 * 1000:
                filtered.append(event)
        return filtered
