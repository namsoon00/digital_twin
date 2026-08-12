from typing import Iterable, Protocol

from ..domain.article import NewsArticle, NewsTarget


class NewsSourceGateway(Protocol):
    def collect(self, target: NewsTarget) -> Iterable[NewsArticle]:
        ...
