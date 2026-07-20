import html
import json
import re
import shutil
import signal
import socket
import subprocess
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from ..domain import news_analysis as news_domain
from ..domain.investment_research import NewsCollectionTarget, ResearchEvidence, classify_news_relevance, compact_text, keyword_polarity, stable_evidence_token
from ..domain.market_data import number
from ..domain.portfolio import utc_now_iso
from .external_signal_utils import external_call_target, guarded_external_call


JsonFetcher = Callable[[str, Dict[str, str]], object]
TextFetcher = Callable[[str, Dict[str, str]], str]
TAG_RE = re.compile(r"<[^>]+>")
DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}
ARTICLE_TEXT_LIMIT = 5000
DEFAULT_NEWS_COLLECTION_PROVIDERS = ["yahoo_search", "yahoo_finance"]
NEWS_API_GUARD_STATE: Dict[str, object] = {}
RSS_PROVIDER_NAMES = {
    "google_rss_kr",
    "google_rss_us",
    "google_news_kr",
    "google_news_us",
    "rss_kr",
    "rss_us",
    "kr",
    "us",
    "yahoo_finance",
    "yahoo_finance_rss",
    "yahoo_rss",
}
DIRECT_BODY_REQUIRED_PROVIDER_NAMES = {"yahoo_search", "yahoo_finance_search"}
GOOGLE_NEWS_BODY_NOISE = [
    "comprehensive up-to-date news coverage",
    "aggregated from sources all over the world by google news",
]


class NewsProviderTimeout(TimeoutError):
    pass


def provider_empty_status(diagnostics: Dict[str, int]) -> str:
    if int(diagnostics.get("candidateCount") or 0) <= 0:
        return "no-candidates"
    if int(diagnostics.get("bodyBudgetRejectedCount") or 0) > 0:
        return "article-body-budget-exhausted"
    if int(diagnostics.get("bodyMissingCount") or 0) > 0:
        return "article-body-unavailable"
    if int(diagnostics.get("preliminaryRejectedCount") or 0) > 0 or int(diagnostics.get("finalRelevanceRejectedCount") or 0) > 0:
        return "relevance-filtered"
    return "empty"


def curl_fetch_bytes(url: str, headers: Dict[str, str] = None, timeout: float = 8.0):
    curl_path = shutil.which("curl")
    if not curl_path:
        return None
    request_timeout = max(0.5, float(timeout or 8.0))
    command = [
        curl_path,
        "-fsSL",
        "--connect-timeout",
        str(max(0.5, min(3.0, request_timeout))),
        "--max-time",
        str(request_timeout),
    ]
    for key, value in (headers or {}).items():
        if key and value:
            command.extend(["-H", str(key) + ": " + str(value)])
    command.append(str(url or ""))
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=request_timeout + 1.0,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip() or "curl request failed"
        raise urllib.error.URLError(message[:180])
    return completed.stdout


@contextmanager
def socket_default_timeout(seconds: float):
    timeout = max(0.5, float(seconds or 8.0))
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        yield
    finally:
        socket.setdefaulttimeout(previous)


def default_json_fetcher(url: str, headers: Dict[str, str] = None, timeout: float = 8.0) -> object:
    request = urllib.request.Request(url, headers=headers or {})
    request_timeout = max(0.5, float(timeout or 8.0))
    raw_bytes = curl_fetch_bytes(url, headers, request_timeout)
    if raw_bytes is not None:
        raw = raw_bytes.decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}
    with socket_default_timeout(request_timeout):
        with urllib.request.urlopen(request, timeout=request_timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}


