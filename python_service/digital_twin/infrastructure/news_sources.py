import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Dict, Iterable, List, Tuple

from ..domain import news_analysis as news_domain
from ..domain.investment_research import NewsCollectionTarget, ResearchEvidence, classify_news_relevance, compact_text, keyword_polarity, stable_evidence_token
from ..domain.market_data import number
from ..domain.portfolio import utc_now_iso


JsonFetcher = Callable[[str, Dict[str, str]], object]
TextFetcher = Callable[[str, Dict[str, str]], str]
TAG_RE = re.compile(r"<[^>]+>")


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


def strip_html(value: object) -> str:
    return compact_text(html.unescape(TAG_RE.sub(" ", str(value or ""))), 240)


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
            relevance = classify_news_relevance(target, title, summary, source_text, source_name)
            if number(relevance.get("relevanceScore")) < self.min_relevance_score() or relevance.get("relationScope") == "noise":
                continue
            polarity, impact = keyword_polarity(title + " " + summary)
            confidence = news_domain.confidence_from_analysis_payload(relevance)
            evidence.append(ResearchEvidence(
                "research:" + symbol + ":news:" + stable_evidence_token(source_name, title, link),
                symbol,
                "news",
                source_text,
                title,
                summary or title,
                link,
                utc_now_iso(),
                polarity,
                news_domain.impact_from_analysis_payload(impact, relevance),
                confidence,
                iso_or_empty(published),
                {
                    "provider": source_name,
                    "locale": locale,
                    "query": query,
                    "feedUrl": url,
                    **relevance,
                },
            ))
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
            relevance = classify_news_relevance(target, title, title, source, "GDELT")
            if number(relevance.get("relevanceScore")) < self.min_relevance_score() or relevance.get("relationScope") == "noise":
                continue
            polarity, impact = keyword_polarity(title)
            confidence = news_domain.confidence_from_analysis_payload(relevance)
            evidence.append(ResearchEvidence(
                "research:" + symbol + ":news:" + stable_evidence_token("GDELT", title, link),
                symbol,
                "news",
                source,
                title,
                title,
                link,
                utc_now_iso(),
                polarity,
                news_domain.impact_from_analysis_payload(impact, relevance),
                confidence,
                iso_or_empty(published),
                {
                    "provider": "GDELT",
                    "sourceCountry": str(article.get("sourceCountry") or "").strip(),
                    "language": str(article.get("language") or "").strip(),
                    "query": query,
                    **relevance,
                },
            ))
            if len(evidence) >= self.per_symbol_limit():
                break
        return evidence
