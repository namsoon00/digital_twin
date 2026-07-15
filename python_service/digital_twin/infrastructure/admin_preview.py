import hashlib
import json
from html import escape
from pathlib import Path
from typing import Dict, Iterable, List

from ..domain.model_review import MODEL_REVIEW_PROMPT_VERSION
from ..domain.message_types import DEFAULT_ALERT_RULES, DEFAULT_ALERT_THRESHOLDS, DEFAULT_CADENCE, DEFAULT_RELATION_RULE_THRESHOLDS, MIN_CADENCE_MINUTES
from ..domain.parsing import parse_assignments
from . import operational_store as stores
from .settings import ROOT_DIR, runtime_settings, settings_path, utc_now


DEFAULT_THRESHOLDS = DEFAULT_ALERT_THRESHOLDS
DEFAULT_RELATION_THRESHOLDS = DEFAULT_RELATION_RULE_THRESHOLDS
ADMIN_PREVIEW_SCHEMA_VERSION = 1

DISPLAY_KEY_LABELS = {
    "modelHypothesis": "모델 설명",
    "customBuyModelFormula": "참고 매수 계산식",
    "customSellModelFormula": "참고 매도 계산식",
    "formulaWeights": "참고 가중치",
    "lossGuardWeakEvidencePenalty": "확인 약할 때 감점",
    "lossGuardConfirmationScore": "손실 관리 확인 점수",
    "lossGuardConfirmationCount": "손실 관리 확인 신호 수",
    "ontologyTypeDbEnabled": "관계 분석 TypeDB 저장",
}

BEGINNER_REPLACEMENTS = [
    ("thesisScore", "buyReasonScore"),
    ("thesisWeight", "buyReasonWeight"),
    ("온톨로지 판단", "관계 판단"),
    ("온톨로지 컨텍스트", "관계 분석 정보"),
    ("온톨로지 그래프", "관계 분석 데이터"),
    ("온톨로지", "관계 분석"),
    ("세계관", "투자 관점"),
    ("관계 압력", "관계 신호"),
    ("증거", "근거"),
    ("컨텍스트", "정보"),
    ("가설", "설명"),
    ("thesis", "보유 이유"),
]


def beginner_friendly_text(value: object) -> str:
    text = str(value or "")
    for before, after in BEGINNER_REPLACEMENTS:
        text = text.replace(before, after)
    return text


def display_key(key: object) -> str:
    raw = str(key or "")
    return DISPLAY_KEY_LABELS.get(raw, beginner_friendly_text(raw))

