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
from .ontology_inference_context import inferencebox_source_name, ontology_projection_from_metadata, relation_contexts_from_snapshot
from .ontology_insights import build_investment_insight_events, relation_news_event_key_suffix, split_operational_and_investment_events
from .ontology_relation_reasoning import decision_action_group_for_label, relation_rule_context_summary_lines, relation_thresholds_from_settings
from .parsing import parse_assignments
from .portfolio import AccountSnapshot, AlertEvent, Position, monitor_state_has_live_account_data, status_has_account_data_failure
from .portfolio_calculations import DEFAULT_FX_RATES, fx_rates_with_external_signals, runtime_fx_currencies_from_external_signals, value_in_base
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
    projection = ontology_projection_from_metadata(metadata)
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


def graph_store_label(value: object) -> str:
    graph_store = str(value or "").strip().lower()
    if graph_store in {"typedb", ""}:
        return "TypeDB"
    return "그래프 저장소"


def normalized_monitoring_graph_store(value: object) -> str:
    graph_store = str(value or "").strip().lower()
    if graph_store in {"", "typedb"}:
        return "typedb"
    return graph_store


def ontology_inference_failure_stage(reason_code: object, status: object, detail: Dict[str, object]) -> str:
    code = str(reason_code or "").strip()
    status_text = str(status or "").strip().lower()
    typedb_read_status = str((detail or {}).get("typedbReadStatus") or "").strip().lower()
    rulebox_status = str((detail or {}).get("ruleboxExecutionStatus") or "").strip().lower()
    if code == "invalidABox":
        return "ABox 검증"
    if code == "missingProjection":
        return "온톨로지 투영 생성"
    if code == "projectionSaveFailed":
        return graph_store_label((detail or {}).get("graphStore")) + " 투영 저장"
    if code == "ruleboxExecutionFailed" or (rulebox_status and rulebox_status not in {"ok", "partial"}):
        return "RuleBox 실행"
    if typedb_read_status and typedb_read_status not in {"ok", "partial"}:
        return "InferenceBox 조회"
    if code == "missingInferenceBox":
        return "InferenceBox 생성/저장"
    if code == "nativeReasoningMissing":
        return "RuleBox materialization"
    if status_text and status_text not in {"ok", "partial"}:
        return "InferenceBox 상태"
    if code == "emptyInferenceBox":
        return "InferenceBox 결과 생성"
    return "관계 추론 연결"


def ontology_inference_failure_detail(reason_code: object, status: object, detail: Dict[str, object]) -> str:
    code = str(reason_code or "").strip()
    status_text = str(status or "").strip()
    parts: List[str] = []
    if status_text:
        parts.append("status=" + status_text)
    for key, label in [
        ("projectionReason", "projectionReason"),
        ("ruleboxExecutionStatus", "ruleboxStatus"),
        ("ruleboxExecutionReason", "ruleboxReason"),
        ("typedbReadStatus", "typedbRead"),
        ("typedbReadReason", "typedbReadReason"),
        ("inferenceReason", "inferenceReason"),
        ("clearInferenceStatus", "clearStatus"),
        ("clearInferenceReason", "clearReason"),
    ]:
        value = str((detail or {}).get(key) or "").strip()
        if value:
            parts.append(label + "=" + value)
    if code == "missingInferenceBox":
        parts.append("inferenceBox 섹션 없음")
    return "; ".join(parts[:8])


