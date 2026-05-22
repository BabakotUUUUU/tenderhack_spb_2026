"""
Парсер неформализованных ресурсов Рунета.

Стратегия (4-й источник, не фиксированный):
  Используется SearXNG — self-hosted агрегатор поиска (open-source, бесплатный).
  SearXNG сам агрегирует результаты из Yandex, Google, Bing и других,
  не раскрывая ключей API и соблюдая анонимность.
  
  Деплоится как Docker-контейнер в той же сети.
  
  Если SearXNG недоступен — фолбэк на DDG HTML (duckduckgo lite).
  
  Затем BeautifulSoup парсит найденные страницы на предмет цен
  (ищем микроразметку schema.org/Product, meta og:price, span.price и т.д.)

Преимущества:
  - 4-й источник динамический (меняется в зависимости от выдачи)
  - Не фиксированный маркетплейс
  - Полностью self-hosted / бесплатный
  - Не зависит от платных API
"""

import logging
import re
import json
from typing import Optional
from app.parsers.base import BaseParser, ProductItem, fetch_with_retry, get_headers, global_rate_limiter
import httpx
import os

logger = logging.getLogger(__name__)

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")
DDG_LITE_URL = "https://html.duckduckgo.com/html/"

# Маркетплейсы исключаем из 4-го источника (они уже есть в первых трёх)
EXCLUDED_DOMAINS = {
    "wildberries.ru", "wb.ru",
    "ozon.ru",
    "market.yandex.ru", "yandex.market",
}

# Regex для извлечения цен из HTML
PRICE_PATTERNS = [
    re.compile(r'"price"\s*:\s*"?(\d[\d\s.,]*)"?', re.I),
    re.compile(r'itemprop="price"[^>]*content="([\d.,]+)"', re.I),
    re.compile(r'class="[^"]*price[^"]*"[^>]*>([\d\s.,]+\s*(?:₽|руб|RUB|р\.))', re.I),
    re.compile(r'([\d\s]{3,10}\s*(?:₽|руб\.?|RUB|р\.))'),
]


def _extract_price_from_html(html: str) -> Optional[float]:
    """Извлекает первую найденную цену из HTML."""
    # Пробуем schema.org
    match = re.search(r'"price":\s*"?([\d.,]+)"?', html)
    if match:
        try:
            return float(match.group(1).replace(",", ".").replace(" ", ""))
        except Exception:
            pass

    # Пробуем meta og:price
    match = re.search(r'property="product:price:amount"\s+content="([\d.,]+)"', html)
    if match:
        try:
            return float(match.group(1))
        except Exception:
            pass

    # Общий паттерн цены
    for pat in PRICE_PATTERNS:
        match = pat.search(html)
        if match:
            raw = re.sub(r"[^\d.,]", "", match.group(1))
            try:
                return float(raw.replace(",", "."))
            except Exception:
                continue
    return None


def _is_excluded(url: str) -> bool:
    return any(d in url for d in EXCLUDED_DOMAINS)


