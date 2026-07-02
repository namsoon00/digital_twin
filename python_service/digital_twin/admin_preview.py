import hashlib
import json
from html import escape
from pathlib import Path
from typing import Dict, Iterable, List

from .domain.model_review import MODEL_REVIEW_PROMPT_VERSION
from .domain.monitoring import DEFAULT_ALERT_RULES, DEFAULT_CADENCE, DEFAULT_THRESHOLDS, MIN_CADENCE_MINUTES
from .domain.parsing import parse_assignments
from .infrastructure.settings import ROOT_DIR, runtime_settings, service_db_path, settings_path, utc_now
from .infrastructure.sqlite_accounts import AccountRegistry


ADMIN_PREVIEW_SCHEMA_VERSION = 1

PUBLIC_SETTING_KEYS = [
    "watchlistSymbols",
    "tossApiBaseUrl",
    "notifyProvider",
    "notifyLinkUrl",
    "notifyIntervalMinutes",
    "fxRates",
    "valuationAssumptions",
    "marketSignalInputs",
    "fairValueFormula",
    "buyScoreFormula",
    "sellScoreFormula",
    "modelName",
    "modelHypothesis",
    "customBuyModelFormula",
    "customSellModelFormula",
    "formulaWeights",
    "decisionThresholds",
    "modelDecisionThresholds",
    "alertRules",
    "alertThresholds",
    "alertCadenceMinutes",
    "modelReviewUseCodex",
    "modelReviewCommand",
    "modelReviewTimeoutSeconds",
    "modelReviewIntervalSeconds",
    "modelReviewBatchSize",
    "externalApiFetchIntervalMinutes",
    "externalFredSeries",
    "externalCryptoIds",
    "externalAlphaMaxSymbols",
    "externalDartLookbackDays",
    "externalDartCorpCodes",
]


def assignment_items(values: Dict[str, float], unit: str = "") -> List[Dict[str, object]]:
    return [
        {
            "key": key,
            "default": value,
            "unit": unit,
        }
        for key, value in values.items()
    ]


def configured(value: object) -> bool:
    return bool(str(value or "").strip())


def relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return path.name


def sanitized_account(account) -> Dict[str, object]:
    return {
        "id": account.account_id,
        "label": account.label,
        "provider": account.provider,
        "baseUrl": account.base_url,
        "clientId": configured(account.client_id),
        "clientSecret": configured(account.client_secret),
        "accountSeq": configured(account.account_seq),
        "watchlistSymbols": list(account.watchlist_symbols),
        "notifyProvider": account.notify_provider,
        "telegramBotToken": configured(account.telegram_bot_token),
        "telegramChatId": configured(account.telegram_chat_id),
        "notifyLinkUrl": account.notify_link_url,
        "enabled": account.enabled,
    }


def public_runtime_settings(settings: Dict[str, str]) -> Dict[str, str]:
    return {
        key: str(settings.get(key) or "")
        for key in PUBLIC_SETTING_KEYS
        if str(settings.get(key) or "").strip()
    }


def configured_runtime_flags(settings: Dict[str, str]) -> Dict[str, bool]:
    return {
        "tossClientId": configured(settings.get("tossClientId")),
        "tossClientSecret": configured(settings.get("tossClientSecret")),
        "tossAccountSeq": configured(settings.get("tossAccountSeq")),
        "telegramBotToken": configured(settings.get("telegramBotToken")),
        "telegramChatId": configured(settings.get("telegramChatId")),
        "alphaVantageApiKey": configured(settings.get("alphaVantageApiKey")),
        "coingeckoApiKey": configured(settings.get("coingeckoApiKey")),
        "fredApiKey": configured(settings.get("fredApiKey")),
        "opendartApiKey": configured(settings.get("opendartApiKey")),
    }


def assignment_snapshot(raw: str, defaults: Dict[str, float], unit: str = "") -> List[Dict[str, object]]:
    return assignment_items(parse_assignments(raw or "", defaults), unit)