def default_text_fetcher(url: str, headers: Dict[str, str] = None, timeout: float = 8.0) -> str:
    request = urllib.request.Request(url, headers=headers or {})
    request_timeout = max(0.5, float(timeout or 8.0))
    raw_bytes = curl_fetch_bytes(url, headers, request_timeout)
    if raw_bytes is not None:
        return raw_bytes.decode("utf-8", errors="replace")
    with socket_default_timeout(request_timeout):
        with urllib.request.urlopen(request, timeout=request_timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")


def news_source_for_url(url: str) -> str:
    host = urllib.parse.urlparse(str(url or "")).netloc.lower()
    if "gdeltproject.org" in host:
        return "GDELT News"
    if "finance.yahoo.com" in host:
        return "Yahoo Finance RSS"
    if "news.google.com" in host:
        return "Google News"
    return "News Article"


def split_csv(raw: object, fallback: Iterable[str]) -> List[str]:
    values = [item.strip() for item in str(raw or "").split(",") if item.strip()]
    return values or list(fallback)


def int_setting(settings: Dict[str, str], key: str, fallback: int, lower: int = 0, upper: int = 100000) -> int:
    try:
        parsed = int(float(str(settings.get(key) or "").strip()))
    except ValueError:
        parsed = fallback
    return max(lower, min(upper, parsed))


def float_setting(settings: Dict[str, str], key: str, fallback: float, lower: float = 0.0, upper: float = 100000.0) -> float:
    try:
        parsed = float(str(settings.get(key) or "").strip())
    except ValueError:
        parsed = fallback
    return max(lower, min(upper, parsed))


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def strip_html(value: object) -> str:
    return compact_text(html.unescape(TAG_RE.sub(" ", str(value or ""))), 240)


def article_block_is_useful(text: object) -> bool:
    value = clean_article_block(text)
    if len(value) < 28:
        return False
    lowered = value.lower()
    if any(term in lowered for term in [
        "cookie",
        "advertisement",
        "subscribe",
        "sign up",
        "all rights reserved",
        *GOOGLE_NEWS_BODY_NOISE,
        "개인정보",
        "구독",
        "광고",
        "저작권",
        "무단전재",
        "기자",
    ]):
        return False
    return True


def clean_article_block(text: object) -> str:
    value = compact_text(text, 500)
    if not value:
        return ""
    for marker in ["Sign in to access your portfolio", "Are you ahead, or behind on retirement?"]:
        index = value.find(marker)
        if index == 0:
            return ""
        if index > 0:
            value = value[:index]
    widget_match = re.search(
        r"\b[A-Z]{1,6}(?:-[A-Z]{2,4})?\s+[A-Z][A-Za-z0-9&.,' -]{2,48}(?:Inc\.|Corporation|Corp\.|Ltd\.|PLC|USD)\s+[+-]?\d",
        value,
    )
    if widget_match and widget_match.start() > 28:
        value = value[: widget_match.start()]
    price_change_count = len(re.findall(r"[+-]?\d[\d,]*(?:\.\d+)?\s+[+-]\d[\d,]*(?:\.\d+)?\s+\([+-]?\d", value))
    ticker_count = len(re.findall(r"\b[A-Z]{1,6}(?:-[A-Z]{2,4})?\b", value))
    numeric_count = len(re.findall(r"[+-]?\d[\d,]*(?:\.\d+)?%?", value))
    if price_change_count >= 2 or (ticker_count >= 4 and numeric_count >= 6):
        return ""
    return compact_text(value, 500)


def article_body_allowed_for_source(source: object, provider: object = "") -> bool:
    return not news_domain.source_is_social_feed(source, provider)


class ArticleTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.capture_stack: List[str] = []
        self.buffer: List[str] = []
        self.blocks: List[str] = []
        self.meta_description = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = str(tag or "").lower()
        if normalized in {"script", "style", "noscript", "svg", "iframe", "nav", "footer", "form"}:
            self.skip_depth += 1
            return
        attr_map = {str(key or "").lower(): str(value or "") for key, value in attrs or []}
        if normalized == "meta":
            name = (attr_map.get("name") or attr_map.get("property") or "").lower()
            if name in {"description", "og:description", "twitter:description"} and attr_map.get("content"):
                self.meta_description = self.meta_description or attr_map["content"]
        if normalized in {"p", "h1", "h2", "h3", "li"}:
            self.capture_stack.append(normalized)
            self.buffer = []

    def handle_endtag(self, tag: str) -> None:
        normalized = str(tag or "").lower()
        if normalized in {"script", "style", "noscript", "svg", "iframe", "nav", "footer", "form"}:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.capture_stack and normalized == self.capture_stack[-1]:
            text = clean_article_block(" ".join(self.buffer))
            if article_block_is_useful(text):
                self.blocks.append(text)
            self.capture_stack.pop()
            self.buffer = []

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not self.capture_stack:
            return
        text = str(data or "").strip()
        if text:
            self.buffer.append(text)


def extract_article_text(raw_html: object) -> str:
    text = str(raw_html or "")
    if not text.strip():
        return ""
    head = text[:600].lower()
    if "<rss" in head or "<feed" in head or "<channel" in head:
        return ""
    parser = ArticleTextParser()
    try:
        parser.feed(text)
    except Exception:  # noqa: BLE001 - malformed news HTML should fall back to feed summaries.
        return ""
    blocks: List[str] = []
    if article_block_is_useful(parser.meta_description):
        blocks.append(compact_text(parser.meta_description, 420))
    seen = {item.casefold() for item in blocks}
    for block in parser.blocks:
        key = block.casefold()
        if key in seen:
            continue
        seen.add(key)
        blocks.append(block)
        if len(" ".join(blocks)) >= ARTICLE_TEXT_LIMIT:
            break
    return compact_text(" ".join(blocks), ARTICLE_TEXT_LIMIT)


def parse_news_datetime(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in [text, text.replace("Z", "+00:00")]:
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = parsedate_to_datetime(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


def iso_or_empty(value: object) -> str:
    parsed = parse_news_datetime(value)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if parsed else str(value or "").strip()


def within_lookback(value: object, lookback_minutes: int) -> bool:
    parsed = parse_news_datetime(value)
    if not parsed:
        return True
    return datetime.now(timezone.utc) - parsed.astimezone(timezone.utc) <= timedelta(minutes=max(1, lookback_minutes))


class NewsSourceGateway:
    def __init__(
        self,
        settings: Dict[str, str] = None,
        fetch_json: JsonFetcher = None,
        fetch_text: TextFetcher = None,
    ):
        self.settings = dict(settings or {})
        timeout = number(self.settings.get("newsCollectionTimeoutSeconds") or self.settings.get("externalApiTimeoutSeconds")) or 8.0
        self.fetch_json = fetch_json or self.guarded_json_fetcher(timeout)
        self.fetch_text = fetch_text or self.guarded_text_fetcher(timeout)
        self._article_body_fetches_used = 0
        self._article_body_fetches_for_target = 0
        self._current_provider_diagnostics: Dict[str, int] = {}

    def reset_provider_diagnostics(self) -> None:
        self._current_provider_diagnostics = {
            "candidateCount": 0,
            "preliminaryRejectedCount": 0,
            "bodyFetchAttemptCount": 0,
            "bodyMissingCount": 0,
            "bodyBudgetRejectedCount": 0,
            "sourceBlockedCount": 0,
            "finalRelevanceRejectedCount": 0,
            "acceptedCount": 0,
        }

    def record_provider_diagnostic(self, key: str) -> None:
        if key not in self._current_provider_diagnostics:
            self._current_provider_diagnostics[key] = 0
        self._current_provider_diagnostics[key] += 1

    def guarded_json_fetcher(self, timeout: float) -> JsonFetcher:
        def fetch(url: str, headers: Dict[str, str] = None) -> object:
            source = news_source_for_url(url)
            return guarded_external_call(
                self.settings,
                source,
                external_call_target(url),
                lambda: default_json_fetcher(url, headers, timeout=timeout),
                state=NEWS_API_GUARD_STATE,
                attempts=1 if source == "GDELT News" else 2,
                rate_limit_seconds=0,
            )

        return fetch

    def guarded_text_fetcher(self, timeout: float) -> TextFetcher:
        def fetch(url: str, headers: Dict[str, str] = None) -> str:
            source = news_source_for_url(url)
            request_timeout = self.article_body_timeout_seconds() if self.is_article_body_request(headers) else timeout
            return guarded_external_call(
                self.settings,
                source,
                external_call_target(url),
                lambda: default_text_fetcher(url, headers, timeout=request_timeout),
                state=NEWS_API_GUARD_STATE,
                attempts=1 if source == "GDELT News" else 2,
                rate_limit_seconds=0,
            )

        return fetch

    def providers(self) -> List[str]:
        providers = [
            item.lower().replace("-", "_")
            for item in split_csv(
                self.settings.get("newsCollectionProviders"),
                DEFAULT_NEWS_COLLECTION_PROVIDERS,
            )
        ]
        if not truthy(self.settings.get("newsCollectionGdeltSyncEnabled"), False):
            providers = [item for item in providers if item != "gdelt"]
        return providers

    def per_symbol_limit(self) -> int:
        return int_setting(self.settings, "newsCollectionPerSymbolLimit", 8, 1, 50)

    def lookback_minutes(self) -> int:
        return int_setting(self.settings, "newsCollectionLookbackMinutes", 180, 5, 1440 * 7)

    def minimum_relevance_state(self) -> str:
        value = str(self.settings.get("newsCollectionMinimumRelevanceState") or "context").strip().lower()
        return value if value in {"direct", "related", "context", "unrelated"} else "context"

    def relevance_state_passes(self, payload: Dict[str, object]) -> bool:
        order = {"unrelated": 0, "context": 1, "related": 2, "direct": 3}
        states = news_domain.news_state_payload(payload or {})
        return order.get(states["relevanceState"], 0) >= order[self.minimum_relevance_state()]

    def provider_timeout_seconds(self, provider: str) -> float:
        normalized = str(provider or "").strip().lower().replace("-", "_")
        if normalized == "gdelt":
            return float_setting(self.settings, "newsCollectionGdeltTimeoutSeconds", 4.0, 0.5, 30.0)
        return float_setting(
            self.settings,
            "newsCollectionProviderTimeoutSeconds",
            float_setting(self.settings, "newsCollectionTimeoutSeconds", 8.0, 0.5, 60.0),
            0.5,
            60.0,
        )

    @contextmanager
    def provider_deadline(self, provider: str):
        seconds = self.provider_timeout_seconds(provider)
        can_alarm = threading.current_thread() is threading.main_thread() and hasattr(signal, "setitimer")
        if not can_alarm or seconds <= 0:
            yield
            return
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)

        def timeout_handler(_signum, _frame):
            raise NewsProviderTimeout(str(provider) + " provider timeout after " + str(round(seconds, 2)) + "s")

        signal.signal(signal.SIGALRM, timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer and previous_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])

    def article_body_enabled(self) -> bool:
        return truthy(self.settings.get("newsCollectionArticleBodyEnabled"), True)

    def require_article_body_for_rss(self) -> bool:
        return truthy(self.settings.get("newsCollectionRequireArticleBodyForRss"), True)

    def provider_is_rss(self, provider: object) -> bool:
        normalized = re.sub(r"[\s-]+", "_", str(provider or "").strip().lower())
        return normalized in RSS_PROVIDER_NAMES

    def provider_requires_article_body(self, provider: object) -> bool:
        normalized = re.sub(r"[\s-]+", "_", str(provider or "").strip().lower())
        return (
            self.provider_is_rss(provider) and self.require_article_body_for_rss()
        ) or normalized in DIRECT_BODY_REQUIRED_PROVIDER_NAMES

    def is_article_body_request(self, headers: Dict[str, str] = None) -> bool:
        accept = str((headers or {}).get("Accept") or "").lower()
        return "text/html" in accept

    def article_body_timeout_seconds(self) -> float:
        return float_setting(
            self.settings,
            "newsCollectionArticleBodyTimeoutSeconds",
            float_setting(self.settings, "newsCollectionProviderTimeoutSeconds", 8.0, 0.5, 60.0),
            0.5,
            30.0,
        )

    def article_body_max_per_target(self) -> int:
        return int_setting(self.settings, "newsCollectionArticleBodyMaxPerTarget", 10000, 0, 10000)

    def article_body_max_per_run(self) -> int:
        return int_setting(self.settings, "newsCollectionArticleBodyMaxPerRun", 10000, 0, 10000)

    def article_body_budget_available(self) -> bool:
        return (
            self._article_body_fetches_for_target < self.article_body_max_per_target()
            and self._article_body_fetches_used < self.article_body_max_per_run()
        )

    def article_text_for_url(self, url: str) -> str:
        if not self.article_body_enabled():
            return ""
        normalized = str(url or "").strip()
        if not normalized.startswith(("http://", "https://")):
            return ""
        if not self.article_body_budget_available():
            return ""
        self._article_body_fetches_for_target += 1
        self._article_body_fetches_used += 1
        try:
            raw_html = self.fetch_text(normalized, {
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "DigitalTwin/1.0",
            })
        except Exception:  # noqa: BLE001 - article-body fetch must not block headline collection.
            return ""
        return extract_article_text(raw_html)

    def news_evidence_from_article(
        self,
        target: NewsCollectionTarget,
        provider: str,
        source: str,
        title: str,
        feed_summary: str,
        link: str,
        published: object,
        metadata: Dict[str, object] = None,
    ) -> Optional[ResearchEvidence]:
        self.record_provider_diagnostic("candidateCount")
        preliminary = classify_news_relevance(target, title, feed_summary or title, source, provider)
        provider_key = re.sub(r"[\s-]+", "_", str(provider or "").strip().lower())
        search_target_unconfirmed = (
            provider_key in DIRECT_BODY_REQUIRED_PROVIDER_NAMES
            and str(preliminary.get("relationScope") or "") != "direct"
            and not list(preliminary.get("matchedAliases") or [])
        )
        if search_target_unconfirmed or not self.relevance_state_passes(preliminary) or not news_domain.relation_scope_is_investable(preliminary.get("relationScope")):
            self.record_provider_diagnostic("preliminaryRejectedCount")
            return None
        article_source_allowed = article_body_allowed_for_source(source, provider)
        body_budget_available = self.article_body_budget_available()
        if article_source_allowed and body_budget_available:
            self.record_provider_diagnostic("bodyFetchAttemptCount")
        elif not article_source_allowed:
            self.record_provider_diagnostic("sourceBlockedCount")
        article_text = self.article_text_for_url(link) if article_source_allowed else ""
        if not article_text and self.provider_requires_article_body(provider):
            self.record_provider_diagnostic("bodyMissingCount")
            if article_source_allowed and not body_budget_available:
                self.record_provider_diagnostic("bodyBudgetRejectedCount")
            return None
        analysis_text = article_text or feed_summary or title
        relevance = classify_news_relevance(target, title, analysis_text, source, provider)
        if not self.relevance_state_passes(relevance) or not news_domain.relation_scope_is_investable(relevance.get("relationScope")):
            self.record_provider_diagnostic("finalRelevanceRejectedCount")
            return None
        polarity = keyword_polarity(title + " " + analysis_text)
        summary_ko = news_domain.korean_article_summary(
            target,
            title,
            article_text,
            feed_summary,
            relevance,
        )
        stock_impact = news_domain.stock_impact_analysis(
            target,
            title,
            article_text,
            feed_summary,
            relevance,
            polarity,
        )
        read_status = "body" if article_text else ("source-blocked" if not article_source_allowed else "feed-summary")
        analysis_source = "article-body" if article_text else ("source-quality-gate" if not article_source_allowed else "feed-summary")
        analysis_quality = "body-read" if article_text else ("source-blocked" if not article_source_allowed else "feed-only")
        article_facts = news_domain.article_analysis_facts(
            target,
            title,
            article_text,
            feed_summary,
            relevance,
            stock_impact,
            source,
            provider,
            link,
            published,
            read_status,
            analysis_source,
            analysis_quality,
            summary_ko,
        )
        symbol = target.normalized_symbol()
        payload = {
            **(metadata or {}),
            **relevance,
            **stock_impact,
            "articleSummaryKo": summary_ko,
            "articleReadStatus": read_status,
            "articleAnalysisSource": analysis_source,
            "articleAnalysisQuality": analysis_quality,
            "articleFacts": article_facts,
            "articleTextPreview": compact_text(article_text, 700) if article_text else "",
        }
        evidence = ResearchEvidence(
            evidence_id="research:" + symbol + ":news:" + stable_evidence_token(provider, title, link),
            symbol=symbol,
            kind="news",
            source=source,
            title=title,
            summary=summary_ko or compact_text(feed_summary or title, 360),
            url=link,
            observed_at=utc_now_iso(),
            polarity=polarity,
            published_at=iso_or_empty(published),
            raw_payload=payload,
        )
        self.record_provider_diagnostic("acceptedCount")
        return evidence

    def collect_for_target(
        self,
        target: NewsCollectionTarget,
        source_types: Iterable[str] = None,
    ) -> Tuple[List[ResearchEvidence], List[Dict[str, object]]]:
        requested = {str(item or "").strip().lower() for item in source_types or [] if str(item or "").strip()}
        if requested and not requested.intersection({"news", "news-full-text", "article", "official"}):
            return [], [{
                "source": "news-source-gateway",
                "symbol": target.normalized_symbol(),
                "ok": True,
                "status": "not-requested",
                "requestedSourceTypes": sorted(requested),
            }]
        items: List[ResearchEvidence] = []
        statuses: List[Dict[str, object]] = []
        seen = set()
        limit = self.per_symbol_limit()
        self._article_body_fetches_for_target = 0
        for provider in self.providers():
            remaining = max(0, limit - len(items))
            if remaining <= 0:
                break
            try:
                self.reset_provider_diagnostics()
                with self.provider_deadline(provider):
                    fetched = self.fetch_provider(provider, target)
                saved = 0
                for item in fetched:
                    key = item.url or item.title
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    items.append(item)
                    saved += 1
                    if saved >= remaining:
                        break
                diagnostics = dict(self._current_provider_diagnostics)
                statuses.append({
                    "source": provider,
                    "symbol": target.normalized_symbol(),
                    "ok": True,
                    "count": saved,
                    **diagnostics,
                    "status": "ok" if saved else provider_empty_status(diagnostics),
                })
            except Exception as error:  # noqa: BLE001 - one feed must not stop the collection cycle.
                statuses.append({"source": provider, "symbol": target.normalized_symbol(), "ok": False, "message": str(error)[:180]})
        ranked = sorted(items, key=self.evidence_rank_key, reverse=True)
        return ranked[: self.per_symbol_limit()], statuses

    def evidence_rank_key(self, item: ResearchEvidence) -> tuple:
        payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
        facts = payload.get("articleFacts") if isinstance(payload.get("articleFacts"), dict) else {}
        body_available = bool(facts.get("bodyAvailable"))
        states = news_domain.news_state_rank({**facts, **payload})
        published = parse_news_datetime(item.published_at or item.observed_at)
        timestamp = published.timestamp() if published else 0.0
        return body_available, *states, timestamp

    def fetch_provider(self, provider: str, target: NewsCollectionTarget) -> List[ResearchEvidence]:
        if provider in {"google_rss_kr", "rss_kr", "kr"}:
            return self.fetch_google_news_rss(target, locale="KR")
        if provider in {"google_rss_us", "rss_us", "us"}:
            return self.fetch_google_news_rss(target, locale="US")
        if provider in {"yahoo_finance", "yahoo_finance_rss", "yahoo_rss"}:
            return self.fetch_yahoo_finance_rss(target)
        if provider in {"yahoo_search", "yahoo_finance_search"}:
            return self.fetch_yahoo_finance_search(target)
        if provider == "gdelt":
            return self.fetch_gdelt(target)
        return []

    def yahoo_finance_symbol(self, target: NewsCollectionTarget) -> str:
        symbol = target.normalized_symbol()
        market = str(target.market or "").upper().strip()
        if "." in symbol:
            return symbol
        if market == "KOSDAQ":
            return symbol + ".KQ"
        if market in {"KR", "KOSPI", "KRX"} or (symbol.isdigit() and len(symbol) == 6):
            return symbol + ".KS"
        return symbol

    def fetch_yahoo_finance_rss(self, target: NewsCollectionTarget) -> List[ResearchEvidence]:
        yahoo_symbol = self.yahoo_finance_symbol(target)
        params = {
            "s": yahoo_symbol,
            "region": "US",
            "lang": "en-US",
        }
        url = "https://feeds.finance.yahoo.com/rss/2.0/headline?" + urllib.parse.urlencode(params)
        xml_text = self.fetch_text(url, {"Accept": "application/rss+xml", "User-Agent": "DigitalTwin/1.0"})
        root = ET.fromstring(xml_text)
        evidence: List[ResearchEvidence] = []
        for item in root.findall(".//item"):
            title = strip_html(item.findtext("title"))
            link = str(item.findtext("link") or "").strip()
            published = item.findtext("pubDate") or ""
            if not title or not link or not within_lookback(published, self.lookback_minutes()):
                continue
            source_text = strip_html(item.findtext("source")) or "Yahoo Finance"
            summary = strip_html(item.findtext("description"))
            item_evidence = self.news_evidence_from_article(
                target,
                "Yahoo Finance RSS",
                source_text,
                title,
                summary,
                link,
                published,
                {
                    "provider": "Yahoo Finance RSS",
                    "query": yahoo_symbol,
                    "feedUrl": url,
                },
            )
            if not item_evidence:
                continue
            evidence.append(item_evidence)
            if len(evidence) >= self.per_symbol_limit():
                break
        return evidence

    def fetch_yahoo_finance_search(self, target: NewsCollectionTarget) -> List[ResearchEvidence]:
        symbol = self.yahoo_finance_symbol(target)
        query = symbol if not symbol.endswith((".KS", ".KQ")) else target.search_query()
        params = {
            "q": query,
            "quotesCount": "1",
            "newsCount": str(min(30, max(8, self.per_symbol_limit() * 2))),
            "enableFuzzyQuery": "false",
        }
        url = "https://query1.finance.yahoo.com/v1/finance/search?" + urllib.parse.urlencode(params)
        payload = self.fetch_json(url, {"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        articles = payload.get("news") if isinstance(payload, dict) and isinstance(payload.get("news"), list) else []
        evidence: List[ResearchEvidence] = []
        for article in articles:
            if not isinstance(article, dict):
                continue
            title = str(article.get("title") or "").strip()
            link = str(article.get("link") or "").strip()
            published_epoch = number(article.get("providerPublishTime"))
            published = datetime.fromtimestamp(published_epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z") if published_epoch else ""
            if not title or not link or not within_lookback(published, self.lookback_minutes()):
                continue
            source = str(article.get("publisher") or "Yahoo Finance").strip()
            item_evidence = self.news_evidence_from_article(
                target,
                "Yahoo Finance Search",
                source,
                title,
                "",
                link,
                published,
                {
                    "provider": "Yahoo Finance Search",
                    "query": query,
                    "searchUrl": url,
                },
            )
            if not item_evidence:
                continue
            evidence.append(item_evidence)
            if len(evidence) >= self.per_symbol_limit():
                break
        return evidence

    def fetch_google_news_rss(self, target: NewsCollectionTarget, locale: str = "KR") -> List[ResearchEvidence]:
        symbol = target.normalized_symbol()
        locale = "US" if str(locale or "").upper() == "US" else "KR"
        query = target.search_query()
        params = {
            "q": query,
            "hl": "en-US" if locale == "US" else "ko",
            "gl": "US" if locale == "US" else "KR",
            "ceid": "US:en" if locale == "US" else "KR:ko",
        }
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)
        xml_text = self.fetch_text(url, {"Accept": "application/rss+xml", "User-Agent": "DigitalTwin/1.0"})
        root = ET.fromstring(xml_text)
        evidence: List[ResearchEvidence] = []
        source_name = "Google News US" if locale == "US" else "Google News KR"
        for item in root.findall(".//item"):
            title = strip_html(item.findtext("title"))
            link = str(item.findtext("link") or "").strip()
            published = item.findtext("pubDate") or ""
            if not title or not link or not within_lookback(published, self.lookback_minutes()):
                continue
            source = item.find("source")
            source_text = strip_html(source.text if source is not None else "") or source_name
            summary = strip_html(item.findtext("description"))
            item_evidence = self.news_evidence_from_article(
                target,
                source_name,
                source_text,
                title,
                summary,
                link,
                published,
                {
                    "provider": source_name,
                    "locale": locale,
                    "query": query,
                    "feedUrl": url,
                },
            )
            if not item_evidence:
                continue
            evidence.append(item_evidence)
            if len(evidence) >= self.per_symbol_limit():
                break
        return evidence

    def fetch_gdelt(self, target: NewsCollectionTarget) -> List[ResearchEvidence]:
        symbol = target.normalized_symbol()
        query = target.search_query()
        params = {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(min(50, max(5, self.per_symbol_limit() * 2))),
            "sort": "HybridRel",
            "timespan": str(self.lookback_minutes()) + "m",
        }
        url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(params)
        payload = self.fetch_json(url, {"Accept": "application/json", "User-Agent": "DigitalTwin/1.0"})
        articles = payload.get("articles") if isinstance(payload, dict) and isinstance(payload.get("articles"), list) else []
        evidence: List[ResearchEvidence] = []
        for article in articles:
            if not isinstance(article, dict):
                continue
            title = str(article.get("title") or "").strip()
            link = str(article.get("url") or "").strip()
            published = article.get("seendate") or ""
            if not title or not link or not within_lookback(published, self.lookback_minutes()):
                continue
            source = str(article.get("domain") or "GDELT News").strip()
            item_evidence = self.news_evidence_from_article(
                target,
                "GDELT",
                source,
                title,
                title,
                link,
                published,
                {
                    "provider": "GDELT",
                    "sourceCountry": str(article.get("sourceCountry") or "").strip(),
                    "language": str(article.get("language") or "").strip(),
                    "query": query,
                },
            )
            if not item_evidence:
                continue
            evidence.append(item_evidence)
            if len(evidence) >= self.per_symbol_limit():
                break
        return evidence
