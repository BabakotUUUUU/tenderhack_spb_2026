from abc import ABC, abstractmethod

from app.parsers.common import ProductItem


class BaseParser(ABC):
    source = "unknown"

    @abstractmethod
    async def search(self, query: str, region: str = "Москва", limit: int = 10, category: str = ""):
        ...

    async def close(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

