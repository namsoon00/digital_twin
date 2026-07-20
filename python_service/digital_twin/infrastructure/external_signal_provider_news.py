import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from ..domain.portfolio import Position, utc_now_iso


class ExternalSignalNewsMixin:
    def add_news_headlines(self, signals: Dict[str, object], positions: List[Position]) -> None:
        if not self.external_api_enabled("externalNewsEnabled"):
            return
        positions_by_symbol = {str(position.symbol or "").upper(): position for position in positions if not position.is_cash()}
        for symbol in self.limited_targets(signals, "News", self.news_symbols(positions), "externalNewsMaxSymbols", 3):
            position = positions_by_symbol.get(symbol)
            if not position:
                continue
            source = "News"
            try:
                provider = self.news_provider_for_position(position)
                source = "Alpha Vantage News" if provider == "alpha_vantage" else "GDELT News"
                target = "NEWS_SENTIMENT:" + symbol if provider == "alpha_vantage" else "doc:" + symbol

                def fetch_news():
                    return self.fetch_alpha_vantage_news(symbol, position) if provider == "alpha_vantage" else self.fetch_gdelt_news(symbol, position)

                news = self.guarded_call(source, target, fetch_news)
                if news:
                    signals.setdefault("newsHeadlines", {})[symbol] = news
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, source, symbol + " ", error)

    def fetch_gdelt_news(self, symbol: str, position: Position) -> Dict[str, object]:
        query = self.gdelt_news_query(position)
        lookback_hours = self.int_setting("externalNewsLookbackHours", 48, 1)
        url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode({
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": "5",
            "sort": "HybridRel",
            "timespan": str(lookback_hours) + "h",
        })
        payload = self.fetch_json(url, {"Accept": "application/json", "User-Agent": "DigitalTwin/1.0"})
        articles = payload.get("articles") if isinstance(payload, dict) and isinstance(payload.get("articles"), list) else []
        items = []
        seen = set()
        for article in articles[:10]:
            if not isinstance(article, dict):
                continue
            title = str(article.get("title") or "").strip()
            url_value = str(article.get("url") or "").strip()
            dedupe_key = url_value or title
            if not title or not url_value or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            items.append({
                "title": title,
                "url": url_value,
                "domain": str(article.get("domain") or "").strip(),
                "sourceCountry": str(article.get("sourceCountry") or "").strip(),
                "language": str(article.get("language") or "").strip(),
                "seenDate": str(article.get("seendate") or "").strip(),
            })
            if len(items) >= 5:
                break
        return {
            "provider": "GDELT",
            "symbol": symbol,
            "name": str(position.name or symbol),
            "query": query,
            "lookbackHours": lookback_hours,
            "items": items,
            "count": len(items),
            "fetchedAt": utc_now_iso(),
        }

    def fetch_alpha_vantage_news(self, symbol: str, position: Position) -> Dict[str, object]:
        api_key = str(self.settings.get("alphaVantageApiKey") or "").strip()
        if not api_key:
            raise RuntimeError("Alpha Vantage API key 없음")
        lookback_hours = self.int_setting("externalNewsLookbackHours", 48, 1)
        time_from = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).strftime("%Y%m%dT%H%M")
        url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode({
            "function": "NEWS_SENTIMENT",
            "tickers": symbol,
            "apikey": api_key,
            "limit": "5",
            "sort": "LATEST",
            "time_from": time_from,
        })
        payload = self.fetch_json(url, {"Accept": "application/json"})
        if isinstance(payload, dict) and (payload.get("Information") or payload.get("Note")):
            raise RuntimeError(str(payload.get("Information") or payload.get("Note")))
        feed = payload.get("feed") if isinstance(payload, dict) and isinstance(payload.get("feed"), list) else []
        items = []
        seen = set()
        for article in feed[:10]:
            if not isinstance(article, dict):
                continue
            title = str(article.get("title") or "").strip()
            url_value = str(article.get("url") or "").strip()
            dedupe_key = url_value or title
            if not title or not url_value or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            ticker_sentiment = self.alpha_news_ticker_sentiment(article, symbol)
            sentiment_label = str(ticker_sentiment.get("ticker_sentiment_label") or "").strip().lower() if ticker_sentiment else ""
            if "bullish" in sentiment_label:
                impact_polarity = "support"
            elif "bearish" in sentiment_label:
                impact_polarity = "risk"
            else:
                impact_polarity = "context"
            items.append({
                "title": title,
                "url": url_value,
                "domain": str(article.get("source") or "").strip(),
                "summary": str(article.get("summary") or "").strip(),
                "seenDate": str(article.get("time_published") or "").strip(),
                "relationScope": "direct" if ticker_sentiment else "related_product",
                "relevanceState": "direct" if ticker_sentiment else "related",
                "stockImpactPolarity": impact_polarity,
                "dataState": "partial",
                "validationState": "conditional",
            })
            if len(items) >= 5:
                break
        return {
            "provider": "Alpha Vantage",
            "symbol": symbol,
            "name": str(position.name or symbol),
            "query": "NEWS_SENTIMENT:" + symbol,
            "lookbackHours": lookback_hours,
            "items": items,
            "count": len(items),
            "fetchedAt": utc_now_iso(),
        }

    def alpha_news_ticker_sentiment(self, article: Dict[str, object], symbol: str) -> Dict[str, object]:
        ticker_sentiments = article.get("ticker_sentiment") if isinstance(article.get("ticker_sentiment"), list) else []
        normalized = str(symbol or "").upper()
        for item in ticker_sentiments:
            if isinstance(item, dict) and str(item.get("ticker") or "").upper() == normalized:
                return item
        return {}

    def news_provider(self) -> str:
        raw = str(self.settings.get("externalNewsProvider") or "auto").strip().lower().replace("-", "_")
        if raw in {"alpha", "alphavantage", "alpha_vantage", "alpha vantage"}:
            return "alpha_vantage"
        if raw == "gdelt":
            return "gdelt"
        return "auto"

    def news_provider_for_position(self, position: Position) -> str:
        provider = self.news_provider()
        if provider in {"gdelt", "alpha_vantage"}:
            return provider
        symbol = str(position.symbol or "").upper()
        market = str(position.market or "").upper()
        currency = str(position.currency or "").upper()
        has_alpha_key = bool(str(self.settings.get("alphaVantageApiKey") or "").strip())
        if has_alpha_key and not symbol.isdigit() and (market == "US" or currency == "USD"):
            return "alpha_vantage"
        return "gdelt"

    def add_gdelt_news(self, signals: Dict[str, object], positions: List[Position]) -> None:
        self.add_news_headlines(signals, positions)

    def news_symbols(self, positions: List[Position]) -> List[str]:
        if not self.external_api_enabled("externalNewsEnabled"):
            return []
        symbols = []
        seen = set()
        for position in positions:
            if position.is_cash():
                continue
            symbol = str(position.symbol or "").upper()
            name = str(position.name or "").strip()
            if not symbol or symbol in seen or not name:
                continue
            seen.add(symbol)
            symbols.append(symbol)
        return symbols

    def gdelt_news_query(self, position: Position) -> str:
        terms = []
        for raw in [position.name, position.symbol]:
            text = str(raw or "").replace('"', " ").strip()
            if text and text not in terms:
                terms.append(text)
        quoted = ['"' + term + '"' for term in terms[:2]]
        return "(" + " OR ".join(quoted) + ")" if quoted else '"' + str(position.symbol or "").upper() + '"'
