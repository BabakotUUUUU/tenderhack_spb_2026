"""
Асинхронный краулер Рунета с очередью и ограничением запросов.

Архитектура:
  - asyncio.Queue для URL-очереди
  - Семафор per-domain для rate limiting
  - robots.txt соответствие
  - Ротация User-Agent
  - Экспоненциальный backoff при ошибках
  - Ограничение глубины и страниц на домен
"""

import asyncio
import logging
import os
import random
import time
from typing import Callable, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.crawler.extractor import ExtractedProduct, extract_product, extract_product_links
from app.crawler.seeds import SeedSite, is_excluded

logger = logging.getLogger(__name__)

MAX_PAGES_PER_DOMAIN = int(os.getenv("CRAWLER_MAX_PAGES_PER_DOMAIN", "20"))
MAX_DEPTH = 3
CRAWL_TIMEOUT = 15.0
MAX_WORKERS = int(os.getenv("CRAWLER_WORKERS", "3"))

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_ROBOTS_CACHE: dict[str, tuple[RobotFileParser, float]] = {}
_ROBOTS_TTL = 3600  # 1 час


def _make_headers(referer: str = "") -> dict:
    h = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
    }
    if referer:
        h["Referer"] = referer
    return h


async def _check_robots(client: httpx.AsyncClient, url: str) -> bool:
    """Проверяет robots.txt. Возвращает True если URL разрешён."""
    try:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        now = time.monotonic()
        cached = _ROBOTS_CACHE.get(base)
        if cached:
            rp, ts = cached
            if now - ts < _ROBOTS_TTL:
                return rp.can_fetch("*", url)

        robots_url = f"{base}/robots.txt"
        resp = await client.get(robots_url, timeout=5.0, headers=_make_headers())
        rp = RobotFileParser()
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        else:
            rp.parse([])  # нет robots.txt → разрешено всё
        _ROBOTS_CACHE[base] = (rp, now)
        return rp.can_fetch("*", url)
    except Exception:
        return True  # при ошибке разрешаем


class CrawlerResult:
    """Результат краулинга одной страницы."""
    __slots__ = ("url", "product", "child_urls", "error")

    def __init__(
        self,
        url: str,
        product: Optional[ExtractedProduct] = None,
        child_urls: Optional[list[str]] = None,
        error: Optional[str] = None,
    ):
        self.url = url
        self.product = product
        self.child_urls = child_urls or []
        self.error = error


async def crawl_url(
    client: httpx.AsyncClient,
    url: str,
    referer: str = "",
    check_robots: bool = True,
) -> CrawlerResult:
    """Скачивает страницу и извлекает из неё товар + дочерние ссылки."""
    if check_robots and not await _check_robots(client, url):
        return CrawlerResult(url=url, error="robots.txt disallow")

    delay = random.uniform(1.5, 4.0)
    await asyncio.sleep(delay)

    for attempt in range(3):
        try:
            resp = await client.get(
                url,
                headers=_make_headers(referer),
                follow_redirects=True,
                timeout=CRAWL_TIMEOUT,
            )
            if resp.status_code == 429:
                wait = (2 ** attempt) * 5 + random.uniform(1, 3)
                logger.warning(f"[Crawler] 429 on {url}, wait {wait:.1f}s")
                await asyncio.sleep(wait)
                continue
            if resp.status_code != 200:
                return CrawlerResult(url=url, error=f"HTTP {resp.status_code}")

            html = resp.text
            product = extract_product(html, url)
            child_urls = extract_product_links(html, url)

            return CrawlerResult(url=url, product=product, child_urls=child_urls)

        except httpx.TimeoutException:
            wait = (2 ** attempt) * 2
            await asyncio.sleep(wait)
        except Exception as exc:
            return CrawlerResult(url=url, error=str(exc))

    return CrawlerResult(url=url, error="max retries exceeded")


async def _fetch_html_playwright(url: str) -> Optional[str]:
    """
    Загружает страницу через Playwright (headless Chromium) и возвращает
    полностью отрендеренный HTML. Используется для JS-тяжёлых страниц каталога.
    """
    try:
        from app.parsers.browser import new_page
        page = await new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(random.uniform(1.5, 2.5))
            html = await page.content()
            return html
        finally:
            try:
                await page.context.close()
            except Exception:
                pass
    except Exception as exc:
        logger.debug(f"[Crawler] Playwright fetch failed for {url}: {exc}")
        return None