def ontology_inference_event_metadata(snapshot: AccountSnapshot) -> Dict[str, object]:
    metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
    projection = ontology_projection_from_metadata(metadata)
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
            "nativeTypeDbReasoned": bool(item.get("nativeTypeDbReasoned")),
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
            "nativeTypeDbReasoned": bool(item.get("nativeTypeDbReasoned")),
        }
        for item in (inference.get("traces") or [])[:8]
        if isinstance(item, dict)
    ]
    rulebox_execution = projection.get("ruleboxExecution") if isinstance(projection.get("ruleboxExecution"), dict) else {}
    graph_store = str(inference.get("graphStore") or projection.get("graphStore") or "")
    source = inferencebox_source_name({
        **dict(inference or {}),
        "graphStore": graph_store,
    })
    return {
        "source": source,
        "graphStore": graph_store,
        "status": str(inference.get("status") or projection.get("status") or ""),
        "reason": str(inference.get("reason") or projection.get("reason") or ""),
        "reasoningMode": str(inference.get("reasoningMode") or rulebox_execution.get("reasoningMode") or ""),
        "querySource": str(inference.get("querySource") or ""),
        "typedbReadStatus": str(inference.get("typedbReadStatus") or ""),
        "typedbReadReason": str(inference.get("typedbReadReason") or ""),
        "projectionMode": str(projection.get("projectionMode") or ""),
        "ruleboxExecutionStatus": str(rulebox_execution.get("status") or ""),
        "ruleboxExecutionReason": str(rulebox_execution.get("reason") or ""),
        "ruleboxStatementCount": int(number(rulebox_execution.get("statementCount")) or 0),
        "ruleboxRelationTypes": list(rulebox_execution.get("relationTypes") or [])[:20] if isinstance(rulebox_execution.get("relationTypes"), list) else [],
        "clearInferenceStatus": str((rulebox_execution.get("clearResult") or {}).get("status") or "") if isinstance(rulebox_execution.get("clearResult"), dict) else "",
        "clearInferenceReason": str((rulebox_execution.get("clearResult") or {}).get("reason") or "") if isinstance(rulebox_execution.get("clearResult"), dict) else "",
        "nativeTypeDbReasoningUsed": bool(inference.get("nativeTypeDbReasoningUsed")),
        "typedbBootstrapReasoningUsed": bool(inference.get("typedbBootstrapReasoningUsed")),
        "entityCount": int(number(inference.get("entityCount")) or 0),
        "relationCount": int(number(inference.get("relationCount")) or 0),
        "traceCount": int(number(inference.get("traceCount")) or 0),
        "nativeRelationCount": int(number(inference.get("nativeRelationCount")) or 0),
        "relations": relations,
        "traces": traces,
    }


def ontology_validation_issues_from_projection(projection: Dict[str, object]) -> List[Dict[str, object]]:
    validation = projection.get("aboxValidation") if isinstance(projection.get("aboxValidation"), dict) else {}
    issues = validation.get("issues") if isinstance(validation.get("issues"), list) else []
    return [dict(item) for item in issues if isinstance(item, dict)]


