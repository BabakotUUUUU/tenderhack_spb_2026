"""Общий HTTP-клиент для всех парсеров."""

import asyncio
import logging
import os
import random
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Если задан PROXY_URL — все запросы идут через прокси.
# Пример: PROXY_URL=http://user:pass@proxy-host:8080
PROXY_URL: Optional[str] = os.getenv("PROXY_URL")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


def browser_headers(referer: str = "") -> dict[str, str]:
    h = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-CH-UA": '"Chromium";v="124", "Google Chrome";v="124"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Windows"',
    }
    if referer:
        h["Referer"] = referer
    return h


def json_headers(referer: str = "") -> dict[str, str]:
    h = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
    }
    if referer:
        h["Referer"] = referer
    return h


def _make_client(timeout: float, connect_timeout: Optional[float] = None) -> httpx.AsyncClient:
    """Создаёт httpx-клиент с опциональным прокси."""
    connect = connect_timeout if connect_timeout is not None else min(5.0, timeout)
    timeout_cfg = httpx.Timeout(timeout, connect=connect, read=timeout, write=5.0, pool=5.0)
    kwargs: dict[str, Any] = {
        "timeout": timeout_cfg,
        "follow_redirects": True,
        "http2": False,
    }
    if PROXY_URL:
        kwargs["proxy"] = PROXY_URL
    return httpx.AsyncClient(**kwargs)


class Fetcher:
    def __init__(self, timeout: float = 15.0, connect_timeout: Optional[float] = None):
        self.client = _make_client(timeout, connect_timeout)

    async def get_json(
        self,
        url: str,
        *,
        headers: Optional[dict] = None,
        params: Optional[dict[str, Any]] = None,
        retries: int = 2,
    ) -> Optional[dict]:
        for attempt in range(retries + 1):
            try:
                await asyncio.sleep(random.uniform(0.3, 1.0))
                r = await self.client.get(url, headers=headers, params=params)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (403, 429, 503):
                    logger.info("[HTTP] %s for %s", r.status_code, url)
                    if attempt < retries:
                        await asyncio.sleep(2 ** attempt * 2)
                    continue
                logger.info("[HTTP] %s for %s", r.status_code, url)
            except (httpx.ConnectTimeout, httpx.ConnectError):
                logger.info("[HTTP] connect failed for %s", url)
                return None  # сразу выходим — повторы не помогут
            except Exception:
                logger.info("[HTTP] request failed for %s", url, exc_info=True)
                if attempt < retries:
                    await asyncio.sleep(2 ** attempt)
        return None

    async def get_text(
        self,
        url: str,
        *,
        headers: Optional[dict] = None,
        params: Optional[dict[str, Any]] = None,
        retries: int = 2,
        return_on_status: Optional[set[int]] = None,
    ) -> Optional[str]:
        """
        Возвращает текст страницы.
        return_on_status — вернуть тело даже при этих кодах ответа
        (например {403} чтобы попробовать разобрать антибот-страницу).
        """
        for attempt in range(retries + 1):
            try:
                await asyncio.sleep(random.uniform(0.3, 1.0))
                r = await self.client.get(url, headers=headers, params=params)
                if r.status_code == 200:
                    return r.text
                if return_on_status and r.status_code in return_on_status:
                    return r.text
                if r.status_code in (403, 429, 503):
                    logger.info("[HTTP] %s for %s", r.status_code, url)
                    if attempt < retries:
                        await asyncio.sleep(2 ** attempt * 2)
                    continue
                logger.info("[HTTP] %s for %s", r.status_code, url)
            except (httpx.ConnectTimeout, httpx.ConnectError):
                logger.info("[HTTP] connect failed for %s", url)
                return None  # сразу выходим
            except Exception:
                logger.info("[HTTP] request failed for %s", url, exc_info=True)
                if attempt < retries:
                    await asyncio.sleep(2 ** attempt)
        return None

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
