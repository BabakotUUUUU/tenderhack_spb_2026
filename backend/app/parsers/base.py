"""
Базовый класс парсера и утилиты ограничения частоты запросов.

Стратегия обхода блокировок (rate limiting):
- Случайные задержки между запросами (2–6 сек) — имитирует человека
- Ротация User-Agent заголовков
- Использование asyncio.Semaphore для параллельного ограничения
- Экспоненциальный backoff при ошибках 429/503
- Опциональная поддержка proxy-list (из переменной окружения PROXY_LIST)

Обоснование выбранных задержек:
  Яндекс Маркет / Ozon / WB блокируют при <1 req/сек с одного IP.
  2–6 сек рандомизация снижает вероятность блокировки до приемлемого уровня
  при хакатонных нагрузках (не продакшн-скейл).
"""

import asyncio
import random
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
]


@dataclass
class ProductItem:
    """Унифицированная структура товарной позиции."""
    title: str
    price: Optional[float]
    id: Optional[str] = None
    currency: str = "RUB"
    old_price: Optional[float] = None
    image_url: Optional[str] = None
    product_url: str = ""
    source: str = ""
    domain: Optional[str] = None
    characteristics: dict = field(default_factory=dict)
    availability: Optional[str] = None
    rating: Optional[float] = None
    reviews_count: Optional[int] = None
    relevance_score: Optional[float] = None  # заполняется ML-ранкером
    relevance_explanation: Optional[str] = None


class RateLimiter:
    """
    Ограничитель частоты запросов с рандомизацией.
    Используется для всех парсеров.
    """
    def __init__(self, min_delay: float = 2.0, max_delay: float = 6.0, max_concurrent: int = 2):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._last_request: dict[str, float] = {}

    async def wait(self, domain: str = "default"):
        async with self._semaphore:
            now = time.monotonic()
            last = self._last_request.get(domain, 0)
            elapsed = now - last
            delay = random.uniform(self.min_delay, self.max_delay)
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self._last_request[domain] = time.monotonic()


# Глобальный rate limiter (один на всё приложение)
global_rate_limiter = RateLimiter(min_delay=2.5, max_delay=5.5, max_concurrent=3)


def get_headers(referer: str = "") -> dict:
    """Генерирует реалистичные заголовки браузера."""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        headers["Referer"] = referer
    return headers


async def fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    domain: str,
    params: dict = None,
    max_retries: int = 3,
) -> Optional[httpx.Response]:
    """
    Выполняет HTTP GET с экспоненциальным backoff при ошибках.
    """
    for attempt in range(max_retries):
        try:
            await global_rate_limiter.wait(domain)
            resp = await client.get(
                url,
                params=params,
                headers=get_headers(f"https://{domain}/"),
                follow_redirects=True,
                timeout=15.0,
            )
            if resp.status_code == 429:
                wait_time = (2 ** attempt) * 3 + random.uniform(1, 3)
                logger.warning(f"[{domain}] Rate limited, waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
                continue
            if resp.status_code in (200, 206):
                return resp
            logger.warning(f"[{domain}] HTTP {resp.status_code} for {url}")
            return None
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            wait_time = (2 ** attempt) * 2
            logger.warning(f"[{domain}] Attempt {attempt+1} failed: {e}. Retry in {wait_time}s")
            await asyncio.sleep(wait_time)
    return None


class BaseParser(ABC):
    """Абстрактный базовый класс для всех парсеров."""
    
    source_name: str = "unknown"
    domain: str = "unknown"
    
    def __init__(self):
        self.client = httpx.AsyncClient(
            headers=get_headers(),
            follow_redirects=True,
            timeout=20.0,
        )
    
    @abstractmethod
    async def search(self, query: str, region: str = "Москва", limit: int = 10) -> list[ProductItem]:
        """Выполняет поиск товаров по запросу."""
        ...
    
    async def close(self):
        await self.client.aclose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        await self.close()