def ontology_validation_issue_summary(projection: Dict[str, object], limit: int = 2) -> str:
    rows: List[str] = []
    for issue in ontology_validation_issues_from_projection(projection)[: max(1, limit)]:
        message = str(issue.get("message") or issue.get("code") or "").strip()
        subject = str(issue.get("subject") or "").strip()
        if not message:
            continue
        if subject:
            rows.append(message + " (" + subject + ")")
        else:
            rows.append(message)
    return "; ".join(rows)


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
        self.runtime_fx_currencies = set()
        self.strategy_model = StrategyModel(settings)

    def use_external_fx_rates(self, external_signals: Dict[str, object] = None) -> None:
        self.fx_rates = fx_rates_with_external_signals(self.base_fx_rates, external_signals)
        self.runtime_fx_currencies = runtime_fx_currencies_from_external_signals(external_signals)

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

    def ontology_inference_missing_required_cycles(self) -> int:
        raw = self.settings.get("ontologyInferenceMissingConsecutiveCycles") or self.settings.get("notificationOntologyInferenceMissingConsecutiveCycles") or 2
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            value = 2
        return max(1, min(5, value))

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
        inference_missing = False
        raw_events.extend(self.connection_events(decision_snapshot, previous))
        raw_events.extend(self.heartbeat_events(decision_snapshot))
        if has_account_data:
            inference_state = self.ontology_inference_missing_state(decision_snapshot)
            inference_missing = bool(inference_state.get("missing"))
            self.attach_ontology_inference_missing_state(decision_snapshot, inference_state)
            inference_missing_events = self.ontology_inference_missing_events(decision_snapshot, previous or {}, inference_state)
            raw_events.extend(inference_missing_events)
            if not inference_missing:
                raw_events.extend(self.ontology_signal_events(signal_snapshot))
        raw_events.extend(self.external_signal_events(signal_snapshot, previous or {}))
        if has_account_data and not inference_missing:
            raw_events.extend(self.holding_timing_events(decision_snapshot))
        raw_events = self.attach_data_freshness(decision_snapshot, raw_events)
        system_events, signal_events = split_operational_and_investment_events(raw_events)
        signal_events = self.enabled_signal_events(signal_events)
        if inference_missing:
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
        inference_missing_events = self.only_rule(
            ONTOLOGY_INFERENCE_MISSING,
            self.ontology_inference_missing_events(snapshot, force_confirmed=True),
        )
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
        account_context = (snapshot.metadata or {}).get("accountContext") if isinstance(snapshot.metadata, dict) else {}
        account_context = account_context if isinstance(account_context, dict) else {}
        snapshot.decisions = decisions_for_positions(
            snapshot.positions,
            snapshot.portfolio,
            self.strategy_model,
            external_signals=snapshot.external_signals,
            relation_contexts_by_symbol=inference_contexts,
            runtime_context={"account": dict(account_context)},
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

    def ontology_inference_missing_state(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        positions = [item for item in snapshot.positions or [] if getattr(item, "symbol", "") and not item.is_cash()]
        if not snapshot.has_live_account_data() or not positions:
            return {"missing": False, "positionCount": len(positions)}
        inference_contexts = relation_contexts_from_snapshot(
            snapshot,
            getattr(self.strategy_model, "settings", {}) if self.strategy_model else {},
        )
        if inference_contexts:
            return {"missing": False, "positionCount": len(positions), "contextCount": len(inference_contexts)}

        reason_code, reason, inference_status = self.ontology_inference_missing_reason(snapshot)
        relation_count = int(number(inference_status.get("relationCount")) or 0)
        trace_count = int(number(inference_status.get("traceCount")) or 0)
        entity_count = int(number(inference_status.get("entityCount")) or 0)
        status_text = str(inference_status.get("status") or "missing").strip() or "missing"
        return {
            "missing": True,
            "reasonCode": reason_code,
            "reason": reason,
            "status": status_text,
            "source": str(inference_status.get("source") or ""),
            "graphStore": str(inference_status.get("graphStore") or ""),
            "rawGraphStore": str(inference_status.get("rawGraphStore") or ""),
            "projectionReason": str(inference_status.get("projectionReason") or ""),
            "reasoningMode": str(inference_status.get("reasoningMode") or ""),
            "querySource": str(inference_status.get("querySource") or ""),
            "typedbReadStatus": str(inference_status.get("typedbReadStatus") or ""),
            "typedbReadReason": str(inference_status.get("typedbReadReason") or ""),
            "positionCount": len(positions),
            "entityCount": entity_count,
            "relationCount": relation_count,
            "traceCount": trace_count,
            "nativeRelationCount": int(number(inference_status.get("nativeRelationCount")) or 0),
            "nativeTypeDbReasoningUsed": bool(inference_status.get("nativeTypeDbReasoningUsed")),
            "typedbBootstrapReasoningUsed": bool(inference_status.get("typedbBootstrapReasoningUsed")),
            "projectionMode": str(inference_status.get("projectionMode") or ""),
            "ruleboxExecutionStatus": str(inference_status.get("ruleboxExecutionStatus") or ""),
            "ruleboxExecutionReason": str(inference_status.get("ruleboxExecutionReason") or ""),
            "ruleboxStatementCount": int(number(inference_status.get("ruleboxStatementCount")) or 0),
            "ruleboxRelationTypes": list(inference_status.get("ruleboxRelationTypes") or [])[:20] if isinstance(inference_status.get("ruleboxRelationTypes"), list) else [],
            "clearInferenceStatus": str(inference_status.get("clearInferenceStatus") or ""),
            "clearInferenceReason": str(inference_status.get("clearInferenceReason") or ""),
            "inferenceStatus": dict(inference_status or {}),
        }

    def attach_ontology_inference_missing_state(self, snapshot: AccountSnapshot, state: Dict[str, object]) -> None:
        compact = dict(state or {})
        compact.pop("inferenceStatus", None)
        snapshot.metadata.setdefault("ontology", {})["inferenceMissingState"] = compact

    def previous_ontology_inference_missing_state(self, previous: Dict[str, object]) -> Dict[str, object]:
        if not isinstance(previous, dict) or not monitor_state_has_live_account_data(previous):
            return {"missing": False}
        positions = previous.get("positions") if isinstance(previous.get("positions"), dict) else {}
        if not positions:
            return {"missing": False}
        metadata = previous.get("metadata") if isinstance(previous.get("metadata"), dict) else {}
        ontology = metadata.get("ontology") if isinstance(metadata.get("ontology"), dict) else {}
        stored = ontology.get("inferenceMissingState") if isinstance(ontology.get("inferenceMissingState"), dict) else {}
        if stored:
            return dict(stored)
        reason_code, reason, inference_status = self.ontology_inference_missing_reason_from_metadata(metadata)
        if not reason_code or reason_code == "positionInferenceMissing":
            return {"missing": False}
        return {
            "missing": True,
            "reasonCode": reason_code,
            "reason": reason,
            "status": str(inference_status.get("status") or "missing"),
            "source": str(inference_status.get("source") or ""),
            "graphStore": str(inference_status.get("graphStore") or ""),
            "reasoningMode": str(inference_status.get("reasoningMode") or ""),
            "positionCount": len(positions),
            "relationCount": int(number(inference_status.get("relationCount")) or 0),
            "traceCount": int(number(inference_status.get("traceCount")) or 0),
            "nativeRelationCount": int(number(inference_status.get("nativeRelationCount")) or 0),
        }

    def ontology_inference_missing_confirmation(self, state: Dict[str, object], previous: Dict[str, object]) -> Dict[str, object]:
        if not bool((state or {}).get("missing")):
            return {"confirmed": False, "requiredCycles": self.ontology_inference_missing_required_cycles(), "currentCycle": 0}
        required = self.ontology_inference_missing_required_cycles()
        previous_state = self.previous_ontology_inference_missing_state(previous or {})
        previous_missing = bool(previous_state.get("missing"))
        immediate_codes = {"invalidABox"}
        immediate = str(state.get("reasonCode") or "") in immediate_codes
        effective_required = 1 if immediate else required
        confirmed = effective_required <= 1 or previous_missing
        return {
            "confirmed": confirmed,
            "requiredCycles": effective_required,
            "currentCycle": effective_required if confirmed else 1,
            "previousMissing": previous_missing,
            "previousReasonCode": str(previous_state.get("reasonCode") or ""),
            "previousStatus": str(previous_state.get("status") or ""),
        }

    def ontology_inference_missing_events(
        self,
        snapshot: AccountSnapshot,
        previous: Dict[str, object] = None,
        state: Dict[str, object] = None,
        force_confirmed: bool = False,
    ) -> List[AlertEvent]:
        state = state if isinstance(state, dict) else self.ontology_inference_missing_state(snapshot)
        if not bool(state.get("missing")):
            return []

        confirmation = self.ontology_inference_missing_confirmation(state, previous or {})
        if force_confirmed and not bool(confirmation.get("confirmed")):
            confirmation = {
                **confirmation,
                "confirmed": True,
                "forced": True,
                "currentCycle": int(confirmation.get("requiredCycles") or 1),
            }
        self.attach_ontology_inference_missing_state(snapshot, {**state, "confirmation": confirmation})
        if not bool(confirmation.get("confirmed")):
            return []

        reason_code = str(state.get("reasonCode") or "missingInferenceBox")
        reason = str(state.get("reason") or "그래프 저장소 InferenceBox 관계 추론을 사용할 수 없습니다")
        inference_status = dict(state.get("inferenceStatus") or {})
        relation_count = int(number(state.get("relationCount")) or 0)
        trace_count = int(number(state.get("traceCount")) or 0)
        entity_count = int(number(state.get("entityCount")) or 0)
        native_used = bool(state.get("nativeTypeDbReasoningUsed"))
        typedb_used = native_used
        status_text = str(state.get("status") or "missing").strip() or "missing"
        graph_store = str(state.get("graphStore") or inference_status.get("graphStore") or "typedb").strip() or "typedb"
        source_name = str(state.get("source") or inference_status.get("source") or ("typedbInferenceBox" if graph_store == "typedb" else "graphStoreInferenceBox"))
        reasoning_mode = str(state.get("reasoningMode") or inference_status.get("reasoningMode") or "").strip()
        query_source = str(state.get("querySource") or inference_status.get("querySource") or "").strip()
        typedb_read_status = str(state.get("typedbReadStatus") or inference_status.get("typedbReadStatus") or "").strip()
        typedb_read_reason = str(state.get("typedbReadReason") or inference_status.get("typedbReadReason") or "").strip()
        failure_detail_context = {
            **inference_status,
            **dict(state or {}),
            "graphStore": graph_store,
            "rawGraphStore": state.get("rawGraphStore") or inference_status.get("rawGraphStore") or "",
        }
        failure_stage = ontology_inference_failure_stage(reason_code, status_text, failure_detail_context)
        failure_detail = ontology_inference_failure_detail(reason_code, status_text, failure_detail_context)
        lines = [
            "상태 온톨로지 추론 결과 없음",
            "저장소 " + graph_store_label(graph_store),
            "추론 소스 " + source_name,
            "판단 차단 매수·매도 판단은 생성하지 않았습니다",
            "원인 " + reason,
            "실패 단계 " + failure_stage,
            "추론 상태 status=" + status_text + ", relations=" + str(relation_count) + ", traces=" + str(trace_count),
            "확인 상태 " + str(int(confirmation.get("currentCycle") or 1)) + "/" + str(int(confirmation.get("requiredCycles") or 1)) + "회 연속 감지",
            "보유 " + str(int(state.get("positionCount") or 0)) + "개",
            "확인 행동 TypeDB 연결, RuleBox 저장 상태, 온톨로지 추론 워커 점검",
        ]
        if failure_detail:
            lines.insert(6, "실패 상세 " + failure_detail)
        if reasoning_mode:
            lines.insert(3, "추론 모드 " + reasoning_mode)
        if query_source or typedb_read_status:
            lines.insert(4, "조회 상태 source=" + (query_source or "unknown") + ", typedbRead=" + (typedb_read_status or "unknown"))
        if typedb_read_reason:
            lines.insert(5, "조회 오류 " + typedb_read_reason)
        if status_text.lower() in {"ok", "partial"} and relation_count == 0 and trace_count == 0:
            lines.insert(4, "조회 결과 " + graph_store_label(graph_store) + " 조회는 성공했지만 InferenceBox 관계와 trace가 0개입니다")
        if inference_status.get("validationIssueSummary"):
            lines.insert(3, "검증 오류 " + str(inference_status.get("validationIssueSummary")))
        inference_metadata = ontology_inference_event_metadata(snapshot)
        if not inference_metadata:
            inference_metadata = {
                "source": source_name,
                "graphStore": graph_store,
                "rawGraphStore": str(state.get("rawGraphStore") or ""),
                "projectionReason": str(state.get("projectionReason") or ""),
                "status": status_text,
                "reasoningMode": reasoning_mode,
                "querySource": query_source,
                "typedbReadStatus": typedb_read_status,
                "typedbReadReason": typedb_read_reason,
                "projectionMode": str(state.get("projectionMode") or ""),
                "ruleboxExecutionStatus": str(state.get("ruleboxExecutionStatus") or ""),
                "ruleboxExecutionReason": str(state.get("ruleboxExecutionReason") or ""),
                "ruleboxStatementCount": int(number(state.get("ruleboxStatementCount")) or 0),
                "ruleboxRelationTypes": list(state.get("ruleboxRelationTypes") or [])[:20] if isinstance(state.get("ruleboxRelationTypes"), list) else [],
                "clearInferenceStatus": str(state.get("clearInferenceStatus") or ""),
                "clearInferenceReason": str(state.get("clearInferenceReason") or ""),
                "nativeTypeDbReasoningUsed": native_used,
                "typedbBootstrapReasoningUsed": bool(state.get("typedbBootstrapReasoningUsed")),
                "graphStoreReasoningUsed": native_used or typedb_used,
                "entityCount": entity_count,
                "relationCount": relation_count,
                "traceCount": trace_count,
                "nativeRelationCount": int(number(state.get("nativeRelationCount")) or 0),
                "relations": [],
                "traces": [],
            }
        inference_metadata = dict(inference_metadata)
        for key in ["aboxValidationStatus", "aboxValidationErrorCount", "aboxValidationWarningCount", "aboxValidationIssues", "validationIssueSummary"]:
            if key in inference_status:
                inference_metadata[key] = inference_status.get(key)
        for key in ["source", "graphStore", "rawGraphStore", "projectionReason", "reasoningMode", "querySource", "typedbReadStatus", "typedbReadReason"]:
            value = state.get(key) or inference_status.get(key)
            if value not in (None, ""):
                inference_metadata[key] = value
        inference_metadata.update({
            "missing": True,
            "missingReasonCode": reason_code,
            "missingReason": reason,
            "confirmation": confirmation,
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
                "실계좌 데이터와 보유 종목이 있는데 그래프 저장소 InferenceBox 관계 추론을 사용할 수 없을 때",
                reason + ", 보유 " + str(int(state.get("positionCount") or 0)) + "개, relationCount=" + str(relation_count) + ", traceCount=" + str(trace_count),
            ),
            metadata={
                "blockedInvestmentJudgment": True,
                "missingInferenceBox": reason_code == "missingInferenceBox",
                "invalidOntologyProjection": reason_code == "invalidABox",
                "missingInferenceReasonCode": reason_code,
                "missingInferenceReason": reason,
                "positionCount": int(state.get("positionCount") or 0),
                "ontologyInference": inference_metadata,
            },
        )]

    def ontology_inference_missing_reason(self, snapshot: AccountSnapshot):
        metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
        return self.ontology_inference_missing_reason_from_metadata(metadata)

    def ontology_inference_missing_reason_from_metadata(self, metadata: Dict[str, object]):
        projection = ontology_projection_from_metadata(metadata)
        if not isinstance(projection, dict) or not projection:
            return "missingProjection", "TypeDB 온톨로지 투영 결과가 없습니다", {
                "status": "missing",
                "source": "typedbInferenceBox",
                "graphStore": "typedb",
                "projectionMode": "",
                "ruleboxExecutionStatus": "",
            }
        inference = projection.get("inferenceBox") if isinstance(projection.get("inferenceBox"), dict) else {}
        rulebox_execution = projection.get("ruleboxExecution") if isinstance(projection.get("ruleboxExecution"), dict) else {}
        clear_result = rulebox_execution.get("clearResult") if isinstance(rulebox_execution.get("clearResult"), dict) else {}
        status = str((inference or {}).get("status") or projection.get("status") or "").strip()
        raw_graph_store = str((inference or {}).get("graphStore") or projection.get("graphStore") or "typedb").strip() or "typedb"
        graph_store = normalized_monitoring_graph_store(raw_graph_store)
        source_name = inferencebox_source_name({
            **dict(inference or {}),
            "graphStore": graph_store,
        }) if isinstance(inference, dict) and inference else ("typedbInferenceBox" if graph_store == "typedb" else "graphStoreInferenceBox")
        common = {
            "status": status or ("empty" if isinstance(inference, dict) else "missing"),
            "source": source_name,
            "graphStore": graph_store,
            "rawGraphStore": raw_graph_store if raw_graph_store.lower() != graph_store.lower() else "",
            "projectionReason": str(projection.get("reason") or ""),
            "inferenceReason": str((inference or {}).get("reason") or ""),
            "reasoningMode": str((inference or {}).get("reasoningMode") or rulebox_execution.get("reasoningMode") or ""),
            "querySource": str((inference or {}).get("querySource") or ""),
            "typedbReadStatus": str((inference or {}).get("typedbReadStatus") or ""),
            "typedbReadReason": str((inference or {}).get("typedbReadReason") or ""),
            "projectionMode": str(projection.get("projectionMode") or ""),
            "ruleboxExecutionStatus": str(rulebox_execution.get("status") or ""),
            "ruleboxExecutionReason": str(rulebox_execution.get("reason") or ""),
            "ruleboxStatementCount": int(number(rulebox_execution.get("statementCount")) or 0),
            "ruleboxRelationTypes": list(rulebox_execution.get("relationTypes") or [])[:20] if isinstance(rulebox_execution.get("relationTypes"), list) else [],
            "clearInferenceStatus": str(clear_result.get("status") or ""),
            "clearInferenceReason": str(clear_result.get("reason") or ""),
            "nativeTypeDbReasoningUsed": bool((inference or {}).get("nativeTypeDbReasoningUsed")) if isinstance(inference, dict) else False,
            "typedbBootstrapReasoningUsed": bool((inference or {}).get("typedbBootstrapReasoningUsed")) if isinstance(inference, dict) else False,
            "entityCount": int(number((inference or {}).get("entityCount")) or 0) if isinstance(inference, dict) else 0,
            "relationCount": int(number((inference or {}).get("relationCount")) or 0) if isinstance(inference, dict) else 0,
            "traceCount": int(number((inference or {}).get("traceCount")) or 0) if isinstance(inference, dict) else 0,
            "nativeRelationCount": int(number((inference or {}).get("nativeRelationCount")) or 0) if isinstance(inference, dict) else 0,
        }
        validation = projection.get("aboxValidation") if isinstance(projection.get("aboxValidation"), dict) else {}
        validation_error_count = int(number(validation.get("errorCount")) or 0)
        if str(projection.get("status") or "").strip().lower() == "invalid-abox" or validation_error_count:
            summary = ontology_validation_issue_summary(projection)
            common.update({
                "aboxValidationStatus": str(validation.get("status") or "invalid"),
                "aboxValidationErrorCount": validation_error_count,
                "aboxValidationWarningCount": int(number(validation.get("warningCount")) or 0),
                "aboxValidationIssues": ontology_validation_issues_from_projection(projection)[:5],
                "validationIssueSummary": summary,
            })
            reason = "ABox 검증 실패"
            if summary:
                reason += ": " + summary
            return "invalidABox", reason, common
        projection_status = str(projection.get("status") or "").strip().lower()
        if projection_status and projection_status not in {"ok", "partial"}:
            reason = graph_store_label(graph_store) + " projection 저장 실패"
            if common.get("projectionReason"):
                reason += ": " + str(common.get("projectionReason"))
            return "projectionSaveFailed", reason, common
        if not inference:
            detail = ontology_inference_failure_detail("missingInferenceBox", common["status"], common)
            reason = graph_store_label(graph_store) + " InferenceBox 응답이 없습니다"
            if detail:
                reason += ": " + detail
            return "missingInferenceBox", reason, common
        if common["ruleboxExecutionStatus"] and common["ruleboxExecutionStatus"].lower() not in {"ok", "partial"}:
            reason = "RuleBox 실행 실패"
            if common["ruleboxExecutionReason"]:
                reason += ": " + common["ruleboxExecutionReason"]
            return "ruleboxExecutionFailed", reason, common
        if status and status.lower() not in {"ok", "partial"}:
            reason = graph_store_label(graph_store) + " InferenceBox 상태가 " + status + "입니다"
            detail = common.get("inferenceReason") or common.get("typedbReadReason")
            if detail:
                reason += ": " + str(detail)
            return "inferenceBoxStatusBlocked", reason, common
        relations = inference.get("relations") if isinstance(inference.get("relations"), list) else []
        traces = inference.get("traces") if isinstance(inference.get("traces"), list) else []
        graph_reasoning_used = bool(inference.get("nativeTypeDbReasoningUsed"))
        if graph_store.lower() == "typedb" and not graph_reasoning_used:
            return "nativeReasoningMissing", graph_store_label(graph_store) + " RuleBox materialization 관계가 아직 없습니다", common
        if not graph_reasoning_used and not relations and not traces:
            return "nativeReasoningMissing", graph_store_label(graph_store) + " 추론 관계가 아직 없습니다", common
        if not relations and not traces:
            return "emptyInferenceBox", graph_store_label(graph_store) + " InferenceBox 관계와 근거가 0개입니다", common
        return "positionInferenceMissing", "보유 종목과 연결된 " + graph_store_label(graph_store) + " InferenceBox 관계가 없습니다", common

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
                ["상태 " + decision_phrase, *self.holding_price_lines(position, snapshot.portfolio, positions.values()), self.flow_context_line(position), self.investor_context_line(position), self.trend_context_line(position), self.holding_action_line(item.decision, item.profit_loss_rate)] + relation_lines + ontology_lines + active_lines,
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