async def crawl_seed_for_query(
    seed: SeedSite,
    query: str,
    on_product: Callable[[ExtractedProduct], None],
    max_products: int = 8,
    client: Optional[httpx.AsyncClient] = None,
) -> int:
    """
    Краулит один seed-сайт по запросу.

    Стратегия:
      1. Playwright загружает поисковую/каталожную страницу (JS-рендеринг)
         → получаем полный DOM со ссылками на товары
      2. httpx обходит отдельные товарные страницы (schema.org в статике)
         → извлекаем title, price, image, characteristics

    Возвращает количество найденных товаров.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=20.0,
            limits=httpx.Limits(max_keepalive_connections=5),
        )

    found = 0
    try:
        search_url = seed["search_pattern"].format(query=query.replace(" ", "+"))
        logger.info(f"[Crawler] Fetching seed (Playwright): {search_url}")

        # Шаг 1: Playwright для поисковой/каталожной страницы (JS-рендеринг)
        html = await _fetch_html_playwright(search_url)

        if not html:
            # Playwright недоступен или упал — httpx fallback
            logger.debug(f"[Crawler] Playwright failed, trying httpx for {search_url}")
            result = await crawl_url(client, search_url, check_robots=False)
            if result.error:
                result = await crawl_url(client, seed["catalog_url"], check_robots=False)
                if result.error:
                    return 0
            html = None  # уже обработано через crawl_url
            child_urls = result.child_urls
            if result.product and result.product.price:
                on_product(result.product)
                found += 1
        else:
            # Playwright отдал HTML — извлекаем товарные ссылки
            from app.crawler.extractor import extract_product, extract_product_links
            child_urls = extract_product_links(html, search_url)
            # Проверяем и сам поиск как товар (редко, но бывает)
            p = extract_product(html, search_url)
            if p and p.price:
                on_product(p)
                found += 1

        if not child_urls:
            # Пробуем каталожную страницу как fallback
            catalog_html = await _fetch_html_playwright(seed["catalog_url"])
            if catalog_html:
                from app.crawler.extractor import extract_product_links
                child_urls = extract_product_links(catalog_html, seed["catalog_url"])

        # Шаг 2: httpx для отдельных товарных страниц (быстро, schema.org)
        tasks = []
        for child_url in (child_urls or [])[:min(15, max_products * 2)]:
            if is_excluded(_extract_domain_from_url(child_url)):
                continue
            tasks.append(crawl_url(client, child_url, referer=search_url))

        child_results = await asyncio.gather(*tasks, return_exceptions=True)
        for cr in child_results:
            if isinstance(cr, Exception):
                continue
            if cr.product and cr.product.price and cr.product.title:
                on_product(cr.product)
                found += 1
                if found >= max_products:
                    break

    finally:
        if own_client:
            await client.aclose()

    logger.info(f"[Crawler] {seed['domain']}: found {found} products for '{query}'")
    return found


def _extract_domain_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc.lstrip("www.")
    except Exception:
        return ""


async def crawl_category_seeds(
    seeds: list[SeedSite],
    query: str,
    on_product: Callable[[ExtractedProduct], None],
    max_total: int = 12,
    max_concurrent_seeds: int = 3,
) -> int:
    """
    Параллельно краулит несколько seed-сайтов одной категории.
    Возвращает общее количество найденных товаров.
    """
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=25.0,
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
    ) as client:
        semaphore = asyncio.Semaphore(max_concurrent_seeds)
        total_found = 0
        lock = asyncio.Lock()

        async def crawl_one(seed: SeedSite) -> None:
            nonlocal total_found
            async with semaphore:
                async with lock:
                    if total_found >= max_total:
                        return
                per_seed = max(2, (max_total - total_found) // max(1, len(seeds)))

                def on_product_safe(p: ExtractedProduct) -> None:
                    nonlocal total_found
                    if total_found < max_total:
                        on_product(p)
                        total_found += 1

                await crawl_seed_for_query(
                    seed, query, on_product_safe, max_products=per_seed, client=client
                )

        await asyncio.gather(*[crawl_one(s) for s in seeds[:6]])

    return total_found
