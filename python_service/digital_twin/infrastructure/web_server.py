import base64
import csv
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
from ..domain.message_types import DEFAULT_ALERT_RULES, DEFAULT_CADENCE, MESSAGE_TYPE_EMOJIS
from ..domain.market_hours import DEFAULT_MARKET_HOUR_SESSIONS
from ..domain.monitoring import RealtimeMonitor
from ..domain.notification_rules import CONDITION_TYPE_LABELS, DEFAULT_HONEY_THRESHOLD, NotificationRuleConfig
from ..domain.notifications import NotificationJob
from ..domain.notification_templates import DEFAULT_NOTIFICATION_TEMPLATES, MESSAGE_TYPE_LABELS, TRIGGER_SUMMARIES, alert_context, template_variables
from ..domain.parsing import parse_assignments
from ..domain.portfolio import utc_now_iso
from ..infrastructure.event_bus import default_event_bus
from ..infrastructure.mock_market import mock_market_payload, mock_market_scenario_list
from ..infrastructure.service_factory import build_symbol_universe_service, flow_lens_snapshot
from ..infrastructure.settings import ROOT_DIR, runtime_settings, save_runtime_settings
from ..infrastructure.sqlite_accounts import AccountRegistry
from ..infrastructure.sqlite_monitoring import SQLiteEventLog, SQLiteMonitorStore
from ..infrastructure.sqlite_notifications import SQLiteNotificationJobStore, SQLiteNotificationRuleStore, SQLiteNotificationTemplateStore
from ..infrastructure.sqlite_runtime import SQLiteAppStore
from ..infrastructure.toss_snapshots import build_snapshot


PUBLIC_DIR = ROOT_DIR / "public"
MEMORY_CATEGORIES = ["identity", "preference", "finance", "travel", "asset", "schedule", "work", "other"]
DOMAIN_TYPES = ["stock", "trip", "asset", "schedule", "task", "note"]
MAX_BODY_BYTES = 1024 * 1024

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
        self.broadcast(event.name, {"event": event.to_dict(), **dict(event.payload or {})})


REALTIME_HUB = RealtimeHub()


class RealtimeEventBridge:
    def __init__(self):
        self.inner = default_event_bus()

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
    events = SQLiteEventLog().events()
    counts: Dict[str, int] = {}
    latest_by_name: Dict[str, DomainEvent] = {}
    for event in events:
        counts[event.name] = counts.get(event.name, 0) + 1
        latest_by_name[event.name] = event
    monitoring = {}
    if latest_by_name.get(MONITORING_CYCLE_COMPLETED):
        monitoring["cycle"] = realtime_event_payload(latest_by_name[MONITORING_CYCLE_COMPLETED])
    if latest_by_name.get(MONITORING_ALERTS_DETECTED):
        monitoring["alerts"] = realtime_event_payload(latest_by_name[MONITORING_ALERTS_DETECTED])
    if latest_by_name.get(MONITORING_SNAPSHOT_COLLECTED):
        monitoring["snapshot"] = realtime_event_payload(latest_by_name[MONITORING_SNAPSHOT_COLLECTED])
    return {
        **REALTIME_HUB.status(),
        "events": counts,
        "latestEvents": [realtime_event_payload(event) for event in events[-12:]],
        "monitoring": monitoring,
        "notificationJobs": notification_queue_store().summary(),
    }


def new_id(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:16]


def configured(value) -> str:
    return str(value or "").strip()


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


def app_store() -> SQLiteAppStore:
    return SQLiteAppStore()


