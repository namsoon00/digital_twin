from typing import Protocol

from ..domain.article import NewsArticle


class NewsAiAnalyzer(Protocol):
    def analyze(self, article: NewsArticle) -> dict:
        ...
