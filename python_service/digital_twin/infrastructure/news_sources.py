import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from ..domain import news_analysis as news_domain
from ..domain.investment_research import NewsCollectionTarget, ResearchEvidence, classify_news_relevance, compact_text, keyword_polarity, stable_evidence_token
from ..domain.market_data import number
from ..domain.portfolio import utc_now_iso


JsonFetcher = Callable[[str, Dict[str, str]], object]
TextFetcher = Callable[[str, Dict[str, str]], str]
TAG_RE = re.compile(r"<[^>]+>")
DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}
ARTICLE_TEXT_LIMIT = 5000


def default_json_fetcher(url: str, headers: Dict[str, str] = None, timeout: float = 8.0) -> object:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=max(0.5, float(timeout or 8.0))) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def default_text_fetcher(url: str, headers: Dict[str, str] = None, timeout: float = 8.0) -> str:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=max(0.5, float(timeout or 8.0))) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def split_csv(raw: object, fallback: Iterable[str]) -> List[str]:
    values = [item.strip() for item in str(raw or "").split(",") if item.strip()]
    return values or list(fallback)


def int_setting(settings: Dict[str, str], key: str, fallback: int, lower: int = 0, upper: int = 100000) -> int:
    try:
        parsed = int(float(str(settings.get(key) or "").strip()))
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
    value = compact_text(text, 500)
    if len(value) < 28:
        return False
    lowered = value.lower()
    if any(term in lowered for term in [
        "cookie",
        "advertisement",
        "subscribe",
        "sign up",
        "all rights reserved",
        "개인정보",
        "구독",
        "광고",
        "저작권",
        "무단전재",
        "기자",
    ]):
        return False
    return True


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
            text = compact_text(" ".join(self.buffer), 420)
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
        self.fetch_json = fetch_json or (lambda url, headers=None: default_json_fetcher(url, headers, timeout=timeout))
        self.fetch_text = fetch_text or (lambda url, headers=None: default_text_fetcher(url, headers, timeout=timeout))

    def providers(self) -> List[str]:
        return [
            item.lower().replace("-", "_")
            for item in split_csv(
                self.settings.get("newsCollectionProviders"),
                ["google_rss_kr", "google_rss_us", "gdelt"],
            )
        ]

    def per_symbol_limit(self) -> int:
        return int_setting(self.settings, "newsCollectionPerSymbolLimit", 8, 1, 50)

    def lookback_minutes(self) -> int:
        return int_setting(self.settings, "newsCollectionLookbackMinutes", 180, 5, 1440 * 7)

    def min_relevance_score(self) -> float:
        return float(int_setting(self.settings, "newsCollectionMinRelevanceScore", 35, 0, 100))

    def article_body_enabled(self) -> bool:
        return truthy(self.settings.get("newsCollectionArticleBodyEnabled"), True)

    def article_text_for_url(self, url: str) -> str:
        if not self.article_body_enabled():
            return ""
        normalized = str(url or "").strip()
        if not normalized.startswith(("http://", "https://")):
            return ""
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
        preliminary = classify_news_relevance(target, title, feed_summary or title, source, provider)
        if number(preliminary.get("relevanceScore")) < self.min_relevance_score() or not news_domain.relation_scope_is_investable(preliminary.get("relationScope")):
            return None
        article_source_allowed = article_body_allowed_for_source(source, provider)
        article_text = self.article_text_for_url(link) if article_source_allowed else ""
        analysis_text = article_text or feed_summary or title
        relevance = classify_news_relevance(target, title, analysis_text, source, provider)
        if number(relevance.get("relevanceScore")) < self.min_relevance_score() or not news_domain.relation_scope_is_investable(relevance.get("relationScope")):
            return None
        polarity, impact = keyword_polarity(title + " " + analysis_text)
        confidence = news_domain.confidence_from_analysis_payload(relevance)
        impact_score = news_domain.impact_from_analysis_payload(impact, relevance)
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
            impact_score,
        )
        symbol = target.normalized_symbol()
        payload = {
            **(metadata or {}),
            **relevance,
            **stock_impact,
            "articleSummaryKo": summary_ko,
            "articleReadStatus": "body" if article_text else ("source-blocked" if not article_source_allowed else "feed-summary"),
            "articleAnalysisSource": "article-body" if article_text else ("source-quality-gate" if not article_source_allowed else "feed-summary"),
            "articleAnalysisQuality": "body-read" if article_text else ("source-blocked" if not article_source_allowed else "feed-only"),
            "articleTextPreview": compact_text(article_text, 700) if article_text else "",
        }
        return ResearchEvidence(
            "research:" + symbol + ":news:" + stable_evidence_token(provider, title, link),
            symbol,
            "news",
            source,
            title,
            summary_ko or compact_text(feed_summary or title, 360),
            link,
            utc_now_iso(),
            polarity,
            impact_score,
            confidence,
            iso_or_empty(published),
            payload,
        )

    def collect_for_target(self, target: NewsCollectionTarget) -> Tuple[List[ResearchEvidence], List[Dict[str, object]]]:
        items: List[ResearchEvidence] = []
        statuses: List[Dict[str, object]] = []
        seen = set()
        for provider in self.providers():
            try:
                fetched = self.fetch_provider(provider, target)
                saved = 0
                for item in fetched:
                    key = item.url or item.title
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    items.append(item)
                    saved += 1
                    if len(items) >= self.per_symbol_limit():
                        break
                statuses.append({"source": provider, "symbol": target.normalized_symbol(), "ok": True, "count": saved})
            except Exception as error:  # noqa: BLE001 - one feed must not stop the collection cycle.
                statuses.append({"source": provider, "symbol": target.normalized_symbol(), "ok": False, "message": str(error)[:180]})
            if len(items) >= self.per_symbol_limit():
                break
        return items, statuses

    def fetch_provider(self, provider: str, target: NewsCollectionTarget) -> List[ResearchEvidence]:
        if provider in {"google_rss_kr", "rss_kr", "kr"}:
            return self.fetch_google_news_rss(target, locale="KR")
        if provider in {"google_rss_us", "rss_us", "us"}:
            return self.fetch_google_news_rss(target, locale="US")
        if provider == "gdelt":
            return self.fetch_gdelt(target)
        return []

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