def local_data_snapshot() -> Dict[str, object]:
    settings = runtime_settings()
    registry = AccountRegistry()
    saved_accounts = registry.load_saved()
    accounts = saved_accounts or registry.load_all()
    enabled_accounts = [account for account in accounts if account.enabled]
    return {
        "generatedAt": utc_now(),
        "sources": {
            "serviceDb": relative_path(service_db_path()),
            "serviceDbExists": service_db_path().exists(),
            "settings": relative_path(settings_path()),
            "settingsExists": settings_path().exists(),
        },
        "accountSource": "sqlite" if saved_accounts else "runtime-default",
        "savedAccountCount": len(saved_accounts),
        "accountCount": len(accounts),
        "enabledAccountCount": len(enabled_accounts),
        "accounts": [sanitized_account(account) for account in accounts],
        "settings": public_runtime_settings(settings),
        "configured": configured_runtime_flags(settings),
        "notification": {
            "alertRules": assignment_snapshot(settings.get("alertRules", ""), DEFAULT_ALERT_RULES),
            "alertThresholds": assignment_snapshot(settings.get("alertThresholds", ""), DEFAULT_THRESHOLDS),
            "alertCadenceMinutes": assignment_snapshot(settings.get("alertCadenceMinutes", ""), DEFAULT_CADENCE, "minutes"),
            "minimumCadenceMinutes": MIN_CADENCE_MINUTES,
        },
    }


