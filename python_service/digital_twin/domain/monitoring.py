from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, List

from .alert_formatting import compact_number, money, price_money, signed_number, signed_pct
from .data_freshness import data_freshness_required, freshness_from_position, freshness_record
from .external_api_sources import external_api_source_metadata
from .external_signal_deltas import external_signals_with_deltas
from .market_data import number
from .market_observations import MARKET_OBSERVATION_CANDIDATES_KEY, market_observation_baseline
from .message_types import (
    DEFAULT_ALERT_RULES,
    DEFAULT_ALERT_THRESHOLDS,
    DEFAULT_CADENCE,
    INVESTMENT_INSIGHT,
    MARKET_OBSERVATION,
    MIN_CADENCE_MINUTES,
    ONTOLOGY_OBSERVATION_FOLLOWUP,
    ONTOLOGY_INFERENCE_MISSING,
    PORTFOLIO_HOLDINGS_SNAPSHOT,
    WATCHLIST_ONTOLOGY_SIGNAL,
    CRYPTO_ONTOLOGY_SIGNAL,
)
from .ontology_inference_context import inferencebox_source_name, ontology_projection_from_metadata, relation_contexts_from_snapshot
from .ontology_insights import build_investment_insight_events, relation_news_event_key_suffix, split_operational_and_investment_events
from .ontology_projection_status import (
    projection_reuses_unchanged_inference,
    projection_waits_for_reasoning_worker,
)
from .ontology_relation_reasoning import relation_rule_context_summary_lines
from .ontology_decision_state import (
    CHANGE_STATE_LABELS,
    DATA_STATE_LABELS,
    REVIEW_LEVEL_LABELS,
    data_state_is_usable,
    review_level_at_least,
)
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
DEFAULT_CONNECTION_FAILURE_ALERT_STREAK = 3


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def ontology_quality_event_metadata(snapshot: AccountSnapshot) -> Dict[str, object]:
    metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
    projection = ontology_projection_from_metadata(metadata)
    if not isinstance(projection, dict) or not projection:
        return {}
    validation = projection.get("aboxValidation") if isinstance(projection.get("aboxValidation"), dict) else {}
    errors = list(validation.get("errors") or [])
    warnings = list(validation.get("warnings") or [])
    status = str(projection.get("status") or "").strip().lower()
    if status in {"error", "failed", "missing", "unavailable"} or errors:
        data_state = "unavailable"
        validation_state = "blocked"
        reason = "온톨로지 연결 또는 검증 오류가 있어 투자 판단을 보류합니다."
    elif projection_waits_for_reasoning_worker(projection):
        data_state = "partial"
        validation_state = "conditional"
        reason = "확정 스냅샷은 저장됐으며 전용 온톨로지 추론 워커의 최신 TypeDB 세대를 기다리고 있습니다."
    elif status in {"partial", "limited", "degraded", "stale"} or warnings:
        data_state = "partial"
        validation_state = "conditional"
        reason = "온톨로지 자료에 누락이나 경고가 있어 투자 판단을 조건부로 사용합니다."
    else:
        data_state = "sufficient"
        validation_state = "ready"
        reason = "온톨로지 연결과 필수 근거가 확인됐습니다."
    return {
        "status": validation_state,
        "validationState": validation_state,
        "dataState": data_state,
        "validationStateLabel": {
            "ready": "검증 완료",
            "conditional": "조건부 사용",
            "blocked": "판단 보류",
        }[validation_state],
        "dataStateLabel": DATA_STATE_LABELS[data_state],
        "qualitySampleId": str(projection.get("qualitySampleId") or ""),
        "source": "ontologyProjection",
        "reason": reason,
        "errors": errors[:5],
        "warnings": warnings[:5],
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
        return "TypeDB native rule 실행"
    if typedb_read_status and typedb_read_status not in {"ok", "partial"}:
        return "InferenceBox 조회"
    if code == "missingInferenceBox":
        return "InferenceBox 생성/저장"
    if code == "nativeReasoningMissing":
        return "TypeDB native rule materialization"
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
            "evidenceRole": str(item.get("evidenceRole") or "context"),
            "reviewLevel": str(item.get("reviewLevel") or "observe"),
            "dataState": str(item.get("dataState") or "partial"),
            "changeState": str(item.get("changeState") or "unchanged"),
            "nativeTypeDbReasoned": bool(item.get("nativeTypeDbReasoned")),
        }
        for item in (inference.get("relations") or [])[:8]
        if isinstance(item, dict)
    ]
    traces = [
        {
            "ruleId": str(item.get("ruleId") or ""),
            "label": str(item.get("label") or ""),
            "reviewLevel": str(item.get("reviewLevel") or "observe"),
            "dataState": str(item.get("dataState") or "partial"),
            "validationState": str(item.get("validationState") or "conditional"),
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
        if event.rule == MARKET_OBSERVATION:
            metadata = dict(event.metadata or {}) if isinstance(event.metadata, dict) else {}
            if not bool(metadata.get("deliveryDeferred")):
                raw = self.settings.get("marketObservationImmediateCadenceMinutes")
                try:
                    value = int(float(str(raw).strip())) if str(raw or "").strip() else MIN_CADENCE_MINUTES
                except ValueError:
                    value = MIN_CADENCE_MINUTES
                return max(MIN_CADENCE_MINUTES, value)
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

    def decision_state_phrase(self, label: object, review_level: object) -> str:
        text = str(label or "-").strip() or "-"
        state = str(review_level or "check").strip().lower()
        return text + " · " + REVIEW_LEVEL_LABELS.get(state, REVIEW_LEVEL_LABELS["check"])

    def enabled_signal_events(self, events: List[AlertEvent]) -> List[AlertEvent]:
        return [event for event in events or [] if self.enabled(event.rule)]

    def events_for_snapshot(
        self,
        snapshot: AccountSnapshot,
        previous: Dict[str, object],
        reasoning_context: Dict[str, object] = None,
    ) -> List[AlertEvent]:
        self.use_external_fx_rates(snapshot.external_signals)
        raw_events: List[AlertEvent] = []
        snapshot = self.snapshot_with_external_signal_deltas(snapshot, previous or {})
        signal_snapshot = snapshot
        decision_snapshot = self.snapshot_with_strategy_states(snapshot)
        has_account_data = decision_snapshot.has_live_account_data()
        projection = ontology_projection_from_metadata(
            decision_snapshot.metadata if isinstance(decision_snapshot.metadata, dict) else {}
        )
        unchanged_inference = projection_reuses_unchanged_inference(projection)
        if unchanged_inference:
            ontology_metadata = decision_snapshot.metadata.setdefault("ontology", {})
            ontology_metadata["investmentEvaluationGate"] = {
                "version": "investment-evaluation-gate-v1",
                "status": "skipped-unchanged-inference",
                "reason": "가격·손익·수급·뉴스 등 실질 입력과 TypeDB 관계 판단이 직전 세대와 같습니다.",
                "typeDbInferenceExecuted": False,
                "aiRequired": False,
                "investmentAlertRequired": False,
            }
        inference_missing_events: List[AlertEvent] = []
        inference_missing = False
        raw_events.extend(self.connection_events(decision_snapshot, previous))
        raw_events.extend(self.heartbeat_events(decision_snapshot))
        # A material quote candidate is always attached to the persisted
        # snapshot for a TypeDB follow-up. Ordinary candidates wait for that
        # relationship result; only a configured critical move bypasses it
        # with a factual raw observation notification.
        observation_events = self.market_observation_events(decision_snapshot, previous)
        candidates = [self.market_observation_candidate_payload(event) for event in observation_events]
        if candidates:
            decision_snapshot.metadata[MARKET_OBSERVATION_CANDIDATES_KEY] = candidates[:50]
        raw_events.extend([
            event for event in observation_events
            if not bool(dict(getattr(event, "metadata", {}) or {}).get("deliveryDeferred"))
        ])
        if has_account_data:
            inference_state = self.ontology_inference_missing_state(decision_snapshot)
            inference_missing = bool(inference_state.get("missing"))
            self.attach_ontology_inference_missing_state(decision_snapshot, inference_state)
            inference_missing_events = self.ontology_inference_missing_events(decision_snapshot, previous or {}, inference_state)
            raw_events.extend(inference_missing_events)
            if not inference_missing and not unchanged_inference:
                raw_events.extend(self.ontology_signal_events(signal_snapshot, reasoning_context=reasoning_context))
        raw_events.extend(self.external_signal_events(signal_snapshot, previous or {}))
        if has_account_data and not inference_missing and not unchanged_inference:
            raw_events.extend(self.holding_timing_events(decision_snapshot))
            raw_events.extend(self.observation_followup_events(decision_snapshot, reasoning_context))
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
        snapshot = self.snapshot_with_strategy_states(snapshot)
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
            elif event.rule == CRYPTO_ONTOLOGY_SIGNAL:
                crypto_freshness = (
                    snapshot.external_signals.get("cryptoFreshness")
                    if isinstance(snapshot.external_signals, dict)
                    and isinstance(snapshot.external_signals.get("cryptoFreshness"), dict)
                    else {}
                )
                record = freshness_record(
                    "CoinGecko",
                    event.rule,
                    settings=self.settings,
                    source_fetched_at=crypto_freshness.get("fetchedAt"),
                    source_as_of=crypto_freshness.get("fetchedAt"),
                    data_quality="actual" if str(crypto_freshness.get("status") or "") == "fresh" else "partial",
                    require_source_as_of=True,
                )
                if str(crypto_freshness.get("status") or "").lower() != "fresh":
                    record["status"] = "stale"
                    record["reason"] = str(crypto_freshness.get("reason") or "CoinGecko 신선도 기준 미충족")
                event.metadata["dataFreshness"] = {
                    "status": record.get("status"),
                    "reason": record.get("reason"),
                    "ageMinutes": record.get("ageMinutes"),
                    "maxAgeMinutes": record.get("maxAgeMinutes"),
                    "sources": [record],
                }
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
        ontology_quality = ontology_quality_event_metadata(snapshot)
        ontology_inference = ontology_inference_event_metadata(snapshot)
        external_api_sources = external_api_source_metadata(snapshot)
        for event in events:
            if generated_at:
                event.generated_at = generated_at
            if ontology_quality:
                event.metadata.setdefault("ontologyQuality", ontology_quality)
            if ontology_inference:
                event.metadata.setdefault("ontologyInference", ontology_inference)
            if external_api_sources:
                event.metadata.setdefault("externalApiSources", external_api_sources.get("externalApiSources"))
                event.metadata.setdefault("externalApiSourceLines", external_api_sources.get("externalApiSourceLines"))
        return events

    def snapshot_with_strategy_states(self, snapshot: AccountSnapshot) -> AccountSnapshot:
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
            or bool(previous_metadata.get("lastConnectionFailure"))
        )
        return (previous_streak if previous_failed else 0) + 1

    def set_connection_failure_streak(self, snapshot: AccountSnapshot, streak: int) -> None:
        metadata = dict(getattr(snapshot, "metadata", {}) or {})
        metadata["connectionFailureStreak"] = int(streak or 0)
        snapshot.metadata = metadata

    def connection_failure_alert_streak(self) -> int:
        raw = self.settings.get("monitorConnectionFailureAlertStreak")
        try:
            value = int(float(str(raw).strip())) if str(raw or "").strip() else DEFAULT_CONNECTION_FAILURE_ALERT_STREAK
        except ValueError:
            value = DEFAULT_CONNECTION_FAILURE_ALERT_STREAK
        return max(1, value)

    def connection_events(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        failure_streak = self.connection_failure_streak(snapshot, previous)
        self.set_connection_failure_streak(snapshot, failure_streak)
        failure_alert_streak = self.connection_failure_alert_streak()
        current_failed = snapshot.mode != "live" or status_has_account_data_failure(snapshot.status)
        if current_failed and failure_streak >= failure_alert_streak:
            stage = self.toss_failure_stage(snapshot) or "-"
            toss = self.toss_diagnostics(snapshot)
            auth_refreshes = int(float(toss.get("authRefreshes") or 0))
            retry_line = "재시도 access token 재발급 " + str(auth_refreshes) + "회" if auth_refreshes else ""
            lines = [
                "상태 연속 인증 실패",
                "연속 실패 " + str(failure_streak) + "회",
                "실패 단계 " + stage,
            ]
            if retry_line:
                lines.append(retry_line)
            lines.append(snapshot.status or snapshot.mode)
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "ALERT",
                "monitorConnection",
                ":".join([snapshot.account_id, "connection", snapshot.mode, "confirmed", snapshot.status]),
                "연결 상태",
                lines,
                criteria=self.criteria(
                    "토스 연결 모드가 live가 아니며 " + str(failure_alert_streak) + "회 이상 연속 실패할 때만 보냅니다",
                    "연속 실패 " + str(failure_streak) + "회, stage=" + stage + ", mode=" + str(snapshot.mode or "-") + ", status=" + str(snapshot.status or "-"),
                ),
                metadata={
                    "connectionFailureStreak": failure_streak,
                    "connectionFailureAlertStreak": failure_alert_streak,
                    "tossFailureStage": stage,
                    "tossAuthRefreshes": auth_refreshes,
                },
            ))
        previous_status = previous.get("status") if previous else ""
        previous_metadata = dict((previous or {}).get("metadata") or {})
        previous_connection_failure = previous_metadata.get("lastConnectionFailure")
        if (previous_status and previous_status != snapshot.status or previous_connection_failure) and not current_failed:
            previous_status_text = str(previous_status or "이전 연결 오류")
            if isinstance(previous_connection_failure, dict):
                previous_status_text = str(previous_connection_failure.get("status") or previous_status_text)
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "WATCH",
                "monitorConnection",
                ":".join([snapshot.account_id, "connection-change", snapshot.status]),
                "연결 상태 변화",
                ["이전 " + previous_status_text, "현재 " + snapshot.status],
                criteria=self.criteria(
                    "직전 스냅샷의 토스 연결 상태와 현재 상태가 다를 때",
                    "이전 " + previous_status_text + ", 현재 " + snapshot.status,
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

    def forced_holdings_snapshot_events(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        positions = [item for item in snapshot.positions or [] if not item.is_cash()]
        if not snapshot.has_live_account_data() or not positions:
            return []
        portfolio_value = snapshot.portfolio.total or snapshot.portfolio.invested
        lines = [
            "기준시각 " + str(snapshot.generated_at or "-"),
            "보유 종목 " + str(len(positions)) + "개",
            "계좌 평가금액 " + money(portfolio_value, "KRW"),
        ]
        for item in positions:
            lines.append(self.holding_snapshot_line(item))
        metadata = {
            "holdingsSnapshot": {
                "positionCount": len(positions),
                "portfolioValue": portfolio_value,
                "positions": [item.to_dict() for item in positions],
            },
            "dataFreshnessRequired": data_freshness_required(PORTFOLIO_HOLDINGS_SNAPSHOT),
            "dataFreshness": freshness_record(
                "accountSnapshot",
                PORTFOLIO_HOLDINGS_SNAPSHOT,
                settings=self.settings,
                source_fetched_at=snapshot.generated_at,
                data_quality=snapshot.mode,
            ),
        }
        event = AlertEvent(
            snapshot.account_id,
            snapshot.account_label,
            "WATCH",
            PORTFOLIO_HOLDINGS_SNAPSHOT,
            ":".join([snapshot.account_id, "holdings-snapshot", str(snapshot.generated_at or now_ms())]),
            "전체 보유 주식 점검",
            lines,
            criteria=self.criteria(
                "강제 점검 또는 수동 확인 요청에서 보유 종목 전체 상태를 확인할 때",
                "보유 " + str(len(positions)) + "개, 계좌 평가금액 " + money(portfolio_value, "KRW"),
            ),
            metadata=metadata,
        )
        return [event for event in self.stamp_events(snapshot, [event]) if self.enabled(event.rule)]

    def market_observation_price_change_threshold(self) -> float:
        default = float(DEFAULT_THRESHOLDS.get("marketObservationPriceChangePct", 2.0) or 2.0)
        try:
            value = float(self.thresholds.get("marketObservationPriceChangePct", default) or default)
        except (TypeError, ValueError):
            value = default
        return max(0.0, min(20.0, value))

    def market_observation_raw_delivery_mode(self) -> str:
        mode = str(self.settings.get("marketObservationRawDeliveryMode") or "critical-only").strip().lower()
        if mode in {"always", "critical-only", "typedb-first", "disabled"}:
            return mode
        return "critical-only"

    def market_observation_immediate_price_change_threshold(self) -> float:
        configured = number(self.settings.get("marketObservationImmediatePriceChangePct"))
        fallback = max(3.0, self.market_observation_price_change_threshold())
        return max(0.0, min(30.0, configured if configured > 0 else fallback))

    def market_observation_deliver_immediately(self, change_pct: float) -> bool:
        mode = self.market_observation_raw_delivery_mode()
        if mode == "always":
            return True
        if mode in {"typedb-first", "disabled"}:
            return False
        return abs(float(change_pct or 0.0)) >= self.market_observation_immediate_price_change_threshold()

    def market_observation_raw_delivery_threshold(self) -> float:
        """Return the threshold actually applied to a raw quote alert."""

        if self.market_observation_raw_delivery_mode() == "always":
            return self.market_observation_price_change_threshold()
        return self.market_observation_immediate_price_change_threshold()

    @staticmethod
    def market_observation_candidate_payload(event: AlertEvent) -> Dict[str, object]:
        metadata = dict(getattr(event, "metadata", {}) or {})
        observation = metadata.get("marketObservation") if isinstance(metadata.get("marketObservation"), dict) else {}
        return {
            "symbol": str(getattr(event, "symbol", "") or "").upper().strip(),
            "marketObservation": dict(observation),
            "deliveryDeferred": bool(metadata.get("deliveryDeferred")),
        }

    @staticmethod
    def monitor_state_positions(previous: Dict[str, object]) -> Dict[str, Dict[str, object]]:
        """Map the last persisted holding/watchlist state by current subject."""

        result: Dict[str, Dict[str, object]] = {}
        state = previous if isinstance(previous, dict) else {}
        for group_key in ("positions", "watchlist"):
            group = state.get(group_key) if isinstance(state.get(group_key), dict) else {}
            values = group.values() if isinstance(group, dict) else []
            for item in values:
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol") or "").upper().strip()
                if not symbol or symbol == "CASH":
                    continue
                # Holding state wins over a duplicate watchlist entry.
                if group_key == "watchlist" and symbol in result:
                    continue
                result[symbol] = dict(item)
        return result

    def market_observation_events(
        self,
        snapshot: AccountSnapshot,
        previous: Dict[str, object] = None,
    ) -> List[AlertEvent]:
        """Create bounded material quote candidates without an investment action.

        The candidate compares the current quote with its most recent durable
        observation anchor, so small consecutive ticks accumulate instead of
        resetting the threshold every monitor cycle. It never names a
        buy/sell/hold action, never consumes an InferenceBox, and can therefore
        be durably queued before the asynchronous TypeDB worker has an
        available direct-TypeQL InferenceBox generation.
        """

        if not snapshot.has_live_account_data():
            return []
        previous_positions = self.monitor_state_positions(previous or {})
        if not previous_positions:
            return []
        threshold = self.market_observation_price_change_threshold()
        current_positions: Dict[str, Position] = {}
        for group_name, positions in (("positions", snapshot.positions), ("watchlist", snapshot.watchlist)):
            for item in positions or []:
                if item.is_cash():
                    continue
                symbol = str(item.symbol or "").upper().strip()
                if not symbol or (group_name == "watchlist" and symbol in current_positions):
                    continue
                current_positions[symbol] = item

        events: List[AlertEvent] = []
        for symbol, item in current_positions.items():
            previous_item = previous_positions.get(symbol)
            if not previous_item:
                continue
            previous_price = self.position_current_price(previous_item)
            current_price = float(item.current_price or 0)
            if previous_price <= 0 or current_price <= 0:
                continue
            currency = item.currency or self.position_currency(item.to_dict())
            baseline = market_observation_baseline(previous or {}, symbol, currency)
            reasoning_baseline_price = (
                number(baseline.get("reasoningPrice"))
                or number(baseline.get("price"))
                or previous_price
            )
            initial_baseline_price = number(baseline.get("initialPrice")) or reasoning_baseline_price
            outbox_price = number(baseline.get("outboxPrice"))
            delivery_baseline_price = outbox_price or initial_baseline_price
            reasoning_change_pct = (
                (current_price - reasoning_baseline_price) / abs(reasoning_baseline_price) * 100.0
            )
            pending_reasoning_price = number(baseline.get("pendingReasoningPrice"))
            pending_change_pct = (
                (current_price - pending_reasoning_price) / abs(pending_reasoning_price) * 100.0
                if pending_reasoning_price > 0
                else reasoning_change_pct
            )
            delivery_change_pct = (
                (current_price - delivery_baseline_price) / abs(delivery_baseline_price) * 100.0
            )
            reasoning_material = (
                abs(reasoning_change_pct) >= threshold
                and (
                    pending_reasoning_price <= 0
                    or abs(pending_change_pct) >= threshold
                )
            )
            immediate_delivery = (
                abs(delivery_change_pct) >= threshold
                and self.market_observation_deliver_immediately(delivery_change_pct)
            )
            if not reasoning_material and not immediate_delivery:
                continue
            if immediate_delivery:
                baseline_price = delivery_baseline_price
                baseline_kind = "last-outbox-alert" if outbox_price > 0 else "initial-observation"
                change_pct = delivery_change_pct
            else:
                baseline_price = reasoning_baseline_price
                baseline_kind = (
                    "last-reasoning-candidate"
                    if baseline.get("reasoningQueuedAt")
                    else "initial-observation"
                    if number(baseline.get("price")) > 0
                    else "previous-snapshot"
                )
                change_pct = reasoning_change_pct
            direction = "up" if change_pct > 0 else "down" if change_pct < 0 else "flat"
            source = str(item.quote_source or item.source or "시세 공급자").strip()
            baseline_label = {
                "last-outbox-alert": "마지막 원시 알림 기준값",
                "last-reasoning-candidate": "마지막 추론 확인 기준값",
                "initial-observation": "초기 관측 기준값",
            }.get(baseline_kind, "직전 저장 기준값")
            comparison_label = {
                "last-outbox-alert": "마지막 원시 알림 기준 시세와 비교",
                "last-reasoning-candidate": "마지막 TypeDB 후속 확인 기준 시세와 비교",
                "initial-observation": "초기 관측 기준 시세와 비교",
            }.get(baseline_kind, "직전 저장 시세와 비교")
            delivery_deferred = not immediate_delivery
            immediate_threshold = self.market_observation_immediate_price_change_threshold()
            raw_delivery_threshold = self.market_observation_raw_delivery_threshold()
            lines = [
                "현재가: " + price_money(current_price, currency),
                baseline_label + ": " + price_money(baseline_price, currency),
                "기준 대비: " + signed_pct(change_pct),
                "직전 저장값: " + price_money(previous_price, currency),
                "관측 기준: " + comparison_label,
                "판단 상태: 가격 변동 사실만 알림 · 매수·매도 판단 없음",
                "후속 처리: TypeDB 추론이 최신 ABox에서 완료되면 관계 분석 결과를 별도 인사이트로 발송",
            ]
            if source:
                lines.insert(3, "시세 출처: " + source)
            criteria_setting = (
                "기준 시세 대비 절대 " + compact_number(threshold)
                + "% 이상 누적 변동 시 TypeDB 후속 확인을 요청"
                if delivery_deferred
                else "기준 시세 대비 절대 " + compact_number(raw_delivery_threshold)
                + "% 이상 누적 변동 시 시세 변동 알림을 즉시 발송"
            )
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "WATCH",
                MARKET_OBSERVATION,
                ":".join([snapshot.account_id, "market-observation", symbol, direction]),
                item.name or symbol,
                lines,
                symbol,
                criteria=self.criteria(
                    criteria_setting,
                    baseline_label + " " + price_money(baseline_price, currency)
                    + " → 현재 " + price_money(current_price, currency)
                    + " (" + signed_pct(change_pct) + ") · 투자 판단 없음",
                ),
                metadata={
                    "marketObservation": {
                        "observationOnly": True,
                        "previousPrice": previous_price,
                        "baselinePrice": baseline_price,
                        "baselineKind": baseline_kind,
                        "currentPrice": current_price,
                        "changePct": round(change_pct, 4),
                        "reasoningBaselinePrice": reasoning_baseline_price,
                        "reasoningChangePct": round(reasoning_change_pct, 4),
                        "pendingReasoningPrice": pending_reasoning_price,
                        "pendingChangePct": round(pending_change_pct, 4),
                        "outboxBaselinePrice": delivery_baseline_price,
                        "outboxChangePct": round(delivery_change_pct, 4),
                        "initialPrice": initial_baseline_price,
                        "thresholdPct": threshold,
                        "deliveryThresholdPct": raw_delivery_threshold if immediate_delivery else threshold,
                        "immediateThresholdPct": immediate_threshold,
                        "rawDeliveryMode": self.market_observation_raw_delivery_mode(),
                        "direction": direction,
                        "currency": currency,
                        "source": source,
                    },
                    "observationOnly": True,
                    "investmentJudgement": False,
                    "deliveryDeferred": delivery_deferred,
                    "deliveryMode": "typedb-first" if delivery_deferred else "deterministic-outbox-before-typedb",
                    "reasoningFollowup": "queued-from-persisted-snapshot",
                },
            ))
        return events

    def observation_followup_events(
        self,
        snapshot: AccountSnapshot,
        reasoning_context: Dict[str, object] = None,
    ) -> List[AlertEvent]:
        """Create one graph-backed insight source after a quote follow-up.

        A material quote observation is intentionally not a buy/sell signal. Once the
        corresponding TypeDB generation is aligned, this source lets the user
        see that the relation analysis completed even when its action state
        remained unchanged.
        """

        context = dict(reasoning_context or {}) if isinstance(reasoning_context, dict) else {}
        followup_symbols = []
        for value in context.get("observationFollowupSymbols") or []:
            symbol = str(value or "").upper().strip()
            if symbol and symbol not in followup_symbols:
                followup_symbols.append(symbol)
        if not followup_symbols:
            return []

        positions: Dict[str, Position] = {}
        for group in (snapshot.positions or [], snapshot.watchlist or []):
            for item in group:
                if item.is_cash():
                    continue
                symbol = str(item.symbol or "").upper().strip()
                if symbol and symbol not in positions:
                    positions[symbol] = item
        relation_contexts = relation_contexts_from_snapshot(
            snapshot,
            getattr(self.strategy_model, "settings", {}) if self.strategy_model else {},
        )
        decisions = {
            str(item.symbol or "").upper().strip(): item.to_dict()
            for item in snapshot.decisions or []
            if str(item.symbol or "").strip()
        }
        source_ids = [
            str(value or "").strip()
            for value in (context.get("sourceEventIds") or context.get("requestEventIds") or [])
            if str(value or "").strip()
        ]
        source_marker = source_ids[0][:120] if source_ids else str(snapshot.generated_at or "current")[:120]
        events: List[AlertEvent] = []
        for symbol in followup_symbols:
            position = positions.get(symbol)
            relation_context = relation_contexts.get(symbol)
            if not position or not is_graph_backed_relation_context(relation_context):
                continue
            relation_decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
            review_level = str(relation_context.get("reviewLevel") or relation_decision.get("reviewLevel") or "observe").strip().lower()
            data_state = str(relation_context.get("dataState") or relation_decision.get("dataState") or "partial").strip().lower()
            change_state = str(relation_context.get("changeState") or relation_decision.get("changeState") or "unchanged").strip().lower()
            if not data_state_is_usable(data_state):
                continue
            decision_state = decisions.get(symbol, {})
            decision_label = str(
                relation_decision.get("label")
                or decision_state.get("decision")
                or "관계 판단 유지"
            ).strip()
            relation_lines = relation_rule_context_summary_lines(relation_context)
            position_context = position.to_dict()
            prompt_context = self.prompt_context_from_decision(decision_state)
            if not prompt_context and isinstance(relation_context.get("promptContext"), dict):
                prompt_context = dict(relation_context.get("promptContext") or {})
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "WATCH",
                ONTOLOGY_OBSERVATION_FOLLOWUP,
                ":".join([snapshot.account_id, "observation-followup", symbol, source_marker]),
                position.name or symbol,
                [
                    "상태: 시세 변화 후 TypeDB 관계 분석 완료",
                    "관계 판단: " + decision_label + " · " + REVIEW_LEVEL_LABELS.get(review_level, REVIEW_LEVEL_LABELS["observe"]),
                    "판단 변화: " + CHANGE_STATE_LABELS.get(change_state, CHANGE_STATE_LABELS["unchanged"]),
                    self.current_price_line(position_context),
                    self.flow_context_line(position_context),
                    self.investor_context_line(position_context),
                    self.trend_context_line(position_context),
                    *relation_lines,
                ],
                symbol,
                criteria=self.criteria(
                    "시세 관측 후속 확인의 최신 ABox TypeDB 관계 분석이 완료될 때",
                    decision_label
                    + " · " + REVIEW_LEVEL_LABELS.get(review_level, REVIEW_LEVEL_LABELS["observe"])
                    + " · " + CHANGE_STATE_LABELS.get(change_state, CHANGE_STATE_LABELS["unchanged"]),
                ),
                metadata={
                    "observationFollowup": True,
                    "observationFollowupSourceEventIds": source_ids[:8],
                    "reviewLevel": review_level,
                    "dataState": data_state,
                    "changeState": change_state,
                    "conflictState": str(relation_context.get("conflictState") or relation_decision.get("conflictState") or "context-only"),
                    "ontologyRelationContext": relation_context,
                    "ontologyPromptContext": prompt_context,
                    "ontologyOpinion": self.ontology_opinion_from_decision(decision_state),
                    "ontologyWorldview": self.ontology_worldview_from_decision(decision_state),
                    "activeInvestmentOpinion": self.active_investment_opinion_from_decision(decision_state),
                    "ontologyReviewContext": self.ai_context_from_decision(decision_state),
                },
            ))
        return events

    def holding_snapshot_line(self, item: Position) -> str:
        currency = item.currency or ("KRW" if str(item.market or "").upper() in {"KR", "KOSPI", "KOSDAQ"} else "")
        current = price_money(item.current_price, currency)
        average = price_money(item.average_price, currency)
        value = self.holding_snapshot_value_text(item)
        return (
            (item.name or item.symbol)
            + " / "
            + item.symbol
            + ": 현재가 "
            + current
            + ", 평균매입가 "
            + average
            + ", 수익률 "
            + signed_pct(item.profit_loss_rate)
            + ", 보유 "
            + compact_number(item.quantity)
            + "주, 평가금액 "
            + value
        )

    def holding_snapshot_value_text(self, item: Position) -> str:
        currency = item.currency or ""
        original = money(item.market_value, currency) if currency and currency != "KRW" else ""
        krw = money(item.market_value_krw or item.market_value, "KRW")
        if original and item.market_value_krw:
            return original + " (약 " + krw + ")"
        return original or krw

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
        projection = ontology_projection_from_metadata(snapshot.metadata if isinstance(snapshot.metadata, dict) else {})
        if projection_waits_for_reasoning_worker(projection):
            return {
                "missing": False,
                "pending": True,
                "reasonCode": "reasoningWorkerPending",
                "reason": str((projection or {}).get("reason") or "전용 온톨로지 추론 워커 처리 대기"),
                "status": str((projection or {}).get("status") or ""),
                "positionCount": len(positions),
            }

        reason_code, reason, inference_status = self.ontology_inference_missing_reason(snapshot)
        if not reason_code:
            return {
                "missing": False,
                "positionCount": len(positions),
                "noMatch": bool(inference_status.get("noMatch")),
                "status": str(inference_status.get("status") or ""),
                "reason": str(inference_status.get("reason") or ""),
                "inferenceStatus": dict(inference_status or {}),
            }
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
            "확인 행동 TypeDB 연결, 네이티브 규칙 저장 상태, 온톨로지 추론 워커 점검",
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
            "nativeTypeDbReasoningCompleted": bool(
                (inference or {}).get("nativeTypeDbReasoningCompleted")
                or (inference or {}).get("typedbNativeRuleEvaluationCompleted")
            ) if isinstance(inference, dict) else False,
            "nativeInferenceOutcome": str((inference or {}).get("nativeInferenceOutcome") or "") if isinstance(inference, dict) else "",
            "generationAligned": bool((inference or {}).get("generationAligned")) if isinstance(inference, dict) else False,
            "sourceAboxSnapshotId": str((inference or {}).get("sourceAboxSnapshotId") or "") if isinstance(inference, dict) else "",
            "targetSymbols": list((inference or {}).get("targetSymbols") or [])[:80] if isinstance(inference, dict) else [],
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
        if projection_waits_for_reasoning_worker(projection):
            common.update({
                "pending": True,
                "pendingReason": str(projection.get("reason") or "전용 온톨로지 추론 워커 처리 대기"),
            })
            return "", "", common
        if projection_status and projection_status not in {
            "ok",
            "partial",
            "unchanged-material-facts",
            "unchanged-material-facts-reasoning-retry",
        }:
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
        verified_no_match = (
            str(status or "").lower() == "empty"
            and bool(common.get("nativeTypeDbReasoningCompleted"))
            and bool(common.get("generationAligned"))
            and bool(common.get("sourceAboxSnapshotId"))
        )
        if verified_no_match:
            common.update({
                "noMatch": True,
                "reason": "현재 ABox에서 TypeDB 네이티브 규칙을 모두 평가했지만 투자 판단 관계가 성립하지 않았습니다.",
            })
            return "", "", common
        if common["ruleboxExecutionStatus"] and common["ruleboxExecutionStatus"].lower() not in {"ok", "partial"}:
            reason = "TypeDB native rule 실행 실패"
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
            return "nativeReasoningMissing", graph_store_label(graph_store) + " native rule materialization 관계가 아직 없습니다", common
        if not graph_reasoning_used and not relations and not traces:
            return "nativeReasoningMissing", graph_store_label(graph_store) + " 추론 관계가 아직 없습니다", common
        if not relations and not traces:
            return "emptyInferenceBox", graph_store_label(graph_store) + " InferenceBox 관계와 근거가 0개입니다", common
        return "positionInferenceMissing", "보유 종목과 연결된 " + graph_store_label(graph_store) + " InferenceBox 관계가 없습니다", common

    def holding_timing_events(self, snapshot: AccountSnapshot) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        positions = {item.symbol.upper(): item.to_dict() for item in snapshot.positions if not item.is_cash()}
        for item in snapshot.decisions:
            position = positions.get(item.symbol.upper()) or item.to_dict()
            decision_state = item.to_dict()
            relation_context = self.relation_context_from_decision(decision_state)
            if not is_graph_backed_relation_context(relation_context):
                continue
            relation_decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
            review_level = str(relation_context.get("reviewLevel") or relation_decision.get("reviewLevel") or item.review_level or "normal")
            data_state = str(relation_context.get("dataState") or relation_decision.get("dataState") or item.data_state or "partial")
            change_state = str(relation_context.get("changeState") or relation_decision.get("changeState") or item.change_state or "unchanged")
            if not data_state_is_usable(data_state):
                continue
            meaningful_change = change_state in {"new-condition", "improving", "worsening", "direction-changed", "new-evidence"}
            if not (review_level_at_least(review_level, "check") or meaningful_change):
                continue
            decision_phrase = self.decision_state_phrase(item.decision, review_level)
            prompt_context = self.prompt_context_from_decision(decision_state)
            relation_lines = self.relation_context_lines(decision_state)
            ontology_lines = self.ontology_context_lines(decision_state)
            active_lines = self.active_investment_opinion_lines(decision_state)
            event_key_parts = [snapshot.account_id, "timing", item.symbol, item.decision, change_state]
            news_event_suffix = relation_news_event_key_suffix(relation_context)
            if news_event_suffix:
                event_key_parts.append(news_event_suffix)
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "ALERT" if review_level in {"act", "immediate"} and item.tone in {"danger", "caution"} else "WATCH",
                "holdingTiming",
                ":".join(event_key_parts),
                item.name,
                ["상태 " + decision_phrase, *self.holding_price_lines(position, snapshot.portfolio, positions.values()), self.flow_context_line(position), self.investor_context_line(position), self.trend_context_line(position), self.holding_action_line(item.decision, item.profit_loss_rate)] + relation_lines + ontology_lines + active_lines,
                item.symbol,
                criteria=self.criteria(
                    "TypeDB 관계 규칙이 확인·대응 상태가 되거나 손익·뉴스·공시 등 의미 있는 변화가 새로 확인될 때",
                    "상태 " + decision_phrase
                    + ", 자료 " + DATA_STATE_LABELS.get(data_state, DATA_STATE_LABELS["partial"])
                    + ", 변화 " + CHANGE_STATE_LABELS.get(change_state, CHANGE_STATE_LABELS["unchanged"])
                    + ", 수익률 " + signed_pct(item.profit_loss_rate)
                    + (", " + " · ".join(relation_lines[:2]) if relation_lines else ""),
                ),
                metadata={
                    "holdingDecision": item.decision,
                    "holdingDecisionBasis": item.decision_basis,
                    "reviewLevel": review_level,
                    "dataState": data_state,
                    "changeState": change_state,
                    "conflictState": str(relation_context.get("conflictState") or item.conflict_state or "context-only"),
                    "validationState": str(relation_context.get("validationState") or item.validation_state or "conditional"),
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
