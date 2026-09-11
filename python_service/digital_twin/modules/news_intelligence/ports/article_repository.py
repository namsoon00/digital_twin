from typing import Iterable, Protocol


class NewsArticleRepository(Protocol):
    def latest(self, symbol: str = "", kind: str = "news", limit: int = 500):
        ...

    def upsert_many(self, items: Iterable[object]) -> int:
        ...
