from typing import Iterable, Protocol

from digital_twin.modules.news_intelligence.domain.article import NewsArticle, NewsTarget


class NewsSourceGateway(Protocol):
    def collect(self, target: NewsTarget) -> Iterable[NewsArticle]:
        ...