PUBLIC_SETTING_KEYS = [
    "appTheme",
    "watchlistSymbols",
    "operationalDbBackend",
    "mysqlHost",
    "mysqlPort",
    "mysqlDatabase",
    "mysqlUser",
    "tossApiBaseUrl",
    "notifyProvider",
    "notifyLinkUrl",
    "fxRates",
    "valuationAssumptions",
    "marketSignalInputs",
    "fairValueFormula",
    "buyScoreFormula",
    "sellScoreFormula",
    "profitTakeScoreFormula",
    "lossCutScoreFormula",
    "notificationScoreFormula",
    "monitorAccountQueueEnabled",
    "monitorAccountIntervalSeconds",
    "monitorAccountBatchSize",
    "monitorAccountLockSeconds",
    "modelName",
    "modelHypothesis",
    "customBuyModelFormula",
    "customSellModelFormula",
    "formulaWeights",
    "decisionThresholds",
    "modelDecisionThresholds",
    "alertRules",
    "alertThresholds",
    "relationRuleThresholds",
    "alertCadenceMinutes",
    "modelReviewUseCodex",
    "modelReviewCommand",
    "modelReviewTimeoutSeconds",
    "modelReviewIntervalSeconds",
    "modelReviewBatchSize",
    "modelReviewTelegramMode",
    "kisMarketSignalsEnabled",
    "kisMarketSignalMaxSymbols",
    "kisMarketSignalCacheMinutes",
    "kisMarketSignalGapSeconds",
    "kisMarketSignalPreferLiveDuringMarketHours",
    "kisMarketSignalLiveRefreshSeconds",
    "kisMarketSignalUnchangedStaleCount",
    "marketDataMaxAgeMinutes",
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
    "externalApiFetchIntervalMinutes",
    "externalSignalCacheMaxAgeMinutes",
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
    "newsCollectionMinRelevanceScore",
    "newsCollectionRequireArticleBodyForRss",
    "newsCollectionIncludeWatchlist",
    "newsCollectionIncludeHoldings",
    "newsCollectionRateLimitSeconds",
    "newsAiAnalysisEnabled",
    "newsAiAnalysisUseCodex",
    "newsAiAnalysisCommand",
    "newsAiAnalysisTimeoutSeconds",
    "ontologyReasoningEnabled",
    "ontologyReasoningIntervalSeconds",
    "ontologyReasoningBatchSize",
    "ontologyLabEnabled",
    "ontologyLabIntervalSeconds",
    "ontologyLabBatchSize",
    "ontologyLabRunHistoryLimit",
    "materialityGateEnabled",
    "materialityMinimumScore",
    "marketMaterialityMinimumScore",
    "marketMaterialityPriceChangePct",
    "marketMaterialityTrendDistancePct",
    "marketMaterialityVolumeRatio",
    "newsMaterialityMinimumScore",
    "dartDisclosureAiAnalysisEnabled",
    "dartDisclosureAiUseCodex",
    "dartDisclosureAiCommand",
    "dartDisclosureAiTimeoutSeconds",
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
        "messageDeliveryLevel": account.message_delivery_profile().get("level"),
        "messageDeliveryLevelLabel": account.message_delivery_profile().get("label"),
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
    try:
        registry = stores.account_registry(settings)
        saved_accounts = registry.load_saved()
        accounts = saved_accounts or registry.load_all()
    except Exception:
        saved_accounts = []
        accounts = []
    enabled_accounts = [account for account in accounts if account.enabled]
    return {
        "generatedAt": utc_now(),
        "sources": {
            "operationalDbBackend": settings.get("operationalDbBackend") or "mysql",
            "settings": relative_path(settings_path()),
            "settingsExists": settings_path().exists(),
        },
        "accountSource": "operational-db" if saved_accounts else "runtime-default",
        "savedAccountCount": len(saved_accounts),
        "accountCount": len(accounts),
        "enabledAccountCount": len(enabled_accounts),
        "accounts": [sanitized_account(account) for account in accounts],
        "settings": public_runtime_settings(settings),
        "configured": configured_runtime_flags(settings),
        "notification": {
            "alertRules": assignment_snapshot(settings.get("alertRules", ""), DEFAULT_ALERT_RULES),
            "alertThresholds": assignment_snapshot(settings.get("alertThresholds", ""), DEFAULT_THRESHOLDS),
            "relationRuleThresholds": assignment_snapshot(settings.get("relationRuleThresholds", ""), DEFAULT_RELATION_THRESHOLDS),
            "alertCadenceMinutes": assignment_snapshot(settings.get("alertCadenceMinutes", ""), DEFAULT_CADENCE, "minutes"),
            "minimumCadenceMinutes": MIN_CADENCE_MINUTES,
        },
    }


def admin_preview_config() -> Dict[str, object]:
    payload: Dict[str, object] = {
        "schemaVersion": ADMIN_PREVIEW_SCHEMA_VERSION,
        "title": "Orbit Alpha Python Admin",
        "route": "/admin/",
        "mode": "github-pages-readonly-preview",
        "description": "Python 서비스의 계정, 알림, 모니터링, 모델 리뷰 구성을 GitHub Pages에서 확인하기 위한 정적 미리보기입니다.",
        "previewUrl": "https://namsoon00.github.io/orbit-alpha/admin/",
        "localUrl": "http://127.0.0.1:3000/admin/",
        "security": [
            "GitHub Pages 미리보기에는 운영 DB 접속 정보, Toss secret, Telegram bot token, 계좌 순번, 채팅 ID 원문을 포함하지 않습니다.",
            "빌드 시점의 운영 DB 계정과 런타임 설정은 secret 원문 없이 마스킹된 값으로만 포함합니다.",
            "실제 설정 저장과 계좌 조회는 로컬 서버의 /api/service-accounts, /api/settings에서만 수행합니다.",
            "공유 미리보기에서는 서버 설정과 계정 DB 쓰기를 차단합니다.",
        ],
        "localData": local_data_snapshot(),
        "pages": [
            {
                "id": "accounts",
                "title": "계정 관리",
                "summary": "여러 Toss 계정과 관심 종목, 알림 채널을 운영 저장소에 저장하는 로컬 전용 관리 화면입니다.",
                "localEndpoints": ["GET /api/service-accounts", "POST /api/service-accounts", "DELETE /api/service-accounts/{id}"],
                "commands": ["npm run python:accounts -- list --json", "npm run python:accounts -- add --id main ..."],
                "storage": ["mysql: service_accounts"],
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
                "storage": ["mysql: runtime_settings", ".env.local"],
                "fields": [
                    {"key": "watchlistSymbols", "label": "기본 관심 종목", "type": "symbols", "default": "TSLA,AAPL,NVDA,000660"},
                    {"key": "notifyProvider", "label": "알림 채널", "type": "select", "options": ["telegram", "kakao", "console"]},
                    {"key": "telegramBotToken", "label": "Telegram Bot Token", "type": "secret", "masked": True},
                    {"key": "telegramChatId", "label": "Telegram Chat ID", "type": "secret", "masked": True},
                    {"key": "notifyLinkUrl", "label": "알림 링크 URL", "type": "url", "default": "http://127.0.0.1:3000?tab=notifications"},
                    {"key": "alphaVantageApiKey", "label": "Alpha Vantage API Key", "type": "secret", "masked": True},
                    {"key": "externalAlphaEnabled", "label": "Alpha Vantage 수집 사용", "type": "toggle", "default": "1"},
                    {"key": "externalAlphaFundamentalsEnabled", "label": "Alpha Vantage 펀더멘털 수집", "type": "toggle", "default": "1"},
                    {"key": "externalAlphaFundamentalsMaxSymbols", "label": "펀더멘털 조회 종목 수", "type": "number", "default": "1"},
                    {"key": "coingeckoApiKey", "label": "CoinGecko API Key", "type": "secret", "masked": True},
                    {"key": "externalCoinGeckoEnabled", "label": "CoinGecko 수집 사용", "type": "toggle", "default": "1"},
                    {"key": "fredApiKey", "label": "FRED API Key", "type": "secret", "masked": True},
                    {"key": "externalFredEnabled", "label": "FRED 수집 사용", "type": "toggle", "default": "1"},
                    {"key": "opendartApiKey", "label": "OpenDART API Key", "type": "secret", "masked": True},
                    {"key": "externalDartEnabled", "label": "OpenDART 수집 사용", "type": "toggle", "default": "1"},
                    {"key": "externalNewsEnabled", "label": "뉴스 헤드라인 수집 사용", "type": "toggle", "default": "1"},
                    {"key": "externalNewsProvider", "label": "뉴스 공급자", "type": "select", "options": ["auto", "gdelt", "alpha_vantage"], "default": "auto"},
                    {"key": "externalApiFetchIntervalMinutes", "label": "외부 API 캐시", "type": "number", "default": "30", "unit": "minutes"},
                    {"key": "externalSignalCacheMaxAgeMinutes", "label": "외부 신호 캐시 TTL", "type": "number", "default": "10", "unit": "minutes"},
                    {"key": "dataFreshnessEnabled", "label": "알림 데이터 신선도 게이트", "type": "toggle", "default": "1"},
                    {"key": "dataFreshnessDefaultMaxAgeMinutes", "label": "알림 기본 신선도", "type": "number", "default": "10", "unit": "minutes"},
                    {"key": "dataFreshnessQuoteMaxAgeMinutes", "label": "시세 알림 신선도", "type": "number", "default": "10", "unit": "minutes"},
                    {"key": "dataFreshnessKisPriceMaxAgeMinutes", "label": "KIS 현재가 신선도", "type": "number", "default": "3", "unit": "minutes"},
                    {"key": "dataFreshnessKisMicrostructureMaxAgeMinutes", "label": "KIS 체결·호가 신선도", "type": "number", "default": "2", "unit": "minutes"},
                    {"key": "dataFreshnessKisInvestorMaxAgeMinutes", "label": "KIS 투자자 수급 신선도", "type": "number", "default": "5", "unit": "minutes"},
                    {"key": "kisMarketSignalUnchangedStaleCount", "label": "KIS 동일 수급값 stale 판정 횟수", "type": "number", "default": "3"},
                    {"key": "dataFreshnessExternalMaxAgeMinutes", "label": "외부 신호 신선도", "type": "number", "default": "10", "unit": "minutes"},
                    {"key": "dataFreshnessExternalEquityMaxAgeMinutes", "label": "미장 신호 신선도", "type": "number", "default": "10", "unit": "minutes"},
                    {"key": "dataFreshnessExternalCryptoMaxAgeMinutes", "label": "크립토 신호 신선도", "type": "number", "default": "10", "unit": "minutes"},
                    {"key": "dataFreshnessMacroMaxAgeMinutes", "label": "거시 신호 신선도", "type": "number", "default": "120", "unit": "minutes"},
                    {"key": "dataFreshnessDisclosureMaxAgeMinutes", "label": "공시 신선도", "type": "number", "default": "120", "unit": "minutes"},
                    {"key": "marketDataMaxAgeMinutes", "label": "추천 시세 신선도", "type": "number", "default": "240", "unit": "minutes"},
                    {"key": "externalFredSeries", "label": "FRED 지표", "type": "text"},
                    {"key": "externalCryptoIds", "label": "CoinGecko 코인 ID", "type": "text"},
                    {"key": "externalDartCorpCodes", "label": "OpenDART 종목 매핑", "type": "assignmentText"},
                    {"key": "externalNewsMaxSymbols", "label": "뉴스 조회 종목 수", "type": "number", "default": "3"},
                    {"key": "externalNewsLookbackHours", "label": "뉴스 조회 기간", "type": "number", "default": "48", "unit": "hours"},
                    {"key": "externalResearchEvidenceMaxItems", "label": "AI 전달 최신 근거 수", "type": "number", "default": "8"},
                    {"key": "newsCollectionEnabled", "label": "뉴스 아카이브 실시간 수집", "type": "toggle", "default": "1"},
                    {"key": "newsCollectionIntervalSeconds", "label": "뉴스 수집 주기", "type": "number", "default": "60", "unit": "seconds"},
                    {"key": "newsCollectionMaxSymbols", "label": "뉴스 수집 종목 수", "type": "number", "default": "40"},
                    {"key": "newsCollectionLookbackMinutes", "label": "뉴스 조회 기간", "type": "number", "default": "180", "unit": "minutes"},
                    {"key": "newsCollectionPerSymbolLimit", "label": "종목별 저장 기사 수", "type": "number", "default": "8"},
                    {"key": "newsCollectionProviders", "label": "뉴스 수집 채널", "type": "text", "default": "yahoo_finance,gdelt"},
                    {"key": "newsCollectionMinRelevanceScore", "label": "뉴스 관련성 최소 점수", "type": "number", "default": "35"},
                    {"key": "newsCollectionRequireArticleBodyForRss", "label": "RSS 원문 본문 필수", "type": "toggle", "default": "1"},
                    {"key": "newsCollectionIncludeWatchlist", "label": "관심종목 뉴스 포함", "type": "toggle", "default": "1"},
                    {"key": "newsCollectionIncludeHoldings", "label": "보유종목 뉴스 포함", "type": "toggle", "default": "1"},
                    {"key": "newsAiAnalysisEnabled", "label": "기사 AI 분석 사용", "type": "toggle", "default": "1"},
                    {"key": "newsAiAnalysisUseCodex", "label": "기사 분석 Codex 사용", "type": "toggle", "default": "1"},
                    {"key": "newsAiAnalysisCommand", "label": "기사 분석 명령", "type": "text"},
                    {"key": "newsAiAnalysisTimeoutSeconds", "label": "기사 분석 타임아웃", "type": "number", "default": "90", "unit": "seconds"},
                    {"key": "ontologyReasoningEnabled", "label": "데이터 변경 추론 사용", "type": "toggle", "default": "1"},
                    {"key": "ontologyReasoningIntervalSeconds", "label": "추론 요청 확인 주기", "type": "number", "default": "10", "unit": "seconds"},
                    {"key": "ontologyReasoningBatchSize", "label": "추론 요청 배치", "type": "number", "default": "20"},
                    {"key": "ontologyLabEnabled", "label": "온톨로지 실험 워커 사용", "type": "toggle", "default": "1"},
                    {"key": "ontologyLabIntervalSeconds", "label": "실험 반복 주기", "type": "number", "default": "300", "unit": "seconds"},
                    {"key": "ontologyLabBatchSize", "label": "실험 배치 수", "type": "number", "default": "5"},
                    {"key": "ontologyLabRunHistoryLimit", "label": "실험 이력 보관 수", "type": "number", "default": "50"},
                    {"key": "materialityGateEnabled", "label": "중요 변경 게이트", "type": "toggle", "default": "1"},
                    {"key": "materialityMinimumScore", "label": "중요 변경 기본 기준", "type": "number", "default": "65"},
                    {"key": "marketMaterialityMinimumScore", "label": "시장 데이터 중요 기준", "type": "number", "default": "65"},
                    {"key": "marketMaterialityPriceChangePct", "label": "가격 중요 변화율", "type": "number", "default": "0.6", "unit": "%"},
                    {"key": "marketMaterialityTrendDistancePct", "label": "추세 중요 이격", "type": "number", "default": "2", "unit": "%"},
                    {"key": "marketMaterialityVolumeRatio", "label": "거래량 중요 배율", "type": "number", "default": "1.5", "unit": "x"},
                    {"key": "newsMaterialityMinimumScore", "label": "뉴스 중요 기준", "type": "number", "default": "65"},
                    {"key": "dartDisclosureAiAnalysisEnabled", "label": "공시 AI 해석 사용", "type": "toggle", "default": "1"},
                    {"key": "dartDisclosureAiUseCodex", "label": "공시 해석 Codex 사용", "type": "toggle", "default": "1"},
                    {"key": "dartDisclosureAiCommand", "label": "공시 해석 명령", "type": "text"},
                    {"key": "dartDisclosureAiTimeoutSeconds", "label": "공시 해석 타임아웃", "type": "number", "default": "90", "unit": "seconds"},
                    {"key": "ontologyTypeDbEnabled", "label": "관계 분석 TypeDB 저장", "type": "toggle", "default": "1"},
                    {"key": "typedbAddress", "label": "TypeDB 주소", "type": "text", "default": "127.0.0.1:1729"},
                    {"key": "typedbUser", "label": "TypeDB 사용자", "type": "text", "default": "admin"},
                    {"key": "typedbPassword", "label": "TypeDB Password", "type": "secret", "masked": True},
                    {"key": "typedbDatabase", "label": "TypeDB Database", "type": "text", "default": "orbit_alpha_ontology"},
                    {"key": "typedbTlsEnabled", "label": "TypeDB TLS", "type": "toggle", "default": "0"},
                    {"key": "typedbTimeoutSeconds", "label": "TypeDB 타임아웃", "type": "number", "default": "20", "unit": "seconds"},
                    {"key": "typedbRetryCount", "label": "TypeDB 재시도", "type": "number", "default": "2"},
                    {"key": "typedbInferenceGenerationKeepCount", "label": "InferenceBox 보관 세대", "type": "number", "default": "1"},
                    {"key": "typedbAutoResetEnabled", "label": "TypeDB 자동 재생성", "type": "toggle", "default": "1"},
                    {"key": "typedbDataRetentionHours", "label": "TypeDB 보관 시간", "type": "number", "default": "24", "unit": "hours"},
                    {"key": "typedbDataMaxSizeMb", "label": "TypeDB 최대 용량", "type": "number", "default": "2048", "unit": "MB"},
                    {"key": "formulaWeights", "label": "참고 가중치", "type": "assignmentText"},
                    {"key": "profitTakeScoreFormula", "label": "참고 익절 계산식", "type": "formula"},
                    {"key": "lossCutScoreFormula", "label": "참고 손실 관리 계산식", "type": "formula"},
                    {"key": "notificationScoreFormula", "label": "알림 발송 공식", "type": "formula"},
                    {"key": "modelDecisionThresholds", "label": "모델 판단 기준", "type": "assignmentText"},
                ],
            },
            {
                "id": "monitoring",
                "title": "실시간 모니터링",
                "summary": "계정별 스냅샷과 외부 신호를 수집하고 세부 투자 신호를 온톨로지 투자 인사이트로 합성해 발송합니다.",
                "commands": [
                    "npm run python:monitor:once -- --dry-run --force",
                    "npm run python:monitor:watch",
                    "npm run python:ontology-reasoning:watch",
                    "npm run python:service:start",
                    "npm run python:service:status",
                ],
                "storage": ["mysql: monitor_snapshots", "mysql: monitor_sent", "mysql: domain_events"],
                "defaults": {
                    "alertRules": assignment_items(DEFAULT_ALERT_RULES),
                    "alertThresholds": assignment_items(DEFAULT_THRESHOLDS),
                    "relationRuleThresholds": assignment_items(DEFAULT_RELATION_THRESHOLDS),
                    "alertCadenceMinutes": assignment_items(DEFAULT_CADENCE, "minutes"),
                    "minimumCadenceMinutes": MIN_CADENCE_MINUTES,
                },
            },
            {
                "id": "symbol-universe",
                "title": "전체 종목 카탈로그",
                "summary": "코스피, 코스닥, 나스닥 전체 종목 목록을 운영 저장소에 저장하고 소스별 신선도를 추적합니다.",
                "localEndpoints": ["GET /api/symbol-universe", "POST /api/symbol-universe/refresh"],
                "commands": [
                    "npm run python:symbols:refresh -- --markets KOSPI,KOSDAQ,NASDAQ",
                    "npm run python:symbols:search -- --query AAPL --market NASDAQ",
                    "npm run python:symbols:status",
                ],
                "storage": ["mysql: symbol_universe", "mysql: symbol_universe_sources"],
                "fields": [
                    {"key": "symbol", "label": "티커/종목코드", "type": "text"},
                    {"key": "market", "label": "시장", "type": "select", "options": ["KOSPI", "KOSDAQ", "NASDAQ"]},
                    {"key": "lastSeenAt", "label": "마지막 원천 확인", "type": "datetime"},
                    {"key": "stale", "label": "신선도 만료", "type": "boolean"},
                ],
            },
            {
                "id": "market-data-collector",
                "title": "추천용 시장 데이터 수집",
                "summary": "전체 종목 카탈로그를 순환하면서 Toss 현재가와 일부 캔들 지표를 운영 저장소에 저장합니다.",
                "localEndpoints": [],
                "commands": [
                    "npm run python:market-data:once",
                    "npm run python:market-data:status",
                    "npm run python:market-data:watch",
                ],
                "storage": ["mysql: market_quote_cache"],
                "fields": [
                    {"key": "marketDataCollectionIntervalSeconds", "label": "수집 주기(초)", "type": "number", "default": "180"},
                    {"key": "marketDataPriceBatchSize", "label": "현재가 배치", "type": "number", "default": "200"},
                    {"key": "marketDataCandleBatchSize", "label": "캔들 배치", "type": "number", "default": "25"},
                    {"key": "marketDataMaxAgeMinutes", "label": "신선도 기준(분)", "type": "number", "default": "240"},
                ],
            },
            {
                "id": "model-review",
                "title": "모델 리뷰 워커",
                "summary": "투자 인사이트 안의 판단 변화 원본 신호를 큐에 넣고, 관계 분석의 투자 관점·반대 신호와 다음 실험을 작성합니다.",
                "commands": [
                    "npm run python:model-review:once -- --dry-run",
                    "npm run python:model-review:watch",
                    "npm run python:model-review:status",
                ],
                "storage": ["mysql: model_review_jobs", "data/python-model-review.log"],
                "settings": [
                    {"key": "modelReviewUseCodex", "label": "Codex 분석 사용", "default": "1"},
                    {"key": "modelReviewCommand", "label": "외부 리뷰 명령", "default": ""},
                    {"key": "modelReviewTimeoutSeconds", "label": "리뷰 타임아웃", "default": "180", "unit": "seconds"},
                    {"key": "modelReviewIntervalSeconds", "label": "워커 주기", "default": "300", "unit": "seconds"},
                    {"key": "modelReviewBatchSize", "label": "회차별 처리 건수", "default": "1"},
                    {"key": "modelReviewTelegramMode", "label": "텔레그램 발송 범위", "default": "actionableOnly", "options": ["actionableOnly", "all", "off"]},
                ],
                "promptVersion": MODEL_REVIEW_PROMPT_VERSION,
            },
            {
                "id": "ontology-experiments",
                "title": "온톨로지 실험",
                "summary": "후보 RuleBox를 운영 그래프와 분리된 샌드박스에서 반복 검증하고, 새 모니터 스냅샷이 들어오면 활성 실험을 다시 실행합니다.",
                "localEndpoints": [
                    "GET /api/ontology/experiments",
                    "GET /api/ontology/experiments/status",
                    "POST /api/ontology/experiments",
                    "POST /api/ontology/experiments/once",
                    "POST /api/ontology/experiments/suggest",
                    "POST /api/ontology/experiments/{id}/run",
                    "POST /api/ontology/experiments/{id}/apply",
                    "POST /api/ontology/experiments/{id}/activate",
                    "POST /api/ontology/experiments/{id}/pause",
                ],
                "commands": [
                    "npm run python:ontology-lab:status",
                    "npm run python:ontology-lab:once",
                    "npm run python:ontology-lab:watch",
                    "python3 python_service/service.py ontology-lab suggest --symbols AAPL",
                    "python3 python_service/service.py ontology-lab apply --id ontology-exp-...",
                    "python3 python_service/service.py ontology-lab activate --id ontology-exp-...",
                    "npm run python:service:restart",
                ],
                "storage": ["data/ontology-lab.json", "data/python-ontology-lab.log"],
                "settings": [
                    {"key": "ontologyLabEnabled", "label": "실험 워커 사용", "default": "1"},
                    {"key": "ontologyLabIntervalSeconds", "label": "반복 주기", "default": "300", "unit": "seconds"},
                    {"key": "ontologyLabBatchSize", "label": "회차별 실험 수", "default": "5"},
                    {"key": "ontologyLabRunHistoryLimit", "label": "이력 보관 수", "default": "50"},
                ],
            },
            {
                "id": "notification-templates",
                "title": "알림 템플릿",
                "summary": "메시지 타입별 포맷을 운영 저장소 템플릿으로 관리합니다. 포맷 변경은 템플릿 수정만으로 다음 발송에 적용됩니다.",
                "commands": [
                    "npm run python:templates -- list",
                    "python3 python_service/service.py templates save < template.json",
                    "python3 python_service/service.py templates reset --message-type monitorHeartbeat",
                ],
                "localEndpoints": ["GET /api/notification-templates", "GET /api/notification-schedules", "POST /api/notification-templates/test-send"],
                "storage": ["mysql: notification_templates", "mysql: notification_jobs"],
                "fields": [
                    {"key": "messageType", "label": "메시지 타입"},
                    {"key": "template", "label": "템플릿 본문"},
                    {"key": "schedule", "label": "마지막 발송, 다음 가능 시각, 최근 대상"},
                    {"key": "variables", "label": "{readableMessage}, {title}, {dataLines}, {triggerSummary}, {body}"},
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
                '<span class="chip">' + escape(display_key(item.get("key"))) + "=" + escape(beginner_friendly_text(item.get("default"))) + escape(str(item.get("unit") or "")) + "</span>"
                for item in value
                if isinstance(item, dict)
            )
        else:
            rendered = '<span class="chip">' + escape(beginner_friendly_text(value)) + "</span>"
        rows.append('<div class="default-row"><strong>' + escape(display_key(group)) + "</strong><div>" + rendered + "</div></div>")
    return '<div class="defaults">' + "".join(rows) + "</div>"


def status_chip(value: object, label: str = "") -> str:
    text = label or ("설정됨" if bool(value) else "미설정")
    tone = "ok" if bool(value) else "muted"
    return '<span class="chip ' + tone + '">' + escape(text) + "</span>"


def render_setting_chips(settings: Dict[str, object]) -> str:
    if not settings:
        return '<span class="muted">저장된 공개 설정 없음</span>'
    return "".join(
        '<span class="chip">' + escape(display_key(key)) + "=" + escape(beginner_friendly_text(value)) + "</span>"
        for key, value in settings.items()
    )


def render_configured_flags(flags: Dict[str, bool]) -> str:
    if not flags:
        return '<span class="muted">설정 상태 없음</span>'
    return "".join(status_chip(value, key + (" 설정됨" if value else " 미설정")) for key, value in flags.items())


def render_account_cards(accounts: List[Dict[str, object]]) -> str:
    if not accounts:
        return '<p class="muted">빌드 시점에 운영 DB에 저장된 계정이 없습니다.</p>'
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
        "<p>현재 빌드에 포함된 운영 DB 계정과 런타임 설정입니다. 민감 값은 원문 대신 설정 여부만 표시합니다.</p>"
        "</div>"
        '<div class="section-grid">'
        '<div class="panel"><h3>소스</h3>'
        '<div class="default-row"><strong>DB</strong><div><span class="chip">' + escape(str(sources.get("operationalDbBackend") or "mysql")) + "</span></div></div>"
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
            '<div class="panel"><h3>API</h3>' + render_list(page.get("localEndpoints") or []) + "</div>"
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
    <link rel="icon" type="image/svg+xml" href="../favicon.svg" />
    <style>
      :root {{
        color-scheme: light;
        --ds-color-bg: #f3f5f8;
        --ds-color-panel: #ffffff;
        --ds-color-panel-soft: #f8fafc;
        --ds-color-ink: #101820;
        --ds-color-muted: #5d6675;
        --ds-color-line: #d5dbe5;
        --ds-color-positive: #137a63;
        --ds-color-positive-soft: #e8f4ef;
        --ds-color-action: #1457a8;
        --ds-color-action-soft: #e8f1fb;
        --ds-color-warning: #946200;
        --ds-color-warning-soft: #fff4df;
        --ds-color-orbit-line: #2f6fbb;
        --ds-color-orbit-glow: rgba(20, 87, 168, 0.12);
        --ds-card-bg: #ffffff;
        --ds-card-row-bg: #fbfcfe;
        --ds-card-head-bg: #f8fafc;
        --ds-card-border: #c8d0dc;
        --ds-card-status-neutral: #758092;
        --ds-radius-control: 6px;
        --ds-radius-panel: 6px;
        --ds-shadow-panel: none;
        --bg: var(--ds-color-bg);
        --surface: var(--ds-color-panel);
        --surface-soft: var(--ds-color-panel-soft);
        --text: var(--ds-color-ink);
        --muted: var(--ds-color-muted);
        --line: var(--ds-color-line);
        --accent: var(--ds-color-positive);
        --warn: var(--ds-color-warning);
        --info: var(--ds-color-action);
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: var(--text);
        background: linear-gradient(180deg, #f7f9fc 0, var(--bg) 240px, #eef2f6 100%);
        line-height: 1.5;
        letter-spacing: 0;
      }}
      header {{
        padding: 20px clamp(16px, 4vw, 48px) 18px;
        background: linear-gradient(135deg, color-mix(in srgb, var(--surface) 94%, var(--ds-color-orbit-line) 6%), var(--surface));
        border-bottom: 1px solid color-mix(in srgb, var(--line) 76%, var(--ds-color-orbit-line) 24%);
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
        font-size: 28px;
        line-height: 1.12;
        letter-spacing: 0;
      }}
      .subtitle {{
        max-width: 780px;
        margin: 10px 0 0;
        color: var(--muted);
      }}
      .badge {{
        border: 1px solid var(--line);
        border-radius: var(--ds-radius-control);
        padding: 6px 10px;
        font-size: 13px;
        font-weight: 800;
        background: var(--surface-soft);
        color: var(--info);
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
        border-radius: var(--ds-radius-control);
        padding: 7px 10px;
        background: var(--surface-soft);
        font-size: 13px;
        font-weight: 800;
      }}
      main {{
        padding: 24px clamp(16px, 4vw, 48px) 48px;
      }}
      .notice {{
        display: grid;
        gap: 8px;
        padding: 14px 16px;
        border: 1px solid var(--warn);
        border-radius: var(--ds-radius-panel);
        background: var(--ds-color-warning-soft);
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
        position: relative;
        background: var(--surface);
        border: 1px solid var(--ds-card-border);
        border-left: 3px solid var(--info);
        border-radius: var(--ds-radius-panel);
        padding: 14px;
        min-width: 0;
        box-shadow: var(--ds-shadow-panel);
      }}
      .panel h3 {{
        padding-bottom: 8px;
        border-bottom: 1px solid var(--ds-card-border);
      }}
      /* Full financial card replacement system */
      .default-row,
      .chip,
      .account-card {{
        border-color: var(--ds-card-border);
      }}
      .default-row {{
        display: grid;
        gap: 6px;
        padding: 10px;
        border: 1px solid var(--ds-card-border);
        border-left: 3px solid var(--ds-card-status-neutral);
        border-radius: var(--ds-radius-control);
        background: linear-gradient(180deg, var(--ds-card-row-bg), var(--ds-card-bg));
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
        border-radius: var(--ds-radius-control);
        background: var(--surface-soft);
        font-size: 13px;
        color: #263241;
      }}
      .chip.ok {{ border-color: var(--accent); background: var(--ds-color-positive-soft); color: var(--accent); }}
      .defaults {{
        margin-top: 12px;
        border-top: 1px solid var(--line);
        padding-top: 12px;
      }}
      .default-row + .default-row {{ margin-top: 10px; }}
      .default-row strong {{
        display: block;
        margin-bottom: 0;
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
        background: linear-gradient(180deg, var(--ds-card-row-bg), var(--ds-card-bg));
        border: 1px solid var(--ds-card-border);
        border-left: 3px solid var(--ds-card-status-neutral);
        border-radius: var(--ds-radius-panel);
        padding: 14px;
        box-shadow: var(--ds-shadow-panel);
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
