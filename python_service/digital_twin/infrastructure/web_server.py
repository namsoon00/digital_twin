import base64
import csv
import errno
import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import select
import socket
import subprocess
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List

from ..application.account_service import AccountApplicationService
from ..application.notification_replay_service import NotificationReplayService
from ..application.ontology_diagnostics_service import OntologyDiagnosticsService
from ..application.symbol_universe_service import DEFAULT_SYMBOL_SEEDS, seed_symbol
from ..domain.events import (
    APP_ITEM_REMOVED,
    APP_ITEM_UPDATED,
    APP_MEMORY_RECORDED,
    APP_MEMORY_REMOVED,
    APP_MEMORY_UPDATED,
    APP_PROFILE_UPDATED,
    CHAT_MESSAGE_APPENDED,
    MONITORING_ALERTS_DETECTED,
    MONITORING_CYCLE_COMPLETED,
    MONITORING_SNAPSHOT_COLLECTED,
    NOTIFICATION_JOB_QUEUED,
    NOTIFICATION_RULE_UPDATED,
    NOTIFICATION_TEMPLATE_UPDATED,
    NOTIFICATION_TEST_REQUESTED,
    SETTINGS_UPDATED,
    SYMBOL_UNIVERSE_REFRESHED,
    DomainEvent,
)
from ..domain.message_types import (
    DEFAULT_ALERT_RULES,
    DEFAULT_CADENCE,
    MESSAGE_TYPE_EMOJIS,
    public_message_catalog,
    user_managed_notification_types,
    visible_notification_template_types,
)
from ..domain.market_hours import DEFAULT_MARKET_HOUR_SESSIONS
from ..domain.monitoring import RealtimeMonitor
from ..domain.notification_rules import CONDITION_TYPE_LABELS, NotificationRuleConfig
from ..domain.notifications import NotificationJob
from ..domain.notification_templates import DEFAULT_NOTIFICATION_TEMPLATES, MESSAGE_TYPE_LABELS, TRIGGER_SUMMARIES, NotificationTemplate, alert_context, template_variables
from ..domain.ontology_inference_ledger import inference_trace_ledger_payload
from ..domain.investment_ubiquitous_language import (
    LANGUAGE_REGISTRY_SETTING_KEY,
    audit_user_facing_investment_text,
    investment_language_registry,
    normalize_investment_language_registry,
    propose_investment_language_changes,
    validate_investment_language_registry,
)
from ..domain.parsing import parse_assignments
from ..domain.portfolio import utc_now_iso
from ..domain.symbol_universe import symbol_search_symbol_candidates
from ..infrastructure.event_bus import EventBus, JsonEventLog, default_event_bus
from ..infrastructure.external_signal_utils import ExternalCircuitOpen, ExternalRateLimited, external_call_target, guarded_external_call
from ..infrastructure.mock_market import mock_market_payload, mock_market_scenario_list
from ..infrastructure.ontology_graph_store import ontology_repository_from_settings
from ..infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
from ..infrastructure import operational_store as stores
from ..infrastructure.operational_error_reporting import operational_error_reporter, report_runtime_error
from ..infrastructure.service_factory import (
    build_investment_calendar_candidate_service,
    build_investment_calendar_research_service,
    build_investment_calendar_runner,
    build_investment_calendar_service,
    build_investment_strategy_proposal_service,
    build_investment_brain_service,
    build_notification_queue_runner,
    build_official_calendar_sync_service,
    build_ontology_lab_service,
    build_rule_change_candidate_service,
    build_symbol_universe_service,
    flow_lens_snapshot,
    investment_analysis_snapshot,
)
from ..infrastructure.settings import ROOT_DIR, read_json, runtime_settings, save_runtime_settings, write_private_json
from ..infrastructure.toss_snapshots import build_snapshot


PUBLIC_DIR = ROOT_DIR / "public"
LOCAL_APP_STORE_PATH = ROOT_DIR / "data" / "store.json"
MEMORY_CATEGORIES = ["identity", "preference", "finance", "travel", "asset", "schedule", "work", "other"]
DOMAIN_TYPES = ["stock", "trip", "asset", "schedule", "task", "note"]
MAX_BODY_BYTES = 1024 * 1024
WEB_PROXY_API_GUARD_STATE: Dict[str, object] = {}

NON_CADENCE_MESSAGE_GUIDES = {
    "modelReview": "판단 변화 알림이 발생하면 별도 워커가 충분히 분석한 뒤 보냅니다.",
    "workHandoff": "작업이 끝나고 커밋, 검증, 푸시, 재시작 결과를 공유할 때 보냅니다.",
    "notification": "사용자가 직접 만든 일반 알림이나 시스템 안내가 있을 때 보냅니다.",
    "default": "타입별 템플릿이 없을 때 fallback으로 사용됩니다.",
}


def now() -> str:
    return utc_now_iso()


WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def websocket_accept_key(key: str) -> str:
    digest = hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def websocket_frame(payload, opcode: int = 0x1) -> bytes:
    raw = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
    length = len(raw)
    header = bytearray([0x80 | opcode])
    if length < 126:
        header.append(length)
    elif length <= 65535:
        header.extend([126, (length >> 8) & 0xFF, length & 0xFF])
    else:
        header.append(127)
        header.extend(length.to_bytes(8, "big"))
    return bytes(header) + raw