class RunetParser(BaseParser):
    source_name = "Интернет (Рунет)"
    domain = "searxng"

    async def search(self, query: str, region: str = "Москва", limit: int = 8) -> list[ProductItem]:
        # 1. Пробуем SearXNG
        urls = await self._search_searxng(query)
        
        # 2. Фолбэк на DDG HTML
        if not urls:
            urls = await self._search_ddg(query)
        
        if not urls:
            return []
        
        # Фильтруем исключённые домены
        urls = [u for u in urls if not _is_excluded(u)][:limit]
        
        # 3. Парсим найденные страницы на предмет цен
        results = []
        for url_data in urls:
            item = await self._scrape_product_page(url_data)
            if item:
                results.append(item)
        
        logger.info(f"[Runet] Found {len(results)} products")
        return results

    async def _search_searxng(self, query: str) -> list[dict]:
        """Поиск через локальный SearXNG."""
        try:
            url = f"{SEARXNG_URL}/search"
            params = {
                "q": f"{query} купить цена",
                "format": "json",
                "language": "ru-RU",
                "categories": "general",
                "engines": "yandex,bing",
            }
            resp = await self.client.get(url, params=params, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                return [
                    {"url": r.get("url", ""), "title": r.get("title", ""), "snippet": r.get("content", "")}
                    for r in data.get("results", [])
                    if r.get("url") and not _is_excluded(r.get("url", ""))
                ]
        except Exception as e:
            logger.warning(f"[SearXNG] unavailable: {e}")
        return []

    async def _search_ddg(self, query: str) -> list[dict]:
        """Поиск через DuckDuckGo HTML (бесплатный, без API-ключа)."""
        try:
            import asyncio, random
            await asyncio.sleep(random.uniform(2, 4))
            
            headers = get_headers("https://duckduckgo.com/")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            
            resp = await self.client.post(
                DDG_LITE_URL,
                data={"q": f"{query} купить цена сайт:*.ru", "kl": "ru-ru"},
                headers=headers,
                timeout=15.0,
            )
            if resp.status_code != 200:
                return []
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            
            for result in soup.find_all("div", class_=re.compile(r"result", re.I))[:15]:
                link = result.find("a", class_=re.compile(r"result__a", re.I))
                snippet_el = result.find("a", class_=re.compile(r"result__snippet", re.I))
                if link and link.get("href"):
                    url = link["href"]
                    if url.startswith("//"):
                        url = "https:" + url
                    title = link.get_text(strip=True)
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                    if not _is_excluded(url):
                        results.append({"url": url, "title": title, "snippet": snippet})
            
            return results
        except Exception as e:
            logger.error(f"[DDG] search error: {e}")
            return []

    async def _scrape_product_page(self, url_data: dict) -> Optional[ProductItem]:
        """Скрапит страницу и извлекает данные о товаре/цене."""
        url = url_data.get("url", "")
        title_hint = url_data.get("title", "")
        
        if not url or not url.startswith("http"):
            return None

        try:
            import asyncio, random
            await asyncio.sleep(random.uniform(1.5, 3.5))
            
            resp = await self.client.get(
                url, headers=get_headers(url), timeout=12.0, follow_redirects=True
            )
            if resp.status_code != 200:
                return None
            
            html = resp.text
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            
            # Извлекаем название
            title = self._extract_title(soup) or title_hint
            if not title:
                return None
            
            # Извлекаем цену
            price = _extract_price_from_html(html)
            
            # Извлекаем изображение
            image_url = self._extract_image(soup, url)
            
            # Характеристики из schema.org
            chars = self._extract_schema_chars(html)
            
            # Определяем источник (домен)
            domain = re.sub(r"https?://(www\.)?", "", url).split("/")[0]
            
            return ProductItem(
                title=title,
                price=price,
                image_url=image_url,
                product_url=url,
                source=f"Рунет ({domain})",
                characteristics=chars,
            )
        except Exception as e:
            logger.warning(f"[Runet] scrape error {url}: {e}")
            return None

    def _extract_title(self, soup) -> Optional[str]:
        for sel in [
            {"itemprop": "name"},
            {"property": "og:title"},
        ]:
            el = soup.find(attrs=sel)
            if el:
                return el.get("content") or el.get_text(strip=True)
        h1 = soup.find("h1")
        return h1.get_text(strip=True) if h1 else None

    def _extract_image(self, soup, base_url: str) -> Optional[str]:
        og_img = soup.find("meta", {"property": "og:image"})
        if og_img and og_img.get("content"):
            return og_img["content"]
        img = soup.find("img", {"itemprop": "image"})
        if img:
            src = img.get("src", "")
            if src.startswith("//"):
                return "https:" + src
            if src.startswith("/"):
                domain = re.match(r"https?://[^/]+", base_url)
                return domain.group(0) + src if domain else src
            return src
        return None

    def _extract_schema_chars(self, html: str) -> dict:
        chars = {}
        try:
            # Ищем JSON-LD с schema.org
            matches = re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
            for m in matches:
                try:
                    data = json.loads(m)
                    if isinstance(data, dict) and data.get("@type") in ("Product", "Offer"):
                        brand = data.get("brand", {})
                        if isinstance(brand, dict):
                            chars["Бренд"] = brand.get("name", "")
                        elif isinstance(brand, str):
                            chars["Бренд"] = brand
                        desc = data.get("description", "")
                        if desc:
                            chars["Описание"] = desc[:100]
                except Exception:
                    continue
        except Exception:
            pass
        return {k: v for k, v in chars.items() if v}
