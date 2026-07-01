import hashlib
import json
from html import escape
from pathlib import Path
from typing import Dict, Iterable, List

from .domain.model_review import MODEL_REVIEW_PROMPT_VERSION
from .domain.monitoring import DEFAULT_ALERT_RULES, DEFAULT_CADENCE, DEFAULT_THRESHOLDS, MIN_CADENCE_MINUTES
from .infrastructure.settings import ROOT_DIR


ADMIN_PREVIEW_SCHEMA_VERSION = 1


def assignment_items(values: Dict[str, float], unit: str = "") -> List[Dict[str, object]]:
    return [
        {
            "key": key,
            "default": value,
            "unit": unit,
        }
        for key, value in values.items()
    ]


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
            "GitHub Pages 미리보기에는 실제 계좌, Toss secret, Telegram bot token, SQLite DB, 로컬 상태 파일을 포함하지 않습니다.",
            "실제 설정 저장과 계좌 조회는 로컬 서버의 /api/service-accounts, /api/settings에서만 수행합니다.",
            "공유 미리보기에서는 서버 설정과 계정 DB 쓰기를 차단합니다.",
        ],
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
                "storage": ["data/settings.json", ".env.local"],
                "fields": [
                    {"key": "watchlistSymbols", "label": "기본 관심 종목", "type": "symbols", "default": "NVDA,TSLA,000660"},
                    {"key": "notifyProvider", "label": "알림 채널", "type": "select", "options": ["telegram", "kakao", "console"]},
                    {"key": "telegramBotToken", "label": "Telegram Bot Token", "type": "secret", "masked": True},
                    {"key": "telegramChatId", "label": "Telegram Chat ID", "type": "secret", "masked": True},
                    {"key": "notifyLinkUrl", "label": "알림 링크 URL", "type": "url", "default": "http://127.0.0.1:3000?tab=alerts"},
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
                "storage": ["data/python-monitor-state.json", "data/domain-events.jsonl"],
                "defaults": {
                    "alertRules": assignment_items(DEFAULT_ALERT_RULES),
                    "alertThresholds": assignment_items(DEFAULT_THRESHOLDS),
                    "alertCadenceMinutes": assignment_items(DEFAULT_CADENCE, "minutes"),
                    "minimumCadenceMinutes": MIN_CADENCE_MINUTES,
                },
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
                "storage": ["data/model-review-queue.json", "data/python-model-review.log"],
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
                "id": "deployment",
                "title": "GitHub Pages 배포",
                "summary": "main 브랜치에 기능이 반영되면 GitHub Actions가 정적 mock 데이터와 Python admin preview를 다시 생성해 gh-pages에 배포합니다.",
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


def render_admin_html(payload: Dict[str, object]) -> str:
    pages = payload.get("pages") or []
    nav = "".join(
        '<a href="#' + escape(str(page.get("id"))) + '">' + escape(str(page.get("title"))) + "</a>"
        for page in pages
        if isinstance(page, dict)
    )
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