def admin_preview_config() -> Dict[str, object]:
    payload: Dict[str, object] = {
        "schemaVersion": ADMIN_PREVIEW_SCHEMA_VERSION,
        "title": "Exit Lens Python Admin",
        "route": "/admin/",
        "mode": "github-pages-readonly-preview",
        "description": "Python 서비스의 계정, 알림, 모니터링, 모델 리뷰 구성을 GitHub Pages에서 확인하기 위한 정적 미리보기입니다.",
        "previewUrl": "https://namsoon00.github.io/digital_twin/admin/",
        "localUrl": "http://127.0.0.1:3000/admin/",
        "security": [
            "GitHub Pages 미리보기에는 SQLite DB 파일, Toss secret, Telegram bot token, 계좌 순번, 채팅 ID 원문을 포함하지 않습니다.",
            "빌드 시점의 로컬 DB 계정과 런타임 설정은 secret 원문 없이 마스킹된 값으로만 포함합니다.",
            "실제 설정 저장과 계좌 조회는 로컬 서버의 /api/service-accounts, /api/settings에서만 수행합니다.",
            "공유 미리보기에서는 서버 설정과 계정 DB 쓰기를 차단합니다.",
        ],
        "localData": local_data_snapshot(),
        "pages": [
            {
                "id": "accounts",
                "title": "계정 관리",
                "summary": "여러 Toss 계정과 관심 종목, 알림 채널을 SQLite에 저장하는 로컬 전용 관리 화면입니다.",
                "localEndpoints": ["GET /api/service-accounts", "POST /api/service-accounts", "DELETE /api/service-accounts/{id}"],
                "commands": ["npm run python:accounts -- list --json", "npm run python:accounts -- add --id main ..."],
                "storage": ["data/service.db"],
                "fields": [
                    {"key": "id", "label": "계정 ID", "type": "text", "required": True},
                    {"key": "label", "label": "표시 이름", "type": "text", "required": True},
                    {"key": "provider", "label": "증권사", "type": "select", "default": "toss"},
                    {"key": "baseUrl", "label": "Toss API Base URL", "type": "url", "default": "https://openapi.tossinvest.com"},
                    {"key": "clientId", "label": "Toss API Key", "type": "secret", "masked": True},
                    {"key": "clientSecret", "label": "Toss Secret Key", "type": "secret", "masked": True},
                    {"key": "accountSeq", "label": "Toss 계좌 순번", "type": "text", "masked": True},
                    {"key": "watchlistSymbols", "label": "관심 종목", "type": "symbols"},
                    {"key": "enabled", "label": "모니터링 사용", "type": "toggle", "default": True},
                ],
            },
            {
                "id": "runtime-settings",
                "title": "런타임 설정",
                "summary": "웹 설정 패널과 Python 서비스가 함께 읽는 로컬 설정입니다. secret 원문은 응답에 다시 내려주지 않습니다.",
                "localEndpoints": ["GET /api/settings", "PUT /api/settings"],
                "commands": ["cp .env.example .env.local", "npm start"],
                "storage": ["data/service.db: runtime_settings", ".env.local"],
                "fields": [
                    {"key": "watchlistSymbols", "label": "기본 관심 종목", "type": "symbols", "default": "TSLA,AAPL,NVDA,000660"},
                    {"key": "notifyProvider", "label": "알림 채널", "type": "select", "options": ["telegram", "kakao", "console"]},
                    {"key": "telegramBotToken", "label": "Telegram Bot Token", "type": "secret", "masked": True},
                    {"key": "telegramChatId", "label": "Telegram Chat ID", "type": "secret", "masked": True},
                    {"key": "notifyLinkUrl", "label": "알림 링크 URL", "type": "url", "default": "http://127.0.0.1:3000?tab=notifications"},
                    {"key": "alphaVantageApiKey", "label": "Alpha Vantage API Key", "type": "secret", "masked": True},
                    {"key": "coingeckoApiKey", "label": "CoinGecko API Key", "type": "secret", "masked": True},
                    {"key": "fredApiKey", "label": "FRED API Key", "type": "secret", "masked": True},
                    {"key": "opendartApiKey", "label": "OpenDART API Key", "type": "secret", "masked": True},
                    {"key": "externalFredSeries", "label": "FRED 지표", "type": "text"},
                    {"key": "externalCryptoIds", "label": "CoinGecko 코인 ID", "type": "text"},
                    {"key": "externalDartCorpCodes", "label": "OpenDART 종목 매핑", "type": "assignmentText"},
                    {"key": "formulaWeights", "label": "공식 가중치", "type": "assignmentText"},
                    {"key": "modelDecisionThresholds", "label": "모델 판단 기준", "type": "assignmentText"},
                ],
            },
            {
                "id": "monitoring",
                "title": "실시간 모니터링",
                "summary": "계정별 스냅샷을 수집하고 장중 판단 변화, 보유 변화, 현금비중 변화를 알림으로 보냅니다.",
                "commands": [
                    "npm run python:monitor:once -- --dry-run --force",
                    "npm run python:monitor:watch",
                    "npm run python:service:start",
                    "npm run python:service:status",
                ],
                "storage": ["data/service.db: monitor_snapshots", "data/service.db: monitor_sent", "data/service.db: domain_events"],
                "defaults": {
                    "alertRules": assignment_items(DEFAULT_ALERT_RULES),
                    "alertThresholds": assignment_items(DEFAULT_THRESHOLDS),
                    "alertCadenceMinutes": assignment_items(DEFAULT_CADENCE, "minutes"),
                    "minimumCadenceMinutes": MIN_CADENCE_MINUTES,
                },
            },
            {
                "id": "symbol-universe",
                "title": "전체 종목 카탈로그",
                "summary": "코스피, 코스닥, 나스닥 전체 종목 목록을 SQLite에 저장하고 소스별 신선도를 추적합니다.",
                "localEndpoints": ["GET /api/symbol-universe", "POST /api/symbol-universe/refresh"],
                "commands": [
                    "npm run python:symbols:refresh -- --markets KOSPI,KOSDAQ,NASDAQ",
                    "npm run python:symbols:search -- --query AAPL --market NASDAQ",
                    "npm run python:symbols:status",
                ],
                "storage": ["data/service.db: symbol_universe", "data/service.db: symbol_universe_sources"],
                "fields": [
                    {"key": "symbol", "label": "티커/종목코드", "type": "text"},
                    {"key": "market", "label": "시장", "type": "select", "options": ["KOSPI", "KOSDAQ", "NASDAQ"]},
                    {"key": "lastSeenAt", "label": "마지막 원천 확인", "type": "datetime"},
                    {"key": "stale", "label": "신선도 만료", "type": "boolean"},
                ],
            },
            {
                "id": "model-review",
                "title": "모델 리뷰 워커",
                "summary": "monitorDecisionChange 이벤트를 큐에 넣고, Codex 또는 로컬 fallback으로 판단 변화 원인과 다음 실험을 작성합니다.",
                "commands": [
                    "npm run python:model-review:once -- --dry-run",
                    "npm run python:model-review:watch",
                    "npm run python:model-review:status",
                ],
                "storage": ["data/service.db: model_review_jobs", "data/python-model-review.log"],
                "settings": [
                    {"key": "modelReviewUseCodex", "label": "Codex 분석 사용", "default": "1"},
                    {"key": "modelReviewCommand", "label": "외부 리뷰 명령", "default": ""},
                    {"key": "modelReviewTimeoutSeconds", "label": "리뷰 타임아웃", "default": "180", "unit": "seconds"},
                    {"key": "modelReviewIntervalSeconds", "label": "워커 주기", "default": "300", "unit": "seconds"},
                    {"key": "modelReviewBatchSize", "label": "회차별 처리 건수", "default": "1"},
                ],
                "promptVersion": MODEL_REVIEW_PROMPT_VERSION,
            },
            {
                "id": "notification-templates",
                "title": "알림 템플릿",
                "summary": "메시지 타입별 포맷을 SQLite 템플릿으로 관리합니다. 포맷 변경은 템플릿 수정만으로 다음 발송에 적용됩니다.",
                "commands": [
                    "npm run python:templates -- list",
                    "python3 python_service/service.py templates save < template.json",
                    "python3 python_service/service.py templates reset --message-type monitorHeartbeat",
                ],
                "storage": ["data/service.db: notification_templates", "data/service.db: notification_jobs"],
                "fields": [
                    {"key": "messageType", "label": "메시지 타입"},
                    {"key": "template", "label": "템플릿 본문"},
                    {"key": "variables", "label": "{title}, {lines}, {rawLines}, {body}, {messageType}"},
                ],
            },
            {
                "id": "deployment",
                "title": "GitHub Pages 배포",
                "summary": "main 브랜치에 기능이 반영되면 GitHub Actions가 정적 웹 자산과 Python admin preview를 다시 생성해 gh-pages에 배포합니다.",
                "workflow": ".github/workflows/pages.yml",
                "trigger": "push to main 또는 workflow_dispatch",
                "commands": ["npm run check", "npm run generate:static"],
                "publishes": ["public/"],
            },
        ],
    }
    payload["buildId"] = build_id(payload)
    return payload