def read_store() -> Dict[str, object]:
    fallback = default_store()
    parsed = app_store().load()
    if not parsed:
        parsed = fallback
        app_store().replace(parsed)
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
    app_store().replace(store)
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
        "tossApiBaseUrl",
        "kisEnv",
        "kisBaseUrl",
        "kisMarketSignalsEnabled",
        "kisMarketSignalMaxSymbols",
        "kisMarketSignalCacheMinutes",
        "kisMarketSignalGapSeconds",
        "kisMarketSignalPreferLiveDuringMarketHours",
        "kisMarketSignalLiveRefreshSeconds",
        "notifyProvider",
        "notifyLinkUrl",
        "notifyIntervalMinutes",
        "fxRates",
        "fairValueFormula",
        "buyScoreFormula",
        "sellScoreFormula",
        "profitTakeScoreFormula",
        "lossCutScoreFormula",
        "notificationScoreFormula",
        "ontologyRelationRules",
        "aiPromptTemplates",
        "aiPromptPolicy",
        "modelName",
        "modelHypothesis",
        "customBuyModelFormula",
        "customSellModelFormula",
        "formulaWeights",
        "decisionThresholds",
        "modelDecisionThresholds",
        "modelTimingScenario",
        "modelTimingSymbols",
        "alertRules",
        "alertThresholds",
        "alertCadenceMinutes",
        "ontologyNeo4jEnabled",
        "neo4jUri",
        "neo4jUser",
        "neo4jDatabase",
        "neo4jTimeoutSeconds",
        "symbolUniverseMaxAgeHours",
        "externalApiFetchIntervalMinutes",
        "externalAlphaEnabled",
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
        "dartDisclosureAiAnalysisEnabled",
        "dartDisclosureAiUseCodex",
        "dartDisclosureAiCommand",
        "dartDisclosureAiTimeoutSeconds",
    ]
    public = {key: settings.get(key, "") for key in public_keys}
    public.update({
        "tossClientId": "",
        "tossClientSecret": "",
        "tossAccountSeq": "",
        "kisAppKey": "",
        "kisAppSecret": "",
        "kisAccountNo": "",
        "kisAccountProductCode": "",
        "telegramBotToken": "",
        "telegramChatId": "",
        "alphaVantageApiKey": "",
        "coingeckoApiKey": "",
        "fredApiKey": "",
        "opendartApiKey": "",
        "neo4jPassword": "",
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
            "kisAccountNo": bool(settings.get("kisAccountNo")),
            "kisAccountProductCode": bool(settings.get("kisAccountProductCode")),
            "telegramBotToken": bool(settings.get("telegramBotToken")),
            "telegramChatId": bool(settings.get("telegramChatId")),
            "alphaVantageApiKey": bool(settings.get("alphaVantageApiKey")),
            "coingeckoApiKey": bool(settings.get("coingeckoApiKey")),
            "fredApiKey": bool(settings.get("fredApiKey")),
            "opendartApiKey": bool(settings.get("opendartApiKey")),
            "neo4jPassword": bool(settings.get("neo4jPassword")),
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


def notification_store() -> SQLiteNotificationTemplateStore:
    return SQLiteNotificationTemplateStore()


def notification_queue_store() -> SQLiteNotificationJobStore:
    return SQLiteNotificationJobStore()


def notification_rule_store() -> SQLiteNotificationRuleStore:
    return SQLiteNotificationRuleStore()


def list_templates_payload() -> Dict[str, object]:
    return {
        "templates": [item.to_dict() for item in notification_store().list()],
        "variables": template_variables(),
    }


def list_notification_rules_payload() -> Dict[str, object]:
    return {
        "rules": [item.to_dict() for item in notification_rule_store().list()],
        "conditionTypes": CONDITION_TYPE_LABELS,
        "defaultThreshold": DEFAULT_HONEY_THRESHOLD,
        "marketHoursSessions": list(DEFAULT_MARKET_HOUR_SESSIONS.values()),
    }


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


def notification_job_public_payload(job: NotificationJob) -> Dict[str, object]:
    context = job.context or {}
    reasons = context.get("honeyReasons") if isinstance(context.get("honeyReasons"), list) else []
    title = str(context.get("title") or context.get("headline") or "").strip()
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
        "honeyScore": context.get("honeyScore"),
        "honeyThreshold": context.get("honeyThreshold"),
        "honeyDecision": context.get("honeyDecision") or ("send" if job.status in {"pending", "processing", "done"} else job.status),
        "honeyReasons": [str(item) for item in reasons],
        "honeyFingerprint": context.get("honeyFingerprint") or "",
        "honeySimilarityRecentCount": context.get("honeySimilarityRecentCount"),
        "honeySimilarityPenalty": context.get("honeySimilarityPenalty"),
        "honeySimilarityWindowMinutes": context.get("honeySimilarityWindowMinutes"),
        "honeySimilarityPreviousScore": context.get("honeySimilarityPreviousScore"),
        "honeySimilarityBypassed": bool(context.get("honeySimilarityBypassed")),
        "honeySimilarityBypassReason": context.get("honeySimilarityBypassReason") or "",
        "honeySuppressionReason": context.get("honeySuppressionReason") or "",
        "honeyStateCooldownEnabled": bool(context.get("honeyStateCooldownEnabled")),
        "honeyStateCooldownMinutes": context.get("honeyStateCooldownMinutes"),
        "honeyStateRecentSentCount": context.get("honeyStateRecentSentCount"),
        "honeyStateLastSentAt": context.get("honeyStateLastSentAt") or "",
        "honeyStateLastSentAgeMinutes": context.get("honeyStateLastSentAgeMinutes"),
        "honeyStateDecision": context.get("honeyStateDecision") or "",
        "honeyStateReason": context.get("honeyStateReason") or "",
        "honeyStateSuppressed": bool(context.get("honeyStateSuppressed")),
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
    jobs = notification_queue_store().recent(
        limit=limit,
        message_type=first_query(query, "messageType") or first_query(query, "message_type"),
        status=first_query(query, "status"),
    )
    return {
        "jobs": [notification_job_public_payload(job) for job in jobs],
        "summary": notification_queue_store().summary(),
        "limit": limit,
    }


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


def notification_schedules_payload() -> Dict[str, object]:
    settings = runtime_settings()
    rules = parse_assignments(settings.get("alertRules", ""), DEFAULT_ALERT_RULES)
    cadence = parse_assignments(settings.get("alertCadenceMinutes", ""), DEFAULT_CADENCE)
    store = SQLiteMonitorStore()
    accounts = {account.account_id: account for account in AccountRegistry().load()}
    now_at = datetime.now(timezone.utc)
    message_types = list(dict.fromkeys(list(DEFAULT_CADENCE.keys()) + list(DEFAULT_NOTIFICATION_TEMPLATES.keys())))
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
    }


def save_template_payload(payload: Dict[str, object]) -> Dict[str, object]:
    message_type = configured(payload.get("messageType") or payload.get("message_type"))
    template = str(payload.get("template") or "")
    description = str(payload.get("description") or "")
    enabled = payload.get("enabled") is not False
    saved = notification_store().upsert(message_type, template, description, enabled)
    event = new_domain_event(
        NOTIFICATION_TEMPLATE_UPDATED,
        saved.message_type,
        {"messageType": saved.message_type, "enabled": saved.enabled, "updatedAt": saved.updated_at},
    )
    return {"template": saved.to_dict(), "eventId": event.event_id}


def reset_template_payload(message_type: str) -> Dict[str, object]:
    saved = notification_store().reset(message_type)
    event = new_domain_event(
        NOTIFICATION_TEMPLATE_UPDATED,
        saved.message_type,
        {"messageType": saved.message_type, "enabled": saved.enabled, "updatedAt": saved.updated_at, "reset": True},
    )
    return {"template": saved.to_dict(), "eventId": event.event_id}


def save_notification_rule_payload(payload: Dict[str, object]) -> Dict[str, object]:
    requested = payload.get("rule") if isinstance(payload.get("rule"), dict) else payload
    rule = NotificationRuleConfig.from_dict(requested if isinstance(requested, dict) else {})
    saved = notification_rule_store().upsert(rule)
    event = new_domain_event(
        NOTIFICATION_RULE_UPDATED,
        saved.message_type,
        {
            "messageType": saved.message_type,
            "enabled": saved.enabled,
            "threshold": saved.threshold,
            "baseScore": saved.base_score,
            "similarityEnabled": saved.similarity_enabled,
            "similarityWindowMinutes": saved.similarity_window_minutes,
            "similarityPenalty": saved.similarity_penalty,
            "similarityBypassConditionCount": len(saved.similarity_bypass_conditions),
            "stateCooldownEnabled": saved.state_cooldown_enabled,
            "stateCooldownMinutes": saved.state_cooldown_minutes,
            "updatedAt": saved.updated_at,
        },
    )
    return {"rule": saved.to_dict(), "eventId": event.event_id}


def reset_notification_rule_payload(message_type: str) -> Dict[str, object]:
    saved = notification_rule_store().reset(message_type)
    event = new_domain_event(
        NOTIFICATION_RULE_UPDATED,
        saved.message_type,
        {
            "messageType": saved.message_type,
            "enabled": saved.enabled,
            "threshold": saved.threshold,
            "baseScore": saved.base_score,
            "similarityEnabled": saved.similarity_enabled,
            "similarityWindowMinutes": saved.similarity_window_minutes,
            "similarityPenalty": saved.similarity_penalty,
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
    accounts = AccountRegistry().load()
    if requested:
        for account in accounts:
            if account.account_id == requested:
                return account
        raise ValueError("요청한 계정을 찾지 못했습니다.")
    if not accounts:
        raise ValueError("테스트 발송에 사용할 계정이 없습니다.")
    return accounts[0]


def notification_test_event(message_type: str, snapshot):
    monitor = RealtimeMonitor(runtime_settings())
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
    dry_run = bool(payload.get("dryRun") or payload.get("dry_run"))
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
    event = notification_test_event(message_type, snapshot)
    if not event:
        return 422, {
            "delivered": False,
            "messageType": message_type,
            "error": "현재 데이터로 만들 수 있는 알림 이벤트가 없습니다.",
        }
    message = notification_store().render(event.rule, alert_context(event))
    if dry_run:
        return 200, {
            "delivered": False,
            "dryRun": True,
            "messageType": message_type,
            "message": message,
            "event": alert_event_public_payload(event),
        }
    public_event = alert_event_public_payload(event)
    source_event = new_domain_event(
        NOTIFICATION_TEST_REQUESTED,
        event.key or message_type,
        {"messageType": message_type, "accountId": account.account_id, "accountLabel": account.label, "event": public_event},
    )
    job = NotificationJob.create(
        message,
        account_id=account.account_id,
        account_label=account.label,
        message_type=event.rule or message_type,
        source_event_id=source_event.event_id,
        source_event_name=source_event.name,
        context=alert_context(event),
    )
    if not notification_queue_store().enqueue(job):
        if job.status == "suppressed":
            return 202, {
                "delivered": False,
                "queued": False,
                "suppressed": True,
                "provider": "Notification Queue",
                "messageType": message_type,
                "event": public_event,
                "score": (job.context or {}).get("honeyScore"),
                "threshold": (job.context or {}).get("honeyThreshold"),
                "reasons": (job.context or {}).get("honeyReasons") or [],
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
    registry = AccountRegistry()
    return AccountApplicationService(registry, registry.settings, event_publisher=RealtimeEventBridge())


def symbol_universe_service():
    return build_symbol_universe_service()


def symbol_universe_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    return symbol_universe_service().search(
        query=first_query(query, "query") or first_query(query, "q"),
        market=first_query(query, "market"),
        limit=int(first_query(query, "limit") or 80),
        offset=int(first_query(query, "offset") or 0),
    )


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
        "<title>Digiter Twin 접근 제한</title>",
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
    request = urllib.request.Request(
        target_url,
        headers={"User-Agent": "DigiterTwin/0.1", **(headers or {})},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def fetch_json_url(target_url: str, timeout: int = 8, headers: Dict[str, str] = None):
    return json.loads(fetch_text(target_url, timeout=timeout, headers={"Accept": "application/json", **(headers or {})}))


def normalize_economic_feed_rss_url(raw_url: str) -> str:
    parsed = urllib.parse.urlparse(str(raw_url or ""))
    query = urllib.parse.parse_qs(parsed.query)
    allowed = (
        parsed.scheme == "https"
        and (
            (parsed.hostname == "news.google.com" and parsed.path == "/rss/search" and query.get("q"))
            or (parsed.hostname == "www.cnbc.com" and re.match(r"^/id/\d+/device/rss/rss\.html$", parsed.path))
            or (parsed.hostname == "feeds.finance.yahoo.com" and parsed.path == "/rss/2.0/headline" and query.get("s"))
            or (parsed.hostname == "www.coindesk.com" and parsed.path == "/arc/outboundfeeds/rss/")
            or (parsed.hostname == "www.federalreserve.gov" and re.match(r"^/feeds/[a-z0-9_-]+\.xml$", parsed.path, re.I))
            or (parsed.hostname == "www.yna.co.kr" and re.match(r"^/rss/[a-z0-9_-]+\.xml$", parsed.path, re.I))
        )
    )
    if not allowed:
        raise ValueError("허용된 RSS URL은 등록된 경제 뉴스 공급자만 가능합니다.")
    return urllib.parse.urlunparse(parsed)


def normalize_economic_feed_gdelt_url(raw_url: str) -> str:
    parsed = urllib.parse.urlparse(str(raw_url or ""))
    query = urllib.parse.parse_qs(parsed.query)
    if parsed.scheme != "https" or parsed.hostname != "api.gdeltproject.org" or parsed.path != "/api/v2/doc/doc":
        raise ValueError("허용된 GDELT URL은 api.gdeltproject.org/api/v2/doc/doc 뿐입니다.")
    if not query.get("query"):
        raise ValueError("GDELT 검색어가 필요합니다.")
    if (query.get("mode", [""])[0]).lower() != "artlist":
        raise ValueError("GDELT mode=ArtList 요청만 허용됩니다.")
    if (query.get("format", [""])[0]).lower() != "json":
        raise ValueError("GDELT format=JSON 요청만 허용됩니다.")
    return urllib.parse.urlunparse(parsed)


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
        "너는 Digiter Twin 웹앱의 로컬 Python 비서 백엔드다.",
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


def chat_payload(body: Dict[str, object]) -> Dict[str, object]:
    message = configured(body.get("message"))
    if not message:
        raise ValueError("메시지를 입력하세요.")
    append_message("user", message)
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
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        if cors:
            self.add_cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not no_body:
            self.wfile.write(body)

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
        except ValueError as error:
            self.send_payload(400, {"error": str(error) or "잘못된 요청입니다."})
        except (urllib.error.URLError, TimeoutError) as error:
            self.send_payload(502, {"error": str(error) or "외부 데이터 요청 실패"})
        except Exception as error:
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

        if path == "/api/symbol-universe":
            if self.command == "GET":
                return self.send_payload(200, symbol_universe_payload(query))

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
                return self.send_payload(200, list_notification_rules_payload())
            if self.command in {"POST", "PUT"}:
                if not self.ensure_writable("공유 모드에서는 알림 룰을 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_notification_rule_payload(self.read_json_body()))

        if path == "/api/notification-jobs" and self.command == "GET":
            return self.send_payload(200, notification_jobs_payload(query))

        if path == "/api/notification-schedules" and self.command == "GET":
            return self.send_payload(200, notification_schedules_payload())

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

        if path == "/api/economic-feed/rss":
            if self.command == "OPTIONS":
                return self.send_payload(204, "", "text/plain; charset=utf-8", cors=True)
            target = normalize_economic_feed_rss_url(first_query(query, "url"))
            return self.send_payload(200, fetch_text(target), "application/rss+xml; charset=utf-8", cors=True)

        if path == "/api/economic-feed/gdelt":
            if self.command == "OPTIONS":
                return self.send_payload(204, "", "text/plain; charset=utf-8", cors=True)
            target = normalize_economic_feed_gdelt_url(first_query(query, "url"))
            return self.send_payload(200, fetch_text(target), "application/json; charset=utf-8", cors=True)

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
            return self.send_payload(200, flow_lens_snapshot(
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


def serve(host: str = "", port: int = 3000):
    selected_host = host or os.environ.get("HOST") or "127.0.0.1"
    selected_port = int(port or os.environ.get("PORT") or 3000)
    while True:
        try:
            server = ReusableThreadingHTTPServer((selected_host, selected_port), DigitalTwinHandler)
            break
        except OSError:
            selected_port += 1
    display_host = "127.0.0.1" if selected_host in {"", "0.0.0.0"} else selected_host
    print("Digiter Twin Python server running at http://" + display_host + ":" + str(selected_port), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