def socket_read_exact(sock, length: int) -> bytes:
    chunks = []
    remaining = length
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            return b""
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_websocket_frame(sock):
    header = socket_read_exact(sock, 2)
    if len(header) < 2:
        return 0x8, b""
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F
    masked = bool(header[1] & 0x80)
    if length == 126:
        length = int.from_bytes(socket_read_exact(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(socket_read_exact(sock, 8), "big")
    mask = socket_read_exact(sock, 4) if masked else b""
    payload = socket_read_exact(sock, length) if length else b""
    if masked and mask:
        payload = bytes(payload[index] ^ mask[index % 4] for index in range(len(payload)))
    return opcode, payload


class RealtimeHub:
    def __init__(self):
        self.clients = set()
        self.recent_events: List[DomainEvent] = []
        self.lock = threading.Lock()

    def add(self, client) -> None:
        with self.lock:
            self.clients.add(client)

    def remove(self, client) -> None:
        with self.lock:
            self.clients.discard(client)

    def status(self) -> Dict[str, object]:
        with self.lock:
            connected = len(self.clients)
        return {"connectedClients": connected}

    def remember_event(self, event: DomainEvent) -> None:
        with self.lock:
            self.recent_events.insert(0, event)
            self.recent_events = self.recent_events[:50]

    def latest_events(self, limit: int = 12) -> List[DomainEvent]:
        with self.lock:
            return list(self.recent_events[:limit])

    def send(self, client, payload, opcode: int = 0x1) -> bool:
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, sort_keys=True)
        try:
            client.sendall(websocket_frame(body, opcode=opcode))
            return True
        except OSError:
            self.remove(client)
            return False

    def broadcast(self, event_type: str, payload: Dict[str, object] = None) -> None:
        message = {
            "type": event_type,
            "payload": dict(payload or {}),
            "occurredAt": now(),
        }
        with self.lock:
            clients = list(self.clients)
        for client in clients:
            self.send(client, message)

    def broadcast_event(self, event: DomainEvent) -> None:
        self.remember_event(event)
        self.broadcast(event.name, {"event": event.to_dict(), **dict(event.payload or {})})


REALTIME_HUB = RealtimeHub()


class RealtimeEventBridge:
    def __init__(self):
        try:
            self.inner = default_event_bus()
        except Exception:  # noqa: BLE001 - domain events should fall back when optional MySQL is offline.
            self.inner = EventBus()
            self.inner.subscribe_all(JsonEventLog().handle)

    def publish(self, event: DomainEvent) -> None:
        self.inner.publish(event)
        REALTIME_HUB.broadcast_event(event)


def publish_domain_event(event: DomainEvent) -> DomainEvent:
    RealtimeEventBridge().publish(event)
    return event


def new_domain_event(name: str, aggregate_id: str, payload: Dict[str, object] = None) -> DomainEvent:
    return publish_domain_event(DomainEvent(name=name, aggregate_id=aggregate_id, payload=dict(payload or {})))


def realtime_event_payload(event: DomainEvent) -> Dict[str, object]:
    return {
        "name": event.name,
        "eventId": event.event_id,
        "aggregateId": event.aggregate_id,
        "occurredAt": event.occurred_at,
        "payload": event.payload,
    }


def realtime_status_payload() -> Dict[str, object]:
    store_warning = ""
    try:
        event_log = stores.event_log()
        counts = event_log.event_counts()
        latest_by_name = event_log.latest_events_by_name([
            MONITORING_CYCLE_COMPLETED,
            MONITORING_ALERTS_DETECTED,
            MONITORING_SNAPSHOT_COLLECTED,
        ])
        latest_events = event_log.latest_events(limit=12)
    except Exception as error:  # noqa: BLE001 - status API should degrade when optional MySQL is offline.
        store_warning = str(error)[:240]
        latest_events = REALTIME_HUB.latest_events(limit=12)
        counts = {}
        for event in latest_events:
            counts[event.name] = counts.get(event.name, 0) + 1
        latest_by_name = {}
    monitoring = {}
    if latest_by_name.get(MONITORING_CYCLE_COMPLETED):
        monitoring["cycle"] = realtime_event_payload(latest_by_name[MONITORING_CYCLE_COMPLETED])
    if latest_by_name.get(MONITORING_ALERTS_DETECTED):
        monitoring["alerts"] = realtime_event_payload(latest_by_name[MONITORING_ALERTS_DETECTED])
    if latest_by_name.get(MONITORING_SNAPSHOT_COLLECTED):
        monitoring["snapshot"] = realtime_event_payload(latest_by_name[MONITORING_SNAPSHOT_COLLECTED])
    try:
        notification_jobs = notification_queue_store().summary()
    except Exception as error:  # noqa: BLE001 - notification queue may share the same optional MySQL backend.
        store_warning = store_warning or str(error)[:240]
        notification_jobs = {"pending": 0, "processing": 0, "done": 0, "suppressed": 0, "failed": 0}
    return {
        **REALTIME_HUB.status(),
        "events": counts,
        "latestEvents": [realtime_event_payload(event) for event in latest_events],
        "monitoring": monitoring,
        "notificationJobs": notification_jobs,
        "storeWarning": store_warning,
    }


def new_id(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:16]


def configured(value) -> str:
    return str(value or "").strip()


def request_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def default_store() -> Dict[str, object]:
    stamped = now()
    return {
        "version": 1,
        "profile": {
            "ownerName": "Namsoon",
            "assistantName": "Twin",
            "preferredLanguage": "한국어",
            "answerStyle": "핵심부터 말하고, 필요한 근거와 실행 단계를 짧게 정리한다.",
            "tone": "담백하고 실무적인 말투. 과장하지 않는다.",
            "decisionStyle": "선택지를 비교하고 리스크와 다음 행동을 분리해서 판단한다.",
            "riskStyle": "투자와 자산 판단은 보수적으로 접근하고, 확신이 낮으면 추가 확인을 요구한다.",
            "financePolicy": "주식은 매수/매도 지시가 아니라 관찰 포인트, 리스크, 체크리스트 중심으로 돕는다.",
            "travelPolicy": "여행은 예산, 이동 동선, 피로도, 예약 마감일을 함께 본다.",
            "schedulePolicy": "일정은 오늘 처리할 것, 미룰 것, 위임할 것을 나눠서 관리한다.",
            "assetPolicy": "자산은 계좌번호나 인증 정보 없이 요약 단위로 기록하고, 목표와 현금흐름 중심으로 관리한다.",
            "boundaries": "법률, 세무, 투자 판단은 최종 결정을 대신하지 않는다. 민감한 정보는 저장하지 않는다.",
        },
        "memories": [
            {
                "id": "mem-default-1",
                "content": "사용자는 한국어로 명확하고 실용적인 답변을 선호한다.",
                "category": "preference",
                "status": "approved",
                "importance": 4,
                "source": "초기 설정",
                "createdAt": stamped,
                "updatedAt": stamped,
            },
            {
                "id": "mem-default-2",
                "content": "비서는 주식, 여행 계획, 자산관리, 스케줄 관리를 우선 도메인으로 다룬다.",
                "category": "identity",
                "status": "approved",
                "importance": 5,
                "source": "초기 설정",
                "createdAt": stamped,
                "updatedAt": stamped,
            },
        ],
        "items": [
            {
                "id": "item-default-1",
                "type": "task",
                "title": "비서에게 나의 투자 기준 입력",
                "status": "open",
                "date": "",
                "notes": "예: 장기 투자, 단기 매매 회피, 현금 비중 선호, 관심 섹터",
                "fields": {},
                "createdAt": stamped,
                "updatedAt": stamped,
            },
            {
                "id": "item-default-2",
                "type": "schedule",
                "title": "이번 주 일정 정리",
                "status": "planned",
                "date": "",
                "notes": "중요한 회의, 마감일, 개인 약속을 입력한다.",
                "fields": {},
                "createdAt": stamped,
                "updatedAt": stamped,
            },
        ],
        "messages": [
            {
                "id": "msg-default-1",
                "role": "assistant",
                "content": "무엇부터 정리할까요? 주식 관심 목록, 여행 계획, 자산 현황, 이번 주 일정 중 하나를 말해주면 바로 기록하고 다음 행동으로 나누겠습니다.",
                "createdAt": stamped,
            }
        ],
    }


def app_store():
    return stores.app_store()


def read_store() -> Dict[str, object]:
    fallback = default_store()
    try:
        parsed = app_store().load()
    except Exception as error:  # noqa: BLE001 - bootstrap must remain available when optional MySQL is offline.
        parsed = read_json(LOCAL_APP_STORE_PATH, {})
        if isinstance(parsed, dict):
            parsed.setdefault("metadata", {})
            parsed["metadata"]["operationalStoreWarning"] = str(error)[:240]
    if not parsed:
        parsed = fallback
        try:
            app_store().replace(parsed)
        except Exception:  # noqa: BLE001 - local fallback keeps the web console readable.
            write_private_json(LOCAL_APP_STORE_PATH, parsed)
    return {
        **fallback,
        **parsed,
        "profile": {**fallback["profile"], **dict(parsed.get("profile") or {})},
        "memories": parsed.get("memories") if isinstance(parsed.get("memories"), list) else [],
        "items": parsed.get("items") if isinstance(parsed.get("items"), list) else [],
        "messages": parsed.get("messages") if isinstance(parsed.get("messages"), list) else [],
    }


def save_store(mutator):
    store = read_store()
    mutator(store)
    try:
        app_store().replace(store)
    except Exception:  # noqa: BLE001 - local fallback keeps manual notes usable without MySQL.
        write_private_json(LOCAL_APP_STORE_PATH, store)
    return store


def snapshot_payload() -> Dict[str, object]:
    store = read_store()
    return {
        "profile": store["profile"],
        "memories": store["memories"],
        "items": store["items"],
        "messages": store["messages"],
    }


def settings_status_payload() -> Dict[str, object]:
    settings = runtime_settings()
    public_keys = [
        "appTheme",
        "watchlistSymbols",
        "mysqlUrl",
        "mysqlHost",
        "mysqlPort",
        "mysqlDatabase",
        "mysqlUser",
        "mysqlUnixSocket",
        "mysqlTablePartitioning",
        "tossApiBaseUrl",
        "kisEnv",
        "kisBaseUrl",
        "kisWebSocketUrl",
        "kisRealtimeWebSocketEnabled",
        "kisRealtimeWebSocketSymbols",
        "kisRealtimeWebSocketIncludeConfiguredInReasoning",
        "kisRealtimeWebSocketMaxSymbols",
        "kisRealtimeWebSocketCollectSeconds",
        "kisRealtimeWebSocketEventIntervalSeconds",
        "kisRealtimeWebSocketReconnectSeconds",
        "kisRealtimeWebSocketTimeoutSeconds",
        "kisMarketSignalsEnabled",
        "kisMarketSignalMaxSymbols",
        "kisMarketSignalCacheMinutes",
        "kisMarketSignalGapSeconds",
        "kisMarketSignalPreferLiveDuringMarketHours",
        "kisMarketSignalLiveRefreshSeconds",
        "kisMarketSignalUnchangedStaleCount",
        "notifyProvider",
        "notifyLinkUrl",
        "fxRates",
        "fairValueFormula",
        "ontologyRelationRules",
        "aiPromptTemplates",
        "aiPromptPolicy",
        "notificationAiGateEnabled",
        "notificationAiGateMessageTypes",
        "notificationAiUseCodex",
        "notificationAiModel",
        "notificationAiTimeoutSeconds",
        "investmentBrainMinimumHypothesisCount",
        "investmentBrainMaximumHypothesisCount",
        "investmentBrainInferenceBoxLimit",
        "investmentBrainResearchEnabled",
        "investmentBrainResearchMaxRounds",
        "investmentBrainResearchEvidenceLimit",
        "investmentBrainResearchMinimumVerifiedCount",
        "investmentBrainResearchMinimumSourceTrustState",
        "investmentBrainResearchCooldownMinutes",
        "investmentBrainNotificationResearchEnabled",
        "investmentBrainNovelHypothesisAiEnabled",
        "investmentBrainNovelHypothesisAiTimeoutSeconds",
        "modelName",
        "modelHypothesis",
        "modelTimingScenario",
        "modelTimingSymbols",
        "operatorReasoningReportEnabled",
        "alertRules",
        "alertThresholds",
        "relationRuleThresholds",
        "alertCadenceMinutes",
        "ontologyTypeDbEnabled",
        "ontologyReasoningEnabled",
        "ontologyReasoningIntervalSeconds",
        "ontologyReasoningBatchSize",
        "ontologyReasoningMaxSymbolsPerRun",
        "typedbNativeRuleTargetSymbolLimit",
        "ontologyReasoningMinIntervalSeconds",
        "ontologyReasoningUrgentMinIntervalSeconds",
        "ontologyReasoningProjectionRetrySeconds",
        "ontologyReasoningUrgentReviewLevels",
        "temporalWindowPeriods",
        "temporalWindowHistoryLimit",
        "ontologyLabAutoApplyEnabled",
        "ontologyLabAutoApplyValidationStates",
        "ontologyLabAutoApplyNeedsReviewEnabled",
        "ontologyLabNotifyEnabled",
        "ontologyRuleCandidateAiEnabled",
        "ontologyRuleCandidateAiUseCodex",
        "ontologyRuleCandidateAiCommand",
        "ontologyRuleCandidateAiTimeoutSeconds",
        "ontologyRuleCandidateAiIntervalMinutes",
        "ontologyRuleCandidateAiMaxCandidates",
        "materialityGateEnabled",
        "marketMaterialityPriceChangePct",
        "marketMaterialityTrendDistancePct",
        "marketMaterialityVolumeRatio",
        "typedbAddress",
        "typedbUser",
        "typedbDatabase",
        "typedbTlsEnabled",
        "typedbTimeoutSeconds",
        "typedbRetryCount",
        "symbolUniverseMaxAgeHours",
        "typedbInferenceGenerationKeepCount",
        "typedbAutoResetEnabled",
        "typedbAgeResetEnabled",
        "typedbDataRetentionHours",
        "typedbDataMaxSizeMb",
        "externalApiFetchIntervalMinutes",
        "externalAlphaEnabled",
        "externalAlphaFundamentalsEnabled",
        "externalAlphaFundamentalsMaxSymbols",
        "externalYFinanceEnabled",
        "externalYFinanceMaxSymbols",
        "externalYFinanceHistoryPeriod",
        "externalYFinanceHistoryInterval",
        "externalYFinanceHistoryRows",
        "externalYFinanceFinancialPeriods",
        "externalYFinanceTabularRows",
        "externalYFinanceOptionExpirations",
        "externalYFinanceOptionsMaxRows",
        "externalYFinanceEarningsLimit",
        "externalYFinanceNewsLimit",
        "externalYFinancePriceMaxAgeMinutes",
        "externalYFinanceOptionsMaxAgeMinutes",
        "externalYFinanceNewsMaxAgeMinutes",
        "externalYFinanceAnalystMaxAgeMinutes",
        "externalYFinanceFundamentalMaxAgeMinutes",
        "externalCoinGeckoEnabled",
        "externalFredEnabled",
        "externalFredSeries",
        "externalCryptoIds",
        "externalAlphaMaxSymbols",
        "externalSecEnabled",
        "externalSecMaxSymbols",
        "externalSecCompanyCiks",
        "externalSecUserAgent",
        "externalDartEnabled",
        "externalDartLookbackDays",
        "externalDartCorpCodes",
        "externalNewsEnabled",
        "externalNewsProvider",
        "externalNewsMaxSymbols",
        "externalNewsLookbackHours",
        "externalResearchEvidenceMaxItems",
        "newsCollectionEnabled",
        "newsCollectionIntervalSeconds",
        "newsCollectionMaxSymbols",
        "newsCollectionLookbackMinutes",
        "newsCollectionPerSymbolLimit",
        "newsCollectionProviders",
        "newsCollectionMinimumRelevanceState",
        "newsDigestMinimumRelevanceState",
        "newsDigestMinimumMaterialityState",
        "newsDigestMinimumNeutralMaterialityState",
        "newsDigestMinimumSourceTrustState",
        "newsCollectionRequireArticleBodyForRss",
        "newsCollectionIncludeWatchlist",
        "newsCollectionIncludeHoldings",
        "newsCollectionRateLimitSeconds",
        "newsEvidenceCleanupEnabled",
        "newsEvidenceMaxAgeMinutes",
        "newsEvidenceCleanupBatchSize",
        "newsEvidenceKeepUndated",
        "newsArticleBodyFailureWarnRate",
        "newsArticleBodyFailureMinimumCount",
        "newsAiAnalysisEnabled",
        "newsAiAnalysisUseCodex",
        "newsAiAnalysisCommand",
        "newsAiAnalysisTimeoutSeconds",
        "investmentCalendarEnabled",
        "investmentCalendarIntervalSeconds",
        "investmentCalendarDefaultWindowDays",
        "investmentCalendarReminderLookbackMinutes",
        "investmentCalendarAutoExtractEnabled",
        "investmentCalendarAutoExtractRegisterUndated",
        "investmentCalendarAutoExtractReviewEnabled",
        "investmentCalendarOfficialMacroSyncEnabled",
        "investmentCalendarOfficialMacroSyncIntervalHours",
        "investmentCalendarOfficialMacroSyncRateLimitSeconds",
        "investmentCalendarOfficialMacroSyncTimeoutSeconds",
        "investmentCalendarBokPolicyDecisionEnabled",
        "investmentCalendarBokPolicyDecisionTimeKst",
        "investmentCalendarBokPolicyDecisionLookaheadYears",
        "dartDisclosureAiAnalysisEnabled",
        "dartDisclosureAiUseCodex",
        "dartDisclosureAiCommand",
        "dartDisclosureAiTimeoutSeconds",
        "notificationQueueIntervalSeconds",
        "notificationQueueBatchSize",
        "notificationSendGapSeconds",
        "notificationProcessingStaleMinutes",
        "monitorAccountQueueEnabled",
        "monitorAccountIntervalSeconds",
        "monitorAccountBatchSize",
        "monitorAccountLockSeconds",
        "marketDataMaxAgeMinutes",
        "marketSignalDataCollectionEnabled",
        "marketSignalDataBatchSize",
        "dataFreshnessEnabled",
        "dataFreshnessDefaultMaxAgeMinutes",
        "dataFreshnessQuoteMaxAgeMinutes",
        "dataFreshnessKisPriceMaxAgeMinutes",
        "dataFreshnessKisMicrostructureMaxAgeMinutes",
        "dataFreshnessKisInvestorMaxAgeMinutes",
        "dataFreshnessExternalMaxAgeMinutes",
        "dataFreshnessExternalEquityMaxAgeMinutes",
        "dataFreshnessExternalCryptoMaxAgeMinutes",
        "dataFreshnessMacroMaxAgeMinutes",
        "dataFreshnessDisclosureMaxAgeMinutes",
        "externalSignalCacheMaxAgeMinutes",
        "aiValuationAutoProposalEnabled",
        "aiValuationCurrentPriceAnchorEnabled",
        "valuationReviewOverrides",
        "aiValuationPreferredParValue",
        "aiValuationPreferredRiskSpreadPct",
        "aiValuationPreferredRequiredYieldPct",
        "aiValuationPreferredMinimumMarginPct",
        "aiValuationBaselineMinimumMarginPct",
    ]
    public = {key: settings.get(key, "") for key in public_keys}
    public.update({
        "tossClientId": "",
        "tossClientSecret": "",
        "tossAccountSeq": "",
        "kisAppKey": "",
        "kisAppSecret": "",
        "telegramBotToken": "",
        "telegramChatId": "",
        "operationsTelegramBotToken": "",
        "operationsTelegramChatId": "",
        "alphaVantageApiKey": "",
        "coingeckoApiKey": "",
        "fredApiKey": "",
        "opendartApiKey": "",
        "typedbPassword": "",
        "mysqlPassword": "",
    })
    for optional_key in ["valuationAssumptions", "marketSignalInputs"]:
        if configured(settings.get(optional_key)):
            public[optional_key] = settings[optional_key]
    return {
        "settings": public,
        "configured": {
            "tossClientId": bool(settings.get("tossClientId")),
            "tossClientSecret": bool(settings.get("tossClientSecret")),
            "tossAccountSeq": bool(settings.get("tossAccountSeq")),
            "kisAppKey": bool(settings.get("kisAppKey")),
            "kisAppSecret": bool(settings.get("kisAppSecret")),
            "telegramBotToken": bool(settings.get("telegramBotToken")),
            "telegramChatId": bool(settings.get("telegramChatId")),
            "operationsTelegramBotToken": bool(settings.get("operationsTelegramBotToken")),
            "operationsTelegramChatId": bool(settings.get("operationsTelegramChatId")),
            "alphaVantageApiKey": bool(settings.get("alphaVantageApiKey")),
            "coingeckoApiKey": bool(settings.get("coingeckoApiKey")),
            "fredApiKey": bool(settings.get("fredApiKey")),
            "opendartApiKey": bool(settings.get("opendartApiKey")),
            "typedbAddress": bool(settings.get("typedbAddress")),
            "typedbPassword": bool(settings.get("typedbPassword")),
            "mysqlPassword": bool(settings.get("mysqlPassword")),
        },
        "locked": bool(configured(os.environ.get("SHARE_TOKEN"))),
    }


def save_settings_payload(payload: Dict[str, object]) -> Dict[str, object]:
    requested = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
    save_runtime_settings(requested if isinstance(requested, dict) else {})
    status = settings_status_payload()
    new_domain_event(
        SETTINGS_UPDATED,
        "runtime",
        {
            "keys": sorted([str(key) for key in (requested or {}).keys()]) if isinstance(requested, dict) else [],
            "configured": status.get("configured") or {},
        },
    )
    return status


def ontology_rulebox_payload() -> Dict[str, object]:
    return ontology_repository_from_settings(runtime_settings()).rulebox_snapshot()


def save_ontology_rulebox_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_repository_from_settings(runtime_settings()).save_rulebox(payload)


def ontology_language_payload() -> Dict[str, object]:
    settings = runtime_settings()
    registry = investment_language_registry(settings)
    validation = validate_investment_language_registry(registry)
    return {
        "registry": registry,
        "validation": {key: value for key, value in validation.items() if key != "registry"},
        "typeDb": {
            "configured": bool(str(settings.get("typedbAddress") or "").strip()),
            "ontologyBox": "LanguageGovernance",
            "projection": "보편언어 사전은 TypeDB 관리 개념으로 저장되며 투자 규칙과 별도로 버전 관리됩니다.",
        },
    }


def save_ontology_language_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    registry_input = body.get("registry") if isinstance(body.get("registry"), dict) else body
    registry = normalize_investment_language_registry(registry_input)
    registry["updatedAt"] = now()
    registry["source"] = "admin-approved"
    validation = validate_investment_language_registry(registry)
    if not validation.get("valid"):
        raise ValueError("보편언어 사전에 오류가 있어 저장하지 않았습니다: " + "; ".join(
            str(item.get("message") or "") for item in validation.get("errors") or []
        ))
    saved_settings = save_runtime_settings({
        LANGUAGE_REGISTRY_SETTING_KEY: json.dumps(registry, ensure_ascii=False, sort_keys=True),
    })
    type_db_sync: Dict[str, object] = {"status": "skipped", "reason": "활성 TypeDB 규칙을 확인하지 못했습니다."}
    repository = ontology_repository_from_settings(saved_settings)
    try:
        rulebox = repository.rulebox_snapshot()
        active_rules = rulebox.get("rules") if isinstance(rulebox.get("rules"), list) else []
        if active_rules:
            type_db_sync = repository.save_rulebox({"rules": active_rules})
        elif not str(saved_settings.get("typedbAddress") or "").strip():
            type_db_sync = {"status": "disabled", "saved": False, "reason": "TypeDB가 설정되지 않아 로컬 사전만 저장했습니다."}
    except Exception as error:  # noqa: BLE001 - the approved registry remains locally recoverable.
        type_db_sync = {"status": "error", "saved": False, "reason": str(error)[:220]}
    result = ontology_language_payload()
    result["saved"] = True
    result["typeDbSync"] = type_db_sync
    new_domain_event(
        SETTINGS_UPDATED,
        "investment-language",
        {
            "keys": [LANGUAGE_REGISTRY_SETTING_KEY],
            "registryVersion": registry.get("version"),
            "termCount": len(registry.get("terms") or []),
            "typeDbStatus": type_db_sync.get("status"),
        },
    )
    return result


def validate_ontology_language_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    registry_input = body.get("registry") if isinstance(body.get("registry"), dict) else body
    validation = validate_investment_language_registry(registry_input)
    return {key: value for key, value in validation.items() if key != "registry"}


def preview_ontology_language_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    settings = runtime_settings()
    if isinstance(body.get("registry"), dict):
        settings = {**settings, LANGUAGE_REGISTRY_SETTING_KEY: body.get("registry")}
    return audit_user_facing_investment_text(
        body.get("text") or "",
        settings,
        str(body.get("level") or "absoluteBeginner"),
    )


def suggest_ontology_language_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    settings = runtime_settings()
    if isinstance(body.get("registry"), dict):
        settings = {**settings, LANGUAGE_REGISTRY_SETTING_KEY: body.get("registry")}
    return propose_investment_language_changes(
        body.get("text") or "",
        settings,
        str(body.get("level") or "absoluteBeginner"),
    )


def run_ontology_rulebox_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_repository_from_settings(runtime_settings()).run_rulebox(payload)


def ontology_diagnostics_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    settings = runtime_settings()
    symbols = [
        item.strip()
        for item in str(first_query(query, "symbols") or first_query(query, "symbol") or "").split(",")
        if item.strip()
    ]
    limit = max(1, min(500, int(first_query(query, "limit") or 80)))
    return OntologyDiagnosticsService(
        ontology_repository=ontology_repository_from_settings(settings),
        settings=settings,
        event_log=stores.event_log(settings),
        notification_queue=stores.notification_job_store(settings),
        strategy_proposal_service=build_investment_strategy_proposal_service(settings),
        decision_episode_store=stores.investment_decision_episode_store(settings),
    ).status(symbols=symbols, limit=limit)


def ontology_inference_ledger_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    settings = runtime_settings()
    repo = ontology_repository_from_settings(settings)
    symbols = ontology_audit_symbols(query)
    limit = safe_int(first_query(query, "limit"), 80, 1, 300)
    try:
        rulebox = repo.rulebox_snapshot() if hasattr(repo, "rulebox_snapshot") else {}
    except Exception as error:  # noqa: BLE001 - ledger can still expose raw InferenceBox rows.
        rulebox = {"status": "error", "reason": str(error)[:220], "rules": []}
    try:
        inferencebox = repo.inferencebox_snapshot(symbols=symbols, limit=limit) if hasattr(repo, "inferencebox_snapshot") else {}
    except Exception as error:  # noqa: BLE001 - return a structured diagnostic payload.
        inferencebox = {
            "status": "error",
            "reason": str(error)[:220],
            "graphStore": getattr(repo, "store_key", "typedb"),
            "source": "typedbInferenceBox",
            "entities": [],
            "relations": [],
            "traces": [],
        }
    payload = inference_trace_ledger_payload(inferencebox, rulebox=rulebox, symbols=symbols, limit=limit)
    payload["ruleboxStatus"] = rulebox.get("status")
    payload["ruleboxReason"] = rulebox.get("reason")
    return payload


ONTOLOGY_AUDIT_BOXES = ["TBox", "ABox", "RuleBox", "RuleBoxGovernance", "LanguageGovernance", "InferenceBox"]
ONTOLOGY_AUDIT_SECTION_LABELS = {
    "tbox": ("TBox", "스키마와 관계 타입"),
    "abox": ("ABox", "현재 실체 데이터"),
    "rulebox": ("RuleBox", "운영 규칙과 후보"),
    "inferencebox": ("InferenceBox", "세대별 추론 결과"),
    "language": ("LanguageGovernance", "보편언어 사전과 승인 상태"),
    "evidence": ("Evidence Trace", "근거, 믿음, 의견, 실행 계획"),
    "sync": ("TypeDB Sync", "동기화와 저장소 상태"),
}
ONTOLOGY_AUDIT_EVIDENCE_KINDS = {
    "evidence",
    "research-evidence",
    "belief",
    "investment-opinion",
    "opinion",
    "active-investment-opinion",
    "execution-plan",
    "reasoning-card",
    "inference-trace",
    "insight",
    "data-quality",
    "data-freshness",
    "provenance",
    "source-reliability",
    "missing-data",
}


def safe_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def ontology_audit_symbols(query: Dict[str, List[str]]) -> List[str]:
    raw = first_query(query, "symbols") or first_query(query, "symbol")
    return [item.strip().upper() for item in str(raw or "").split(",") if item.strip()]


def ontology_audit_row_text(row: Dict[str, object]) -> str:
    try:
        return json.dumps(row, ensure_ascii=False, sort_keys=True).lower()
    except (TypeError, ValueError):
        return str(row or "").lower()


def ontology_audit_row_payload(row: Dict[str, object], row_type: str) -> Dict[str, object]:
    raw = dict(row or {})
    label = str(raw.get("label") or raw.get("title") or raw.get("id") or raw.get("relationType") or raw.get("type") or row_type)
    kind = str(raw.get("kind") or raw.get("nodeKind") or raw.get("relationType") or raw.get("type") or row_type)
    box = str(raw.get("ontologyBox") or raw.get("box") or "")
    source = str(raw.get("sourceLabel") or raw.get("source") or "")
    target = str(raw.get("targetLabel") or raw.get("target") or "")
    relation_type = str(raw.get("relationType") or raw.get("type") or "")
    stable_source = json.dumps({
        "type": row_type,
        "box": box,
        "id": raw.get("id"),
        "source": raw.get("source"),
        "target": raw.get("target"),
        "relationType": relation_type,
        "label": label,
    }, ensure_ascii=False, sort_keys=True)
    return {
        "key": hashlib.sha1(stable_source.encode("utf-8")).hexdigest()[:14],
        "rowType": row_type,
        "id": str(raw.get("id") or ""),
        "label": label,
        "kind": kind,
        "box": box,
        "relationType": relation_type,
        "source": source,
        "target": target,
        "symbol": str(raw.get("symbol") or ""),
        "ruleId": str(raw.get("ruleId") or raw.get("sourceRuleId") or raw.get("semanticRuleId") or ""),
        "status": str(raw.get("status") or ""),
        "updatedAt": str(raw.get("updatedAt") or raw.get("createdAt") or ""),
        "weight": raw.get("weight"),
        "raw": raw,
    }


def ontology_audit_filtered_rows(rows: List[Dict[str, object]], search: str, symbols: List[str]) -> List[Dict[str, object]]:
    needle = str(search or "").strip().lower()
    clean_symbols = [item.upper() for item in symbols or [] if item]
    result = []
    for row in rows or []:
        haystack = ontology_audit_row_text(row)
        if needle and needle not in haystack:
            continue
        if clean_symbols and not any(symbol in haystack.upper() for symbol in clean_symbols):
            continue
        result.append(row)
    return result


def ontology_audit_section_payload(
    section_id: str,
    rows: List[Dict[str, object]],
    limit: int,
    offset: int,
    search: str,
    symbols: List[str],
) -> Dict[str, object]:
    label, description = ONTOLOGY_AUDIT_SECTION_LABELS.get(section_id, (section_id, ""))
    filtered = ontology_audit_filtered_rows(rows, search, symbols)
    paged = filtered[offset: offset + limit]
    return {
        "id": section_id,
        "label": label,
        "description": description,
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
        "hasMore": offset + limit < len(filtered),
        "entityCount": len([row for row in filtered if row.get("rowType") == "entity"]),
        "relationCount": len([row for row in filtered if row.get("rowType") == "relation"]),
        "rows": paged,
    }


def ontology_audit_rulebox_rows(rulebox: Dict[str, object], graph_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    rows = list(graph_rows or [])
    if rows:
        return rows
    for rule in rulebox.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        rows.append(ontology_audit_row_payload({
            **rule,
            "id": rule.get("id") or rule.get("rule_id") or rule.get("name"),
            "label": rule.get("label") or rule.get("title") or rule.get("id") or "RuleBox rule",
            "kind": "rule",
            "ontologyBox": "RuleBox",
            "status": "fallback" if rulebox.get("defaultsFallbackUsed") else str(rule.get("status") or "active"),
        }, "rule"))
    return rows


def ontology_audit_sync_rows(
    repo,
    tbox: Dict[str, object],
    rulebox: Dict[str, object],
    inferencebox: Dict[str, object],
    diagnostics: Dict[str, object],
) -> List[Dict[str, object]]:
    rows = [
        ontology_audit_row_payload({
            "id": "typedb.tbox",
            "label": "TBox metadata",
            "kind": "sync-status",
            "ontologyBox": "TBox",
            "status": tbox.get("status") or ("ok" if tbox.get("configured") else "disabled"),
            "updatedAt": tbox.get("updatedAt") or "",
            "source": tbox.get("source") or tbox.get("storeSource") or "",
            "raw": tbox,
        }, "status"),
        ontology_audit_row_payload({
            "id": "typedb.rulebox",
            "label": "RuleBox snapshot",
            "kind": "sync-status",
            "ontologyBox": "RuleBox",
            "status": rulebox.get("status") or ("ok" if rulebox.get("configured") else "disabled"),
            "updatedAt": rulebox.get("updatedAt") or "",
            "ruleCount": rulebox.get("ruleCount"),
            "raw": rulebox,
        }, "status"),
        ontology_audit_row_payload({
            "id": "typedb.inferencebox",
            "label": "InferenceBox snapshot",
            "kind": "sync-status",
            "ontologyBox": "InferenceBox",
            "status": inferencebox.get("status") or ("ok" if inferencebox.get("configured") else "disabled"),
            "updatedAt": inferencebox.get("updatedAt") or "",
            "relationCount": inferencebox.get("relationCount"),
            "traceCount": inferencebox.get("traceCount"),
            "raw": inferencebox,
        }, "status"),
        ontology_audit_row_payload({
            "id": "ontology.diagnostics",
            "label": "Ontology diagnostics",
            "kind": "diagnostics",
            "ontologyBox": "Runtime",
            "status": diagnostics.get("status") or diagnostics.get("readiness") or "",
            "updatedAt": diagnostics.get("generatedAt") or "",
            "raw": diagnostics,
        }, "status"),
    ]
    if hasattr(repo, "read_inference_generation_records"):
        try:
            for index, generation in enumerate(repo.read_inference_generation_records()[:20]):
                rows.append(ontology_audit_row_payload({
                    **generation,
                    "id": generation.get("generationId") or generation.get("snapshotId") or ("generation-" + str(index + 1)),
                    "label": "Inference generation " + str(index + 1),
                    "kind": "inference-generation",
                    "ontologyBox": "InferenceBox",
                    "status": "materialized",
                    "updatedAt": generation.get("updatedAt") or "",
                }, "status"))
        except Exception as error:  # noqa: BLE001 - audit UI should expose read errors instead of failing.
            rows.append(ontology_audit_row_payload({
                "id": "typedb.inference-generation.error",
                "label": "Inference generation read error",
                "kind": "sync-error",
                "ontologyBox": "InferenceBox",
                "status": "error",
                "reason": str(error)[:220],
            }, "status"))
    return rows


def ontology_audit_payload(query: Dict[str, List[str]], requested_section: str = "") -> Dict[str, object]:
    settings = runtime_settings()
    repo = ontology_repository_from_settings(settings)
    limit = safe_int(first_query(query, "limit"), 80, 1, 300)
    offset = safe_int(first_query(query, "offset"), 0, 0, 100000)
    search = first_query(query, "q") or first_query(query, "query")
    symbols = ontology_audit_symbols(query)
    section_filter = str(requested_section or first_query(query, "section") or "").strip().lower()
    if section_filter == "all":
        section_filter = ""
    compact_all = not section_filter
    fast_compact_summary = compact_all and not search and not symbols
    section_ids = [section_filter] if section_filter in ONTOLOGY_AUDIT_SECTION_LABELS else list(ONTOLOGY_AUDIT_SECTION_LABELS.keys())
    section_box_map = {
        "tbox": ["TBox"],
        "abox": ["ABox"],
        "rulebox": ["RuleBox", "RuleBoxGovernance"],
        "inferencebox": ["InferenceBox"],
        "evidence": ["ABox", "InferenceBox"],
        "sync": [],
    }
    read_boxes = sorted({
        box
        for section_id in section_ids
        for box in section_box_map.get(section_id, ONTOLOGY_AUDIT_BOXES)
    })
    graph_entities: List[Dict[str, object]] = []
    graph_relations: List[Dict[str, object]] = []
    graph_error = ""
    if fast_compact_summary:
        graph_error = ""
    elif read_boxes and hasattr(repo, "read_entity_rows") and hasattr(repo, "read_relation_rows"):
        try:
            if compact_all:
                sample_limit = max(5, min(80, limit))
                for box in read_boxes:
                    graph_entities.extend([
                        ontology_audit_row_payload(row, "entity")
                        for row in repo.read_entity_rows([box], sample_limit)
                    ])
                    graph_relations.extend([
                        ontology_audit_row_payload(row, "relation")
                        for row in repo.read_relation_rows([box], sample_limit)
                    ])
            else:
                graph_entities = [ontology_audit_row_payload(row, "entity") for row in repo.read_entity_rows(read_boxes)]
                graph_relations = [ontology_audit_row_payload(row, "relation") for row in repo.read_relation_rows(read_boxes)]
        except Exception as error:  # noqa: BLE001 - admin audit must degrade gracefully.
            graph_error = str(error)[:240]
    elif read_boxes:
        graph_error = "TypeDB row reader is not available for this graph store."

    graph_rows = graph_entities + graph_relations
    by_box = {}
    for row in graph_rows:
        by_box.setdefault(str(row.get("box") or ""), []).append(row)

    tbox_metadata: Dict[str, object] = {}
    rulebox: Dict[str, object] = {}
    inferencebox: Dict[str, object] = {}
    diagnostics: Dict[str, object] = {}
    try:
        tbox_metadata = (
            {"status": "sampled", "source": "audit-sample", "configured": bool(getattr(repo, "address", ""))}
            if compact_all
            else repo.active_tbox_metadata()
            if ("tbox" in section_ids or "sync" in section_ids) and hasattr(repo, "active_tbox_metadata")
            else {}
        )
    except Exception as error:  # noqa: BLE001
        tbox_metadata = {"status": "error", "reason": str(error)[:220]}
    try:
        rulebox = (
            {"status": "sampled", "source": "audit-sample", "rules": []}
            if compact_all
            else repo.rulebox_snapshot()
            if ("rulebox" in section_ids or "sync" in section_ids) and hasattr(repo, "rulebox_snapshot")
            else {}
        )
    except Exception as error:  # noqa: BLE001
        rulebox = {"status": "error", "reason": str(error)[:220], "rules": []}
    try:
        inferencebox = (
            {"status": "sampled", "source": "audit-sample", "entities": [], "relations": [], "traces": []}
            if compact_all
            else repo.inferencebox_snapshot(symbols=symbols, limit=min(300, max(80, limit)))
            if ("inferencebox" in section_ids or "sync" in section_ids) and hasattr(repo, "inferencebox_snapshot")
            else {}
        )
    except Exception as error:  # noqa: BLE001
        inferencebox = {"status": "error", "reason": str(error)[:220], "entities": [], "relations": [], "traces": []}
    try:
        diagnostics = (
            {"status": "sampled", "reason": "기본 감사 화면은 빠른 샘플만 읽고, 상세 진단은 /api/ontology/audit/sync에서 실행합니다."}
            if compact_all and "sync" in section_ids
            else OntologyDiagnosticsService(
                ontology_repository=repo,
                settings=settings,
                event_log=stores.event_log(settings),
                notification_queue=stores.notification_job_store(settings),
                strategy_proposal_service=build_investment_strategy_proposal_service(settings),
            ).status(symbols=symbols, limit=min(300, max(80, limit))) if "sync" in section_ids else {}
        )
    except Exception as error:  # noqa: BLE001
        diagnostics = {"status": "error", "reason": str(error)[:220]}

    inference_rows = by_box.get("InferenceBox", [])
    if not inference_rows:
        inference_rows = [
            ontology_audit_row_payload({**row, "ontologyBox": "InferenceBox"}, "entity")
            for row in (inferencebox.get("entities") or [])
            if isinstance(row, dict)
        ] + [
            ontology_audit_row_payload({**row, "ontologyBox": "InferenceBox"}, "relation")
            for row in (inferencebox.get("relations") or [])
            if isinstance(row, dict)
        ] + [
            ontology_audit_row_payload({**row, "ontologyBox": "InferenceBox", "kind": row.get("kind") or "inference-trace"}, "trace")
            for row in (inferencebox.get("traces") or [])
            if isinstance(row, dict)
        ]

    evidence_rows = [
        row for row in graph_rows
        if str(row.get("kind") or "").lower() in ONTOLOGY_AUDIT_EVIDENCE_KINDS
        or any(token in ontology_audit_row_text(row) for token in ["evidence", "belief", "opinion", "executionplan", "reasoningcard"])
    ]

    sections = {}
    if "tbox" in section_ids:
        sections["tbox"] = ontology_audit_section_payload("tbox", by_box.get("TBox", []), limit, offset, search, symbols)
    if "abox" in section_ids:
        sections["abox"] = ontology_audit_section_payload("abox", by_box.get("ABox", []), limit, offset, search, symbols)
    if "rulebox" in section_ids:
        sections["rulebox"] = ontology_audit_section_payload(
            "rulebox",
            ontology_audit_rulebox_rows(rulebox, by_box.get("RuleBox", []) + by_box.get("RuleBoxGovernance", [])),
            limit,
            offset,
            search,
            symbols,
        )
    if "inferencebox" in section_ids:
        sections["inferencebox"] = ontology_audit_section_payload("inferencebox", inference_rows, limit, offset, search, symbols)
    if "evidence" in section_ids:
        sections["evidence"] = ontology_audit_section_payload("evidence", evidence_rows, limit, offset, search, symbols)
    if "sync" in section_ids:
        sections["sync"] = ontology_audit_section_payload(
            "sync",
            ontology_audit_sync_rows(repo, tbox_metadata, rulebox, inferencebox, diagnostics),
            limit,
            offset,
            search,
            symbols,
        )

    totals = {key: value.get("total", 0) for key, value in sections.items()}
    status = "error" if graph_error else "ok"
    if not getattr(repo, "address", "") and all((sections.get(key) or {}).get("total", 0) == 0 for key in ["tbox", "abox", "inferencebox"] if key in sections):
        status = "disabled"
    return {
        "generatedAt": now(),
        "status": status,
        "graphStore": getattr(repo, "store_key", "typedb"),
        "storeLabel": getattr(repo, "store_label", "TypeDB"),
        "configured": bool(getattr(repo, "address", "") or rulebox.get("configured") or tbox_metadata.get("configured")),
        "error": graph_error,
        "query": {
            "limit": limit,
            "offset": offset,
            "q": search,
            "symbols": symbols,
            "section": section_filter or "all",
        },
        "summary": {
            "sectionTotals": totals,
            "graphRowCount": len(graph_rows),
            "entityCount": len(graph_entities),
            "relationCount": len(graph_relations),
            "ruleCount": rulebox.get("ruleCount") or len(rulebox.get("rules") or []) or (sections.get("rulebox") or {}).get("total", 0),
            "inferenceRelationCount": inferencebox.get("relationCount") or (sections.get("inferencebox") or {}).get("relationCount", 0),
            "inferenceTraceCount": inferencebox.get("traceCount") or 0,
            "diagnosticsStatus": diagnostics.get("status") or diagnostics.get("readiness") or "",
            "tboxStatus": tbox_metadata.get("status") or "",
            "ruleboxStatus": rulebox.get("status") or "",
            "inferenceboxStatus": inferencebox.get("status") or "",
        },
        "sections": sections,
        "tbox": tbox_metadata,
        "rulebox": {
            "status": rulebox.get("status"),
            "ruleCount": rulebox.get("ruleCount"),
            "conditionCount": rulebox.get("conditionCount"),
            "derivationCount": rulebox.get("derivationCount"),
            "defaultsFallbackUsed": rulebox.get("defaultsFallbackUsed"),
            "versionCount": rulebox.get("versionCount"),
            "source": rulebox.get("source"),
        },
        "inferencebox": {
            "status": inferencebox.get("status"),
            "relationCount": inferencebox.get("relationCount"),
            "traceCount": inferencebox.get("traceCount"),
            "reasoningMode": inferencebox.get("reasoningMode"),
            "source": inferencebox.get("source"),
        },
        "diagnostics": diagnostics,
    }


def propose_ontology_rule_candidates_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    symbols = body.get("symbols") if isinstance(body.get("symbols"), list) else []
    result = build_rule_change_candidate_service(runtime_settings()).propose(
        symbols=symbols,
        trigger=str(body.get("trigger") or "manual"),
    )
    snapshot = ontology_repository_from_settings(runtime_settings()).rulebox_snapshot()
    result["rulebox"] = snapshot
    return result


def seed_ontology_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_repository_from_settings(runtime_settings()).seed_ontology(payload)


def ontology_lab_service():
    return build_ontology_lab_service(runtime_settings())


def list_ontology_experiments_payload() -> Dict[str, object]:
    return ontology_lab_service().list()


def ontology_experiments_status_payload() -> Dict[str, object]:
    return ontology_lab_service().status()


def create_ontology_experiment_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_lab_service().create(payload if isinstance(payload, dict) else {})


def suggest_ontology_experiments_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    symbols = body.get("symbols") if isinstance(body.get("symbols"), list) else []
    candidate_result = build_rule_change_candidate_service(runtime_settings()).propose(
        symbols=symbols,
        trigger=str(body.get("trigger") or "ontology-lab-suggest"),
    )
    result = ontology_lab_service().suggest_from_rule_candidates(candidate_result, body)
    result["candidateResult"] = {
        "status": candidate_result.get("status"),
        "candidateCount": candidate_result.get("candidateCount"),
        "savedCount": candidate_result.get("savedCount"),
        "contextSummary": candidate_result.get("contextSummary") or {},
    }
    return result


def ontology_experiment_payload(experiment_id: str) -> Dict[str, object]:
    return ontology_lab_service().report(experiment_id)


def run_ontology_experiment_payload(experiment_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_lab_service().run(experiment_id, payload if isinstance(payload, dict) else {})


def apply_ontology_experiment_payload(experiment_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_lab_service().apply_recommendations(experiment_id, payload if isinstance(payload, dict) else {})


def apply_ontology_experiments_batch_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_lab_service().apply_recommendation_batch(payload if isinstance(payload, dict) else {})


def run_ontology_experiments_once_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    limit = int(body.get("limit") or 0)
    force = bool(body.get("force"))
    return ontology_lab_service().run_once(limit=limit, force=force)


def activate_ontology_experiment_payload(experiment_id: str) -> Dict[str, object]:
    return ontology_lab_service().activate(experiment_id)


def pause_ontology_experiment_payload(experiment_id: str) -> Dict[str, object]:
    return ontology_lab_service().pause(experiment_id)


def investment_strategy_proposal_service():
    return build_investment_strategy_proposal_service(runtime_settings())


def list_investment_strategy_proposals_payload() -> Dict[str, object]:
    return investment_strategy_proposal_service().list()


def investment_strategy_proposals_status_payload() -> Dict[str, object]:
    return investment_strategy_proposal_service().status()


def investment_strategy_proposal_payload(proposal_id: str) -> Dict[str, object]:
    return investment_strategy_proposal_service().get(proposal_id)


def validate_investment_strategy_proposal_payload(proposal_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return investment_strategy_proposal_service().validate_materialization(proposal_id, payload if isinstance(payload, dict) else {})


def approve_investment_strategy_proposal_payload(proposal_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return investment_strategy_proposal_service().approve(proposal_id, payload if isinstance(payload, dict) else {})


def investment_strategy_proposal_performance_payload(proposal_id: str) -> Dict[str, object]:
    return investment_strategy_proposal_service().performance(proposal_id)


def record_investment_strategy_proposal_performance_payload(proposal_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return investment_strategy_proposal_service().record_performance_sample(proposal_id, payload if isinstance(payload, dict) else {})


def notification_store():
    return stores.notification_template_store()


def notification_queue_store():
    return stores.notification_job_store()


def notification_rule_store():
    return stores.notification_rule_store()


def list_templates_payload() -> Dict[str, object]:
    visible_types = set(visible_notification_template_types())
    try:
        templates = [
            item
            for item in notification_store().list()
            if item.message_type in visible_types
        ]
    except Exception:  # noqa: BLE001 - default templates keep settings UI available without MySQL.
        templates = [NotificationTemplate.default(message_type) for message_type in visible_notification_template_types()]
    return {
        "templates": [item.to_dict() for item in templates],
        "variables": template_variables(),
        "visibleMessageTypes": visible_notification_template_types(),
    }


def list_notification_rules_payload(include_internal: bool = False) -> Dict[str, object]:
    managed_order = user_managed_notification_types()
    managed_types = set(managed_order)
    try:
        rules = notification_rule_store().list()
    except Exception:  # noqa: BLE001 - default rules keep settings UI available without MySQL.
        catalog = user_managed_notification_types() + ([] if not include_internal else [key for key in DEFAULT_ALERT_RULES if key not in managed_types])
        rules = [
            NotificationRuleConfig.from_dict({
                "messageType": message_type,
                "enabled": bool(DEFAULT_ALERT_RULES.get(message_type, 1)),
            })
            for message_type in catalog
        ]
    rules_by_type = {item.message_type: item for item in rules}
    visible_rules = [rules_by_type[item] for item in managed_order if item in rules_by_type]
    internal_rules = [item for item in rules if item.message_type not in managed_types]
    payload = {
        "rules": [item.to_dict() for item in visible_rules],
        "conditionTypes": CONDITION_TYPE_LABELS,
        "marketHoursSessions": list(DEFAULT_MARKET_HOUR_SESSIONS.values()),
        "messageCatalog": public_message_catalog(),
        "managedMessageTypes": user_managed_notification_types(),
        "internalRuleCount": len(internal_rules),
    }
    if include_internal:
        payload["internalRules"] = [item.to_dict() for item in internal_rules]
    return payload


def include_internal_notification_query(query: Dict[str, List[str]]) -> bool:
    value = first_query(query, "includeInternal").lower()
    return value in {"1", "true", "yes", "y"}


def compact_notification_text(value: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", full_notification_text(value)).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def full_notification_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def notification_processing_age_minutes(job: NotificationJob) -> float:
    started_at = parse_utc(str((job.context or {}).get("processingStartedAt") or job.updated_at or job.created_at or ""))
    if job.status != "processing" or not started_at:
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds() / 60)


def notification_next_eligible_at(context: Dict[str, object]) -> str:
    if not context.get("cooldownEnabled"):
        return ""
    if str(context.get("cooldownDecision") or "") != "cooldown" and not context.get("cooldownSuppressed"):
        return ""
    last_sent_at = parse_utc(str(context.get("cooldownLastSentAt") or ""))
    if not last_sent_at:
        return ""
    try:
        minutes = int(float(context.get("cooldownMinutes") or 0))
    except (TypeError, ValueError):
        minutes = 0
    if minutes <= 0:
        return ""
    return utc_iso(last_sent_at + timedelta(minutes=minutes))


def notification_suppression_summary(job: NotificationJob) -> str:
    context = dict(job.context or {})
    if job.status != "suppressed":
        return ""
    if job.last_error:
        return job.last_error
    if context.get("cooldownReason"):
        return str(context.get("cooldownReason"))
    if context.get("marketHoursReason"):
        return str(context.get("marketHoursReason"))
    if context.get("quietHoursReason"):
        return str(context.get("quietHoursReason"))
    reason = str(context.get("deliverySuppressionReason") or "").strip()
    if reason == "stale_data":
        return "데이터 신선도 기준 미통과"
    if reason == "market_closed":
        return "장 시간 외 발송 보류"
    if reason == "state_cooldown":
        return "같은 상태 반복 발송 보류"
    return reason or "알림 정책으로 발송 보류"


def notification_job_diagnostics(jobs: List[NotificationJob]) -> Dict[str, object]:
    settings = runtime_settings()
    try:
        stale_minutes = max(1, int(settings.get("notificationProcessingStaleMinutes") or 30))
    except (TypeError, ValueError):
        stale_minutes = 30
    reason_counts: Dict[str, int] = {}
    stale_processing = 0
    for job in jobs:
        if job.status == "suppressed":
            reason = notification_suppression_summary(job) or "보류 사유 없음"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if job.status == "processing" and notification_processing_age_minutes(job) >= stale_minutes:
            stale_processing += 1
    top_reasons = sorted(reason_counts.items(), key=lambda item: item[1], reverse=True)[:6]
    return {
        "processingStaleMinutes": stale_minutes,
        "staleProcessingCount": stale_processing,
        "suppressionReasons": [{"reason": reason, "count": count} for reason, count in top_reasons],
    }


def notification_job_public_payload(job: NotificationJob) -> Dict[str, object]:
    context = job.context or {}
    reasons = context.get("deliveryReasons") if isinstance(context.get("deliveryReasons"), list) else []
    title = str(context.get("title") or context.get("headline") or "").strip()
    processing_age = notification_processing_age_minutes(job)
    try:
        stale_minutes = max(1, int(runtime_settings().get("notificationProcessingStaleMinutes") or 30))
    except (TypeError, ValueError):
        stale_minutes = 30
    return {
        "jobId": job.job_id,
        "messageType": job.message_type,
        "messageTypeLabel": MESSAGE_TYPE_LABELS.get(job.message_type, job.message_type),
        "messageTypeIcon": MESSAGE_TYPE_EMOJIS.get(job.message_type, "🔔"),
        "status": job.status,
        "accountId": job.account_id,
        "accountLabel": job.account_label,
        "createdAt": job.created_at,
        "updatedAt": job.updated_at,
        "sourceEventName": job.source_event_name,
        "title": title,
        "symbol": str(context.get("symbol") or "").strip(),
        "rawSymbol": str(context.get("rawSymbol") or context.get("symbol") or "").strip(),
        "symbolName": str(context.get("symbolDisplayName") or context.get("displaySymbolName") or "").strip(),
        "textPreview": compact_notification_text(job.text),
        "fullText": full_notification_text(job.text),
        "lastError": job.last_error,
        "suppressionSummary": notification_suppression_summary(job),
        "nextEligibleAt": notification_next_eligible_at(context),
        "processingAgeMinutes": round(processing_age, 1),
        "recoverableProcessing": bool(job.status == "processing" and processing_age >= stale_minutes),
        "deliveryDecision": context.get("deliveryDecision") or ("send" if job.status in {"pending", "processing", "done"} else job.status),
        "deliveryGateState": context.get("deliveryGateState") or "",
        "deliveryGateReason": context.get("deliveryGateReason") or "",
        "deliveryReasons": [str(item) for item in reasons],
        "deliveryFingerprint": context.get("deliveryFingerprint") or "",
        "deliveryReviewLevel": context.get("deliveryReviewLevel") or "",
        "deliveryDataState": context.get("deliveryDataState") or "",
        "deliveryChangeState": context.get("deliveryChangeState") or "",
        "deliveryConflictState": context.get("deliveryConflictState") or "",
        "deliveryValidationState": context.get("deliveryValidationState") or "",
        "repeatRecentCount": context.get("repeatRecentCount"),
        "repeatWindowMinutes": context.get("repeatWindowMinutes"),
        "repeatBypassed": bool(context.get("repeatBypassed")),
        "repeatBypassReason": context.get("repeatBypassReason") or "",
        "deliverySuppressionReason": context.get("deliverySuppressionReason") or "",
        "cooldownEnabled": bool(context.get("cooldownEnabled")),
        "cooldownMinutes": context.get("cooldownMinutes"),
        "cooldownRecentSentCount": context.get("cooldownRecentSentCount"),
        "cooldownLastSentAt": context.get("cooldownLastSentAt") or "",
        "cooldownLastSentAgeMinutes": context.get("cooldownLastSentAgeMinutes"),
        "cooldownDecision": context.get("cooldownDecision") or "",
        "cooldownReason": context.get("cooldownReason") or "",
        "cooldownSuppressed": bool(context.get("cooldownSuppressed")),
        "marketHoursEnabled": bool(context.get("marketHoursEnabled")),
        "marketHoursMarket": context.get("marketHoursMarket") or "",
        "marketHoursLabel": context.get("marketHoursLabel") or "",
        "marketHoursStatus": context.get("marketHoursStatus") or "",
        "marketHoursDecision": context.get("marketHoursDecision") or "",
        "marketHoursReason": context.get("marketHoursReason") or "",
        "marketHoursLocalTime": context.get("marketHoursLocalTime") or "",
        "marketHoursOpenTime": context.get("marketHoursOpenTime") or "",
        "marketHoursCloseTime": context.get("marketHoursCloseTime") or "",
        "marketHoursTimezone": context.get("marketHoursTimezone") or "",
        "quietHoursSuppressed": bool(context.get("quietHoursSuppressed")),
        "quietHoursReason": context.get("quietHoursReason") or "",
        "quietHoursStart": context.get("quietHoursStart") or "",
        "quietHoursEnd": context.get("quietHoursEnd") or "",
        "quietHoursTimezone": context.get("quietHoursTimezone") or "",
    }


def notification_jobs_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = max(1, min(200, int(first_query(query, "limit") or 40)))
    try:
        jobs = notification_queue_store().recent(
            limit=limit,
            message_type=first_query(query, "messageType") or first_query(query, "message_type"),
            status=first_query(query, "status"),
        )
        summary = notification_queue_store().summary()
    except Exception:  # noqa: BLE001 - empty queue keeps the console readable without MySQL.
        jobs = []
        summary = {"pending": 0, "processing": 0, "done": 0, "suppressed": 0, "failed": 0}
    return {
        "jobs": [notification_job_public_payload(job) for job in jobs],
        "summary": summary,
        "diagnostics": notification_job_diagnostics(jobs),
        "limit": limit,
    }


def replay_notification_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    identifier = configured(body.get("identifier") or body.get("notificationNumber") or body.get("jobId"))
    result = NotificationReplayService(
        queue=notification_queue_store(),
        account_repository=stores.account_registry(),
        runner_factory=build_notification_queue_runner,
        lookup_limit=int(body.get("lookupLimit") or 200),
    ).replay(
        identifier,
        direct=request_bool(body.get("direct")),
        dry_run=request_bool(body.get("dryRun", body.get("dry_run"))),
    )
    return result.to_dict()


def research_evidence_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = max(1, min(500, int(first_query(query, "limit") or 8)))
    symbol = configured(first_query(query, "symbol")).upper()
    kind = configured(first_query(query, "kind"))
    store = stores.research_evidence_store()
    return {
        "items": [item.to_dict() for item in store.latest(symbol=symbol, kind=kind, limit=limit)],
        "summary": store.summary(),
        "symbol": symbol,
        "kind": kind,
        "limit": limit,
    }


def delete_research_evidence_payload(evidence_id: str, query: Dict[str, List[str]]) -> Dict[str, object]:
    normalized_id = configured(evidence_id)
    if not normalized_id:
        raise ValueError("삭제할 근거 ID가 필요합니다.")
    store = stores.research_evidence_store()
    removed = store.delete(normalized_id)
    if removed:
        new_domain_event(
            APP_ITEM_REMOVED,
            normalized_id,
            {"itemId": normalized_id, "type": "researchEvidence"},
        )
    payload = research_evidence_payload(query)
    payload["deleted"] = removed
    payload["deletedId"] = normalized_id
    return payload


def investment_calendar_service():
    return build_investment_calendar_service(runtime_settings(), event_publisher=RealtimeEventBridge())


def investment_calendar_candidate_service():
    return build_investment_calendar_candidate_service(runtime_settings(), event_publisher=RealtimeEventBridge())


def investment_calendar_research_service():
    return build_investment_calendar_research_service(runtime_settings())


def investment_calendar_query_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    return {
        "from": first_query(query, "from") or first_query(query, "fromAt"),
        "to": first_query(query, "to") or first_query(query, "toAt"),
        "status": first_query(query, "status"),
        "symbol": first_query(query, "symbol"),
        "eventType": first_query(query, "eventType") or first_query(query, "event_type"),
        "limit": first_query(query, "limit") or "200",
    }


def investment_calendar_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    return investment_calendar_service().list_events(investment_calendar_query_payload(query))


def save_investment_calendar_event_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return investment_calendar_service().save_event(payload if isinstance(payload, dict) else {})


def delete_investment_calendar_event_payload(event_id: str) -> Dict[str, object]:
    return investment_calendar_service().delete_event(event_id)


def investment_calendar_reminders_once_payload() -> Dict[str, object]:
    return build_investment_calendar_runner(runtime_settings(), event_publisher=RealtimeEventBridge()).run_once()


def investment_calendar_sync_official_payload() -> Dict[str, object]:
    return build_official_calendar_sync_service(runtime_settings(), event_publisher=RealtimeEventBridge()).run_once(force=True)


def investment_calendar_candidates_query_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    return {
        "status": first_query(query, "status") or "pending",
        "limit": first_query(query, "limit") or "100",
        "page": first_query(query, "page"),
        "pageSize": first_query(query, "pageSize") or first_query(query, "page_size"),
        "offset": first_query(query, "offset"),
    }


def investment_calendar_candidates_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    return investment_calendar_candidate_service().list_candidates(investment_calendar_candidates_query_payload(query))


def research_investment_calendar_candidates_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return investment_calendar_research_service().recommend(payload if isinstance(payload, dict) else {})


def approve_investment_calendar_candidate_payload(candidate_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return investment_calendar_candidate_service().approve_candidate(candidate_id, payload if isinstance(payload, dict) else {})


def reject_investment_calendar_candidate_payload(candidate_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return investment_calendar_candidate_service().reject_candidate(candidate_id, payload if isinstance(payload, dict) else {})


def parse_utc(value: str):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def cadence_records_for_type(sent: Dict[str, object], message_type: str) -> List[Dict[str, object]]:
    records = []
    prefix = "cadence:python:"
    for key, sent_at in sent.items():
        if not str(key).startswith(prefix):
            continue
        parts = str(key).split(":", 4)
        if len(parts) != 5 or parts[3] != message_type:
            continue
        parsed = parse_utc(str(sent_at or ""))
        if not parsed:
            continue
        records.append({
            "accountId": parts[2],
            "target": parts[4],
            "sentAt": utc_iso(parsed),
            "sentAtEpoch": parsed.timestamp(),
        })
    records.sort(key=lambda item: float(item.get("sentAtEpoch") or 0), reverse=True)
    return records


def notification_schedules_payload(include_internal: bool = False) -> Dict[str, object]:
    settings = runtime_settings()
    rules = parse_assignments(settings.get("alertRules", ""), DEFAULT_ALERT_RULES)
    cadence = parse_assignments(settings.get("alertCadenceMinutes", ""), DEFAULT_CADENCE)
    store = stores.monitor_store()
    accounts = {account.account_id: account for account in stores.account_registry().load()}
    now_at = datetime.now(timezone.utc)
    if include_internal:
        message_types = list(dict.fromkeys(list(DEFAULT_CADENCE.keys()) + list(DEFAULT_NOTIFICATION_TEMPLATES.keys())))
    else:
        message_types = user_managed_notification_types()
    schedules = []
    for message_type in message_types:
        has_cadence = message_type in DEFAULT_CADENCE
        minutes = int(cadence.get(message_type, DEFAULT_CADENCE.get(message_type, 0)) or 0)
        records = cadence_records_for_type(store.sent, message_type)
        last_record = records[0] if records else {}
        last_sent_at = parse_utc(str(last_record.get("sentAt") or "")) if last_record else None
        next_eligible_at = last_sent_at + timedelta(minutes=max(10, minutes)) if last_sent_at and minutes else None
        enabled = bool(rules.get(message_type, 1)) if has_cadence else True
        recent_targets = []
        for record in records[:4]:
            account = accounts.get(str(record.get("accountId") or ""))
            target = str(record.get("target") or "all")
            recent_targets.append({
                "accountId": record.get("accountId") or "",
                "accountLabel": account.label if account else str(record.get("accountId") or ""),
                "target": "" if target == "all" else target,
                "sentAt": record.get("sentAt") or "",
            })
        if not has_cadence:
            status = "event"
        elif not enabled:
            status = "disabled"
        elif next_eligible_at and next_eligible_at > now_at:
            status = "waiting"
        else:
            status = "ready"
        if minutes:
            cadence_text = "조건이 다시 충족되면 최소 " + str(max(10, minutes)) + "분 간격으로 보냅니다."
        else:
            cadence_text = "정해진 주기 없이 해당 이벤트가 생길 때만 보냅니다."
        schedules.append({
            "messageType": message_type,
            "label": MESSAGE_TYPE_LABELS.get(message_type, message_type),
            "icon": MESSAGE_TYPE_EMOJIS.get(message_type, "🔔"),
            "enabled": enabled,
            "status": status,
            "cadenceMinutes": max(10, minutes) if minutes else 0,
            "cadenceText": cadence_text,
            "triggerSummary": TRIGGER_SUMMARIES.get(message_type) or NON_CADENCE_MESSAGE_GUIDES.get(message_type) or "설정한 조건이 실제 데이터에서 충족될 때 보냅니다.",
            "lastSentAt": utc_iso(last_sent_at) if last_sent_at else "",
            "nextEligibleAt": utc_iso(next_eligible_at) if next_eligible_at else "",
            "eligibleNow": bool(enabled and (not next_eligible_at or next_eligible_at <= now_at)),
            "recentTargets": recent_targets,
        })
    return {
        "generatedAt": utc_now_iso(),
        "schedules": schedules,
        "managedMessageTypes": user_managed_notification_types(),
    }


def save_template_payload(payload: Dict[str, object]) -> Dict[str, object]:
    message_type = configured(payload.get("messageType") or payload.get("message_type"))
    template = str(payload.get("template") or "")
    description = str(payload.get("description") or "")
    enabled = payload.get("enabled") is not False
    try:
        saved = notification_store().upsert(message_type, template, description, enabled)
    except Exception:  # noqa: BLE001 - respond with normalized payload when optional MySQL is offline.
        saved = NotificationTemplate(message_type, template, description, enabled, now())
    event = new_domain_event(
        NOTIFICATION_TEMPLATE_UPDATED,
        saved.message_type,
        {"messageType": saved.message_type, "enabled": saved.enabled, "updatedAt": saved.updated_at},
    )
    return {"template": saved.to_dict(), "eventId": event.event_id}


def reset_template_payload(message_type: str) -> Dict[str, object]:
    try:
        saved = notification_store().reset(message_type)
    except Exception:  # noqa: BLE001
        saved = NotificationTemplate.default(message_type)
    event = new_domain_event(
        NOTIFICATION_TEMPLATE_UPDATED,
        saved.message_type,
        {"messageType": saved.message_type, "enabled": saved.enabled, "updatedAt": saved.updated_at, "reset": True},
    )
    return {"template": saved.to_dict(), "eventId": event.event_id}


def save_notification_rule_payload(payload: Dict[str, object]) -> Dict[str, object]:
    requested = payload.get("rule") if isinstance(payload.get("rule"), dict) else payload
    rule = NotificationRuleConfig.from_dict(requested if isinstance(requested, dict) else {})
    try:
        saved = notification_rule_store().upsert(rule)
    except Exception:  # noqa: BLE001
        saved = rule
        saved.updated_at = now()
    event = new_domain_event(
        NOTIFICATION_RULE_UPDATED,
        saved.message_type,
        {
            "messageType": saved.message_type,
            "enabled": saved.enabled,
            "similarityEnabled": saved.similarity_enabled,
            "similarityWindowMinutes": saved.similarity_window_minutes,
            "similarityBypassConditionCount": len(saved.similarity_bypass_conditions),
            "stateCooldownEnabled": saved.state_cooldown_enabled,
            "stateCooldownMinutes": saved.state_cooldown_minutes,
            "updatedAt": saved.updated_at,
        },
    )
    return {"rule": saved.to_dict(), "eventId": event.event_id}


def reset_notification_rule_payload(message_type: str) -> Dict[str, object]:
    try:
        saved = notification_rule_store().reset(message_type)
    except Exception:  # noqa: BLE001
        saved = NotificationRuleConfig.from_dict({"messageType": message_type, "enabled": bool(DEFAULT_ALERT_RULES.get(message_type, 1))})
        saved.updated_at = now()
    event = new_domain_event(
        NOTIFICATION_RULE_UPDATED,
        saved.message_type,
        {
            "messageType": saved.message_type,
            "enabled": saved.enabled,
            "similarityEnabled": saved.similarity_enabled,
            "similarityWindowMinutes": saved.similarity_window_minutes,
            "stateCooldownEnabled": saved.state_cooldown_enabled,
            "stateCooldownMinutes": saved.state_cooldown_minutes,
            "updatedAt": saved.updated_at,
            "reset": True,
        },
    )
    return {"rule": saved.to_dict(), "eventId": event.event_id}


def alert_event_public_payload(event) -> Dict[str, object]:
    context = alert_context(event)
    return {
        "accountId": event.account_id,
        "accountLabel": event.account_label,
        "messageType": event.rule,
        "rule": event.rule,
        "severity": event.severity,
        "symbol": event.symbol,
        "rawSymbol": context.get("rawSymbol") or event.symbol,
        "symbolName": context.get("symbolDisplayName") or "",
        "title": event.title,
        "lines": list(event.lines or []),
        "key": event.key,
    }


def selected_notification_test_account(payload: Dict[str, object]):
    requested = configured(payload.get("accountId") or payload.get("account_id"))
    accounts = stores.account_registry().load()
    if requested:
        for account in accounts:
            if account.account_id == requested:
                return account
        raise ValueError("요청한 계정을 찾지 못했습니다.")
    if not accounts:
        raise ValueError("테스트 발송에 사용할 계정이 없습니다.")
    return accounts[0]


def attach_notification_test_ontology_projection(snapshot, settings: Dict[str, str]) -> None:
    metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
    ontology = metadata.get("ontology") if isinstance(metadata.get("ontology"), dict) else {}
    existing_projection = ontology.get("projection") or ontology.get("typedb")
    if isinstance(existing_projection, dict) and isinstance(existing_projection.get("inferenceBox"), dict):
        return
    try:
        recorder = PortfolioOntologyProjectionRecorder(
            ontology_repository_from_settings(settings),
            quality_store=stores.ontology_quality_sample_store(settings),
            projection_run_store=stores.ontology_projection_run_store(settings),
            decision_episode_store=stores.investment_decision_episode_store(settings),
            hypothesis_proposal_store=stores.investment_research_store(settings),
            settings=settings,
            source="notification-test",
        )
        recorder.record_snapshot(snapshot)
    except Exception as error:  # noqa: BLE001 - test dispatch should report TypeDB readiness instead of crashing.
        snapshot.metadata.setdefault("ontology", {})["projection"] = {
            "saved": False,
            "status": "error",
            "graphStore": "typedb",
            "reason": "notification test TypeDB projection failed: " + str(error)[:160],
        }


def notification_test_event(message_type: str, snapshot):
    settings = runtime_settings()
    attach_notification_test_ontology_projection(snapshot, settings)
    monitor = RealtimeMonitor(settings)
    events = monitor.type_check_events_for_snapshot(snapshot)
    for event in events:
        if event.rule == message_type:
            return event
    for event in monitor.events_for_snapshot(snapshot, {}):
        if event.rule == message_type:
            return event
    return None


def notification_template_test_payload(payload: Dict[str, object]):
    message_type = configured(payload.get("messageType") or payload.get("message_type"))
    if not message_type:
        raise ValueError("messageType은 필요합니다.")
    dry_run = request_bool(payload.get("dryRun", payload.get("dry_run")))
    bypass_policy = (
        request_bool(payload.get("bypassPolicy", payload.get("bypass_policy")))
        or request_bool(payload.get("directSend", payload.get("direct_send")))
    )
    account = selected_notification_test_account(payload)
    snapshot = build_snapshot(account)
    if snapshot.mode != "live" and not payload.get("allowDemo"):
        return 409, {
            "delivered": False,
            "messageType": message_type,
            "error": "실제 토스 데이터를 가져오지 못했습니다: " + (snapshot.status or snapshot.mode),
            "snapshot": {
                "accountId": snapshot.account_id,
                "accountLabel": snapshot.account_label,
                "mode": snapshot.mode,
                "status": snapshot.status,
                "generatedAt": snapshot.generated_at,
            },
        }
    if message_type == "investmentInsight":
        missing_event = notification_test_event("ontologyInferenceMissing", snapshot)
        if missing_event:
            return 409, {
                "delivered": False,
                "messageType": message_type,
                "blockedBy": "ontologyInferenceMissing",
                "error": "온톨로지 추론 결과가 없어 투자 판단 테스트 발송을 막았습니다.",
                "event": alert_event_public_payload(missing_event),
            }
    event = notification_test_event(message_type, snapshot)
    if not event:
        return 422, {
            "delivered": False,
            "messageType": message_type,
            "error": "현재 데이터로 만들 수 있는 알림 이벤트가 없습니다.",
        }
    context = alert_context(event)
    context.update({
        "testDispatch": True,
        "notificationTestBypassPolicy": bypass_policy,
        "messageType": event.rule or message_type,
    })
    message = notification_store().render(event.rule, context)
    job = NotificationJob.create(
        message,
        account_id=account.account_id,
        account_label=account.label,
        message_type=event.rule or message_type,
        context=context,
    )
    runner = build_notification_queue_runner(dry_run=dry_run)
    runner.apply_account_delivery_context(job, account)
    rendered_message = runner.render(job)
    if rendered_message:
        job.text = rendered_message
    if dry_run:
        return 200, {
            "delivered": False,
            "dryRun": True,
            "messageType": message_type,
            "direct": bypass_policy,
            "message": job.text,
            "event": alert_event_public_payload(event),
        }
    public_event = alert_event_public_payload(event)
    source_event = new_domain_event(
        NOTIFICATION_TEST_REQUESTED,
        event.key or message_type,
        {"messageType": message_type, "accountId": account.account_id, "accountLabel": account.label, "event": public_event},
    )
    job.source_event_id = source_event.event_id
    job.source_event_name = source_event.name
    if bypass_policy:
        store = notification_queue_store()
        job.status = "processing"
        job.attempts = 1
        job.updated_at = utc_now_iso()
        store.upsert_job(job)
        try:
            runner.deliver(job, {account.account_id: account}, job.text)
            operator_detail = runner.capture_operator_report_after_delivery(job, job.text)
            store.mark_done(job)
            return 200, {
                "delivered": True,
                "queued": False,
                "direct": True,
                "bypassPolicy": True,
                "jobId": job.job_id,
                "provider": "Notification Direct Test",
                "messageType": message_type,
                "operatorReportStatus": job.context.get("operatorReasoningReportStatus"),
                "operatorReportJobId": job.context.get("operatorReasoningReportJobId"),
                "operatorReportDetail": operator_detail,
                "event": public_event,
            }
        except Exception as error:  # noqa: BLE001 - expose direct test failures to the UI.
            store.mark_failed(job, str(error))
            return 502, {
                "delivered": False,
                "queued": False,
                "direct": True,
                "bypassPolicy": True,
                "jobId": job.job_id,
                "provider": "Notification Direct Test",
                "messageType": message_type,
                "event": public_event,
                "error": str(error),
            }
    if not notification_queue_store().enqueue(job):
        if job.status == "suppressed":
            return 202, {
                "delivered": False,
                "queued": False,
                "suppressed": True,
                "provider": "Notification Queue",
                "messageType": message_type,
                "event": public_event,
                "deliveryDecision": (job.context or {}).get("deliveryDecision"),
                "deliveryGateState": (job.context or {}).get("deliveryGateState"),
                "reasons": (job.context or {}).get("deliveryReasons") or [],
                "error": job.last_error,
            }
        return 409, {
            "delivered": False,
            "queued": False,
            "provider": "Notification Queue",
            "messageType": message_type,
            "event": public_event,
            "error": "알림 작업을 큐에 적재하지 못했습니다.",
        }
    new_domain_event(
        NOTIFICATION_JOB_QUEUED,
        job.job_id,
        {
            "jobId": job.job_id,
            "messageType": job.message_type,
            "accountId": job.account_id,
            "sourceEventId": source_event.event_id,
        },
    )
    return 202, {
        "delivered": False,
        "queued": True,
        "jobId": job.job_id,
        "provider": "Notification Queue",
        "messageType": message_type,
        "event": public_event,
    }


def account_service() -> AccountApplicationService:
    registry = stores.account_registry()
    return AccountApplicationService(registry, registry.settings, event_publisher=RealtimeEventBridge())


def symbol_universe_service():
    return build_symbol_universe_service()


def symbol_universe_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    search = first_query(query, "query") or first_query(query, "q")
    market = first_query(query, "market")
    limit = int(first_query(query, "limit") or 16)
    offset = int(first_query(query, "offset") or 0)
    try:
        return symbol_universe_service().search(
            query=search,
            market=market,
            limit=limit,
            offset=offset,
        )
    except Exception as error:  # noqa: BLE001 - seed universe keeps search usable without optional MySQL.
        items = fallback_symbol_universe_items(search, market)
        return {
            "items": items[offset: offset + limit],
            "summary": fallback_symbol_universe_summary(str(error)[:240]),
            "query": search or "",
            "market": market or "",
            "limit": limit,
            "offset": offset,
            "total": len(items),
            "storeWarning": str(error)[:240],
        }


def symbol_universe_suggest_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    search = first_query(query, "query") or first_query(query, "q")
    market = first_query(query, "market")
    limit = int(first_query(query, "limit") or 8)
    try:
        return symbol_universe_service().suggest(
            query=search,
            market=market,
            limit=limit,
        )
    except Exception as error:  # noqa: BLE001 - autocomplete can fall back to local seed symbols.
        return {
            "items": fallback_symbol_universe_items(search, market)[:limit],
            "query": search or "",
            "market": market or "",
            "limit": limit,
            "storeWarning": str(error)[:240],
        }


def fallback_symbol_universe_items(search: str = "", market: str = "") -> List[Dict[str, object]]:
    needle = configured(search).lower()
    market_filter = configured(market).upper()
    candidate_symbol_list = symbol_search_symbol_candidates(search)
    candidate_symbols = set(candidate_symbol_list)
    seed_symbols = list(DEFAULT_SYMBOL_SEEDS)
    for symbol in reversed(candidate_symbol_list):
        if symbol in seed_symbols:
            seed_symbols.remove(symbol)
        seed_symbols.insert(0, symbol)
    items = [seed_symbol(symbol).to_dict(24) for symbol in seed_symbols]
    if market_filter:
        items = [item for item in items if str(item.get("market") or "").upper() == market_filter]
    if needle:
        def matches(item):
            if str(item.get("symbol") or "").upper() in candidate_symbols:
                return True
            haystack = " ".join([
                str(item.get("symbol") or ""),
                str(item.get("name") or ""),
                str(item.get("market") or ""),
                str(item.get("sector") or ""),
            ]).lower()
            return needle in haystack

        items = [item for item in items if matches(item)]
    return items


def fallback_symbol_universe_summary(warning: str = "") -> Dict[str, object]:
    items = [seed_symbol(symbol).to_dict(24) for symbol in DEFAULT_SYMBOL_SEEDS]
    markets = []
    for market in sorted({str(item.get("market") or "") for item in items if item.get("market")}):
        markets.append({
            "market": market,
            "count": len([item for item in items if item.get("market") == market]),
            "lastSeenAt": "",
            "stale": True,
            "source": "Orbit Alpha seed",
            "sourceUrl": "local-default",
        })
    return {
        "markets": markets,
        "sources": [],
        "maxAgeHours": 24,
        "total": len(items),
        "storeWarning": warning,
    }


def refresh_symbol_universe_payload(payload: Dict[str, object]) -> Dict[str, object]:
    raw_markets = payload.get("markets") if isinstance(payload, dict) else None
    if isinstance(raw_markets, str):
        markets = [item.strip() for item in raw_markets.split(",") if item.strip()]
    elif isinstance(raw_markets, list):
        markets = [str(item or "").strip() for item in raw_markets if str(item or "").strip()]
    else:
        markets = None
    result = symbol_universe_service().refresh(markets)
    new_domain_event(
        SYMBOL_UNIVERSE_REFRESHED,
        ",".join(markets or []) or "all",
        {"summary": result.get("summary") or {}, "markets": markets or []},
    )
    return result


def service_accounts_payload() -> Dict[str, object]:
    return {"accounts": account_service().list_masked()}


def save_account_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return {"account": account_service().save_payload(payload).masked()}


def remove_account_payload(account_id: str) -> Dict[str, object]:
    return {"removed": account_service().remove(account_id), "id": account_id}


def parse_cookies(cookie_header: str) -> Dict[str, str]:
    cookies = {}
    for part in str(cookie_header or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if not key:
            continue
        cookies[key] = urllib.parse.unquote(value.strip())
    return cookies


def share_denied_page() -> str:
    return "".join([
        "<!doctype html>",
        '<html lang="ko"><head><meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width, initial-scale=1" />',
        "<title>Orbit Alpha 접근 제한</title>",
        "<style>body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f6f4ee;color:#171717}main{max-width:520px;padding:32px;line-height:1.6}h1{font-size:22px;margin:0 0 10px}p{margin:0;color:#5f5a53}</style>",
        "</head><body><main><h1>공유 접근 토큰이 필요합니다.</h1>",
        "<p>서버를 공유한 사람이 제공한 전체 URL로 다시 접속하세요.</p>",
        "</main></body></html>",
    ])


def parse_number(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def fetch_text(target_url: str, timeout: int = 8, headers: Dict[str, str] = None) -> str:
    def fetch() -> str:
        request = urllib.request.Request(
            target_url,
            headers={"User-Agent": "OrbitAlpha/0.1", **(headers or {})},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")

    return guarded_external_call(
        runtime_settings(),
        web_proxy_source_for_url(target_url),
        external_call_target(target_url),
        fetch,
        state=WEB_PROXY_API_GUARD_STATE,
        rate_limit_seconds=0,
    )


def fetch_json_url(target_url: str, timeout: int = 8, headers: Dict[str, str] = None):
    return json.loads(fetch_text(target_url, timeout=timeout, headers={"Accept": "application/json", **(headers or {})}))


def web_proxy_source_for_url(target_url: str) -> str:
    host = urllib.parse.urlparse(str(target_url or "")).netloc.lower()
    if "stlouisfed.org" in host:
        return "FRED"
    if "opendart.fss.or.kr" in host:
        return "OpenDART"
    if "m.stock.naver.com" in host:
        return "Naver Finance"
    if "stooq.com" in host:
        return "Stooq"
    return "Web Proxy"


def normalize_fred_observations_url(query: Dict[str, List[str]]) -> str:
    series_id = configured(first_query(query, "series_id")).upper()
    api_key = configured(first_query(query, "api_key"))
    limit = configured(first_query(query, "limit") or "1")
    sort_order = configured(first_query(query, "sort_order") or "desc").lower()
    if not re.match(r"^[A-Z0-9_.-]{1,40}$", series_id):
        raise ValueError("FRED series_id 형식이 올바르지 않습니다.")
    if not re.match(r"^[A-Za-z0-9]{16,64}$", api_key):
        raise ValueError("FRED_API_KEY 형식이 올바르지 않습니다.")
    if not re.match(r"^\d{1,4}$", limit):
        raise ValueError("FRED limit 형식이 올바르지 않습니다.")
    if sort_order not in {"asc", "desc"}:
        raise ValueError("FRED sort_order는 asc 또는 desc만 가능합니다.")
    params = urllib.parse.urlencode({
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "limit": limit,
        "sort_order": sort_order,
    })
    return "https://api.stlouisfed.org/fred/series/observations?" + params


def normalize_opendart_company_url(query: Dict[str, List[str]]) -> str:
    api_key = configured(first_query(query, "crtfc_key"))
    corp_code = configured(first_query(query, "corp_code") or "00126380")
    if not re.match(r"^[A-Za-z0-9]{32,64}$", api_key):
        raise ValueError("OpenDART API key 형식이 올바르지 않습니다.")
    if not re.match(r"^\d{8}$", corp_code):
        raise ValueError("OpenDART corp_code 형식이 올바르지 않습니다.")
    return "https://opendart.fss.or.kr/api/company.json?" + urllib.parse.urlencode({
        "crtfc_key": api_key,
        "corp_code": corp_code,
    })


def first_query(query: Dict[str, List[str]], key: str) -> str:
    value = query.get(key)
    if isinstance(value, list):
        return value[0] if value else ""
    return str(value or "")


def compact_flow_lens_payload(payload: Dict[str, object]) -> Dict[str, object]:
    """Keep initial dashboard payload small; full ontology rows load on demand."""
    if not isinstance(payload, dict):
        return payload
    compact = dict(payload)
    decision = compact.get("tossDecision")
    if not isinstance(decision, dict):
        return compact
    decision = dict(decision)
    compact["tossDecision"] = decision

    strategy = decision.get("ontologyStrategy")
    if isinstance(strategy, dict):
        omitted = []
        strategy = dict(strategy)
        for key in [
            "prompt",
            "reasoningCards",
            "entities",
            "relations",
            "tboxEntities",
            "tboxRelations",
            "aboxEntities",
            "aboxRelations",
            "evidence",
            "beliefs",
            "opinions",
            "activeInvestmentOpinions",
            "executionPlans",
            "insights",
            "dataQuality",
        ]:
            value = strategy.pop(key, None)
            if value not in (None, [], {}, ""):
                omitted.append(key)
                if isinstance(value, list):
                    strategy[key + "Count"] = len(value)
        strategy["detailLevel"] = "summary"
        strategy["detailAvailable"] = True
        strategy["heavyFieldsOmitted"] = omitted
        decision["ontologyStrategy"] = strategy

    analysis = decision.get("investmentAnalysis")
    if isinstance(analysis, dict):
        analysis = dict(analysis)
        reasoning_cards = analysis.pop("reasoningCards", None)
        if isinstance(reasoning_cards, list):
            analysis["reasoningCardCount"] = len(reasoning_cards)
        analysis["detailLevel"] = "summary"
        analysis["detailAvailable"] = True
        decision["investmentAnalysis"] = analysis

    compact["payloadDetail"] = "summary"
    compact["fullDetailPath"] = "/api/flow-lens?detail=full"
    return compact


def category_for(value: str) -> str:
    text = str(value or "")
    if re.search(r"주식|투자|종목|포트폴리오|배당|매수|매도", text):
        return "finance"
    if re.search(r"자산|현금|계좌|예산|지출|저축|대출", text):
        return "asset"
    if re.search(r"여행|항공|호텔|숙소|동선|예약", text):
        return "travel"
    if re.search(r"일정|회의|약속|마감|캘린더|할 일", text):
        return "schedule"
    if re.search(r"좋아|싫어|선호|말투|스타일|방식", text):
        return "preference"
    if re.search(r"나는|내가|나의|목표|직업|역할", text):
        return "identity"
    return "other"


def normalize_amount(value):
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return text


def normalize_item_fields(fields) -> Dict[str, str]:
    if not isinstance(fields, dict):
        return {}
    return {str(key): "" if value is None else str(value).strip() for key, value in fields.items()}


def patch_item(item: Dict[str, object], body: Dict[str, object]) -> Dict[str, object]:
    next_item = dict(item)
    if body.get("type") in DOMAIN_TYPES:
        next_item["type"] = body["type"]
    if "title" in body and configured(body.get("title")):
        next_item["title"] = configured(body.get("title"))
    if "status" in body:
        next_item["status"] = configured(body.get("status")) or "open"
    if "date" in body:
        next_item["date"] = configured(body.get("date"))
    if "amount" in body:
        next_item["amount"] = normalize_amount(body.get("amount"))
    if "currency" in body:
        next_item["currency"] = configured(body.get("currency"))
    if "ticker" in body:
        next_item["ticker"] = configured(body.get("ticker")).upper()
    if "location" in body:
        next_item["location"] = configured(body.get("location"))
    if "notes" in body:
        next_item["notes"] = configured(body.get("notes"))
    if "fields" in body:
        next_item["fields"] = {**dict(next_item.get("fields") or {}), **normalize_item_fields(body.get("fields"))}
    next_item["updatedAt"] = now()
    return next_item


def fallback_reply(message: str) -> str:
    text = configured(message)
    if re.search(r"주식|투자|종목|포트폴리오", text):
        return "투자 판단은 매수/매도 단정보다 가격 기준, 손절 기준, 보유 이유, 현금 비중을 나눠 확인하겠습니다."
    if re.search(r"일정|회의|약속|마감", text):
        return "일정은 오늘 처리할 일, 미룰 일, 의존성이 있는 일을 분리해서 정리하겠습니다."
    if re.search(r"여행|항공|호텔|숙소", text):
        return "여행 계획은 날짜, 예산, 이동 동선, 예약 마감일을 기준으로 정리하겠습니다."
    return "기록했습니다. 필요한 내용을 주식, 여행, 자산, 일정 중 어느 쪽으로 정리할지 알려주면 다음 행동으로 나누겠습니다."


def local_memory_candidates(message: str) -> List[Dict[str, object]]:
    text = configured(message)
    if len(text) < 12:
        return []
    signals = ["나는", "내가", "나의", "선호", "좋아", "싫어", "원해", "중요", "성향", "스타일", "방식", "투자", "여행", "일정", "자산", "목표"]
    if not any(signal in text for signal in signals):
        return []
    normalized = re.sub(r"^(나는|내가|나의)\s*", "", text).strip()
    return [{
        "content": ("사용자는 " + normalized)[:180],
        "category": category_for(text),
        "importance": 4 if re.search(r"선호|싫어|좋아|중요|원해|성향|방식|스타일", text) else 3,
    }]


def memory_fingerprint(content: str) -> str:
    return re.sub(r"[.,!?'\"]", "", re.sub(r"\s+", "", str(content or "").lower())).removeprefix("사용자는")


def persist_memory_candidates(candidates: List[Dict[str, object]]) -> List[Dict[str, object]]:
    saved = []
    if not candidates:
        return saved

    def mutate(store):
        for candidate in candidates[:3]:
            content = configured(candidate.get("content"))
            if len(content) < 5:
                continue
            category = candidate.get("category") if candidate.get("category") in MEMORY_CATEGORIES else category_for(content)
            next_fingerprint = memory_fingerprint(content)
            duplicate = any(
                memory.get("status") != "archived"
                and memory.get("category") == category
                and memory_fingerprint(memory.get("content")).find(next_fingerprint) >= 0
                for memory in store["memories"]
            )
            if duplicate:
                continue
            stamped = now()
            memory = {
                "id": new_id("mem"),
                "content": content,
                "category": category,
                "status": "approved",
                "importance": max(1, min(5, int(candidate.get("importance") or 3))),
                "source": "conversation",
                "createdAt": stamped,
                "updatedAt": stamped,
            }
            store["memories"].insert(0, memory)
            saved.append(memory)

    save_store(mutate)
    if saved:
        new_domain_event(
            APP_MEMORY_RECORDED,
            "conversation",
            {"count": len(saved), "memoryIds": [item.get("id") for item in saved], "source": "conversation"},
        )
    return saved


def append_message(role: str, content: str) -> Dict[str, object]:
    message = {}

    def mutate(store):
        message.update({"id": new_id("msg"), "role": role, "content": content, "createdAt": now()})
        store["messages"].append(message)
        store["messages"] = store["messages"][-80:]

    save_store(mutate)
    new_domain_event(
        CHAT_MESSAGE_APPENDED,
        message.get("id") or role,
        {"messageId": message.get("id"), "role": role},
    )
    return message


def run_local_codex(message: str) -> str:
    if os.environ.get("LOCAL_CODEX_ENABLED") == "0":
        return ""
    codex = os.environ.get("CODEX_BIN") or "codex"
    prompt = "\n".join([
        "너는 Orbit Alpha 웹앱의 로컬 Python 비서 백엔드다.",
        "한국어로 답하고, 투자 관련 답변은 확인할 데이터와 리스크 중심으로만 말한다.",
        "파일을 수정하지 말고 설명만 한다.",
        "",
        "사용자 질문:",
        message,
    ])
    with tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8") as output:
        output_path = output.name
    try:
        result = subprocess.run(
            [
                codex,
                "-a",
                "never",
                "--sandbox",
                "read-only",
                "--cd",
                str(ROOT_DIR),
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "--output-last-message",
                output_path,
                "-",
            ],
            input=prompt,
            text=True,
            cwd=str(ROOT_DIR),
            env={**os.environ, "NO_COLOR": "1"},
            timeout=int(os.environ.get("CODEX_TIMEOUT_MS") or "90000") / 1000,
            capture_output=True,
        )
        if result.returncode != 0:
            return ""
        return Path(output_path).read_text(encoding="utf-8").strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    finally:
        try:
            Path(output_path).unlink()
        except OSError:
            pass


def is_investment_brain_question(message: str, body: Dict[str, object] = None) -> bool:
    body = body if isinstance(body, dict) else {}
    if configured(body.get("mode") or body.get("engine")).lower() in {"investment", "ontology", "investment-brain"}:
        return True
    compact = str(message or "").lower()
    return any(term in compact for term in [
        "주식", "종목", "매수", "매도", "보유", "추가매수", "분할축소", "손절",
        "포트폴리오", "수익률", "투자", "리스크", "공시", "주가", "증권",
    ])


def investment_brain_question_payload(body: Dict[str, object]) -> Dict[str, object]:
    message = configured(body.get("message") or body.get("question"))
    if not message:
        raise ValueError("투자 질문을 입력하세요.")
    result = build_investment_brain_service().ask(
        message,
        account_id=configured(body.get("accountId")),
        symbol=configured(body.get("symbol")),
    )
    return result


def chat_payload(body: Dict[str, object]) -> Dict[str, object]:
    message = configured(body.get("message"))
    if not message:
        raise ValueError("메시지를 입력하세요.")
    append_message("user", message)
    if is_investment_brain_question(message, body):
        result = investment_brain_question_payload(body)
        append_message("assistant", str(result.get("reply") or ""))
        return result
    reply = run_local_codex(message) or fallback_reply(message)
    candidates = persist_memory_candidates(local_memory_candidates(message))
    append_message("assistant", reply)
    return {"reply": reply, "memoryCandidates": candidates, "usedFallback": True, "engine": "python"}


def stock_input_to_naver_code(symbol: str) -> str:
    match = re.match(r"^(\d{6})(?:\.(KS|KQ|KR))?$", configured(symbol).upper())
    return match.group(1) if match else ""


def stock_input_to_stooq_symbol(symbol: str) -> str:
    cleaned = configured(symbol).upper()
    if not cleaned or stock_input_to_naver_code(cleaned):
        return ""
    return cleaned if "." in cleaned else cleaned + ".US"


def fetch_naver_quote(symbol: str) -> Dict[str, object]:
    code = stock_input_to_naver_code(symbol)
    payload = fetch_json_url("https://m.stock.naver.com/api/stock/" + code + "/basic")
    price = parse_number(payload.get("closePrice"))
    if price is None:
        raise ValueError("국내 종목 가격을 찾지 못했습니다.")
    return {
        "inputSymbol": symbol,
        "symbol": code,
        "displaySymbol": code,
        "name": payload.get("stockName") or code,
        "exchange": payload.get("stockExchangeName") or "KR",
        "currency": "KRW",
        "price": price,
        "previousClose": None,
        "change": parse_number(payload.get("compareToPreviousClosePrice")),
        "changePercent": parse_number(payload.get("fluctuationsRatio")),
        "open": None,
        "high": None,
        "low": None,
        "volume": parse_number(payload.get("accumulatedTradingVolume")),
        "marketStatus": payload.get("marketStatus") or "",
        "asOf": payload.get("localTradedAt") or "",
        "source": "Naver Finance",
    }


def fetch_stooq_quote(symbol: str) -> Dict[str, object]:
    stooq_symbol = stock_input_to_stooq_symbol(symbol)
    raw = fetch_text("https://stooq.com/q/l/?s=" + urllib.parse.quote(stooq_symbol.lower()) + "&f=sd2t2ohlcvpn&h&e=csv")
    rows = raw.strip().splitlines()
    if len(rows) < 2:
        raise ValueError("해외 종목 가격을 찾지 못했습니다.")
    header = next(csv.reader([rows[0]]))
    values = next(csv.reader([rows[1]]))
    row = dict(zip(header, values))
    close = parse_number(row.get("Close"))
    if close is None:
        raise ValueError("해외 종목 가격을 찾지 못했습니다. 미국 종목은 AAPL, TSLA처럼 입력하거나 거래소 접미사를 붙여 주세요.")
    previous_close = parse_number(row.get("Prev"))
    change = close - previous_close if previous_close is not None else None
    change_percent = (change / previous_close) * 100 if previous_close else None
    return {
        "inputSymbol": symbol,
        "symbol": row.get("Symbol") or stooq_symbol,
        "displaySymbol": re.sub(r"\.US$", "", row.get("Symbol") or stooq_symbol, flags=re.I),
        "name": row.get("Name") or configured(symbol).upper(),
        "exchange": (row.get("Symbol") or stooq_symbol).split(".")[1] if "." in (row.get("Symbol") or stooq_symbol) else "US",
        "currency": "USD",
        "price": close,
        "previousClose": previous_close,
        "change": change,
        "changePercent": change_percent,
        "open": parse_number(row.get("Open")),
        "high": parse_number(row.get("High")),
        "low": parse_number(row.get("Low")),
        "volume": parse_number(row.get("Volume")),
        "marketStatus": "DELAYED",
        "asOf": " ".join([row.get("Date") or "", row.get("Time") or ""]).strip(),
        "source": "Stooq",
    }


def fetch_quote(symbol: str) -> Dict[str, object]:
    return fetch_naver_quote(symbol) if stock_input_to_naver_code(symbol) else fetch_stooq_quote(symbol)


def stock_snapshot(symbol: str) -> Dict[str, object]:
    clean = configured(symbol)
    try:
        quote = fetch_quote(clean)
        return {"inputSymbol": clean, "quote": quote, "news": [], "error": ""}
    except Exception as error:
        return {"inputSymbol": clean, "quote": None, "news": [], "error": str(error) or "종목 정보를 가져오지 못했습니다."}


class DigitalTwinHandler(BaseHTTPRequestHandler):
    server_version = "DigitalTwinPython/0.1"

    def log_message(self, format, *args):
        if os.environ.get("WEB_SERVER_LOG_REQUESTS") == "1":
            super().log_message(format, *args)

    def do_OPTIONS(self):
        self.handle_request()

    def do_GET(self):
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self.handle_websocket()
            return
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_PUT(self):
        self.handle_request()

    def do_PATCH(self):
        self.handle_request()

    def do_DELETE(self):
        self.handle_request()

    def parsed(self):
        return urllib.parse.urlsplit(self.path)

    def parsed_query(self) -> Dict[str, List[str]]:
        return urllib.parse.parse_qs(self.parsed().query, keep_blank_values=True)

    def path_name(self) -> str:
        return urllib.parse.unquote(self.parsed().path or "/")

    def handle_websocket(self):
        if self.path_name() != "/ws":
            return self.send_payload(404, {"error": "웹소켓 엔드포인트를 찾지 못했습니다."})
        if not self.authorize_share():
            return
        key = configured(self.headers.get("Sec-WebSocket-Key"))
        if not key:
            return self.send_payload(400, {"error": "Sec-WebSocket-Key가 필요합니다."})
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", websocket_accept_key(key))
        self.end_headers()
        self.close_connection = True
        client = self.connection
        REALTIME_HUB.add(client)
        REALTIME_HUB.send(client, {
            "type": "realtime.connected",
            "payload": realtime_status_payload(),
            "occurredAt": now(),
        })
        try:
            while True:
                readable, _, _ = select.select([client], [], [], 25)
                if not readable:
                    if not REALTIME_HUB.send(client, {"type": "realtime.status", "payload": realtime_status_payload(), "occurredAt": now()}):
                        break
                    continue
                opcode, payload = read_websocket_frame(client)
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    REALTIME_HUB.send(client, payload, opcode=0xA)
                    continue
                if opcode == 0x1 and payload.strip() == b"ping":
                    REALTIME_HUB.send(client, {"type": "realtime.pong", "payload": realtime_status_payload(), "occurredAt": now()})
        except (OSError, ValueError, socket.timeout):
            pass
        finally:
            REALTIME_HUB.remove(client)
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def read_json_body(self) -> Dict[str, object]:
        length = int(self.headers.get("Content-Length") or "0")
        if length > MAX_BODY_BYTES:
            raise ValueError("요청이 너무 큽니다.")
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def send_payload(self, status: int, payload, content_type: str = "application/json; charset=utf-8", cors: bool = False):
        no_body = status in {204, 304} or self.command == "HEAD"
        body = b"" if no_body else (
            json.dumps(payload, ensure_ascii=False).encode("utf-8") if content_type.startswith("application/json") else (
                payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
            )
        )
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            if cors:
                self.add_cors_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not no_body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def add_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Accept, Authorization, Content-Type, Cache-Control, Pragma, X-Requested-With")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin, Access-Control-Request-Headers, Access-Control-Request-Private-Network")

    def send_redirect(self, location: str, cookie: str = ""):
        self.send_response(302)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def authorize_share(self) -> bool:
        expected = configured(os.environ.get("SHARE_TOKEN"))
        if not expected:
            return True
        parsed = self.parsed()
        query = self.parsed_query()
        supplied = first_query(query, "share_token")
        if supplied and hmac.compare_digest(supplied, expected):
            clean_query = {key: values for key, values in query.items() if key != "share_token"}
            encoded = urllib.parse.urlencode(clean_query, doseq=True)
            clean_path = parsed.path + (("?" + encoded) if encoded else "")
            self.send_redirect(
                clean_path,
                "dt_share_token=" + urllib.parse.quote(supplied) + "; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400",
            )
            return False
        cookies = parse_cookies(self.headers.get("Cookie", ""))
        if hmac.compare_digest(cookies.get("dt_share_token", ""), expected):
            return True
        if self.path_name().startswith("/api/"):
            self.send_payload(401, {"error": "공유 접근 토큰이 필요합니다."})
        else:
            self.send_payload(401, share_denied_page(), "text/html; charset=utf-8")
        return False

    def handle_request(self):
        if not self.authorize_share():
            return
        path = self.path_name()
        try:
            if path.startswith("/api/"):
                self.handle_api(path)
            else:
                self.serve_static(path)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
        except ValueError as error:
            self.send_payload(400, {"error": str(error) or "잘못된 요청입니다."})
        except (urllib.error.URLError, TimeoutError, ExternalCircuitOpen, ExternalRateLimited) as error:
            report_runtime_error(operational_error_reporter(), "Python web server", error, "HTTP 502 " + path)
            self.send_payload(502, {"error": str(error) or "외부 데이터 요청 실패"})
        except Exception as error:
            report_runtime_error(operational_error_reporter(), "Python web server", error, "HTTP 500 " + path)
            self.send_payload(500, {"error": str(error) or "서버 오류"})

    def ensure_writable(self, message: str) -> bool:
        if configured(os.environ.get("SHARE_TOKEN")):
            self.send_payload(403, {"error": message})
            return False
        return True

    def handle_api(self, path: str):
        query = self.parsed_query()
        if path == "/api/service-accounts":
            if self.command == "GET":
                return self.send_payload(200, service_accounts_payload())
            if self.command in {"POST", "PUT"}:
                if not self.ensure_writable("공유 모드에서는 계정 DB를 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_account_payload(self.read_json_body()))

        account_match = re.match(r"^/api/service-accounts/([^/]+)$", path)
        if account_match and self.command == "DELETE":
            if not self.ensure_writable("공유 모드에서는 계정 DB를 변경할 수 없습니다."):
                return
            return self.send_payload(200, remove_account_payload(urllib.parse.unquote(account_match.group(1))))

        if path == "/api/settings":
            if self.command == "GET":
                return self.send_payload(200, settings_status_payload())
            if self.command == "PUT":
                if not self.ensure_writable("공유 모드에서는 서버 설정을 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_settings_payload(self.read_json_body()))

        if path == "/api/ontology/rulebox":
            if self.command == "GET":
                return self.send_payload(200, ontology_rulebox_payload())
            if self.command in {"POST", "PUT"}:
                if not self.ensure_writable("공유 모드에서는 TypeDB RuleBox를 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_ontology_rulebox_payload(self.read_json_body()))

        if path == "/api/ontology/language":
            if self.command == "GET":
                return self.send_payload(200, ontology_language_payload())
            if self.command in {"POST", "PUT"}:
                if not self.ensure_writable("공유 모드에서는 보편언어 사전을 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_ontology_language_payload(self.read_json_body()))

        if path == "/api/ontology/language/validate" and self.command == "POST":
            return self.send_payload(200, validate_ontology_language_payload(self.read_json_body()))

        if path == "/api/ontology/language/preview" and self.command == "POST":
            return self.send_payload(200, preview_ontology_language_payload(self.read_json_body()))

        if path == "/api/ontology/language/suggest" and self.command == "POST":
            return self.send_payload(200, suggest_ontology_language_payload(self.read_json_body()))

        if path == "/api/ontology/rulebox/run" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 TypeDB 네이티브 규칙 추론을 실행할 수 없습니다."):
                return
            return self.send_payload(200, run_ontology_rulebox_payload(self.read_json_body()))

        if path == "/api/ontology/diagnostics" and self.command == "GET":
            return self.send_payload(200, ontology_diagnostics_payload(query))

        if path == "/api/ontology/inference-ledger" and self.command == "GET":
            return self.send_payload(200, ontology_inference_ledger_api_payload(query))

        if path == "/api/ontology/audit" and self.command == "GET":
            return self.send_payload(200, ontology_audit_payload(query))

        ontology_audit_match = re.match(r"^/api/ontology/audit/([^/]+)$", path)
        if ontology_audit_match and self.command == "GET":
            return self.send_payload(200, ontology_audit_payload(
                query,
                urllib.parse.unquote(ontology_audit_match.group(1)),
            ))

        if path == "/api/ontology/rulebox/candidates" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 TypeDB RuleBox 후보를 생성할 수 없습니다."):
                return
            return self.send_payload(200, propose_ontology_rule_candidates_payload(self.read_json_body()))

        if path == "/api/ontology/seed" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 그래프 시드를 실행할 수 없습니다."):
                return
            return self.send_payload(200, seed_ontology_payload(self.read_json_body()))

        if path == "/api/ontology/experiments" and self.command == "GET":
            return self.send_payload(200, list_ontology_experiments_payload())

        if path == "/api/ontology/experiments" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 실험을 생성할 수 없습니다."):
                return
            return self.send_payload(200, create_ontology_experiment_payload(self.read_json_body()))

        if path == "/api/ontology/experiments/status" and self.command == "GET":
            return self.send_payload(200, ontology_experiments_status_payload())

        if path == "/api/ontology/experiments/once" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 실험을 실행할 수 없습니다."):
                return
            return self.send_payload(200, run_ontology_experiments_once_payload(self.read_json_body()))

        if path == "/api/ontology/experiments/suggest" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 AI 온톨로지 실험 제안을 생성할 수 없습니다."):
                return
            return self.send_payload(200, suggest_ontology_experiments_payload(self.read_json_body()))

        if path == "/api/ontology/experiments/apply" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 실험 제안을 운영 반영할 수 없습니다."):
                return
            return self.send_payload(200, apply_ontology_experiments_batch_payload(self.read_json_body()))

        ontology_experiment_run_match = re.match(r"^/api/ontology/experiments/([^/]+)/run$", path)
        if ontology_experiment_run_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 실험을 실행할 수 없습니다."):
                return
            return self.send_payload(200, run_ontology_experiment_payload(
                urllib.parse.unquote(ontology_experiment_run_match.group(1)),
                self.read_json_body(),
            ))

        ontology_experiment_apply_match = re.match(r"^/api/ontology/experiments/([^/]+)/apply$", path)
        if ontology_experiment_apply_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 실험 제안을 운영 반영할 수 없습니다."):
                return
            return self.send_payload(200, apply_ontology_experiment_payload(
                urllib.parse.unquote(ontology_experiment_apply_match.group(1)),
                self.read_json_body(),
            ))

        ontology_experiment_activate_match = re.match(r"^/api/ontology/experiments/([^/]+)/activate$", path)
        if ontology_experiment_activate_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 실험 상태를 변경할 수 없습니다."):
                return
            return self.send_payload(200, activate_ontology_experiment_payload(
                urllib.parse.unquote(ontology_experiment_activate_match.group(1)),
            ))

        ontology_experiment_pause_match = re.match(r"^/api/ontology/experiments/([^/]+)/pause$", path)
        if ontology_experiment_pause_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 실험 상태를 변경할 수 없습니다."):
                return
            return self.send_payload(200, pause_ontology_experiment_payload(
                urllib.parse.unquote(ontology_experiment_pause_match.group(1)),
            ))

        ontology_experiment_match = re.match(r"^/api/ontology/experiments/([^/]+)$", path)
        if ontology_experiment_match and self.command == "GET":
            return self.send_payload(200, ontology_experiment_payload(urllib.parse.unquote(ontology_experiment_match.group(1))))

        if path == "/api/investment-strategy-proposals" and self.command == "GET":
            return self.send_payload(200, list_investment_strategy_proposals_payload())

        if path == "/api/investment-strategy-proposals/status" and self.command == "GET":
            return self.send_payload(200, investment_strategy_proposals_status_payload())

        strategy_proposal_action_match = re.match(r"^/api/investment-strategy-proposals/([^/]+)/(validate|approve|performance)$", path)
        if strategy_proposal_action_match:
            proposal_id = urllib.parse.unquote(strategy_proposal_action_match.group(1))
            action = strategy_proposal_action_match.group(2)
            if action == "performance" and self.command == "GET":
                return self.send_payload(200, investment_strategy_proposal_performance_payload(proposal_id))
            if self.command == "POST":
                if action == "validate":
                    if not self.ensure_writable("공유 모드에서는 투자 전략 제안을 검증할 수 없습니다."):
                        return
                    return self.send_payload(200, validate_investment_strategy_proposal_payload(proposal_id, self.read_json_body()))
                if action == "approve":
                    if not self.ensure_writable("공유 모드에서는 투자 전략 제안을 승인할 수 없습니다."):
                        return
                    return self.send_payload(200, approve_investment_strategy_proposal_payload(proposal_id, self.read_json_body()))
                if action == "performance":
                    if not self.ensure_writable("공유 모드에서는 투자 전략 성과를 기록할 수 없습니다."):
                        return
                    return self.send_payload(200, record_investment_strategy_proposal_performance_payload(proposal_id, self.read_json_body()))

        strategy_proposal_match = re.match(r"^/api/investment-strategy-proposals/([^/]+)$", path)
        if strategy_proposal_match and self.command == "GET":
            return self.send_payload(200, investment_strategy_proposal_payload(urllib.parse.unquote(strategy_proposal_match.group(1))))

        if path == "/api/symbol-universe":
            if self.command == "GET":
                return self.send_payload(200, symbol_universe_payload(query))

        if path == "/api/symbol-universe/suggest":
            if self.command == "GET":
                return self.send_payload(200, symbol_universe_suggest_payload(query))

        if path == "/api/symbol-universe/refresh" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 종목 유니버스를 갱신할 수 없습니다."):
                return
            return self.send_payload(200, refresh_symbol_universe_payload(self.read_json_body()))

        if path == "/api/notification-templates":
            if self.command == "GET":
                return self.send_payload(200, list_templates_payload())
            if self.command in {"POST", "PUT"}:
                if not self.ensure_writable("공유 모드에서는 알림 템플릿을 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_template_payload(self.read_json_body()))

        if path == "/api/notification-rules":
            if self.command == "GET":
                return self.send_payload(200, list_notification_rules_payload(include_internal_notification_query(query)))
            if self.command in {"POST", "PUT"}:
                if not self.ensure_writable("공유 모드에서는 알림 룰을 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_notification_rule_payload(self.read_json_body()))

        if path == "/api/notification-jobs" and self.command == "GET":
            return self.send_payload(200, notification_jobs_payload(query))

        if path == "/api/notification-jobs/replay" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 알림을 재발송할 수 없습니다."):
                return
            return self.send_payload(200, replay_notification_payload(self.read_json_body()))

        if path == "/api/research-evidence" and self.command == "GET":
            return self.send_payload(200, research_evidence_payload(query))

        if path == "/api/investment-calendar/events":
            if self.command == "GET":
                return self.send_payload(200, investment_calendar_payload(query))
            if self.command in {"POST", "PUT"}:
                if not self.ensure_writable("공유 모드에서는 투자 캘린더 이벤트를 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_investment_calendar_event_payload(self.read_json_body()))

        if path == "/api/investment-calendar/candidates" and self.command == "GET":
            return self.send_payload(200, investment_calendar_candidates_payload(query))

        if path == "/api/investment-calendar/candidates/research" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 AI 리서치 캘린더 후보를 생성할 수 없습니다."):
                return
            return self.send_payload(200, research_investment_calendar_candidates_payload(self.read_json_body()))

        calendar_candidate_match = re.match(r"^/api/investment-calendar/candidates/([^/]+)/(approve|reject)$", path)
        if calendar_candidate_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 투자 캘린더 후보를 검토할 수 없습니다."):
                return
            candidate_id = urllib.parse.unquote(calendar_candidate_match.group(1))
            action = calendar_candidate_match.group(2)
            if action == "approve":
                return self.send_payload(200, approve_investment_calendar_candidate_payload(candidate_id, self.read_json_body()))
            return self.send_payload(200, reject_investment_calendar_candidate_payload(candidate_id, self.read_json_body()))

        if path == "/api/investment-calendar/reminders/run" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 투자 캘린더 알림을 큐잉할 수 없습니다."):
                return
            return self.send_payload(200, investment_calendar_reminders_once_payload())

        if path == "/api/investment-calendar/sync-official" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 공식 투자 일정을 동기화할 수 없습니다."):
                return
            return self.send_payload(200, investment_calendar_sync_official_payload())

        calendar_event_match = re.match(r"^/api/investment-calendar/events/([^/]+)$", path)
        if calendar_event_match:
            event_id = urllib.parse.unquote(calendar_event_match.group(1))
            if self.command == "GET":
                payload = investment_calendar_payload({"limit": ["500"]})
                payload["event"] = next((item for item in payload.get("events") or [] if item.get("eventId") == event_id), None)
                return self.send_payload(200 if payload.get("event") else 404, payload if payload.get("event") else {"error": "투자 캘린더 이벤트를 찾지 못했습니다."})
            if self.command == "DELETE":
                if not self.ensure_writable("공유 모드에서는 투자 캘린더 이벤트를 변경할 수 없습니다."):
                    return
                return self.send_payload(200, delete_investment_calendar_event_payload(event_id))

        evidence_match = re.match(r"^/api/research-evidence/([^/]+)$", path)
        if evidence_match and self.command == "DELETE":
            if not self.ensure_writable("공유 모드에서는 저장된 리서치 근거를 변경할 수 없습니다."):
                return
            evidence_id = urllib.parse.unquote(evidence_match.group(1))
            return self.send_payload(200, delete_research_evidence_payload(evidence_id, query))

        if path == "/api/notification-schedules" and self.command == "GET":
            return self.send_payload(200, notification_schedules_payload(include_internal_notification_query(query)))

        if path == "/api/notification-templates/test-send" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 실제 알림을 발송할 수 없습니다."):
                return
            status, payload = notification_template_test_payload(self.read_json_body())
            return self.send_payload(status, payload)

        template_match = re.match(r"^/api/notification-templates/([^/]+)$", path)
        if template_match and self.command == "DELETE":
            if not self.ensure_writable("공유 모드에서는 알림 템플릿을 변경할 수 없습니다."):
                return
            return self.send_payload(200, reset_template_payload(urllib.parse.unquote(template_match.group(1))))

        rule_match = re.match(r"^/api/notification-rules/([^/]+)$", path)
        if rule_match and self.command == "DELETE":
            if not self.ensure_writable("공유 모드에서는 알림 룰을 변경할 수 없습니다."):
                return
            return self.send_payload(200, reset_notification_rule_payload(urllib.parse.unquote(rule_match.group(1))))

        if path == "/api/data-api/fred/observations":
            if self.command == "OPTIONS":
                return self.send_payload(204, {}, cors=True)
            return self.send_payload(200, fetch_json_url(normalize_fred_observations_url(query)), cors=True)

        if path == "/api/data-api/opendart/company":
            if self.command == "OPTIONS":
                return self.send_payload(204, {}, cors=True)
            return self.send_payload(200, fetch_json_url(normalize_opendart_company_url(query)), cors=True)

        if path == "/api/mock-market/scenarios":
            if self.command == "OPTIONS":
                return self.send_payload(204, {}, cors=True)
            return self.send_payload(200, mock_market_scenario_list(), cors=True)

        if path == "/api/mock-market/candles":
            if self.command == "OPTIONS":
                return self.send_payload(204, {}, cors=True)
            flat_query = {key: first_query(query, key) for key in query}
            return self.send_payload(200, mock_market_payload(flat_query), cors=True)

        if path == "/api/flow-lens" and self.command == "GET":
            mock_value = configured(first_query(query, "mock") or first_query(query, "mode")).lower()
            detail = configured(first_query(query, "detail") or first_query(query, "view")).lower()
            payload = flow_lens_snapshot(
                mock=mock_value in {"1", "true", "mock"},
                watchlist_symbols=first_query(query, "watchlistSymbols"),
            )
            if detail not in {"full", "detail", "all"}:
                payload = compact_flow_lens_payload(payload)
            return self.send_payload(200, payload)

        if path == "/api/investment-analysis" and self.command == "GET":
            mock_value = configured(first_query(query, "mock") or first_query(query, "mode")).lower()
            return self.send_payload(200, investment_analysis_snapshot(
                mock=mock_value in {"1", "true", "mock"},
                watchlist_symbols=first_query(query, "watchlistSymbols"),
            ))

        if path == "/api/bootstrap" and self.command == "GET":
            return self.send_payload(200, snapshot_payload())

        if path == "/api/realtime/status" and self.command == "GET":
            return self.send_payload(200, realtime_status_payload())

        if path == "/api/profile" and self.command == "PUT":
            body = self.read_json_body()
            if not body.get("ownerName") or not body.get("assistantName"):
                return self.send_payload(400, {"error": "이름과 비서 이름은 필요합니다."})
            store = save_store(lambda draft: draft.update({"profile": {**draft["profile"], **body}}))
            new_domain_event(
                APP_PROFILE_UPDATED,
                "profile",
                {
                    "ownerName": store["profile"].get("ownerName"),
                    "assistantName": store["profile"].get("assistantName"),
                },
            )
            return self.send_payload(200, {"profile": store["profile"]})

        if path == "/api/chat" and self.command == "POST":
            return self.send_payload(200, chat_payload(self.read_json_body()))

        if path == "/api/investment-brain/questions" and self.command == "POST":
            return self.send_payload(200, investment_brain_question_payload(self.read_json_body()))

        if path == "/api/investment-brain/episodes" and self.command == "GET":
            try:
                limit = int(first_query(query, "limit") or 50)
            except ValueError:
                limit = 50
            return self.send_payload(200, build_investment_brain_service().episodes(
                account_id=first_query(query, "accountId"),
                symbol=first_query(query, "symbol"),
                limit=limit,
            ))

        if path == "/api/investment-brain/performance" and self.command == "GET":
            try:
                limit = int(first_query(query, "limit") or 500)
            except ValueError:
                limit = 500
            return self.send_payload(200, build_investment_brain_service().performance(
                account_id=first_query(query, "accountId"),
                symbol=first_query(query, "symbol"),
                limit=limit,
            ))

        if path == "/api/investment-brain/hypothesis-templates" and self.command == "GET":
            return self.send_payload(200, build_investment_brain_service().hypothesis_templates())

        if path == "/api/investment-brain/research-runs" and self.command == "GET":
            try:
                limit = int(first_query(query, "limit") or 50)
            except ValueError:
                limit = 50
            return self.send_payload(200, build_investment_brain_service().research_runs(
                account_id=first_query(query, "accountId"),
                symbol=first_query(query, "symbol"),
                limit=limit,
            ))

        if path == "/api/investment-brain/hypothesis-proposals" and self.command == "GET":
            try:
                limit = int(first_query(query, "limit") or 50)
            except ValueError:
                limit = 50
            return self.send_payload(200, build_investment_brain_service().hypothesis_proposals(
                status=first_query(query, "status"),
                symbol=first_query(query, "symbol"),
                limit=limit,
            ))

        hypothesis_proposal_match = re.match(r"^/api/investment-brain/hypothesis-proposals/([^/]+)$", path)
        if hypothesis_proposal_match and self.command == "PATCH":
            body = self.read_json_body()
            return self.send_payload(200, build_investment_brain_service().review_hypothesis_proposal(
                hypothesis_proposal_match.group(1),
                configured(body.get("status")),
                configured(body.get("note")),
            ))

        if path == "/api/investment-brain/learning-proposals" and self.command == "GET":
            try:
                limit = int(first_query(query, "limit") or 50)
            except ValueError:
                limit = 50
            return self.send_payload(200, build_investment_brain_service().learning_proposals(
                status=first_query(query, "status"),
                limit=limit,
            ))

        learning_match = re.match(r"^/api/investment-brain/learning-proposals/([^/]+)$", path)
        if learning_match and self.command == "PATCH":
            body = self.read_json_body()
            return self.send_payload(200, build_investment_brain_service().review_learning_proposal(
                learning_match.group(1),
                configured(body.get("status")),
                configured(body.get("note")),
            ))

        if path == "/api/memories":
            if self.command == "GET":
                return self.send_payload(200, {"memories": read_store()["memories"]})
            if self.command == "POST":
                body = self.read_json_body()
                content = configured(body.get("content"))
                if not content:
                    return self.send_payload(400, {"error": "기억 내용을 입력하세요."})
                stamped = now()
                memory = {
                    "id": new_id("mem"),
                    "content": content,
                    "category": body.get("category") if body.get("category") in MEMORY_CATEGORIES else "other",
                    "status": "candidate" if body.get("status") == "candidate" else "approved",
                    "importance": max(1, min(5, int(body.get("importance") or 3))),
                    "source": "manual",
                    "createdAt": stamped,
                    "updatedAt": stamped,
                }
                store = save_store(lambda draft: draft["memories"].insert(0, memory))
                new_domain_event(
                    APP_MEMORY_RECORDED,
                    memory["id"],
                    {"memoryId": memory["id"], "category": memory["category"], "source": "manual"},
                )
                return self.send_payload(200, {"memory": memory, "memories": store["memories"]})

        memory_match = re.match(r"^/api/memories/([^/]+)$", path)
        if memory_match and self.command == "PATCH":
            memory_id = memory_match.group(1)
            body = self.read_json_body()

            def mutate(draft):
                next_memories = []
                for memory in draft["memories"]:
                    if memory.get("id") == memory_id:
                        updated = {**memory, **body, "updatedAt": now()}
                        if body.get("content"):
                            updated["content"] = configured(body.get("content"))
                        next_memories.append(updated)
                    else:
                        next_memories.append(memory)
                draft["memories"] = next_memories

            store = save_store(mutate)
            new_domain_event(APP_MEMORY_UPDATED, memory_id, {"memoryId": memory_id})
            return self.send_payload(200, {"memories": store["memories"]})
        if memory_match and self.command == "DELETE":
            memory_id = memory_match.group(1)
            store = save_store(lambda draft: draft.update({"memories": [memory for memory in draft["memories"] if memory.get("id") != memory_id]}))
            new_domain_event(APP_MEMORY_REMOVED, memory_id, {"memoryId": memory_id})
            return self.send_payload(200, {"memories": store["memories"]})

        if path == "/api/items":
            if self.command == "GET":
                return self.send_payload(200, {"items": read_store()["items"]})
            if self.command == "POST":
                body = self.read_json_body()
                title = configured(body.get("title"))
                if body.get("type") not in DOMAIN_TYPES or not title:
                    return self.send_payload(400, {"error": "유형과 제목을 입력하세요."})
                stamped = now()
                item = {
                    "id": new_id("item"),
                    "type": body.get("type"),
                    "title": title,
                    "status": configured(body.get("status")) or "open",
                    "date": configured(body.get("date")),
                    "amount": normalize_amount(body.get("amount")),
                    "currency": configured(body.get("currency")),
                    "ticker": configured(body.get("ticker")).upper(),
                    "location": configured(body.get("location")),
                    "notes": configured(body.get("notes")),
                    "fields": normalize_item_fields(body.get("fields")),
                    "createdAt": stamped,
                    "updatedAt": stamped,
                }
                store = save_store(lambda draft: draft["items"].insert(0, item))
                new_domain_event(
                    APP_ITEM_UPDATED,
                    item["id"],
                    {"itemId": item["id"], "type": item["type"], "status": item["status"]},
                )
                return self.send_payload(200, {"item": item, "items": store["items"]})

        item_match = re.match(r"^/api/items/([^/]+)$", path)
        if item_match and self.command == "PATCH":
            item_id = item_match.group(1)
            body = self.read_json_body()
            store = save_store(lambda draft: draft.update({"items": [patch_item(item, body) if item.get("id") == item_id else item for item in draft["items"]]}))
            new_domain_event(APP_ITEM_UPDATED, item_id, {"itemId": item_id, "patched": True})
            return self.send_payload(200, {"items": store["items"]})
        if item_match and self.command == "DELETE":
            item_id = item_match.group(1)
            store = save_store(lambda draft: draft.update({"items": [item for item in draft["items"] if item.get("id") != item_id]}))
            new_domain_event(APP_ITEM_REMOVED, item_id, {"itemId": item_id})
            return self.send_payload(200, {"items": store["items"]})

        if path == "/api/stocks" and self.command == "GET":
            symbols = []
            for symbol in str(first_query(query, "symbols") or "").split(","):
                cleaned = symbol.strip()
                if cleaned and cleaned not in symbols:
                    symbols.append(cleaned)
            return self.send_payload(200, {
                "stocks": [stock_snapshot(symbol) for symbol in symbols[:12]],
                "source": "Quotes: Stooq/Naver Finance, News: multi-channel RSS/GDELT",
                "fetchedAt": now(),
            })

        self.send_payload(404, {"error": "API를 찾지 못했습니다."})

    def serve_static(self, path: str):
        target = "/index.html" if path == "/" else path
        file_path = (PUBLIC_DIR / target.lstrip("/")).resolve()
        try:
            file_path.relative_to(PUBLIC_DIR.resolve())
        except ValueError:
            return self.send_payload(403, "Forbidden", "text/plain; charset=utf-8")
        if file_path.exists() and file_path.is_dir():
            if not path.endswith("/"):
                return self.send_redirect(path + "/")
            file_path = file_path / "index.html"
        if not file_path.exists() or file_path.is_dir():
            return self.send_payload(404, "Not found", "text/plain; charset=utf-8")
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith(("text/", "application/javascript", "application/json")) else ""))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


MAX_PORT_FALLBACK_ATTEMPTS = 20


def port_fallback_enabled(value: str = None) -> bool:
    configured = value if value is not None else os.environ.get("ALLOW_PORT_FALLBACK")
    return str(configured or "").strip().lower() not in {"0", "false", "no", "off"}


def bind_web_server(host: str, port: int, allow_port_fallback: bool = True, server_factory=None):
    factory = server_factory or ReusableThreadingHTTPServer
    requested_port = int(port)
    attempt_count = MAX_PORT_FALLBACK_ATTEMPTS if allow_port_fallback else 1
    last_error = None

    for offset in range(attempt_count):
        candidate_port = requested_port + offset
        if candidate_port > 65535:
            break
        try:
            return factory((host, candidate_port), DigitalTwinHandler), candidate_port
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            last_error = error

    final_port = min(65535, requested_port + attempt_count - 1)
    message = "Address already in use for ports " + str(requested_port) + "-" + str(final_port)
    if last_error is not None:
        raise OSError(errno.EADDRINUSE, message) from last_error
    raise OSError(errno.EADDRINUSE, message)


def serve(host: str = "", port: int = 3000):
    selected_host = host or os.environ.get("HOST") or "127.0.0.1"
    selected_port = int(port or os.environ.get("PORT") or 3000)
    requested_port = selected_port
    server, selected_port = bind_web_server(
        selected_host,
        selected_port,
        allow_port_fallback=port_fallback_enabled(),
    )
    display_host = "127.0.0.1" if selected_host in {"", "0.0.0.0"} else selected_host
    if selected_port != requested_port:
        print(
            "Orbit Alpha requested port " + str(requested_port) + " is occupied; using port " + str(selected_port),
            flush=True,
        )
    print("Orbit Alpha Python server running at http://" + display_host + ":" + str(selected_port), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