def build_id(payload: Dict[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def field_summary(fields: Iterable[Dict[str, object]]) -> str:
    chips = []
    for field in fields:
        label = str(field.get("label") or field.get("key") or "")
        if field.get("masked"):
            label += " (masked)"
        chips.append('<span class="chip">' + escape(label) + "</span>")
    return "".join(chips)


def render_list(items: Iterable[str]) -> str:
    values = [str(item) for item in items if str(item).strip()]
    if not values:
        return '<span class="muted">-</span>'
    return "<ul>" + "".join("<li>" + escape(item) + "</li>" for item in values) + "</ul>"


def render_defaults(defaults: Dict[str, object]) -> str:
    if not defaults:
        return ""
    rows = []
    for group, value in defaults.items():
        if isinstance(value, list):
            rendered = "".join(
                '<span class="chip">' + escape(str(item.get("key"))) + "=" + escape(str(item.get("default"))) + escape(str(item.get("unit") or "")) + "</span>"
                for item in value
                if isinstance(item, dict)
            )
        else:
            rendered = '<span class="chip">' + escape(str(value)) + "</span>"
        rows.append('<div class="default-row"><strong>' + escape(str(group)) + "</strong><div>" + rendered + "</div></div>")
    return '<div class="defaults">' + "".join(rows) + "</div>"


def status_chip(value: object, label: str = "") -> str:
    text = label or ("설정됨" if bool(value) else "미설정")
    tone = "ok" if bool(value) else "muted"
    return '<span class="chip ' + tone + '">' + escape(text) + "</span>"


def render_setting_chips(settings: Dict[str, object]) -> str:
    if not settings:
        return '<span class="muted">저장된 공개 설정 없음</span>'
    return "".join(
        '<span class="chip">' + escape(str(key)) + "=" + escape(str(value)) + "</span>"
        for key, value in settings.items()
    )


def render_configured_flags(flags: Dict[str, bool]) -> str:
    if not flags:
        return '<span class="muted">설정 상태 없음</span>'
    return "".join(status_chip(value, key + (" 설정됨" if value else " 미설정")) for key, value in flags.items())


def render_account_cards(accounts: List[Dict[str, object]]) -> str:
    if not accounts:
        return '<p class="muted">빌드 시점에 SQLite DB에 저장된 계정이 없습니다.</p>'
    cards = []
    for account in accounts:
        symbols = account.get("watchlistSymbols") if isinstance(account.get("watchlistSymbols"), list) else []
        credentials = "".join(
            [
                status_chip(account.get("clientId"), "API key"),
                status_chip(account.get("clientSecret"), "Toss secret"),
                status_chip(account.get("accountSeq"), "계좌 순번"),
                status_chip(account.get("telegramBotToken"), "Bot token"),
                status_chip(account.get("telegramChatId"), "Chat ID"),
            ]
        )
        cards.append(
            '<div class="account-card">'
            '<div><strong>' + escape(str(account.get("label") or account.get("id") or "-")) + "</strong>"
            '<p>' + escape(str(account.get("id") or "-")) + " · " + escape(str(account.get("provider") or "-")) + " · " + ("활성" if account.get("enabled") is not False else "비활성") + "</p></div>"
            '<div><span class="muted">관심 종목</span><p>' + escape(", ".join(str(symbol) for symbol in symbols) or "-") + "</p></div>"
            '<div><span class="muted">알림</span><p>' + escape(str(account.get("notifyProvider") or "-")) + " · " + escape(str(account.get("notifyLinkUrl") or "-")) + "</p></div>"
            '<div class="credential-row">' + credentials + "</div>"
            "</div>"
        )
    return "".join(cards)


def render_local_data(payload: Dict[str, object]) -> str:
    local_data = payload.get("localData") if isinstance(payload.get("localData"), dict) else {}
    sources = local_data.get("sources") if isinstance(local_data.get("sources"), dict) else {}
    accounts = local_data.get("accounts") if isinstance(local_data.get("accounts"), list) else []
    settings = local_data.get("settings") if isinstance(local_data.get("settings"), dict) else {}
    configured_flags = local_data.get("configured") if isinstance(local_data.get("configured"), dict) else {}
    return (
        '<section class="section" id="local-data">'
        '<div class="section-head">'
        '<p class="eyebrow">local-data</p>'
        "<h2>로컬 DB 빌드 스냅샷</h2>"
        "<p>현재 빌드에 포함된 SQLite 계정과 런타임 설정입니다. 민감 값은 원문 대신 설정 여부만 표시합니다.</p>"
        "</div>"
        '<div class="section-grid">'
        '<div class="panel"><h3>소스</h3>'
        '<div class="default-row"><strong>DB</strong><div><span class="chip">' + escape(str(sources.get("serviceDb") or "data/service.db")) + "</span>" + status_chip(sources.get("serviceDbExists"), "파일 확인") + "</div></div>"
        '<div class="default-row"><strong>설정</strong><div><span class="chip">' + escape(str(sources.get("settings") or "data/settings.json")) + "</span>" + status_chip(sources.get("settingsExists"), "파일 확인") + "</div></div>"
        '<div class="default-row"><strong>빌드 시각</strong><div><span class="chip">' + escape(str(local_data.get("generatedAt") or "-")) + "</span></div></div>"
        "</div>"
        '<div class="panel"><h3>요약</h3>'
        '<span class="chip">계정 ' + escape(str(local_data.get("accountCount", 0))) + "</span>"
        '<span class="chip">활성 ' + escape(str(local_data.get("enabledAccountCount", 0))) + "</span>"
        '<span class="chip">저장 행 ' + escape(str(local_data.get("savedAccountCount", 0))) + "</span>"
        '<span class="chip">소스 ' + escape(str(local_data.get("accountSource") or "-")) + "</span>"
        + render_configured_flags(configured_flags) +
        "</div>"
        '<div class="panel wide"><h3>런타임 설정</h3>' + render_setting_chips(settings) + "</div>"
        "</div>"
        '<div class="account-list">' + render_account_cards(accounts) + "</div>"
        "</section>"
    )


def render_admin_html(payload: Dict[str, object]) -> str:
    pages = payload.get("pages") or []
    nav = "".join(
        '<a href="#' + escape(str(page.get("id"))) + '">' + escape(str(page.get("title"))) + "</a>"
        for page in pages
        if isinstance(page, dict)
    )
    nav = '<a href="#local-data">로컬 DB</a>' + nav
    sections = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        fields = page.get("fields") if isinstance(page.get("fields"), list) else []
        settings = page.get("settings") if isinstance(page.get("settings"), list) else []
        field_block = field_summary(fields or settings)
        sections.append(
            '<section class="section" id="' + escape(str(page.get("id"))) + '">'
            '<div class="section-head">'
            '<p class="eyebrow">' + escape(str(page.get("id"))) + "</p>"
            "<h2>" + escape(str(page.get("title"))) + "</h2>"
            "<p>" + escape(str(page.get("summary") or "")) + "</p>"
            "</div>"
            '<div class="section-grid">'
            '<div class="panel"><h3>명령</h3>' + render_list(page.get("commands") or []) + "</div>"
            '<div class="panel"><h3>저장 위치</h3>' + render_list(page.get("storage") or page.get("publishes") or []) + "</div>"
            '<div class="panel wide"><h3>구성 필드</h3>' + (field_block or '<span class="muted">정적 구성 없음</span>') + render_defaults(page.get("defaults") or {}) + "</div>"
            "</div>"
            "</section>"
        )
    return """<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
    <title>{title}</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f6f7f9;
        --surface: #ffffff;
        --text: #18202b;
        --muted: #667085;
        --line: #d9dee7;
        --accent: #166a5b;
        --warn: #9b5b00;
        --info: #295b9f;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: var(--text);
        background: var(--bg);
        line-height: 1.5;
      }}
      header {{
        padding: 28px clamp(16px, 4vw, 48px) 22px;
        background: var(--surface);
        border-bottom: 1px solid var(--line);
      }}
      .topline {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        flex-wrap: wrap;
      }}
      h1 {{
        margin: 0;
        font-size: clamp(28px, 5vw, 44px);
        letter-spacing: 0;
      }}
      .subtitle {{
        max-width: 780px;
        margin: 10px 0 0;
        color: var(--muted);
      }}
      .badge {{
        border: 1px solid var(--line);
        border-radius: 999px;
        padding: 6px 10px;
        font-size: 13px;
        background: #f9fafb;
        color: var(--accent);
        white-space: nowrap;
      }}
      nav {{
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 22px;
      }}
      nav a {{
        color: var(--text);
        text-decoration: none;
        border: 1px solid var(--line);
        border-radius: 6px;
        padding: 7px 10px;
        background: #fbfcfd;
      }}
      main {{
        padding: 24px clamp(16px, 4vw, 48px) 48px;
      }}
      .notice {{
        display: grid;
        gap: 8px;
        padding: 14px 16px;
        border: 1px solid #d8c7a8;
        border-radius: 8px;
        background: #fffaf0;
        color: #43320b;
        margin-bottom: 20px;
      }}
      .notice p {{ margin: 0; }}
      .section {{
        border-top: 1px solid var(--line);
        padding: 24px 0;
      }}
      .section-head {{
        max-width: 880px;
        margin-bottom: 14px;
      }}
      .eyebrow {{
        margin: 0 0 4px;
        font-size: 12px;
        text-transform: uppercase;
        color: var(--info);
        font-weight: 700;
      }}
      h2 {{
        margin: 0 0 6px;
        font-size: 24px;
        letter-spacing: 0;
      }}
      h3 {{
        margin: 0 0 10px;
        font-size: 15px;
      }}
      .section-head p {{
        margin: 0;
        color: var(--muted);
      }}
      .section-grid {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
      }}
      .panel {{
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 14px;
        min-width: 0;
      }}
      .panel.wide {{
        grid-column: span 1;
      }}
      ul {{
        margin: 0;
        padding-left: 18px;
      }}
      li + li {{ margin-top: 6px; }}
      .chip {{
        display: inline-flex;
        align-items: center;
        min-height: 28px;
        margin: 0 6px 6px 0;
        padding: 4px 8px;
        border: 1px solid var(--line);
        border-radius: 6px;
        background: #f9fafb;
        font-size: 13px;
        color: #263241;
      }}
      .chip.ok {{ border-color: #b9d8cf; background: #eef8f5; color: var(--accent); }}
      .defaults {{
        margin-top: 12px;
        border-top: 1px solid var(--line);
        padding-top: 12px;
      }}
      .default-row + .default-row {{ margin-top: 10px; }}
      .default-row strong {{
        display: block;
        margin-bottom: 6px;
        font-size: 13px;
      }}
      .account-list {{
        display: grid;
        gap: 10px;
        margin-top: 12px;
      }}
      .account-card {{
        display: grid;
        grid-template-columns: 1.2fr 1fr 1.2fr;
        gap: 12px;
        align-items: start;
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 14px;
      }}
      .account-card p {{
        margin: 4px 0 0;
        color: var(--muted);
      }}
      .credential-row {{
        grid-column: 1 / -1;
      }}
      .muted {{ color: var(--muted); }}
      footer {{
        color: var(--muted);
        font-size: 13px;
        border-top: 1px solid var(--line);
        padding-top: 20px;
      }}
      footer a {{ color: var(--info); }}
      @media (max-width: 920px) {{
        .section-grid {{ grid-template-columns: 1fr; }}
        .account-card {{ grid-template-columns: 1fr; }}
      }}
    </style>
  </head>
  <body>
    <header>
      <div class="topline">
        <h1>{title}</h1>
        <span class="badge">build {build_id}</span>
      </div>
      <p class="subtitle">{description}</p>
      <nav>{nav}</nav>
    </header>
    <main>
      <div class="notice">{security}</div>
      {local_data}
      {sections}
      <footer>
        <p>정적 구성 JSON: <a href="config.json?v={build_id}">config.json</a></p>
        <p>배포 기준: main push -> npm run check -> npm run generate:static -> gh-pages</p>
      </footer>
    </main>
  </body>
</html>
""".format(
        title=escape(str(payload.get("title"))),
        build_id=escape(str(payload.get("buildId"))),
        description=escape(str(payload.get("description"))),
        nav=nav,
        security="".join("<p>" + escape(line) + "</p>" for line in payload.get("security") or []),
        local_data=render_local_data(payload),
        sections="".join(sections),
    )


def write_admin_preview(output_dir: Path = None) -> Dict[str, object]:
    target = Path(output_dir or ROOT_DIR / "public" / "admin")
    if not target.is_absolute():
        target = (ROOT_DIR / target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    payload = admin_preview_config()
    (target / "config.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target / "index.html").write_text(render_admin_html(payload), encoding="utf-8")
    return payload
