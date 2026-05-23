"""Общий HTTP-клиент для всех парсеров."""

import asyncio
import logging
import random
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

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


class Fetcher:
    def __init__(self, timeout: float = 15.0):
        timeout_config = httpx.Timeout(timeout, connect=min(5.0, timeout), read=timeout, write=5.0, pool=5.0)
        self.client = httpx.AsyncClient(
            timeout=timeout_config,
            follow_redirects=True,
            http2=False,
        )

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
                await asyncio.sleep(random.uniform(0.5, 1.5))
                r = await self.client.get(url, headers=headers, params=params)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (403, 429, 503):
                    logger.info("[HTTP] %s for %s", r.status_code, url)
                    if attempt < retries:
                        await asyncio.sleep(2 ** attempt * 2)
                    continue
                logger.info("[HTTP] %s for %s", r.status_code, url)
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
    ) -> Optional[str]:
        for attempt in range(retries + 1):
            try:
                await asyncio.sleep(random.uniform(0.5, 1.5))
                r = await self.client.get(url, headers=headers, params=params)
                if r.status_code == 200:
                    return r.text
                if r.status_code in (403, 429, 503):
                    logger.info("[HTTP] %s for %s", r.status_code, url)
                    if attempt < retries:
                        await asyncio.sleep(2 ** attempt * 2)
                    continue
                logger.info("[HTTP] %s for %s", r.status_code, url)
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
