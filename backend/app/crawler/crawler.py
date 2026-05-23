"""
Асинхронный краулер Рунета.

Архитектура:
  - httpx-первый подход: для большинства сайтов статика без JS достаточна
  - Playwright только если seed.use_playwright=True или httpx вернул пустой результат
  - Параллельный обход товарных ссылок через asyncio.gather
  - Коrotkiye задержки: сайты это не агрессивный парсинг, а точечный поиск
"""

import asyncio
import logging
import os
import random
import re
from typing import Callable, Optional
from urllib.parse import urlparse, urljoin

import httpx

from app.crawler.extractor import ExtractedProduct, extract_product, extract_product_links
from app.crawler.seeds import SeedSite, is_excluded

logger = logging.getLogger(__name__)

MAX_PAGES_PER_DOMAIN = int(os.getenv("CRAWLER_MAX_PAGES_PER_DOMAIN", "15"))
MAX_DEPTH = 2
CRAWL_TIMEOUT = 12.0
MAX_WORKERS = int(os.getenv("CRAWLER_WORKERS", "4"))

# Если задан PROXY_URL — краулер использует прокси
PROXY_URL: Optional[str] = os.getenv("PROXY_URL")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


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


def _make_client(**kwargs) -> httpx.AsyncClient:
    """Создаёт httpx-клиент с опциональным прокси."""
    if PROXY_URL:
        kwargs["proxy"] = PROXY_URL
    return httpx.AsyncClient(**kwargs)


# ── Сайт-специфичные паттерны ссылок ─────────────────────────────────────────

# Расширенные паттерны для 4tochki.ru и foroffice.ru
_SITE_PRODUCT_PATTERNS: dict[str, re.Pattern] = {
    "4tochki.ru": re.compile(
        r"/catalog/tyres/[a-z][a-z0-9\-]+/[a-z][a-z0-9\-]+-\d{5,}/?$",
        re.IGNORECASE,
    ),
    "foroffice.ru": re.compile(
        r"/products/description/\d{4,}\.html$",
        re.IGNORECASE,
    ),
}


def _extract_product_links_for_site(html: str, base_url: str, domain: str) -> list[str]:
    """
    Извлекает продуктовые ссылки с учётом сайт-специфичных паттернов.
    Для известных сайтов использует точный regexp; иначе — общий.
    """
    base_netloc = urlparse(base_url).netloc.lstrip("www.")
    site_key = next((k for k in _SITE_PRODUCT_PATTERNS if k in base_netloc), None)

    if site_key:
        pattern = _SITE_PRODUCT_PATTERNS[site_key]
        links: list[str] = []
        for m in re.finditer(r'href="(/[^"]{5,120})"', html):
            path = m.group(1)
            if pattern.search(path):
                full = urljoin(base_url, path)
                links.append(full)
        # Дедупликация, сохраняя порядок
        seen: set[str] = set()
        result = []
        for l in links:
            if l not in seen:
                seen.add(l)
                result.append(l)
        return result[:30]

    return extract_product_links(html, base_url)


async def _fetch_html_httpx(client: httpx.AsyncClient, url: str, referer: str = "") -> Optional[str]:
    """Скачивает страницу через httpx. Без delays — вызывается точечно."""
    for attempt in range(2):
        try:
            r = await client.get(url, headers=_make_headers(referer), follow_redirects=True)
            if r.status_code == 200:
                return r.text
            if r.status_code == 429:
                await asyncio.sleep(3.0 * (attempt + 1))
                continue
            logger.debug(f"[Crawler] {r.status_code} for {url}")
            return None
        except (httpx.ConnectTimeout, httpx.ConnectError):
            logger.debug(f"[Crawler] connect error for {url}")
            return None
        except httpx.TimeoutException:
            if attempt < 1:
                await asyncio.sleep(2.0)
        except Exception as exc:
            logger.debug(f"[Crawler] error for {url}: {exc}")
            return None
    return None


