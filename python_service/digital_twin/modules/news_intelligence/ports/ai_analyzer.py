from typing import Protocol

from digital_twin.modules.news_intelligence.domain.article import NewsArticle


class NewsAiAnalyzer(Protocol):
    def analyze(self, article: NewsArticle) -> dict:
        ...