async def _fetch_html_playwright(url: str) -> Optional[str]:
    """Загружает страницу через Playwright (headless Chromium)."""
    try:
        from app.parsers.browser import new_page
        page = await new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=18_000)
            await asyncio.sleep(random.uniform(1.0, 2.0))
            return await page.content()
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
      1. httpx → поисковая страница → извлекаем ссылки на товары
      2. Playwright если seed.use_playwright=True или httpx вернул пустые ссылки
      3. httpx → отдельные товарные страницы (быстро, schema.org в статике)
    """
    own_client = client is None
    if own_client:
        kwargs = {
            "follow_redirects": True,
            "timeout": httpx.Timeout(CRAWL_TIMEOUT, connect=5.0),
            "limits": httpx.Limits(max_keepalive_connections=5),
        }
        if PROXY_URL:
            kwargs["proxy"] = PROXY_URL
        client = httpx.AsyncClient(**kwargs)

    found = 0
    domain = seed["domain"]
    use_playwright = seed.get("use_playwright", False)

    try:
        search_url = seed["search_pattern"].format(query=query.replace(" ", "+"))
        logger.info(f"[Crawler] {domain}: fetching {search_url}")

        html: Optional[str] = None

        # Шаг 1: httpx для поисковой страницы
        if not use_playwright:
            html = await _fetch_html_httpx(client, search_url)

        # Шаг 2: Playwright ТОЛЬКО если seed явно требует JS-рендеринга
        if not html and use_playwright:
            logger.debug(f"[Crawler] {domain}: trying Playwright for {search_url}")
            html = await _fetch_html_playwright(search_url)

        # Шаг 3: каталожная страница как запасной вариант (только httpx)
        if not html and seed["catalog_url"] != search_url:
            catalog_url = seed["catalog_url"]
            html = await _fetch_html_httpx(client, catalog_url)
            if not html and use_playwright:
                html = await _fetch_html_playwright(catalog_url)

        if not html:
            logger.warning(f"[Crawler] {domain}: no HTML retrieved")
            return 0

        # Извлекаем прямой товар только если search_url сам является товарной страницей
        # (для каталожных/поисковых страниц JSON-LD может быть AggregateOffer — пропускаем)
        site_key = next((k for k in _SITE_PRODUCT_PATTERNS if k in domain), None)
        search_path = urlparse(search_url).path
        is_product_url = (
            site_key is None  # неизвестный сайт — пробуем
            or bool(_SITE_PRODUCT_PATTERNS[site_key].search(search_path))
        )
        if is_product_url:
            direct = extract_product(html, search_url)
            if direct and direct.price and direct.title:
                on_product(direct)
                found += 1

        # Извлекаем ссылки на дочерние страницы товаров
        child_urls = _extract_product_links_for_site(html, search_url, domain)
        logger.debug(f"[Crawler] {domain}: found {len(child_urls)} product links")

        # Если на поисковой странице нет ссылок — пробуем каталог
        if not child_urls and seed["catalog_url"] != search_url:
            catalog_html = await _fetch_html_httpx(client, seed["catalog_url"])
            if not catalog_html and use_playwright:
                catalog_html = await _fetch_html_playwright(seed["catalog_url"])
            if catalog_html:
                child_urls = _extract_product_links_for_site(catalog_html, seed["catalog_url"], domain)
                logger.debug(f"[Crawler] {domain}: catalog gave {len(child_urls)} links")

        # Шаг 4: параллельный обход товарных страниц через httpx
        # Лимит: не более max_products+2 страниц, максимум 4 одновременно
        to_fetch = [u for u in child_urls if not is_excluded(_url_domain(u))][:min(max_products + 2, 10)]

        if to_fetch:
            # Небольшая задержка перед параллельным обходом
            await asyncio.sleep(random.uniform(0.3, 0.6))

            sem = asyncio.Semaphore(4)  # не более 4 одновременных запросов к одному сайту

            async def _fetch_one(url: str) -> Optional[ExtractedProduct]:
                async with sem:
                    html_p = await _fetch_html_httpx(client, url, referer=search_url)
                if not html_p:
                    return None
                return extract_product(html_p, url)

            tasks = [_fetch_one(u) for u in to_fetch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for res in results:
                if isinstance(res, Exception) or res is None:
                    continue
                if res.price and res.title:
                    on_product(res)
                    found += 1
                    if found >= max_products:
                        break

    finally:
        if own_client:
            await client.aclose()

    logger.info(f"[Crawler] {domain}: found {found} products for '{query}'")
    return found


def _url_domain(url: str) -> str:
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
    kwargs: dict = {
        "follow_redirects": True,
        "timeout": httpx.Timeout(CRAWL_TIMEOUT, connect=5.0),
        "limits": httpx.Limits(max_keepalive_connections=10, max_connections=20),
    }
    if PROXY_URL:
        kwargs["proxy"] = PROXY_URL

    async with httpx.AsyncClient(**kwargs) as client:
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
